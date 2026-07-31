"""State-model semantics: sittings/episodes, holds, absence timer freeze,
Class-2 expiry, superseded-but-findable history."""

from __future__ import annotations

import os
import sys
import unittest

_RECORD_EVAL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _RECORD_EVAL not in sys.path:
    sys.path.insert(0, _RECORD_EVAL)

from runner import ops as O
from runner.engine import GateViolation, ReplayEngine
from runner.policy import Policy

from runner.tests.fixtures import scenario, tiny_pool


class Silent(Policy):
    name = "silent"

    def decide(self, moment):
        return []


class ProposeOnInstruction(Policy):
    """Raises one Class-2 proposal at the first instruction; applies on ratify."""

    name = "propose_on_instruction"

    def __init__(self):
        self.proposed = False

    def decide(self, moment):
        if moment.kind == "instruction" and not self.proposed:
            self.proposed = True
            return [O.Propose(
                proposal_kind="restructuring", proposal_cls="2",
                description="merge the build-latency subsections",
                payload=(O.SplitSection(section="Q1", new_titles=("A", "B")),))]
        if moment.kind == "ratified":
            return [O.ApplyConsented(proposal_id=pid) for pid in moment.matched]
        return []


def run(policy, events):
    return ReplayEngine(tiny_pool(), scenario(events), policy).run()


class SittingTest(unittest.TestCase):
    def test_next_summons_parks_and_holds_evaporate(self):
        r = run(Silent(), [
            {"type": "session_start", "anchor": "Q1"},
            {"type": "finding", "ref": "G01"},
            {"type": "gesture", "verb": "hold", "target": "G01"},
            {"type": "session_start", "anchor": "Q2"},
        ])
        t1, t2 = r.state.sittings["T1"], r.state.sittings["T2"]
        self.assertEqual(t1.state, "parked")
        self.assertEqual(r.state.holds, [])
        self.assertEqual(t2.state, "filed")      # scenario end files the live one
        self.assertNotEqual(t1.episode_id, t2.episode_id)

    def test_same_anchor_same_day_coalesces_one_episode(self):
        r = run(Silent(), [
            {"type": "session_start", "anchor": "Q1"},
            {"type": "finding", "ref": "G01"},
            {"type": "session_start", "anchor": "Q1"},   # same day micro-burst
            {"type": "clock", "advance_days": 1},
            {"type": "session_start", "anchor": "Q1"},   # next day: new episode
        ])
        s = r.state.sittings
        self.assertEqual(s["T1"].episode_id, s["T2"].episode_id)
        self.assertNotEqual(s["T1"].episode_id, s["T3"].episode_id)

    def test_clock_files_and_empty_sitting_files_silently(self):
        r = run(Silent(), [
            {"type": "session_start", "anchor": "Q1"},
            {"type": "clock", "advance_days": 1},
        ])
        t1 = r.state.sittings["T1"]
        self.assertEqual(t1.state, "filed")
        self.assertTrue(t1.silent_filed)

    def test_background_finding_lands_in_sediment_without_sitting(self):
        r = run(Silent(), [
            {"type": "session_start", "anchor": "Q1"},
            {"type": "clock", "advance_days": 2},
            {"type": "finding", "ref": "G01", "background": True},
        ])
        rec = r.state.findings["G01"]
        self.assertTrue(rec.background)
        self.assertIsNone(rec.sitting_id)
        self.assertEqual(rec.citations[0]["stratum"], "sediment")


