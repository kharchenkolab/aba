# Context & memory

How the agent's working context is built each turn — as a **transient projection of the
durable entity model**, not a store in its own right — and how that context stays bounded,
survives a reset, and remembers across sessions.

> Status: current as of 2026-07. This is the **maintained** reference.

## Aims & principles

The agent has no memory of its own. Everything it "knows" at turn *N* is **re-derived from
durable state** — the entity DB, the message log, the bundle, the compute env. The context
is a *view*, and the discipline that follows from that one fact:

- **Re-project, don't re-derive.** A context reset — server bounce, new session, a wiped
  in-process cache, even a lost DB — must **rebuild** the context by projecting the durable
  record, never by *re-running the work* that produced it. The DB is authoritative; the
  message log is authoritative; the on-disk recovery archive mirrors both. Memory-wipe
  recovery (below) is this principle operating at the extreme: entities + provenance
  re-project from sidecars, and only a `reproduce` re-executes — deliberately, from an exec
  record. Failure prevented: silent divergence, where a restarted agent "remembers" a state
  the durable record doesn't hold.
- **Compose from independent contributors, no special-casing.** The per-turn context is the
  concatenation of a handful of pure functions of durable state — a cross-thread entity
  **sidebar**, the **focus card**, the **thread card**, the **bundle**-projected system
  blocks, a **compute-env** cue, and the **compacted history**. Each reads durable state
  directly and knows nothing of the others. Failure prevented: a change in one input (a new
  focus type, a lab rule override) rippling into another.
- **Compaction is non-destructive; `msgs_grow` is NOT an invariant.** History compaction
  shrinks only what the *LLM sees* this turn — the durable message log is never mutated.
  Because a resume rehydrates a **bounded, compact** context that then stays roughly flat,
  the count of messages the model receives is decoupled from the count of messages on disk.
  Failure prevented: unbounded context growth on one side, loss of the authoritative
  transcript on the other.

## The model

A single per-turn object, the **Manifest** (`core/manifest/types.py`), carries the projected
inputs — a `FocusCard`, an optional `ThreadContext`, and `policy_text`. It carries *inputs,
not the final string*: the rendered system prompt is assembled by concatenating the manifest
projection with the other contributors. That assembly happens in one place,
`guide.py:622`:

```
system = sidebar_text + focus_text + thread_text + stable_sys
```

The six independent contributors and where each is projected from:

| Contributor | Source (durable) | Built by |
|---|---|---|
| `sidebar_text` — cross-thread entity snapshot | entity DB | `render_project_sidebar` → bio's `render_bio_project_sidebar` |
| `focus_text` — the focused entity's card + policy | entity DB + adaptive policy | `build_manifest` → `render_focus_preamble` |
| `thread_text` — pinned evidence/claims for this thread | entity DB | registered thread-context renderer |
| `stable_sys` / `dynamic_sys` — identity, rules, skills, memory, capabilities, recipes | the `EffectiveBundle` | `build_system` (bio) |
| compute-env cue (into `dynamic_sys`) | live node/Slurm probe | `compute_env.context_line()` |
| `llm_history` — the compacted transcript | the message log | `effective_history` |

The Manifest is also serialized (`Manifest.to_dict`) and streamed to the client as a
`manifest` SSE event (`guide.py:668`) so the right-rail drawer can show what the agent is
seeing — **the UI side of the manifest is owned by [`contact-surface.md`](contact-surface.md);
this doc owns the agent-context side.**

Underneath, a **filesystem recovery archive** (`projects/<pid>/`: `entities/*.json`,
`edges.jsonl`, `threads/*.jsonl`, `.exec/*.json`, `project.json`) mirrors the DB so the
projection can be rebuilt even after total DB loss. The **bundle** — the other big context
input — is a cross-cut of its own, owned by
[`bundle-and-content.md`](bundle-and-content.md); here it is simply one contributor.

## Per-turn manifest assembly

`build_manifest` (`core/manifest/assembler.py:240`) composes the Manifest for one Guide (or
advisor) turn. It resolves the focused entity and dispatches to a **per-type focus-card
builder** registered by content at import time (`register_card_builder`, `assembler.py:24`);
unregistered types fall back to `_generic_card`. The assembler itself never imports content —
it holds the slot and calls whatever was registered, the same registry inversion used for the
thread-context renderer, the policy provider, and the sidebar. `_call_builder`
(`assembler.py:34`) degrades gracefully to a 1-arg builder so a new kwarg (e.g.
`focus_member_id`) doesn't flag-day every pack.

