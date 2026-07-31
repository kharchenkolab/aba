"""A relative dataset path must bind to the CALLING kernel's sandbox.

Field failure: an agent downloaded a set of files into its kernel cwd,
verified every one (size + decompression), and registered the directory by
relative name. The dataset that appeared in the project was a DIFFERENT copy —
a partial download under the same name left in a kernel sandbox by a session
two days earlier. Two of its three files were truncated; the entity went
`status: active` carrying the agent's verification claim, which was true of
the copy it made and false of the copy adopted. Two agents then spent four
minutes and eight tool calls diagnosing damaged data that was intact at its
source.

The mechanism was `_scratch_bases`: it offers EVERY local kernel jobdir as a
resolution candidate — not the calling one — and `_resolve_dataset_path` takes
first-existing-wins with no recency tiebreak. Any stale kernel holding a
same-named directory outranks the live one by accident of store order.

Two guards, because either alone leaves the hole open:
  * the calling kernel (the Run's recorded `weft_targets`) is preferred;
  * with no such hint, the NEWEST sandbox wins, never an arbitrary one.
"""
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.bio


def _mk_sandbox(root: Path, kid: str, payload: bytes, mtime: float) -> Path:
    """A kernel jobdir holding ./incoming/SET/part.gz with `payload` bytes."""
    d = root / "site-local" / kid / "incoming" / "SET"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "part.gz"
    f.write_bytes(payload)
    os.utime(f, (mtime, mtime))
    os.utime(d, (mtime, mtime))
    os.utime(d.parent, (mtime, mtime))
    os.utime(root / "site-local" / kid, (mtime, mtime))
    return d


@pytest.fixture()
def two_sandboxes(tmp_path, monkeypatch):
    """A STALE kernel (older, listed first — the field's ordering) and the
    LIVE one the caller is actually running in."""
    ws = tmp_path / "weft"
    now = time.time()
    stale = _mk_sandbox(ws, "krn_stale", b"partial", now - 2 * 86400)
    live = _mk_sandbox(ws, "krn_live", b"complete-payload", now)

    from content.bio.tools import curation

    monkeypatch.setattr(curation, "_ctx_thread", lambda ctx: "thr_x", raising=False)

    class _Compute:
        def sync_call(self, name, *a, **k):
            if name == "list_kernels":
                # stale FIRST — exactly the order that lost in the field
                return {"kernels": [{"jobdir": "krn_stale", "site": "local"},
                                    {"jobdir": "krn_live", "site": "local"}]}
            return {}

    import core.compute.adapter as _ad
    monkeypatch.setattr(_ad, "get_compute", lambda: _Compute())
    monkeypatch.setattr(_ad, "weft_workspace", lambda: ws)
    return stale, live


def _resolve(monkeypatch, weft_targets):
    """Resolve './incoming/SET' with the Run advertising `weft_targets`."""
    from content.bio.tools import curation
    import content.bio.lifecycle.runs as runs
    import core.graph.entities as ents

    monkeypatch.setattr(runs, "active_run_id", lambda tid: "run_1", raising=False)
    monkeypatch.setattr(
        ents, "get_entity",
        lambda rid: {"artifact_path": None,
                     "metadata": {"weft_targets": list(weft_targets)}},
        raising=False)
    return Path(curation._resolve_dataset_path("./incoming/SET", {"thread_id": "thr_x"}))


def test_calling_kernel_wins_over_a_stale_sandbox(two_sandboxes, monkeypatch):
    """THE field bug. The Run names the kernel it ran in; that sandbox must be
    the one a relative path resolves against, whatever order the store lists
    kernels in."""
    stale, live = two_sandboxes
    got = _resolve(monkeypatch, ["krn_live"])
    assert got == live, (
        f"relative path bound to {got} — the caller ran in {live}; binding to "
        f"another kernel's sandbox adopts someone else's bytes under the "
        f"agent's name")
    assert (got / "part.gz").read_bytes() == b"complete-payload"


def test_without_a_kernel_hint_the_newest_sandbox_wins(two_sandboxes, monkeypatch):
    """Defence in depth: no recorded target (a bare thread, a pre-run call) must
    still not mean 'whichever the store happened to list first'. Recency is the
    only defensible tiebreak — the agent's own write is the newest thing there."""
    stale, live = two_sandboxes
    got = _resolve(monkeypatch, [])
    assert got == live, (
        f"with no kernel hint the resolver chose {got}; a two-day-old sandbox "
        f"must never outrank the one just written to")


def test_stale_sandbox_still_reachable_when_it_is_the_only_hit(tmp_path, monkeypatch):
    """WIDE, the false-negative side: preferring the caller must not make other
    sandboxes unreachable — a legitimate cross-kernel register (the agent wrote
    in an earlier kernel of the same run) must still resolve."""
    ws = tmp_path / "weft"
    now = time.time()
    only = _mk_sandbox(ws, "krn_other", b"payload", now - 86400)

    from content.bio.tools import curation
    monkeypatch.setattr(curation, "_ctx_thread", lambda ctx: "thr_x", raising=False)

    class _Compute:
        def sync_call(self, name, *a, **k):
            return ({"kernels": [{"jobdir": "krn_other", "site": "local"}]}
                    if name == "list_kernels" else {})

    import core.compute.adapter as _ad
    monkeypatch.setattr(_ad, "get_compute", lambda: _Compute())
    monkeypatch.setattr(_ad, "weft_workspace", lambda: ws)
    got = _resolve(monkeypatch, ["krn_live_but_gone"])
    assert got == only, (
        f"the only existing sandbox became unreachable ({got}) — preferring the "
        f"caller must be an ORDERING, not a filter")
