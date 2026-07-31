"""Completeness + smoke checks for the compiled corpus assertions."""

import json
import os
import unittest

from runner.compiled import registry
from runner.predicates import PREDICATES

POOLS_ROOT = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "pools")


def corpus_assertions():
    out = {}
    for pool_name in sorted(os.listdir(POOLS_ROOT)):
        scen_dir = os.path.join(POOLS_ROOT, pool_name, "scenarios")
        if not os.path.isdir(scen_dir):
            continue
        for fn in sorted(os.listdir(scen_dir)):
            if fn.endswith(".json"):
                with open(os.path.join(scen_dir, fn)) as fh:
                    s = json.load(fh)
                out[(pool_name, s["id"])] = s
    return out


class TestCompleteness(unittest.TestCase):
    def setUp(self):
        self.corpus = corpus_assertions()
        self.reg = registry()

    def test_every_assertion_compiled_exactly_once(self):
        total = 0
        for key, scen in self.corpus.items():
            compiled = self.reg.get(key, [])
            self.assertEqual(
                len(compiled), len(scen["assertions"]),
                f"{key}: {len(compiled)} compiled vs "
                f"{len(scen['assertions'])} corpus assertions")
            self.assertEqual([c.index for c in compiled],
                             list(range(len(compiled))), f"{key}: index gap")
            total += len(compiled)
        self.assertEqual(total, 106)

    def test_kinds_match_corpus(self):
        for key, scen in self.corpus.items():
            for c in self.reg[key]:
                self.assertEqual(c.kind, scen["assertions"][c.index]["kind"],
                                 f"{key}[{c.index}] kind mismatch")

    def test_windows_resolve_against_streams(self):
        for key, scen in self.corpus.items():
            ts = {e["t"] for e in scen["events"]}
            t_max = max(ts)
            for c in self.reg[key]:
                at = scen["assertions"][c.index]["at"]
                if at == "end":
                    # end-assertions grade terminal state (window None) or a
                    # documented sub-window (e.g. a sitting's growth bound)
                    if c.window is not None:
                        lo, hi = c.window
                        self.assertLessEqual(lo, hi, f"{key}[{c.index}]")
                else:
                    self.assertIsNotNone(c.window, f"{key}[{c.index}]")
                    lo, hi = c.window
                    self.assertLessEqual(lo, hi, f"{key}[{c.index}]")
                    # windows either mirror the corpus 'at' or extend to
                    # stream end (open-ended readings use 999)
                    # compiled windows must CONTAIN the corpus window
                    # (widening is a documented reading choice; 999 = to-end)
                    spec = at.replace("event:", "")
                    s_lo = int(spec.split("..")[0])
                    s_hi = int(spec.split("..")[-1])
                    self.assertLessEqual(lo, s_lo, f"{key}[{c.index}]")
                    self.assertGreaterEqual(hi, s_hi, f"{key}[{c.index}]")
                    self.assertLessEqual(s_hi, t_max, f"{key}[{c.index}]")

    def test_predicates_exist(self):
        for key, compiled in self.reg.items():
            for c in compiled:
                self.assertIn(c.predicate, PREDICATES, f"{key}[{c.index}]")


class TestSmokeGrade(unittest.TestCase):
    def test_scripted_good_beats_inert_on_keyed_scenario(self):
        from runner.grade import grade
        pool_dir = os.path.join(POOLS_ROOT, "checkout-p99-lb-idle-timeout")
        good, _ = grade(pool_dir, "contradiction", "scripted_good")
        inert, _ = grade(pool_dir, "contradiction", "inert")
        n_good = sum(1 for g in good if g.passed)
        n_inert = sum(1 for g in inert if g.passed)
        self.assertGreater(n_good, n_inert + 1,
                           f"scripted_good {n_good}/6 vs inert {n_inert}/6")
        self.assertGreaterEqual(n_good, 4,
                                [f"[{g.index}] {g.detail}" for g in good
                                 if not g.passed])

    def test_no_predicate_errors_anywhere(self):
        from runner.grade import grade
        for pool_name in sorted(os.listdir(POOLS_ROOT)):
            pool_dir = os.path.join(POOLS_ROOT, pool_name)
            scen_dir = os.path.join(pool_dir, "scenarios")
            if not os.path.isdir(scen_dir):
                continue
            for fn in sorted(os.listdir(scen_dir)):
                if not fn.endswith(".json"):
                    continue
                graded, _ = grade(pool_dir, fn[:-5], "never_restructure")
                for g in graded:
                    self.assertNotIn("PREDICATE ERROR", g.detail,
                                     f"{pool_name}/{fn}[{g.index}]: {g.detail}")


if __name__ == "__main__":
    unittest.main()
