"""Follow-on 2 — the agent-promotion discipline rule is injected into the primary
agent's system prompt (modularity2 §4a)."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_promo_rule_")
os.environ.update({
    "ABA_DB_PATH": str(Path(_tmp) / "t.db"),
    "ABA_RUNTIME_DIR": _tmp,
    "ARTIFACTS_DIR": str(Path(_tmp) / "artifacts"),
    "ABA_WORK_DIR": str(Path(_tmp) / "work"),
    "DATA_DIR": str(Path(_tmp) / "data"),
})
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db   # noqa: E402
init_db()
import content.bio  # noqa: E402,F401
from content.bio.prompts.build import build_system   # noqa: E402


def _text(out):
    return out if isinstance(out, str) else out[0]


def test_promotion_rule_in_primary_prompt():
    prompt = _text(build_system([], role="primary"))
    assert "Promotion discipline" in prompt
    assert "Promote sparingly" in prompt
    assert "Never promote silently" in prompt


def test_promotion_md_is_a_bundle_rule():
    # it lives in the bundle (system scope), so a lab/institution can override it
    assert (ROOT / "backend/system_bundle/rules/promotion.md").is_file()
