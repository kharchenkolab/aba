"""Baselines: all 16 scenarios replay clean under all five; each baseline's
signature behavior holds; none trips the gate."""

from __future__ import annotations

import os
import sys
import unittest

_RECORD_EVAL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _RECORD_EVAL not in sys.path:
    sys.path.insert(0, _RECORD_EVAL)

from runner.baselines import BASELINES
from runner.engine import ReplayEngine
from runner.events import load_all_scenarios, load_pool

from runner.tests.fixtures import POOLS_ROOT

_POOLS = {}
_SCENARIOS = {}


def _pool(name):
    if name not in _POOLS:
        pool_dir = os.path.join(POOLS_ROOT, name)
        _POOLS[name] = load_pool(pool_dir)
        _SCENARIOS[name] = load_all_scenarios(pool_dir, _POOLS[name])
    return _POOLS[name], _SCENARIOS[name]


def _replay(pool_name, scenario_id, policy_name):
    pool, scenarios = _pool(pool_name)
    scen = next(s for s in scenarios if s.id == scenario_id)
    return ReplayEngine(pool, scen, BASELINES[policy_name](),
                        snapshots=False).run()


class AllScenariosAllBaselinesTest(unittest.TestCase):
    def test_16_scenarios_x_5_baselines_replay_without_exceptions(self):
        total = 0
        for pool_name in ("checkout-p99-lb-idle-timeout", "gaia-bh1"):
            pool, scenarios = _pool(pool_name)
            self.assertEqual(len(scenarios), 8)
            for scen in scenarios:
                for name, cls in BASELINES.items():
                    r = ReplayEngine(pool, scen, cls(), snapshots=False).run()
                    self.assertEqual(r.summary["events"], len(scen.events))
                    total += 1
        self.assertEqual(total, 80)


class InertTest(unittest.TestCase):
    def test_inert_only_sediments(self):
        r = _replay("checkout-p99-lb-idle-timeout", "flood", "inert")
        st = r.state
        self.assertEqual(len(st.prose), 0)
        self.assertEqual(len(st.proposals), 0)
        self.assertEqual(r.summary["sections_created"], 0)
        self.assertTrue(all(row.stratum == "sediment"
                            for row in st.routing_rows.values()))


class NeverRestructureTest(unittest.TestCase):
    def test_writes_and_routes_but_never_structures(self):
        r = _replay("checkout-p99-lb-idle-timeout", "contradiction",
                    "never_restructure")
        st = r.state
        self.assertEqual(r.summary["sections_created"], 0)
        self.assertGreater(len(st.prose), 0)
        # honest multi-question routing: F04 (Q1+Q2) cited under both roots
        for q in ("Q1", "Q2"):
            root = st.sections[st.question_roots[q]]
            self.assertIn("F04", root.cited)
        # the ratified claim draft applied into ratified prose
        ratified = [p for p in st.prose.values() if p.ratified]
        self.assertEqual(len(ratified), 1)
        self.assertIn("F03", ratified[0].provenance)
        # ...and it ignores the overturn: no addendum, F03 not superseded
        self.assertEqual(len(st.addenda), 0)
        self.assertIsNone(st.findings["F03"].superseded_by)

    def test_untagged_findings_go_to_notes(self):
        r = _replay("checkout-p99-lb-idle-timeout", "flood",
                    "never_restructure")
        rows = [row for row in r.state.routing_rows.values()
                if row.product == "F36"]
        self.assertTrue(rows)
        self.assertTrue(all(row.stratum == "notes" for row in rows))


