"""Trajectory recording — every applied op, every delta a predicate may need.

The trace is a per-event list of entries: the event itself, gap/timer
accounting, sitting transitions, gesture compilations, ops proposed /
applied / rejected, proposal and plan-item transitions, routing settlements,
unmatched-ratify errors, and a per-event state digest.  With `snapshots=True`
(the default) a full `state.export()` snapshot is stored per event as well —
S3 predicates inspect trajectories, so we record generously.

Determinism: the run digest is a sha256 over the canonical JSON of all
entries EXCLUDING the bulky `snapshot` key (each entry's `state_digest`
already pins the state's content), so the digest is identical whether or not
snapshots are retained.  Nothing wall-clock enters the trace; all ids are
allocated from deterministic counters.
"""

from __future__ import annotations

import hashlib
import json


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def digest_of(obj) -> str:
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()


class Trace:
    def __init__(self, pool_id: str, scenario_id: str, policy_name: str,
                 snapshots: bool = True):
        self.pool_id = pool_id
        self.scenario_id = scenario_id
        self.policy_name = policy_name
        self.snapshots = snapshots
        self.entries: list[dict] = []
        self._current: dict | None = None

    # -- per-event lifecycle -------------------------------------------------

    def begin_event(self, event_dict: dict) -> None:
        self._current = {"event": event_dict, "notes": [], "ops": []}

    def note(self, category: str, **data) -> None:
        entry = {"note": category}
        entry.update(data)
        if self._current is None:           # pre/post-stream notes
            self.entries.append({"event": None, "notes": [entry], "ops": []})
        else:
            self._current["notes"].append(entry)

    def op(self, status: str, op_dict: dict, **data) -> None:
        entry = {"status": status, "op": op_dict}
        entry.update(data)
        self._current["ops"].append(entry)

    def end_event(self, state_export: dict) -> None:
        assert self._current is not None
        self._current["state_digest"] = digest_of(state_export)
        if self.snapshots:
            self._current["snapshot"] = state_export
        self.entries.append(self._current)
        self._current = None

    # -- digests -------------------------------------------------------------

    def run_digest(self) -> str:
        slim = []
        for e in self.entries:
            slim.append({k: v for k, v in e.items() if k != "snapshot"})
        return digest_of({
            "pool": self.pool_id,
            "scenario": self.scenario_id,
            "policy": self.policy_name,
            "entries": slim,
        })

    # -- convenience counters ------------------------------------------------

    def count_ops(self, status: str = "applied") -> int:
        return sum(1 for e in self.entries for o in e["ops"] if o["status"] == status)

    def count_notes(self, category: str) -> int:
        return sum(1 for e in self.entries for n in e["notes"]
                   if n["note"] == category)

    def notes(self, category: str) -> list[dict]:
        return [n for e in self.entries for n in e["notes"]
                if n["note"] == category]
