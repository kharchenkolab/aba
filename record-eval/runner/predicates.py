"""Predicate library — grades replay trajectories against corpus assertions.

Built bottom-up from the 106 corpus assertion sentences (RUNNER_HANDOFF §2):
a predicate was added only when no existing one expressed a sentence, and
parameterized aggressively instead of proliferating.  Final count: 17.

Every predicate has the signature

    pred(traj, pool, scenario, *, window, **params) -> Verdict

where `traj` is a Trajectory wrapper over a Trace with per-event snapshots,
`window` is a (t_lo, t_hi) tuple of scenario t values or None meaning END
(terminal state), and Verdict(passed, detail) is the result.  Predicates are
total: they never raise on empty stores — they fail with a detail string.

Conventions relied on (documented in engine.py / REPORT.md):
- the "tray" is reconstructed as pending routing rows + pending proposals
  (rows carry `typed`; NOTE: the current engine types every policy row
  "routine", so decision-typing enters via pending decision proposals);
- "cited under all tagged questions, no copies" uses the deduped
  `section.cited` list, never `finding.citations` (not deduped);
- the `contested` flag on ratified prose carries "no prose asserts X at face
  value" while a Class-X addendum proposal pends;
- prose-maturity-vs-claim-strength joins against the Pool object (the trace
  does not carry strengths).
"""

from __future__ import annotations

from dataclasses import dataclass

from .ops import CLASS_ORDER

MATURITY_ORDER = {"conjecture": 0, "supported": 1, "cross-checked": 2, "robust": 3}
PLAN_ORDER = {"proposed": 0, "planned": 1, "taken-up": 2, "produced": 3, "absorbed": 4}
STRUCTURAL_OPS = ("promote_section", "demote_section", "merge_sections",
                  "split_section")


@dataclass
class Verdict:
    passed: bool
    detail: str


def _ok(detail=""):
    return Verdict(True, detail)


def _no(detail):
    return Verdict(False, detail)


def _tokens(text):
    out = set()
    for w in str(text).split():
        w = w.strip(".,;:!?()[]'\"").lower()
        if w.endswith("'s"):
            w = w[:-2]
        if w:
            out.add(w)
    return out


def _has_token(text_or_tokens, needle):
    toks = text_or_tokens if isinstance(text_or_tokens, set) \
        else _tokens(text_or_tokens)
    return needle.lower() in toks


def _desc_matches(description, tokens):
    """At least half of the sought tokens appear in the description."""
    if not tokens:
        return True
    d = _tokens(description)
    hits = sum(1 for t in tokens if t.lower() in d)
    return hits * 2 >= len(tokens)


class Trajectory:
    """Window-aware wrapper over a Trace (entries with events + snapshots)."""

    def __init__(self, trace):
        self.entries = trace.entries
        self._evented = [e for e in self.entries
                         if e.get("event") and "t" in e["event"]]

    def snap_end(self, window):
        """Snapshot at end-of-window (last event with t <= hi), or terminal."""
        if window is None:
            return self.entries[-1].get("snapshot") or {}
        lo, hi = window
        best = None
        for e in self._evented:
            if e["event"]["t"] <= hi:
                best = e
        if best is None:
            best = self._evented[0] if self._evented else self.entries[-1]
        return best.get("snapshot") or {}

    def snap_before(self, t):
        """Snapshot just before the first event with this t (or empty)."""
        prev = None
        for e in self._evented:
            if e["event"]["t"] >= t:
                break
            prev = e
        return (prev or {}).get("snapshot") or {}

    def in_window(self, window):
        if window is None:
            return list(self._evented)
        lo, hi = window
        return [e for e in self._evented if lo <= e["event"]["t"] <= hi]

    def ops_in(self, window, names=None, status="applied"):
        out = []
        for e in self.in_window(window):
            for o in e.get("ops", []):
                if o.get("status") != status:
                    continue
                if names and o["op"].get("op") not in names:
                    continue
                out.append((e["event"]["t"], o))
        return out

    def notes_in(self, window, category):
        out = []
        for e in self.in_window(window):
            for n in e.get("notes", []):
                if n.get("note") == category:
                    out.append((e["event"]["t"], n))
        return out


# ---------------------------------------------------------------------------
# 1
def gap_record(traj, pool, scenario, *, window, min_gap_days=0,
               background_landings=None, briefing=False,
               check_touched_last_sitting=False):
    """An attention gap is honestly recorded: absence logged, background
    landings in sediment, re-entry briefing present, sittings indexed by
    touched-set."""
    snap = traj.snap_end(window)
    absences = snap.get("absences") or []
    gap = max((a.get("days", 0) for a in absences), default=0)
    if min_gap_days and gap < min_gap_days:
        return _no(f"no recorded absence >= {min_gap_days}d (max {gap})")
    if background_landings is not None:
        bg = [f for f in snap.get("findings", {}).values() if f.get("background")]
        if len(bg) < background_landings:
            return _no(f"only {len(bg)} background landings "
                       f"(need {background_landings})")
        for f in bg:
            strata = {c["stratum"] for c in f.get("citations", [])}
            if "sediment" not in strata:
                return _no(f"background {f['id']} not in sediment")
    if briefing:
        gap_end = max((a.get("ended_day", 0) for a in absences), default=0)
        briefs = [b for b in (snap.get("briefings") or [])
                  if b.get("day", -1) >= gap_end]
        if not briefs:
            return _no("no re-entry briefing recorded at/after the gap end")
    if check_touched_last_sitting:
        sittings = list(snap.get("sittings", {}).values())
        if sittings:
            last = max(sittings, key=lambda s: s.get("opened_event", 0))
            tags = set()
            for fid in last.get("findings", []):
                tags |= set(pool.finding(fid).questions)
            if tags and not tags <= set(last.get("touched", [])):
                return _no(f"sitting {last['id']} touched-set misses "
                           f"{sorted(tags - set(last.get('touched', [])))}")
    return _ok(f"gap={gap}d")


