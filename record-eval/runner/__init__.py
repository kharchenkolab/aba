"""Record eval runner — S1+S2.

Replay engine + Record state model + invariant gate + reference baselines.

Layout:
  events.py       event/pool/scenario dataclasses + loaders (ref validation)
  state.py        the Record state model + read-only RecordView
  ops.py          typed op vocabulary (policy -> engine)
  engine.py       replay engine + THE GATE (consent conservation,
                  authored-text immutability)
  policy.py       Policy ABC + Moment (the editorial-moment payload)
  baselines.py    inert, never_restructure, append_to_first_question,
                  obey_overturn_labels, ignore_weak
  scripted_good.py hand-written good policy for
                  checkout-p99-lb-idle-timeout / contradiction
  trace.py        trajectory recording + stable digests
  cli.py          `python3 -m runner.cli replay ...` (run from record-eval/)

Stdlib only; Python >= 3.10.
"""

__all__ = [
    "events",
    "state",
    "ops",
    "engine",
    "policy",
    "baselines",
    "scripted_good",
    "trace",
    "cli",
]
