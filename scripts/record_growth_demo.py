#!/usr/bin/env python3
"""Growth-arc demo — the Record from inception to sizable complexity.

Seeds ONE synthetic investigation (a service-latency regression study —
generic, non-domain) at six stages of its life, each stage a fuller replay
of the same deterministic story, into six projects (stage1..stage6) under a
scratch runtime. Every stage's World is then asserted against expected
structural counts and cross-stage CONSISTENCY invariants:

  - question titles of stage k are a prefix of stage k+1, same order
    (structure grows by accretion; nothing silently reorders or vanishes);
  - claim/prose/note/run counts grow monotonically;
  - every sitting's run_ids resolve into that stage's sediment;
  - every tray row's thread resolves to a question.

With --serve, the staged runtime is served through the sidecar app
(record_face_server.build_app) for visual checks:

    python3 scripts/record_growth_demo.py --serve --port 8010
    → notebook.html?live=1&api=http://127.0.0.1:8010&project=stage4
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

# ------------------------------------------------------------------ story
# One investigation, told as (day, action) events; a stage is a horizon.
# Questions arrive over time; claims mature; a pivot parks a line; scale
# arrives as weeks of runs. All content generic.

QUESTIONS = [  # (key, title, question, arrives_day)
    ("q_lat", "What regressed in checkout latency?",
     "why did p99 regress 5x at peak while p50 held?", 0),
    ("q_cause", "What is the mechanism?",
     "what turns idle time into tail latency?", 3),
    ("q_alert", "Why did alerting miss it?",
     "why did no page fire for a 5x tail regression?", 9),
    ("q_tune", "Is the idle window tunable?",
     "what idle-timeout value avoids the penalty without ballooning pools?",
     10),
    ("q_retry", "Is the retry layer amplifying?",
     "do client retries double the damage past the deadline?", 14),
    ("q_cap", "Where is the capacity ceiling?",
     "how close to saturation does the fix leave stage 2?", 21),
    ("q_hist", "Has this happened before?",
     "are there earlier unexplained tail episodes with this signature?", 27),
]

# The org axis is recursive: subquestions arrive UNDER a line and may be
# promoted when their weight outgrows it. key -> (parent_key, promote_day):
# before promote_day the node is seeded as a child; from promote_day on it
# stands top-level (flat order is creation order, so promotion never
# reorders the accretion-consistency prefix).
SUBQUESTIONS = {
    "q_tune": ("q_cause", None),     # stays a subquestion through stage6
    "q_retry": ("q_cause", 23),      # nested at stage5, promoted by stage6
}

CLAIMS = [  # (title, question_key, day, status_by_stage_end)
    ("p99 regression is peak-hour only", "q_lat", 2, "supported"),
    ("regression onset matches the config push", "q_lat", 4, "contested"),
    ("idle connections pay a reconnect penalty", "q_cause", 5, "supported"),
    ("timeout change armed the idle penalty", "q_cause", 8, "validated"),
    ("alert threshold averaged away the tail", "q_alert", 11, "supported"),
    ("retries past deadline double exposure", "q_retry", 16, "preliminary"),
    ("120s idle keeps pools warm at peak", "q_tune", 20, "supported"),
    ("stage 2 saturates at 1.8x current peak", "q_cap", 23, "preliminary"),
    ("two prior episodes share the signature", "q_hist", 29, "preliminary"),
]

NARRATIVES = [  # (question_key, day, title, text) — ratified prose, arriving
    # as the story coheres; each paragraph written AT the maturity its day's
    # evidence supports (the rubric's "prose tracks evidence", seeded)
    ("q_lat", 4, "What we know so far",
     "The p99 regression is confined to peak hours; p50 holds throughout "
     "(supported). Onset roughly matches the config push, but the alignment "
     "is contested — the push landed mid-window and the first bad quantile "
     "precedes it by minutes."),
    ("q_cause", 8, "The mechanism, as it stands",
     "Idle connections pay a reconnect penalty on first use after a quiet "
     "gap (supported). The timeout change armed that penalty at peak: "
     "shortening the idle window turns warm pools cold between bursts, so "
     "the first request of every burst eats a handshake (validated). This "
     "is the load-bearing finding."),
    ("q_lat", 12, "Where this line settled",
     "Settled: the regression is the idle-timeout interaction, not a "
     "capacity problem. The config-push timing question is closed as "
     "coincidental. Line parked; active work continues under the "
     "mechanism question."),
    ("q_alert", 17, "Why the pager stayed quiet",
     "The alert threshold averaged away the tail: a one-minute mean over "
     "mixed traffic smooths sub-minute p99 spikes below the page line "
     "(supported). A drafted claim on the averaging window is pending."),
    ("q_retry", 24, "Early read on retry amplification",
     "Retries fired past the client deadline double downstream exposure in "
     "the replayed traces (preliminary — one load test, one trace day)."),
    ("q_cap", 30, "Headroom after the fix",
     "The capacity model puts stage-2 saturation at 1.8x current peak "
     "(preliminary); the fix leaves roughly 1.6x headroom before the next "
     "ceiling."),
    ("q_tune", 26, "Tuning readout",
     "A 120-second idle window keeps pools warm through peak gaps "
     "(supported); shorter windows re-arm the reconnect penalty, longer "
     "ones have not been costed."),
]

REVISIONS = [  # (qkey, day, revises_title, text) — ratified prose is never
    # rewritten: a revision SUPERSEDES its predecessor (wasDerivedFrom) and
    # cites the thread's claims, retiring their chips into the story
    ("q_cause", 19, "The mechanism, as it stands",
     "Idle connections pay a reconnect penalty on first use after a quiet "
     "gap (supported), and the timeout change armed that penalty at peak "
     "(validated): shortening the idle window turns warm pools cold "
     "between bursts, so the first request of every burst eats a "
     "handshake. The tunable-window line below is mapping the safe range."),
]

STAGES = {  # stage name -> story horizon in days
    "stage1": -1,   # inception: nothing yet
    "stage2": 1,    # first sitting
    "stage3": 5,    # first claims + first proposal
    "stage4": 12,   # three questions, maturing, a parked line
    "stage5": 22,   # five questions, weeks of runs
    "stage6": 31,   # full complexity
}
RUNS_PER_DAY = 3            # sediment mass
SITTING_SPACING_H = 4       # two bursts a day → distinct sittings


def seed_stage(name: str, horizon: int) -> None:
    """Build one project = the story up to `horizon` days, deterministically."""
    from core.graph._schema import _conn, init_db
    from core.graph.entities import create_entity, update_entity
    from core.graph.edges import add_edge
    from core.graph.proposals_store import add_proposal

    init_db()
    update_entity("workspace", title=f"Latency regression study ({name})")

    qids: dict[str, str] = {}
    for key, title, question, day in QUESTIONS:
        if day > horizon:
            continue
        lifecycle = "open"
        if key == "q_lat" and horizon >= 12:
            lifecycle = "parked"          # the pivot: settled, parked
        parent = None
        if key in SUBQUESTIONS:
            pkey, promote_day = SUBQUESTIONS[key]
            if pkey in qids and (promote_day is None or horizon < promote_day):
                parent = qids[pkey]
        qids[key] = create_entity(
            entity_type="thread", title=title,  # noqa: seam
            parent_entity_id=parent,
            metadata={"question": question, "open_questions": [],
                      "lifecycle": lifecycle})

    n_claims = 0
    cids: dict[str, list[tuple[int, str]]] = {}   # qkey -> [(day, claim id)]
    for title, qkey, day, status in CLAIMS:
        if day > horizon or qkey not in qids:
            continue
        cid = create_entity(entity_type="claim", title=title,  # noqa: seam
                            metadata={"thread_id": qids[qkey]})
        update_entity(cid, status=status)
        cids.setdefault(qkey, []).append((day, cid))
        # evidence artifacts: one figure per claim, edged supports
        fid = create_entity(entity_type="figure",  # noqa: seam
                            title=f"figs/{qkey}_d{day:02d}.png")
        add_edge(fid, cid, "supports")
        n_claims += 1

    # the story stratum: ratified paragraphs arrive as the story coheres —
    # metadata.text is the body the face reads (prose_body_key seam)
    nar_by_title: dict[str, str] = {}
    nar_stamps: list[tuple[str, str]] = []   # (entity id, story-time ts)
    for qkey, day, title, text in NARRATIVES:
        if day > horizon or qkey not in qids:
            continue
        nar_by_title[title] = create_entity(
            entity_type="narrative",  # noqa: seam
            title=title,
            metadata={"thread_id": qids[qkey], "text": text})
        nar_stamps.append((nar_by_title[title],
                           f"2026-06-{day + 1:02d}T18:00:00Z"))
    # revisions supersede with provenance; citing the thread's claims
    # retires their chips into the story (the drafting loop's shape)
    from core.graph.edges import add_edge as _add_edge
    for qkey, day, revises_title, text in REVISIONS:
        if day > horizon or revises_title not in nar_by_title:
            continue
        cited = [cid for d, cid in cids.get(qkey, []) if d <= day]
        new = create_entity(
            entity_type="narrative",  # noqa: seam
            title=revises_title,
            metadata={"thread_id": qids[qkey], "text": text,
                      "cites": cited, "drafted_claims": len(cited)})
        _add_edge(new, nar_by_title[revises_title], "wasDerivedFrom")
        nar_by_title[revises_title] = new
        nar_stamps.append((new, f"2026-06-{day + 1:02d}T18:00:00Z"))
    if horizon >= 4:
        create_entity(entity_type="note",  # noqa: seam
                      title="check the connection-pool metrics next sweep",
                      metadata={"thread_id": qids.get("q_cause",
                                                      qids["q_lat"])})
    if horizon >= 5:  # a leftover nobody carried + a pending routing proposal
        create_entity(entity_type="figure",  # noqa: seam
                      title="figs/uncarried_scan.png")
        add_proposal(thread_id=qids["q_cause"] if "q_cause" in qids
                     else qids["q_lat"],
                     kind="route", headline="file the reconnect-penalty "
                     "figure under the mechanism question",
                     signature=f"{name}-route-1")
    if horizon >= 12:
        add_proposal(thread_id=qids["q_alert"], kind="claim",
                     headline="draft: averaging window hides sub-minute tails",
                     signature=f"{name}-claim-1")

    with _conn() as c:
        # story-time stamps: the face reads dates off created_at/updated_at,
        # so seeded rows must live in story time, not seeding time — and
        # ratified prose names its ratifier
        for eid, ts in nar_stamps:
            c.execute("UPDATE entities SET created_at=?, updated_at=?, "
                      "actor='human:you' WHERE id=?", (ts, ts, eid))
        if "q_lat" in qids and horizon >= 12:   # parked on day 12
            c.execute("UPDATE entities SET updated_at=? WHERE id=?",
                      ("2026-06-13T18:00:00Z", qids["q_lat"]))
        c.execute("UPDATE entities SET created_at=?, updated_at=? "
                  "WHERE type='note'", ("2026-06-06T11:00:00Z",) * 2)
        c.commit()

    with _conn() as c:
        active = sorted(qids.values())
        for day in range(0, horizon + 1):
            for burst in range(2):
                for i in range(RUNS_PER_DAY if burst == 0 else 1):
                    rid = f"r-d{day:02d}-{burst}-{i}"
                    # a burst works ONE anchor (the day's question); the
                    # evening burst is a drive-by on the next line — the
                    # busy-scientist shape, not a per-run thread spray
                    tid = (active[(day + burst) % len(active)]
                           if active else None)
                    hh = 9 + burst * SITTING_SPACING_H
                    t0 = f"2026-06-{day + 1:02d}T{hh:02d}:{i * 7:02d}:00Z"
                    c.execute(
                        "INSERT INTO runs (run_id, thread_id, state, "
                        "agent_spec_name, turn_index, started_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (rid, tid,
                         "failed" if (day + i) % 11 == 3 else "done",
                         "guide", i, t0, t0))
        c.commit()

    # a distillation record freezes the day-8 morning sitting: it owns its
    # runs (clustering never redraws them) and wears a human label on the
    # face — the record-face invariant, seeded
    if horizon >= 8 and qids:
        active = sorted(qids.values())
        frozen_tid = active[8 % len(active)]
        did = create_entity(
            entity_type="note",  # noqa: seam
            title="traced the reconnect path end-to-end",
            metadata={"sitting_of": frozen_tid,
                      "run_ids": [f"r-d08-0-{i}"
                                  for i in range(RUNS_PER_DAY)]})
        with _conn() as c:
            c.execute("UPDATE entities SET created_at=?, updated_at=?, "
                      "actor='human:you' WHERE id=?",
                      ("2026-06-09T12:00:00Z",) * 2 + (did,))
            c.commit()


def build_runtime(root: Path) -> None:
    import os
    os.environ["ABA_RUNTIME_DIR"] = str(root)
    from core.graph import _schema
    for name, horizon in STAGES.items():
        pdir = root / "projects" / name
        pdir.mkdir(parents=True, exist_ok=True)
        token = _schema.bind_active_db(pdir / "project.db")
        try:
            seed_stage(name, horizon)
        finally:
            _schema._active_db_path.reset(token)
        print(f"seeded {name} (day ≤ {horizon})")


# ------------------------------------------------------------- assertions

def check_worlds(fetch) -> None:
    """Structural post-conditions + cross-stage consistency. `fetch(pid)`
    returns a World dict (in-process or over HTTP)."""
    worlds = {n: fetch(n) for n in STAGES}
    prev_titles: list[str] = []
    prev_counts = (0, 0, 0, 0)
    for name, horizon in STAGES.items():
        w = worlds[name]
        titles = [q["title"] for q in w["questions"]]
        n = (len(w["claims"]), len(w["prose"]), len(w["notes"]),
             len(w["sediment"]["runs"]))
        # inception face
        if horizon < 0:
            assert titles == [] and n == (0, 0, 0, 0), f"{name} not bare"
        # prefix consistency: growth is accretion, never reordering
        assert titles[:len(prev_titles)] == prev_titles, \
            f"{name}: question order changed: {prev_titles} -> {titles}"
        assert all(a >= b for a, b in zip(n, prev_counts)), \
            f"{name}: counts shrank {prev_counts} -> {n}"
        # internal referential integrity
        run_ids = {r["run_id"] for r in w["sediment"]["runs"]}
        for s in w["sittings"]:
            missing = [r for r in s["run_ids"] if r not in run_ids]
            assert not missing, f"{name}: sitting {s['id']} refs {missing}"
        qid_set = {q["id"] for q in w["questions"]}
        for p in w["tray"]:
            assert p["thread_id"] in qid_set, \
                f"{name}: tray row {p['id']} off-question"
        for c in w["claims"]:
            assert c["questions"], f"{name}: claim {c['id']} unrouted"
        # the story stratum READS: past the first days prose bodies exist,
        # and no body ever leaks an internal id into the reading surface
        if horizon >= 4:
            assert any(p.get("body") for p in w["prose"]), \
                f"{name}: story stratum empty — narrative missing"
        for p in w["prose"]:
            b = p.get("body") or ""
            assert not any(t in b for t in ("thr_", "run_", "sit-")), \
                f"{name}: internal id leaked into prose body"
        # the org axis: every parent resolves to a question in the same
        # stage, and the promotion arc actually flips between stages
        title_of = {q["id"]: q["title"] for q in w["questions"]}
        parents = {q["title"]: title_of[q["parent"]]
                   for q in w["questions"] if q.get("parent")}
        for pid in (q["parent"] for q in w["questions"] if q.get("parent")):
            assert pid in qid_set, f"{name}: dangling parent {pid}"
        if 10 <= horizon:
            assert parents.get("Is the idle window tunable?") == \
                "What is the mechanism?", f"{name}: q_tune not nested"
        if 14 <= horizon < 23:
            assert "Is the retry layer amplifying?" in parents, \
                f"{name}: q_retry should be nested pre-promotion"
        if horizon >= 23:
            assert "Is the retry layer amplifying?" not in parents, \
                f"{name}: q_retry should be promoted by day 23"
        # the freeze: from day 8 exactly one sitting is a distillation
        # entity — labeled, its runs owned by it alone, and its record
        # absent from the loose-notes stream
        frozen = [s for s in w["sittings"] if s.get("frozen")]
        if horizon >= 8:
            assert len(frozen) == 1 and \
                frozen[0]["label"] == "traced the reconnect path end-to-end", \
                f"{name}: frozen sitting missing or unlabeled"
            fr = set(frozen[0]["run_ids"])
            assert fr and fr <= run_ids, f"{name}: frozen runs unresolved"
            for s in w["sittings"]:
                if not s.get("frozen"):
                    assert not (fr & set(s["run_ids"])), \
                        f"{name}: frozen runs re-clustered"
            assert all("sitting_of" not in str(n) for n in w["notes"]), \
                f"{name}: distillation leaked into loose notes"
        else:
            assert not frozen, f"{name}: premature frozen sitting"
        # the revision arc: from day 19 the mechanism section reads v2 ONLY
        # (old prose superseded, kept in rows), and its citations cover the
        # thread's claims so the chips retire into the story
        mech = next((q for q in w["questions"]
                     if q["title"] == "What is the mechanism?"), None)
        if mech and horizon >= 19:
            heads = [p for p in w["prose"] if p["id"] in mech["prose"]]
            assert len(heads) == 1 and heads[0].get("versions") == 2, \
                f"{name}: mechanism head should be revision 2"
            assert set(heads[0].get("cites") or []) >= set(mech["claims"]), \
                f"{name}: revision must cite the thread's claims"
            assert len(w["prose"]) > len(
                [p for q2 in w["questions"] for p in q2["prose"]]), \
                f"{name}: superseded prose must stay in the rows"
        elif mech and 8 <= horizon < 19:
            heads = [p for p in w["prose"] if p["id"] in mech["prose"]]
            assert heads and "versions" not in heads[0], \
                f"{name}: mechanism should still be v1"
        prev_titles, prev_counts = titles, n
        print(f"{name}: q={len(titles)} claims={n[0]} prose={n[1]} "
              f"notes={n[2]} runs={n[3]} sittings={len(w['sittings'])} "
              f"tray={len(w['tray'])} leftovers={len(w['leftovers'])} OK")
    print("growth-arc consistency: ALL OK")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=None,
                        help="runtime dir (default: fresh temp dir)")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()

    root = Path(args.dir) if args.dir else \
        Path(tempfile.mkdtemp(prefix="record_growth_"))
    if not (root / "projects").exists():
        build_runtime(root)
    else:
        print(f"reusing runtime {root}")

    from record_face_server import build_app
    app = build_app(root)

    # in-process check (no HTTP needed)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    check_worlds(lambda pid: client.get(
        f"/api/record/world?project_id={pid}&sediment_limit=500").json())

    if args.serve:
        import uvicorn
        print(f"serving stages on 127.0.0.1:{args.port} from {root}")
        uvicorn.run(app, host="127.0.0.1", port=args.port,
                    log_level="warning")


if __name__ == "__main__":
    main()


def fetch_http(port: int):
    """Helper for checking a served instance from another process."""
    def f(pid: str) -> dict:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/record/world"
                f"?project_id={pid}&sediment_limit=500", timeout=10) as r:
            return json.load(r)
    return f