# 2
def proposal_state(traj, pool, scenario, *, window, desc_tokens=(),
                   kind=None, cls=None, status="pending", max_active_age=None,
                   accepted_at_t=None, applied=None, created_in_window=False,
                   any_of_desc=None, require_ratified_prose_provenance=None,
                   requires_provisional_dep=None):
    """A proposal matched by description tokens/kind is in the given lifecycle
    state (pending/accepted/expired/...), optionally unexpired (active_age
    bound), accepted at a specific t, applied, or backing ratified prose."""
    snap = traj.snap_end(window)
    props = list(snap.get("proposals", {}).values())
    cands = []
    for p in props:
        if kind and p.get("kind") != kind:
            continue
        if cls and p.get("cls") != cls:
            continue
        if any_of_desc:
            if not any(_desc_matches(p.get("description", ""), toks)
                       for toks in any_of_desc):
                continue
        elif desc_tokens and not _desc_matches(p.get("description", ""), desc_tokens):
            continue
        cands.append(p)
    if not cands:
        return _no(f"no proposal matching kind={kind} tokens={list(desc_tokens)} "
                   f"among {len(props)}")
    lo_t = window[0] if window else None
    for p in sorted(cands, key=lambda p: p["id"]):
        if p.get("status") != status:
            continue
        if max_active_age is not None and p.get("active_age", 0) > max_active_age:
            continue
        if applied is not None and bool(p.get("applied")) != applied:
            continue
        if created_in_window and window and not (
                any(n.get("proposal") == p["id"]
                    for _, n in traj.notes_in(window, "proposal_opened"))):
            continue
        if accepted_at_t is not None:
            acc = [t for t, n in traj.notes_in(None, "proposal_accepted")
                   if n.get("proposal") == p["id"]]
            if accepted_at_t not in acc:
                continue
        if require_ratified_prose_provenance:
            need = set(require_ratified_prose_provenance)
            hit = any(pr.get("ratified")
                      and need <= set(pr.get("provenance", []))
                      for pr in snap.get("prose", {}).values())
            if not hit:
                continue
        if requires_provisional_dep:
            dep = snap.get("findings", {}).get(requires_provisional_dep, {})
            if not dep.get("provisional_open"):
                continue
        return _ok(f"{p['id']} {p.get('status')} age={p.get('active_age')}")
    return _no(f"matched {len(cands)} proposal(s) but none satisfies "
               f"status={status}/age<={max_active_age}/applied={applied}"
               f"/accepted_at={accepted_at_t}")


# 3
def ops_bounded(traj, pool, scenario, *, window, forbid_kinds=(),
                max_cls=None, structure_frozen=False,
                forbid_new_addendum_except=None, no_revise_ratified=False):
    """No ops of the forbidden kinds / above the class ceiling are applied in
    the window (structure-frozen gaps, nothing-above-Class-1 absences,
    growth-at-Class-2 pre-consent)."""
    forbid = set(forbid_kinds)
    if structure_frozen:
        forbid |= set(STRUCTURAL_OPS)
    for t, o in traj.ops_in(window):
        name = o["op"].get("op")
        if name in forbid:
            return _no(f"forbidden op {name} applied at t={t}")
        if structure_frozen and name == "create_section" \
                and o["op"].get("section_kind") == "section":
            return _no(f"full section created at t={t} in frozen window")
        if max_cls is not None:
            eff = o.get("effective_cls", o["op"].get("cls", "0"))
            if CLASS_ORDER.get(eff, 0) > CLASS_ORDER[max_cls]:
                return _no(f"op {name} at t={t} effective class {eff} "
                           f"> {max_cls}")
        if no_revise_ratified and name == "revise_prose":
            return _no(f"revise_prose applied at t={t} (ratified guard)")
    if forbid_new_addendum_except is not None:
        allowed = set(forbid_new_addendum_except)
        for t, n in traj.notes_in(window, "proposal_opened"):
            snap = traj.snap_end(window)
            pr = snap.get("proposals", {}).get(n.get("proposal"), {})
            if pr.get("kind") == "addendum":
                prov = _tokens(pr.get("description", ""))
                if not (allowed & prov):
                    return _no(f"fresh addendum proposal {n.get('proposal')} "
                               f"at t={t} not among allowed {sorted(allowed)}")
    return _ok("no forbidden ops in window")


