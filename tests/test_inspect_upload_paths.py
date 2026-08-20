"""What `inspect_upload` may look at, and what it says when it cannot.

Guards two claims, both of which the tool used to get wrong:

  1. **Any path this workspace can read is inspectable.** The old resolver
     accepted only DATA_DIR, REFS_DIR, or an exactly-matching registered
     artifact_path, and answered anything else with `path not found` — about a
     directory that existed and was readable. That is a false statement of
     fact made to the one caller least able to check it, and it left the agent
     with no truthful way to answer "what is in this folder?": list_data_files
     covers registered datasets and DATA_DIR, find_files searches the custody
     chain BY NAME (and the filename is the unknown), read_file rejects a
     directory. Live (bug report 2026-08-20) an agent in exactly that corner
     described a group share from its path name instead. It is also the wrong
     shape for the workspace: register_dataset already records an out-of-tree
     path in place with no copy, so refusing to LOOK at what we will happily
     REGISTER had it backwards.

  2. **Denied, absent and empty are three different facts.** pathlib ignores
     ENOENT/ENOTDIR/EBADF/ELOOP and nothing else, so a denied stat came back
     out as a raw PermissionError; and rglob swallows PermissionError
     mid-walk, so a closed subtree silently vanished and a closed ROOT
     rendered as "empty directory". Opening the tool up to /groups shares
     makes mixed permissions the normal case, so each one has to say itself.

Run: .venv/bin/python -m pytest tests/test_inspect_upload_paths.py -q
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_iup_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "iup.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
sys.path.insert(0, str(ROOT / "backend"))

from content.bio.tools import file_io  # noqa: E402


def _isolate(tmp_path, monkeypatch, registered=()):
    """An empty project whose DATA_DIR is nowhere near the paths under test."""
    dd = tmp_path / "project" / "data"
    dd.mkdir(parents=True)
    monkeypatch.setattr(file_io, "_registered_datasets", lambda: list(registered))
    import core.config as cfg
    import core.projects as prj
    monkeypatch.setattr(cfg, "project_data_dir", lambda pid: dd)
    monkeypatch.setattr(prj, "current_project_id", lambda: "test")
    return dd


def _shut(p: Path):
    """Close a path off, or skip — root and some ACL filesystems ignore modes."""
    p.chmod(0o000)
    if os.access(p, os.R_OK):
        p.chmod(0o755)
        pytest.skip("running with rights that ignore mode bits (root?)")


# ── 1. out-of-project paths are first-class ─────────────────────────────────

def test_a_readable_folder_outside_the_project_is_inspectable(tmp_path, monkeypatch):
    """The headline: a group share the user pointed at, not registered, not
    under DATA_DIR. This is the whole reason the tool exists for this caller."""
    _isolate(tmp_path, monkeypatch)
    share = tmp_path / "groups" / "somelab" / "scMultiome"
    share.mkdir(parents=True)
    (share / "counts.rds").write_bytes(b"x" * 4096)

    out = file_io.inspect_upload({"path": str(share)})

    assert "error" not in out, f"a readable folder must be inspectable: {out}"
    assert out["kind"] == "directory"
    assert [f["path"] for f in out["files"]] == ["counts.rds"]


def test_a_readable_file_outside_the_project_is_inspectable(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    f = tmp_path / "elsewhere" / "counts.rds"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 10)

    out = file_io.inspect_upload({"path": str(f)})

    assert "error" not in out, f"a readable file must be inspectable: {out}"
    assert out["kind"] == "file"


def test_an_existing_path_is_never_reported_as_not_found(tmp_path, monkeypatch):
    """The narrow claim, stated on its own: whatever the tool decides to do
    with an out-of-tree path, it must not deny that the path is there."""
    _isolate(tmp_path, monkeypatch)
    share = tmp_path / "groups" / "somelab" / "scMultiome"
    share.mkdir(parents=True)

    out = file_io.inspect_upload({"path": str(share)})

    assert "not found" not in str(out.get("error") or ""), \
        "the directory exists and is readable — saying otherwise is a false fact"


# ── 2. denied, absent and empty are three answers ───────────────────────────

def test_a_denied_path_is_reported_as_denied_not_as_absent(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    box = tmp_path / "closed"
    (box / "inner").mkdir(parents=True)
    _shut(box)
    try:
        out = file_io.inspect_upload({"path": str(box / "inner")})
    finally:
        box.chmod(0o755)

    err = str(out.get("error") or "")
    assert "denied" in err.lower(), f"a denied path must say so, got {out!r}"
    assert "not found" not in err, "denied is not absent"


def test_an_unlistable_folder_is_not_reported_as_empty(tmp_path, monkeypatch):
    """A closed directory yields nothing from rglob. Nothing renders as an
    empty folder, which is the opposite of the truth — there may be anything
    in there. Only reachable now that out-of-tree paths get this far."""
    _isolate(tmp_path, monkeypatch)
    d = tmp_path / "nolist"
    d.mkdir()
    (d / "hidden.csv").write_text("a\n")
    _shut(d)
    try:
        out = file_io.inspect_upload({"path": str(d)})
    finally:
        d.chmod(0o755)

    blob = (str(out.get("summary") or "") + str(out.get("error") or "")).lower()
    assert out.get("files") in (None, []), "nothing was actually listed"
    assert "empty" not in blob or "not" in blob, \
        f"an unreadable folder must not read as empty: {out!r}"
    assert "cannot be listed" in blob or "denied" in blob, \
        f"it must say WHY the listing is blank: {out!r}"


def test_a_partly_readable_tree_declares_what_it_could_not_read(tmp_path, monkeypatch):
    """rglob skips a closed subtree in silence, so a bounded listing reads as
    exhaustive. The bound has to be in the result."""
    _isolate(tmp_path, monkeypatch)
    mix = tmp_path / "mix"
    (mix / "open").mkdir(parents=True)
    (mix / "shut").mkdir()
    (mix / "open" / "a.csv").write_text("a\n")
    (mix / "shut" / "b.csv").write_text("b\n")
    _shut(mix / "shut")
    try:
        out = file_io.inspect_upload({"path": str(mix)})
    finally:
        (mix / "shut").chmod(0o755)

    assert [f["path"] for f in out["files"]] == ["open/a.csv"]
    assert out.get("unreadable") == ["shut"], \
        f"the subtree we could not enter must be named: {out!r}"
    assert "INCOMPLETE" in out["summary"], \
        "a bounded listing that doesn't declare its bound reads as exhaustive"


# ── 3. what must NOT change ─────────────────────────────────────────────────

def test_a_genuinely_absent_path_still_errors_with_the_options(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    out = file_io.inspect_upload({"path": str(tmp_path / "no" / "such.csv")})
    assert "not found" in str(out.get("error") or "")
    assert "registered_datasets" in out, "the agent still gets the real options"


def test_a_relative_path_still_resolves_against_data_dir(tmp_path, monkeypatch):
    dd = _isolate(tmp_path, monkeypatch)
    (dd / "local.csv").write_text("a,b\n1,2\n")
    out = file_io.inspect_upload({"path": "local.csv"})
    assert "error" not in out and out["kind"] == "file"


def test_basename_auto_resolve_still_rescues_a_constructed_path(tmp_path, monkeypatch):
    """The agent builds a DATA_DIR-shaped path for a dataset registered
    elsewhere; the tool still finds it and says that it corrected."""
    real = tmp_path / "work" / "counts.rds"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"x" * 8)
    dd = _isolate(tmp_path, monkeypatch,
                  registered=[{"name": "counts.rds", "path": str(real),
                               "title": "Counts"}])

    out = file_io.inspect_upload({"path": str(dd / "counts.rds")})

    assert "error" not in out, out
    assert out["path_corrected"]["to"] == str(real)