class AppendToFirstQuestionTest(unittest.TestCase):
    def test_everything_lands_under_the_anchor(self):
        # F14 in gaia/interleaved is tagged Q1+Q3 but emitted in a Q2 sitting
        r = _replay("gaia-bh1", "interleaved", "append_to_first_question")
        st = r.state
        f14_rows = [row for row in st.routing_rows.values()
                    if row.product == "F14"]
        self.assertEqual(len(f14_rows), 1)
        self.assertEqual(f14_rows[0].questions, ["Q2"])   # anchor, not tags
        honest = _replay("gaia-bh1", "interleaved", "never_restructure")
        h_rows = [row for row in honest.state.routing_rows.values()
                  if row.product == "F14"]
        self.assertEqual(h_rows[0].questions, ["Q1", "Q3"])

    def test_creates_only_class1_stubs(self):
        r = _replay("checkout-p99-lb-idle-timeout", "interleaved",
                    "append_to_first_question")
        made = [s for s in r.state.sections.values()
                if s.kind != "question-root"]
        self.assertTrue(made)
        for s in made:
            self.assertEqual(s.kind, "stub")
            self.assertEqual(s.history[0]["cls"], "1")


class ObeyOverturnLabelsTest(unittest.TestCase):
    def test_mechanical_revision_both_severities_legal_paths(self):
        r = _replay("checkout-p99-lb-idle-timeout", "contradiction",
                    "obey_overturn_labels")
        st = r.state
        # unratified severities marked superseded directly, findable
        self.assertEqual(st.findings["F10"].superseded_by, "F15")
        self.assertEqual(st.findings["F05"].superseded_by, "F16")
        # F03 backs RATIFIED prose: its supersession mark rides the pending
        # Class-X proposal (the gate floors mark_superseded at 'X' there),
        # so it is NOT applied without consent
        self.assertIsNone(st.findings["F03"].superseded_by)
        # unratified prose citing F10/F05 was revised in place (chain kept)
        for fid in ("F10", "F05"):
            olds = [p for p in st.prose.values()
                    if fid in p.provenance and p.superseded_by is not None]
            self.assertTrue(olds, f"no revised block for {fid}")
        # the RATIFIED F03 prose went through the addendum PROPOSAL channel:
        # never rewritten, proposal still pending (no second ratify exists)
        ratified = [p for p in st.prose.values() if p.ratified]
        self.assertEqual(len(ratified), 1)
        self.assertIsNone(ratified[0].superseded_by)
        addenda_props = [p for p in st.proposals.values()
                         if p.kind == "addendum"]
        self.assertEqual(len(addenda_props), 1)
        self.assertEqual(addenda_props[0].status, "pending")
        self.assertTrue(ratified[0].contested)
        self.assertEqual(len(st.addenda), 0)      # pending, never silently applied
        # the pending payload carries both the addendum and the mark
        kinds = sorted(op.kind for op in addenda_props[0].payload)
        self.assertEqual(kinds, ["add_addendum", "mark_superseded"])

    def test_gaia_contradiction_treats_both_severities_identically(self):
        # F12-over-F03 (unratified) -> direct revision; F35-over-F32 arrives
        # after the CE framing ratify: same mechanical rule, addendum channel
        r = _replay("gaia-bh1", "contradiction", "obey_overturn_labels")
        st = r.state
        self.assertEqual(st.findings["F03"].superseded_by, "F12")
        self.assertEqual(st.findings["F07"].superseded_by, "F10")
        self.assertEqual(st.findings["F32"].superseded_by, "F35")


class IgnoreWeakTest(unittest.TestCase):
    def test_weak_findings_are_dropped_entirely(self):
        r = _replay("checkout-p99-lb-idle-timeout", "contradiction",
                    "ignore_weak")
        st = r.state
        # F03 is weak: no routing row, no prose, and the draft_claim gesture
        # aimed at it was ignored -> the t=8 ratify matched nothing
        self.assertEqual([row for row in st.routing_rows.values()
                          if row.product == "F03"], [])
        self.assertEqual([p for p in st.prose.values()
                          if "F03" in p.provenance], [])
        self.assertEqual(len(st.proposals), 0)
        self.assertEqual(r.summary["unmatched_ratifies"], 1)
        # but the finding still exists in the sediment (landing is substrate)
        self.assertEqual(st.findings["F03"].citations[0]["stratum"], "sediment")
        # moderate/strong handled honestly
        self.assertTrue([row for row in st.routing_rows.values()
                         if row.product == "F04"])


if __name__ == "__main__":
    unittest.main()