# 4
def plan_item_state(traj, pool, scenario, *, window, kinds=(), target=None,
                    targets_any=(), owner=None, text_tokens=(), min_state=None,
                    max_state=None, distinct_count=None, provisional_target=None,
                    provisional_cleared=None, discharged=None,
                    same_discharge_row=False, not_dangling=False,
                    forbid_prose_from=(), max_prose_maturity="conjecture"):
    """Typed plan/scrutiny items matching (kind, target, owner, text) reach the
    required lifecycle state in-window; provisional marks behave per §6
    (set while open, cleared only by an accepted naming row)."""
    snap = traj.snap_end(window)
    items = []
    for it in snap.get("plan_items", {}).values():
        if kinds and it.get("kind") not in kinds:
            continue
        if target and it.get("target") != target:
            continue
        if targets_any and it.get("target") not in targets_any:
            continue
        if owner and it.get("owner") != owner:
            continue
        if text_tokens and not _desc_matches(it.get("text", ""), text_tokens):
            continue
        items.append(it)
    if not items:
        return _no(f"no plan item matching kinds={list(kinds)} target={target} "
                   f"owner={owner} tokens={list(text_tokens)}")
    if distinct_count is not None and len(items) < distinct_count:
        return _no(f"only {len(items)} matching items (need {distinct_count})")
    for it in items:
        st = it.get("state", "proposed")
        if min_state and PLAN_ORDER.get(st, -1) < PLAN_ORDER[min_state]:
            return _no(f"{it['id']} state={st} < {min_state}")
        if max_state and PLAN_ORDER.get(st, 9) > PLAN_ORDER[max_state]:
            return _no(f"{it['id']} state={st} > {max_state}")
        if discharged is not None and bool(it.get("discharged_by")) != discharged:
            return _no(f"{it['id']} discharged_by={it.get('discharged_by')}")
        if not_dangling and it.get("state") in ("proposed", "planned") \
                and not it.get("discharged_by"):
            return _no(f"{it['id']} left dangling at {it.get('state')}")
    if same_discharge_row and len(items) >= 2:
        rows = {it.get("discharged_by") for it in items}
        if len(rows) != 1 or None in rows:
            return _no(f"items discharged by different/no rows: {sorted(map(str, rows))}")
    tgt = target or (items[0].get("target") if items else None)
    if provisional_target is not None and tgt:
        f = snap.get("findings", {}).get(tgt, {})
        has = bool(f.get("provisional_open"))
        if has != provisional_target:
            return _no(f"finding {tgt} provisional_open={f.get('provisional_open')}")
    if provisional_cleared is not None and tgt:
        f = snap.get("findings", {}).get(tgt, {})
        if bool(f.get("provisional_open")) == provisional_cleared:
            return _no(f"finding {tgt} provisional_open={f.get('provisional_open')} "
                       f"(cleared={provisional_cleared} expected)")
    for fid in forbid_prose_from:
        for pr in snap.get("prose", {}).values():
            if fid in pr.get("provenance", []) and \
                    MATURITY_ORDER.get(pr.get("maturity"), 0) > \
                    MATURITY_ORDER[max_prose_maturity]:
                return _no(f"prose {pr['id']} from {fid} at {pr.get('maturity')} "
                           f"exceeds {max_prose_maturity}")
    return _ok(f"{len(items)} item(s) ok: "
               + ", ".join(f"{i['id']}={i.get('state')}" for i in items[:4]))


# 5
def sitting_index(traj, pool, scenario, *, window, mode,
                  leftovers_min=(), require_hold_evaporated=None, at=None):
    """Sitting/episode hygiene: same-day same-anchor micro-bursts coalesce;
    empty sittings file silently; mid-sitting distill keeps the sitting open;
    the leftovers shelf catches unrouted artifacts; touched-set indexing."""
    snap = traj.snap_end(window)
    sittings = [s for s in snap.get("sittings", {}).values()]
    if mode == "coalesce":
        opened = [n for _, n in traj.notes_in(window, "sitting_opened")]
        sids = [n["sitting"] for n in opened]
        eps = {snap.get("sittings", {}).get(s, {}).get("episode_id") for s in sids}
        if len(sids) >= 2 and len(eps) == 1:
            return _ok(f"sittings {sids} share episode {eps.pop()}")
        return _no(f"sittings {sids} in episodes {sorted(map(str, eps))}")
    if mode == "silent_filed":
        for s in sittings:
            opened_in = window is None or (
                any(n.get("sitting") == s["id"]
                    for _, n in traj.notes_in(window, "sitting_opened")))
            if opened_in and not s.get("findings") and s.get("silent_filed"):
                return _ok(f"{s['id']} silently filed")
        return _no("no empty sitting silently filed in window")
    if mode == "mid_distill":
        for t, n in traj.notes_in((at, at) if at else window, "distill_settled"):
            sid = n.get("sitting")
            after = traj.snap_end((t, t))
            s = after.get("sittings", {}).get(sid, {})
            if s.get("state") == "open":
                return _ok(f"distill at t={t} settled {n.get('rows_settled')} "
                           f"with sitting {sid} still open")
        return _no("no mid-sitting distill (sitting closed or no distill)")
    if mode == "leftovers":
        last = max(sittings, key=lambda s: len(s.get("findings", [])), default=None)
        if last is None:
            return _no("no sittings")
        left = set(last.get("leftovers", []))
        missing = [f for f in leftovers_min if f not in left]
        if missing:
            return _no(f"leftovers shelf misses {missing} (has {sorted(left)})")
        if require_hold_evaporated:
            if snap.get("holds"):
                return _no(f"holds not evaporated: {snap['holds']}")
        return _ok(f"leftovers={sorted(left)}")
    if mode == "touched":
        for s in sittings:
            tags = set()
            for fid in s.get("findings", []):
                tags |= set(pool.finding(fid).questions)
            if not tags <= set(s.get("touched", [])):
                return _no(f"{s['id']} touched misses "
                           f"{sorted(tags - set(s.get('touched', [])))}")
        return _ok(f"{len(sittings)} sittings indexed by touched-set")
    return _no(f"unknown mode {mode}")


