"""Did concurrent work actually OVERLAP? — the axis the concurrency lane missed.

`regtest/live/workflows.py --concurrent N` drives N threads at once and checks
that they do not corrupt each other: each recalls its own state, none sees a
sibling's, every row is attributed to its own thread. Every one of those
assertions is satisfied by lanes that ran strictly ONE AFTER ANOTHER. So the
lane was green through exactly the complaint users keep making — "I started
three threads and they are not progressing in parallel" — because nobody was
measuring wall-clock overlap.

This is the missing half, kept as a pure function over spans so it can be
guarded hermetically (tests/test_concurrency_metric.py) instead of only being
exercised by a live run that costs real LLM spend.

Vocabulary:
  parallelism   sum(span durations) / wall-clock span of the whole set.
                N fully-overlapping lanes → N. Fully serial → 1.0.
  max_in_flight the largest number of spans open at any instant — the honest
                answer to "were two ever running at the same time?", which
                parallelism alone can fake when one lane dwarfs the others.
"""
from __future__ import annotations


def overlap_report(spans: list[tuple[float, float]]) -> dict:
    """spans: [(start, end)] in seconds, any order. -> a verdict dict.

    Degenerate shapes are answers, not exceptions: an empty set, a single
    span, and zero-duration spans all have to say what they are, because a
    lane that measured nothing must never read as "concurrency confirmed"."""
    spans = [(float(a), float(b)) for a, b in spans if b is not None and a is not None]
    if not spans:
        return {"lanes": 0, "measured": False,
                "note": "no spans — this says NOTHING about concurrency"}
    total = sum(max(0.0, b - a) for a, b in spans)
    wall = max(b for _a, b in spans) - min(a for a, _b in spans)
    if wall <= 0 or total <= 0:
        return {"lanes": len(spans), "measured": False,
                "note": "zero-duration spans — nothing ran long enough to overlap"}
    # sweep the endpoints: +1 on open, -1 on close. Ties close before they open
    # so two spans that merely TOUCH (one ends exactly as the next begins —
    # the signature of perfect serialization) never read as in flight together.
    events = sorted([(a, 1) for a, _b in spans] + [(b, -1) for _a, b in spans],
                    key=lambda e: (e[0], e[1]))
    cur = peak = 0
    for _t, d in events:
        cur += d
        peak = max(peak, cur)
    return {"lanes": len(spans), "measured": True,
            "wall_s": round(wall, 2), "busy_s": round(total, 2),
            "parallelism": round(total / wall, 2), "max_in_flight": peak}


def serialization_checks(spans: list[tuple[float, float]], *,
                         min_parallelism: float = 1.5) -> list[tuple[str, bool]]:
    """The lane's assertions. ARMED FIRST: a set that measured nothing fails
    with that as the only finding, rather than answering the question it was
    asked with a number derived from no work."""
    r = overlap_report(spans)
    if not r.get("measured"):
        return [(f"PRECONDITION: lanes produced measurable spans "
                 f"({r.get('note')})", False)]
    n = r["lanes"]
    return [
        (f"{n} lanes were in flight together at some instant "
         f"(max_in_flight={r['max_in_flight']})", r["max_in_flight"] >= min(2, n)),
        (f"work overlapped rather than queued "
         f"(parallelism={r['parallelism']}, floor={min_parallelism}, "
         f"wall={r['wall_s']}s busy={r['busy_s']}s)",
         r["parallelism"] >= min_parallelism),
    ]
