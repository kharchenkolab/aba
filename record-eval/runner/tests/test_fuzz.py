"""S5 fuzzing-harness tests (small N to stay fast)."""

import os
import random
import unittest

from runner.events import load_pool
from runner.fuzz import fuzz_pool, random_ordering, synth_scenario

POOLS_ROOT = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "pools")
POOL_DIRS = [os.path.join(POOLS_ROOT, d) for d in sorted(os.listdir(POOLS_ROOT))
             if os.path.isdir(os.path.join(POOLS_ROOT, d, "scenarios"))]


class TestOrderingGeneration(unittest.TestCase):
    def test_orderings_are_dependency_closed(self):
        for pool_dir in POOL_DIRS:
            pool = load_pool(pool_dir)
            for k in range(10):
                rng = random.Random(k)
                order = random_ordering(pool, rng)
                seen = set()
                for fid in order:
                    for dep in pool.finding(fid).depends_on:
                        self.assertIn(dep, seen,
                                      f"{pool.id}: {fid} before dep {dep}")
                    seen.add(fid)
                self.assertEqual(len(order), len(pool.findings))

    def test_generation_is_deterministic(self):
        pool = load_pool(POOL_DIRS[0])
        s1 = synth_scenario(pool, random.Random(42), "fuzz-x")
        s2 = synth_scenario(pool, random.Random(42), "fuzz-x")
        self.assertEqual([ (e.t, e.type, e.ref, e.anchor) for e in s1.events],
                         [ (e.t, e.type, e.ref, e.anchor) for e in s2.events])

    def test_orderings_vary_across_seeds(self):
        pool = load_pool(POOL_DIRS[0])
        o1 = random_ordering(pool, random.Random(1))
        o2 = random_ordering(pool, random.Random(2))
        self.assertNotEqual(o1, o2)


class TestFuzzReplay(unittest.TestCase):
    def test_baselines_clean_on_random_orderings(self):
        for pool_dir in POOL_DIRS:
            counts, digests = fuzz_pool(
                pool_dir, n=5, seed=11,
                policies=["inert", "never_restructure",
                          "obey_overturn_labels"])
            for pol, fams in counts.items():
                self.assertEqual(sum(fams.values()), 0,
                                 f"{pool_dir}/{pol}: {fams}")

    def test_fuzz_digests_deterministic(self):
        pool_dir = POOL_DIRS[0]
        _, d1 = fuzz_pool(pool_dir, n=3, seed=5, policies=["inert"])
        _, d2 = fuzz_pool(pool_dir, n=3, seed=5, policies=["inert"])
        self.assertEqual(d1, d2)


if __name__ == "__main__":
    unittest.main()
