# The agent loop

How one user message becomes an agent turn — the Reasoning-plane lifecycle that
streams text, calls tools, survives a client disconnect, and ends — plus the
swappable LLM integration underneath it.

> Status: current as of 2026-07. This is the **maintained** reference; the design/
> evolution log lives in `misc/durable_turns_plan.md` (the disconnect-survival
> redesign) and `misc/agent_guidance.md` / `misc/prompt_quality_test_plan.md`.

## Aims & principles

A turn is a long-lived, model-driven state machine that must keep running while
clients come and go, stay honest about what it sent the model, and stay
domain-neutral so the science plugs in as content. So:

- **Decouple turn lifetime from the HTTP request.** The loop runs as a background
  asyncio task that emits into a per-turn event log; the SSE response is only a
  *subscriber*. A tab switch, network blip, or reload unsubscribes — it never
  cancels the work. Prevents the stranded-turn failure: a disconnect mid-tool
  leaving a Turn in `executing_tools` forever with a permanent frontend spinner
  (`misc/durable_turns_plan.md`, the C-1 motivation).
- **Persist every transition; the DB is the truth, not memory.** A `Turn` row is
  checkpointed on every state change, so a restart can reap what was in flight
  and a resumed conversation reconstructs from durable state, not a live object.
- **The model is swappable.** Every provider/SDK detail sits behind the
  `LLMRuntime` protocol and a model→spec catalog; the orchestrator never imports
  an SDK. Prevents hard-coding one vendor so a local small model, the Claude
  Agent SDK, or a scripted fake can't be dropped in per-agent.
- **The API contract is enforced structurally, never hoped for.** Every
  assistant `tool_use` gets exactly one `tool_result`; UI-only blocks are
  stripped before the wire. A crash/resume/summary-cut can't produce a 400.
- **Domain-neutral core.** The loop speaks entities, tools, and turns; bio enters
  only through the content pack (`active_pack()`). The domain is content, not
  core code — see the honest residual coupling in *Known gaps*.
- **The agent's context is a projection, re-projected each turn.** History
  compaction and the focus/manifest projection are OWNED by
  [`context-and-memory.md`](context-and-memory.md); the loop consumes them.

## The model

The loop's nouns, and where each lives:

- **`Turn`** (`core/runtime/turn.py`) — the durable per-turn record: a `TurnState`
  enum (`GENERATING → EXECUTING_TOOLS → {AWAITING_USER | AWAITING_TOOL_RESULT} →
  SUMMARIZING → DONE|FAILED`), the pending-work blobs (`pending_tool_ids`,
  `pending_approval`, `pending_deferred`, `pending_user_signal`), usage, and the
  `run_id`. Serialized to the `runs` table.
- **`checkpoint(turn)`** (`core/runtime/checkpoint.py`) — one idempotent upsert
  per transition; also home to the reaper (`reap_stale_turns`) and the
  message-log repair that keeps history API-clean.
- **`TurnSink`** (`core/runtime/turn_sink.py`) — an append-only event log per
  `run_id`: an in-memory tail (ring of 1000) **and** a JSONL on disk, a process
  registry, and the SSE consumer. Reconnect replays from the tail (or disk); the
  sink outlives the task so a late reattach still works.
- **`turn_executor`** (`core/runtime/turn_executor.py`) — the durable-task
  wrapper: `start_turn()` spawns the loop as a detached task and `_drain()` pumps
  its yielded dicts onto the sink, re-binding the project inside the task.
- **`LLMRuntime`** (`core/runtime/llm_runtime.py`) — the protocol for **one
  model-driven phase**: `run_turn(req, tool_executor, halt_on_tools)` async-yields
  *primitive* events (`TextDelta`, `ToolUseStart`, `ToolResult`, `TurnDone`,
  `TurnHalt`). The orchestrator loops over phases and translates events to SSE.
- **`AgentSpec`** (`core/runtime/agent.py`) — the declaration that binds a turn:
  `model`, `runtime` (direct/sdk/openai/fake), `prompt_mode`, `tool_allowlist`,
  summary budgets. The **model catalog** (`core/llm_catalog.py`) maps a
  user-picked model → its spec.

