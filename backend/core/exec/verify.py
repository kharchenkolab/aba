"""Environment-verify primitives — honest import/capability probes.

Split out of ``env_integrity`` (W3.4 served-base retirement): these are the
runtime-agnostic "does it ACTUALLY load / can it ACTUALLY use a GPU" checks,
kept after the served-base heal machinery was deleted. They import the real
thing in a throwaway subprocess on the target interpreter, rather than trusting
``find_spec`` — a package built against the wrong numpy ABI, a half-written
package, or one missing a system lib all HAVE a spec but fail to load (the
tensorflow/scipy incidents, 2026-06-23/24).

Language-symmetric: ``verify_python_imports`` mirrors run_python's path
assembly; the GPU probes answer the verify-at-use question (can a torch job on
THIS node reach a GPU) that only the node itself can answer.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from typing import Optional, Sequence

# Positive-probe memo: {(identity_key, name): True}. POSITIVES ONLY — a
# verified load is stable for a given identity (sessions key on
# (session_id, rev): any install bumps rev; frozen envs key on EnvID), while
# a failure may be transient and must re-derive. In-process by design: a
# server restart re-pays one probe per identity, never a stale verdict.
# Field numbers behind this (2026-07-22): 24s post-install probe, 69s on a
# request that installed nothing. The substrate's verify-first pre-check
# (~0.4s) replaces this at F-V2; until then this is the consumer stopgap.
_PROBE_MEMO: dict = {}
_PROBE_MEMO_LOCK = threading.Lock()


def verify_python_imports(
    import_names: Sequence[str],
    *,
    extra_paths: Optional[Sequence[str]] = None,
    python_exe: Optional[str] = None,
    argv_builder=None,
    timeout_s: int = 180,
    memo_key: Optional[tuple] = None,
) -> tuple[bool, str]:
    """Actually import each name in a fresh subprocess on the target interpreter.
    Returns ``(ok, detail)``.

    ``ok=False`` means present-but-unloadable — ABI mismatch, partial install,
    missing system lib — i.e. the exact "find_spec says yes, import explodes"
    case. ``detail`` carries the traceback tail for the agent/operator.

    Target selection: ``argv_builder`` (preferred for the DEFAULT env) is a
    callable ``args -> full argv`` — the topology-blind path
    (``project_env.exec_argv``): it composes the activation line for envs with
    no directly-usable prefix (mounted/squashfs bases, lazy sessions) and
    re-resolves the session runtime per call, so a post-install verify sees the
    flipped (materialized) session, never the stale base. ``python_exe`` is a
    bare interpreter path for envs that HAVE one (a named-env prefix). The
    probed env's own site-packages are authoritative, so ``extra_paths``
    defaults to none; pass it to verify against a temp install prefix before
    merging (transactional installs).
    """
    names = [n for n in (import_names or []) if n]
    if not names:
        return True, ""
    if memo_key is not None:
        with _PROBE_MEMO_LOCK:
            names = [n for n in names if (memo_key, n) not in _PROBE_MEMO]
        if not names:
            return True, ""            # every name already proven for this identity
    if extra_paths is None:
        extra_paths = []          # the target interpreter's own site-packages win
    # append (not prepend) so the interpreter's own packages win
    appends = "".join(f"sys.path.append({str(p)!r})\n" for p in (extra_paths or []))
    names_lit = ", ".join(repr(n) for n in names)
    script = (
        "import sys\n"
        f"{appends}"
        "import importlib\n"
        f"for _n in [{names_lit}]:\n"
        "    importlib.import_module(_n)\n"
        "print('ABA_IMPORT_OK')\n"
    )
    if argv_builder is not None:
        cmd = list(argv_builder(["-c", script]))
    else:
        cmd = [python_exe or sys.executable, "-c", script]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        return False, f"import verification timed out after {timeout_s}s"
    except Exception as e:  # noqa: BLE001
        return False, f"could not launch import verification: {e}"
    if proc.returncode == 0 and "ABA_IMPORT_OK" in (proc.stdout or ""):
        if memo_key is not None:
            with _PROBE_MEMO_LOCK:
                for n in names:
                    _PROBE_MEMO[(memo_key, n)] = True
        return True, ""
    detail = ((proc.stderr or "") + (proc.stdout or "")).strip()
    return False, detail[-1400:]


def gpu_capability_ok() -> tuple[Optional[bool], str]:
    """Can a GPU workload actually use a GPU in THIS interpreter? (via torch.cuda).
    Returns (ok, detail):
      True  — a usable CUDA GPU is visible;
      False — torch is present but sees NO usable GPU (a CPU-only build, or a CUDA
              build with no runtime/driver on this node) — a GPU job would silently
              run on CPU on an idle allocated GPU (the scVI-on-CPU incident);
      None  — torch isn't importable → not a torch GPU job, so don't judge it.
    The verify-at-use boundary: certainty about a remote node's accelerator can only
    be had ON that node, so this runs where the job runs (slurm_entry) and also backs
    the compute_env `gpu_usable` hint."""
    try:
        import torch  # noqa
    except Exception:  # noqa: BLE001 — no torch → not a torch-GPU job
        return None, "torch not importable"
    try:
        if torch.cuda.is_available():
            return True, f"torch {torch.__version__}, cuda {torch.version.cuda}"
        return False, (f"torch {torch.__version__} sees no usable GPU "
                       f"(version.cuda={torch.version.cuda}, cuda.is_available()=False)")
    except Exception as e:  # noqa: BLE001
        return False, f"torch.cuda probe errored: {e}"


def torch_cuda_build() -> Optional[str]:
    """The CUDA version torch was BUILT against (`torch.version.cuda`), or None if torch
    is a CPU-only build / not importable. Node-INDEPENDENT (a property of the build, not
    of runtime GPU visibility) — so ABA on a CPU login node can tell whether a GPU JOB on
    a compute node would be able to use the GPU, WITHOUT a GPU here. This is what backs
    the compute_env `gpu_usable` hint; the on-node `gpu_capability_ok` is the verify-at-use."""
    try:
        import torch  # noqa
        return torch.version.cuda
    except Exception:  # noqa: BLE001
        return None
