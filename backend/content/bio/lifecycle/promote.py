"""
Promotion flows: figure → result → finding → claim.

Each promotion creates a new higher-level entity and edges it to its
source(s) with `supports` (for evidence) and `wasDerivedFrom` (so the
provenance walk works the same as for run-generated artifacts).
"""
from __future__ import annotations
import json
from typing import Optional, Any

from core.graph.edges import add_edge, remove_edge
from core.graph.entities import create_entity, get_entity, update_entity

# Placeholder used when interpretation is not provided at Pin time. The A3
# background Guide turn replaces this once the description has been generated.
AI_INTERPRETATION_PLACEHOLDER = "✨ generating…"


def pin_evidence(
    *,
    thread_id: str,
    target_result_id: Optional[str] = None,
    evidence_kind: str,                          # figure | table | cell | note | narrative | value | text
    evidence_id: Optional[str] = None,           # if the evidence already exists as an entity
    evidence_payload: Optional[dict[str, Any]] = None,  # else, create one from {title, artifact_path, metadata, …}
    caption: str = "",
    interpretation: Optional[str] = None,         # None → AI placeholder + A3 background job (TODO)
    origin: str = "internal",                    # "internal" | "external"
    parent_run_id: Optional[str] = None,         # optional 'produced_by' edge (run output pins)
    title: Optional[str] = None,                  # explicit Result title; else derived
) -> dict:
    """The ONE pin operation. Five UX surfaces (EntityMenu Pin, RunView output Pin,
    chat-message Pin, external upload, Result-page Add-evidence) all converge here.

    - When target_result_id is None, creates a NEW Result wrapping the evidence.
    - When target_result_id is set, appends the evidence as a NEW member to that
      existing Result; interpretation is NOT regenerated (member-add only).
    - Resolves the evidence: uses evidence_id if provided; else creates a new entity
      from evidence_payload (caller controls payload — file upload, message snippet, …).

    Returns {result_id, member_id, evidence_id} so callers know what was just attached.
    """
    from content.bio.graph.result_members import add_result_member

    # 1. Resolve / create the evidence entity.
    if evidence_id is None:
        if evidence_kind in ("text", "value"):
            # Text-only members don't get their own entity — they live as inline panels.
            # Skip creation; the member will carry `text` directly.
            evidence_id = None
        else:
            payload = dict(evidence_payload or {})
            md = dict(payload.get("metadata") or {})
            md.setdefault("thread_id", thread_id)
            md.setdefault("origin", origin)
            evidence_id = create_entity(
                entity_type=evidence_kind,
                title=payload.get("title") or evidence_kind,
                artifact_path=payload.get("artifact_path"),
                metadata=md,
            )
            if parent_run_id:
                add_edge(evidence_id, parent_run_id, "produced_by")
    elif not get_entity(evidence_id):
        raise ValueError(f"evidence {evidence_id} not found")

    member_kind = "figure" if evidence_kind in ("figure", "table", "cell", "value") else evidence_kind
    if member_kind not in ("figure", "table", "value", "text"):
        # Notes / narratives ride as "text" panels — the entity holds the prose,
        # the member carries the ref + caption.
        member_kind = "text"

    if target_result_id is not None:
        # Append to existing Result — interpretation untouched.
        r = get_entity(target_result_id)
        if not r or r["type"] != "result":
            raise ValueError(f"target result {target_result_id} not found")
        out = add_result_member(target_result_id, kind=member_kind, ref=evidence_id,
                                text=(evidence_payload or {}).get("text"),
                                caption=caption)
        if evidence_id:
            add_edge(target_result_id, evidence_id, "includes")
            add_edge(target_result_id, evidence_id, "supports", {"direction": "result-supported-by-evidence"})
        members = (out.get("metadata") or {}).get("members", []) if out else []
        member_id = members[-1].get("id") if members else None
        return {"result_id": target_result_id, "member_id": member_id, "evidence_id": evidence_id, "created_result": False}

    # New Result. The figure CAPTION lives on the member (filled by the A3
    # background auto_interpret daemon — see auto_interpret below). The
    # Result-level `interpretation` field is reserved for an explicit
    # cross-evidence synthesis the user writes; it defaults to empty and
    # stays empty unless the user types something. Old design used the
    # placeholder "✨ generating…" here and the daemon overwrote it, but
    # since the daemon now writes to member.caption that placeholder would
    # be stuck on screen forever — so we no longer set one.
    interp = interpretation if interpretation is not None else ""
    auto_title = title or (interp.strip().split("\n")[0][:80] if interp.strip() else "")
    if not auto_title:
        ev = get_entity(evidence_id) if evidence_id else None
        auto_title = (ev or {}).get("title") or "Result"
    # `invested` flips to True on ANY meaningful user action on the Result:
    # editing a caption, editing the interpretation, editing the title, or
    # adding/removing/reordering members. Drives the unpin "user-never-
    # invested" semantics — if False at unpin time, we archive the auto-
    # generated wrapper; if True, we preserve the user's work.
    rid = create_entity(
        entity_type="result",
        title=auto_title,
        parent_entity_id=(get_entity(evidence_id) or {}).get("parent_entity_id") if evidence_id else None,
        metadata={
            "thread_id": thread_id,
            "origin": origin,
            "interpretation": interp,
            "interpretation_origin": "user" if interp else "",
            "invested": bool(interp),   # user supplied an interpretation at create-time → invested
            "members": [],
        },
    )
    # Initial member of an auto-created wrapper Result — NOT a user
    # action, so don't flip `invested`. The user's first real edit will.
    add_result_member(rid, kind=member_kind, ref=evidence_id,
                      text=(evidence_payload or {}).get("text"), caption=caption,
                      invested=False)
    if evidence_id:
        add_edge(rid, evidence_id, "includes")
        add_edge(rid, evidence_id, "supports", {"direction": "result-supported-by-evidence"})
        add_edge(rid, evidence_id, "wasDerivedFrom")
    if parent_run_id and evidence_id:
        add_edge(rid, parent_run_id, "wasDerivedFrom")
    final = get_entity(rid)
    members = (final.get("metadata") or {}).get("members", []) if final else []
    member_id = members[-1].get("id") if members else None
    return {"result_id": rid, "member_id": member_id, "evidence_id": evidence_id, "created_result": True}


