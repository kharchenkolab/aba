# ABA architecture notes

Succinct, principle-first orientations to the parts of ABA, for developers working on the
system. Each doc explains **one coherent part**: what it's *for*, the invariants that
shape it, how it composes, and where the code is. These are **explanation, not API
reference** — a map + compass, not a manual. [`envs.md`](envs.md) is the exemplar.

> Status: index current as of 2026-07. Each doc carries its own status line; these docs
> are the **maintained** orientation.

## The model in one breath

ABA is **structured human–AI collaboration through a shared, typed, persistent
representation in the scientist's own ontology** — datasets, analyses, results, figures,
claims. That shared **entity model is the narrow waist** (the contract). Three **planes**
orbit it, each a producer/consumer of the waist — **Compute** (doing the science),
**Reasoning** (the agent), **Contact** (the human surface) — and four **cross-cuts**
attach uniformly to all of them — **knowledge/bundle**, **verifiability/provenance**,
**context/memory**, **deployment/access**. The engine is domain-neutral; the domain
(`content/bio`, the recipe pack) enters as **content, never core code**. See
[`overview.md`](overview.md) for the full picture.

## Index

**Start here**
- [`overview.md`](overview.md) — the whole system on one page: the waist, the three planes, the four cross-cuts, and how a request flows through them.

**The waist — the entity model (the contract everything hinges on)**
- [`entity-model.md`](entity-model.md) — the typed graph (entities/edges), the store + read-port, per-project DB binding, the entity-type registry, and **promotion** (substrate → entity).
- [`provenance.md`](provenance.md) — exec records, the typed `derivation`+`actor` on every entity, revisions, reproduce/revert/export — the *verifiability* cross-cut, realized in the waist.

**Reasoning plane — the agent**
- [`agent-loop.md`](agent-loop.md) — `/api/chat` → guide → durable turns (resume/reconnect, plan+approval gates, deferred tools), and LLM integration (runtimes, streaming, prompt caching).
- [`tools-and-mcp.md`](tools-and-mcp.md) — the in-process MCP gateway as a registry, the tool clusters, and the tiered catalog-presentation policy.

**Compute plane — doing the science**
- [`compute-execution.md`](compute-execution.md) — the kernel pool (Python/R), `run_python`/`run_r`, path-agnostic artifact harvest, interactive-vs-background routing.
- [`jobs-and-hpc.md`](jobs-and-hpc.md) — background jobs, the `BatchSubmitter` protocol, the weft lanes (local / shared-fs cluster / **detached** `site=` targeting), continuation, HPC discovery.
- [`compute-sites.md`](compute-sites.md) — connecting external compute: the `WeftAdapter`/`SitePort` doorway, `weft-sites.yaml`, the science-first **Settings → Compute** tab (probe → propose → confirm), the Guide's connect tools, and the shared-controller weft-ui mount.
- [`envs.md`](envs.md) — environments & provisioning: the base/overlay/isolated tiers, `ensure_capability`, ABI integrity, GPU/accelerator.

**Contact plane — the human surface**
- [`contact-surface.md`](contact-surface.md) — the frontend architecture (platform shell / lib seams / bio domain / viewer registry) and the uniform interaction primitives (focus / highlight / reference / revise).

**Cross-cuts — attach uniformly to every plane**
- [`bundle-and-content.md`](bundle-and-content.md) — the scope-chain (system → installation → lab → user → `EffectiveBundle`), skills/recipes, capabilities, and **the content-pack seam** (`core ↛ content`).
- [`context-and-memory.md`](context-and-memory.md) — the agent's context as a transient projection of the durable model; history compaction; memory-wipe recovery.
- [`deployment-and-access.md`](deployment-and-access.md) — the deployment-agnostic core, config topology, and the reserved identity/access seam.
  - [`settings-reference.md`](settings-reference.md) — **generated** catalogue of every `ABA_*` setting (env, type, default, `weft_fate`, `reduction`); produced from the `config.py` registry by `scripts/gen_settings_reference.py`. Not hand-edited.

## Writing an arch doc

A **one-sitting, principle-first orientation** to one part of ABA, for a developer new to
that area. After reading it they should know what it's *for*, the few *invariants* that
shape it (and the failure each prevents), how the pieces *compose*, and *where in the
code* to look.

**Template (follow [`envs.md`](envs.md)):**
1. **Title + a one-line "what this is."**
2. **Status blockquote** — `current as of <YYYY-MM>`, "the maintained reference".
3. **`## Aims & principles`** — what it's for + the **load-bearing invariants as imperatives, each tied to the failure it prevents.** Lead here; derive the rest.
4. **`## The model`** — the core abstractions (nouns) and how they relate; a small ASCII/mermaid diagram if the shape isn't obvious.
5. **`## <approach>`** (1–3 sections) — how it actually works (verbs), principle-first.
6. **`## Key implementation references`** — a `where │ what` table mapping code paths to responsibilities (the jump-off points).
7. **`## Known gaps`** — honest limits + designed-but-unbuilt.

**Principles:**
- **Principle over enumeration.** State the *one* imperative and derive the design from it (`envs.md`: "integrity-safe by construction" → every rule follows). Explain *why it must be so*, not a feature tour.
- **Verify, don't assume.** Check every claim against the code; when a design log and the code disagree, **trust the code**. Unverifiable → a marked gap, never a guess.
- **Today, not the roadmap.** Describe what's true now; put designed-but-unbuilt in *Known gaps*.
- **Point, don't duplicate.** Cite `path:line` and link sibling arch docs — the doc is the durable orientation. **Own your topic; where another doc owns something you touch, summarize in one line and link.**
- **Succinct is an altitude, not a word count** (~150–250 lines). Longer → it's becoming a reference; cut or split. One coherent part per doc.
- **Dense, present-tense, declarative.** Name invariants precisely; the reader should be able to *act* — extend safely, find the seam — after one read.

## Glossary (shared vocabulary — use these terms)

- **Entity model / the waist** — the shared, typed, persistent graph both scientist and agent read and write; the *contract*, not the store.
- **Entity** — a typed object the scientist reasons about and can point at (dataset, analysis, result, figure, claim). First-class, provenance-bearing.
- **Substrate** — raw inputs, intermediates, logs, un-promoted outputs; *not* entities until promoted.
- **Promotion** — the bilateral gate elevating substrate into the entity graph (scientist "pins", agent "this is a result"); `demote` is the inverse.
- **Plane** — Compute / Reasoning / Contact; each a producer or consumer of the waist. A change in one plane must not ripple into another.
- **Cross-cut** — knowledge/bundle, verifiability/provenance, context/memory, deployment/access; each must attach to *every* entity/producer uniformly (an invariant, not a convention).
- **Guide** — the agent orchestrator: the Reasoning-plane turn loop (`guide.py`).
- **Bundle / `EffectiveBundle`** — layered content (system → installation → lab → user) composed into the effective rules, skills, capability catalog, refsources, and settings.
- **Content-pack seam** — the invariant that `core/` never imports `content/`; the domain plugs in via registries, hooks, and bundle projection.
- **Skill / recipe** — a `bp-*` knowhow the agent discovers (`search_skills`) and follows; lives in the recipe pack.
- **Capability** — a provisionable tool/library/env, materialized on demand via `ensure_capability`.
- **Exec record** — the provenance unit: a thin row + JSON sidecar capturing a run's code + environment; the substrate of reproduce/revise/recover.
- **Manifest** — the per-turn projection of entity + focus state into the agent's context (the focus cards).