# 6
def salience_state(traj, pool, scenario, *, window, specs):
    """Salience semantics: pinned is filed and can never fade; faded is
    findable but not carried into story; held excerpts evaporate at sitting
    end without entering the record."""
    snap = traj.snap_end(window)
    for fid, expect in specs:
        f = snap.get("findings", {}).get(fid)
        if f is None:
            return _no(f"{fid} not landed")
        if expect == "pinned":
            if not f.get("pinned"):
                return _no(f"{fid} not pinned")
            if f.get("faded"):
                return _no(f"{fid} pinned but faded")
        elif expect == "faded_findable":
            if not f.get("faded"):
                return _no(f"{fid} not faded")
            story = [c for c in f.get("citations", [])
                     if c.get("stratum") == "story" and not c.get("revised")]
            if story:
                return _no(f"{fid} faded but carried in story: {story}")
        elif expect == "hold_evaporated":
            if fid in (snap.get("holds") or []):
                return _no(f"{fid} still held")
        else:
            return _no(f"unknown salience expectation {expect}")
    return _ok("; ".join(f"{f}:{e}" for f, e in specs))


# 7
def cited_under_questions(traj, pool, scenario, *, window, findings=None,
                          questions=None, forbidden=(), all_tagged=False,
                          question_map=None):
    """Multi-question findings are cited under all their tagged questions as
    one entity, no copies (deduped section.cited), and never under forbidden
    questions.  question_map routes specific findings to specific questions."""
    snap = traj.snap_end(window)
    sections = snap.get("sections", {}).values()
    by_q = {}
    for s in sections:
        q = s.get("question_id")
        by_q.setdefault(q, set()).update(s.get("cited", []))
    # a pending routing row IS the visible destination proposal: citations
    # settle only at distill, so pre-distill windows read rows (C2 fix)
    for r in snap.get("routing_rows", {}).values():
        if r.get("stratum") == "story":
            for q in r.get("questions", []):
                by_q.setdefault(q, set()).add(r.get("product"))
    checks = []
    if question_map:
        checks = [(fid, qs) for fid, qs in question_map.items()]
    elif all_tagged:
        checks = [(f.id, f.questions) for f in pool.findings
                  if len(f.questions) >= 2
                  and f.id in snap.get("findings", {})]
    elif findings:
        checks = [(fid, questions or pool.finding(fid).questions)
                  for fid in findings]
    for fid, qs in checks:
        for q in qs:
            if fid not in by_q.get(q, set()):
                return _no(f"{fid} not cited under {q} "
                           f"(cited under {sorted(q2 for q2, v in by_q.items() if fid in v)})")
        for q in forbidden:
            if fid in by_q.get(q, set()):
                return _no(f"{fid} cited under forbidden {q}")
    # "no copies": section.cited is deduped by construction; verify no
    # duplicate prose provenance per (finding, question)
    for fid, qs in checks:
        for q in qs:
            secs = [s["id"] for s in sections if s.get("question_id") == q]
            blocks = [p for p in snap.get("prose", {}).values()
                      if p.get("section_id") in secs
                      and fid in p.get("provenance", [])
                      and not p.get("superseded_by")]
            if len(blocks) > 1:
                return _no(f"{fid} appears in {len(blocks)} live prose blocks "
                           f"under {q} (copies)")
    return _ok(f"{len(checks)} finding(s) correctly multi-cited")


# 8
def routing_destination(traj, pool, scenario, *, window, spec):
    """Findings route to the required strata and never into forbidden question
    narratives (tangentials to notes/sediment; salt away from the causal
    section); a row is the visible receipt for cross-anchor placements."""
    snap = traj.snap_end(window)
    for fid, rule in spec.items():
        f = snap.get("findings", {}).get(fid)
        if f is None:
            return _no(f"{fid} not landed")
        cits = [c for c in f.get("citations", []) if not c.get("revised")]
        strata = {c["stratum"] for c in cits}
        strata |= {r.get("stratum") for r in snap.get("routing_rows", {}).values()
                   if r.get("product") == fid and r.get("status") == "pending"}
        allowed = set(rule.get("allowed", ("story", "notes", "sediment")))
        extra = strata - allowed
        if extra:
            return _no(f"{fid} found in {sorted(extra)} (allowed {sorted(allowed)})")
        rows = [r for r in snap.get("routing_rows", {}).values()
                if r.get("product") == fid]
        for q in rule.get("forbid_questions", ()):
            if any(c.get("question") == q and c["stratum"] == "story"
                   for c in cits):
                return _no(f"{fid} cited in {q}'s story")
            if any(r.get("stratum") == "story" and q in r.get("questions", [])
                   and r.get("status") == "pending" for r in rows):
                return _no(f"{fid} has a pending story row under {q} - the "
                           f"row is the destination proposal")
        if rule.get("require_row_question"):
            q = rule["require_row_question"]
            rows = snap.get("routing_rows", {}).values()
            if not any(r.get("product") == fid and q in r.get("questions", [])
                       for r in rows):
                return _no(f"no visible routing row placing {fid} under {q}")
        if rule.get("faded") and not f.get("faded"):
            return _no(f"{fid} expected faded")
        if rule.get("pinned") and not f.get("pinned"):
            return _no(f"{fid} expected pinned")
        if rule.get("row_exists"):
            rows = snap.get("routing_rows", {}).values()
            if not any(r.get("product") == fid for r in rows):
                return _no(f"no routing row for {fid} (destination never proposed)")
    return _ok(f"{len(spec)} finding(s) routed within bounds")