def unpin_evidence(evidence_id: str, thread_id: Optional[str] = None) -> dict:
    """Inverse of pin_evidence (B / #321).

    For each non-archived Result in `thread_id` (or any thread, if None) that
    INCLUDES `evidence_id`:
      • exactly 1 member AND interpretation_origin == 'ai' (still auto)
          → ARCHIVE the Result (user never invested; hard-delete handled by
            the destructive-confirm in #322).
      • exactly 1 member AND interpretation_origin == 'user' (edited)
          → ARCHIVE the Result (preserve the user's interpretation; reversible).
      • >1 members → REMOVE only this member from the Result; Result stays.

    Returns {affected: [{result_id, action}]} for the UI/log.
    """
    from content.bio.graph.result_members import remove_result_member
    incoming = edges_to(evidence_id)
    wrap_ids = {e["source_id"] for e in incoming if e.get("rel_type") == "includes"}
    affected: list[dict] = []
    for rid in wrap_ids:
        r = get_entity(rid)
        if not r or r["type"] != "result" or r.get("status") == "archived":
            continue
        if thread_id and (r.get("metadata") or {}).get("thread_id") != thread_id:
            continue
        members = (r.get("metadata") or {}).get("members") or []
        # Identify which member(s) reference this evidence; usually exactly one.
        target_members = [m for m in members if m.get("ref") == evidence_id]
        if len(members) <= 1 or len(members) - len(target_members) == 0:
            # Single binary signal: did the user invest in this Result? Any
            # caption edit / interpretation edit / title edit / member add
            # flips `metadata.invested` to True. Drives whether the wrapper
            # gets archived (no investment → safe to drop) or preserved.
            invested = bool((r.get("metadata") or {}).get("invested"))
            from core.graph.entities import archive_entity as _archive
            _archive(rid)
            remove_edge(rid, evidence_id, "includes")
            remove_edge(rid, evidence_id, "supports")
            remove_edge(rid, evidence_id, "wasDerivedFrom")
            affected.append({"result_id": rid, "action": "archived",
                             "reason": "single-member-edited" if invested else "single-member-ai"})
        else:
            for m in target_members:
                remove_result_member(rid, m["id"])
            remove_edge(rid, evidence_id, "includes")
            remove_edge(rid, evidence_id, "supports")
            affected.append({"result_id": rid, "action": "removed_member",
                             "members_left": len(members) - len(target_members)})
    return {"affected": affected, "evidence_id": evidence_id}


