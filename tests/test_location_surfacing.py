"""Location-surfacing census — every surface that NAMES a dataset says
WHERE it lives.

The class (live, 2026-07-26): a dataset's home site is recorded at
registration (`metadata.home.site`), but the naming surfaces rendered
title/path only. Silence reads as "local": an agent probed the local disk
three times for a remote-homed dataset before inferring the site from an
unrelated ambient line, and a viewer link minted for a remote source died
at click time with a raw error.

THE CENSUS (a naming surface ships location or it doesn't ship — new
surfaces must enroll here):
  1. content/bio/data_location.py         — the ONE owner (facts + renderers)
  2. list_data_files                      — `site` on every registered row,
                                            recorded size + note when remote
  3. [PROJECT STATE] sidebar              — ' · on <site>' per remote line
  4. kernel orientation block             — ' · on <site>' per remote line
  5. entity /preview                      — typed {kind:"remote", site, bytes}
                                            (covered at the route seam here)
  6. get_viewer_url                       — remote source annotated with cost
                                            / over-gate refusal naming levers
  (Already-guarded elsewhere: find_files durability+site —
  test_path_resolution.py; dataset card lives-on/mirror — frontend tests;
  Files-tab tree — test_output_door_census.py.)

Contract on EVERY surface, both sides: a remote home is NAMED; a local
dataset gains ZERO location noise.

Run: pytest tests/test_location_surfacing.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="aba_locsurf_")
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_DB_PATH", str(Path(_tmp) / "l.db"))
os.environ.setdefault("ARTIFACTS_DIR", str(Path(_tmp) / "artifacts"))
os.environ.setdefault("ABA_WORK_DIR", str(Path(_tmp) / "work"))
os.environ.setdefault("DATA_DIR", str(Path(_tmp) / "data"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.graph._schema import init_db  # noqa: E402
init_db()
import content.bio  # noqa: E402,F401
from content.bio.data_location import (dataset_location, location_suffix,  # noqa: E402
                                       remote_use_note)

pytestmark = pytest.mark.bio

_REMOTE = {"id": "dat_r", "type": "dataset", "title": "series-a",
           "artifact_path": "/remote/home/series-a",
           "metadata": {"by_reference": True,
                        "home": {"site": "siteA", "path": "/remote/home/series-a"},
                        "descriptor": {"total_bytes": 84_000_000, "n_files": 1},
                        "layout_hint": "1 file"}}
_LOCAL = {"id": "dat_l", "type": "dataset", "title": "table-b",
          "artifact_path": str(Path(_tmp) / "data" / "table-b.csv"),
          "metadata": {}}


# ── 1. the owner ─────────────────────────────────────────────────────────────

def test_owner_facts_both_sides():
    r = dataset_location(_REMOTE)
    assert r["site"] == "siteA" and r["remote"] is True
    assert r["total_bytes"] == 84_000_000
    l = dataset_location(_LOCAL)
    assert l["site"] == "local" and l["remote"] is False
    # renderers: remote named, local silent — the whole contract
    assert location_suffix(_REMOTE) == " · on siteA"
    assert location_suffix(_LOCAL) == ""
    assert "siteA" in (remote_use_note(_REMOTE) or "")
    assert remote_use_note(_LOCAL) is None
    # mirrored shape (WIDE): the suffix says both facts
    m = {**_REMOTE, "metadata": {**_REMOTE["metadata"],
                                 "local_mirror": {"path": "/x"}}}
    assert "mirrored locally" in location_suffix(m)


# ── 2. list_data_files ───────────────────────────────────────────────────────

def _patch_entities(monkeypatch, ents):
    import core.graph.entities as gmod
    monkeypatch.setattr(gmod, "list_entities",
                        lambda **kw: [e for e in ents
                                      if kw.get("type_filter") in (None, e["type"])])


def test_list_data_files_carries_site_size_and_note(monkeypatch):
    _patch_entities(monkeypatch, [_REMOTE, _LOCAL])
    from content.bio.tools.file_io import list_data_files
    out = list_data_files({})
    by = {f["filename"]: f for f in out["files"] if f.get("registered")}
    r = by["series-a"]
    assert r["site"] == "siteA"
    assert r["size_bytes"] == 84_000_000, \
        "remote size must come from the recorded descriptor, not a local stat"
    assert "siteA" in r["note"]
    l = by["table-b.csv"]
    assert l["site"] == "local" and "note" not in l   # zero noise locally
    assert "non-local `site`" in out["message"]


def test_list_data_files_message_quiet_when_all_local(monkeypatch):
    _patch_entities(monkeypatch, [_LOCAL])
    from content.bio.tools.file_io import list_data_files
    out = list_data_files({})
    assert "non-local" not in out["message"]


# ── 3. the [PROJECT STATE] sidebar ───────────────────────────────────────────

def test_sidebar_names_the_remote_home(monkeypatch):
    import content.bio.cards.sidebar as sb
    monkeypatch.setattr(sb, "list_entities",
                        lambda **kw: ([_REMOTE, _LOCAL]
                                      if kw.get("type_filter") == "dataset" else []))
    monkeypatch.setattr(sb, "count_entities", lambda **kw: 0)
    text = sb.render_bio_project_sidebar()
    (r_line,) = [ln for ln in text.splitlines() if "series-a" in ln]
    assert "· on siteA" in r_line
    (l_line,) = [ln for ln in text.splitlines() if "table-b" in ln]
    assert "on " not in l_line.replace("→", "")       # local: zero noise


# ── 4. the kernel orientation block ──────────────────────────────────────────

def test_orientation_block_names_the_remote_home(monkeypatch):
    import content.bio.tools.run_exec as rx
    _patch_entities(monkeypatch, [_REMOTE, _LOCAL])
    text = rx._prior_run_files_preamble("p", "t", None, fresh_kernel=True)
    assert "series-a" in text, "armed: the block must actually render"
    (r_line,) = [ln for ln in text.splitlines()
                 if "series-a" in ln and "→" in ln]
    assert "· on siteA" in r_line
    l_lines = [ln for ln in text.splitlines() if "table-b" in ln and "→" in ln]
    assert all("· on " not in ln for ln in l_lines)


# ── 5. the preview route seam ────────────────────────────────────────────────

def test_preview_names_the_site_instead_of_silent_none():
    from content.bio.data_location import remote_preview_answer
    assert remote_preview_answer(_REMOTE) == {
        "kind": "remote", "site": "siteA", "total_bytes": 84_000_000}
    # a MIRRORED remote dataset previews normally (falls through) — the
    # typed answer is only for "no local bytes anywhere"
    mirrored = {**_REMOTE, "metadata": {**_REMOTE["metadata"],
                                        "local_mirror": {"path": "/x"}}}
    assert remote_preview_answer(mirrored) is None
    assert remote_preview_answer(_LOCAL) is None
    # the route seam is pinned structurally: main.entities_preview must
    # consult the owner (import-level tripwire, no heavy app import)
    import re
    src = (Path(_BACKEND) / "main.py").read_text()
    m = re.search(r"def entities_preview.*?(?=\n@|\nclass |\ndef )", src, re.S)
    assert m and "remote_preview_answer" in m.group(0), \
        "/preview no longer consults data_location.remote_preview_answer"


# ── 6. get_viewer_url pre-flight ─────────────────────────────────────────────

def _viewer_result(monkeypatch, entity):
    import core.graph.entities as gmod
    import content.bio.tools.viewers as vt
    monkeypatch.setattr(gmod, "get_entity", lambda eid: entity)

    class _V:
        mode, open_external, id, label = "external", "launcher-1", "viewer-1", "V1"
    monkeypatch.setattr("core.viewers.registry.viewers_for", lambda node: [_V()])
    return vt.open_viewer_impl({"entity_id": entity["id"]})


def test_viewer_link_annotates_a_remote_source(monkeypatch):
    out = _viewer_result(monkeypatch, _REMOTE)
    assert out["ok"] is True
    assert "siteA" in out["note"] and "MB" in out["note"], \
        "a link to a remote source must say what opening will cost"


def test_viewer_link_over_gate_names_the_refusal(monkeypatch):
    big = {**_REMOTE, "metadata": {**_REMOTE["metadata"],
                                   "descriptor": {"total_bytes": 40 * 1024**3,
                                                  "n_files": 1}}}
    out = _viewer_result(monkeypatch, big)
    assert "OVER the transfer gate" in out["note"] and "siteA" in out["note"]


def test_viewer_link_local_source_carries_no_note(monkeypatch):
    out = _viewer_result(monkeypatch, _LOCAL)
    assert out["ok"] is True and "note" not in out


# ── 7. plan placement lint ───────────────────────────────────────────────────

def test_plan_lint_flags_a_site_blind_plan(monkeypatch):
    _patch_entities(monkeypatch, [_REMOTE, _LOCAL])
    from content.bio.data_location import plan_placement_note
    note = plan_placement_note('{"steps": [{"description": "load and summarize"}]}')
    assert note is not None
    assert "siteA" in note and "series-a" in note
    assert "site='siteA'" in note                    # the lever is named


def test_plan_lint_quiet_when_deliberate_or_local(monkeypatch):
    from content.bio.data_location import plan_placement_note
    # the plan mentions the site → placement is deliberate → quiet
    _patch_entities(monkeypatch, [_REMOTE])
    assert plan_placement_note('{"steps":[{"description":"run it on siteA"}]}') is None
    # all-local project → quiet
    _patch_entities(monkeypatch, [_LOCAL])
    assert plan_placement_note('{"steps":[{"description":"anything"}]}') is None
    # mirrored remote → either copy works → quiet
    m = {**_REMOTE, "metadata": {**_REMOTE["metadata"],
                                 "local_mirror": {"path": "/x"}}}
    _patch_entities(monkeypatch, [m])
    assert plan_placement_note('{"steps":[{"description":"anything"}]}') is None


def test_plan_intercept_consults_the_lint():
    """Seam pin: the present_plan intercept must run the lint (structural —
    the intercept needs a full agent turn to exercise behaviorally)."""
    import re
    src = (Path(_BACKEND) / "guide.py").read_text()
    m = re.search(r'if name == "present_plan":.*?_runtime_halt_after', src, re.S)
    assert m and "plan_placement_note" in m.group(0), \
        "present_plan no longer consults data_location.plan_placement_note"


# ── 9. run placement stamps at DISPATCH, not completion ─────────────────────

def test_kernel_dispatch_stamps_placement_before_execute():
    """Seam pin: the remote-kernel lane must note_run_site() BEFORE the
    blocking execute() — the stamp used to land only at result registration,
    so a long remote step's Run card claimed 'ran locally' the whole time
    and flipped only at the end (live UX finding). Structural: the lane
    needs a live session to exercise behaviorally."""
    import re
    src = (Path(_BACKEND) / "content/bio/tools/run_exec.py").read_text()
    m = re.search(r"record_weft_target.*?sess\.execute\(", src, re.S)
    assert m, "kernel dispatch block not found"
    assert "note_run_site" in m.group(0), \
        "dispatch no longer stamps placement before execute()"


# ── 8. viewer launch page: the remote failure offers the FIX ────────────────

def test_launch_page_offers_mirror_and_retry(tmp_path):
    from core.viewers.launch_page import render
    html = render(tmp_path)                          # no dist → CSS links empty
    assert 'id="vl-mirror"' in html                  # the affordance exists
    assert "/mirror" in html and "mirrorAndRetry" in html
    # gated on the remote-failure signature + an entity-backed source — a
    # plain local failure must NOT grow a mirror button
    assert "lives on|bring it home|not on this machine" in html
    assert "remoteish && params.entity_id" in html   # both gates, together


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(
        [sys.executable, "-m", "pytest", __file__, "-v"]))
