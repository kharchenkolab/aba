"""
Proactive proposals engine (Phase D).

The Guide and advisors *notice* things and propose an action — draft a claim
from converging results, refine a thread's question, file an open question,
wrap up a thread on return, flag an N+1 re-check. Every proposal is:

  - **attributed**   (an `advisor`),
  - **dismissible**  (status → dismissed),
  - **reversible**   (accept records `undo_data`; undo reverses it),
  - **delayed / non-spammy** (de-duplicated by `signature` across ALL statuses,
    so a dismissed idea doesn't re-nag until the world changes).

Triggers (see ui3_impl.md Phase D):
  - post-turn (from guide.py, background): title/question, convergence, subquestion
  - data-upload event: nplus1
  - thread-open event: return_wrap

D0 ships the seam with a FAKE-mode stub detector (one proposal of each kind) so
the plumbing + UI + accept/dismiss/undo are testable without the LLM. Real
detectors land in D1–D3.
"""
from __future__ import annotations

import hashlib
import json as _json
from typing import Optional

from config import FAKE_SESSION, API_KEY, MODEL
from core.graph._schema import gen_entity_id, WORKSPACE_ID
from core.graph.edges import add_edge
from core.graph.entities import get_entity, update_entity, create_entity, archive_entity, list_entities
from core.graph.messages import get_messages
from core.graph.proposals_store import add_proposal, get_proposal, update_proposal, proposal_signature_exists, list_proposals

KINDS = ("title", "question", "convergence", "subquestion", "return_wrap", "nplus1")


