"""Engine mechanics: ratify free-text matching, distill settlement,
discharge subsumption, determinism on the real corpus."""

from __future__ import annotations

import os
import sys
import unittest

_RECORD_EVAL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _RECORD_EVAL not in sys.path:
    sys.path.insert(0, _RECORD_EVAL)

from runner import ops as O
from runner.baselines import BASELINES
from runner.engine import ReplayEngine, match_ratify_target
from runner.events import load_pool, load_scenario
from runner.policy import Policy
from runner.state import Proposal

from runner.tests.fixtures import POOLS_ROOT, scenario, tiny_pool


def _proposal(pid, desc, event=0):
    return Proposal(id=pid, cls="3", kind="claim_draft", description=desc,
                    created_event=event)


class RatifyMatchTest(unittest.TestCase):
    def test_single_match_by_overlap(self):
        props = [
            _proposal("PR1", "claim draft: the Jun 16 onset claim into the "
                             "impact section", 0),
            _proposal("PR2", "claim draft on F10: rollback appears to fix", 1),
        ]
        matched, unmatched = match_ratify_target(
            "the Jun 16 onset claim draft, into the impact section", props)
        self.assertEqual(matched, ["PR1"])
        self.assertEqual(unmatched, [])

    def test_and_target_matches_two_proposals(self):
        props = [
            _proposal("PR1", "restructuring: close Q1 and appendix the "
                             "vetting machinery", 0),
            _proposal("PR2", "claim draft on F30: the black hole verdict", 1),
        ]
        matched, unmatched = match_ratify_target(
            "the Q1-closure restructuring proposal and the black-hole "
            "claim draft", props)
        self.assertEqual(sorted(matched), ["PR1", "PR2"])
        self.assertEqual(unmatched, [])

    def test_no_overlap_is_unmatched(self):
        props = [_proposal("PR1", "claim draft on F12: the joint orbit", 0)]
        matched, unmatched = match_ratify_target(
            "the common-envelope formation framing", props)
        self.assertEqual(matched, [])
        self.assertEqual(len(unmatched), 1)

    def test_only_pending_proposals_match(self):
        pr = _proposal("PR1", "claim draft: the onset claim", 0)
        pr.status = "expired"
        matched, unmatched = match_ratify_target("the onset claim draft", [pr])
        self.assertEqual(matched, [])

    def test_tie_breaks_to_earliest(self):
        props = [_proposal("PR1", "claim draft alpha", 0),
                 _proposal("PR2", "claim draft beta", 1)]
        matched, _ = match_ratify_target("the claim draft", props)
        self.assertEqual(matched, ["PR1"])


class UnmatchedRatifyRecordedTest(unittest.TestCase):
    def test_unmatched_ratify_is_a_trace_error_not_an_exception(self):
        class Silent(Policy):
            name = "silent"

            def decide(self, moment):
                return []

        r = ReplayEngine(tiny_pool(), scenario([
            {"type": "session_start", "anchor": "Q1"},
            {"type": "ratify", "target": "a proposal that does not exist"},
        ]), Silent()).run()
        self.assertEqual(r.summary["unmatched_ratifies"], 1)


class DistillTest(unittest.TestCase):
    def test_distill_settles_all_pending_routine_rows_by_place(self):
        class Router(Policy):
            name = "router"

            def decide(self, moment):
                if moment.kind == "finding_landed":
                    f = moment.finding
                    return [O.RouteFinding(finding_id=f.id, stratum="story",
                                           questions=f.questions or ())]
                return []

        r = ReplayEngine(tiny_pool(), scenario([
            {"type": "session_start", "anchor": "Q1"},
            {"type": "finding", "ref": "G01"},
            {"type": "session_start", "anchor": "Q2"},   # G01's row still pending
            {"type": "finding", "ref": "G02"},
            {"type": "distill"},
        ]), Router()).run()
        rows = r.state.routing_rows
        self.assertTrue(all(row.status == "accepted" for row in rows.values()))
        # G02 (Q1+Q2) cited under both roots: one entity, no copies
        for q in ("Q1", "Q2"):
            root = r.state.sections[r.state.question_roots[q]]
            self.assertIn("G02", root.cited)
        self.assertEqual(len(r.state.routing_tables), 1)

    def test_decisions_are_not_accepted_by_distill(self):
        class Drafter(Policy):
            name = "drafter"

            def decide(self, moment):
                if moment.kind == "finding_landed":
                    return [O.Propose(
                        proposal_kind="claim_draft", proposal_cls="3",
                        description="claim draft on G01",
                        payload=(O.WriteProse(section="Q1", text="x",
                                              provenance=("G01",),
                                              ratified=True),))]
                return []

        r = ReplayEngine(tiny_pool(), scenario([
            {"type": "session_start", "anchor": "Q1"},
            {"type": "finding", "ref": "G01"},
            {"type": "distill"},
        ]), Drafter()).run()
        pr = r.state.proposals["PR1"]
        self.assertEqual(pr.status, "pending")     # decisions wait for ratify
        self.assertEqual(r.state.routing_tables[0]["decisions_pending"], ["PR1"])