def _load_annotation_prompt(name: str) -> str:
    """Read an annotation system-prompt from
    `content/bio/prompts/annotations/<name>.md`. Edit the file → next
    request picks up the new prompt (no server bounce). Cached for the
    process lifetime to avoid disk hits on every annotation; bust by
    deleting `_PROMPT_CACHE[name]`.

    Annotation prompts live as Markdown content (not Python constants)
    so they're easy to find, diff, and iterate on without touching code.
    """
    from pathlib import Path as _Path
    if name in _PROMPT_CACHE:
        return _PROMPT_CACHE[name]
    p = _Path(__file__).parent.parent / "prompts" / "annotations" / f"{name}.md"
    text = p.read_text() if p.is_file() else ""
    _PROMPT_CACHE[name] = text
    return text


_PROMPT_CACHE: dict[str, str] = {}


def _sync_anthropic_client():
    """Sync Anthropic client for the caption helper. The shared `_llm_client()`
    now returns `AsyncAnthropic` (so guide.py's streaming loop doesn't park
    the event loop), but caption_via_vision_llm runs in a worker thread and
    needs a SYNC client — otherwise `client.messages.create(...)` returns
    a coroutine that's never awaited and reading `.content` throws, getting
    eaten by the try/except and silently degrading to chat-text pluck (the
    2026-05-31 hang). Mirrors `_llm_client`'s OAuth-vs-API-key selection."""
    import anthropic, os
    from core.config import API_KEY
    from core.llm import _oauth_bearer
    if os.environ.get("ABA_LLM_CREDENTIAL", "apikey").lower() == "oauth":
        tok = _oauth_bearer()
        if tok:
            return anthropic.Anthropic(auth_token=tok)
    return anthropic.Anthropic(api_key=API_KEY)


def _image_block(disk_path):
    """If `disk_path` points to a vision-readable file, return a vision
    content block for the Anthropic API (with downscaling). Otherwise None.
    Helper for `_llm_annotation_request`."""
    import base64
    from pathlib import Path as _Path
    fpath = _Path(disk_path) if disk_path else None
    if fpath is None or not fpath.is_file():
        return None
    suffix = fpath.suffix.lower().lstrip(".")
    media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "webp": "image/webp", "gif": "image/gif"}.get(suffix)
    if not media:
        return None        # PDFs/SVGs not vision-readable here
    # Downscale before sending. Anthropic vision recommends ≤1568 px on the
    # longest side; anything larger is resized server-side anyway. Skip the
    # resample when the image is already small (avoids re-encoding R defaults).
    raw = fpath.read_bytes()
    MAX_SIDE = 1568
    SIZE_THRESHOLD = 256 * 1024
    if len(raw) > SIZE_THRESHOLD:
        try:
            from PIL import Image
            import io
            im = Image.open(io.BytesIO(raw))
            w, h = im.size
            long_side = max(w, h)
            if long_side > MAX_SIDE:
                scale = MAX_SIDE / long_side
                im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            if im.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            raw = buf.getvalue()
            media = "image/png"
        except Exception:  # noqa: BLE001 — resize is best-effort
            pass
    b64 = base64.b64encode(raw).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}}


