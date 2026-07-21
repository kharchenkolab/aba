"""regtest harness — seed-staging guard (runner exit 3 → sweep setup-error).

A scenario whose declared `data_files` are not all present in DATA_DIR runs the
agent against incomplete inputs; the agent then correctly refuses to fabricate
and every downstream produce/pin step fails as if the PRODUCT under-performed.
That masquerade cost a full investigation. The guard turns a missing seed into
a loud SETUP-ERROR (runner exit 3) that the sweep treats like infra — never
scored, never baked into a baseline — rather than a 0-score regression.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "regtest" / "harness"))

pytestmark = pytest.mark.platform


def test_sweep_classifies_setup_error_as_unscored():
    """Runner exit 3 (seed guard) → an infra-flagged error row: mech_total None
    (unscored), infra truthy (never baked by --accept), reason names the gap."""
    import sweep
    row = sweep.score_of({"_error": "SETUP-ERROR: declared data_files missing "
                                    "from DATA_DIR (scenario fixture/staging gap "
                                    "— not a product failure)",
                          "_setup_error": True, "_infra": 1})
    assert row["mech_total"] is None          # unscored — not a 0/N regression
    assert row["infra"]                        # --accept skips infra rows
    assert any("SETUP-ERROR" in f for f in row["fails"])


def test_ordinary_error_is_not_infra():
    """A plain runner error (no _setup_error) stays a real failure, infra 0 —
    the guard must not accidentally launder genuine crashes."""
    import sweep
    row = sweep.score_of({"_error": "no run dir produced"})
    assert row["mech_total"] is None and not row["infra"]


def test_declared_data_files_name_normalization():
    """The guard compares BASENAMES across the declared shapes (str, or a dict
    with name/path) so a declared 'sub/x.tsv' matches a staged 'x.tsv' — mirrors
    the runner's `Path(d).name` normalization."""
    decls = ["a.csv", {"name": "b.tsv"}, {"path": "sub/c.parquet"}, {"bogus": 1}, ""]
    names = [d if isinstance(d, str) else (d.get("name") or d.get("path") or "")
             for d in decls]
    names = [Path(n).name for n in names if n]
    assert names == ["a.csv", "b.tsv", "c.parquet"]