```
POST /api/chat ─► start_turn() ─► asyncio task ══► guide.stream_response()  (durable)
      │                              │ _drain: push        outer while:
      ▼                          TurnSink ◄──────────┐   GENERATING ─► run_turn (one phase)
 StreamingResponse ◄─ stream_from_sink   in-mem tail │      │ TextDelta/ToolUseStart/ToolResult
      (a subscriber)        │            + JSONL      │      ▼
 reconnect: GET /stream?since=N ─► replay + live      └── EXECUTING_TOOLS ─► loop
 resume:    POST /resume ─────────► new Turn continues the thread
                                     halts ─► AWAITING_USER (plan/clarify/approval)
                                              AWAITING_TOOL_RESULT (deferred job)
```

## The request flow & the durable task

`POST /api/chat` (`main.py:944`) binds the project from the request body, builds
the `stream_response(...)` async generator, and hands it to
`turn_executor.start_turn` (`main.py:979`). `start_turn` allocates the sink and
spawns `_drain` as a background task; the handler returns a `StreamingResponse`
over `stream_from_sink(sink, since=0)` — a *subscriber*, not the producer. The
task captures the project id synchronously and re-binds it via `projects.bind(pid)`
inside `_drain` (`turn_executor.py:49`) so a concurrent request for another
project can't repoint the process-global DB mid-turn (the 2026-06 cross-project
corruption race).

**Reconnect** is `GET /api/turns/{run_id}/stream?since=<seq>` (`main.py:1728`):
each SSE frame carries a monotonic `seq`; the client persists its last seq and
reattaches from there. `replay_since` serves the gap from the in-memory tail, or
falls back to scanning the JSONL when the client is further behind than the tail
holds (`turn_sink.py:160`). If the process restarted and the sink is gone, the
endpoint rehydrates the whole event stream from disk, or (if the Turn is terminal
in the DB) emits a synthetic `done`.

**Resume** is `POST /api/turns/{run_id}/resume` (`main.py:1849`): a turn parked in
`AWAITING_USER` is continued by appending the user's reply (or, for an approval,
writing the held tool's real `tool_result`) and spawning a **fresh** Turn on the
same thread — the model sees the completed pair plus the new message and picks up
naturally. Deferred background jobs re-enter through their own webhook
(`/tool_result/{tool_use_id}`) and `settle_deferred_job` — the job continuation
that re-drives `stream_response` is OWNED by [`jobs-and-hpc.md`](jobs-and-hpc.md).

## The turn loop & its gates

`guide.stream_response` is the orchestrator: a single outer `while True` that
drives one `Turn` through its states. Each iteration:

1. **Prepare history.** `effective_history` (compaction, OWNED by
   [`context-and-memory.md`](context-and-memory.md)) runs off-loop in a thread,
   then `history_prep.ensure_tool_pair_completeness` canonicalizes it: dedupe
   duplicate `tool_result`s, fill *middle* orphans in-memory, drop *backward*
   orphans (`core/runtime/history_prep.py:153`). A `[llm-prep]` fingerprint of
   the exact message list is logged for the `[llm-sent]` match (below).
2. **Run one phase.** `make_runtime(spec)` picks the runtime;
   `runtime.run_turn(req, _tool_executor)` streams primitive events which the loop
   translates to the SSE vocabulary (`delta`, `tool_start`, `tool_progress`,
   `tool_chunk`, `tool_result`, `manifest`, …). `_StreamCompleted` carries the
   assembled assistant blocks, which are persisted with `append_message`.
3. **Dispatch tools.** The `_tool_executor` closure (guide.py:800) owns the
   *gates*, then delegates the actual call to `active_pack().execute_tool()` on a
   worker thread. Dispatch mechanics + the MCP gateway are OWNED by
   [`tools-and-mcp.md`](tools-and-mcp.md).
4. **Checkpoint + branch** on `stop_reason` / halt signal.

The four **halts** — each a distinct `TurnHalt` reason the runtime raises and the
loop maps to state:

- **plan** (`present_plan`) — validates + persists a `plan` entity (with
  provenance actor/derivation, see [`provenance.md`](provenance.md)), emits the
  plan card, then halts *after* the ack (a well-formed tool pair stays in
  history). → `AWAITING_USER`.
