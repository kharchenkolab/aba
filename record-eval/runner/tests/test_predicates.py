"""Unit tests for the predicate library against hand-built mini-trajectories.

Each test constructs a minimal fake trace (entries with events + snapshots)
for one predicate — no replay needed; predicates only read the recorded
trajectory plus the synthetic fixture pool (build-latency, no biology).
"""

import unittest

from runner import predicates as P
from runner.tests.fixtures import tiny_pool


class FakeTrace:
    def __init__(self, entries):
        self.entries = entries


def entry(t, etype="finding", index=None, snapshot=None, ops=(), notes=(),
          **ev):
    e = {"event": {"t": t, "type": etype,
                   "index": index if index is not None else t - 1, **ev},
         "notes": list(notes), "ops": list(ops), "state_digest": "x"}
    if snapshot is not None:
        e["snapshot"] = snapshot
    return e


def snap(**stores):
    base = {"sections": {}, "prose": {}, "proposals": {}, "plan_items": {},
            "routing_rows": {}, "findings": {}, "sittings": {}, "holds": [],
            "salience": {}, "absences": [], "briefings": [], "addenda": {},
            "routing_tables": []}
    base.update(stores)
    return base


def traj(*entries):
    return P.Trajectory(FakeTrace(list(entries)))


POOL = tiny_pool()


class TestWindows(unittest.TestCase):
    def test_snap_end_and_in_window(self):
        t = traj(entry(1, snapshot=snap(day=0)),
                 entry(5, snapshot=snap(day=5)),
                 entry(9, snapshot=snap(day=9)))
        self.assertEqual(t.snap_end((1, 5))["day"], 5)
        self.assertEqual(t.snap_end(None)["day"], 9)
        self.assertEqual(len(t.in_window((2, 9))), 2)

    def test_end_entry_without_t_ignored(self):
        t = traj(entry(1, snapshot=snap(day=0)),
                 {"event": {"type": "END"}, "notes": [], "ops": [],
                  "snapshot": snap(day=99)})
        self.assertEqual(t.snap_end((1, 1))["day"], 0)
        self.assertEqual(t.snap_end(None)["day"], 99)


class TestGapRecord(unittest.TestCase):
    def test_gap_and_briefing(self):
        s = snap(absences=[{"days": 21}], briefings=[{"day": 21}],
                 findings={"G03": {"id": "G03", "background": True,
                                   "citations": [{"stratum": "sediment"}]}})
        v = P.gap_record(traj(entry(1, snapshot=s)), POOL, None, window=None,
                         min_gap_days=21, briefing=True, background_landings=1)
        self.assertTrue(v.passed, v.detail)

    def test_missing_gap_fails(self):
        v = P.gap_record(traj(entry(1, snapshot=snap())), POOL, None,
                         window=None, min_gap_days=10)
        self.assertFalse(v.passed)


class TestProposalState(unittest.TestCase):
    def test_pending_unexpired(self):
        s = snap(proposals={"PR1": {"id": "PR1", "cls": "2", "kind": "structure",
                                    "description": "merge the cache sections",
                                    "status": "pending", "active_age": 3,
                                    "applied": False}})
        v = P.proposal_state(traj(entry(1, snapshot=s)), POOL, None,
                             window=None, desc_tokens=("merge", "cache"),
                             cls="2", status="pending", max_active_age=13)
        self.assertTrue(v.passed, v.detail)

    def test_expired_fails_pending_check(self):
        s = snap(proposals={"PR1": {"id": "PR1", "cls": "2", "kind": "structure",
                                    "description": "merge the cache sections",
                                    "status": "expired", "active_age": 15}})
        v = P.proposal_state(traj(entry(1, snapshot=s)), POOL, None,
                             window=None, desc_tokens=("merge",),
                             status="pending")
        self.assertFalse(v.passed)

    def test_accepted_at_t(self):
        s = snap(proposals={"PR1": {"id": "PR1", "cls": "3", "kind": "structure",
                                    "description": "demote the image section",
                                    "status": "accepted", "active_age": 0,
                                    "applied": True}})
        e = entry(4, etype="ratify",
                  notes=[{"note": "proposal_accepted", "proposal": "PR1"}],
                  snapshot=s)
        v = P.proposal_state(traj(e), POOL, None, window=(4, 5),
                             desc_tokens=("demote",), status="accepted",
                             accepted_at_t=4, applied=True)
        self.assertTrue(v.passed, v.detail)