# 9
def overturn_handling(traj, pool, scenario, *, window, pairs, mode,
                      cascade_reexam_target=None, post_still_cited=(),
                      require_withdrawn_draft_target=None):
    """An overturn is handled at the right severity: 'absorb' = Class-1/2
    supersession (marked superseded, citations revised, no addendum needed,
    still findable); 'interrupt' = Class-X addendum proposal against the
    committed/ratified prose, target contested/revised, never rewritten;
    'differential' = the two severities are handled differently."""
    snap = traj.snap_end(window)

    def absorbed(overturned, by):
        f = snap.get("findings", {}).get(overturned)
        if f is None:
            return f"{overturned} not landed"
        marked = f.get("superseded_by") == by
        # a merely-pending proposal counts ONLY where ratified prose is
        # implicated (the legal path necessarily pends awaiting consent);
        # otherwise absorption means the mark actually applied (C2 fix)
        ratified_implicated = any(
            (pr.get("ratified") or pr.get("authored"))
            and overturned in pr.get("provenance", [])
            for pr in snap.get("prose", {}).values())
        pending = ratified_implicated and any(
            p.get("status") == "pending"
            and _has_token(p.get("description", ""), overturned)
            for p in snap.get("proposals", {}).values())
        if not (marked or pending):
            return f"{overturned} neither marked superseded by {by} nor pending"
        # findable: still present with citations
        if not f.get("citations"):
            return f"{overturned} has no citations (deleted?)"
        return None

    def interrupted(overturned, by):
        props = [p for p in snap.get("proposals", {}).values()
                 if p.get("kind") == "addendum"
                 and (_has_token(p.get("description", ""), by)
                      or _has_token(p.get("description", ""), overturned))]
        if not props:
            return (f"no Class-X addendum proposal referencing "
                    f"{overturned}/{by}")
        if not any(p.get("cls") == "X" for p in props):
            return f"addendum proposal for {overturned} not Class X"
        # target prose contested or revised, never rewritten
        cited = [p for p in snap.get("prose", {}).values()
                 if overturned in p.get("provenance", [])
                 and (p.get("ratified") or p.get("authored"))]
        if cited and not any(p.get("contested") or p.get("addenda")
                             for p in cited):
            return (f"ratified prose citing {overturned} neither contested "
                    f"nor carrying addenda")
        return None

    if mode == "absorb":
        for overturned, by in pairs:
            err = absorbed(overturned, by)
            if err:
                return _no(err)
    elif mode == "interrupt":
        for overturned, by in pairs:
            err = interrupted(overturned, by)
            if err:
                return _no(err)
    elif mode == "differential":
        (a_over, a_by), (i_over, i_by) = pairs
        err = absorbed(a_over, a_by)
        if err:
            return _no(f"absorb half: {err}")
        # absorb must NOT have used the interrupt machinery
        if any(p.get("kind") == "addendum"
               and _has_token(p.get("description", ""), a_over)
               for p in snap.get("proposals", {}).values()):
            return _no(f"{a_over} absorbed via addendum (severity conflated)")
        err = interrupted(i_over, i_by)
        if err:
            return _no(f"interrupt half: {err}")
    else:
        return _no(f"unknown mode {mode}")
    if cascade_reexam_target:
        # the re-exam must be TRIGGERED by the overturn: created in-window,
        # not a pre-existing gesture item on the same target
        idx_to_t = {e["event"]["index"]: e["event"]["t"]
                    for e in traj.in_window(None)}
        lo = window[0] if window else None
        hi = window[1] if window else None

        def _in_win(created_event):
            t = idx_to_t.get(created_event)
            return t is not None and (lo is None or lo <= t <= hi)

        items = [i for i in snap.get("plan_items", {}).values()
                 if i.get("target") == cascade_reexam_target
                 and _in_win(i.get("created_event", -1))]
        rows = [r for r in snap.get("routing_rows", {}).values()
                if _has_token(r.get("note", ""), cascade_reexam_target)
                and _in_win(r.get("created_event", -1))]
        if not items and not rows:
            return _no(f"no overturn-triggered re-examination of dependent "
                       f"{cascade_reexam_target} in window")
    for fid in post_still_cited:
        f = snap.get("findings", {}).get(fid, {})
        if not any(c["stratum"] == "story" and not c.get("revised")
                   for c in f.get("citations", [])):
            return _no(f"post-overturn {fid} not cited in the revised story")
    if require_withdrawn_draft_target:
        t = require_withdrawn_draft_target
        drafts = [p for p in snap.get("proposals", {}).values()
                  if p.get("kind") == "claim_draft"
                  and _has_token(p.get("description", ""), t)]
        if drafts and not any(p.get("status") in ("withdrawn", "dismissed")
                              or p.get("withdrawn") for p in drafts):
            return _no(f"claim draft on {t} neither withdrawn nor dismissed")
    return _ok(f"{mode} handled for {pairs}")