- **clarify** (`ask_clarification`) — halt-after with the question. → `AWAITING_USER`.
- **approval** — a tool whose `approval_policy` needs consent halts *before*
  dispatch; the held tool's `tool_use` is left unresolved and its input parked in
  `pending_approval`. The resume endpoint runs (or rejects) it. → `AWAITING_USER`.
- **deferred** — a background `run_python` submits a job and returns `{deferred}`;
  the turn parks in `AWAITING_TOOL_RESULT` with `pending_deferred`, and the job's
  terminal webhook settles the held tool_use later.

**Crash recovery** is the reaper's job, not the request path's: at startup /
project-switch, `reap_stale_turns` marks any `GENERATING/EXECUTING_TOOLS/
SUMMARIZING` turn `FAILED` (unless its task is live in *this* process —
`turn_sink.live_run_ids()`), and repairs trailing orphan `tool_use`s in the
message log so the next request is API-clean without a live history scan.

## LLM integration & pluggable runtimes

`core/llm.py` is the provider seam. `_RealStream` is the one place a request
reaches the Anthropic SDK, and it owns:

- **The single history→API transform.** `history_prep.api_messages` reduces every
  message to `{role, content}` and strips UI-only blocks (e.g. the `attachments`
  chip) — the only sanctioned boundary; a validity-guard test asserts no
  disallowed block type ever escapes (`history_prep.py:49`).
- **Three prompt-cache breakpoints.** The system prompt is sent as a **stable
  cached prefix** (`cache_control: ephemeral`) plus an **uncached dynamic tail**
  (the per-turn BM25 recipes catalog + compute-env line), so a per-intent change
  invalidates only the small tail, not the ~26K prefix. `cache_control` also
  marks the **last tool** and the **last message block** — 3 of the 4 available
  breakpoints (`core/llm.py:77-112`). On `oauth_cc` credentials a byte-exact
  Claude Code marker is prepended as the first (uncached) system block.
- **A replayable raw-request dump.** The exact kwargs (structured system, tools,
  messages — with cache_control) are written to `ABA_RAW_REQUEST_DIR`
  (default `/tmp/aba_llm_sent`), so `client.messages.create(**json.load(...))`
  reproduces the call byte-for-byte. A `[llm-sent]` line logs the SHA of the
  message envelope; it must match guide's `[llm-prep]` SHA — if they differ,
  something between prep and the wire mutated the payload.
- **Credentials & clients.** `_credential_mode()` selects `apikey` / `oauth` /
  `oauth_cc`; OAuth bearers are auto-refreshed from a rotating store; clients are
  cached per `(mode, auth)` over an HTTP/2 httpx client so streams multiplex
  instead of paying a TLS handshake per call.

**The runtime seam.** `DirectAPIRuntime` (`core/runtime/llm_runtime_direct.py`)
wraps `core.llm.make_open_stream`: `open_and_consume_stream` opens the stream,
emits `TextDelta`s, retries transient errors (`is_transient` in
`llm_errors.py`: 429/5xx/529/timeouts) with cancel-aware exponential backoff
(`min(2**attempt, 8)`, up to 4 tries), then assembles the assistant blocks and
dispatches tools. Three sibling runtimes target the same protocol, picked per
`AgentSpec.runtime` (env `ABA_FAKE_SESSION` / `ABA_RUNTIME_OVERRIDE` win): the
**Claude Agent SDK** (`llm_runtime_sdk.py`, native MCP + its own retry/cache),
**OpenAI-compatible** (`llm_runtime_openai.py`, self-hosted vLLM/Qwen3 with
`<think>`-tag stripping), and **fake** (`llm_runtime_fake.py`, scripted JSONL for
tests/eval). The **model catalog** (`llm_catalog.py`) is the user-facing knob: a
project picks a model (`current_model_for_project`), and `spec_for_model` derives
the spec — re-resolved at the turn boundary so a Settings change takes effect on
the next turn with no restart.

## Key implementation references

