# Tools & the MCP layer

How the agent *acts*: every tool the Guide can call is one entry in a **registry behind a
single in-process MCP gateway**, rendered into the prompt by **one tiered presentation
policy**, and dispatched through **one hook-driven path** — not hand-wired per agent.

> Status: current as of 2026-07. This is the **maintained** reference; the design/evolution
> log lives in `misc/phase6_mcp_wrapping.md` (the wrapping migration), `misc/tool_presentation.md`
> (the tiering policy), and `misc/modularity_audit3.md` (the seam audit).

## Aims & principles

The tool catalog is a **cross-cutting input to every agent decision**: a change to how a
tool is named, typed, or described has platform-wide blast radius and erodes quality
*silently* if made carelessly. So the layer is built around a few hard invariants:

- **One registry, one gateway — never per-agent wiring.** Tools are declared once (as
  `@mcp.tool()` handlers on the `aba_core` in-process server) and reach the agent through
  the same `gateway.list_tools()` / `gateway.call()` channel as any external stdio MCP
  server. Adding a tool is one decorated function; no agent code changes.
- **The calling CONTRACT is identical across all prompt modes.** Param names, types,
  `required`, `enum`, `default` are *never* altered by presentation — a tool call that works
  in one mode works byte-for-byte in every mode. Only **prose** (docstrings, param
  `description`/`title`) is tiered, and **full prose is always recoverable via
  `describe_tool`**. This is the "shared agent inputs" change-discipline in
  `.claude/CLAUDE.md`.
- **Tier rendering lives in ONE policy table, consumed in ONE place.** Presentation is
  decided by `_POLICY[prompt_mode]` in `core/runtime/mcp/presentation.py`, read only by
  `gateway.list_tools(mode=…)`. You change a tier by editing its `_POLICY` entry — *never*
  by adding an `if compact:` branch. This keeps tuning one tier (the tight lean window) from
  silently degrading another (the production `standard` agent).
- **Steer the agent through a HOOK registry, not per-tool branches.** Guardrails
  (anti-fabrication vetoes, point-of-use steers, recipe-uptake nudges) register into an
  ordered, matcher-scoped Pre/Post hook registry; the dispatcher just drives it. A new
  guardrail is a registered function, not another `if name == …` in the dispatch path.
- **Every change to a shared agent input ships a BEHAVIORAL guard, not just a structural
  test** — contract-invariance across modes, the lean budget ceiling, and (for any
  production tier) tool-argument correctness in the regtest sweep. A byte/structural-only
  change is insufficient.

## The model

Four nouns, one flow:

- **Gateway** (`core/runtime/mcp/gateway.py`) — owns a background asyncio loop in a daemon
  thread; sync callers submit coroutines and block on the result (`_submit`,
  `gateway.py:47`). It holds every server `handle` and is the *only* producer of the tool
  catalog and the *only* call dispatcher.
- **`aba_core` server** (`content/bio/mcp_servers/aba_core/`) — an in-process `FastMCP`
  server hosting the whole bio tool catalog. Registered at startup with
  `expose_in_catalog=True, strip_prefix_in_catalog=True` (`main.py:288`) so its tools appear
  in the catalog at their **bare** names (`run_python`, `Skill`, …), preserving every
  recipe/prompt reference to a tool by unprefixed name.
- **Tool clusters** — `@mcp.tool()` handlers grouped by concern, each a
  `register_<cluster>(mcp)` called from `make_server()` (`server.py:55`): `simple`,
  `ctx_read`, `curation`, `discovery`, `file_io`, `plan_etc`, `run_exec`, `revisions`,
  `cells`, `entity_ops`, `feedback`, `jobs` (~80 `@mcp.tool()` registrations total). The
  decorator generates each tool's JSON schema from the function signature + docstring — the
  handler is the single source of truth for both impl and schema.
- **Presentation policy** (`presentation.py`) — a frozen `_POLICY` mapping `prompt_mode →
  ToolPresentation(docstring, param_prose)`; the only place tiers are defined.

```
 guide.py (turn)                      agent decides to call `run_python`
   │ list_tools(mode=spec.prompt_mode)         │ execute_tool(name, input_, ctx)
   ▼   ── the single catalog producer ──       ▼   _dispatch_tool
 gateway.list_tools ── presentation_for(mode)  is_inprocess_tool(name)? ──► gateway.call("aba_core:run_python")
   │  applies docstring/param-prose tiering       │  hooks.run_pre (veto/rewrite)      │ (sync→async bridge)
   ▼  (CONTRACT untouched)                         ▼  hooks.run_post (steer result)     ▼ memory transport
 tool schemas → LLM                              stash_ctx → aba_ctx_id (hidden arg)   aba_core @mcp.tool handler
                                                                                        │ peek_ctx(aba_ctx_id)
                                                                       delegate → content/bio/tools impl  OR  inline impl
```