class TestOpsBounded(unittest.TestCase):
    def test_structural_op_in_frozen_window_fails(self):
        e = entry(3, ops=[{"status": "applied",
                           "op": {"op": "demote_section", "cls": "3"},
                           "effective_cls": "3", "consent": "PR1"}],
                  snapshot=snap())
        v = P.ops_bounded(traj(e), POOL, None, window=(2, 4),
                          structure_frozen=True)
        self.assertFalse(v.passed)

    def test_class_ceiling(self):
        e = entry(3, ops=[{"status": "applied",
                           "op": {"op": "promote_section", "cls": "3"},
                           "effective_cls": "3", "consent": "PR1"}],
                  snapshot=snap())
        v = P.ops_bounded(traj(e), POOL, None, window=(2, 4), max_cls="2")
        self.assertFalse(v.passed)
        v = P.ops_bounded(traj(e), POOL, None, window=(5, 9), max_cls="2")
        self.assertTrue(v.passed)


class TestPlanItemState(unittest.TestCase):
    def _snap(self, state="planned", provisional=("check",),
              discharged_by=None):
        return snap(
            plan_items={"PI1": {"id": "PI1", "owner": "Q2", "kind": "check",
                                "target": "G02", "text": "check G02",
                                "state": state, "discharged_by": discharged_by,
                                "created_event": 2}},
            findings={"G02": {"id": "G02", "provisional_open": list(provisional),
                              "citations": [{"stratum": "sediment"}]}})

    def test_planned_with_provisional(self):
        v = P.plan_item_state(traj(entry(3, snapshot=self._snap())), POOL,
                              None, window=(3, 3), kinds=("check",),
                              target="G02", min_state="planned",
                              provisional_target=True)
        self.assertTrue(v.passed, v.detail)

    def test_absorbed_clears_provisional(self):
        s = self._snap(state="absorbed", provisional=(), discharged_by="R9")
        v = P.plan_item_state(traj(entry(9, snapshot=s)), POOL, None,
                              window=None, kinds=("check",), target="G02",
                              min_state="absorbed", provisional_cleared=True)
        self.assertTrue(v.passed, v.detail)

    def test_dangling_fails(self):
        v = P.plan_item_state(traj(entry(9, snapshot=self._snap())), POOL,
                              None, window=None, kinds=("check",),
                              target="G02", not_dangling=True)
        self.assertFalse(v.passed)


class TestSittingIndex(unittest.TestCase):
    def test_coalesce(self):
        s = snap(sittings={"T1": {"id": "T1", "episode_id": "E1",
                                  "findings": ["G01"], "touched": ["Q1"]},
                           "T2": {"id": "T2", "episode_id": "E1",
                                  "findings": [], "touched": []}})
        e1 = entry(3, etype="session_start",
                   notes=[{"note": "sitting_opened", "sitting": "T1"}],
                   snapshot=s)
        e2 = entry(5, etype="session_start",
                   notes=[{"note": "sitting_opened", "sitting": "T2"}],
                   snapshot=s)
        v = P.sitting_index(traj(e1, e2), POOL, None, window=(3, 5),
                            mode="coalesce")
        self.assertTrue(v.passed, v.detail)

    def test_silent_filed(self):
        s = snap(sittings={"T1": {"id": "T1", "episode_id": "E1",
                                  "findings": [], "silent_filed": True,
                                  "touched": []}})
        e = entry(4, etype="session_start",
                  notes=[{"note": "sitting_opened", "sitting": "T1"}],
                  snapshot=s)
        v = P.sitting_index(traj(e), POOL, None, window=(4, 5),
                            mode="silent_filed")
        self.assertTrue(v.passed, v.detail)

    def test_touched_superset(self):
        s = snap(sittings={"T1": {"id": "T1", "episode_id": "E1",
                                  "findings": ["G02"], "touched": ["Q1"]}})
        v = P.sitting_index(traj(entry(4, snapshot=s)), POOL, None,
                            window=None, mode="touched")
        self.assertFalse(v.passed)  # G02 bears Q1+Q2, touched only Q1


