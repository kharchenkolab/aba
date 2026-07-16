"""Mount weft-ui (the expert compute surface) inside aba's server —
misc/compute_settings.md §8: one process, one port, one origin, ONE Weft.

The mount runs weft-ui in shared-controller mode: its panels serve the
same `Weft` instance aba's WeftAdapter embeds (the controller factory
resolves at the sub-app's lifespan startup, which the attach() chaining
runs AFTER aba's own startup — so compute.configure() has already
happened). If weft-ui isn't installed, or the substrate is offline,
everything degrades: no mount / a disabled mount, and the Settings tab
simply hides its "Advanced ↗" affordances.

The bearer token is minted per boot and only ever handed out through
/api/compute/advanced — same trust domain as every other aba API.
"""
from __future__ import annotations

import secrets
from typing import Optional

MOUNT_PATH = "/weft"

_state: dict = {"available": False, "token": None}


def mount(app) -> bool:
    """Attach weft-ui under MOUNT_PATH (call at app build time, before any
    catch-all routes). Returns False (and stays unmounted) when weft-ui is
    not installed in this environment."""
    try:
        from weft_ui.embed import attach
    except ImportError:
        return False
    from core.compute.adapter import weft_workspace

    def _controller():
        # resolved at sub-lifespan startup; raises when the substrate is
        # offline — weft-ui then degrades that mount, aba boots fine
        from core.compute.adapter import get_compute
        return get_compute().raw_controller()

    token = secrets.token_urlsafe(24)
    attach(app, path=MOUNT_PATH, workspace=weft_workspace(), token=token,
           controller=_controller)
    _state.update(available=True, token=token)
    return True


def advanced_url(site: Optional[str] = None) -> Optional[str]:
    """The 'Advanced ↗' target: weft-ui's Compute page (chat rail hidden),
    optionally deep-linked to one site. None when the mount is absent."""
    if not _state["available"]:
        return None
    anchor = "#/compute" + (f"/{site}" if site else "")
    return f"{MOUNT_PATH}/?token={_state['token']}&hide=chat{anchor}"