**Policy injection** is a second slot the manifest carries but content fills: `_policy_for`
(`assembler.py:77`) asks the registered provider (bio's adaptive lifecycle layer) for
guidance keyed on the focus entity's type (or `"workspace"` when nothing is focused). The
result rides in `Manifest.policy_text` and is appended by `render_focus_preamble`
(`assembler.py:207`), which also emits the deixis rule ("*this figure* always resolves to the
focused entity") added after a live focus regression.

The **cross-thread sidebar** (`content/bio/cards/sidebar.py`) is the deterministic,
no-LLM snapshot of project-wide state the agent might reference across threads: datasets with
paths + layout hints (`sidebar.py:45`), threads with the current one marked (`sidebar.py:72`),
and registry-driven curation **counts** for any type declaring `capabilities.sidebar: count`
(`sidebar.py:87`). Shared cross-thread state lives here — queryable and regenerated every
turn — so the thread's own chat history stays purely the conversational record.

`render_focus_preamble` matches its pre-refactor output byte-for-byte so the **prompt-cache
prefix** doesn't shift; the split into a stable (cached) system block and a small dynamic
(uncached) tail — and the cache breakpoints themselves — are owned by
[`agent-loop.md`](agent-loop.md).

## History compaction

`effective_history` (`core/summarize/rolling.py:27`) is the single entry point, run
off-thread from the async loop (`guide.py:700`) because it may make a synchronous LLM call.
It is a two-tier funnel and **operates on a copy** — the durable message log is untouched:

- **Tier 1 — deterministic pruning** (`core/summarize/pruning.py:154`, no LLM). Older
  `tool_result` *contents* are replaced with a one-line stub (`[earlier] tool | ok |
  plots=[…]`), and chatty pure-text inter-step narration older than `K_TEXT_KEEP` is dropped;
  `tool_use` blocks, user messages, and the last `K_TOOL_KEEP` results stay verbatim. A
  frozen `_ALWAYS_KEEP_TOOLS` set (`pruning.py:39`) keeps navigation/skill results whole
  regardless of age. Typical: 50KB → ~12KB with the conversational shape intact; most threads
  never need Tier 2.
- **Tier 2 — neutral-voice budget summary** (`core/summarize/budget_summary.py:247`). Fires
  *only* when pruning still leaves the messages above a char budget (default 400K ≈ 100K
  tokens; `_threshold`, `budget_summary.py:36`). It folds the oldest contiguous block into a
  single third-person `<summary>` message, keeping the last `TAIL_KEEP` (default 20) verbatim,
  and **caches the summary per thread** in `thread_summaries`, regenerating incrementally.
  It is intentionally per-*thread* (no cross-thread bleed), third-person (no agent-voice
  mimicry loop), and run on a decoupled Haiku-class model (`_summary_model`,
  `budget_summary.py:137`) so it can't inherit the chat model's rate-limit budget. `_TIER2_DIAG`
  counters (`budget_summary.py:163`) make a non-firing Tier 2 diagnosable.

The `lean` primary spec passes a tighter `budget_chars` + `tail_keep` to demand much earlier
Tier-2 summarization inside a small vLLM window; `None` preserves production behavior
bit-for-bit. Budget precedence (`guide._summary_budget`): the dedicated override
knob `ABA_HISTORY_SUMMARY_BUDGET_OVERRIDE_CHARS` (>0; a registered lazy setting —
deliberately distinct from the global-threshold var, which only fills the no-pin
fall-through) > the spec's pinned `summary_budget_chars` (grounded_guide pins 100K)
> the global default — guarded by `tests/test_summary_budget_precedence.py`. The prior workspace-keyed agent-voice summarizer is **deleted** (it caused a
voice-mimicry loop); `rolling.py` is now just the funnel.

## Memory-wipe recovery

The archive that makes "re-project, don't re-derive" true at the limit. See
[`provenance.md`](provenance.md) for the exec-record engine that the recovered records feed.

- **Scribe** (`core/recovery/scribe.py`) — a per-process background thread. Every
  entity/edge/message/project mutation in `core/graph/*` enqueues a typed event
  (`EntityUpserted`, `EdgeOp`, `MessageAppended`, …); a ~1 s tick drains and writes per-project
  sidecars + jsonl logs (`_drain`, `scribe.py:223`). The DB stays authoritative — the scribe
  only mirrors, coalescing per-entity and accepting "a few seconds of loss on a crash" for
  simplicity. `ABA_RECOVERY_DISABLED=1` swaps in a no-op scribe (`scribe.py:592`).
- **Walker** (`core/recovery/walker.py:238`, `aba-recover recover`) — the inverse. Rebuilds
  a `project.db` from `project.json` + `entities/*.json` + `edges*.jsonl` + `threads/*.jsonl`
  + `.exec/*.json`, replaying edge snapshots then the live tail and honoring message `clear`
  sentinels. Torn last lines are skipped, not fatal. Cross-host imports normalize absolute
  paths (`_normalize_path`) and auto-rename on a pid collision. **`backfill_project`
  (`walker.py:511`) goes DB → FS** to repair drift after a missed-hook bug is fixed; the
  **drift detector** (`core/recovery/drift.py`) compares the live DB against what the walker
  *would* reconstruct, so silent archive gaps surface while the DB is still fine.
- **Compatibility report** (`core/recovery/report.py:189`) — after a walk, scans the imported
  project for host-side references (entity types, recipes, capabilities, tools) it can't
  resolve, writing `recovery_report.json` for the UI banner. Version skew comes from the
  `aba_commit`/`aba_version` fingerprint the scribe stamps into `project.json`.
- **By-title view** (`core/recovery/by_title.py`) — human-readable `*-by-title/` symlink
  dirs over the canonical ID-named storage. A pure **derivation** of the DB (rebuilt by
  `--refresh-symlinks`), never a source of truth.

On a reset the entities *and their exec records* re-project from these files; the agent's
context is then rebuilt exactly as any turn builds it. A `reproduce` re-*derives* a result by
re-executing its exec record — that engine is owned by [`provenance.md`](provenance.md); this
doc's concern is only that the record survives the wipe to be reproduced from.

## Typed memory

Cross-session notes the agent keeps deliberately (`core/memory/typed_files.py`). Each memory
is one `.md` file with `name`/`description`/`type` frontmatter under `projects/<pid>/memory/`;
the four types (`user`, `feedback`, `project`, `reference`; `typed_files.py:34`) mirror Claude
Code's task-shaping distinction, so they live in core, not bio. `write_memory`
(`typed_files.py:112`) regenerates a one-line `MEMORY.md` index; `memory_index_block`
(`typed_files.py:189`) injects that index — index only, not bodies — into the per-turn prompt
via the bundle-side `memory` block, so the agent sees what's available and loads a body on
demand with `read_memory`.

## Key implementation references

| Where | What |
|---|---|
| `core/manifest/assembler.py` | `build_manifest`; registered card/thread/policy/sidebar slots; `render_focus_preamble` |
| `core/manifest/types.py` | `Manifest`/`FocusCard`/`ThreadContext`; `to_dict` (drawer sidecar) |
| `content/bio/cards/sidebar.py` | cross-thread project snapshot (datasets, threads, curation counts) |
| `content/bio/prompts/build.py` | bio's `build_system` — the bundle-projected system blocks (`memory`, rules, skills) |
| `guide.py:567`–`622` | per-turn assembly: manifest → focus/thread/sidebar → concat with system + compute-env |
| `core/summarize/rolling.py` | `effective_history` — the prune→summary funnel (off-thread) |
| `core/summarize/pruning.py` | Tier-1 deterministic pruning; `_ALWAYS_KEEP_TOOLS` |
| `core/summarize/budget_summary.py` | Tier-2 per-thread neutral-voice summary; cache; `_TIER2_DIAG` |
| `core/memory/typed_files.py` | typed markdown memory + always-loaded index |
| `core/recovery/scribe.py` · `walker.py` | FS mirror (DB→FS) and rebuild (FS→DB) + backfill |
| `core/recovery/report.py` · `drift.py` · `by_title.py` · `cli.py` | compat report · drift detector · symlink view · `aba-recover` |
| `core/prompts.py` | prompt-provider registry (e.g. `thread_summary`) |

## Known gaps

- **The top-level concat is hardcoded, not a registry of contributors.** `guide.py:622`
  hand-assembles `sidebar + focus + thread + system` in a fixed order. The sub-projections
  are properly inverted (each registered), but the final composition is not — a new
  context contributor means editing `guide.py`, not registering a slot. The principle
  ("compose from independent contributors") is honored one level down but not at the top.
- **The cross-thread sidebar covers 5 of 15 entity types.** It surfaces datasets and threads
  in detail plus counts for the three types declaring `sidebar: count` (result, claim,
  finding). The other ten (analysis, figure, table, note, narrative, plan, reference, cell,
  capability, workspace) never enter the cross-thread snapshot — the agent learns of them only
  by focusing one or calling `list_entities` explicitly. Counts are registry-opt-in, but
  *detailed* surfacing is still hardcoded to two types.
- **Typed memory lives outside the recovery archive.** The scribe mirrors entities, edges,
  messages, and exec records; it does *not* enqueue `projects/<pid>/memory/*.md`. On same-host
  recovery this is harmless (the files sit in the project dir), but a cross-host import that
  copies only the recovery archive would **silently lose typed memory** — it is neither in the
  sidecars nor in the compatibility report's reachability scan.
