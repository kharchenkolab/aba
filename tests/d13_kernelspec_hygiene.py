"""
Kernelspec hygiene (P0) — run_r must never be hijacked by a stale/foreign
IRkernel spec, and ABA's kernelspecs must be scoped to the env they point at, so
a test pointing ABA_ENVS_DIR at a throwaway /tmp env can't poison the user's
global Jupyter dir (the bug that DOA'd live run_r: an e2e-test spec pointed at a
/tmp R that was later wiped, and _ensure_r_kernelspec trusted the name blindly).

Deterministic; no conda / Rscript / kernel launch — we fabricate kernelspec JSON
on disk and exercise the argv[0] validator + the JUPYTER_DATA_DIR scoping.

Run:
    .venv/bin/python tests/d13_kernelspec_hygiene.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_d13_")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "d13.db")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core import config                              # noqa: E402  (sets JUPYTER_DATA_DIR)
from core.exec.kernels import jupyter as jk          # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def _write_spec(name, argv0):
    """Fabricate an IRkernel-style kernelspec under the scoped Jupyter data dir."""
    d = Path(os.environ["JUPYTER_DATA_DIR"]) / "kernels" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "kernel.json").write_text(json.dumps({
        "argv": [argv0, "--slave", "-e", "IRkernel::main()", "--args", "{connection_file}"],
        "display_name": "R", "language": "R",
    }))
    return d


def test_jupyter_data_dir_scoped():
    print("JUPYTER_DATA_DIR scoped to ENVS_DIR/jupyter (not the global dir)")
    want = str(Path(os.environ["ABA_ENVS_DIR"]).resolve() / "jupyter")
    got = os.environ.get("JUPYTER_DATA_DIR")
    check("JUPYTER_DATA_DIR == ENVS_DIR/jupyter", got == want, f"{got} != {want}")
    check("not the user's global ~/.local/share/jupyter", ".local/share/jupyter" not in (got or ""))


def test_spec_validator():
    print("_r_spec_points_into: reject stale/foreign, accept in-env")
    tenv = jk.tools_env()                             # ENVS_DIR/tools
    good_r = tenv / "lib" / "R" / "bin" / "R"
    good_r.parent.mkdir(parents=True, exist_ok=True)
    good_r.write_text("#!/bin/sh\n")                  # a real file inside our env

    check("missing spec → False", jk._r_spec_points_into("aba_r", tenv) is False)

    _write_spec("ir", "/tmp/aba_e2e_envs/tools/lib/R/bin/R")   # the exact poisoning case
    check("stale /tmp spec → False (hijack rejected)", jk._r_spec_points_into("ir", tenv) is False)

    other = Path(_tmp) / "other" / "R"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("x")                             # exists, but outside our env
    _write_spec("foreign", str(other))
    check("exists-but-outside-env spec → False", jk._r_spec_points_into("foreign", tenv) is False)

    _write_spec("aba_r", str(good_r))
    check("in-env existing R → True", jk._r_spec_points_into("aba_r", tenv) is True)


def test_private_spec_name():
    print("ABA uses a private R spec name, not the clobberable 'ir'")
    check("_R_SPEC_NAME is private (not 'ir')", jk._R_SPEC_NAME != "ir", jk._R_SPEC_NAME)
    check("_R_SPEC_NAME is aba_r", jk._R_SPEC_NAME == "aba_r", jk._R_SPEC_NAME)


def main() -> int:
    test_jupyter_data_dir_scoped()
    test_spec_validator()
    test_private_spec_name()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL KERNELSPEC-HYGIENE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
