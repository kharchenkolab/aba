"""Golden-path tool smoke test — the guard for the *missing-dependency /
silent-soft-error* failure class.

Motivation (the `tabulate` incident, 2026-07): `read_csv_info` calls
`pandas.DataFrame.to_markdown()`, which needs the optional `tabulate` package.
`tabulate` was NOT declared in `install/core/environment.yml`, yet every dev/test
env had it transitively — so every proxy was green while a clean deploy env broke
on the very first real use. Same class as the earlier `httpx[http2]`/`h2` scar.

This test closes that gap two ways:
  1. **Import closure** — importing every tool entry-point module surfaces any
     import-time missing dependency.
  2. **Golden path** — actually calling the data tools on a fixture surfaces
     *call-time* missing deps (like `tabulate`, only imported inside to_markdown)
     and any `{"error": ...}` soft-failure that no other test asserts against.
  3. **Graceful degradation** — even if the optional dep vanishes, the tool must
     degrade to a plain-text preview, never raise/hard-fail.

IMPORTANT: the full value of (1)+(2) is realized when this runs in a **clean env
built from `install/core/environment.yml`** (the deployment substrate) — not the
dev env, which may carry deps transitively. Wire that clean-env run into CI; this
file encodes the assertions.
"""
from __future__ import annotations
import os
import sys
import importlib
import pkgutil
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="aba_toolsmoke_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "smoke.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.graph._schema import init_db  # noqa: E402
init_db()
import content.bio  # noqa: E402,F401  (registers the pack + tool modules)


def test_all_tool_modules_import():
    """Every module under content.bio.tools must import — catches import-time
    missing deps (the class, at module scope)."""
    import content.bio.tools as tools_pkg
    failures = {}
    for m in pkgutil.iter_modules(tools_pkg.__path__):
        name = f"content.bio.tools.{m.name}"
        try:
            importlib.import_module(name)
        except Exception as e:  # noqa: BLE001
            failures[name] = repr(e)
    assert not failures, f"tool modules failed to import: {failures}"


def test_read_csv_golden_path():
    """read_csv_info on a real CSV must return a populated preview + column
    info with NO soft-error — this is the exact path `tabulate` broke."""
    from core.config import project_data_dir
    from core.projects import current as current_project_id   # moved (burn-down #1)
    from content.bio.tools.ctx_read import read_csv_info
    d = project_data_dir(current_project_id())
    (d / "smoke.csv").write_text("gene,count,group\nACT1,101,ctrl\nTUB1,57,treat\n")
    res = read_csv_info({"filename": "smoke.csv"})
    assert "error" not in res, f"soft-error from read_csv_info: {res.get('error')}"
    assert res["rows"] == 2 and res["columns"] == 3, res
    assert res.get("preview"), "empty preview"
    assert "ACT1" in res["preview"], f"preview missing data: {res['preview']!r}"


def test_read_csv_degrades_without_tabulate(monkeypatch):
    """If the optional markdown backend is ever unavailable, the tool must fall
    back to a plain-text preview rather than hard-fail (belt-and-suspenders on
    the fix, independent of what the env happens to carry)."""
    import pandas as pd
    from core.config import project_data_dir
    from core.projects import current as current_project_id   # moved (burn-down #1)
    from content.bio.tools.ctx_read import read_csv_info

    def _boom(self, *a, **k):
        raise ImportError("Missing optional dependency 'tabulate'.")
    monkeypatch.setattr(pd.DataFrame, "to_markdown", _boom, raising=True)

    d = project_data_dir(current_project_id())
    (d / "smoke2.csv").write_text("a,b\n1,2\n3,4\n")
    res = read_csv_info({"filename": "smoke2.csv"})
    assert "error" not in res, f"did not degrade gracefully: {res.get('error')}"
    assert res.get("preview") and "1" in res["preview"], res


if __name__ == "__main__":
    # Standalone runner (matches the repo's script-style tests). monkeypatch is
    # pytest-only, so run that one under pytest; run the other two directly.
    test_all_tool_modules_import()
    print("PASS test_all_tool_modules_import")
    test_read_csv_golden_path()
    print("PASS test_read_csv_golden_path")
    print("(run test_read_csv_degrades_without_tabulate under pytest — needs monkeypatch)")
