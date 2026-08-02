"""Record drafting advisor — phase-3 slice (RECORD_DESIGN §13.2 item 7).

Deterministic v0 of the drafting-during-work role: after a turn, when a
thread holds claims but no narrative, propose drafting the story stub. The
proposal is a RECORD-WRITE kind — accepting it creates the narrative stub
entity (undoable), which the Record face then renders under the question.
The LLM drafting (S6 charter) later rides the same proposal kind; the
detector stays cheap and local.

Signature carries the claim count, so a dismissal stops re-nagging until
the world changes (another claim lands) — the proposals-store discipline.
"""
from __future__ import annotations

from core.graph.entities import find_entities
from core.graph.proposals_store import add_proposal

# This pack's claim ladder (claim.yaml metadata.confidence); positives in
# ladder order, then the terminal negatives — the draft reads strongest-first.
_LADDER = ("preliminary", "supported", "validated", "contested", "refuted")
_NEGATIVE = {"contested", "refuted"}


def _of_thread(entity_type: str, tid: str) -> list[dict]:
    return [e for e in find_entities(type=entity_type, include_archived=False,
                                     not_deleted=True)
            if (e.get("metadata") or {}).get("thread_id") == tid]


def _claims_of_thread(tid: str) -> list[dict]:
    """Everything playing the claim ROLE on this line: the direct
    claim-type rows (always), plus whatever the role registration adds —
    with the same one-hop reference resolution the face uses (a finding
    reaches its question through the results it stands on)."""
    rows = {e["id"]: e for e in _of_thread("claim", tid)}
    from core.record.world import _of_role, _question_ref
    for e in _of_role("claim"):
        if e["id"] not in rows and tid in _question_ref(e, {tid}):
            rows[e["id"]] = e
    return list(rows.values())


_PLATFORM_LIFECYCLE = {"active", "archived"}


def _conf(c: dict) -> str:
    v = ((c.get("metadata") or {}).get("confidence")
         or c.get("status") or "preliminary")
    # the platform lifecycle column is NOT a maturity — an entity without
    # a confidence starts at the ladder's floor, never "(active)"
    return "preliminary" if v in _PLATFORM_LIFECYCLE else v


def _stated(c: dict) -> str:
    """A claim's FULL assertion — packs truncate display titles, so the
    draft weaves metadata.statement when present (claim.yaml), title only
    as the fallback. A truncated sentence must never enter the story."""
    md = c.get("metadata") or {}
    return (md.get("statement") or md.get("text")
            or c.get("title") or "").rstrip(".")


def compose_draft(claims: list[dict]) -> str:
    """Weave the thread's claims into a readable story draft — mechanical
    v0 (titles at their maturity, strongest first, negatives set apart).
    The scientist ratifies by accepting; LLM drafting rides the same
    payload later."""
    def rung(c):
        try:
            return _LADDER.index(_conf(c))
        except ValueError:
            return 0
    pos = sorted((c for c in claims if _conf(c) not in _NEGATIVE),
                 key=rung, reverse=True)
    neg = [c for c in claims if _conf(c) in _NEGATIVE]
    parts = []
    if pos:
        lead, rest = pos[0], pos[1:]
        parts.append(f"{_stated(lead)} ({_conf(lead)}).")
        if rest:
            parts.append("Also in hand: " + "; ".join(
                f"{_stated(c)} ({_conf(c)})" for c in rest) + ".")
    if neg:
        parts.append("Set aside: " + "; ".join(
            f"{_stated(c)} ({_conf(c)})" for c in neg) + ".")
    return " ".join(parts)


# Distilled from the S6 charter (record-eval/runner/llm_policy.py): prose
# tracks evidence — never ahead of it; the scientist ratifies, never the model.
_DRAFT_CHARTER = (
    "You draft the story paragraph of a scientist's lab-notebook Record. "
    "Weave the claims below into ONE readable paragraph (2-6 sentences, "
    "scaling with the claims; always COMPLETE the final sentence). Rules: "
    "prose tracks evidence — state each finding AT its "
    "given maturity, naming it in parentheses, never stronger; order for "
    "reading (chronology or mechanism), but the strongest finding must be "
    "unmistakably the center; set contested/refuted material apart at the "
    "end; no headings, no lists. NEVER reproduce entity identifiers "
    "(dat_/fig_/thr_/res_ codes and the like) even when a claim's text "
    "contains them — refer to artifacts by NAME or description; the "
    "record reads as prose, not as a database. Include NOTHING the "
    "listed claims do not state: no analysis, confirmation, or success "
    "language, no mechanisms or consequences of your own — connect the "
    "claims plainly and stop. "
    "FIGURES: when a FIGURES list is provided (ID → title), place each "
    "figure where the prose discusses its finding by writing its marker "
    "[[figure:ID]] alone on its own line between sentences — a manuscript "
    "shows evidence at the point of mention, never dumped at the end. "
    "Refer to figures in prose by TITLE; the ID appears only inside the "
    "marker. Use only listed IDs. "
    "Reply with the paragraph (and markers) only.")


