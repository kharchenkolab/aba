"""
P3: a CLI bioinformatics tool materializes on demand via conda (micromamba)
into the shared wipeable tools env, and run_python invokes it via subprocess
(tools/bin on PATH). Isolated dirs incl. throwaway ENVS_DIR. Live network +
conda solve (slow — first run downloads micromamba + solves seqkit).

Run:
    .venv/bin/python tests/p3_cli_tools.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_p3_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "p3.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))
# Catalog content is pack-sourced (installation scope) — point it at the shared
# seed fixture so the capability catalog is populated (pack seeds as test data).
sys.path.insert(0, str(Path(__file__).resolve().parent))       # tests/ for the helper
import _catalog_fixture                                          # noqa: E402
_catalog_fixture.install()

from core.graph._schema import init_db                       # noqa: E402
import content.bio  # noqa: E402,F401
from content.bio.tools import ensure_capability, run_python  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()

    print("micromamba bootstrap")
    from core.exec.mamba import ensure_micromamba
    mb = ensure_micromamba()
    check("micromamba binary present + executable", Path(mb).exists() and os.access(mb, os.X_OK), mb)

    print("ensure_capability: conda CLI tool (seqkit) — materialize (slow)")
    r = ensure_capability({"name": "seqkit"})
    check("seqkit conda materialize → ready", r.get("status") == "ready", str(r))

    print("run_python invokes the CLI tool via subprocess (tools/bin on PATH)")
    rp = run_python({"code": (
        "import subprocess\n"
        "out = subprocess.run(['seqkit', 'version'], capture_output=True, text=True)\n"
        "print('rc', out.returncode); print(out.stdout.strip() or out.stderr.strip())\n"
    ), "timeout_s": 60})
    check("seqkit runs from run_python", rp.get("returncode") == 0 and "seqkit" in (rp.get("stdout") or "").lower(),
          f"rc={rp.get('returncode')} stdout={rp.get('stdout')!r} stderr={rp.get('stderr')!r}")

    print("library path still works alongside (pip overlay)")
    rl = ensure_capability({"name": "pyfaidx"})
    check("pyfaidx (pip) → ready", rl.get("status") == "ready", str(rl))
    ri = run_python({"code": "import pyfaidx; print('pyfaidx', pyfaidx.__version__)"})
    check("run_python imports pip-materialized library", ri.get("returncode") == 0
          and "pyfaidx" in (ri.get("stdout") or ""))

    print("isolation: .venv pristine, tools env under ENVS_DIR")
    import sys as _s
    venv_site = next((p for p in _s.path if ".venv" in p and "site-packages" in p), "")
    check("seqkit NOT installed into .venv", not (Path(venv_site) / "seqkit").exists() if venv_site else True)
    check("tools env lives under throwaway ENVS_DIR",
          (Path(_tmp) / "envs" / "tools" / "bin").exists())

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL P3 CLI-TOOL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