# 10
def face_value_retired(traj, pool, scenario, *, window, findings):
    """No live prose still asserts the superseded findings' face-value
    readings: every live story block citing them is revised, superseded,
    contested, or carries an addendum — while the findings stay findable."""
    snap = traj.snap_end(window)
    for fid in findings:
        f = snap.get("findings", {}).get(fid)
        if f is None:
            return _no(f"{fid} not landed")
        if not f.get("citations"):
            return _no(f"{fid} unfindable (no citations)")
        for p in snap.get("prose", {}).values():
            if fid not in p.get("provenance", []) or p.get("superseded_by"):
                continue
            if not (p.get("contested") or p.get("addenda")
                    or p.get("revision_of")):
                # unrevised live prose asserting the superseded reading
                return _no(f"prose {p['id']} still cites {fid} at face value")
    return _ok(f"{list(findings)} retired but findable")


# 11
def section_promotion(traj, pool, scenario, *, window, question,
                      exactly_once=True, none_before=True, consent="accepted",
                      consent_by_t=None):
    """The direction's promotion from stub to full section is proposed within
    the window (exactly once, none before) and carries the required consent:
    'accepted' | 'pending_ok' (in-stream ratification impossible — flagged
    corpus reading)."""
    promos = []
    for t, o in traj.ops_in(None, names=("promote_section", "create_section")):
        op = o["op"]
        if op.get("op") == "create_section" and op.get("section_kind") != "section":
            continue
        target = op.get("section", "") or op.get("question_id", "")
        if question in str(target):
            promos.append((t, o))
    props = [
        (t, n) for t, n in traj.notes_in(None, "proposal_opened")
        if question.lower() in str(n).lower() or "promot" in str(n).lower()
    ]
    lo, hi = window
    in_w = [t for t, _ in promos if lo <= t <= hi]
    before = [t for t, _ in promos if t < lo]
    prop_in_w = [t for t, _ in props if lo <= t <= hi]
    if none_before and before:
        return _no(f"promotion applied before window at t={before}")
    if not in_w and not prop_in_w:
        return _no(f"no promotion (op or proposal) for {question} in "
                   f"t={lo}..{hi}; ops at {[t for t, _ in promos]}")
    if exactly_once and len(in_w) > 1:
        return _no(f"promotion applied {len(in_w)} times in window")
    if consent == "accepted" and in_w:
        _, o = next(p for p in promos if p[0] == in_w[0])
        if o.get("consent") is None and o.get("effective_cls", "0") in ("2", "3", "X"):
            return _no("promotion applied without consent")
    return _ok(f"promotion at t={in_w or prop_in_w} consent={consent}")


# 12
def section_state(traj, pool, scenario, *, window, question=None,
                  title_tokens=(), status=None, rank=None, min_cited=None,
                  cites_any=(), cites_all=(), no_child_section=False,
                  has_child_section=False, created_after_t=None,
                  require_draft_pending_target=None, intent=None,
                  outline_bounds=False, max_depth=3, min_findings=2,
                  nothing_deleted=False, any_appendix=False):
    """Sections match the expected face at end-of-window: status/rank/kind,
    citation contents, creation time, child structure — or (outline_bounds
    mode) the global outline invariants: depth <= 3, no rendered section
    under 2 findings except intent/floor-converted stubs."""
    snap = traj.snap_end(window)
    sections = list(snap.get("sections", {}).values())
    if outline_bounds:
        for s in sections:
            depth, cur, seen = 1, s, set()
            while cur.get("parent_id") and cur["id"] not in seen:
                seen.add(cur["id"])
                cur = snap["sections"].get(cur["parent_id"], {})
                depth += 1
            if depth > max_depth:
                return _no(f"section {s['id']} at depth {depth} > {max_depth}")
            if s.get("kind") == "section" and not s.get("intent") \
                    and s.get("status") == "live" \
                    and len(s.get("cited", [])) < min_findings:
                return _no(f"rendered section {s['id']} holds "
                           f"{len(s.get('cited', []))} findings")
        return _ok(f"outline bounded over {len(sections)} sections")
    cands = []
    for s in sections:
        if question and s.get("question_id") != question:
            continue
        if title_tokens and not _desc_matches(s.get("title", ""), title_tokens):
            continue
        cands.append(s)
    if not cands:
        return _no(f"no section for question={question} tokens={list(title_tokens)}")
    agg_cited = set()
    for s in cands:
        agg_cited |= set(s.get("cited", []))
    best = max(cands, key=lambda s: len(s.get("cited", [])))
    if status and not any(s.get("status") == status for s in cands):
        return _no(f"no section with status={status} "
                   f"(have {[s.get('status') for s in cands]})")
    if rank and not any(s.get("rank") == rank for s in cands):
        return _no(f"no section with rank={rank}")
    if intent is not None and not any(bool(s.get("intent")) == intent
                                      for s in cands):
        return _no(f"no section with intent={intent}")
    if min_cited is not None and len(agg_cited) < min_cited:
        return _no(f"cited {len(agg_cited)} findings (< {min_cited})")
    if cites_any and not (agg_cited & set(cites_any)):
        return _no(f"cites none of {list(cites_any)}")
    if cites_all and not set(cites_all) <= agg_cited:
        return _no(f"missing citations {sorted(set(cites_all) - agg_cited)}")
    kids = [s for s in sections
            if s.get("parent_id") in {c["id"] for c in cands}
            and s.get("kind") == "section"]
    child_secs = kids + [s for s in cands
                         if s.get("kind") == "section"]
    if no_child_section and child_secs:
        return _no(f"full section exists: {[s['id'] for s in child_secs]}")
    if has_child_section and not child_secs:
        return _no("no full (child) section exists")
    if created_after_t is not None:
        created = [s for s in cands if s.get("kind") != "question-root"]
        if created and not any(True for s in created):
            pass
        opened = [t for t, n in traj.notes_in(None, "sitting_opened")]
        for s in created:
            ev = s.get("created_event", -1)
            evs = [e["event"]["t"] for e in traj.in_window(None)
                   if e["event"]["index"] == ev]
            if evs and evs[0] <= created_after_t:
                return _no(f"section {s['id']} created at t={evs[0]} "
                           f"(before {created_after_t})")
    if require_draft_pending_target:
        t = require_draft_pending_target
        ok = any(p.get("kind") == "claim_draft" and p.get("status") == "pending"
                 and _has_token(p.get("description", ""), t)
                 for p in snap.get("proposals", {}).values())
        if not ok:
            return _no(f"no pending claim draft on {t}")
    if any_appendix and not any(s.get("rank") == "appendix" for s in sections):
        return _no("no appendix-rank section anywhere")
    if nothing_deleted:
        for f in snap.get("findings", {}).values():
            if not f.get("citations"):
                return _no(f"finding {f['id']} lost all citations")
    return _ok(f"{best['id']} status={best.get('status')} "
               f"rank={best.get('rank')} cited={len(agg_cited)}")