def _llm_annotation_request(
    *,
    disk_path=None,
    producing_code: str = "",
    chat_context: str = "",
    extras: dict | None = None,
    title_hint: str = "",
    system_prompt: str,
    max_tokens: int = 600,
) -> str:
    """LAYER 1 — reusable LLM annotation request, domain-agnostic.

    Composes the standard ABA "[optional image] + producing code + chat
    context + extras" user message, calls a SYNC Anthropic client (we're
    typically in a daemon thread off the asyncio loop), and returns the
    raw text response. Any failure → "" so callers can fall back.

    All inputs are OPTIONAL — vision-less callers (table summaries,
    dataset descriptions, claim refinement) just leave `disk_path=None`
    and pass relevant context via `producing_code` / `chat_context` /
    `extras` (a dict of free-form labeled sections, rendered as
    "LABEL:\\n<content>" blocks). Caller supplies the `system_prompt`
    (which can ask for JSON, plain prose, structured bullets, …) and
    parses the return.
    """
    try:
        user_blocks: list = []
        img = _image_block(disk_path)
        if img is not None:
            user_blocks.append(img)
        text_parts: list[str] = []
        if title_hint:
            text_parts.append(f"Title hint (from the producer, not authoritative): {title_hint!r}")
        if producing_code:
            text_parts.append(f"Producing code:\n```\n{producing_code}\n```")
        if chat_context:
            text_parts.append(f"Conversation context (user asks + agent narration):\n{chat_context}")
        for label, content in (extras or {}).items():
            if content:
                text_parts.append(f"{label}:\n{content}")
        if not text_parts and img is None:
            return ""    # nothing to annotate
        user_blocks.append({"type": "text", "text": "\n\n".join(text_parts) or "(no context)"})

        from core.config import MODEL
        client = _sync_anthropic_client()
        r = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_blocks}],
        )
        return " ".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
    except Exception:  # noqa: BLE001
        return ""


def annotate_figure(disk_path, producing_code: str, chat_context: str, title_hint: str) -> dict:
    """LAYER 2 — figure-specific annotation. Returns
    `{"title": str, "caption": str}`. Empty dict on any failure.

    Uses _llm_annotation_request with the figure annotation system prompt
    (asks for JSON), then parses. Future Layer-2 annotators (table
    summaries, claim refinement, dataset descriptions) follow the same
    shape: pick a system prompt + a parser for the return value.
    """
    import json as _json, re as _re
    raw = _llm_annotation_request(
        disk_path=disk_path, producing_code=producing_code,
        chat_context=chat_context, title_hint=title_hint,
        system_prompt=_load_annotation_prompt("figure"),
        max_tokens=400,
    )
    if not raw:
        return {}
    # Be lenient: the LLM may wrap JSON in ```json fences or add a stray
    # leading sentence. Extract the first {...} block we see.
    m = _re.search(r"\{.*\}", raw, _re.S)
    if not m:
        return {}
    try:
        out = _json.loads(m.group(0))
    except _json.JSONDecodeError:
        return {}
    if not isinstance(out, dict):
        return {}
    return {
        "title": (out.get("title") or "").strip(),
        "caption": (out.get("caption") or "").strip(),
    }


# Back-compat shim — main.py's _llm_figure_caption still calls this name.
# Returns just the caption string (the title-aware path is the new default;
# this shim is only used by the explicit "Suggest" button, which feeds the
# text into the promote dialog's caption field).
def caption_via_vision_llm(disk_path, producing_code: str, chat_context: str, title: str) -> str:
    return annotate_figure(disk_path, producing_code, chat_context, title).get("caption", "")


def _artifact_url_to_disk(url: str):
    """Resolve a stored /artifacts/<pid>/<name> URL to a disk Path.
    Local copy (auto_interpret can't import from main.py without a cycle)."""
    from pathlib import Path as _Path
    from core.config import project_artifacts_dir, ARTIFACTS_DIR
    if not url or not url.startswith("/artifacts/"):
        return None
    parts = url[len("/artifacts/"):].split("/")
    if len(parts) == 2 and parts[0] and parts[1] and ".." not in parts[0] and ".." not in parts[1]:
        return project_artifacts_dir(parts[0]) / parts[1]
    if len(parts) == 1 and parts[0] and ".." not in parts[0]:
        return _Path(ARTIFACTS_DIR) / parts[0]
    return None


