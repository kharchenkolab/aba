"""Scripted-good on checkout/contradiction — the sanity ceiling.

Verifies the behaviors the scenario's assertions expect, so S4 has a policy
that provably can pass: ratified onset prose via claim draft + ratify; the
alternatives item; the Class-X addendum proposal on F20 with the cascade
re-exam mark; revise-with-provenance on F15/F16; superseded-but-findable
everywhere; no gate violations; deterministic."""

from __future__ import annotations

import os
import sys
import unittest

_RECORD_EVAL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _RECORD_EVAL not in sys.path:
    sys.path.insert(0, _RECORD_EVAL)

from runner.engine import ReplayEngine
from runner.events import load_pool, load_scenario
from runner.scripted_good import ScriptedGood

from runner.tests.fixtures import POOLS_ROOT


def _run(snapshots=False):
    pool_dir = os.path.join(POOLS_ROOT, "checkout-p99-lb-idle-timeout")
    pool = load_pool(pool_dir)
    scen = load_scenario(
        os.path.join(pool_dir, "scenarios", "contradiction.json"), pool)
    return ReplayEngine(pool, scen, ScriptedGood(), snapshots=snapshots).run()


class ScriptedGoodTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = _run()
        cls.st = cls.r.state

    def test_replays_clean_and_deterministically(self):
        self.assertEqual(self.r.summary["events"], 30)
        self.assertEqual(self.r.summary["unmatched_ratifies"], 0)
        self.assertEqual(self.r.summary["digest"], _run().summary["digest"])

    def test_onset_claim_ratified_via_draft_and_ratify(self):
        ratified = [p for p in self.st.prose.values() if p.ratified]
        self.assertEqual(len(ratified), 1)
        block = ratified[0]
        self.assertIn("F03", block.provenance)
        claim = self.st.proposals["PR1"]
        self.assertEqual(claim.kind, "claim_draft")
        self.assertEqual(claim.status, "accepted")
        self.assertTrue(claim.applied)

    def test_class_x_addendum_proposed_never_rewritten_still_pending(self):
        ratified = [p for p in self.st.prose.values() if p.ratified][0]
        # never rewritten: text intact, no superseding revision
        self.assertTrue(ratified.text.startswith("Onset:"))
        self.assertIsNone(ratified.superseded_by)
        # the addendum proposal exists, is Class X, cites F20+F03, and is
        # still pending (no second ratify in the stream) — never auto-applied
        addenda = [p for p in self.st.proposals.values() if p.kind == "addendum"]
        self.assertEqual(len(addenda), 1)
        self.assertEqual(addenda[0].cls, "X")
        self.assertEqual(addenda[0].status, "pending")
        self.assertEqual(len(self.st.addenda), 0)
        # the prose wears the visible condition mark meanwhile
        self.assertTrue(ratified.contested)

    def test_cascade_reexam_fired_against_live_attribution(self):
        # raised at F20's landing (event t=22 -> index 21), same cycle
        items = [i for i in self.st.plan_items.values()
                 if i.target == "F05" and i.kind == "check"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].created_event, 21)

    def test_alternatives_item_compiled_and_discharged_by_f16_row(self):
        alts = [i for i in self.st.plan_items.values()
                if i.target == "F05" and i.kind == "alternatives"]
        self.assertEqual(len(alts), 1)
        self.assertEqual(alts[0].origin, "gesture")
        # both scrutiny items on F05 discharged by the SAME named row
        f05_items = [i for i in self.st.plan_items.values()
                     if i.target == "F05"]
        self.assertEqual(len(f05_items), 2)
        rows = {i.discharged_by for i in f05_items}
        self.assertEqual(len(rows), 1)
        row = self.st.routing_rows[rows.pop()]
        self.assertEqual(row.product, "F16")
        self.assertEqual(sorted(row.discharges),
                         sorted(i.id for i in f05_items))
        self.assertTrue(all(i.state == "absorbed" for i in f05_items))
        self.assertEqual(self.st.findings["F05"].provisional_open, [])

    def test_revise_with_provenance_superseded_findable(self):
        st = self.st
        self.assertEqual(st.findings["F03"].superseded_by, "F20")
        self.assertEqual(st.findings["F10"].superseded_by, "F15")
        self.assertEqual(st.findings["F05"].superseded_by, "F16")
        for old_f, new_f in (("F03", "F20"), ("F10", "F15"), ("F05", "F16")):
            olds = [p for p in st.prose.values()
                    if old_f in p.provenance and not p.ratified
                    and p.revision_of is None and p.superseded_by is not None]
            self.assertTrue(olds, f"original {old_f} block missing")
            for old in olds:
                new = st.prose[old.superseded_by]
                self.assertIn(new_f, new.provenance)   # provenance-linked
                self.assertEqual(new.revision_of, old.id)

    def test_no_face_value_assertions_survive(self):
        st = self.st
        live_unratified = [p for p in st.prose.values()
                           if p.superseded_by is None and not p.ratified]
        for p in live_unratified:
            for fid in ("F03", "F05", "F10"):
                if fid in p.provenance:
                    # only revised readings may still cite the superseded ids
                    self.assertTrue(
                        p.revision_of is not None,
                        f"live block {p.id} cites {fid} at face value")

    def test_sections_and_routing(self):
        st = self.st
        self.assertEqual(self.r.summary["sections_created"], 3)
        self.assertTrue(any(s.title == "Impact" for s in st.sections.values()))
        self.assertTrue(any(s.title == "Mechanism"
                            for s in st.sections.values()))
        # all routine rows settled by the four distills
        self.assertEqual(self.r.summary["rows"]["pending"], 0)


if __name__ == "__main__":
    unittest.main()
