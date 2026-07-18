"""Context/memory live study (release_test_plan: 'Context / memory —
compaction, wipe recovery'): drive a thread PAST Tier-2 history compaction
(LLM thread summary) and require the agent to still answer project questions
from the DURABLE model (entities, pins), not the now-collapsed transcript.

Compaction thresholds are env-shrunk BEFORE the backend imports, so Tier-2
fires after a handful of chatty turns instead of ~400k chars. The scenario
POSITIVELY asserts a thread_summaries row exists — without that probe a run
where compaction never fired would pass vacuously.

Run:  python regtest/datasets/compaction_study.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Shrink ONLY the Tier-2 trigger; K-windows stay at defaults so a short
# thread's history is NOT pruned first (shrunken K left the pruned history
# under any threshold and Tier-2 could never fire — first-run finding, caught
# by the vacuity guard). NOTE: the global env knob is INERT for the primary
# agent — grounded_guide.yaml pins summary_budget_chars: 100000 and the spec
# wins (second-run finding; logged in the ledger as a doc/priority wart) —
# so main() also shrinks the LOADED spec in-process below.
os.environ.setdefault("ABA_HISTORY_SUMMARY_THRESHOLD_CHARS", "6000")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study  # noqa: E402 — throwaway home, oauth bridge, portal, harness

from study import (  # noqa: E402
    URL, drive_turn, run_scenario, scenario, RESULTS, tools_named,
)


@scenario("compaction_survival")
def compaction_survival(client, pid, tid):
    expected = sum(i * 3 % 17 for i in range(200))   # table.csv ground truth
    caps = [drive_turn(client, pid, tid,
        f"Register {URL} as a dataset called 'Signal table'. Then compute "
        f"the sum of its value column and pin that as a Result titled "
        f"'Signal total' with the number in the interpretation.")]
    # chatty filler turns — enough MESSAGES (>22, the TAIL_KEEP+2 gate) and
    # enough unpruned CHARS (>6k threshold) for Tier-2 to actually fire
    for k in range(8):
        caps.append(drive_turn(client, pid, tid,
            f"Quick check #{k}: print the integers from {k*50} to "
            f"{k*50+200}, one per line, then print their sum on the last "
            f"line and tell me just that sum."))
    # the probe question — must be answerable from the DURABLE model alone
    caps.append(drive_turn(client, pid, tid,
        "Without re-running anything: which dataset is this project working "
        "on, and what exact total does the pinned 'Signal total' result "
        "record? Answer from the project records."))
    final = caps[-1]["text"]

    import sqlite3
    db = os.environ["ABA_DB_PATH"]
    n_sum = 0
    try:
        c = sqlite3.connect(db)
        n_sum = c.execute("SELECT COUNT(*) FROM thread_summaries").fetchone()[0]
        c.close()
    except sqlite3.Error:
        n_sum = 0
    from core.summarize.budget_summary import tier2_diag
    print(f"    [tier2 diag] {tier2_diag()}")
    return caps, [
        ("Tier-2 compaction actually FIRED (thread_summaries row exists)",
         n_sum > 0),
        ("dataset recalled by name after compaction",
         "signal table" in final.lower()),
        ("pinned total recalled exactly (durable model, not transcript)",
         str(expected) in final.replace(",", "")),
    ]


@scenario("reentry_where_were_we")
def reentry_where_were_we(client, pid, tid):
    """The returns-days-later persona, compressed: a FRESH thread in a
    project with real prior work (the compaction scenario's entities). 'Where
    did we leave off?' must be answered from the durable project state —
    named entities, not vague filler and not a claim of emptiness."""
    cap = drive_turn(client, pid, tid,
        "I'm coming back to this project after a while. Where did we leave "
        "off — what data do we have and what results are pinned?")
    txt = cap["text"].lower()
    consulted = any(t["name"] in ("list_entities", "read_entity",
                                  "search_entities", "get_lineage")
                    for t in cap["tools"])
    return [cap], [
        ("agent consulted the durable model", consulted),
        ("names the dataset", "signal table" in txt),
        ("names the pinned result", "signal total" in txt),
        ("does not claim the project is empty",
         not any(w in txt for w in ("no data", "empty project",
                                    "nothing yet", "no results"))),
    ]


def main() -> None:
    from core.compute import adapter as ad
    st = ad.configure()
    assert st["ok"], st["detail"]
    # the primary spec's summary_budget_chars OVERRIDES the global env knob —
    # shrink the loaded spec itself so Tier-2 fires within this short study
    from core.runtime.agent import get_agent_spec, resolve_primary_spec_name
    sp = get_agent_spec(resolve_primary_spec_name())
    if sp is not None:
        try:
            sp.summary_budget_chars = 6000
            sp.summary_tail_keep = 10
        except Exception:  # noqa: BLE001 — frozen dataclass
            object.__setattr__(sp, "summary_budget_chars", 6000)
            object.__setattr__(sp, "summary_tail_keep", 10)
        print("[compact] primary spec summary budget shrunk to 6000 chars")
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as client:
        run_scenario(client, "compaction_survival", compaction_survival)
        # SAME project, NEW thread — the re-entry probe rides on the real
        # state the first scenario built
        pid2 = client.post("/api/projects",
                           json={"name": "ds-compaction_survival"}).json()["id"]
        tid2 = client.post("/api/threads",
                           json={"project_id": pid2,
                                 "title": "reentry"}).json()["id"]
        from study import RESULTS as _R
        from study import verify_jobs_truth
        import time as _t
        t0 = _t.time()
        try:
            caps, checks = reentry_where_were_we(client, pid2, tid2)
            checks = list(checks)
            v = verify_jobs_truth()
            checks += ([(f"truth-sweep: {x}", False) for x in v]
                       or [("jobs-vs-substrate truth sweep clean", True)])
            ok = all(c for _, c in checks)
        except Exception as e:  # noqa: BLE001
            checks = [(f"EXCEPTION: {e}", False)]
            ok = False
        _R.append(("reentry_where_were_we", ok))
        print(f"[{'PASS' if ok else 'FAIL'}] reentry_where_were_we "
              f"({int(_t.time() - t0)}s)")
        for label, c in checks:
            print(f"    {'✓' if c else '✗'} {label}")
    if not RESULTS:
        sys.exit("[compact] ZERO scenarios ran")
    ok = all(v for _, v in RESULTS)
    print("\nCOMPACTION STUDY:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
