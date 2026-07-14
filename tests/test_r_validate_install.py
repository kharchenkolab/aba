"""V-1 — propose_capability validator clarity.

When the agent passes an `r_package` proposal with `source=github`, the
validator must reject malformed `package` fields with a message that
tells the agent (a) which field is wrong, (b) what the right shape is,
(c) what 'ref' is for, and (d) what value it actually received.

The agent that triggered this fix (prj_3c4eb185, 2026-06-09) had put
'owner/repo' into `ref` and the branch into `revision`. The old
message ('github package must be owner/repo') named the right field
but the agent didn't realize 'ref' was the branch slot — it cost one
round-trip to recover.

Run: .venv/bin/python tests/test_r_validate_install.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_validate_install_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH",):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.exec.r import validate_install  # noqa: E402


def test_github_owner_slash_repo_passes():
    assert validate_install("github", "kharchenkolab/pagoda2", "devel") is None


def test_github_missing_owner_returns_actionable_error():
    """The shape of the error message is part of the contract — the
    agent needs each cue to recover in ONE turn:"""
    msg = validate_install("github", "pagoda2", "devel")
    assert msg is not None
    # (a) names the field that was wrong
    assert "package" in msg
    # (b) says what shape it should have
    assert "owner/repo" in msg
    # (c) tells the agent what 'ref' is for (the part that cost the round-trip)
    assert "ref" in msg
    assert any(w in msg for w in ("branch", "tag", "commit"))
    # (d) echoes what was received, so the agent doesn't have to guess
    assert "pagoda2" in msg


def test_github_empty_package_returns_actionable_error():
    msg = validate_install("github", "", None)
    assert msg is not None
    assert "owner/repo" in msg


def test_cran_valid_passes():
    assert validate_install("cran", "Seurat", None) is None


def test_unknown_source_message_unchanged():
    """V-1 only touches the github branch. Other validator messages keep
    their existing wording — regression guard."""
    msg = validate_install("svn", "anything", None)
    assert msg is not None
    assert "unknown R source" in msg


def test_invalid_ref_message_unchanged():
    msg = validate_install("github", "kharchenkolab/pagoda2", "bad ref with spaces")
    assert msg is not None
    assert "ref" in msg
    assert "invalid characters" in msg


# ─── runner ────────────────────────────────────────────────────────────────
TESTS = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    fails = 0
    for fn in TESTS:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:
            fails += 1
            import traceback; traceback.print_exc()
            print(f"  FAIL {fn.__name__}: {e!r}")
    if fails:
        print(f"\n{fails}/{len(TESTS)} FAILED")
        sys.exit(1)
    print(f"\nall {len(TESTS)} tests passed")