class DischargeSubsumptionTest(unittest.TestCase):
    def test_row_names_items_marks_clear_only_on_acceptance(self):
        class Discharger(Policy):
            name = "discharger"

            def decide(self, moment):
                if moment.kind == "finding_landed" and moment.event.ref == "G02":
                    items = [i["id"] for i in
                             moment.state.plan_items(target="G01")]
                    return [O.RouteFinding(
                        finding_id="G02", stratum="story",
                        questions=("Q1", "Q2"),
                        note="cache-rate measurement answers both scrutiny "
                             "questions on G01",
                        discharges=tuple(items))]
                return []

        r = ReplayEngine(tiny_pool(), scenario([
            {"type": "session_start", "anchor": "Q1"},
            {"type": "finding", "ref": "G01"},
            {"type": "gesture", "verb": "check", "target": "G01"},
            {"type": "gesture", "verb": "corroborate", "target": "G01"},
            {"type": "finding", "ref": "G02"},
            {"type": "distill"},
        ]), Discharger()).run()
        items = list(r.state.plan_items.values())
        self.assertEqual(len(items), 2)
        # one landing discharged both items, named on the row
        self.assertTrue(all(i.state == "absorbed" for i in items))
        self.assertTrue(all(i.discharged_by is not None for i in items))
        self.assertFalse(any(i.provisional for i in items))
        self.assertEqual(r.state.findings["G01"].provisional_open, [])

    def test_direct_advance_to_absorbed_does_not_clear_provisional(self):
        # section 6: "provisional marks clear only when that row is accepted"
        # — a policy advancing the item by fiat does not lift the mark
        class DirectAdvancer(Policy):
            name = "direct_advancer"

            def decide(self, moment):
                if moment.kind == "instruction":
                    item = moment.state.plan_items(target="G01")[0]
                    return [O.AdvancePlanItem(item_id=item["id"],
                                              to_state="absorbed")]
                return []

        r = ReplayEngine(tiny_pool(), scenario([
            {"type": "session_start", "anchor": "Q1"},
            {"type": "finding", "ref": "G01"},
            {"type": "gesture", "verb": "check", "target": "G01"},
            {"type": "instruction", "text": "call it settled"},
        ]), DirectAdvancer()).run()
        item = r.state.plan_items["PI1"]
        self.assertEqual(item.state, "absorbed")
        self.assertTrue(item.provisional)               # mark still held
        self.assertIsNone(item.discharged_by)
        self.assertEqual(r.state.findings["G01"].provisional_open, ["PI1"])

    def test_provisional_holds_until_the_row_is_accepted(self):
        class Silent(Policy):
            name = "silent"

            def decide(self, moment):
                return []

        r = ReplayEngine(tiny_pool(), scenario([
            {"type": "session_start", "anchor": "Q1"},
            {"type": "finding", "ref": "G01"},
            {"type": "gesture", "verb": "check", "target": "G01"},
        ]), Silent()).run()
        item = r.state.plan_items["PI1"]
        self.assertTrue(item.provisional)
        self.assertEqual(item.state, "planned")    # user's own intent: no ceremony
        self.assertEqual(item.origin, "gesture")
        self.assertEqual(r.state.findings["G01"].provisional_open, ["PI1"])


class SplitReparentsProseTest(unittest.TestCase):
    def test_split_does_not_strand_prose_on_the_retired_section(self):
        class Splitter(Policy):
            name = "splitter"

            def decide(self, moment):
                if moment.kind == "finding_landed":
                    return [
                        O.CreateSection(question_id="Q1", title="Old",
                                        section_kind="stub", cls="1"),
                        O.WriteProse(section="Q1/Old", text="carried prose",
                                     provenance=("G01",)),
                        O.Propose(proposal_kind="restructuring",
                                  proposal_cls="2",
                                  description="split the old section",
                                  payload=(O.SplitSection(
                                      section="Q1/Old",
                                      new_titles=("A", "B")),)),
                    ]
                if moment.kind == "ratified":
                    return [O.ApplyConsented(proposal_id=pid)
                            for pid in moment.matched]
                return []

        r = ReplayEngine(tiny_pool(), scenario([
            {"type": "session_start", "anchor": "Q1"},
            {"type": "finding", "ref": "G01"},
            {"type": "ratify", "target": "split the old section"},
        ]), Splitter()).run()
        st = r.state
        old = next(s for s in st.sections.values() if s.title == "Old")
        self.assertEqual(old.status, "merged")
        block = st.prose["P1"]
        self.assertEqual(block.section_id, old.merged_into)   # first child
        child = st.sections[old.merged_into]
        self.assertEqual(child.title, "A")


class DeterminismTest(unittest.TestCase):
    def test_real_corpus_replay_is_deterministic(self):
        pool_dir = os.path.join(POOLS_ROOT, "gaia-bh1")
        pool = load_pool(pool_dir)
        scen = load_scenario(os.path.join(pool_dir, "scenarios", "pivot.json"),
                             pool)
        digests = set()
        for _ in range(2):
            policy = BASELINES["obey_overturn_labels"]()
            r = ReplayEngine(pool, scen, policy, snapshots=False).run()
            digests.add(r.summary["digest"])
        self.assertEqual(len(digests), 1)

    def test_digest_is_independent_of_snapshot_retention(self):
        pool_dir = os.path.join(POOLS_ROOT, "checkout-p99-lb-idle-timeout")
        pool = load_pool(pool_dir)
        scen = load_scenario(
            os.path.join(pool_dir, "scenarios", "contradiction.json"), pool)
        d = []
        for snapshots in (True, False):
            policy = BASELINES["never_restructure"]()
            r = ReplayEngine(pool, scen, policy, snapshots=snapshots).run()
            d.append(r.summary["digest"])
        self.assertEqual(d[0], d[1])


if __name__ == "__main__":
    unittest.main()
