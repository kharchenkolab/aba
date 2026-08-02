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

    def test_whats_new_since_cursor(self):
        # armed: without a cursor the event is present (proven above);
        # a past cursor keeps it, a future cursor filters it out
        past = assemble_world(since="2000-01-01T00:00:00Z")
        self.assertIn("seeded-event", [e["title"] for e in past["whats_new"]])
        future = assemble_world(since="2999-01-01T00:00:00Z")
        self.assertEqual(future["whats_new"], [])

    def test_project_title_from_workspace_entity(self):
        w = assemble_world()
        # the schema bootstrap seeds a default workspace row; live projects
        # get their real name healed onto it at open (core/projects.py)
        self.assertEqual(w["project"]["title"], "Workspace")

    def test_leftovers_edge_complement(self):
        w = assemble_world()
        ids = {r["id"] for r in w["leftovers"]}
        self.assertIn(self.a1, ids)          # loose artifact
        self.assertNotIn(self.a2, ids)       # carried by inbound includes
        self.assertNotIn(self.a3, ids)       # pinned
        self.assertNotIn(self.a4, ids)       # archived
        self.assertNotIn(self.a5, ids)       # carries a claim (outbound supports)

    def test_maturity_key_reads_metadata_not_status(self):
        # packs that keep the ladder in metadata (e.g. confidence) register
        # maturity_key; the platform status column stays lifecycle
        cid = create_entity(entity_type="cc", title="metadata-graded claim",
                            metadata={"grade": "firm", "thread_id": self.q1,
                                      "caveats": ["n is small"],
                                      "evidence_ids": ["x1", "x2"]})
        try:
            register_record_roles(ROLES, maturity_order=LADDER,
                                  artifact_types=ARTS, maturity_key="grade")
            w = assemble_world()
            row = next(c for c in w["claims"] if c["id"] == cid)
            self.assertEqual(row["maturity"], "firm")
            self.assertEqual(row["rung"], 1)
            self.assertEqual(row["caveats"], ["n is small"])
            self.assertEqual(row["evidence"], 2)
            # claims WITHOUT the metadata key fall back to platform status
            c1 = next(c for c in w["claims"] if c["id"] == self.c1)
            self.assertEqual(c1["maturity"], "draft")
            self.assertEqual(c1["rung"], 0)
            # …but the lifecycle words are NOT maturities: a claim whose
            # status is "active" starts at the ladder's floor, never
            # reaching a reader as "(active)"
            act = create_entity(entity_type="cc", title="lifecycle-only",
                                metadata={"thread_id": self.q1})
            from core.graph.entities import update_entity
            update_entity(act, status="active")
            try:
                w2 = assemble_world()
                row2 = next(c for c in w2["claims"] if c["id"] == act)
                self.assertEqual(row2["maturity"], LADDER[0])
                self.assertEqual(row2["rung"], 0)
            finally:
                from core.graph.entities import delete_entity_hard
                delete_entity_hard(act)
        finally:
            from core.graph.entities import delete_entity_hard
            delete_entity_hard(cid)
            register_record_roles(ROLES, maturity_order=LADDER,
                                  artifact_types=ARTS)

    def test_question_parent_only_within_question_set(self):
        # the org axis is recursive: parent_entity_id onto ANOTHER question
        # ships as `parent`; a parent outside the question set is not a
        # tree edge and the row stays top-level (no parent key)
        sub = create_entity(entity_type="qq", title="sub-line under Q1",
                            parent_entity_id=self.q1)
        stray = create_entity(entity_type="qq", title="stray parent",
                              parent_entity_id=self.c1)   # a claim, not a question
        try:
            w = assemble_world()
            rows = {q["id"]: q for q in w["questions"]}
            self.assertEqual(rows[sub]["parent"], self.q1)
            self.assertNotIn("parent", rows[stray])
            self.assertNotIn("parent", rows[self.q1])
        finally:
            from core.graph.entities import delete_entity_hard
            delete_entity_hard(sub)
            delete_entity_hard(stray)

    def test_prose_body_key_ships_readable_body(self):
        # the story stratum renders prose BODIES; the pack names the
        # metadata key (narrative packs: "text"). Without the key, or when
        # the entity lacks it, the row stays title-only — honest projection.
        pid = create_entity(entity_type="pp", title="stub with body",
                            metadata={"question_id": self.q1,
                                      "text": "Variance tracks the batch "
                                              "assignment; calibration drift "
                                              "is ruled out at siteA."})
        try:
            register_record_roles(ROLES, maturity_order=LADDER,
                                  artifact_types=ARTS, prose_body_key="text")
            w = assemble_world()
            row = next(p for p in w["prose"] if p["id"] == pid)
            self.assertIn("batch assignment", row["body"])
            bare = next(p for p in w["prose"] if p["id"] == self.p1)
            self.assertNotIn("body", bare)          # absent key -> no body
            # unregistered key: NO prose row carries a body
            register_record_roles(ROLES, maturity_order=LADDER,
                                  artifact_types=ARTS)
            w2 = assemble_world()
            self.assertTrue(all("body" not in p for p in w2["prose"]))
        finally:
            from core.graph.entities import delete_entity_hard
            delete_entity_hard(pid)
            register_record_roles(ROLES, maturity_order=LADDER,
                                  artifact_types=ARTS)

    def test_runs_carry_their_ask(self):
        # a run row says what the run WAS: the last user TEXT message on
        # its thread at/before its start; tool-result user rows are noise
        from core.graph.messages import append_message
        m1 = append_message("user", [{"type": "text",
                                      "text": "map the variance drivers"}],
                            thread_id=self.q1)
        m2 = append_message("user", [{"type": "tool_result",
                                      "content": "irrelevant"}],
                            thread_id=self.q1)
        m3 = append_message("user", [{"type": "text",
                                      "text": "now check the calibration"}],
                            thread_id=self.q1)
        with _conn() as c:
            c.execute("UPDATE messages SET ts='2026-01-01T09:00:00Z' WHERE id=?", (m1,))
            c.execute("UPDATE messages SET ts='2026-01-01T09:30:00Z' WHERE id=?", (m2,))
            c.execute("UPDATE messages SET ts='2026-01-02T09:00:00Z' WHERE id=?", (m3,))
            c.commit()
        try:
            w = assemble_world()
            byid = {r["run_id"]: r for r in w["sediment"]["runs"]}
            self.assertEqual(byid["r-early"]["ask"],
                             "map the variance drivers")   # not the tool_result
            self.assertEqual(byid["r-late"]["ask"],
                             "now check the calibration")
            self.assertNotIn("ask", byid["r-mid"])         # background run
        finally:
            with _conn() as c:
                c.execute("DELETE FROM messages WHERE id IN (?,?,?)",
                          (m1, m2, m3))
                c.commit()

    def test_runs_carry_their_produced_images(self):
        # a run row shows what the run LEFT: image basenames from its exec
        # records' produced[] — the sediment expand affordance must open
        # onto something real (non-image outputs stay out). PRODUCTION
        # SHAPE: exec records attach to ANALYSIS ids (ana_…), never to the
        # turn run ids in the runs table — the join that must fire is
        # thread + time window, so the fixture uses a MISMATCHED run_id.
        import tempfile
        from core.graph import exec_records
        with tempfile.TemporaryDirectory() as td:
            exec_records.create(
                thread_id=self.q1, run_id="ana_elsewhere",
                tool_name="run_python", status="ok",
                started_at="2026-01-01T10:00:01Z", cwd=td,
                payload={"produced": [
                    {"url": "/artifacts/p/figs/scatter.png"},
                    {"url": "/artifacts/p/table.csv"},
                    {"url": "/artifacts/p/figs/scatter.png"},   # dedup
                    {"url": None},                              # env file
                ]})
            w = assemble_world()
            byid = {r["run_id"]: r for r in w["sediment"]["runs"]}
            self.assertEqual(byid["r-early"]["outputs"], ["scatter.png"])
            self.assertNotIn("outputs", byid["r-mid"])

    def test_multi_type_claim_role_and_one_hop_reference(self):
        # a role may be played by several types, statement keys are
        # candidates in order, and an entity with NO direct question
        # address reaches one through the evidence it stands on (one hop)
        base = create_entity(entity_type="rr", title="base result",
                             metadata={"thread_id": self.q1})
        fnd = create_entity(entity_type="ff", title="derived finding",
                            metadata={"text": "the derived full assertion"})
        add_edge(fnd, base, "supports")
        try:
            register_record_roles(
                {**ROLES, "claim": ("cc", "ff")},
                maturity_order=LADDER, artifact_types=ARTS,
                claim_statement_key=("statement", "text"))
            w = assemble_world()
            row = next(c for c in w["claims"] if c["id"] == fnd)
            self.assertEqual(row["questions"], [self.q1])   # via the hop
            self.assertEqual(row["statement"], "the derived full assertion")
            self.assertTrue(any(c["id"] == self.c1 for c in w["claims"]))
        finally:
            from core.graph.entities import delete_entity_hard
            delete_entity_hard(fnd)
            delete_entity_hard(base)
            register_record_roles(ROLES, maturity_order=LADDER,
                                  artifact_types=ARTS)

    def test_claim_statement_key_ships_full_assertion(self):
        # display titles truncate; the registered statement key ships the
        # FULL assertion (absent when equal to the title — no duplication)
        cid = create_entity(entity_type="cc", title="short display title",
                            metadata={"thread_id": self.q1,
                                      "statement": "the full assertion, "
                                      "much longer than any display title, "
                                      "stated as a complete sentence"})
        try:
            register_record_roles(ROLES, maturity_order=LADDER,
                                  artifact_types=ARTS,
                                  claim_statement_key="statement")
            w = assemble_world()
            row = next(c for c in w["claims"] if c["id"] == cid)
            self.assertIn("complete sentence", row["statement"])
            c1 = next(c for c in w["claims"] if c["id"] == self.c1)
            self.assertNotIn("statement", c1)
        finally:
            from core.graph.entities import delete_entity_hard
            delete_entity_hard(cid)
            register_record_roles(ROLES, maturity_order=LADDER,
                                  artifact_types=ARTS)

    def test_distillation_freezes_a_sitting(self):
        # a note carrying sitting_of becomes a FROZEN sitting: it wears its
        # title, owns its runs (clustering never redraws them), and leaves
        # the loose-notes stream; unknown run ids drop silently
        did = create_entity(entity_type="nn", title="traced the early runs",
                            metadata={"sitting_of": self.q1,
                                      "run_ids": ["r-early", "r-ghost"]})
        try:
            w = assemble_world()
            frozen = [s for s in w["sittings"] if s.get("frozen")]
            self.assertEqual(len(frozen), 1)
            self.assertEqual(frozen[0]["label"], "traced the early runs")
            self.assertEqual(frozen[0]["run_ids"], ["r-early"])
            self.assertEqual(frozen[0]["thread_id"], self.q1)
            # the owned run appears in NO derived sitting
            for s in w["sittings"]:
                if not s.get("frozen"):
                    self.assertNotIn("r-early", s["run_ids"])
            self.assertNotIn(did, [n["id"] for n in w["notes"]])
        finally:
            from core.graph.entities import delete_entity_hard
            delete_entity_hard(did)

    def test_prose_revision_supersedes_never_deletes(self):
        # a revision (wasDerivedFrom onto older prose) removes the old row
        # from the question's reading list but keeps it in the prose rows;
        # the head carries the chain length and its citations
        old = create_entity(entity_type="pp", title="v1",
                            metadata={"question_id": self.q1, "text": "one"})
        new = create_entity(entity_type="pp", title="v2",
                            metadata={"question_id": self.q1, "text": "two",
                                      "cites": [self.c1]})
        add_edge(new, old, "wasDerivedFrom")
        try:
            register_record_roles(ROLES, maturity_order=LADDER,
                                  artifact_types=ARTS, prose_body_key="text")
            w = assemble_world()
            q1 = next(q for q in w["questions"] if q["id"] == self.q1)
            self.assertIn(new, q1["prose"])
            self.assertNotIn(old, q1["prose"])
            rows = {p["id"]: p for p in w["prose"]}
            self.assertIn(old, rows)                  # provenance kept
            self.assertEqual(rows[new]["versions"], 2)
            self.assertEqual(rows[new]["revises"], old)
            self.assertEqual(rows[new]["cites"], [self.c1])
            self.assertNotIn("versions", rows[old])
        finally:
            from core.graph.entities import delete_entity_hard
            delete_entity_hard(new)
            delete_entity_hard(old)
            register_record_roles(ROLES, maturity_order=LADDER,
                                  artifact_types=ARTS)

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
