"""Shared precondition gate for tests that need a CONFIGURED COMPUTE SUBSTRATE.

Some guards drive `run_python` / `run_r` for real (harvest helpers, exec-record
shapes, revision seeds). In a bare test process the substrate is not configured,
so those calls return `substrate_offline` and the test can prove nothing.

Historically such tests bailed with an early `return` after a non-raising
`check()`, which read as PASS under pytest — a guard that measured NOTHING
reporting green. Skipping says so out loud instead, and still runs the test for
real on a box/CI where the substrate IS up (which excluding it from the gate
would not).

Works under both runners: `pytest.skip` when pytest is driving, and a printed
SKIP + `True` return for the module's own `__main__` runner, whose loops treat a
raised Skipped as a failure.
"""
from __future__ import annotations

import os


def substrate_offline_reason() -> str | None:
    """Why the compute substrate is unusable here, or None when it is fine."""
    try:
        from core.compute import status
        st = status() or {}
        if st.get("ok"):
            return None
        return str(st.get("detail") or "compute substrate not configured")
    except Exception as e:  # noqa: BLE001 — import/config failure is also "offline"
        return f"compute substrate unavailable: {type(e).__name__}: {e}"


def skip_without_substrate() -> bool:
    """Skip (pytest) or signal an honest early return (standalone).

    Returns True when the caller should `return` immediately; False to proceed.
    Never returns True under pytest — it raises Skipped there."""
    reason = substrate_offline_reason()
    if reason is None:
        return False
    msg = f"needs a configured compute substrate — {reason}"
    if os.environ.get("PYTEST_CURRENT_TEST"):
        import pytest
        pytest.skip(msg)
    print(f"  [SKIP] {msg}")
    return True
