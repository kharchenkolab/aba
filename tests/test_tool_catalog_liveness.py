"""The agent's tool catalog must never be silently EMPTY.

`aba_core` IS the catalog — `TOOL_SCHEMAS` is pruned in favour of it — so a
failure to register that one in-process server takes the agent from "can do
things" to "can only talk", with no other symptom. Every turn still returns a
fluent answer. Nothing errors. `/api/health` stayed `{"ok": true, "degraded":
false}`.

Measured live 2026-08-08. The `mcp` dependency was declared unpinned; a rebuild
resolved it to 2.0.0, which moved `mcp.server.fastmcp`; the import raised; the
handler printed one line to a log nobody was tailing. Three concurrent agent
turns then answered in prose with **zero tool calls**, and the run read as a
concurrency failure — the instrument reported "lanes did not recall their
state" when the truth was that no lane had run anything at all.

Two guards, because there are two independent ways back into that state:

  * the DEPENDENCY can drift again (this is why the pin exists), and
  * the failure can go unreported again (this is why the selfcheck exists).

The second is the load-bearing one. A pin only stops the cause we already know
about; the selfcheck catches the next cause, whatever it is.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
ENVS = [ROOT / "install" / "core" / "environment.yml",
        ROOT / "install" / "core" / "environment-boot.yml"]
LIFESPAN = ROOT / "backend" / "lifespan.py"
ABA_CORE = ROOT / "backend" / "content" / "bio" / "mcp_servers" / "aba_core" / "server.py"


# ── the dependency that broke ────────────────────────────────────────────────

def test_the_import_we_pin_FOR_is_still_the_one_in_use():
    """ARMED. The pin below is justified by ONE import. If aba_core stops
    importing `mcp.server.fastmcp`, this file is guarding a ghost — and the
    pin should be revisited rather than silently kept forever."""
    assert "from mcp.server.fastmcp import" in ABA_CORE.read_text(), (
        "aba_core no longer imports mcp.server.fastmcp — re-check whether the "
        "`mcp <2` pin in install/core/environment*.yml is still needed")


@pytest.mark.parametrize("env", ENVS, ids=[e.name for e in ENVS])
def test_mcp_is_pinned_below_the_release_that_moved_it(env):
    """`mcp` 2.0.0 moved `mcp.server.fastmcp`. An unpinned dep is how the
    catalog went to zero without a single line of code changing."""
    text = env.read_text()
    lines = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
    entries = [ln[2:].strip().strip('"').strip("'") for ln in lines if ln.startswith("- ")]
    mcp = [e for e in entries if re.match(r"^mcp\b", e)]
    assert mcp, f"{env.name}: no mcp dep found — did the scanner break?"
    assert any(re.search(r"[<=]", e) for e in mcp), (
        f"{env.name}: `mcp` is unpinned ({mcp}). 2.0.0 moved mcp.server.fastmcp, "
        f"which aba_core imports, and an empty catalog is a silent outage")


# ── the reporting path, which is what generalizes ────────────────────────────

def _tool_catalog_check(n_tools, *, raises=False, err=None):
    """Build the selfcheck exactly as lifespan does, against a fake gateway."""
    aba_core_error = err

    def check() -> dict:
        try:
            if raises:
                raise RuntimeError("gateway down")
            n = n_tools
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "severity": "critical",
                    "detail": f"tool catalog unreadable: {type(e).__name__}: {e}"}
        if n:
            return {"ok": True, "severity": "info", "detail": f"{n} tools exposed"}
        return {"ok": False, "severity": "critical",
                "detail": ("the agent's tool catalog is EMPTY — it can answer but "
                           "cannot act; every turn will look plausible and do "
                           "nothing"
                           + (f" (aba_core: {aba_core_error})" if aba_core_error else ""))}
    return check


def test_an_empty_catalog_is_CRITICAL_not_a_warning():
    """It is not a degraded feature; it is the agent unable to act at all."""
    r = _tool_catalog_check(0)()
    assert r["ok"] is False and r["severity"] == "critical", r


def test_the_empty_catalog_detail_carries_the_CAUSE():
    """The operator needs the import error, not just the symptom — that is the
    difference between a 5-minute fix and a day of guessing at agent behaviour."""
    r = _tool_catalog_check(0, err="ModuleNotFoundError: No module named "
                                   "'mcp.server.fastmcp'")()
    assert "mcp.server.fastmcp" in r["detail"], r["detail"]


def test_a_populated_catalog_is_silent():
    """CEILING: a check that fires on a healthy install is noise."""
    r = _tool_catalog_check(37)()
    assert r["ok"] is True and "37" in r["detail"]


def test_an_UNREADABLE_gateway_is_also_critical():
    """WIDE. 'cannot tell' must not read as healthy here: the catalog is the
    one thing whose absence is invisible from the outside."""
    r = _tool_catalog_check(0, raises=True)()
    assert r["ok"] is False and r["severity"] == "critical"


# ── the wiring: a check that is never registered reports nothing ─────────────

def test_lifespan_REGISTERS_the_check():
    """ARMED, against the failure that made this necessary in the first place —
    a signal that exists but reaches no surface."""
    src = LIFESPAN.read_text()
    assert 'selfcheck.register("tool_catalog"' in src, (
        "the tool-catalog check is not registered in lifespan — /api/health "
        "would stay green with an empty catalog again")


def test_the_aba_core_failure_is_CAPTURED_not_just_printed():
    """The handler used to print and drop the exception. Capturing it is what
    lets the health surface name the cause."""
    src = LIFESPAN.read_text()
    assert "_aba_core_error" in src
    assert re.search(r"except Exception as e:.*\n.*aba_core in-process server failed"
                     r".*\n\s*_aba_core_error\s*=", src), \
        "the aba_core failure is printed but not captured for the health surface"


def test_selfcheck_severity_ranks_critical_above_warning():
    """The health endpoint reports `worst`; this check is only useful if
    'critical' actually outranks the settings warnings it sits beside."""
    sys.path.insert(0, str(ROOT / "backend"))
    from core.runtime import selfcheck
    assert selfcheck._SEV_ORDER["critical"] > selfcheck._SEV_ORDER["warning"]


def test_a_registered_check_reaches_the_results(monkeypatch):
    """End of the wire: register → run() → a not-ok record an operator can see."""
    sys.path.insert(0, str(ROOT / "backend"))
    from core.runtime import selfcheck
    monkeypatch.setattr(selfcheck, "_checks", {}, raising=False)
    monkeypatch.setattr(selfcheck, "_results", [], raising=False)
    selfcheck.register("tool_catalog", _tool_catalog_check(0))
    out = selfcheck.run()
    rec = next(r for r in out if r["name"] == "tool_catalog")
    assert rec["ok"] is False and rec["severity"] == "critical"
    assert selfcheck.degraded() is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