class TestSalienceState(unittest.TestCase):
    def test_pinned_faded_hold(self):
        s = snap(findings={"G01": {"id": "G01", "pinned": True, "faded": False,
                                   "citations": [{"stratum": "story",
                                                  "question": "Q1"}]},
                           "G03": {"id": "G03", "pinned": False, "faded": True,
                                   "citations": [{"stratum": "sediment"}]}},
                 holds=[])
        v = P.salience_state(traj(entry(9, snapshot=s)), POOL, None,
                             window=None,
                             specs=[("G01", "pinned"),
                                    ("G03", "faded_findable"),
                                    ("G02", "hold_evaporated")])
        # G02 never landed -> fail
        self.assertFalse(v.passed)
        s["findings"]["G02"] = {"id": "G02", "citations": []}
        v = P.salience_state(traj(entry(9, snapshot=s)), POOL, None,
                             window=None,
                             specs=[("G01", "pinned"),
                                    ("G03", "faded_findable"),
                                    ("G02", "hold_evaporated")])
        self.assertTrue(v.passed, v.detail)


class TestCitedUnderQuestions(unittest.TestCase):
    def _snap(self, q2_cited=("G02",)):
        return snap(
            sections={"S1": {"id": "S1", "question_id": "Q1",
                             "cited": ["G01", "G02"], "kind": "question-root",
                             "status": "live", "parent_id": None},
                      "S2": {"id": "S2", "question_id": "Q2",
                             "cited": list(q2_cited), "kind": "question-root",
                             "status": "live", "parent_id": None}},
            findings={"G02": {"id": "G02", "citations": []}})

    def test_multi_cited(self):
        v = P.cited_under_questions(traj(entry(5, snapshot=self._snap())),
                                    POOL, None, window=None,
                                    findings=("G02",), questions=("Q1", "Q2"))
        self.assertTrue(v.passed, v.detail)

    def test_missing_question_fails(self):
        v = P.cited_under_questions(traj(entry(5, snapshot=self._snap(()))),
                                    POOL, None, window=None,
                                    findings=("G02",), questions=("Q1", "Q2"))
        self.assertFalse(v.passed)

    def test_forbidden(self):
        v = P.cited_under_questions(traj(entry(5, snapshot=self._snap())),
                                    POOL, None, window=None,
                                    findings=("G01",), questions=("Q1",),
                                    forbidden=("Q2",))
        self.assertTrue(v.passed, v.detail)


class TestRoutingDestination(unittest.TestCase):
    def test_strata_and_forbidden_question(self):
        s = snap(findings={"G03": {"id": "G03", "faded": False,
                                   "citations": [{"stratum": "notes",
                                                  "question": None,
                                                  "revised": False}]}})
        v = P.routing_destination(traj(entry(5, snapshot=s)), POOL, None,
                                  window=None,
                                  spec={"G03": dict(allowed=("notes",
                                                             "sediment"))})
        self.assertTrue(v.passed, v.detail)
        s["findings"]["G03"]["citations"].append(
            {"stratum": "story", "question": "Q2", "revised": False})
        v = P.routing_destination(traj(entry(5, snapshot=s)), POOL, None,
                                  window=None,
                                  spec={"G03": dict(allowed=("notes",
                                                             "sediment"))})
        self.assertFalse(v.passed)


