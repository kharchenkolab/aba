---
name: summarize-thread
description: Roll up a long thread into the current findings + open questions
when_to_use: Thread is getting long (>30 turns) or user wants a checkpoint summary
---

# Summarize thread

Read the thread's recent context (findings, claims, pinned figures).
Emit a structured summary: what we've established, what's still open,
what to do next. Lives in the thread's metadata so future turns see
the summary instead of the full transcript.