def _strip_markers(text: str) -> str:
    import re
    return re.sub(r"\[\[figure:[^\]]+\]\]", "", text)


def _gate_draft(text: str, claims: list[dict],
                figure_ids: tuple[str, ...] = ()) -> str:
    """Mechanical acceptance gate on model output (the S6 lesson: sanitize
    edges, never trust shape): a usable draft is plain prose that names at
    least one maturity, leaks no internal ids, and ENDS — a draft cut at
    the token cap reads as a truncated sentence on the face (measured
    live: two of three ratified narratives ended mid-clause).

    [[figure:ID]] markers are MARKUP, not prose — the renderer embeds the
    figure at that point. They are stripped before the id-leak check, and
    markers naming unknown ids are dropped whole (never trusted)."""
    import re
    text = (text or "").strip()
    if figure_ids:
        text = re.sub(
            r"\[\[figure:([^\]]+)\]\]",
            lambda m: m.group(0) if m.group(1) in figure_ids else "",
            text)
    else:
        text = _strip_markers(text)
    text = text.strip()
    if not text or text.startswith(("#", "-", "*")):
        return ""
    prose = _strip_markers(text)
    if not any(f"({_conf(c)})" in prose for c in claims):
        return ""
    if any(t in prose for t in ("thr_", "run_", "sit-", "prj_")):
        return ""
    if not prose.rstrip(")”\"' \n").endswith((".", "!", "?")):
        return ""
    return text


def _figures_of(claims: list[dict]) -> list[tuple[str, str]]:
    """(id, title) of the image-bearing evidence the claims stand on —
    the drafter weaves these inline at their point of mention. A container
    support (result) carries its image one hop down, exactly as the world's
    supports_index resolves it."""
    from core.graph.edges import edges_from
    from core.graph.entities import get_entity
    from core.record.world import _CARRY_RELS, _image_of
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for c in claims:
        for ed in edges_from(c["id"]):
            if ed["rel_type"] != "supports":
                continue
            sid = ed["target_id"]
            if sid in seen:
                continue
            seen.add(sid)
            e = get_entity(sid)
            if not e:
                continue
            img = _image_of(e)
            if img is None:
                for ed2 in edges_from(e["id"]):
                    if ed2["rel_type"] not in _CARRY_RELS:
                        continue
                    child = get_entity(ed2["target_id"])
                    if child and _image_of(child):
                        img = _image_of(child)
                        break
            if img:
                out.append((sid, e.get("title") or sid))
    return out


def llm_draft(claims: list[dict],
              figures: list[tuple[str, str]] | None = None) -> str:
    """LLM drafting behind the SAME proposal kind — flag-gated
    (RECORD_LLM_DRAFTS=1), empty on any failure so the deterministic
    composer always backstops it. With `figures`, the charter invites
    [[figure:ID]] markers at the point of mention — manuscript-style."""
    import os
    if os.environ.get("RECORD_LLM_DRAFTS") != "1":
        return ""
    try:
        from core.config import MODEL
        from core.llm import (_CC_MARKER_BLOCK, _wants_cc_marker,
                              sync_anthropic_client)
        rows = "\n".join(f"- {_stated(c)} ({_conf(c)})" for c in claims)
        if figures:
            rows += "\n\nFIGURES:\n" + "\n".join(
                f"- {fid} → {title}" for fid, title in figures)
        system = ([dict(_CC_MARKER_BLOCK),
                   {"type": "text", "text": _DRAFT_CHARTER}]
                  if _wants_cc_marker() else _DRAFT_CHARTER)
        fig_ids = tuple(fid for fid, _ in (figures or []))

        def _attempt(user: str) -> str:
            # headroom, not a target — the charter caps length at 2-6
            # sentences; a tight cap TRUNCATES (the gate refuses those)
            r = sync_anthropic_client().messages.create(
                model=MODEL, max_tokens=1024, system=system,
                messages=[{"role": "user", "content": user}])
            text = "\n".join(b.text for b in r.content
                             if getattr(b, "type", "") == "text")
            return _gate_draft(text, claims, fig_ids)

        out = _attempt(rows)
        if not out:
            # ONE corrective retry — measured failure mode: weaving figures
            # dilutes attention and the maturity parentheticals get dropped
            out = _attempt(
                rows + "\n\nREMINDER: state each finding AT its maturity, "
                "naming the maturity in parentheses exactly as listed, "
                "e.g. (preliminary) — and COMPLETE the final sentence.")
        return out
    except Exception:  # noqa: BLE001 — advisory path; silence is the failure mode
        return ""