class TestOverturnHandling(unittest.TestCase):
    def _interrupt_snap(self):
        return snap(
            findings={"G01": {"id": "G01", "superseded_by": None,
                              "citations": [{"stratum": "story",
                                             "question": "Q1"}]}},
            prose={"P1": {"id": "P1", "section_id": "S1", "ratified": True,
                          "authored": False, "contested": True,
                          "provenance": ["G01"], "addenda": [],
                          "superseded_by": None}},
            proposals={"PR1": {"id": "PR1", "cls": "X", "kind": "addendum",
                               "description": "addendum: G02 supersedes G01",
                               "status": "pending", "applied": False}})

    def test_interrupt(self):
        v = P.overturn_handling(traj(entry(5, snapshot=self._interrupt_snap())),
                                POOL, None, window=(5, 5),
                                pairs=[("G01", "G02")], mode="interrupt")
        self.assertTrue(v.passed, v.detail)

    def test_absorb(self):
        s = snap(findings={"G01": {"id": "G01", "superseded_by": "G02",
                                   "citations": [{"stratum": "story",
                                                  "revised": True}]}})
        v = P.overturn_handling(traj(entry(5, snapshot=s)), POOL, None,
                                window=(5, 5), pairs=[("G01", "G02")],
                                mode="absorb")
        self.assertTrue(v.passed, v.detail)

    def test_differential_conflated_fails(self):
        # absorb half ALSO carries an addendum naming it -> severity conflated
        s = self._interrupt_snap()
        s["findings"]["G01"]["superseded_by"] = "G02"
        v = P.overturn_handling(traj(entry(5, snapshot=s)), POOL, None,
                                window=(5, 5),
                                pairs=[("G01", "G02"), ("G01", "G02")],
                                mode="differential")
        self.assertFalse(v.passed)

    def test_cascade_requires_in_window_creation(self):
        s = self._interrupt_snap()
        s["plan_items"] = {"PI1": {"id": "PI1", "kind": "check",
                                   "target": "G03", "state": "planned",
                                   "created_event": 1}}
        t = traj(entry(2, index=1, snapshot=s), entry(6, index=5, snapshot=s))
        v = P.overturn_handling(t, POOL, None, window=(5, 6),
                                pairs=[("G01", "G02")], mode="interrupt",
                                cascade_reexam_target="G03")
        self.assertFalse(v.passed)  # item created at t=2, before window


class TestFaceValueRetired(unittest.TestCase):
    def test_unrevised_prose_fails(self):
        s = snap(findings={"G01": {"id": "G01",
                                   "citations": [{"stratum": "story"}]}},
                 prose={"P1": {"id": "P1", "provenance": ["G01"],
                               "contested": False, "addenda": [],
                               "revision_of": None, "superseded_by": None}})
        v = P.face_value_retired(traj(entry(5, snapshot=s)), POOL, None,
                                 window=None, findings=("G01",))
        self.assertFalse(v.passed)
        s["prose"]["P1"]["contested"] = True
        v = P.face_value_retired(traj(entry(5, snapshot=s)), POOL, None,
                                 window=None, findings=("G01",))
        self.assertTrue(v.passed, v.detail)


class TestSectionPromotion(unittest.TestCase):
    def test_promotion_in_window(self):
        e1 = entry(3, snapshot=snap())
        e2 = entry(7, ops=[{"status": "applied",
                            "op": {"op": "promote_section", "section": "Q2",
                                   "cls": "3"},
                            "effective_cls": "3", "consent": "PR1"}],
                   snapshot=snap())
        v = P.section_promotion(traj(e1, e2), POOL, None, window=(5, 9),
                                question="Q2")
        self.assertTrue(v.passed, v.detail)

    def test_promotion_before_window_fails(self):
        e = entry(3, ops=[{"status": "applied",
                           "op": {"op": "promote_section", "section": "Q2",
                                  "cls": "3"},
                           "effective_cls": "3", "consent": "PR1"}],
                  snapshot=snap())
        v = P.section_promotion(traj(e), POOL, None, window=(5, 9),
                                question="Q2")
        self.assertFalse(v.passed)


class TestSectionState(unittest.TestCase):
    def test_status_rank_cites(self):
        s = snap(sections={"S1": {"id": "S1", "question_id": "Q1",
                                  "status": "closed", "rank": "main",
                                  "cited": ["G01", "G02"], "kind": "section",
                                  "parent_id": None, "intent": False},
                           "S2": {"id": "S2", "question_id": "Q2",
                                  "status": "live", "rank": "appendix",
                                  "cited": ["G02"], "kind": "section",
                                  "parent_id": None, "intent": False}},
                 findings={"G01": {"id": "G01", "citations": [{}]},
                           "G02": {"id": "G02", "citations": [{}]}})
        v = P.section_state(traj(entry(5, snapshot=s)), POOL, None,
                            window=None, question="Q1", status="closed",
                            cites_all=("G01", "G02"), nothing_deleted=True)
        self.assertTrue(v.passed, v.detail)
        v = P.section_state(traj(entry(5, snapshot=s)), POOL, None,
                            window=None, question="Q2", rank="appendix")
        self.assertTrue(v.passed, v.detail)

    def test_outline_bounds(self):
        s = snap(sections={"S1": {"id": "S1", "question_id": "Q1",
                                  "status": "live", "kind": "section",
                                  "cited": ["G01"], "parent_id": None,
                                  "intent": False}})
        v = P.section_state(traj(entry(5, snapshot=s)), POOL, None,
                            window=None, outline_bounds=True)
        self.assertFalse(v.passed)  # rendered section with 1 finding