## The gateway: one registry, two transports

The gateway abstracts *where a tool lives* behind a uniform surface. It runs a single
background event loop (`_ensure_loop`, `gateway.py:29`); `_submit` marshals every
coroutine onto it via `run_coroutine_threadsafe` and blocks the sync caller on the Future
(`gateway.py:47`). A `cancel_token` registered as an interrupter lets a user **Stop** cancel
the in-flight asyncio task, which surfaces as a structured `{status: "cancelled"}` the model
can react to (`gateway.py:65`).

Two handle types **duck-type** the same surface (`state`, `tools`, `call_tool`, `shutdown`)
so callers can't tell them apart:
- **stdio subprocess** (`server_handle.py`) — external MCP servers from `mcp/servers.yaml`
  (e.g. `lakefs`), added at startup (`start_all`) or at runtime (`add_server`, the
  materialization path for an `mcp_server`-archetype capability).
- **in-process memory transport** (`in_process.py`) — hosts `aba_core` *in the same
  process* via the SDK's memory streams. This is deliberate: bio tools take a `ctx` dict of
  non-serializable runtime objects (cancel_token, `threading.Queue` progress channel,
  Jupyter kernel session, an open SQLite handle) that cannot cross a process boundary. The
  payoff is the *shape* — declared schemas, uniform dispatch, structural readiness to move a
  tool out — without JSON-RPC-plumbing every runtime object (`in_process.py:1`).

Because ContextVars don't cross FastMCP's per-request task boundary, per-call `ctx` is
bridged out-of-band: the dispatcher `stash_ctx(ctx)` → a thread-safe store keyed by id,
injects a hidden `aba_ctx_id` arg (not advertised in any schema, so the model never sees
it), and the handler calls `peek_ctx(aba_ctx_id)` to recover it; `pop_ctx` in a `finally`
guarantees no leak (`core/runtime/tool_ctx.py`).

## `list_tools(mode)`: the single catalog producer + the tiering policy

`gateway.list_tools(mode, priority_tools)` (`gateway.py:204`) is the **only** function that
renders the catalog. It walks every *connected* handle, skips `expose_in_catalog=False`
handles, emits each tool in Anthropic wire shape `{name, description, input_schema}`, and
applies exactly two **prose-only** knobs from the policy:

- **`docstring`**: `full` (whole docstring) vs `summary` (first line via
  `_compact_description`) — but a tool in `priority_tools` always keeps its full docstring.
- **`param_prose`**: `keep` vs `drop` `description`/`title` from `input_schema` via
  `strip_schema_prose` (`presentation.py:79`), which strips prose *only from schema nodes* —
  a parameter literally *named* `title` (e.g. `run_python`'s) survives, because dropping it
  would break the contract.

The tiers (`_POLICY`, `presentation.py:52`): `full` keeps everything; `standard`
(grounded_guide — the production Opus/1M agent) summarizes docstrings but **keeps full param
prose** and is *never* cut to fit another tier's budget; `lean`/`lean_small` (small local
models on a ~40K window) *also* drop param prose to fit and lean on `describe_tool` to
recover it — an isolated decision that can never touch `standard`. `describe_tool`
(`discovery.py:123`) returns the full description + `input_schema` for any tool regardless of
how it was rendered this turn — the escape hatch that makes prose-tiering safe.

The consumer is one call in the turn assembly: `guide.py:556` passes
`mode=spec.prompt_mode` (validated to `full|standard|lean|lean_small` in
`core/runtime/agent.py:125`) and a `_PRIORITY_TOOLS` tuple whose membership is a runtime
tuning concern (`guide.py:547`).

## Dispatch + agent-steering via the hook registry

`execute_tool` → `_dispatch_tool` (`content/bio/tools/__init__.py:939`) routes by name:
`is_inprocess_tool(name)` → `mcp_call("aba_core:name", …)` through the gateway; a bare
`server:tool` name → the external-server path; otherwise the (now-empty) legacy `EXECUTORS`
dict. Around the call it drives the **hook registry** (`core/runtime/hooks.py`), which
adopts the Claude Agent SDK's Pre/Post/PostFailure *decision contract* — deny with a
model-facing reason, rewrite the input, or steer the output — but runs as plain in-process
calls, not a control subprocess:

- **`run_pre`** may return a `Deny` (rendered to a typed `{status:"blocked", executed:false}`
  result — a design-level block, *not* an error to work around) or a `Rewrite` of the input.
- **`run_post`** mutates the result in place (attaching steers/warnings); PostFailure hooks
  fire only on a failure result.