def auto_interpret(result_id: str) -> Optional[str]:
    """A3: background figure-caption generator. Fires on every pin → result
    promotion. Uses the vision-LLM path (figure image + producing code + ±6
    chat turns) and stores the result as the figure MEMBER's caption — not
    on the Result's interpretation field. A figure caption belongs on the
    figure; the Result-level interpretation is reserved for an explicit
    cross-panel synthesis the user (or future agent) writes when there's
    more than one piece of evidence.

    Pipeline:
      1. Locate the FIRST figure member of the Result.
      2. Find the tool_result that emitted the figure to gather a ±6-turn
         chat-context window (user asks + agent narration).
      3. Call the vision LLM (caption_via_vision_llm). On failure, fall
         back to the legacy chat-text pluck so upload-from-outside flows
         still get *something*.
      4. Write to member.caption + member.caption_origin='ai'.

    Idempotent: bails if the figure member's caption_origin is already
    'user' (user edited first)."""
    from core.graph.messages import get_messages
    from core.graph._schema import WORKSPACE_ID
    from content.bio.graph.result_members import update_result_member

    r = get_entity(result_id)
    if not r or r["type"] != "result":
        return None
    md = r.get("metadata") or {}
    members = md.get("members") or []
    # Find the first figure member — this is the canonical "the figure" for
    # the typical chat-pin flow (one figure → one Result).
    fig_member = next((m for m in members
                       if m.get("kind") == "figure" and m.get("ref")), None)
    if not fig_member:
        return None
    if fig_member.get("caption_origin") == "user":
        return None  # user beat us; do not overwrite
    ev = get_entity(fig_member["ref"])
    if not ev:
        return None
    art = ev.get("artifact_path")

    msgs = get_messages(WORKSPACE_ID, thread_id=md.get("thread_id")) if md.get("thread_id") else get_messages(WORKSPACE_ID)

    def asst_text(m: dict) -> str:
        if m.get("role") != "assistant":
            return ""
        return " ".join(b.get("text", "") for b in m.get("content", [])
                        if isinstance(b, dict) and b.get("type") == "text").strip()

    def turn_text(m: dict) -> str:
        if m.get("role") == "user":
            parts = []
            for b in m.get("content", []):
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif isinstance(b, str):
                    parts.append(b)
            return " ".join(p for p in parts if p).strip()
        if m.get("role") == "assistant":
            return asst_text(m)
        return ""

    # Find the tool_result that produced this artifact.
    prod_idx: Optional[int] = None
    for i, m in enumerate(msgs):
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for blk in content:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "tool_result" and art:
                try:
                    plots = (json.loads(blk["content"]) or {}).get("plots") or []
                    if any(p.get("url") == art for p in plots):
                        prod_idx = i
                except Exception:  # noqa: BLE001
                    pass
            elif blk.get("type") == "image" and art and blk.get("url") == art:
                prod_idx = i

    # Build the ±6-turn chat-context window for the LLM.
    chat_context = ""
    if prod_idx is not None:
        lo, hi = max(0, prod_idx - 6), min(len(msgs), prod_idx + 4)
        chunks: list[str] = []
        for j in range(lo, hi):
            t = turn_text(msgs[j])
            if not t:
                continue
            role = msgs[j].get("role")
            tag = "USER" if role == "user" else "AGENT"
            anchor = " (← figure here)" if j == prod_idx else ""
            chunks.append(f"[{tag}{anchor}] {t}")
        chat_context = "\n\n".join(chunks)[:3000]

    producing_code = (ev.get("producing_code") or "")[:6000]
    title_hint = (ev.get("title") or "").strip()

    # 1) LLM annotation path — title + caption together.
    ann = annotate_figure(_artifact_url_to_disk(art) if art else None,
                          producing_code, chat_context, title_hint)
    text = (ann.get("caption") or "").strip()
    new_title = (ann.get("title") or "").strip()

    # 2) Fallback: legacy text-pluck (kept for upload flows where there's no
    #    producing code AND for the rare LLM-failure case). Title fallback
    #    stays whatever was on the Result already (no good source to derive
    #    one without an LLM call).
    if not text:
        if prod_idx is not None:
            for j in range(prod_idx, min(prod_idx + 4, len(msgs))):
                t = asst_text(msgs[j])
                if t:
                    text = t
                    break
        if not text:
            for m in reversed(msgs):
                t = asst_text(m)
                if t:
                    text = t
                    break
        if text:
            text = text[:400].strip()

    if not text:
        return None
    # Re-fetch in case the user edited while we were searching.
    cur = get_entity(result_id)
    if not cur:
        return None
    cur_members = (cur.get("metadata") or {}).get("members") or []
    cur_fig = next((m for m in cur_members
                    if m.get("kind") == "figure" and m.get("id") == fig_member.get("id")), None)
    if not cur_fig:
        return None
    if cur_fig.get("caption_origin") == "user":
        return None
    update_result_member(result_id, fig_member["id"],
                        caption=text, caption_origin="ai")
    # If the LLM produced a better title than what the pipeline auto-named
    # the Result (and the user hasn't already edited the title — i.e. the
    # Result isn't `invested`), update the title too. Skips when LLM didn't
    # supply a title (text-pluck fallback path) or the user has edited.
    cur_md = cur.get("metadata") or {}
    if new_title and not cur_md.get("invested"):
        # update_entity with title — but we must NOT trip the
        # entity-PATCH "invested" flip (auto_interpret is NOT a user
        # action). Update title via the raw helper so invested stays False.
        update_entity(result_id, title=new_title)
    # Push an out-of-band notification so the frontend refreshes
    # without polling. Best-effort — never breaks the daemon if the
    # event channel is unavailable.
    try:
        from core.runtime.notifications import broadcast
        broadcast({"type": "entity_updated", "entity_id": result_id,
                   "reason": "caption_ready"})
    except Exception:  # noqa: BLE001
        pass
    return text


