"""Every handle a run HANDS BACK must open at every door that takes one.

The doors were each tested alone and all passed; nothing ever fed one door's
OUTPUT into another door's INPUT. Three separate frictions lived in that gap:

  * `run_r` returned `{"url": "/artifacts/<pid>/<hash>.txt"}` and `view_file` on
    that exact URL answered "file not found" (thr_a1f7f687) — the agent re-ran
    the whole step to print to stdout instead.
  * `view_artifact` accepted `/artifacts/…` but had no site-aware branch, so a
    remote figure was unviewable and cost an extra remote job to copy it home.
  * `get_viewer_url(path=…)` rejected absolute remote paths with advice the
    agent had to guess at.

So this file is a MATRIX, not a case: handle shapes × door resolvers. Adding a
door or a new handle shape is one line, and a door that only understands its own
favourite spelling fails here instead of in a live session.

The handles are taken from a harvest-SHAPED payload (the dict `run_python` /
`run_r` actually return), not hand-built paths — a test that constructs its own
input cannot catch a door that disagrees with what runs emit.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

_TMP = tempfile.mkdtemp(prefix="aba_handles_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = str(Path(_TMP) / "h.db")
os.environ["ABA_PROJECTS_DIR"] = _TMP + "/projects"
os.environ["ARTIFACTS_DIR"] = str(Path(_TMP) / "artifacts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.graph._schema import init_db  # noqa: E402
from core import projects  # noqa: E402

init_db()
projects.init()
_PID = projects.create_project("Handles")["id"]
projects.set_current(_PID)

PNG = b"\x89PNG\r\n\x1a\n" + b"body" * 8


_PAYLOAD: dict = {}


def _harvest_payload():
    """A tool result shaped exactly like what run_python/run_r return — WITH the
    exec record the real harvest writes alongside it.

    The record matters: a harvested artifact is SERVED under a generated id
    (`a1b2c3.png`) while the human name the agent actually sees survives only in
    the exec record's `produced[]`. Building the payload without it would fake
    the easy half and leave the name tier untested — the tier that exists
    because three `view_artifact` calls once failed on names the agent had just
    been shown."""
    global _PAYLOAD
    if _PAYLOAD:
        return _PAYLOAD
    from core.config import project_artifacts_dir
    from core.graph import exec_records
    d = Path(project_artifacts_dir(_PID))
    d.mkdir(parents=True, exist_ok=True)
    (d / "a1b2c3.png").write_bytes(PNG)
    url = f"/artifacts/{_PID}/a1b2c3.png"
    exec_records.create(
        thread_id="thr_handles", run_id=None, tool_use_id="tu1",
        tool_name="run_python", status="ok", code="plot()",
        code_hash="h", started_at="2026-07-27T00:00:00+00:00",
        completed_at="2026-07-27T00:00:01+00:00", cwd=str(d),
        payload={"executor": "kernel:python", "kind": "script",
                 "produced": [{"kind": "figure", "idx": 0, "url": url,
                               "name": "umap_by_sample.png",
                               "size": len(PNG)}]})
    _PAYLOAD = {"plots": [{"url": url, "original_name": "umap_by_sample.png",
                           "bytes": len(PNG)}],
                "tables": [], "files": []}
    return _PAYLOAD


# ── the doors: name → (resolve(handle) -> Path|None) ────────────────────────

def _door_view_file(handle: str):
    from content.bio.tools.file_io import _resolve_project_path
    got, err = _resolve_project_path(handle, None, must_exist=True,
                                     enforce_sandbox=False)
    return None if err else Path(got)


def _door_view_artifact(handle: str):
    from content.bio.mcp_servers.aba_core.tools.entity_ops import _resolve_view_path
    return _resolve_view_path(handle)


DOORS = {"view_file": _door_view_file, "view_artifact": _door_view_artifact}


def _handles():
    """Every spelling a run's own result gives the agent for one artifact."""
    p = _harvest_payload()["plots"][0]
    return {
        "served url": p["url"],                     # what the result literally contains
        "original name": p["original_name"],        # what the agent SEES and re-types
    }


# Parametrize over static NAMES, never over `_handles()` itself: decorator
# arguments are evaluated at COLLECTION time, before conftest rebinds this
# module's database — so building the fixture there wrote the exec record into
# the pre-rebind DB and the name tier then found nothing. The handle is resolved
# inside the test body, where the fixtures have actually run.
HANDLE_SHAPES = ("served url", "original name")


@pytest.mark.parametrize("door", sorted(DOORS))
@pytest.mark.parametrize("shape", HANDLE_SHAPES)
def test_every_handle_opens_at_every_door(door, shape):
    """THE matrix. A door that understands only its favourite spelling fails
    here — which is exactly how view_file came to reject the URL run_r had
    just handed back."""
    handle = _handles()[shape]
    got = DOORS[door](handle)
    assert got is not None, (
        f"{door} could not open the {shape} handle {handle!r} — a run emits it, "
        f"so every door that takes a handle must accept it")
    assert got.read_bytes() == PNG, f"{door} resolved {shape} to the wrong bytes"


def test_the_matrix_is_armed():
    """A matrix over an empty handle set, or doors that accept anything, proves
    nothing. Pin both: real handles exist, and a nonsense handle is REFUSED by
    every door."""
    assert len(_handles()) >= 2 and len(DOORS) >= 2
    assert set(_handles()) == set(HANDLE_SHAPES), \
        "a handle shape was added without adding it to the matrix"
    for name, door in DOORS.items():
        assert door("/artifacts/%s/definitely-not-here.png" % _PID) is None, name
        assert door("no_such_file_anywhere.png") is None, name


def test_remote_absolute_handle_reaches_the_remote_tier(monkeypatch):
    """The fourth handle shape: an absolute path on ANOTHER machine. It cannot
    resolve on this filesystem by construction, so the contract is that the door
    ROUTES it to the remote tier rather than flatly refusing (the refusal is what
    cost five failed view_artifact calls in one turn)."""
    from content.bio.mcp_servers.aba_core.tools import entity_ops as EO
    seen = {}

    def _fake(path, thread_id=None, site=None):
        seen["path"] = path
        return None, ""
    monkeypatch.setattr(EO, "_fetch_remote_view_file", _fake)
    # the local resolver must miss first (it is not on this box)…
    assert EO._resolve_view_path("/home/u/hand/picked/umap.png") is None
    # …and the remote tier is what the door consults next
    EO._fetch_remote_view_file("/home/u/hand/picked/umap.png")
    assert seen["path"] == "/home/u/hand/picked/umap.png"
