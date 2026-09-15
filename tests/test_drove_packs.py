"""What a drive actually stood on — observed from two independent records.

THE FAILURE SHAPE THIS AVOIDS. Recording "the pins, re-read at the end of the
gate" as "what was verified" is the instrument handing the subject its own
answer: it agrees with itself whatever happened, and a drive that adopted
something else entirely still writes a confident line. So the observation comes
from weft's realization ledger (what was made ready) intersected with the
store's catalog (what each published version's EnvID is) — two records written
by different code at different times.

The assertions below are mostly about the DEGENERATE shapes, because every one
of them used to be spelled the same way as success: an empty answer.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

_spec = importlib.util.spec_from_file_location(
    "_drove", Path(__file__).resolve().parents[1] / "scripts" / "drove_packs.py")
_drove = importlib.util.module_from_spec(_spec)
sys.modules["_drove"] = _drove
_spec.loader.exec_module(_drove)
drove, OK, FATAL = _drove.drove, _drove.OK, _drove.FATAL

EID_A = "env:v1:" + "a" * 64
EID_B = "env:v1:" + "b" * 64
EID_SESSION = "env:v1:" + "c" * 64


def _workspace(tmp_path: Path, env_ids, state="ready") -> str:
    db = tmp_path / "weft" / ".weft" / "state.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE realizations (env_id TEXT, site TEXT, state TEXT)")
    for e in env_ids:
        con.execute("INSERT INTO realizations VALUES (?,?,?)", (e, "local", state))
    con.commit(); con.close()
    return str(tmp_path)


def _store(tmp_path: Path, mapping) -> str:
    tree = tmp_path / "store"; tree.mkdir(parents=True, exist_ok=True)
    envs = {}
    for pack, version, eid in mapping:
        envs.setdefault(pack, {"versions": {}})["versions"][version] = {"env_id": eid}
    (tree / "catalog.json").write_text(json.dumps({"envs": envs}))
    return str(tree)


def test_reports_the_packs_the_drive_realized(tmp_path):
    ws = _workspace(tmp_path / "w", [EID_A, EID_B])
    tree = _store(tmp_path / "s", [("pack-one", "2026.01.01-aaaa", EID_A),
                                   ("pack-two", "2026.01.02-bbbb", EID_B)])
    rc, pairs, _ = drove(ws, tree)
    assert rc == OK
    assert pairs == ["pack-one=2026.01.01-aaaa", "pack-two=2026.01.02-bbbb"]


def test_the_version_reported_is_the_one_realized_not_the_newest(tmp_path):
    """ARMED against re-deriving. The store holds a NEWER version of the same
    pack; the drive stood on the older one. Anything that re-resolved instead of
    observing would report the newer."""
    ws = _workspace(tmp_path / "w", [EID_A])
    tree = _store(tmp_path / "s", [("pack-one", "2026.01.01-aaaa", EID_A),
                                   ("pack-one", "2026.09.09-zzzz", EID_B)])
    rc, pairs, _ = drove(ws, tree)
    assert rc == OK and pairs == ["pack-one=2026.01.01-aaaa"]


def test_session_scope_envs_are_counted_not_recorded(tmp_path):
    """WIDE — the ordinary shape. Lanes build their own envs; those are realized
    too and are not published packs. They must not become pins, and they must
    not be silently dropped either."""
    ws = _workspace(tmp_path / "w", [EID_A, EID_SESSION])
    tree = _store(tmp_path / "s", [("pack-one", "2026.01.01-aaaa", EID_A)])
    rc, pairs, notes = drove(ws, tree)
    assert rc == OK and pairs == ["pack-one=2026.01.01-aaaa"]
    assert any("not published packs" in n for n in notes)
    assert any("1 realized" in n for n in notes)


# ── the empty answers, each of which used to look like success ──────────────

def test_a_drive_that_realized_nothing_is_FATAL(tmp_path):
    ws = _workspace(tmp_path / "w", [])
    tree = _store(tmp_path / "s", [("pack-one", "2026.01.01-aaaa", EID_A)])
    rc, pairs, notes = drove(ws, tree)
    assert rc == FATAL and not pairs
    assert any("realized NO environment" in n for n in notes)


def test_realizations_that_are_not_ready_do_not_count(tmp_path):
    """A pack whose realization FAILED is not something the drive stood on."""
    ws = _workspace(tmp_path / "w", [EID_A], state="failed")
    tree = _store(tmp_path / "s", [("pack-one", "2026.01.01-aaaa", EID_A)])
    rc, _, notes = drove(ws, tree)
    assert rc == FATAL and any("realized NO environment" in n for n in notes)


def test_no_weft_workspace_is_FATAL_and_says_so(tmp_path):
    tree = _store(tmp_path / "s", [("pack-one", "2026.01.01-aaaa", EID_A)])
    rc, _, notes = drove(str(tmp_path / "nowhere"), tree)
    assert rc == FATAL and any("weft workspace" in n for n in notes)


def test_an_unreadable_store_is_FATAL_and_says_so(tmp_path):
    ws = _workspace(tmp_path / "w", [EID_A])
    rc, _, notes = drove(ws, str(tmp_path / "nostore"))
    assert rc == FATAL and any("env store" in n for n in notes)


def test_realized_but_nothing_matches_the_store_is_FATAL(tmp_path):
    """The store/workspace mismatch. A deployment always stands on its base
    pack, so 'realized things, matched none' is a broken pairing — not a true
    report that no packs were used."""
    ws = _workspace(tmp_path / "w", [EID_SESSION])
    tree = _store(tmp_path / "s", [("pack-one", "2026.01.01-aaaa", EID_A)])
    rc, pairs, notes = drove(ws, tree)
    assert rc == FATAL and not pairs
    assert any("NONE of them is a published pack" in n for n in notes)
