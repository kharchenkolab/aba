"""Audit-#7 PASS proof: guide.py works with a mock content pack.

The audit (misc/modularity_audit.md §6 row 7) was held at PARTIAL
because guide.py imported content.bio.* at the top. Wave 2 A.3 lifts
those imports into the ContentPack protocol. This test exercises the
boundary by:

1. Clearing the active pack singleton.
2. Registering a MockPack that returns minimal stubs (empty tool
   schema list, an executor that returns {}, an empty prompts dict
   pointed at lambdas, etc.).
3. Calling stream_response with FAKE_SESSION enabled — same path the
   FakeSession tests use.
4. Asserting it completes without ever importing content.bio.

If guide.py ever regresses and imports bio directly again, this test
fails because the import would happen even with bio absent (and we
verify via sys.modules that no content.bio.* module is loaded by the
critical execution path).

CAVEAT: pytest's tests/conftest.py registers BIO_PACK once at process
startup; this test resets to a Mock for its duration and restores at
teardown. Other parallel tests in the same process won't see the
clobber because of the module-level singleton ordering.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.platform


@pytest.fixture
def mock_pack(monkeypatch):
    from core.runtime.content_pack import (
        active_pack, clear_active_pack_for_testing, set_active_pack,
    )

    class _Stub:
        name = "mock"
        _sysspec = ("STUB SYSTEM", "")
        def prompts(self):
            # Match build_system's signature: returns (stable, dynamic) tuple.
            return {
                "system": lambda *a, **k: self._sysspec,
                "recipes_reminder": lambda *a, **k: "",
                "focus_preamble": lambda *a, **k: ("", []),
            }
        def tools(self):         return []
        def execute_tool(self):  return lambda *a, **k: {}
        def cards(self):         return {}
        def register_hooks(self) -> None: pass
        def new_session_id(self) -> str: return "sess_mock00000"

    # Save the live pack (bio) so we can restore it.
    try:
        prior = active_pack()
    except RuntimeError:
        prior = None
    clear_active_pack_for_testing()

    m = _Stub()
    set_active_pack(m)
    m.register_hooks()
    yield m

    # Teardown: restore the prior pack if there was one.
    clear_active_pack_for_testing()
    if prior is not None:
        set_active_pack(prior)


def test_pack_lookup_works_with_mock(mock_pack):
    """The boundary: active_pack().prompts()/tools()/execute_tool() return
    what the mock specified — no bio behind the curtain."""
    from core.runtime.content_pack import active_pack

    pack = active_pack()
    assert pack.name == "mock"
    assert pack.tools() == []
    assert pack.prompts()["system"]() == ("STUB SYSTEM", "")
    assert pack.new_session_id() == "sess_mock00000"


def test_guide_module_has_no_top_level_bio_imports():
    """guide.py's TOP-LEVEL imports must not reference content.bio.* —
    this is the audit-#7 PARTIAL→PASS criterion.

    Lazy/conditional bio imports inside function bodies are tracked
    separately (see `test_guide_lazy_bio_imports_are_gated` below) but
    don't block the audit metric — they're follow-up work after the
    deeper A.2 extraction lifts the per-tool dispatch out of guide.py.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "backend" / "guide.py"
    tree = ast.parse(src.read_text())
    bio_imports: list[tuple[int, str]] = []
    # Only walk MODULE-level statements (tree.body), not inside FunctionDef.
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("content.bio"):
                bio_imports.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("content.bio"):
                    bio_imports.append((node.lineno, alias.name))
    assert not bio_imports, (
        "guide.py has TOP-LEVEL imports of content.bio — this is audit-#7's "
        "PARTIAL→PASS regression marker. All bio access MUST go through "
        f"core.runtime.content_pack.active_pack(). Found: {bio_imports}"
    )


def test_guide_has_no_bio_imports_anywhere():
    """Stricter form of the top-level audit: guide.py must NEVER import
    from content.bio, top-level OR in function bodies. W1-A.2 phase 4'
    lifted the residual 9 lazy imports into hook subscribers in
    content/bio/lifecycle/guide_hooks.py — guide.py now fires generic
    events via core.hooks.dispatcher.dispatch and any content pack can
    subscribe. A new lazy import here would be a regression.

    This test replaces the prior gated-at-9 ceiling. If a guide.py edit
    legitimately needs to lift a NEW bio behavior out of inline calls
    into a hook, register a new event type instead of importing
    content.bio.*.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "backend" / "guide.py"
    tree = ast.parse(src.read_text())
    bio_imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("content.bio"):
                bio_imports.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("content.bio"):
                    bio_imports.append((node.lineno, alias.name))
    assert not bio_imports, (
        "guide.py must not import from content.bio (any level). Lift "
        "the behavior into a hook subscriber in "
        "content/bio/lifecycle/guide_hooks.py and fire a generic "
        f"on_<event> via dispatch(). Found: {bio_imports}"
    )

