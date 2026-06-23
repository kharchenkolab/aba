"""Pin idempotency — re-clicking pin must not create duplicate Results.

The bug (2026-06-23, prj_febd5e48): hitting Pin on a figure in the Run
view kept minting fresh Result entities even though the figure entity
already existed and was already wrapped. Audit found the same shape at
five distinct sites — pin_artifact, pin_cell_from_exec, pin_entity_to_result,
run_pin_output, and pin_evidence(target_result_id=None) callers in
general. The fix pushes dedupe into pin_evidence itself (via an
`includes`-edge scan) so every auto-wrap caller becomes idempotent for
free. create_cell_from_exec also gets an entity-level idempotency check
to mirror materialize_entity_from_artifact.

Coverage here:
  1. pin_evidence: two auto-wrap calls for the same evidence → same Result,
                   created_result=False on the second.
  2. pin_evidence: cross-thread does NOT collide (a pin in thread A
                   doesn't suppress a pin in thread B for the same evidence).
  3. pin_artifact: two POSTs equivalent calls → one figure entity AND
                   one Result entity.
  4. create_cell_from_exec: two calls → same cell entity id.
  5. pin_cell_from_exec: two calls → same cell AND same Result.
  6. Result.metadata.primary_evidence_id is stamped on new Results.
"""
from __future__ import annotations
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_tmp = tempfile.mkdtemp(prefix="aba_pin_idem_")
os.environ["ABA_DB_PATH"]     = os.path.join(_tmp, "x.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_WORK_DIR"]    = os.path.join(_tmp, "work")
os.environ["ABA_ENVS_DIR"]    = os.path.join(_tmp, "envs")
os.environ["DATA_DIR"]        = os.path.join(_tmp, "data")
os.environ["ARTIFACTS_DIR"]   = os.path.join(_tmp, "artifacts")

from core.graph._schema import init_db                              # noqa: E402
init_db()

import content.bio                                                  # noqa: E402,F401


def _make_figure_entity(title: str, thread_id: str = "thr_pin") -> str:
    from core.graph.entities import create_entity
    art = os.path.join(_tmp, f"{title.replace(' ', '_')}.png")
    open(art, "w").write("x")
    return create_entity(
        entity_type="figure", title=title, artifact_path=art,
        metadata={"thread_id": thread_id},
    )


def _make_exec_with_figure(thread_id: str = "thr_pin") -> tuple[str, str]:
    """Returns (exec_id, artifact_path). The exec record has one figure
    artifact at idx=0 so pin_artifact(...) can consume it."""
    from core.graph import exec_records
    art = os.path.join(_tmp, f"exec_fig_{os.urandom(3).hex()}.png")
    open(art, "w").write("z")
    cwd = os.path.join(_tmp, "work", "exec_pin"); os.makedirs(cwd, exist_ok=True)
    exec_id = exec_records.create(
        thread_id=thread_id,
        tool_name="run_r",
        status="ok",
        code="ggplot(...)",
        started_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        cwd=cwd,
        payload={"language": "r",
                  "produced": [{"kind": "figure", "url": art,
                                "original_name": "plot.png"}]},
    )
    return exec_id, art


def _count_active_results(thread_id: str | None = None) -> int:
    """Active results in the (optional) thread."""
    from core.graph._schema import _conn
    if thread_id:
        with _conn() as c:
            rows = c.execute(
                "SELECT id, metadata FROM entities "
                "WHERE type='result' AND status='active'",
            ).fetchall()
        import json
        return sum(
            1 for r in rows
            if (json.loads(r["metadata"] or "{}").get("thread_id") == thread_id)
        )
    with _conn() as c:
        n = c.execute(
            "SELECT COUNT(*) AS n FROM entities WHERE type='result' AND status='active'",
        ).fetchone()
    return n["n"]


# ── 1. pin_evidence idempotency ─────────────────────────────────────────