def _heads(narratives: list[dict]) -> list[dict]:
    """Narratives not superseded by a revision (wasDerivedFrom points FROM
    the revision AT the superseded one)."""
    from core.graph.edges import edges_to
    ids = {n["id"] for n in narratives}
    superseded = set()
    for n in narratives:
        for ed in edges_to(n["id"]):
            if ed["rel_type"] == "wasDerivedFrom" and ed["source_id"] in ids:
                superseded.add(n["id"])
    return [n for n in narratives if n["id"] not in superseded]


def review_thread(tid: str):
    """Propose a story draft when the record is behind the claims — a first
    draft when no narrative stands, a REVISION when claims have landed
    since the head was drafted (`drafted_claims` staleness)."""
    claims = _claims_of_thread(tid)
    if len(claims) < 2:
        return None
    text = llm_draft(claims, _figures_of(claims)) or compose_draft(claims)
    payload = {"title": "What we know so far", "text": text,
               "cites": [c["id"] for c in claims],
               "drafted_claims": len(claims)}
    heads = _heads(_of_thread("narrative", tid))
    if not heads:
        return add_proposal(
            thread_id=tid, kind="record_draft", advisor="record_drafter",
            headline=(f"the record is behind the work — draft the story "
                      f"for this line ({len(claims)} claims, no narrative "
                      f"yet)"),
            signature=f"record_draft:{tid}:{len(claims)}",
            payload=payload)
    head = heads[-1]
    seen = (head.get("metadata") or {}).get("drafted_claims")
    # staleness is DRIFT, not growth: a superseded/retired claim leaves the
    # story citing a ghost just as surely as a new claim leaves it behind
    # (measured live: a cross-line supersede shrank the claim set and the
    # ratified prose kept asserting the dead version)
    if seen is not None and len(claims) != seen:
        payload["revises"] = head["id"]
        payload["title"] = head.get("title") or payload["title"]
        return add_proposal(
            thread_id=tid, kind="record_draft", advisor="record_drafter",
            headline=(f"the claim set has moved since this story was "
                      f"drafted ({len(claims)} now, {seen} then) — revise it"),
            signature=f"record_draft:{tid}:{len(claims)}",
            payload=payload)
    return None


# NOTE on question naming: the verbatim-first-message heading fixes itself
# — the pack's D1 detector (proposals/scheduler._detect_title_question)
# already refines guide-owned questions silently from the second assistant
# turn, and suggests (ephemeral, self-expiring) on user-owned ones. This
# advisor deliberately does NOT duplicate it; the face's heading clamp is
# the turn-one display defense.


def _unfiled_session_note(ctx: dict, tid: str) -> None:
    """Prose rules don't guarantee filing (measured live: an analysis turn
    left nothing behind twice, rule present both times) — so detect the
    state mechanically and ride the end-of-session suggestion channel:
    tool-heavy turn, runs on the line, zero products reachable from it."""
    if (ctx.get("total_tool_calls") or 0) < 2 or ctx.get("suggestion"):
        return
    try:
        # "analysis happened here": turn runs OR exec records — one long
        # turn with many execs is the COMMON unfiled shape (measured live:
        # a 7-exec first turn filed nothing and the runs-only gate never
        # armed, so the nudge never fired)
        from core.graph import exec_records
        from core.graph.runs_port import list_runs
        if (len(list_runs(thread_id=tid)) < 2
                and len(exec_records.list_by_thread(tid, limit=3)) < 2):
            return
        if _claims_of_thread(tid):
            return
        for t in ("result", "figure"):
            if _of_thread(t, tid):
                return
        ctx["suggestion"] = (
            "The record is behind this session's work: analyses ran on this "
            "line but nothing was filed. Keep the key figure and file the "
            "main conclusion as a result with a one-line interpretation.")
    except Exception:  # noqa: BLE001 — advisory; never break the turn
        pass


def _on_stop_record(ctx: dict) -> None:
    tid = ctx.get("thread_id")
    if not tid:
        return
    _unfiled_session_note(ctx, tid)      # synchronous: rides ctx.suggestion
    from core import projects
    projects.spawn(review_thread, tid)


from core.hooks.dispatcher import register as _register  # noqa: E402

_register("on_stop", _on_stop_record, priority=25)