def promote_figure_to_result(
    figure_id: str,
    interpretation: str,
    title: Optional[str] = None,
) -> str:
    """Create a `result` entity that interprets a figure."""
    fig = get_entity(figure_id)
    if not fig:
        raise ValueError(f"figure {figure_id} not found")
    if fig["type"] != "figure":
        raise ValueError(f"can only promote figures (got {fig['type']})")

    auto_title = title or interpretation.strip().split("\n")[0][:80] or fig["title"]
    rid = create_entity(
        entity_type="result",
        title=auto_title,
        parent_entity_id=fig.get("parent_entity_id"),
        metadata={"interpretation": interpretation, "evidence_figure": figure_id},
    )
    add_edge(rid, figure_id, "supports", {"direction": "result-supported-by-figure"})
    add_edge(rid, figure_id, "wasDerivedFrom")
    return rid


def promote_results_to_finding(
    result_ids: list[str],
    text: str,
    title: Optional[str] = None,
) -> str:
    """Aggregate one or more results into a `finding`."""
    if not result_ids:
        raise ValueError("a finding requires at least one supporting result")
    results = [get_entity(rid) for rid in result_ids]
    if any(r is None for r in results):
        raise ValueError("one or more results not found")
    if any(r["type"] != "result" for r in results):  # type: ignore[index]
        raise ValueError("all sources must be result entities")

    auto_title = title or text.strip().split("\n")[0][:80]
    fid = create_entity(
        entity_type="finding",
        title=auto_title,
        metadata={"text": text, "supporting_results": result_ids},
    )
    for rid in result_ids:
        add_edge(fid, rid, "supports", {"direction": "finding-supported-by-result"})
        add_edge(fid, rid, "wasDerivedFrom")
    return fid