def test_pin_evidence_dedupes_on_same_evidence():
    """Two auto-wrap pin_evidence calls for the same figure → one Result.
    Second call returns the same result_id with created_result=False."""
    from content.bio.lifecycle.promote import pin_evidence
    fig = _make_figure_entity("dedupe_basic")
    before = _count_active_results("thr_pin")
    a = pin_evidence(thread_id="thr_pin", evidence_kind="figure", evidence_id=fig)
    b = pin_evidence(thread_id="thr_pin", evidence_kind="figure", evidence_id=fig)
    assert a["result_id"] == b["result_id"], (a, b)
    assert a.get("created_result") is True
    assert b.get("created_result") is False
    assert _count_active_results("thr_pin") == before + 1


def test_pin_evidence_does_not_collide_across_threads():
    """A pin in thread A must NOT suppress a pin of the same evidence in
    thread B (the thread is the pin's scope of intent)."""
    from content.bio.lifecycle.promote import pin_evidence
    fig = _make_figure_entity("cross_thread", thread_id="thr_A")
    a = pin_evidence(thread_id="thr_A", evidence_kind="figure", evidence_id=fig)
    b = pin_evidence(thread_id="thr_B", evidence_kind="figure", evidence_id=fig)
    assert a["result_id"] != b["result_id"], (a, b)
    assert a.get("created_result") is True
    assert b.get("created_result") is True


def test_pin_evidence_stamps_primary_evidence_id_on_new_result():
    """The frontend derives pinned-state from Result.metadata —
    primary_evidence_id must be set so the icon can flip without an
    extra edges API call."""
    from content.bio.lifecycle.promote import pin_evidence
    from core.graph.entities import get_entity
    fig = _make_figure_entity("stamps_id")
    out = pin_evidence(thread_id="thr_pin", evidence_kind="figure", evidence_id=fig)
    r = get_entity(out["result_id"])
    md = r.get("metadata") or {}
    assert md.get("primary_evidence_id") == fig, md


# ── 2. pin_artifact end-to-end ──────────────────────────────────────────

def test_pin_artifact_two_calls_one_result_and_one_figure():
    """The Run-view 'Pin figure' click. Double-click must not produce a
    duplicate figure OR a duplicate Result."""
    from content.bio.lifecycle.artifacts import pin_artifact
    from core.graph._schema import _conn
    exec_id, _ = _make_exec_with_figure()
    a = pin_artifact(exec_id, "figure", 0)
    b = pin_artifact(exec_id, "figure", 0)
    assert a["entity_id"] == b["entity_id"], (a, b)
    assert a["result_id"] == b["result_id"], (a, b)
    with _conn() as c:
        n_fig = c.execute(
            "SELECT COUNT(*) AS n FROM entities WHERE exec_id = ? "
            "AND artifact_kind='figure' AND status='active'",
            (exec_id,),
        ).fetchone()["n"]
        # Results don't carry exec_id; instead count Results pointing at
        # this figure via primary_evidence_id.
        rows = c.execute(
            "SELECT metadata FROM entities WHERE type='result' AND status='active'",
        ).fetchall()
    assert n_fig == 1
    import json
    n_res = sum(
        1 for r in rows
        if json.loads(r["metadata"] or "{}").get("primary_evidence_id") == a["entity_id"]
    )
    assert n_res == 1, f"expected 1 Result wrapping fig {a['entity_id']}, got {n_res}"


# ── 3. cell idempotency ─────────────────────────────────────────────────

def test_create_cell_from_exec_dedupes():
    """Repeat create_cell_from_exec for the same exec_id returns the same
    entity id. Used to dupe (every call did create_entity unconditionally)."""
    from content.bio.lifecycle.cells import create_cell_from_exec
    exec_id, _ = _make_exec_with_figure(thread_id="thr_cell")
    a = create_cell_from_exec(exec_id)
    b = create_cell_from_exec(exec_id)
    assert a == b, (a, b)


def test_pin_cell_from_exec_dedupes_cell_and_result():
    """The high-level 'pin this output' gesture must be idempotent
    end-to-end."""
    from content.bio.lifecycle.cells import pin_cell_from_exec
    exec_id, _ = _make_exec_with_figure(thread_id="thr_cell_pin")
    a = pin_cell_from_exec(exec_id)
    b = pin_cell_from_exec(exec_id)
    assert a["cell_id"] == b["cell_id"], (a, b)
    assert a["result_id"] == b["result_id"], (a, b)