class TestStubIsPlan(unittest.TestCase):
    def test_stub_with_items_no_prose(self):
        s = snap(sections={"S9": {"id": "S9", "question_id": "Q2",
                                  "kind": "stub", "intent": True,
                                  "status": "live", "cited": [],
                                  "parent_id": None, "title": "cache story"}},
                 plan_items={"PI1": {"id": "PI1", "owner": "Q2",
                                     "kind": "plan", "state": "planned",
                                     "target": None, "created_event": 1}})
        v = P.stub_is_plan(traj(entry(5, snapshot=s)), POOL, None,
                           window=None, question="Q2")
        self.assertTrue(v.passed, v.detail)
        s["prose"]["P1"] = {"id": "P1", "section_id": "S9", "provenance": []}
        v = P.stub_is_plan(traj(entry(5, snapshot=s)), POOL, None,
                           window=None, question="Q2")
        self.assertFalse(v.passed)


class TestTrayState(unittest.TestCase):
    def test_empty_tray_fails_non_empty(self):
        v = P.tray_state(traj(entry(5, snapshot=snap())), POOL, None,
                         window=None, non_empty=True)
        self.assertFalse(v.passed)

    def test_typed_tray(self):
        s = snap(routing_rows={"R1": {"id": "R1", "status": "pending",
                                      "typed": "routine", "product": "G01",
                                      "questions": []}},
                 proposals={"PR1": {"id": "PR1", "status": "pending",
                                    "cls": "3", "kind": "claim_draft",
                                    "description": "d"}})
        v = P.tray_state(traj(entry(5, snapshot=s)), POOL, None, window=None,
                         non_empty=True, min_routine=1,
                         max_undifferentiated=9)
        self.assertTrue(v.passed, v.detail)


class TestNarrativeGrowth(unittest.TestCase):
    def test_bounds(self):
        ops = [{"status": "applied", "op": {"op": "write_prose"},
                "effective_cls": "1", "consent": None}] * 4
        e = entry(5, ops=ops, snapshot=snap())
        v = P.narrative_growth_bounded(traj(e), POOL, None, window=(5, 5),
                                       max_changes=3)
        self.assertFalse(v.passed)
        v = P.narrative_growth_bounded(traj(e), POOL, None, window=(5, 5),
                                       max_changes=4)
        self.assertTrue(v.passed, v.detail)


class TestConsentConservation(unittest.TestCase):
    def test_high_class_without_consent_fails(self):
        e = entry(5, ops=[{"status": "applied",
                           "op": {"op": "demote_section", "cls": "3"},
                           "effective_cls": "3", "consent": None}],
                  snapshot=snap())
        v = P.consent_conservation(traj(e), POOL, None)
        self.assertFalse(v.passed)

    def test_acceptance_outside_ratify_fails(self):
        e = entry(5, etype="finding",
                  notes=[{"note": "proposal_accepted", "proposal": "PR1"}],
                  snapshot=snap())
        v = P.consent_conservation(traj(e), POOL, None)
        self.assertFalse(v.passed)


class TestProvenanceChain(unittest.TestCase):
    def test_ratified_prose_carries_chain(self):
        s = snap(prose={"P1": {"id": "P1", "ratified": True,
                               "provenance": ["G01", "G02"], "addenda": [],
                               "section_id": "S1"}})
        v = P.provenance_chain(traj(entry(5, snapshot=s)), POOL, None,
                               window=None, findings=("G01", "G02"))
        self.assertTrue(v.passed, v.detail)
        v = P.provenance_chain(traj(entry(5, snapshot=s)), POOL, None,
                               window=None, findings=("G01", "G03"))
        self.assertFalse(v.passed)


if __name__ == "__main__":
    unittest.main()
