"""Weft-kernel working-directory constraint: the identity predicate, and the
DIAGNOSIS for when it bites.

A weft kernel's driver (`weft/kernels/driver.{py,R,jl}`) addresses its own
protocol files relative to the process cwd (`blocks/NNNN.code`, `.out`, `.rc`).
So a block that leaves the working directory somewhere else orphans the driver:
its next write fails and the interpreter exits.

Live (mendel, 2026-07-26): ordinary analysis code —

    work_dir <- "/home/.../work"; dir.create(work_dir); setwd(work_dir)

killed the kernel with `cannot open file 'blocks/0002.rc.tmp'` /
`Error in file(con, "w")` / `Execution halted` → exit 1, every in-memory object
lost. The agent then re-ran the same code into the same death, because the
surfaced error names the driver's own `writeLines`, never the chdir.

WHY THERE IS NO PREVENTION HERE. A refusal based on scanning the submitted code
was built and removed, for two reasons that both matter:

  * It cannot see the case that matters. A `setwd()` inside a LIBRARY function
    is invisible to a source scan, so the guard offers no protection precisely
    where the user has no control.
  * It charges for the case that does not need it. `setwd()`/`os.chdir()` are
    ordinary code, and weft kernels back LOCAL sessions too — so enforcing it
    would have disabled a standard idiom across all kernel work, everywhere.

And the fatal pattern is narrower than "calls chdir": a chdir that is RESTORED
before the block ends (`withr::with_dir`, `on.exit(setwd(old))`, a
context manager) is harmless, because the driver writes after the block
completes. Only a chdir that PERSISTS is fatal — which no static scan can tell
apart. Nor can aba intervene at runtime: the driver's `.rc` write happens at the
end of the SAME block, so there is no later block in which to restore anything.

The real fix LANDED in the substrate (weft 2a58add): the drivers capture the
jobdir before any user code and anchor every protocol path — and WEFT_BLOCK_DIR —
absolutely, so cwd persistence is a supported gesture again and `setwd()` /
`os.chdir()` are ordinary code. What remains here is therefore a FALLBACK, not a
policy: on a deployment still running an older substrate the death can still
happen, and `cwd_drift_diagnosis` makes it legible so an agent corrects itself
instead of looping. It fires only on the driver's exact relative-write signature,
so it costs nothing once the substrate is current.
"""
from __future__ import annotations

import re

# Statement-position chdir calls, per language. Anchored to the start of a
# statement (line start or after `;`) so prose mentioning setwd in a string, or a
# kwarg named `chdir=`, is not matched. Used for DIAGNOSIS ONLY — to sharpen a
# death message when the submitted code visibly chdir'd. A miss here just means
# the generic (still correct) explanation.
_PATTERNS = {
    "r": re.compile(r"(?:^|;)\s*setwd\s*\(", re.M),
    "python": re.compile(r"(?:^|;)\s*(?:os\s*\.\s*chdir|chdir)\s*\(", re.M),
}

# The driver's own failure signature: a relative `blocks/NNNN.*` write that could
# not be opened. Matching this — rather than guessing from the code — is what
# makes the diagnosis honest for a chdir that came from inside a package.
_DRIVER_WRITE_FAILED = re.compile(
    r"blocks/\d+\.(?:rc|out|err)"          # the driver's own protocol file
    r"|cannot open the connection"
    r"|cannot open file 'blocks/")


def is_weft_kernel(sess) -> bool:
    """Does this session run under a weft kernel driver (→ cannot be left in a
    different working directory)?

    The UNION of three signals. The original bug was not that `work_dir` is a
    wrong signal — only a weft kernel has a work dir — but that it was treated as
    NECESSARY when it is merely SUFFICIENT: it is set only for a LOCAL site, so a
    remote weft kernel has none, and reading it as necessary made every remote
    kernel look chdir-able (that shipped a controller-local `setwd` into remote
    kernels). `kernel_id` holds for both; the class name covers a session
    inspected before `kernel_start` returns."""
    return (getattr(sess, "kernel_id", None) is not None
            or getattr(sess, "work_dir", None) is not None
            or type(sess).__name__ == "WeftKernelSession")


def _strip_comments(line: str) -> str:
    """Drop a trailing `#` comment, respecting quotes. Both R and Python use `#`,
    so one pass serves both. Deliberately simple: this only sharpens a message,
    so a pathological string costs a less specific hint, never a wrong verdict."""
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
    """The submitted line that visibly changes the working directory, else None.
    Comment-only mentions and quoted text are ignored. Absence does NOT mean the
    block did not chdir — a library call can, invisibly."""
    pat = _PATTERNS.get("r" if str(lang).lower() in ("r", "rlang") else "python")
    if not pat or not code:
        return None
    for raw in code.splitlines():
        if pat.search(_strip_comments(raw)):
            return raw.strip()[:200]
    return None


def cwd_drift_diagnosis(msg: str, stderr: str, code: str = "",
                        lang: str = "python") -> str | None:
    """When a kernel death carries the driver's relative-write signature, explain
    the ACTUAL cause and what to do. None when the death looks like anything else
    (OOM, walltime, a kill) — a wrong explanation is worse than none.

    Names the offending line when the submitted code visibly chdir'd; otherwise
    says a library call may have, which is the honest reading when the source is
    clean."""
    blob = f"{msg}\n{stderr}"
    if not _DRIVER_WRITE_FAILED.search(blob):
        return None
    call = "setwd()" if str(lang).lower() in ("r", "rlang") else "os.chdir()"
    join = ('file.path(dir, "out.rds")' if call == "setwd()"
            else 'os.path.join(dir, "out.png")')
    offense = chdir_offense(code, lang)
    who = (f"This block ran `{offense}`."
           if offense else
           "Nothing in this block visibly changes it, so a library call likely "
           "did (some packages chdir internally and do not restore it).")
    return (
        "WHY THIS KERNEL DIED — the working directory moved. This kernel's "
        "driver writes its own per-block files (blocks/NNNN.*) using paths "
        "relative to the working directory, so if a block leaves the process in a "
        f"different directory, the driver cannot write and the interpreter exits. "
        f"{who}\n"
        f"State in memory is gone; the next call starts a fresh kernel. To avoid "
        f"it: do not leave the kernel in another directory — keep the path in a "
        f"variable and build filenames with {join}, or restore the directory "
        f"before the block ends (R: `old <- setwd(d); on.exit(setwd(old))`; "
        f"Python: `contextlib.chdir` / restore in a `finally`). A chdir that is "
        f"restored before the block ends is harmless. This is a substrate "
        f"limitation, not your mistake — a fix that makes the driver immune is "
        f"pending.")
