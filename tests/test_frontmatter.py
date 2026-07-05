"""Canonical frontmatter parser (burn-down #4). Pins the contract the skills +
memory loaders share, so the dedup can't silently change parsing behavior.
Standalone-runnable (base env may lack pytest)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core.frontmatter import parse_frontmatter  # noqa: E402


def test_valid_frontmatter():
    fm, body = parse_frontmatter("---\nname: x\ntype: reference\n---\nhello world")
    assert fm == {"name": "x", "type": "reference"}
    assert body == "hello world"


def test_no_frontmatter_returns_empty_and_stripped_body():
    fm, body = parse_frontmatter("  just a body\n")
    assert fm == {}
    assert body == "just a body"


def test_empty_frontmatter_block():
    fm, body = parse_frontmatter("---\n---\nbody")
    assert fm == {}
    assert body == "body"


def test_unterminated_raises():
    raised = False
    try:
        parse_frontmatter("---\nname: x\nno closing fence")
    except ValueError:
        raised = True
    assert raised, "unterminated frontmatter must raise ValueError"


def test_non_mapping_raises():
    raised = False
    try:
        parse_frontmatter("---\n- just\n- a\n- list\n---\nbody")
    except ValueError:
        raised = True
    assert raised, "non-mapping frontmatter must raise ValueError"


def test_leading_fence_but_body_only():
    # A '---' that is a horizontal rule mid-body (not at pos 0) is not frontmatter.
    fm, body = parse_frontmatter("intro\n\n---\n\nmore")
    assert fm == {}
    assert body == "intro\n\n---\n\nmore"


if __name__ == "__main__":
    for fn in [test_valid_frontmatter, test_no_frontmatter_returns_empty_and_stripped_body,
               test_empty_frontmatter_block, test_unterminated_raises, test_non_mapping_raises,
               test_leading_fence_but_body_only]:
        fn(); print("PASS", fn.__name__)
    print("all passed")
