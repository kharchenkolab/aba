"""S4 acceptance tests over the real corpus matrix (built once per module)."""

import unittest

from runner.baselines import BASELINES
from runner.matrix import (acceptance, build_matrix, per_organ,
                           scenario_discrimination)

_MATRIX = None


def matrix():
    global _MATRIX
    if _MATRIX is None:
        _MATRIX = build_matrix()
    return _MATRIX


class TestAcceptance(unittest.TestCase):
    def test_every_scenario_failed_by_some_baseline(self):
        flags = acceptance(matrix())
        self.assertEqual(flags, [],
                         f"non-discriminating scenarios: {flags}")

    def test_matrix_covers_all_16_scenarios_and_5_baselines(self):
        m = matrix()
        self.assertEqual(len(m), 16)
        for key, cell in m.items():
            for pol in BASELINES:
                self.assertIn(pol, cell, f"{key} missing {pol}")

    def test_scripted_good_is_ceiling_on_keyed_scenario(self):
        m = matrix()
        cell = m[("checkout-p99-lb-idle-timeout", "contradiction")]
        good = sum(1 for g in cell["scripted_good"] if g.passed)
        self.assertEqual(good, len(cell["scripted_good"]),
                         [g.detail for g in cell["scripted_good"]
                          if not g.passed])
        for pol in BASELINES:
            n = sum(1 for g in cell[pol] if g.passed)
            self.assertLessEqual(n, good)
        # and at least one baseline is strictly below the ceiling
        self.assertTrue(any(
            sum(1 for g in cell[pol] if g.passed) < good
            for pol in BASELINES))

    def test_inert_never_beats_never_restructure_overall(self):
        m = matrix()
        tot = {p: 0 for p in ("inert", "never_restructure")}
        for cell in m.values():
            for p in tot:
                tot[p] += sum(1 for g in cell[p] if g.passed)
        self.assertLessEqual(tot["inert"], tot["never_restructure"])

    def test_per_organ_report_covers_all_kinds(self):
        organs = per_organ(matrix())
        self.assertEqual(
            sorted(organs),
            sorted(["routing", "consent ceremony", "plan lifecycle",
                    "salience floors", "provenance", "structure"]))

    def test_discrimination_is_reported_per_scenario(self):
        disc = scenario_discrimination(matrix())
        self.assertEqual(len(disc), 16)
        for key, failers in disc.items():
            self.assertGreaterEqual(len(failers), 1, f"{key} unfailed")


if __name__ == "__main__":
    unittest.main()
