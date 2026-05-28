"""
Conversation summarization (arch §3.3 memory management).

When the project's workspace conversation grows long, the oldest portion
is collapsed into a running summary so the LLM call stays bounded while
the full thread remains in the DB for display and exact retrieval.

Prototype implementation: a single rolling summary covering everything
older than the most recent RECENT_KEEP messages, regenerated (cheaply,
via Haiku) when enough new messages have accrued. No-op in fake mode.
"""
from __future__ import annotations
import json
import sqlite3

from config import API_KEY, MODEL, FAKE_SESSION
from core.graph._schema import _conn, _utcnow

SUMMARY_THRESHOLD = 30   # only summarize once the thread exceeds this
RECENT_KEEP = 12         # always send this many recent messages verbatim
REGEN_STEP = 8           # regenerate the summary every N new old-messages


def _ensure_table():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                entity_id    TEXT PRIMARY KEY,
                covered      INTEGER NOT NULL,
                summary      TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )
        """)
        c.commit()


def _get(entity_id: str) -> tuple[int, str] | None:
    _ensure_table()
    with _conn() as c:
        try:
            r = c.execute(
                "SELECT covered, summary FROM conversation_summaries WHERE entity_id=?",
                (entity_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return (r["covered"], r["summary"]) if r else None


def _put(entity_id: str, covered: int, summary: str):
    _ensure_table()
    with _conn() as c:
        c.execute(
            "INSERT INTO conversation_summaries (entity_id, covered, summary, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(entity_id) DO UPDATE SET "
            "covered=excluded.covered, summary=excluded.summary, updated_at=excluded.updated_at",
            (entity_id, covered, summary, _utcnow()),
        )
        c.commit()


def _text_of(msg: dict) -> str:
    parts = []
    for b in msg.get("content", []):
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b["text"])
        elif isinstance(b, dict) and b.get("type") == "tool_use":
            parts.append(f"[ran {b.get('name')}]")
    return f"{msg['role']}: {' '.join(parts)}"


def extract_session_cells(messages: list[dict], max_chars: int = 6000) -> str:
    """Concatenated code of run_python/run_r cells in `messages` (kernels.md §8.2).

    Summarization collapses old messages to prose — but for a kernel-backed
    thread that would drop the very cells that built the live session's state,
    so the agent loses the record of HOW objects were made and can't replay
    after a kernel restart. We keep the code (compact, results stripped). If the
    listing is huge, keep the most RECENT cells (they define current state)."""
    blocks: list[str] = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for b in m.get("content", []):
            if isinstance(b, dict) and b.get("type") == "tool_use" \
                    and b.get("name") in ("run_python", "run_r"):
                code = ((b.get("input") or {}).get("code") or "").strip()
                if code:
                    lang = "r" if b["name"] == "run_r" else "python"
                    blocks.append(f"```{lang}\n{code}\n```")
    if not blocks:
        return ""
    text = "\n".join(blocks)
    return text[-max_chars:] if len(text) > max_chars else text


def _summarize(old_msgs: list[dict], prior: str | None) -> str:
    transcript = "\n".join(_text_of(m) for m in old_msgs)[:8000]
    system = (
        "You compress an earlier portion of a scientific working session into "
        "a compact summary that preserves decisions, key numbers, named "
        "entities (figures/results/samples), and open questions. 4–8 bullet "
        "lines. This becomes the agent's memory of what came before."
    )
    user = (("Existing summary:\n" + prior + "\n\n") if prior else "") + \
        "New earlier messages to fold in:\n" + transcript
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    msg = client.messages.create(
        model=MODEL, max_tokens=400, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()


def effective_history(entity_id: str, messages: list[dict]) -> list[dict]:
    """
    Return the message list to send to the LLM: if the thread is long,
    a summary message standing in for the old portion, followed by the
    recent messages. Otherwise the messages unchanged.
    """
    n = len(messages)
    if n <= SUMMARY_THRESHOLD or FAKE_SESSION:
        return messages

    to_cover = n - RECENT_KEEP
    existing = _get(entity_id)
    covered, summary = (existing or (0, None))

    # Regenerate when enough new old-messages have accrued.
    if summary is None or to_cover - covered >= REGEN_STEP:
        try:
            new_summary = _summarize(messages[:to_cover], summary)
            _put(entity_id, to_cover, new_summary)
            summary, covered = new_summary, to_cover
        except Exception:
            if summary is None:
                return messages  # can't summarize; fall back to full history

    content = [{
        "type": "text",
        "text": f"[Summary of the earlier {covered} messages in this project]\n{summary}",
    }]
    # Retain the code cells from the summarized region so a kernel-backed
    # session's state remains explainable / replayable (kernels.md §8.2).
    cells = extract_session_cells(messages[:covered])
    if cells:
        content.append({
            "type": "text",
            "text": ("[Code cells executed earlier in this session — their state persists in "
                     "your kernel; this is the record of how objects were built, for recall "
                     "or replay after a restart]:\n" + cells),
        })
    summary_msg = {"role": "user", "content": content}
    # Keep messages after the covered point (at least RECENT_KEEP).
    tail = messages[covered:]
    return [summary_msg] + tail
