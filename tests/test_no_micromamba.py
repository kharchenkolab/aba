"""W3.5 weft-only cutover — the guard that keeps the hybrid state from coming back.

The served-base micromamba/conda env machinery was removed: the science lanes
(run_python/run_r, kernels, ensure_capability) go through weft ONLY. This test
fails loudly if any of it is reintroduced — a source-level grep guard plus the
behavioral invariant that a deployment with no base pack errors (never silently
runs a served base).

Run: .venv/bin/python tests/test_no_micromamba.py
"""
from __future__ import annotations
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# Scanned trees (product code only — tests may still reference the names in prose).
_SCAN_DIRS = [BACKEND / "core", BACKEND / "content"]
# Patterns that mean "the old micromamba/served-base path is back".
_FORBIDDEN = [
    re.compile(r"core\.exec\.mamba"),               # the deleted module
    re.compile(r"\brun_micromamba\b"),
    re.compile(r"\bensure_micromamba\b"),
    re.compile(r"import\s+tools_env\b"),             # the deleted conda tools env
    re.compile(r"Provisioning\(\s*conda"),          # conda via MaterializingExecutor
]


def _py_files():
    for d in _SCAN_DIRS:
        for p in d.rglob("*.py"):
            if "/tests/" in str(p) or p.name.startswith("test_"):
                continue
            yield p


def test_mamba_module_is_gone():
    assert not (BACKEND / "core/exec/mamba.py").exists(), \
        "core/exec/mamba.py is back — the micromamba bootstrap must stay deleted"


def test_no_micromamba_references_in_product_code():
    offenders = []
    for p in _py_files():
        text = p.read_text(errors="ignore")
        for pat in _FORBIDDEN:
            for m in pat.finditer(text):
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{p.relative_to(BACKEND)}:{line}  ({pat.pattern})")
    assert not offenders, (
        "micromamba/served-base machinery reintroduced:\n  " + "\n  ".join(offenders))


def test_no_pack_errors_instead_of_served_base():
    """base_env.require raises no_base_pack when no pack is declared, and
    run_python_code returns a structured error — never a served-base run."""
    _tmp = tempfile.mkdtemp(prefix="aba_nomm_")
    os.environ["ABA_RUNTIME_DIR"] = _tmp
    os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
    os.environ["ABA_WEFT_WORKSPACE"] = str(Path(_tmp) / "weft")
    os.environ["ABA_HOME"] = str(Path(_tmp) / "home")
    os.environ.pop("ABA_DB_PATH", None)

    from core import projects
    projects.init()
    pid = projects.create_project("nomm")["id"]
    projects.set_current(pid)
    from core.compute import adapter, base_env
    from core.compute.errors import ComputeError
    adapter.configure()

    # No bundle base pack in this bare deployment.
    assert base_env.pack_name("python") is None
    raised = False
    try:
        base_env.require("python")
    except ComputeError as e:
        raised = True
        assert e.code == "no_base_pack", e.code
    assert raised, "base_env.require must raise when no pack is declared"

    from core.exec.run import run_python_code
    r = run_python_code("import sys; print(sys.executable)", project_id=pid, run_id="nomm1")
    assert "error" in r and "pack is not available" in r["error"], r
    # Crucially: it did NOT run (no stdout from a served-base interpreter).
    assert not r.get("stdout"), "no-pack run must not execute on a served base"


def test_default_probe_python_does_not_swallow():
    """The silent-revival bug: _default_probe_python must not swallow a weft error
    into None (which routed installs to the old path). It returns None ONLY when no
    pack is declared; a pack-declared-but-unrealizable case propagates."""
    import inspect
    from content.bio.tools import discovery
    src = inspect.getsource(discovery._default_probe_python)
    assert "except Exception" not in src, \
        "_default_probe_python swallows again — a weft error must propagate, not become None"


TESTS = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    fails = 0
    for fn in TESTS:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:
            fails += 1
            import traceback
            traceback.print_exc()
            print(f"  FAIL {fn.__name__}: {e!r}")
    print(f"\n{'ALL PASS' if not fails else str(fails)+' FAILED'} ({len(TESTS)} tests)")
    sys.exit(1 if fails else 0)