# 13
def stub_is_plan(traj, pool, scenario, *, window, question, intent=True,
                 no_prose=True, min_items=1, title_tokens=(),
                 require_hold_evaporated=None):
    """An intent-born stub's content IS the plan: intent-marked stub exists
    for the question, carries plan items, and no prose pretends evidence
    (RECORD_DESIGN §14.5)."""
    snap = traj.snap_end(window)
    stubs = [s for s in snap.get("sections", {}).values()
             if s.get("question_id") == question
             and (not intent or s.get("intent"))
             and (not title_tokens or _desc_matches(s.get("title", ""), title_tokens))]
    if not stubs:
        return _no(f"no intent stub for {question}")
    items = [i for i in snap.get("plan_items", {}).values()
             if i.get("owner") == question]
    if len(items) < min_items:
        return _no(f"{len(items)} plan items under {question} (< {min_items})")
    if no_prose:
        # question-root prose counts too: evidence prose under the root is
        # still prose pretending evidence for the direction (C2 fix)
        sids = {s["id"] for s in snap.get("sections", {}).values()
                if s.get("question_id") == question}
        blocks = [p for p in snap.get("prose", {}).values()
                  if p.get("section_id") in sids]
        if blocks:
            return _no(f"stub carries prose {[p['id'] for p in blocks]}")
    if require_hold_evaporated and require_hold_evaporated in (snap.get("holds") or []):
        return _no(f"{require_hold_evaporated} still held")
    return _ok(f"stub {stubs[0]['id']} with {len(items)} plan items")


# 14
def tray_state(traj, pool, scenario, *, window, non_empty=None,
               max_undifferentiated=None, min_routine=None,
               rows_for=(), compare_around_t=None):
    """The needs-you tray (reconstructed: pending routing rows + pending
    proposals) stays bounded and typed — routine batchable, decisions
    one-by-one.  Peak checks (non_empty/min_routine) scan every in-window
    snapshot, because distills settle rows: the post-settle end snapshot is
    empty by construction (C2 fix).  The typing partition is unconditional.
    rows_for requires a visible destination row (any status) per finding."""
    snap = traj.snap_end(window)

    def tray_of(s):
        rows = [r for r in s.get("routing_rows", {}).values()
                if r.get("status") == "pending"]
        props = [p for p in s.get("proposals", {}).values()
                 if p.get("status") == "pending"]
        routine = [r for r in rows if r.get("typed") == "routine"]
        decisions = [r for r in rows if r.get("typed") == "decision"] + props
        return rows, props, routine, decisions

    snaps = [e.get("snapshot") or {} for e in traj.in_window(window)] or [snap]
    # typing partition must hold at EVERY snapshot
    for s in snaps:
        rows, props, routine, decisions = tray_of(s)
        if len(routine) + len(decisions) < len(rows) + len(props):
            return _no("tray rows lack routine/decision typing")
        if max_undifferentiated is not None and                 len(rows) + len(props) > max_undifferentiated and not routine:
            return _no(f"{len(rows) + len(props)} undifferentiated tray items")
    peak_total = max(len(t[0]) + len(t[1]) for t in map(tray_of, snaps))
    peak_routine = max(len(t[2]) for t in map(tray_of, snaps))
    if non_empty and peak_total == 0:
        return _no("tray never non-empty in window (inert)")
    if min_routine is not None and peak_routine < min_routine:
        return _no(f"peak routine rows {peak_routine} (< {min_routine})")
    if rows_for:
        all_rows = snap.get("routing_rows", {}).values()
        for fid in rows_for:
            if not any(r.get("product") == fid for r in all_rows):
                return _no(f"no visible destination row for {fid}")
    rows, props, routine, decisions = tray_of(snap)
    total = len(rows) + len(props)
    if compare_around_t is not None:
        # "immediately before and after": snapshot just before t vs at t,
        # not the terminal state (C2 fix)
        before = traj.snap_before(compare_around_t)
        at = traj.snap_end((compare_around_t, compare_around_t))
        _, _, b_routine, b_dec = tray_of(before)
        _, _, a_routine, a_dec = tray_of(at)
        before_n = len(b_routine) + len(b_dec)
        after_n = len(a_routine) + len(a_dec)
        idx_at = {e["event"]["t"]: e["event"]["index"]
                  for e in traj.in_window(None)}
        resolved = sum(
            1 for p in at.get("proposals", {}).values()
            if p.get("status") in ("accepted", "dismissed")
            and p.get("decided_event") == idx_at.get(compare_around_t))
        if abs(before_n - after_n) > resolved:
            return _no(f"needs-you count moved {before_n}->{after_n} across "
                       f"the restructuring beyond items it resolved "
                       f"({resolved})")
    return _ok(f"tray: {len(routine)} routine + {len(decisions)} decisions")


