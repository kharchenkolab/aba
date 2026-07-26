"""Weft-kernel working-directory constraint — the predicate and the guardrail.

A weft kernel's driver (`weft/kernels/driver.{py,R,jl}`) addresses its OWN
protocol files relative to the process cwd (`blocks/NNNN.code`, `.out`, `.rc`).
So the first block that changes the working directory orphans the driver's
bookkeeping: its next write fails and the interpreter halts.

Live consequence (mendel, 2026-07-26): ordinary analysis code —

    work_dir <- "/home/.../work"; dir.create(work_dir); setwd(work_dir)

killed the kernel with `cannot open file 'blocks/0002.rc.tmp'` /
`Error in file(con, "w")` / `Execution halted` → exit 1, all in-memory state
lost, and the agent re-ran the same code into the same death on the restarted
kernel. The surfaced error names the driver's `writeLines`, never the `setwd`
that caused it.

Two things live here:

* `is_weft_kernel(sess)` — the ONE predicate for "this session cannot chdir".
  It must NOT be spelled `getattr(sess, "work_dir", None)`: WeftKernelSession
  sets `work_dir` only for a LOCAL site, so that spelling reads every REMOTE
  weft kernel as chdir-able. Two callers had it, and one of them injected a
  `setwd` into remote kernels because of it.

* `chdir_offense(code, lang)` — a STOPGAP: refuse a block that would chdir,
  with an actionable message, instead of sending it and losing the kernel. It is
  a door-local mitigation, not a fix (a chdir arriving from any other weft
  client still kills the kernel); the real fix is the driver resolving its
  jobdir once and writing absolutely, requested in
  `weft/misc/from-aba-kernel-cwd-fatal.md`. DELETE THIS once that lands —
  `setwd()`/`os.chdir()` are legitimate code and refusing them is a cost we
  only accept while the alternative is a dead kernel.
"""
from __future__ import annotations

import re

# Statement-position chdir calls, per language. Anchored to the start of a
# statement (line start or after `;`) so prose mentioning setwd in a string or a
# kwarg named `chdir=` is not caught.
_PATTERNS = {
    "r": re.compile(r"(?:^|;)\s*setwd\s*\(", re.M),
    "python": re.compile(r"(?:^|;)\s*(?:os\s*\.\s*chdir|chdir)\s*\(", re.M),
}


def is_weft_kernel(sess) -> bool:
    """Does this session run under a weft kernel driver (→ cannot chdir)?

    The UNION of three signals. The original bug was not that `work_dir` is a
    wrong signal — it is a fine one, since only a weft kernel has a work dir —
    but that it was treated as NECESSARY when it is merely SUFFICIENT: it is set
    only for a LOCAL site, so a remote weft kernel has none. `kernel_id` is the
    signal that holds for both (weft.py assigns it before work_dir, so work_dir
    implies kernel_id on a real session); the class name covers a session
    inspected before kernel_start returns."""
    return (getattr(sess, "kernel_id", None) is not None
            or getattr(sess, "work_dir", None) is not None
            or type(sess).__name__ == "WeftKernelSession")


def _strip_comments(line: str) -> str:
    """Drop a trailing `#` comment, respecting quotes. Both R and Python use
    `#`, so one pass serves both. Deliberately simple: this only decides whether
    to WARN, so a pathological string is a missed warning, never a wrong edit."""
    out, quote = [], ""
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def chdir_offense(code: str, lang: str) -> str | None:
    """The offending source line when `code` would change the kernel's working
    directory, else None. Comment-only mentions and quoted text are ignored."""
    pat = _PATTERNS.get("r" if str(lang).lower() in ("r", "rlang") else "python")
    if not pat or not code:
        return None
    for raw in code.splitlines():
        stripped = _strip_comments(raw)
        if pat.search(stripped):
            return raw.strip()[:200]
    return None


def chdir_refusal(offense: str, lang: str) -> dict:
    """The agent-facing refusal for a block that would chdir. Names the real
    constraint and the two ways forward — a bare "not allowed" would just get
    retried."""
    call = "setwd()" if str(lang).lower() in ("r", "rlang") else "os.chdir()"
    join = ('file.path(work_dir, "plot.png")' if call == "setwd()"
            else 'os.path.join(work_dir, "plot.png")')
    return {
        "status": "error",
        "error": "kernel.chdir_forbidden",
        "offending_line": offense,
        "note": (
            f"This block calls {call}, which would KILL this kernel: a weft "
            f"kernel's driver writes its own per-block files (blocks/NNNN.*) "
            f"relative to the working directory, so changing it makes the "
            f"driver's next write fail and the interpreter exit — taking every "
            f"object in memory with it. Nothing was run.\n"
            f"Instead: keep the kernel where it is and address files "
            f"ABSOLUTELY — set `work_dir <- \"…\"` (or a Python variable), "
            f"create it, and build paths with {join}. Writes into the kernel's "
            f"own sandbox are what get harvested as this Run's outputs, so "
            f"prefer bare relative names there and absolute paths elsewhere."),
    }