def _sig(*parts: str) -> str:
    return hashlib.sha1("".join(p or "" for p in parts).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Evaluation (detectors)
# --------------------------------------------------------------------------

def evaluate_thread(thread_id: Optional[str], trigger: str) -> list[int]:
    """Run the detectors appropriate for `trigger` against the thread, inserting
    pending proposals (deduped by signature). Returns the new proposal ids.
    Best-effort: never raises into the caller (it runs in a background hook)."""
    if not thread_id:
        return []
    detectors = {
        "post_turn": [_detect_title_question, _detect_convergence],
        # D3: thread_open -> return_wrap; data_upload -> nplus1.
    }.get(trigger, [])
    out: list[int] = []
    for det in detectors:
        try:   # each detector is best-effort: a transient model failure in one
               # must not suppress the others (this runs in a background hook).
            out += det(thread_id)
        except Exception as e:
            print(f"[proposals] {det.__name__} failed ({trigger}): {type(e).__name__}: {e}")
    return out


# --- D1: title/question auto-evolution (ownership-governed) ----------------

_QNAME_SYSTEM = (
    "You name scientific investigation threads. Given a thread's recent "
    "conversation, produce a SHORT title (a label of at most 5 words — NOT a "
    "restatement of the question) and a crisp one-sentence QUESTION capturing "
    "what the thread investigates. Only set changed=true if your version is "
    "clearly better than the current title/question; otherwise changed=false. "
    "Output only the JSON object, no prose: "
    '{"title": "...", "question": "...", "changed": true|false}'
)


# An ignored question-rename suggestion fades after this many more assistant
# turns (unobtrusive — the user shouldn't have to keep dismissing it).
_QSUGGEST_EXPIRE_TURNS = 4


def _expire_question_suggestions(thread_id: str, current_turn: int) -> None:
    """Auto-dismiss pending question suggestions the user has scrolled past."""
    for p in list_proposals(thread_id=thread_id, status="pending"):
        if p.get("kind") != "question":
            continue
        raised = int((p.get("payload") or {}).get("raised_turn") or 0)
        if current_turn - raised >= _QSUGGEST_EXPIRE_TURNS:
            update_proposal(p["id"], status="dismissed")


def _detect_title_question(thread_id: str) -> list[int]:
    """Ownership-governed title/question evolution:
      - **Guide-owned** (user hasn't set the question): refine it *silently* —
        just update with the best guess, no notification, no proposal.
      - **User-owned** (user edited it): offer an *ephemeral* rename suggestion
        that the user can accept/dismiss, and which fades on its own if ignored.
    """
    thr = get_entity(thread_id)
    if not thr or thr.get("type") != "thread":
        return []
    msgs = get_messages(WORKSPACE_ID, thread_id=thread_id)
    n = sum(1 for m in msgs if m.get("role") == "assistant")
    meta = dict(thr.get("metadata") or {})
    user_owned = (meta.get("question_source") or "guide") == "user"

    if user_owned:
        _expire_question_suggestions(thread_id, n)

    last = int(meta.get("q_eval_turn") or 0)
    step = 6 if user_owned else 2          # the Guide refines its placeholder readily
    if n < 2 or (n - last) < step:
        return []
    meta["q_eval_turn"] = n                # record the evaluation (persisted below)

    cur_q = (meta.get("question") or "").strip()
    cur_t = (thr.get("title") or "").strip()
    res = _ask_json(_QNAME_SYSTEM, _qname_prompt(cur_t, cur_q, _thread_digest(msgs)),
                    fake={"changed": True, "title": "Proneural cell stability",
                          "question": "Are proneural-marker-high cells a stable subpopulation?"})
    new_q = (res or {}).get("question", "").strip() if res else ""
    new_t = _short_title((res or {}).get("title") or "") if res else ""
    nothing_better = not res or not res.get("changed") or \
        ((not new_q or new_q == cur_q) and (not new_t or new_t == cur_t))
    if nothing_better:
        update_entity(thread_id, metadata=meta)   # persist the gate marker
        return []

    if not user_owned:
        # Guide owns the placeholder → apply the refinement silently.
        if new_q:
            meta["question"] = new_q
        meta["question_source"] = "guide"
        fields = {"metadata": meta}
        if new_t and new_t != cur_t:
            fields["title"] = new_t
        update_entity(thread_id, **fields)
        return []

    # User owns it → an ephemeral, dismissible rename suggestion.
    update_entity(thread_id, metadata=meta)
    payload = {"question": new_q or cur_q, "title": new_t or cur_t,
               "set_source": "user", "raised_turn": n}
    sig = _sig("question", thread_id, payload["question"], payload["title"])
    pid = add_proposal(thread_id=thread_id, kind="question", advisor="guide",
                       headline="Rename to fit the discussion?",
                       body=(new_q or new_t), payload=payload, signature=sig)
    return [pid] if pid else []


def _short_title(t: str) -> str:
    words = " ".join((t or "").strip().split()).split(" ")
    return " ".join(words[:6])[:48].strip().strip(".?!")


def _thread_digest(msgs: list, max_msgs: int = 8, per: int = 320) -> str:
    out = []
    for m in msgs[-max_msgs:]:
        txt = " ".join(b.get("text", "") for b in m.get("content", [])
                       if isinstance(b, dict) and b.get("type") == "text").strip()
        if txt:
            out.append(f"{m['role']}: {txt[:per]}")
    return "\n".join(out)


def _qname_prompt(cur_t: str, cur_q: str, digest: str) -> str:
    return (f"Current title: {cur_t or '(none)'}\n"
            f"Current question: {cur_q or '(none)'}\n\n"
            f"Recent conversation:\n{digest or '(empty)'}\n")


_TRANSIENT = ("overloaded", "rate_limit", "timeout", "connection",
              "529", "503", "502", "500")


def _ask_json(system: str, prompt: str, fake: Optional[dict] = None) -> Optional[dict]:
    """One-shot Haiku call expecting a small JSON object. Retries transient API
    failures (e.g. 529 overloaded) a few times with backoff, mirroring the chat
    loop, so a momentary blip doesn't silently degrade the result. Returns `fake`
    verbatim in fake mode; returns None on a persistent failure (callers degrade
    gracefully)."""
    if FAKE_SESSION:
        return fake
    import time
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    text = None
    for attempt in range(3):
        try:
            msg = client.messages.create(model=MODEL, max_tokens=220, system=system,
                                         messages=[{"role": "user", "content": prompt}])
            text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
            break
        except Exception as e:
            status = getattr(e, "status_code", None)
            transient = status in (408, 429, 500, 502, 503, 504, 529) or \
                any(t in str(e).lower() for t in _TRANSIENT)
            if attempt == 2 or not transient:
                return None
            time.sleep(2 ** attempt)   # 1s, 2s
    if not text:
        return None
    # Models may fence the JSON and append prose; decode the first object and
    # ignore anything after it.
    i = text.find("{")
    if i < 0:
        return None
    try:
        obj, _ = _json.JSONDecoder().raw_decode(text[i:])
        return obj
    except Exception:
        return None


def _thread_results(thread_id: str) -> list[dict]:
    """Pinned, keepable results tagged to this thread (figures/tables)."""
    out = []
    for e in list_entities(include_archived=False):
        if e.get("type") not in ("figure", "table"):
            continue
        m = e.get("metadata") or {}
        if m.get("thread_id") == thread_id and e.get("pinned"):
            out.append(e)
    return out


# --- D2: convergence detection ---------------------------------------------

_CONVERGE_SYSTEM = (
    "You decide whether a set of pinned results in a scientific thread converge "
    "on a single preliminary claim. You are given each result's title and the "
    "scientist's own interpretation. Judge whether two or more of them point to "
    "one coherent conclusion. A converging SUBSET is enough — ignore results "
    "that address an orthogonal topic rather than letting them block the rest. "
    "Be conservative about what counts as mutual support. If two or more "
    "reinforce one claim, write ONE modest, preliminary single-sentence claim "
    "and list ONLY the ids of the results that support it. Output only the JSON "
    'object, no prose: '
    '{"converges": true|false, "statement": "...", "evidence_ids": ["..."]}'
)


def _claimed_result_ids(thread_id: str) -> set:
    """Result ids already cited as evidence by a live claim in this thread."""
    ids: set = set()
    for e in list_entities(include_archived=False):
        if e.get("type") != "claim":
            continue
        if (e.get("metadata") or {}).get("thread_id") != thread_id:
            continue
        for rid in (e.get("metadata") or {}).get("evidence_ids") or []:
            ids.add(rid)
    return ids


def _detect_convergence(thread_id: str) -> list[int]:
    results = [r for r in _thread_results(thread_id)
               if (r.get("metadata") or {}).get("interpretation")]
    if len(results) < 3:
        return []
    claimed = _claimed_result_ids(thread_id)
    cand = [r for r in results if r["id"] not in claimed]
    if len(cand) < 3:
        return []

    msgs = get_messages(WORKSPACE_ID, thread_id=thread_id)
    n = sum(1 for m in msgs if m.get("role") == "assistant")
    thr = get_entity(thread_id)
    meta = dict(thr.get("metadata") or {})
    if n - int(meta.get("conv_eval_turn") or 0) < 3:
        return []
    sig = _sig("convergence", thread_id, ",".join(sorted(r["id"] for r in cand)))
    if proposal_signature_exists(sig):
        return []   # already evaluated this exact shelf state
    meta["conv_eval_turn"] = n
    update_entity(thread_id, metadata=meta)

    res = _ask_json(
        _CONVERGE_SYSTEM, _converge_prompt(cand),
        fake={"converges": True,
              "statement": "These results converge on a stable proneural subpopulation.",
              "evidence_ids": [c["id"] for c in cand[:3]]})
    if not res or not res.get("converges"):
        return []
    cand_ids = {c["id"] for c in cand}
    ev = [rid for rid in (res.get("evidence_ids") or []) if rid in cand_ids]
    stmt = (res.get("statement") or "").strip()
    if len(ev) < 2 or not stmt:
        return []
    pid = add_proposal(thread_id=thread_id, kind="convergence", advisor="guide",
                       headline=f"{len(ev)} results point the same way — draft a claim?",
                       body=stmt, payload={"statement": stmt, "evidence_ids": ev},
                       signature=sig)
    return [pid] if pid else []


def _converge_prompt(cand: list[dict]) -> str:
    lines = []
    for r in cand:
        interp = (r.get("metadata") or {}).get("interpretation", "")
        lines.append(f"[{r['id']}] {r.get('title','')} — {interp}")
    return "Pinned results:\n" + "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Accept / dismiss / undo
# --------------------------------------------------------------------------

def accept_proposal(pid: int) -> dict:
    """Perform the proposal's side-effect, record undo data, mark accepted.
    Returns {proposal, result_id?, created?}."""
    p = get_proposal(pid)
    if not p:
        raise ValueError(f"proposal {pid} not found")
    if p["status"] != "pending":
        return {"proposal": p}
    kind, tid, payload = p["kind"], p["thread_id"], (p["payload"] or {})

    result_id = None
    undo: dict = {}

    if kind == "convergence":
        result_id = _create_claim(payload, tid)
        undo = {"archive_entity": result_id}
    elif kind in ("title", "question"):
        thr = get_entity(tid) or {}
        m = dict(thr.get("metadata") or {})
        undo = {"restore_title": thr.get("title"),
                "restore_question": m.get("question"),
                "restore_question_source": m.get("question_source")}
        if payload.get("title"):
            update_entity(tid, title=payload["title"])
        if payload.get("question"):
            m["question"] = payload["question"]
            m["question_source"] = payload.get("set_source", "guide")
            update_entity(tid, metadata=m)
    elif kind == "subquestion":
        oqid = _file_oq(tid, payload.get("text", ""))
        undo = {"remove_oq": oqid}
        result_id = oqid
    elif kind in ("return_wrap", "nplus1"):
        # Informational: accepting just acknowledges (no destructive effect).
        pass

    update_proposal(pid, status="accepted", result_id=result_id, undo_data=undo)
    return {"proposal": get_proposal(pid), "result_id": result_id}


def dismiss_proposal(pid: int) -> dict:
    update_proposal(pid, status="dismissed")
    return {"proposal": get_proposal(pid)}


def undo_proposal(pid: int) -> dict:
    """Reverse a previously accepted proposal and return it to pending."""
    p = get_proposal(pid)
    if not p:
        raise ValueError(f"proposal {pid} not found")
    undo = p.get("undo_data") or {}
    tid = p["thread_id"]
    if "archive_entity" in undo and undo["archive_entity"]:
        archive_entity(undo["archive_entity"])
    if "restore_title" in undo:
        update_entity(tid, title=undo["restore_title"])
    if "restore_question" in undo:
        thr = get_entity(tid) or {}
        m = dict(thr.get("metadata") or {})
        m["question"] = undo.get("restore_question")
        m["question_source"] = undo.get("restore_question_source")
        update_entity(tid, metadata=m)
    if "remove_oq" in undo and undo["remove_oq"]:
        thr = get_entity(tid) or {}
        m = dict(thr.get("metadata") or {})
        m["open_questions"] = [o for o in (m.get("open_questions") or [])
                               if o.get("id") != undo["remove_oq"]]
        update_entity(tid, metadata=m)
    update_proposal(pid, status="pending", result_id="", undo_data={})
    return {"proposal": get_proposal(pid)}


# --------------------------------------------------------------------------
# Side-effect helpers (mirror the main.py claim/OQ creation)
# --------------------------------------------------------------------------

def _create_claim(payload: dict, tid: str) -> str:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    stmt = (payload.get("statement") or "Untitled claim").strip()
    ev = list(payload.get("evidence_ids") or [])
    cid = create_entity(
        entity_type="claim", title=stmt[:80],
        metadata={"statement": stmt, "negative": False,
                  "evidence_ids": ev, "caveats": [], "alternatives": [],
                  "confidence": "preliminary", "thread_id": tid,
                  "status_log": [{"from": None, "to": "preliminary",
                                  "reason": "created from proposal", "actor": "guide", "at": now}]})
    for rid in ev:
        add_edge(cid, rid, "supports")
    return cid


def _file_oq(tid: str, text: str) -> str:
    from datetime import datetime, timezone
    thr = get_entity(tid) or {}
    m = dict(thr.get("metadata") or {})
    oqs = list(m.get("open_questions") or [])
    oqid = gen_entity_id("oq")
    oqs.append({"id": oqid, "text": text.strip(), "status": "open",
                "source": "guide", "at": datetime.now(timezone.utc).isoformat()})
    m["open_questions"] = oqs
    update_entity(tid, metadata=m)
    return oqid


# ---------- Hook handlers ----------
# Pass D: post-turn proposal evaluation registered as an on_stop hook.

import asyncio as _asyncio
from core.hooks.dispatcher import register as _register_hook


def _on_stop_evaluate(ctx: dict) -> None:
    """ctx: thread_id. Fires evaluate_thread off the response path."""
    tid = ctx.get("thread_id")
    if not tid:
        return
    try:
        loop = _asyncio.get_event_loop()
        loop.run_in_executor(None, evaluate_thread, tid, "post_turn")
    except RuntimeError:
        evaluate_thread(tid, "post_turn")


# Priority 20 so reflect (priority 10) runs first if it ever needs to.
_register_hook("on_stop", _on_stop_evaluate, priority=20)