# 15
def narrative_growth_bounded(traj, pool, scenario, *, window, max_changes,
                             min_changes=0):
    """Narrative growth tracks insight, not output: the number of
    narrative-level changes (story prose written, sections promoted/created,
    addenda applied) in the window stays within bounds."""
    n = 0
    for t, o in traj.ops_in(window, names=("write_prose", "promote_section",
                                           "add_addendum")):
        n += 1
    for t, o in traj.ops_in(window, names=("create_section",)):
        if o["op"].get("section_kind") == "section":
            n += 1
    if n > max_changes:
        return _no(f"{n} narrative-level changes (> {max_changes})")
    if n < min_changes:
        return _no(f"{n} narrative-level changes (< {min_changes})")
    return _ok(f"{n} narrative-level changes in window")


# 16
def consent_conservation(traj, pool, scenario, *, window=None,
                         no_auto_ratify=True, addenda_via_proposals=False,
                         plan_still_open=(), hold_evaporated=None):
    """Consent arithmetic: every applied Class-2/3/X op rode an accepted
    proposal; decisions never auto-accept (only ratify events / routine
    distills); expiries lapse visibly; addenda arrive only as proposals."""
    for t, o in traj.ops_in(window):
        eff = o.get("effective_cls", "0")
        if CLASS_ORDER.get(eff, 0) >= CLASS_ORDER["2"] and o.get("consent") is None:
            return _no(f"op {o['op'].get('op')} class {eff} applied at t={t} "
                       f"without consent")
    if no_auto_ratify:
        ratify_ts = {e["event"]["t"] for e in traj.in_window(None)
                     if e["event"].get("type") in ("ratify", "distill")}
        for t, n in traj.notes_in(window, "proposal_accepted"):
            if t not in ratify_ts:
                return _no(f"proposal {n.get('proposal')} accepted at t={t} "
                           f"outside any ratify/distill event")
    if addenda_via_proposals:
        for t, o in traj.ops_in(window, names=("add_addendum",)):
            if o.get("consent") is None:
                return _no(f"addendum applied at t={t} without a proposal")
    snap = traj.snap_end(window)
    for kind, target in plan_still_open:
        items = [i for i in snap.get("plan_items", {}).values()
                 if i.get("kind") == kind and i.get("target") == target]
        if not items:
            return _no(f"no {kind} item on {target}")
        if any(PLAN_ORDER.get(i.get("state"), 0) >= PLAN_ORDER["produced"]
               for i in items):
            return _no(f"{kind} on {target} advanced without work")
    if hold_evaporated and hold_evaporated in (snap.get("holds") or []):
        return _no(f"{hold_evaporated} still held at end")
    return _ok("consent conserved")


# 17
def provenance_chain(traj, pool, scenario, *, window, findings,
                     question=None, ratified=True, via_section=False,
                     title_tokens=()):
    """A claim/section's evidence chain is walkable: the (ratified) prose or
    section reaches every listed finding in one step (provenance links or
    section citations)."""
    snap = traj.snap_end(window)
    need = set(findings)
    if via_section:
        secs = [s for s in snap.get("sections", {}).values()
                if (question is None or s.get("question_id") == question)
                and (not title_tokens or _desc_matches(s.get("title", ""),
                                                       title_tokens))]
        got = set()
        for s in secs:
            got |= set(s.get("cited", []))
            for p in snap.get("prose", {}).values():
                if p.get("section_id") == s["id"]:
                    got |= set(p.get("provenance", []))
                    for aid in p.get("addenda", []):
                        ad = snap.get("addenda", {}).get(aid, {})
                        got |= set(ad.get("provenance", []))
        if need <= got:
            return _ok(f"section chain reaches {sorted(need)}")
        return _no(f"section chain misses {sorted(need - got)}")
    for p in snap.get("prose", {}).values():
        if ratified and not p.get("ratified"):
            continue
        prov = set(p.get("provenance", []))
        for aid in p.get("addenda", []):
            ad = snap.get("addenda", {}).get(aid, {})
            prov |= set(ad.get("provenance", []))
        if need <= prov:
            return _ok(f"prose {p['id']} carries {sorted(need)}")
    return _no(f"no {'ratified ' if ratified else ''}prose carries all of "
               f"{sorted(need)}")


PREDICATES = {
    "gap_record": gap_record,
    "proposal_state": proposal_state,
    "ops_bounded": ops_bounded,
    "plan_item_state": plan_item_state,
    "sitting_index": sitting_index,
    "salience_state": salience_state,
    "cited_under_questions": cited_under_questions,
    "routing_destination": routing_destination,
    "overturn_handling": overturn_handling,
    "face_value_retired": face_value_retired,
    "section_promotion": section_promotion,
    "section_state": section_state,
    "stub_is_plan": stub_is_plan,
    "tray_state": tray_state,
    "narrative_growth_bounded": narrative_growth_bounded,
    "consent_conservation": consent_conservation,
    "provenance_chain": provenance_chain,
}
