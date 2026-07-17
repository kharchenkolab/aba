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
                           "script": "user_code.py", "job_id": "<nonce>"}
                          job_id doubles as the MEMO NONCE: identical code
                          must not collide into weft's task memo.
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
    before = _snapshot()
    try:
        p = subprocess.run([exe, script], capture_output=True, text=True)
        tail = (p.stdout or "")[-20000:]
        if p.stderr:
            tail += ("\n--- stderr ---\n" + p.stderr[-6000:])
        result["stdout_tail"] = tail
        result["returncode"] = p.returncode
        if p.returncode != 0:
            result["status"] = "error"
            result["error"] = (p.stderr or p.stdout or "")[-2000:] \
                or f"exit code {p.returncode}"
    except Exception as e:  # noqa: BLE001 — report, never swallow
        result.update(status="error", returncode=1, error=str(e)[:2000])
    result["outputs"] = sorted(_snapshot() - before - {"result.json"})
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
