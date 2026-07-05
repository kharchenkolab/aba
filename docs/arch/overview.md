# ABA architecture — the whole system on one page

What ABA is, the one contract everything hinges on, how the parts fit, and where to go
next. Start here, then follow a link.

> Status: current as of 2026-07. This is the **maintained** orientation; the conceptual
> essay is `misc/modularity2.md`, the architectural intent is `misc/arch3.md`, and the
> latest structural assessment (grades + open coupling) is `misc/modularity_audit3.md`.

## Aims & principles

ABA is **structured human–AI collaboration through a shared, typed, persistent
representation in the scientist's own ontology** — datasets, analyses, results, figures,
claims. Both the scientist and the agent read and write the *same* model of the work; it
is common ground made durable — not a chat transcript, not a pile of files, but a
mutually-editable shared external memory. Bioinformatics is the *instantiation*, not the
essence. The one imperative everything below serves: **keep the engine domain-neutral and
let the domain enter as content, never as core code.**

- **The entity model is a narrow waist.** Every part of the system talks to the science
  *only* through one typed graph (the contract). A change on one side of the waist does not
  ripple to the other. See [`entity-model.md`](entity-model.md).
- **The domain is content, not code.** `core/` never imports `content/` (CI-enforced); the
  bio vocabulary, recipes, and capabilities plug in through the bundle and registries. The
  test of success (arch3): *could `content/legal/` slot in beside `content/bio/` without
  touching the platform?* See [`bundle-and-content.md`](bundle-and-content.md).
- **Two shapes of modularity, two shapes of test.** Addable things (an entity type, a
  compute backend, a viewer) must be *local changes* — a **swap test**: the blast radius
  stops at one producer/consumer of the waist. Cross-cutting properties (provenance, access)
  must attach to *everything* uniformly — a **coverage invariant**, enforced in CI so a new
  producer can't silently skip one.

## The model — a waist, three planes, four cross-cuts

```
        ┌──────────── Reasoning plane (the agent) ────────────┐
        │   agent-loop.md · tools-and-mcp.md                  │
        └───────────────────────┬─────────────────────────────┘
                                │
   Contact plane ──────►  ┌─────┴──────┐  ◄────── Compute plane
   (the human surface)    │ THE WAIST  │          (doing the science)
   contact-surface.md     │ entity     │          compute-execution.md
                          │ model +    │          jobs-and-hpc.md · envs.md
                          │ provenance │
                          └─────┬──────┘
                                │
   cross-cuts attach uniformly to every plane and the waist:
   bundle-and-content · provenance · context-and-memory · deployment-and-access
```

- **The waist — the entity model.** The typed graph (entities, edges, focus) plus the
  provenance that rides on it. The single source of truth; a swappable store sits behind a
  read-port. → [`entity-model.md`](entity-model.md), [`provenance.md`](provenance.md).
- **Reasoning plane — the agent.** The durable turn loop that turns a message into
  streamed work, and the MCP tool registry it acts through. → [`agent-loop.md`](agent-loop.md),
  [`tools-and-mcp.md`](tools-and-mcp.md).
- **Compute plane — doing the science.** Running code now (kernels) and later (jobs/HPC),
  in integrity-safe environments. → [`compute-execution.md`](compute-execution.md),
  [`jobs-and-hpc.md`](jobs-and-hpc.md), [`envs.md`](envs.md).
- **Contact plane — the human surface.** The entity-oriented frontend and the uniform
  focus / highlight / reference / revise primitives. → [`contact-surface.md`](contact-surface.md).
- **Cross-cuts.** Knowledge/bundle, verifiability/provenance, context/memory, and
  deployment/access each attach to every plane. → [`bundle-and-content.md`](bundle-and-content.md),
  [`context-and-memory.md`](context-and-memory.md), [`deployment-and-access.md`](deployment-and-access.md).

## How a request flows (the planes in motion)

One user message, end to end:

1. **Contact → Reasoning.** The frontend posts to `/api/chat` (`main.py`). A **durable
   turn** starts as a background task (`guide.py`) that streams to a `TurnSink`; the SSE
   response is just a subscriber, so a disconnect doesn't kill the work. ([`agent-loop.md`](agent-loop.md))
2. **Compose the context.** The turn projects a *transient* context from durable state: the
   **EffectiveBundle** (rules/skills/capabilities, layered per deployment — [`bundle-and-content.md`](bundle-and-content.md))
   plus the **Manifest** (focus cards over current entities — [`context-and-memory.md`](context-and-memory.md)).
   A reset re-projects; it never re-derives.
3. **Act through tools.** The agent calls MCP tools from the gateway registry
   ([`tools-and-mcp.md`](tools-and-mcp.md)) — searching skills, ensuring a capability, then
   running code.
