"""The model SDK must be pinned below the major that changed its HTTP client.

Measured live 2026-08-25, on a staged rebuild. `anthropic` was declared
unpinned in install/core/environment*.yml. A rebuild resolved it to 1.0.0,
which switched the SDK's HTTP transport from `httpx` to `httpx2`:

    TypeError: Invalid `http_client` argument;
    Expected an instance of `httpx2.AsyncClient`
    but got <class 'httpx.AsyncClient'>

core/llm.py builds every client as `AsyncAnthropic(..., http_client=
_httpx_async_client())` — an httpx client, deliberately, because the
AsyncClient runs over HTTP/2 and needs httpx[http2]'s h2 extras. So every
chat turn in the new image failed to construct a provider and reported "No
model provider is connected yet". Not one line of our code had changed.

This is the SECOND time an unpinned dependency silently broke the product on
rebuild: `mcp` 2.0.0 moved `mcp.server.fastmcp` and took the tool catalog to
zero (2026-08-08, guarded in test_tool_catalog_liveness.py). Same shape, same
file, one line apart.

What made it survivable was the deploy's surface tier catching it before
promote. What made it reach a staged image at all is that nothing pins it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
ENVS = [ROOT / "install" / "core" / "environment.yml",
        ROOT / "install" / "core" / "environment-boot.yml"]
LLM = ROOT / "backend" / "core" / "llm.py"


def _entries(env: Path) -> list[str]:
    lines = [ln.split("#", 1)[0].strip() for ln in env.read_text().splitlines()]
    return [ln[2:].strip().strip('"').strip("'") for ln in lines if ln.startswith("- ")]


def test_the_argument_we_pin_FOR_is_still_in_use():
    """ARMED. The pin is justified by one call shape. If llm.py stops handing
    the SDK an httpx client, the pin is guarding a ghost and should be
    revisited rather than kept forever."""
    assert "http_client=" in LLM.read_text(), (
        "core/llm.py no longer passes http_client= — re-check whether the "
        "`anthropic <1` pin in install/core/environment*.yml is still needed")


@pytest.mark.parametrize("env", ENVS, ids=[e.name for e in ENVS])
def test_anthropic_is_pinned_below_the_major_that_swapped_httpx(env):
    """`anthropic` 1.0.0 expects httpx2.AsyncClient. We pass httpx.AsyncClient
    (for the h2 extras), so an unpinned resolve produces an image that cannot
    talk to any model — a total outage with no code change."""
    got = [e for e in _entries(env) if re.match(r"^anthropic\b", e)]
    assert got, f"{env.name}: no anthropic dep found — did the scanner break?"
    assert any(re.search(r"[<=]", e) for e in got), (
        f"{env.name}: `anthropic` is unpinned ({got}). 1.0.0 swapped httpx for "
        f"httpx2 and rejects the http_client core/llm.py passes; every turn "
        f"then reports 'No model provider is connected yet'")
