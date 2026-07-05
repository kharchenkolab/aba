"""Regression: the run_python kernel comes up multithreaded, never single-core.

Starts a REAL Jupyter python kernel through the backend code path and checks
that (a) the BLAS/OMP thread env vars are pinned at kernel launch and (b)
`torch.get_num_threads()` is > 1 inside the kernel — even though we deliberately
poison the parent environment with `OMP_NUM_THREADS=1` first (the jupyter/jax
trap that pegged scvi to one core).

Isolated DB/work/data; uses the live ENVS overlay (read-only) so torch imports.

Run:  .venv/bin/python tests/test_kernel_thread_env.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_kthreads_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "k.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
# Keep the LIVE pylib overlay (torch/scvi live there) even though runtime is isolated.
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
# Poison the parent env: this is exactly what makes a kernel single-core if the
# fix isn't there. The kernel must override it.
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                          # noqa: E402
from core.exec.kernels.jupyter import JupyterKernelSession, _kernel_threads  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()
    want = _kernel_threads()
    print(f"expected kernel thread count = {want} (min(cpu_count,8); cpu={os.cpu_count()})\n")

    sess = JupyterKernelSession("test-threads", "python",
                                cwd=str(Path(_tmp) / "work"))
    try:
        code = (
            "import os, json\n"
            "out = {k: os.environ.get(k) for k in "
            "('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS')}\n"
            "try:\n"
            "    import torch; out['torch_threads'] = torch.get_num_threads()\n"
            "except Exception as e:\n"
            "    out['torch_threads'] = f'ERR {e!r}'\n"
            "print('RESULT ' + json.dumps(out))\n"
        )
        res = sess.execute(code, timeout_s=120)
        line = next((l for l in res.stdout.splitlines() if l.startswith("RESULT ")), "")
        print("kernel reported:", line or res.stdout or res.stderr)
        import json
        data = json.loads(line[len("RESULT "):]) if line else {}

        for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            check(f"{var} pinned to {want} in kernel (parent had 1)",
                  data.get(var) == str(want), f"got {data.get(var)!r}")

        tt = data.get("torch_threads")
        check("torch.get_num_threads() > 1 in kernel",
              isinstance(tt, int) and tt > 1, f"got {tt!r}")
        check(f"torch.get_num_threads() == {want}",
              tt == want, f"got {tt!r}")
    finally:
        sess.shutdown()

    print()
    if _failures:
        print(f"FAILED: {_failures}")
        return 1
    print("ALL KERNEL THREAD-ENV CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