def add_result_to_finding(finding_id: str, result_id: str) -> dict:
    """Attach an additional result to an existing finding."""
    finding = get_entity(finding_id)
    if not finding or finding["type"] != "finding":
        raise ValueError("finding not found")
    result = get_entity(result_id)
    if not result or result["type"] != "result":
        raise ValueError("result not found")
    supporting = list((finding.get("metadata") or {}).get("supporting_results", []))
    if result_id not in supporting:
        supporting.append(result_id)
        meta = dict(finding.get("metadata") or {})
        meta["supporting_results"] = supporting
        update_entity(finding_id, metadata=meta)
        add_edge(finding_id, result_id, "supports", {"direction": "finding-supported-by-result"})
        add_edge(finding_id, result_id, "wasDerivedFrom")
    return get_entity(finding_id)  # type: ignore[return-value]


def remove_result_from_finding(finding_id: str, result_id: str) -> dict:
    finding = get_entity(finding_id)
    if not finding or finding["type"] != "finding":
        raise ValueError("finding not found")
    supporting = list((finding.get("metadata") or {}).get("supporting_results", []))
    if result_id in supporting:
        supporting.remove(result_id)
        meta = dict(finding.get("metadata") or {})
        meta["supporting_results"] = supporting
        update_entity(finding_id, metadata=meta)
        remove_edge(finding_id, result_id, "supports")
        remove_edge(finding_id, result_id, "wasDerivedFrom")
    return get_entity(finding_id)  # type: ignore[return-value]


def create_finding_from_draft(
    title: str,
    summary: str,
    evidence_ids: Optional[list[str]] = None,
    caveats: Optional[list[dict]] = None,
    status: str = "candidate",
) -> str:
    """Create a structured finding directly (selection-to-finding / M3).

    Evidence may be any entity (figure/table/result) — not just results — so
    a finding can be crystallized straight from chat before promotion.
    """
    evidence_ids = evidence_ids or []
    fid = create_entity(
        entity_type="finding",
        title=(title.strip()[:120] or "Untitled finding"),
        metadata={
            "text": summary, "summary": summary,
            "supporting_results": evidence_ids,
            "evidence": evidence_ids,
            "caveats": caveats or [],
            "maturity": status,
        },
    )
    for eid in evidence_ids:
        if get_entity(eid):
            add_edge(fid, eid, "supports", {"direction": "finding-supported-by-evidence"})
            add_edge(fid, eid, "wasDerivedFrom")
    return fid


def set_finding_fields(
    finding_id: str,
    summary: Optional[str] = None,
    caveats: Optional[list[dict]] = None,
    status: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """Edit a finding's structured fields (M7 finding view)."""
    f = get_entity(finding_id)
    if not f or f["type"] != "finding":
        raise ValueError("finding not found")
    meta = dict(f.get("metadata") or {})
    if summary is not None:
        meta["summary"] = summary; meta["text"] = summary
    if caveats is not None:
        meta["caveats"] = caveats
    if status is not None:
        meta["maturity"] = status
    update_entity(finding_id, metadata=meta,
                  **({"title": title.strip()[:120]} if title else {}))
    return get_entity(finding_id)  # type: ignore[return-value]


def promote_findings_to_claim(
    finding_ids: list[str],
    text: str,
    title: Optional[str] = None,
) -> str:
    """Lift one or more findings into a publishable `claim`."""
    if not finding_ids:
        raise ValueError("a claim requires at least one supporting finding")
    findings = [get_entity(fid) for fid in finding_ids]
    if any(f is None for f in findings):
        raise ValueError("one or more findings not found")
    if any(f["type"] != "finding" for f in findings):  # type: ignore[index]
        raise ValueError("all sources must be finding entities")

    auto_title = title or text.strip().split("\n")[0][:80]
    cid = create_entity(
        entity_type="claim",
        title=auto_title,
        metadata={"text": text, "supporting_findings": finding_ids},
    )
    for fid in finding_ids:
        add_edge(cid, fid, "supports", {"direction": "claim-supported-by-finding"})
        add_edge(cid, fid, "wasDerivedFrom")
    return cid
