"""The concurrency lane's missing axis: did the lanes actually OVERLAP?

`--concurrent N` asserted only that concurrent threads do not corrupt each
other. Lanes that ran strictly one after another satisfy every one of those
checks, so the lane stayed green through the exact complaint it exists to
catch. These guard the metric that closes the gap
(regtest/harness/concurrency.py).

Run: python tests/test_concurrency_metric.py   (or via pytest)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regtest.harness.concurrency import (overlap_report,          # noqa: E402
                                         serialization_checks)


def test_serialized_lanes_are_reported_as_serialized():
    """THE case the lane could not see: three lanes, each 10s, run back to
    back. Correctness-wise they are perfect; they took 30s to do 30s of work."""
    spans = [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)]
    r = overlap_report(spans)
    assert r["parallelism"] == 1.0
    assert r["max_in_flight"] == 1, "touching spans are not 'in flight together'"
    assert [ok for _n, ok in serialization_checks(spans)] == [False, False]


def test_overlapping_lanes_pass():
    """ARMED the other way: genuine concurrency must not trip the guard, or
    the lane becomes a permanent red nobody reads."""
    spans = [(0.0, 10.0), (0.5, 10.5), (1.0, 11.0)]
    r = overlap_report(spans)
    assert r["max_in_flight"] == 3
    assert r["parallelism"] > 2.5
    assert all(ok for _n, ok in serialization_checks(spans))


def test_one_long_lane_cannot_fake_parallelism():
    """WIDE — the degenerate shape parallelism alone gets wrong: one lane runs
    100s while two blink for 0.1s inside it. sum/wall is barely over 1, but the
    real failure is that the two SHORT lanes never overlapped each other. Both
    signals are reported so neither can carry the verdict alone."""
    spans = [(0.0, 100.0), (10.0, 10.1), (50.0, 50.1)]
    r = overlap_report(spans)
    assert r["max_in_flight"] == 2          # the long one plus one blink
    assert r["parallelism"] < 1.1           # and almost no overlap by time
    assert not all(ok for _n, ok in serialization_checks(spans))


def test_a_set_that_measured_nothing_fails_as_a_precondition():
    """A run in which nothing happened must say THAT — never answer the
    concurrency question with a number derived from no work."""
    for spans in ([], [(5.0, 5.0), (5.0, 5.0)]):
        r = overlap_report(spans)
        assert r["measured"] is False
        checks = serialization_checks(spans)
        assert len(checks) == 1 and checks[0][1] is False
        assert "PRECONDITION" in checks[0][0]


def test_a_single_lane_is_not_evidence_of_anything():
    """One span cannot demonstrate concurrency; it must not read as success."""
    checks = serialization_checks([(0.0, 10.0)])
    assert not all(ok for _n, ok in checks)


def _standalone() -> int:
    import traceback
    rc = 0
    for t in (test_serialized_lanes_are_reported_as_serialized,
              test_overlapping_lanes_pass,
              test_one_long_lane_cannot_fake_parallelism,
              test_a_set_that_measured_nothing_fails_as_a_precondition,
              test_a_single_lane_is_not_evidence_of_anything):
        try:
            t()
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