class AbsenceTimerTest(unittest.TestCase):
    """The documented rule: attention gaps of <= 3 days accrue to Class-2
    timers; longer gaps are absences and contribute zero (timers freeze)."""

    def _events_with_gap(self, days):
        return [
            {"type": "session_start", "anchor": "Q1"},
            {"type": "instruction", "text": "tidy this up"},   # -> Class-2 proposal
            {"type": "clock", "advance_days": days},
            {"type": "instruction", "text": "back at it"},     # settles the gap
        ]

    def test_small_gap_accrues(self):
        r = run(ProposeOnInstruction(), self._events_with_gap(2))
        pr = r.state.proposals["PR1"]
        self.assertEqual(pr.status, "pending")
        self.assertEqual(pr.active_age, 2)
        self.assertEqual(r.state.absences, [])

    def test_large_gap_freezes(self):
        r = run(ProposeOnInstruction(), self._events_with_gap(21))
        pr = r.state.proposals["PR1"]
        self.assertEqual(pr.status, "pending")
        self.assertEqual(pr.active_age, 0)          # 14.7: timers froze
        self.assertEqual([a["days"] for a in r.state.absences], [21])

    def test_consecutive_clocks_form_one_gap_background_does_not_break_it(self):
        events = [
            {"type": "session_start", "anchor": "Q1"},
            {"type": "instruction", "text": "tidy this up"},
            {"type": "clock", "advance_days": 2},
            {"type": "finding", "ref": "G01", "background": True},
            {"type": "clock", "advance_days": 2},
            {"type": "instruction", "text": "back"},
        ]
        r = run(ProposeOnInstruction(), events)
        pr = r.state.proposals["PR1"]
        # 2+2 = one 4-day gap > 3: frozen, despite each clock being small
        self.assertEqual(pr.active_age, 0)
        self.assertEqual([a["days"] for a in r.state.absences], [4])

    def test_class2_expires_at_14_active_days_and_never_applies(self):
        events = [
            {"type": "session_start", "anchor": "Q1"},
            {"type": "instruction", "text": "tidy this up"},
        ]
        for _ in range(7):
            events.append({"type": "clock", "advance_days": 2})
            events.append({"type": "session_start", "anchor": "Q1"})
        r = run(ProposeOnInstruction(), events)
        pr = r.state.proposals["PR1"]
        self.assertEqual(pr.status, "expired")
        self.assertEqual(pr.active_age, 14)

        # a lapsed proposal can never apply — even if a ratify names it later
        events2 = events + [{"type": "ratify",
                             "target": "merge the build-latency subsections"}]
        with self.assertRaises(GateViolation):
            # the ratify matches nothing pending; force the point by applying
            class LateApplier(ProposeOnInstruction):
                def decide(self, moment):
                    if moment.kind == "ratified":
                        return [O.ApplyConsented(proposal_id="PR1")]
                    return super().decide(moment)
            run(LateApplier(), events2)

    def test_expired_is_visible_not_silent(self):
        events = [
            {"type": "session_start", "anchor": "Q1"},
            {"type": "instruction", "text": "tidy this up"},
        ]
        for _ in range(7):
            events.append({"type": "clock", "advance_days": 2})
            events.append({"type": "session_start", "anchor": "Q1"})
        r = run(ProposeOnInstruction(), events)
        self.assertEqual(len(r.trace.notes("proposal_expired")), 1)
        # nothing was applied: the split never happened
        self.assertEqual(r.summary["sections_created"], 0)


class HistoryTest(unittest.TestCase):
    def test_superseded_but_findable(self):
        class Reviser(Policy):
            name = "reviser"

            def decide(self, moment):
                if moment.kind == "finding_landed" and moment.event.ref == "G01":
                    return [O.WriteProse(section="Q1", text="original reading",
                                         provenance=("G01",))]
                if moment.kind == "finding_landed" and moment.event.ref == "G02":
                    block = moment.state.prose_blocks(citing="G01")[0]
                    return [
                        O.MarkSuperseded(finding_id="G01", by="G02"),
                        O.ReviseProse(prose_id=block["id"],
                                      text="revised reading",
                                      provenance=("G02",)),
                    ]
                return []

        r = run(Reviser(), [
            {"type": "session_start", "anchor": "Q1"},
            {"type": "finding", "ref": "G01"},
            {"type": "finding", "ref": "G02"},
        ])
        old = r.state.prose["P1"]
        new = r.state.prose["P2"]
        self.assertEqual(old.superseded_by, "P2")       # kept, linked, findable
        self.assertEqual(old.text, "original reading")  # never rewritten
        self.assertEqual(new.revision_of, "P1")
        self.assertIn("G01", new.provenance)            # provenance chain intact
        self.assertIn("G02", new.provenance)
        rec = r.state.findings["G01"]
        self.assertEqual(rec.superseded_by, "G02")
        self.assertTrue(all(c["revised"] for c in rec.citations))


if __name__ == "__main__":
    unittest.main()