4. **Compute.** `run_python`/`run_r` execute on a pooled kernel ([`compute-execution.md`](compute-execution.md))
   in an integrity-safe environment ([`envs.md`](envs.md)); heavy work is submitted as a
   job and its completion **re-enters** the turn loop ([`jobs-and-hpc.md`](jobs-and-hpc.md)).
5. **Promote into the waist.** Path-agnostic harvest registers figures/tables as **entities**,
   each stamped with a typed `derivation` + `actor` — you cannot mint an un-provenanced one
   ([`provenance.md`](provenance.md)). Promotion (pin / "this is a result") is bilateral
   ([`entity-model.md`](entity-model.md)).
6. **Back to Contact.** Results stream to the shelf; the scientist points at them (focus,
   pin, revise) and that curation flows back into step 2's context next turn.

## The document set

| Layer | Docs |
|---|---|
| **Start** | this page |
| **Waist** | [`entity-model.md`](entity-model.md) · [`provenance.md`](provenance.md) |
| **Reasoning** | [`agent-loop.md`](agent-loop.md) · [`tools-and-mcp.md`](tools-and-mcp.md) |
| **Compute** | [`compute-execution.md`](compute-execution.md) · [`jobs-and-hpc.md`](jobs-and-hpc.md) · [`envs.md`](envs.md) |
| **Contact** | [`contact-surface.md`](contact-surface.md) |
| **Cross-cuts** | [`bundle-and-content.md`](bundle-and-content.md) · [`context-and-memory.md`](context-and-memory.md) · [`deployment-and-access.md`](deployment-and-access.md) |

The house style for these docs — and the glossary of shared terms (waist, plane, entity,
promotion, bundle, exec record, …) — is in [`README.md`](README.md).

## Key implementation references

| Where | What |
|---|---|
| `backend/main.py` | the FastAPI composition root: mounts the routers (`core/web/routers/*` + `content/bio/web`), wires `lifespan.py` + the project-pin middleware, and hosts the Reasoning-plane entries (`/api/chat`, `/api/turns/*/resume`, `/tool_result`) that import `guide` |
| `backend/core/web/` | the platform web layer: domain-neutral `routers/*`, `deps.py` (project pin), `middleware.py`, `artifacts.py` |
| `backend/guide.py` | the agent turn loop (`stream_response`) — the Reasoning plane |
| `backend/core/` | the domain-neutral engine: `graph/` (waist), `runtime/` (turns/LLM/MCP), `exec/` + `jobs/` (Compute), `bundle/` + `skills/` + `catalog/` (knowledge), `recovery/` + `summarize/` (context) |
| `backend/content/bio/` | the bio content pack: entity types, tools, lifecycle, cards — the domain, plugged in |
| `backend/system_bundle/` | the `system`-scope bundle: universal rules + core skills |
| `frontend/src/` | the Contact plane (platform shell / lib seams / bio domain / viewers) |
| `install/` | the deployment shells (mac / linux / cluster / OOD) — see [`deployment-and-access.md`](deployment-and-access.md) |

## Known gaps (architecture-level)

These are cross-cutting; each subsystem doc carries its own local gaps. Full detail +
grades in `misc/modularity_audit3.md`.

- **`guide → core.jobs` down-edge (up-edge dissolved).** The Compute→Reasoning *up*-edge is
  gone (Item 1): a finished job re-enters through `core/reasoning_port` (guide registers the
  handler at import), so `core.jobs` no longer imports `guide` — enforced by `check_seam` rule 4.
  What remains is the forward *down*-edge: `guide` imports a concrete job-submit function
  (compute-neutrality, deferred). ([`jobs-and-hpc.md`](jobs-and-hpc.md), [`agent-loop.md`](agent-loop.md))
- **Entry-point monoliths (decomposition in progress).** `main.py` (was ~3000 loc, now ~2066)
  is being split: the domain-neutral platform routes now live in `core/web/routers/*` (admin,
  jobs, settings, memory, threads, projects, turns, misc), with `lifespan.py` + project-pin
  middleware in `core/web`. Remaining in main: the Reasoning-plane entries (which import guide)
  and the bio-coupled routes (→ `content/bio/web`, Item 2A.4). `guide.py`'s `stream_response`
  (~1089 loc) is still the un-split monolith (Item 2B). Seams are CI-guarded (route-table
  snapshot + pin-coverage). Plan: `misc/item2_decomposition.md`.
- **Store read-port burn-down.** The typed read-port exists and is ratcheted, but some
  modules still reach raw SQL (multi-hop lineage walks have no edge-port yet). ([`entity-model.md`](entity-model.md))
- **Identity is a reserved seam.** Single-user today (`human:local`); real multi-user
  attribution + scope enforcement await the access layer. ([`deployment-and-access.md`](deployment-and-access.md))
