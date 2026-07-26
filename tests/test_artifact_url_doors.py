"""A served artifact URL is a first-class handle at every read door.

`/artifacts/<pid>/<hash>.ext` is what our OWN tool results hand back: a harvested
run output arrives as `{"url": "/artifacts/…", "original_name": …}`. So the very
next call is likely to pass that URL straight back in — and it has to work.

It did not. `_resolve_project_path` (read_file / view_file / write_file) treated
the URL as a filesystem path, which resolves to a literal `/artifacts/…` that
cannot exist. Live (thr_a1f7f687): `run_r` wrote a listing to a file, harvested
it, returned its URL, and `view_file` on that exact URL answered "file not
found" — the agent had to re-run the whole step and print to stdout instead.
`view_artifact` had always mapped these; this door had not, so the platform
contradicted itself between two consecutive results.

ARMED on the ROUND TRIP: the tests take the URL from a harvest-shaped payload
rather than hand-building a path, so a door that only accepts disk paths fails
them. WIDE: covers the read doors, the write door's refusal (a served artifact is
not editable), a URL for a file that genuinely does not exist, and a plain
absolute path (must keep working unchanged).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

_TMP = tempfile.mkdtemp(prefix="aba_arturl_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = str(Path(_TMP) / "a.db")
os.environ["ABA_PROJECTS_DIR"] = _TMP + "/projects"
os.environ["ARTIFACTS_DIR"] = str(Path(_TMP) / "artifacts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.graph._schema import init_db  # noqa: E402
from core import projects  # noqa: E402

init_db()
projects.init()
_PID = projects.create_project("ArtifactURLs")["id"]
projects.set_current(_PID)


def _harvested(name: str, body: str) -> str:
    """Write a file where the artifact server serves from, and return the URL a
    tool result would hand back for it (the shape run_r/run_python emit)."""
    from core.config import project_artifacts_dir
    d = Path(project_artifacts_dir(_PID))
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body)
    return f"/artifacts/{_PID}/{name}"


def test_view_file_opens_the_url_a_run_just_returned():
    """THE regression, end to end."""
    from content.bio.tools.view_file import view_file_tool
    url = _harvested("671ac6dd.txt", "sample_a\nsample_b\n")
    out = view_file_tool({"path": url}, None)
    assert "error" not in out, out
    assert out["kind"] == "text"
    assert "sample_a" in out["text"]


def test_read_file_opens_the_same_url():
    """The other read door on the same resolver."""
    from content.bio.tools.file_io import _resolve_project_path
    url = _harvested("abc123.txt", "hello\n")
    got, err = _resolve_project_path(url, None, must_exist=True,
                                     enforce_sandbox=False)
    assert err is None, err
    assert Path(got).read_text() == "hello\n"
    assert not got.startswith("/artifacts/"), "must map to disk, not stay a URL"


def test_missing_artifact_url_still_reports_not_found():
    """CEILING: mapping the URL must not invent a file. A URL for something that
    was swept must still fail — with the search bounds, as before."""
    from content.bio.tools.view_file import view_file_tool
    out = view_file_tool({"path": f"/artifacts/{_PID}/does-not-exist.txt"}, None)
    assert "error" in out
    assert "not found" in out["error"].lower()


def test_write_door_still_refuses_a_served_artifact():
    """A served artifact is an immutable output, not an editable file — the
    sandbox check must still reject it for write/edit."""
    from content.bio.tools.file_io import _resolve_project_path
    url = _harvested("readonly.txt", "x\n")
    _got, err = _resolve_project_path(url, None, must_exist=False,
                                      enforce_sandbox=True)
    assert err and "sandbox" in err.lower()


def test_plain_absolute_paths_are_unchanged():
    """CEILING: the URL branch must not disturb ordinary path handling."""
    from content.bio.tools.file_io import _resolve_project_path
    real = Path(_TMP) / "plain.txt"
    real.write_text("plain\n")
    got, err = _resolve_project_path(str(real), None, must_exist=True,
                                     enforce_sandbox=False)
    assert err is None and Path(got) == real.resolve()


def test_a_path_merely_containing_artifacts_is_not_a_url():
    """WIDE — the degenerate shape: only a LEADING /artifacts/ is the served-URL
    form. A real directory called artifacts elsewhere must stay a plain path."""
    from content.bio.tools.file_io import _resolve_project_path
    d = Path(_TMP) / "proj" / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "f.txt").write_text("nested\n")
    got, err = _resolve_project_path(str(d / "f.txt"), None, must_exist=True,
                                     enforce_sandbox=False)
    assert err is None and Path(got).read_text() == "nested\n"
