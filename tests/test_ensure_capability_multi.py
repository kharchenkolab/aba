"""P0: ensure_capability accepts multiple package names (list) — fixes recipes that
called ensure_capability("numpy","scipy") intending several packages (the 2nd positional
wrongly landed in `source`). Single-capability overrides (source/package/ref) stay valid
only with ONE name. We stub the impl to assert dispatch, not real installs."""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _make_tool(monkeypatch_calls):
    """Register ensure_capability against a fake FastMCP that just captures the fn,
    with content.bio.tools.ensure_capability stubbed to record each (name, overrides)."""
    captured = {}

    class _FakeMCP:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    # stub the impl: record the input_ dict, echo a ready result
    import content.bio.tools as _t
    def _stub_impl(input_, ctx=None):
        monkeypatch_calls.append(dict(input_))
        return {"status": "ready", "name": input_.get("name")}
    orig = _t.ensure_capability
    _t.ensure_capability = _stub_impl

    from content.bio.mcp_servers.aba_core.tools.discovery import register_discovery_tools
    register_discovery_tools(_FakeMCP())
    return captured["ensure_capability"], (lambda: setattr(_t, "ensure_capability", orig))


def test_list_of_names_ensures_each():
    calls = []
    fn, restore = _make_tool(calls)
    try:
        out = fn(["numpy", "scipy", "pandas"])
    finally:
        restore()
    assert [c["name"] for c in calls] == ["numpy", "scipy", "pandas"], calls
    # no bogus source/package leaked onto any call
    assert all("source" not in c and "package" not in c for c in calls), calls
    assert out["status"] == "ok" and out["ensured"] == ["numpy", "scipy", "pandas"]
    assert len(out["results"]) == 3


def test_single_name_still_works_with_overrides():
    calls = []
    fn, restore = _make_tool(calls)
    try:
        out = fn("mypkg", source="github", package="owner/repo", ref="dev")
    finally:
        restore()
    assert len(calls) == 1 and calls[0]["name"] == "mypkg"
    assert calls[0]["source"] == "github" and calls[0]["package"] == "owner/repo"
    assert out["status"] == "ready"


def test_list_with_overrides_is_rejected():
    calls = []
    fn, restore = _make_tool(calls)
    try:
        out = fn(["numpy", "scipy"], source="bioconda")
    finally:
        restore()
    assert out["status"] == "error" and "single" in out["note"].lower()
    assert calls == [], "must not dispatch when overrides+list are ambiguous"


def test_partial_status_when_one_not_ready():
    calls = []
    fn, restore = _make_tool([])   # placeholder; override impl below
    # custom impl: second one not_found
    import content.bio.tools as _t
    seq = iter([{"status": "ready", "name": "a"}, {"status": "not_found", "name": "b"}])
    _t.ensure_capability = lambda input_, ctx=None: next(seq)
    try:
        out = fn(["a", "b"])
    finally:
        restore()
    assert out["status"] == "partial" and "b(not_found)" in out["note"], out


if __name__ == "__main__":
    test_list_of_names_ensures_each(); print("ok  list ensures each (no source leak)")
    test_single_name_still_works_with_overrides(); print("ok  single + overrides")
    test_list_with_overrides_is_rejected(); print("ok  list+overrides rejected")
    test_partial_status_when_one_not_ready(); print("ok  partial status")
    print("all ensure_capability multi-package tests passed")
