"""THE GATE — deliberately misbehaving policies must trip both invariants."""

from __future__ import annotations

import os
import sys
import unittest

_RECORD_EVAL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _RECORD_EVAL not in sys.path:
    sys.path.insert(0, _RECORD_EVAL)

from runner import ops as O
from runner.engine import GateViolation, ReplayEngine
from runner.policy import Moment, Policy

from runner.tests.fixtures import scenario, tiny_pool


class _Base(Policy):
    name = "misbehaving"

    def decide(self, moment: Moment):
        return []


class UnconsentedDemoter(_Base):
    """(a) attempts an unconsented Class-3 demotion."""

    def decide(self, moment):
        if moment.kind == "finding_landed":
            return [O.DemoteSection(section="Q1", to="appendix", cls="3")]
        return []


class RatifiedRewriter(_Base):
    """(b) legally lands ratified prose, then rewrites it directly."""

    def decide(self, moment):
        if moment.kind == "gesture" and moment.event.verb == "draft_claim":
            return [O.Propose(
                proposal_kind="claim_draft", proposal_cls="3",
                description="claim draft: the build-latency regression claim",
                payload=(O.WriteProse(section="Q1", text="Ratified claim.",
                                      provenance=("G01",), ratified=True),))]
        if moment.kind == "ratified":
            return [O.ApplyConsented(proposal_id=pid) for pid in moment.matched]
        if moment.kind == "instruction":
            block = moment.state.prose_blocks(ratified=True)[0]
            return [O.ReviseProse(prose_id=block["id"],
                                  text="quietly rewritten")]
        return []


class SelfRatifier(_Base):
    """Applies its own pending proposal without any consent event."""

    def decide(self, moment):
        if moment.kind == "finding_landed":
            return [
                O.Propose(proposal_kind="restructuring", proposal_cls="2",
                          description="split the impact section",
                          payload=(O.SplitSection(section="Q1",
                                                  new_titles=("A", "B")),)),
            ]
        if moment.kind == "instruction":
            pid = moment.state.pending_proposals()[0]["id"]
            return [O.ApplyConsented(proposal_id=pid)]
        return []


class UnconsentedRatifiedWriter(_Base):
    """Writes ratified prose directly (only the user writes)."""

    def decide(self, moment):
        if moment.kind == "finding_landed":
            return [O.WriteProse(section="Q1", text="ratified by nobody",
                                 provenance=("G01",), ratified=True)]
        return []


class DirectAddendumWriter(_Base):
    """Adds an addendum without the propose->ratify ceremony."""

    def decide(self, moment):
        if moment.kind == "finding_landed" and moment.event.ref == "G01":
            return [O.WriteProse(section="Q1", text="plain prose",
                                 provenance=("G01",))]
        if moment.kind == "finding_landed" and moment.event.ref == "G02":
            block = moment.state.prose_blocks(citing="G01")[0]
            return [O.AddAddendum(prose_id=block["id"], text="sneaky addendum",
                                  provenance=("G02",))]
        return []


class GateTest(unittest.TestCase):
    def _run(self, policy, events):
        engine = ReplayEngine(tiny_pool(), scenario(events), policy)
        return engine.run()

    def test_unconsented_class3_demotion_trips(self):
        with self.assertRaises(GateViolation) as ctx:
            self._run(UnconsentedDemoter(), [
                {"type": "session_start", "anchor": "Q1"},
                {"type": "finding", "ref": "G01"},
            ])
        self.assertIn("consent conservation", str(ctx.exception))

    def test_direct_rewrite_of_ratified_prose_trips(self):
        with self.assertRaises(GateViolation) as ctx:
            self._run(RatifiedRewriter(), [
                {"type": "session_start", "anchor": "Q1"},
                {"type": "finding", "ref": "G01"},
                {"type": "gesture", "verb": "draft_claim", "target": "G01"},
                {"type": "ratify", "target": "the build-latency regression claim draft"},
                {"type": "instruction", "text": "tighten the wording"},
            ])
        self.assertIn("authored-text immutability", str(ctx.exception))

    def test_apply_without_consent_event_trips(self):
        with self.assertRaises(GateViolation):
            self._run(SelfRatifier(), [
                {"type": "session_start", "anchor": "Q1"},
                {"type": "finding", "ref": "G01"},
                {"type": "instruction", "text": "carry on"},
            ])

    def test_unconsented_ratified_write_trips(self):
        with self.assertRaises(GateViolation):
            self._run(UnconsentedRatifiedWriter(), [
                {"type": "session_start", "anchor": "Q1"},
                {"type": "finding", "ref": "G01"},
            ])

    def test_direct_addendum_trips(self):
        with self.assertRaises(GateViolation):
            self._run(DirectAddendumWriter(), [
                {"type": "session_start", "anchor": "Q1"},
                {"type": "finding", "ref": "G01"},
                {"type": "finding", "ref": "G02"},
            ])

    def test_legal_path_passes_the_same_gate(self):
        # the identical demotion, expressed legally, applies cleanly
        class LegalDemoter(_Base):
            def decide(self, moment):
                if moment.kind == "finding_landed":
                    return [O.Propose(
                        proposal_kind="restructuring", proposal_cls="3",
                        description="demote the Q1 impact section to appendix",
                        payload=(O.DemoteSection(section="Q1", to="appendix",
                                                 cls="3"),))]
                if moment.kind == "ratified":
                    return [O.ApplyConsented(proposal_id=pid)
                            for pid in moment.matched]
                return []

        result = self._run(LegalDemoter(), [
            {"type": "session_start", "anchor": "Q1"},
            {"type": "finding", "ref": "G01"},
            {"type": "ratify", "target": "the Q1 demotion to appendix"},
        ])
        root = result.state.sections[result.state.question_roots["Q1"]]
        self.assertEqual(root.rank, "appendix")
        pr = result.state.proposals["PR1"]
        self.assertEqual(pr.status, "accepted")
        self.assertTrue(pr.applied)


if __name__ == "__main__":
    unittest.main()