| Where | What |
|---|---|
| `guide.py` (`stream_response`, 256-1345) | the orchestrator: history prep, per-phase drive, halt gates, SSE translation, checkpointing |
| `core/runtime/turn.py` | `Turn` dataclass + `TurnState`; row (de)serialization |
| `core/runtime/checkpoint.py` | `checkpoint`, `load_turn`, `reap_stale_turns`, orphan/message repair, `settle_deferred_job` |
| `core/runtime/turn_sink.py` | `TurnSink` (in-mem tail + JSONL), registry, `replay_since`/`rehydrate`, `stream_from_sink` |
| `core/runtime/turn_executor.py` | `start_turn` (spawn background task) + `_drain` (pump → sink, re-bind project) |
| `core/runtime/llm_runtime.py` | the `LLMRuntime` protocol + primitive event types + `RuntimeRequest`/`SystemSpec` |
| `core/runtime/llm_runtime_direct.py` | `DirectAPIRuntime` + `open_and_consume_stream` (retry/backoff, block assembly, tool dispatch, halt envelopes) |
| `core/runtime/llm_runtime_{sdk,openai,fake}.py` | swappable runtimes (Claude Agent SDK / OpenAI-compatible / scripted) |
| `core/runtime/agent.py` | `AgentSpec`, `make_runtime`, `resolve_spec_for_turn`, `run_advisor_one_shot` |
| `core/llm.py` | provider seam: `_RealStream` (3 cache breakpoints, raw dump), credential modes, client cache |
| `core/llm_catalog.py` | the model→spec catalog behind the per-project model selector |
| `core/runtime/{history_prep,llm_errors}.py` | history canonicalization; transient-error classification + friendly messages |
| `main.py` (`/api/chat`, `/api/turns/*`) | chat entry, reconnect stream, resume, deferred-result webhook, cancel |

## Known gaps

- **`stream_response` is still a single ~1089-line function.** The durable-turn
  *infrastructure* (Turn/checkpoint/sink/executor wrapper) and the per-*phase*
  runtime (`DirectAPIRuntime.run_turn`) are extracted and live — but the
  orchestration itself (the outer loop, the gate branches, history prep, and the
  event→SSE translation) remains inline in guide.py, not a `TurnExecutor` class.
  `turn.py` and `agent.py` both flag the full state-machine extraction (an
  `Agent(spec).run()` driving off `TurnState`) as deferred to a later pass. Treat
  the loop body as as-built inline code, not a clean object.
- **The loop carries a one-way compute coupling (down-edge only).** The
  Compute→Reasoning *up*-edge is dissolved: a finished job re-enters through
  `core/reasoning_port` (guide registers the handler at import), so
  `core/jobs/continuation.py` no longer imports `stream_response` — enforced by
  `check_seam.sh` rule 4 (modularity_audit3 Item 1, Phase 1). What remains is the
  *down*-edge: `guide.py:27` still imports the concrete `submit_python_job` from
  `core.jobs.runner` (should submit through an interface — Item 1 / Phase 2b).
  Two smaller content reaches remain direct: the plan-entity actor
  (`agent_actor_for_thread`, guide.py:812) and background-submit params
  (`bg_submit_kwargs`, guide.py:921, entangled with the down-edge). The
  plan-orientation preamble no longer reaches content privates — it now goes
  through `core/services` (`plan_orientation_preamble`, Phase 2a). A few
  bio-shaped type literals also persist (`"result"`, `("figure","view")` at
  guide.py:179/593). The content-pack seam holds for tools/prompts/hooks/services
  but is not yet total here.
- **`RuntimeRequest.max_tokens` is not threaded through the direct path.** The
  cap is read from `ABA_MAX_TOKENS` (default 16000) inside `core/llm.py`, so the
  `max_tokens=8192` the orchestrator sets on the request is inert on
  `DirectAPIRuntime`. Harmless today (env default is the effective value) but a
  seam that will surprise anyone tuning per-turn budgets from the spec.
- **Deferred-tool timeout is recorded but not enforced by a watchdog.**
  `pending_deferred.timeout_s` is persisted; resolution depends on the job's
  terminal webhook (or the reaper) firing. A job that neither completes nor is
  reaped can leave a turn parked in `AWAITING_TOOL_RESULT` — see
  [`jobs-and-hpc.md`](jobs-and-hpc.md) for the job-side lifecycle.