# ── 4. was_new vs was_new_result semantics ──────────────────────────────

def test_pin_artifact_reports_was_new_result_separately():
    """Entity-newness and Result-newness are distinct signals after the
    dedupe fix. revisions.py:74 (auto_interpret gate) reads
    was_new_result, not was_new — so this contract matters."""
    from content.bio.lifecycle.artifacts import pin_artifact
    exec_id, _ = _make_exec_with_figure(thread_id="thr_was_new")
    a = pin_artifact(exec_id, "figure", 0)
    # First pin: both new.
    assert a["was_new"] is True
    assert a["was_new_result"] is True
    # Repeat pin: neither new.
    b = pin_artifact(exec_id, "figure", 0)
    assert b["was_new"] is False
    assert b["was_new_result"] is False


def test_backfill_primary_evidence_id_idempotent():
    """Pre-PIN-B Results don't carry primary_evidence_id in metadata —
    the frontend pin-state derivation fails on them. The startup
    backfill recovers the field via the `includes` edge and must be
    idempotent (no-op on already-stamped Results)."""
    from content.bio.lifecycle.promote import (
        pin_evidence, backfill_primary_evidence_id,
    )
    from core.graph.entities import get_entity, update_entity
    fig = _make_figure_entity("backfill_target")
    out = pin_evidence(thread_id="thr_bf", evidence_kind="figure", evidence_id=fig)
    rid = out["result_id"]
    # Simulate pre-PIN-B state: strip the stamped field from metadata.
    r = get_entity(rid)
    md = dict(r["metadata"] or {})
    md.pop("primary_evidence_id", None)
    update_entity(rid, metadata=md)
    assert "primary_evidence_id" not in (get_entity(rid).get("metadata") or {})
    # First backfill run finds + fixes it.
    n1 = backfill_primary_evidence_id()
    assert n1 >= 1
    r2 = get_entity(rid)
    assert (r2.get("metadata") or {}).get("primary_evidence_id") == fig
    # Second run is a no-op for THIS row (idempotent — count may include
    # other test rows but the field can't be re-stamped on this one).
    md3 = dict((get_entity(rid).get("metadata") or {}))
    n2 = backfill_primary_evidence_id()
    md4 = dict((get_entity(rid).get("metadata") or {}))
    assert md3 == md4
    assert n2 == 0  # this row was the only one needing it


def test_pin_then_unpin_then_repin_round_trip():
    """Re-pin after unpin must create a brand-new Result (not resurrect
    the archived one). Drives the UI toggle: red → click → unpinned →
    click → red again, with a fresh Result entity each cycle."""
    from content.bio.lifecycle.promote import pin_evidence, unpin_evidence
    fig = _make_figure_entity("round_trip")
    a = pin_evidence(thread_id="thr_rt", evidence_kind="figure", evidence_id=fig)
    assert a.get("created_result") is True
    unpin_evidence(fig, thread_id="thr_rt")
    # After unpin, the prior Result is no longer active; pin_evidence
    # must create a fresh one (NOT silently reuse the archived shell).
    c = pin_evidence(thread_id="thr_rt", evidence_kind="figure", evidence_id=fig)
    assert c.get("created_result") is True, c
    assert c["result_id"] != a["result_id"]


def test_pin_artifact_orphan_entity_then_pin_wraps_new_result():
    """Edge case: materialize the figure entity FIRST without wrapping
    (wrap_in_result=False), THEN pin it. was_new=False (entity reused)
    but was_new_result=True (Result freshly created). Pre-PIN-A this
    would have under-fired auto_interpret."""
    from content.bio.lifecycle.artifacts import pin_artifact
    exec_id, _ = _make_exec_with_figure(thread_id="thr_orphan")
    a = pin_artifact(exec_id, "figure", 0, wrap_in_result=False)
    assert a["was_new"] is True
    assert a["was_new_result"] is False
    assert a["result_id"] is None
    b = pin_artifact(exec_id, "figure", 0, wrap_in_result=True)
    assert b["was_new"] is False          # entity already existed
    assert b["was_new_result"] is True    # but Result is brand new
    assert b["result_id"] is not None
