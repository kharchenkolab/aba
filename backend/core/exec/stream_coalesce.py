"""Coalesce kernel iopub.stream chunks into UI-flush-sized bursts.

Raw Jupyter iopub messages can arrive every few milliseconds during a chatty
cell (R progress bars, tqdm, install logs). Forwarding each one over SSE
would flood the wire AND give the browser nothing useful to render — chunks
that small accumulate too fast for the eye to follow.

The coalescer batches push() calls and flushes when EITHER:
  - accumulated bytes ≥ `flush_bytes` (default 10 KB), OR
  - elapsed since last flush ≥ `flush_interval_s` (default 1.0s)

Whichever fires first. A no-output quiet period emits nothing — frontend
"last activity Xs ago" handles the liveness UI from event timestamps.

Stdout and stderr accumulate independently — each flush emits at most one
chunk per stream that has unflushed bytes. The on_flush callback receives a
dict `{stream, text, bytes_total, elapsed_s}` so the SSE layer doesn't need
to know coalescer internals.

The coalescer holds no thread-affinity itself; the caller (the kernel exec
worker thread) drives push() and the periodic flush check synchronously.
"""
from __future__ import annotations
import time
from typing import Callable, Optional


class Coalescer:
    def __init__(
        self,
        *,
        flush_bytes: int = 10240,
        flush_interval_s: float = 1.0,
        on_flush: Optional[Callable[[dict], None]] = None,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.flush_bytes = max(1, flush_bytes)
        self.flush_interval_s = max(0.01, flush_interval_s)
        self.on_flush = on_flush
        self._now = now_fn
        self._start_ts = self._now()
        self._last_flush_ts = self._start_ts
        # Per-stream buffers — flushed independently so a stderr-only burst
        # doesn't wait for stdout to also accumulate.
        self._buf: dict[str, list[str]] = {"stdout": [], "stderr": []}
        self._pending_bytes: int = 0
        # Lifetime byte counters per stream, surfaced in the flush event so
        # the UI can show "12.3 KB stdout" without re-summing every chunk.
        self._total: dict[str, int] = {"stdout": 0, "stderr": 0}

    def push(self, stream: str, text: str) -> None:
        """Buffer one stdout/stderr chunk. Triggers a flush if either cap is hit."""
        if not text:
            return
        if stream not in ("stdout", "stderr"):
            stream = "stdout"
        self._buf[stream].append(text)
        self._total[stream] += len(text)
        self._pending_bytes += len(text)
        if self._pending_bytes >= self.flush_bytes:
            self.flush(reason="bytes")
            return
        if self._now() - self._last_flush_ts >= self.flush_interval_s:
            self.flush(reason="interval")

    def maybe_flush(self) -> None:
        """Time-only flush check — call this from the caller's poll loop when
        no new data arrived but elapsed time should trigger a flush of any
        pending bytes. No-op when buffers are empty."""
        if self._pending_bytes == 0:
            return
        if self._now() - self._last_flush_ts >= self.flush_interval_s:
            self.flush(reason="interval")

    def flush(self, *, reason: str = "manual") -> None:
        """Emit any pending bytes per stream. Resets the time clock even when
        buffers are empty (used as a "no-op heartbeat" by callers that want
        consistent timing)."""
        for stream in ("stdout", "stderr"):
            if not self._buf[stream]:
                continue
            text = "".join(self._buf[stream])
            self._buf[stream] = []
            if self.on_flush is not None:
                self.on_flush({
                    "stream": stream,
                    "text": text,
                    "bytes_total": self._total[stream],
                    "elapsed_s": round(self._now() - self._start_ts, 3),
                    "reason": reason,
                })
        self._pending_bytes = 0
        self._last_flush_ts = self._now()

    @property
    def bytes_total(self) -> dict[str, int]:
        return dict(self._total)
