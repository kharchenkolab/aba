"""In-process pub/sub for out-of-band events (caption ready, background
job done, entity updated, …).

The chat SSE stream (`/api/chat`) is per-turn and closes when the turn
ends. For things that happen OUTSIDE a turn — a background daemon
finishing, a caption arriving 2 seconds after a pin while the agent is
busy in a kernel cell — we need a separate push channel so the UI can
refresh on demand instead of polling at guessed intervals.

Shape:
  - Subscribers are asyncio.Queues attached by the `/api/notifications`
    SSE endpoint on connection; cleaned up on disconnect.
  - `broadcast(event)` pushes to all live queues. Safe to call from
    ANY thread — uses `loop.call_soon_threadsafe` to bridge to the
    asyncio event loop captured at startup.
  - Best-effort fan-out: a slow subscriber drops events (bounded queue)
    rather than blocking the producer.

Today's only producer is `auto_interpret` (caption_ready). Wire other
out-of-band events here as they come up — same envelope, same channel.
"""
from __future__ import annotations
import asyncio
from typing import Optional

# The main asyncio event loop, captured once at FastAPI startup so
# producers running on worker threads can schedule their pushes onto it
# via call_soon_threadsafe. Without this, queue.put_nowait() from a
# non-loop thread is a race.
_loop: Optional[asyncio.AbstractEventLoop] = None
_subscribers: list[asyncio.Queue] = []


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Capture the main event loop. Call once from `@app.on_event('startup')`."""
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue:
    """Register a subscriber queue. The SSE endpoint owns the queue and
    must unsubscribe on disconnect."""
    q: asyncio.Queue = asyncio.Queue(maxsize=128)
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    try:
        _subscribers.remove(q)
    except ValueError:
        pass


def broadcast(event: dict) -> None:
    """Push `event` to all live subscribers. Drops on a full queue (a
    slow client should not block other clients or the producer)."""
    # Wire-contract conformance (core/runtime/wire.py) — warn-once, never fatal.
    from core.runtime import wire
    wire.check(event, "notify")
    if _loop is None or not _subscribers:
        return

    def _push():
        for q in list(_subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    try:
        _loop.call_soon_threadsafe(_push)
    except RuntimeError:
        # Loop was closed (shutdown). Silent — best-effort fan-out.
        pass
