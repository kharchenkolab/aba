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

    # -- class-floor attacks (misdeclaration cannot buy a cheaper tier) ------

    def test_misdeclared_demotion_is_treated_at_its_floor(self):
        class MisdeclaredDemoter(_Base):
            def decide(self, moment):
                if moment.kind == "finding_landed":
                    return [O.DemoteSection(section="Q1", to="appendix",
                                            cls="1")]     # lies about weight
                return []

        with self.assertRaises(GateViolation) as ctx:
            self._run(MisdeclaredDemoter(), [
                {"type": "session_start", "anchor": "Q1"},
                {"type": "finding", "ref": "G01"},
            ])
        self.assertIn("class-3", str(ctx.exception))      # floored, then gated

    def test_misdeclared_split_and_full_section_trip(self):
        class MisdeclaredStructurer(_Base):
            def __init__(self, op):
                self.op = op

            def decide(self, moment):
                if moment.kind == "finding_landed":
                    return [self.op]
                return []

        for op in (
            O.SplitSection(section="Q1", new_titles=("A", "B"), cls="0"),
            O.MergeSections(sections=("Q1",), question_id="Q1", title="M",
                            cls="1"),
            O.PromoteSection(section="Q1", to_kind="section", cls="0"),
            O.CreateSection(question_id="Q1", title="Full", cls="1",
                            section_kind="section"),    # full section: floor 2
        ):
            with self.assertRaises(GateViolation, msg=op.kind):
                self._run(MisdeclaredStructurer(op), [
                    {"type": "session_start", "anchor": "Q1"},
                    {"type": "finding", "ref": "G01"},
                ])

    def test_class1_stub_creation_stays_legal(self):
        class StubMaker(_Base):
            def decide(self, moment):
                if moment.kind == "finding_landed":
                    return [O.CreateSection(question_id="Q1", title="Working",
                                            section_kind="stub", cls="1")]
                return []

        r = self._run(StubMaker(), [
            {"type": "session_start", "anchor": "Q1"},
            {"type": "finding", "ref": "G01"},
        ])
        self.assertEqual(r.summary["sections_created"], 1)

    # -- consent-ceiling attacks (payload smuggling) --------------------------

    def test_cheap_proposal_cannot_carry_expensive_payload(self):
        class PayloadSmuggler(_Base):
            def decide(self, moment):
                if moment.kind == "finding_landed":
                    return [O.Propose(
                        proposal_kind="restructuring", proposal_cls="2",
                        description="a routine tidy-up",
                        payload=(O.DemoteSection(section="Q1", to="appendix",
                                                 cls="3"),))]
                return []

        with self.assertRaises(GateViolation) as ctx:
            self._run(PayloadSmuggler(), [
                {"type": "session_start", "anchor": "Q1"},
                {"type": "finding", "ref": "G01"},
            ])
        self.assertIn("consent ceiling", str(ctx.exception))

    def test_cheap_proposal_cannot_smuggle_ratified_prose(self):
        class RatifiedSmuggler(_Base):
            def decide(self, moment):
                if moment.kind == "finding_landed":
                    return [O.Propose(
                        proposal_kind="note", proposal_cls="1",
                        description="just a note refresh",
                        payload=(O.WriteProse(section="Q1", text="x",
                                              provenance=("G01",),
                                              ratified=True),))]
                return []

        with self.assertRaises(GateViolation):
            self._run(RatifiedSmuggler(), [
                {"type": "session_start", "anchor": "Q1"},
                {"type": "finding", "ref": "G01"},
            ])

    def test_misdeclared_payload_inside_adequate_consent_applies(self):
        # the floor treats, it does not punish: a payload op declaring cls "1"
        # under a Class-3 proposal applies fine once ratified
        class HonestEnough(_Base):
            def decide(self, moment):
                if moment.kind == "finding_landed":
                    return [O.Propose(
                        proposal_kind="restructuring", proposal_cls="3",
                        description="demote the Q1 section to appendix",
                        payload=(O.DemoteSection(section="Q1", to="appendix",
                                                 cls="1"),))]
                if moment.kind == "ratified":
                    return [O.ApplyConsented(proposal_id=pid)
                            for pid in moment.matched]
                return []

        r = self._run(HonestEnough(), [
            {"type": "session_start", "anchor": "Q1"},
            {"type": "finding", "ref": "G01"},
            {"type": "ratify", "target": "the Q1 demotion to appendix"},
        ])
        root = r.state.sections[r.state.question_roots["Q1"]]
        self.assertEqual(root.rank, "appendix")

    # -- mark_superseded vs ratified prose (state-dependent X floor) ----------

    def test_superseding_a_finding_cited_by_ratified_prose_trips(self):
        class SupersederOfRatified(_Base):
            def decide(self, moment):
                if moment.kind == "gesture" and moment.event.verb == "draft_claim":
                    return [O.Propose(
                        proposal_kind="claim_draft",
                        proposal_cls=O.CLAIM_DRAFT_PROPOSAL_CLS,
                        description="claim draft: the build-latency claim",
                        payload=(O.WriteProse(section="Q1", text="Ratified.",
                                              provenance=("G01",),
                                              ratified=True),))]
                if moment.kind == "ratified":
                    return [O.ApplyConsented(proposal_id=pid)
                            for pid in moment.matched]
                if moment.kind == "finding_landed" and moment.event.ref == "G02":
                    # G01 now backs ratified prose: a Class-1 mark must trip
                    return [O.MarkSuperseded(finding_id="G01", by="G02")]
                return []

        with self.assertRaises(GateViolation) as ctx:
            self._run(SupersederOfRatified(), [
                {"type": "session_start", "anchor": "Q1"},
                {"type": "finding", "ref": "G01"},
                {"type": "gesture", "verb": "draft_claim", "target": "G01"},
                {"type": "ratify", "target": "the build-latency claim draft"},
                {"type": "finding", "ref": "G02"},
            ])
        self.assertIn("class-X", str(ctx.exception))

    def test_superseding_unratified_finding_stays_class1(self):
        class PlainSuperseder(_Base):
            def decide(self, moment):
                if moment.kind == "finding_landed" and moment.event.ref == "G02":
                    return [O.MarkSuperseded(finding_id="G01", by="G02")]
                return []

        r = self._run(PlainSuperseder(), [
            {"type": "session_start", "anchor": "Q1"},
            {"type": "finding", "ref": "G01"},
            {"type": "finding", "ref": "G02"},
        ])
        self.assertEqual(r.state.findings["G01"].superseded_by, "G02")

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
