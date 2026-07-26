"""Detached-node harness — runs a background job on a machine that shares
NOTHING with the controller (no filesystem, possibly a different OS/arch).

This file is shipped TO the node as part of the job's payload (a CAS-staged
input dir; see weft_submitter's detached branch) and executed there with a
`python3` — the activated env's when the task carries env=EnvID (weft puts
its prefix first on PATH), else the node system's. It must stay STDLIB-ONLY
and never import aba — the node has no aba, no controller paths, no ABA_*.

It is a language-agnostic HARNESS, not an interpreter: the user script runs
as a SUBPROCESS per spec.json, so any runtime the node/env provides works
(python3, Rscript, ...). Contract (paths relative to the task workdir):

  payload/aba_entry.py    this file
  payload/user_code.py    (or user_code.R, ... — named by spec.script)
  payload/spec.json       {"interpreter": "python3"|"Rscript",
                           "script": "user_code.py", "job_id": "<nonce>",
                           "timeout_s": <ceiling>}
                          job_id doubles as the MEMO NONCE: identical code
                          must not collide into weft's task memo. timeout_s
                          is enforced HERE (the node kills the script) —
                          ssh-kind sites have no scheduler walltime, so
                          without this a runaway loop runs forever.
  result.json             written HERE on completion:
                          {status, returncode, error?, stdout_tail,
                           outputs: [relpaths produced], runtime, seconds}

Everything the script writes to the workdir persists in the task's run dir —
addressable from the controller by (run, rel), keepable, shippable home.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time


def _snapshot() -> set:
    out = set()
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in ("payload", ".weft")]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), ".")
            if not rel.startswith(("payload/", ".weft")):
                out.add(rel)
    return out


# cwd-escape probe (harvest honesty): outputs are collected RELATIVE TO THE
# WORKDIR — a script that chdir/setwd's elsewhere writes files nothing will
# ever collect, silently. The harness wraps the user script with a prologue
# recording the starting dir and an epilogue writing "<start>\n<final>" to a
# dot-sentinel in the starting dir; result.json carries both so the
# controller can warn. Inline (stdlib-only file — cannot import the
# controller's core.exec.run helpers; keep the sentinel NAME in sync).
_SENTINEL = ".aba-final-cwd"
_PY_PRO = "import os as _aba_os; _ABA_START_DIR = _aba_os.getcwd()\n"
_PY_EPI = ("\ntry:\n"
           "    import os as _aba_os\n"
           f"    with open(_aba_os.path.join(_ABA_START_DIR, {_SENTINEL!r}), 'w') as _aba_f:\n"
           "        _aba_f.write(_ABA_START_DIR + '\\n' + _aba_os.getcwd())\n"
           "except Exception:\n"
           "    pass\n")
_R_PRO = ".aba_start_dir <- getwd()\n"
_R_EPI = ('\ntry(writeLines(c(.aba_start_dir, getwd()), '
          f'file.path(.aba_start_dir, "{_SENTINEL}")), silent = TRUE)\n')


def _wrap_script(script: str, interp: str) -> str:
    """Copy the payload script into the workdir with the probe wrapped around
    it (payload mounts may be read-only). Dot-named so output collection
    skips it."""
    is_r = "rscript" in interp.lower()
    pro, epi = (_R_PRO, _R_EPI) if is_r else (_PY_PRO, _PY_EPI)
    with open(script) as fh:
        body = fh.read()
    wrapped = "._aba_wrapped" + (".R" if is_r else ".py")
    if not is_r:
        # `from __future__` must be the first statement in a Python file, so a
        # blind prepend turns a valid script into a SyntaxError. Slip the probe
        # in AFTER any future imports (and the docstring/comments they may
        # follow) instead of ahead of them.
        body = _py_insert_after_future(body, pro)
        with open(wrapped, "w") as fh:
            fh.write(body + epi)
        return wrapped
    with open(wrapped, "w") as fh:
        fh.write(pro + body + epi)
    return wrapped


def _py_insert_after_future(body: str, pro: str) -> str:
    """Insert `pro` after the last `from __future__ import …` line, else at the
    top. Line-based on purpose: the probe must not disturb the payload's own
    line numbering any more than necessary, and a full parse would fail on the
    very scripts (syntax errors) whose traceback the user needs."""
    lines = body.splitlines(keepends=True)
    last = -1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("from __future__ import"):
            last = i
        elif s and not s.startswith("#") and last >= 0:
            break                      # future block ended
    if last < 0:
        return pro + body
    return "".join(lines[:last + 1]) + pro + "".join(lines[last + 1:])


def _read_sentinel() -> tuple:
    try:
        with open(_SENTINEL) as fh:
            lines = fh.read().splitlines()
        os.unlink(_SENTINEL)
        if len(lines) >= 2:
            return lines[0].strip() or None, lines[1].strip() or None
    except OSError:
        pass
    return None, None


def _runtime_version(interp: str) -> str:
    try:
        r = subprocess.run([interp, "--version"], capture_output=True,
                           text=True, timeout=30)
        return (r.stdout or r.stderr).strip().splitlines()[0][:120]
    except Exception:  # noqa: BLE001
        return "unknown"


def main() -> int:
    t0 = time.time()
    with open(os.path.join("payload", "spec.json")) as fh:
        spec = json.load(fh)
    interp = spec.get("interpreter") or "python3"
    script = os.path.join("payload", spec.get("script") or "user_code.py")
    timeout = spec.get("timeout_s")   # ceiling; absent (legacy spec) → unbounded
    result = {"status": "ok", "returncode": 0, "stdout_tail": "",
              "outputs": [], "runtime": "", "seconds": 0.0,
              "job_id": spec.get("job_id")}
    exe = shutil.which(interp)
    if exe is None:
        result.update(status="error", returncode=127,
                      error=f"no {interp!r} available on this machine")
        _write(result, t0)
        return 1
    result["runtime"] = _runtime_version(exe)
    try:
        script = _wrap_script(script, interp)
    except Exception:  # noqa: BLE001 — probe is best-effort, never blocks the run
        pass
    before = _snapshot()
    try:
        p = subprocess.run([exe, script], capture_output=True, text=True,
                           timeout=timeout or None)
        tail = (p.stdout or "")[-20000:]
        if p.stderr:
            tail += ("\n--- stderr ---\n" + p.stderr[-6000:])
        result["stdout_tail"] = tail
        result["returncode"] = p.returncode
        if p.returncode != 0:
            result["status"] = "error"
            result["error"] = (p.stderr or p.stdout or "")[-2000:] \
                or f"exit code {p.returncode}"
    except subprocess.TimeoutExpired as e:
        partial = e.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode(errors="replace")
        result["stdout_tail"] = partial[-20000:]
        result.update(status="error", returncode=124,
                      error=f"timed out after {timeout}s — the script was "
                            f"killed on the node (timeout_s ceiling; raise it "
                            f"or use a sized background job)")
    except Exception as e:  # noqa: BLE001 — report, never swallow
        result.update(status="error", returncode=1, error=str(e)[:2000])
    start_cwd, final_cwd = _read_sentinel()
    if final_cwd:
        result["start_cwd"] = start_cwd
        result["final_cwd"] = final_cwd
    result["outputs"] = sorted(_snapshot() - before - {"result.json"}
                               - {"._aba_wrapped.py", "._aba_wrapped.R"})
    _write(result, t0)
    return 0 if result["status"] == "ok" else 1


def _write(result: dict, t0: float) -> None:
    result["seconds"] = round(time.time() - t0, 2)
    with open("result.json", "w") as fh:
        json.dump(result, fh)
    sys.stdout.write(f"[aba-harness] {result['status']} "
                     f"({len(result.get('outputs') or [])} outputs)\n")


if __name__ == "__main__":
    raise SystemExit(main())