Guardrails register declaratively with a name-matcher (`content/bio/tools/__init__.py:731`):
a **pre-exec veto** on `run_python|run_r` blocks pseudoreplication-DE and
synthetic-data-after-a-failed-fetch *before execution*; post hooks add recipe-uptake nudges,
fetch-fail anti-fabrication steers, judgment guardrails, path-recovery hints, and
multi-panel-figure steers. These are point-of-use levers that land where soft prompt rules do
not — but they are a *registry*, not a branch pile in the dispatcher.

`ensure_capability`/skills **discovery** (how a tool/recipe gets provisioned or found) is
owned by [`bundle-and-content.md`](bundle-and-content.md); the **kernel execution** behind
`run_python`/`run_r` is owned by [`compute-execution.md`](compute-execution.md); the turn
loop that *decides* to call a tool and streams the result is
[`agent-loop.md`](agent-loop.md). This doc owns only the registry, the catalog, and the
dispatch/steer path between them.

## Key implementation references

| Where | What |
|---|---|
| `core/runtime/mcp/gateway.py` | the gateway: background loop + `_submit` sync↔async bridge (`:47`); `list_tools(mode)` the single catalog producer (`:204`); `call` dispatch (`:286`); `register_inprocess_server`/`add_server`; `is_inprocess_tool`/`is_mcp_tool` |
| `core/runtime/mcp/presentation.py` | the ONE tiering policy: `_POLICY[prompt_mode]` (`:52`), `presentation_for`, `strip_schema_prose` (contract-preserving prose strip) |
| `core/runtime/mcp/in_process.py` | in-process memory-transport handle; duck-types `ServerHandle`; per-call timeout policy |
| `core/runtime/mcp/server_handle.py` · `config.py` | stdio-subprocess handle; `servers.yaml` config |
| `core/runtime/tool_ctx.py` | non-serializable `ctx` bridge: `stash_ctx`/`peek_ctx`/`pop_ctx` + hidden `aba_ctx_id` |
| `content/bio/mcp_servers/aba_core/server.py` | `make_server()` factory; per-cluster `register_*` calls (`:55`) |
| `content/bio/mcp_servers/aba_core/tools/*.py` | the tool clusters (~80 `@mcp.tool()` handlers); `discovery.py:123` `describe_tool` |
| `content/bio/tools/__init__.py` | `_dispatch_tool` (`:939`); hook-guardrail definitions + registrations (`:731`+); re-exports of cluster impls; `TOOL_SCHEMAS`/`EXECUTORS` now empty (`:28`,`:213`) |
| `core/runtime/hooks.py` | the Pre/Post/PostFailure hook registry (`Deny`/`Rewrite`, `run_pre`/`run_post`, `deny_to_result`) |
| `guide.py:554` · `core/runtime/agent.py:61` | the catalog consumer (`list_tools(mode=spec.prompt_mode)` + `_PRIORITY_TOOLS`); `AgentSpec.prompt_mode` |
| `tests/test_tool_presentation.py` · `regtest/placement/` | behavioral guards: contract-invariance across modes; tool-argument correctness for `standard` |
| `misc/phase6_mcp_wrapping.md` · `misc/tool_presentation.md` · `misc/modularity_audit3.md` | design/evolution logs |

## Known gaps

- **Two inconsistent impl/wrapper patterns coexist.** Early clusters keep the implementation
  in `content/bio/tools/<cluster>.py` and the `@mcp.tool()` handler is a *thin wrapper* that
  imports and delegates (`simple.py`, `discovery.py`: `from content.bio.tools import …`);
  later clusters put the implementation **inline** in the `aba_core` cluster module
  (`jobs.py`, `entity_ops.py`). Both work, but "where does a tool actually live?" has two
  answers — a reader/maintainer hazard, and re-exports in `content/bio/tools/__init__.py`
  exist largely to keep the delegating pattern's cross-cluster imports working.
- **`content/bio/tools/__init__.py` fuses three roles.** It is simultaneously a re-export
  hub (cluster impls surfaced for advisors/tests), *the* tool dispatcher (`_dispatch_tool`),
  and ~600 lines of steering **guardrails** (regexes + hook functions). The guardrails
  migrated to the hook *registry* for structure, but their definitions still live in the
  dispatcher module rather than a `guardrails/` package — the file is a natural split point.
- **Stale counts in comments.** Cluster docstrings reference "46 bio tools" from the WU-1
  migration; the code today registers ~80 `@mcp.tool()` handlers (some env-gated
  experimental variants). Trust the decorators, not the comment.
- **In-process only, by necessity.** `aba_core` cannot move out-of-process without a
  serialization story for the `ctx` runtime objects; the memory transport preserves the
  *shape* for a future move but that move is not built.
