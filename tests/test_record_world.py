"""Record World assembler (core/record/world.py) — phase-1 projection guards.

Seeds a generic project graph (synthetic type names — types are opaque to the
store, and the guard stays domain-neutral) and asserts the World projection:
the register seam, question rows carrying their metadata, claim maturity
rungs from the registered ladder, multi-question references via edges AND
metadata, archived exclusion, sediment run ordering/limit, and the
empty-skeleton behavior when no roles are registered.

Armed: every seeded row an assertion relies on is checked present before the
projection is read. Wide: degenerate shapes — a question with no metadata, a
claim with a status outside the ladder (rung None, not a crash), an entity
with no edges, a run with NULL thread_id.

Run: python tests/test_record_world.py   (or pytest)
"""
from __future__ import annotations
import os
import sys
import tempfile
import unittest
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_record_world_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "d.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.graph._schema import _conn, init_db          # noqa: E402
from core.graph.audit import log_event                 # noqa: E402
from core.graph.entities import create_entity, get_entity, update_entity  # noqa: E402
from core.graph.edges import add_edge                  # noqa: E402
from core.graph.proposals_store import add_proposal, update_proposal  # noqa: E402
from core.graph.runs_port import list_runs             # noqa: E402
from core.record.world import (                        # noqa: E402
    assemble_world, derive_sittings, register_record_roles, record_roles,
)

LADDER = ("draft", "firm", "solid", "contested", "dead")
ROLES = {"question": "qq", "claim": "cc", "prose": "pp", "note": "nn"}
ARTS = ("aa",)


def _seed_run(run_id: str, thread_id, started_at: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO runs (run_id, thread_id, state, started_at, updated_at)"
            " VALUES (?, ?, 'done', ?, ?)",
            (run_id, thread_id, started_at, started_at))
        c.commit()


class RecordWorldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        register_record_roles(ROLES, maturity_order=LADDER,
                              artifact_types=ARTS)
        cls.q1 = create_entity(
            entity_type="qq", title="Q1 variance across siteA runs",
            metadata={"question": "what drives variance in siteA?",
                      "open_questions": [{"text": "batch effect?"}],
                      "lifecycle": "open"})
        cls.q2 = create_entity(entity_type="qq", title="Q2 bare")  # no metadata
        cls.c1 = create_entity(entity_type="cc", title="claim one")
        update_entity(cls.c1, status="draft")
        add_edge(cls.c1, cls.q1, "relates_to")
        # multi-question: metadata names q1, an edge names q2; status outside ladder
        cls.c2 = create_entity(entity_type="cc", title="claim two",
                               metadata={"thread_id": cls.q1})
        update_entity(cls.c2, status="offbeat")
        add_edge(cls.c2, cls.q2, "relates_to")
        cls.c3 = create_entity(entity_type="cc", title="claim archived")
        update_entity(cls.c3, status="archived")
        cls.p1 = create_entity(entity_type="pp", title="prose for q2",
                               metadata={"question_id": cls.q2})
        cls.n1 = create_entity(entity_type="nn", title="loose note")
        _seed_run("r-early", cls.q1, "2026-01-01T10:00:00Z")
        _seed_run("r-mid", None, "2026-01-02T10:00:00Z")     # NULL thread
        _seed_run("r-late", cls.q1, "2026-01-03T10:00:00Z")
        # leftovers shelf: a1 loose; a2 carried (inbound includes); a3 pinned;
        # a4 archived; a5 carried by its own outbound supports
        cls.a1 = create_entity(entity_type="aa", title="figs/scatter.png")
        cls.a2 = create_entity(entity_type="aa", title="figs/carried.png")
        holder = create_entity(entity_type="rr", title="holder")
        add_edge(holder, cls.a2, "includes")
        cls.a3 = create_entity(entity_type="aa", title="figs/pinned.png")
        update_entity(cls.a3, pinned=True)
        cls.a4 = create_entity(entity_type="aa", title="figs/gone.png")
        update_entity(cls.a4, status="archived")
        cls.a5 = create_entity(entity_type="aa", title="figs/backing.png")
        add_edge(cls.a5, cls.c1, "supports")
        # tray: one pending, one dismissed
        cls.pr1 = add_proposal(thread_id=cls.q1, kind="route",
                               headline="file the scatter under Q1",
                               signature="sig-1")
        pr2 = add_proposal(thread_id=cls.q1, kind="route",
                           headline="stale suggestion", signature="sig-2")
        update_proposal(pr2, status="dismissed")
        log_event("marker", entity_id=cls.q1, title="seeded-event")

    # -- armed: the seeds this file reasons about must actually exist --
    def test_00_armed_seeds_present(self):
        for eid in (self.q1, self.q2, self.c1, self.c2, self.c3,
                    self.p1, self.n1):
            self.assertIsNotNone(get_entity(eid), eid)
        self.assertEqual(get_entity(self.c3)["status"], "archived")
        self.assertEqual(len(list_runs()), 3)

    def test_roles_echo(self):
        w = assemble_world()
        self.assertEqual(w["roles"]["question"], "qq")
        self.assertEqual(w["maturity_ladder"], list(LADDER))
        self.assertEqual(record_roles()["claim"], "cc")

    def test_questions_carry_metadata_and_membership(self):
        w = assemble_world()
        by_id = {q["id"]: q for q in w["questions"]}
        self.assertEqual(set(by_id), {self.q1, self.q2})
        q1 = by_id[self.q1]
        self.assertEqual(q1["question"], "what drives variance in siteA?")
        self.assertEqual(q1["lifecycle"], "open")
        self.assertEqual(set(q1["claims"]), {self.c1, self.c2})
        q2 = by_id[self.q2]
        self.assertIsNone(q2["question"])      # degenerate: no metadata
        self.assertEqual(q2["claims"], [self.c2])
        self.assertEqual(q2["prose"], [self.p1])

    def test_claim_rungs_and_multi_question(self):
        w = assemble_world()
        by_id = {c["id"]: c for c in w["claims"]}
        self.assertNotIn(self.c3, by_id)               # archived excluded
        self.assertEqual(by_id[self.c1]["rung"], 0)
        self.assertIsNone(by_id[self.c2]["rung"])      # outside the ladder
        self.assertEqual(set(by_id[self.c2]["questions"]), {self.q1, self.q2})

    def test_sediment_runs_oldest_first_with_limit(self):
        w = assemble_world()
        ids = [r["run_id"] for r in w["sediment"]["runs"]]
        self.assertEqual(ids, ["r-early", "r-mid", "r-late"])
        self.assertIsNone(w["sediment"]["runs"][1]["thread_id"])
        newest_two = [r["run_id"] for r in list_runs(limit=2)]
        self.assertEqual(newest_two, ["r-mid", "r-late"])  # still oldest-first
        self.assertEqual([r["run_id"] for r in list_runs(thread_id=self.q1)],
                         ["r-early", "r-late"])

    def test_stable_organ_keys(self):
        w = assemble_world()
        for key in ("sittings", "whats_new", "tray", "leftovers"):
            self.assertIn(key, w)

    # -- sittings: pure clustering over run rows --
    def test_sittings_cluster_by_gap_and_thread(self):
        runs = [
            {"run_id": "r1", "thread_id": "T1",
             "started_at": "2026-01-01T10:00:00Z", "updated_at": "2026-01-01T10:05:00Z"},
            {"run_id": "r2", "thread_id": "T1",   # 10 min later: same sitting
             "started_at": "2026-01-01T10:15:00Z", "updated_at": "2026-01-01T10:20:00Z"},
            {"run_id": "r3", "thread_id": "T1",   # 3 h later: new sitting
             "started_at": "2026-01-01T13:30:00Z", "updated_at": "2026-01-01T13:31:00Z"},
            {"run_id": "r4", "thread_id": "T2",   # other thread, own sitting
             "started_at": "2026-01-01T10:16:00Z", "updated_at": "2026-01-01T10:17:00Z"},
            {"run_id": "r5", "thread_id": None,   # background: no sitting
             "started_at": "2026-01-01T11:00:00Z", "updated_at": "2026-01-01T11:00:00Z"},
            {"run_id": "r6", "thread_id": "T1",   # no timestamps: coalesces
             "started_at": None, "updated_at": None},
        ]
        sits = derive_sittings(runs, gap_minutes=45)
        self.assertEqual(len(sits), 3)
        t1 = [s for s in sits if s["thread_id"] == "T1"]
        self.assertEqual([s["run_ids"] for s in t1], [["r1", "r2"], ["r3", "r6"]])
        self.assertEqual([s["run_ids"] for s in sits
                          if s["thread_id"] == "T2"], [["r4"]])
        self.assertNotIn("r5", [rid for s in sits for rid in s["run_ids"]])
        self.assertEqual(t1[0]["started_at"], "2026-01-01T10:00:00Z")
        self.assertEqual(t1[0]["ended_at"], "2026-01-01T10:20:00Z")

    def test_sittings_gap_exactly_at_threshold_coalesces(self):
        runs = [
            {"run_id": "r1", "thread_id": "T1",
             "started_at": "2026-01-01T10:00:00Z", "updated_at": "2026-01-01T10:00:00Z"},
            {"run_id": "r2", "thread_id": "T1",   # exactly 45 min after end
             "started_at": "2026-01-01T10:45:00Z", "updated_at": "2026-01-01T10:45:00Z"},
        ]
        self.assertEqual(len(derive_sittings(runs, gap_minutes=45)), 1)

    def test_world_sittings_from_seeded_runs(self):
        w = assemble_world()
        self.assertEqual([s["run_ids"] for s in w["sittings"]],
                         [["r-early"], ["r-late"]])   # days apart, r-mid unthreaded

    # -- tray, what's-new, leftovers --
    def test_tray_carries_only_pending(self):
        self.assertIsNotNone(self.pr1)   # armed: dedup didn't swallow the seed
        w = assemble_world()
        heads = [p["headline"] for p in w["tray"]]
        self.assertIn("file the scatter under Q1", heads)
        self.assertNotIn("stale suggestion", heads)

    def test_whats_new_carries_events(self):
        w = assemble_world()
        titles = [e["title"] for e in w["whats_new"]]
        self.assertIn("seeded-event", titles)

    def test_leftovers_edge_complement(self):
        w = assemble_world()
        ids = {r["id"] for r in w["leftovers"]}
        self.assertIn(self.a1, ids)          # loose artifact
        self.assertNotIn(self.a2, ids)       # carried by inbound includes
        self.assertNotIn(self.a3, ids)       # pinned
        self.assertNotIn(self.a4, ids)       # archived
        self.assertNotIn(self.a5, ids)       # carries a claim (outbound supports)

    def test_unregistered_roles_yield_empty_skeleton(self):
        register_record_roles({})
        try:
            w = assemble_world()
            self.assertEqual(w["questions"], [])
            self.assertEqual(w["claims"], [])
            self.assertEqual(w["roles"], {})
        finally:
            register_record_roles(ROLES, maturity_order=LADDER,
                                  artifact_types=ARTS)

    def test_router_contract(self):
        from core.web.routers.record import record_world
        w = record_world(pid="p-test")
        self.assertEqual(w["project_id"], "p-test")
        self.assertEqual(w["version"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
