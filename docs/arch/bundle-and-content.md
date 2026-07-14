# Bundle & content

How the domain enters ABA as **content, never core code** — layered, curator-editable
soft config composed into one `EffectiveBundle` that every consumer reads.

> Status: current as of 2026-07. This is the **maintained** reference.

## Aims & principles

The engine is **domain-neutral**: nothing in `core/` knows what a "figure" or a "scanpy
QC" is. The domain — bio entity types, recipes, capability catalog, reference providers,
prompts — is **content that plugs in**. The load-bearing test (arch3): *could a hypothetical
`content/legal/` slot in beside `content/bio/` without touching the platform?* Every
invariant below serves that test.

- **The content-pack seam: `core/` NEVER imports `content/`.** CI-enforced by
  `scripts/check_seam.sh` (three rules — no `import content`, no bio entity-type name as a
  string literal, no bio-named module import). Dependencies that *must* run the other way
  are **inverted through core registries**, never a direct import: content registers a
  callable / hook / prompt / spec into `core`, and `core` calls it by name with a safe
  fallback. A missing pack degrades, never crashes.
- **Content is layered and curator-editable, no redeploy.** Site policy, lab recipes, and a
  user's preferences are *files in a bundle directory*, composed at startup by a
  scope-count-agnostic algorithm. Adding a scope is appending an entry to a list; editing
  a rule is editing a markdown file.
- **Narrowest wins, floors are additive.** A narrower scope *overrides* the same-named
  rule / skill / capability; but *required* rules, the R-base package list, and policy
  text are **additive** — a lab extends the floor, it can't delete the platform's
  non-negotiables.
- **Every consumer reads the `EffectiveBundle` — never a hardcoded default.** The composed
  bundle is the single source of truth for policy, skills, capabilities, refsources, and
  settings. A consumer that reaches past it to an on-disk default is a bug.

## The model

**Scope chain → composer → `EffectiveBundle` → projection into live registries.**

```
resolve_scopes()          load_bundle()                 content/bio projects it
[system, installation,  → EffectiveBundle           →   ├─ skills  → core.skills registry
 lab, user]  (broadest-    (policy, rules, skills,       ├─ catalog → core.catalog (per-project)
 first ScopeBundle list)   catalog, refsources,          ├─ refsources → core.data.refsources
                           settings, provenance)         └─ BioPack → guide.py (prompts/tools)
```

- **Scope** — one bundle directory. `resolve_scopes` (`core/bundle/scope_resolver.py:193`)
  produces an *ordered, broadest-first* list of `ScopeBundle`s from env/site.yaml:
  `system` (the repo's `backend/system_bundle/`, only non-optional scope) → `installation`
  (the deployment's own bundle at `$ABA_HOME/installation`, where the imported recipe pack
  lands) → `lab` (group-shared) → `user` (`~/.aba/bundle`). The module is
  **scope-count-agnostic** (`scope_resolver.py:9`): new scopes are appended, no other code
  changes shape.
- **`EffectiveBundle`** (`core/bundle/loader.py:94`) — the composed result: `policy_text`,
  `required_rules` + `overrideable_rules`, `skills`, `catalog` + `r_base_specs` +
  `collection_dirs`, `refsources`, `settings`, and a `Provenance` recording *which scope
  contributed each item and what it shadowed*. Composed **once** at process start and
  cached (`core/bundle/active.py:34`, `get_bundle()`); `reload_bundle()` re-resolves for
  tests / the `aba bundle inspect --reload` admin path.
- **Projection** — a content pack turns `EffectiveBundle` fields into live core registry
  state. This is the seam's other half: core defines the registry, content fills it.

## Composition: precedence rules

`load_bundle` (`core/bundle/loader.py:758`) walks the chain generically. Each subsystem has
one precedence rule, chosen by whether it is a *floor* (additive) or a *choice* (override):

| Field | Rule | Direction |
|---|---|---|
| `AGENTS.md`/`CLAUDE.md` policy | **concatenate** (per-scope blocks, `## <label> policy`) | broadest-first, additive |
| `rules/required/*.md` | **additive** — every scope's copy, even same-name | broadest-first |
| `rules/*.md` (loose) | **override by filename**, narrowest wins | narrowest-first |
| `skills/**` | **override by skill name** + `agents:` filter + `disable_recipes` | narrowest-first |
| `catalog/*.yaml` capabilities | **override by name**, narrowest wins | narrowest-first |
| `catalog/*.yaml` `packages:` (R-base) | **extend** (dedup, order-preserved) | broadest-first, additive |
| `catalog/<dir>/collection.yaml` | **register** each collection dir | — |
| `knowhow/refsources/*.yaml` | **override by `provider:` name**, narrowest wins | narrowest-first |
| `settings.yaml`/`.json` | **deep dict-merge**: scalars narrowest-wins, lists extend | broadest→narrowest |

Capability specs carry a **`role`** (`library | tool | viewer | converter` — weft rewrite #11,
`core.catalog.capability_role`): explicit, else derived from `archetype` (cli/mcp/pipeline →
tool, else library). Roles say how an entry is USED, orthogonal to how it's provisioned.
`viewer`-role entries carry a declarative `viewer:` block (mode/extensions/entity_types/
launcher/priority) and project LIVE into the viewers registry
(`core/viewers/registry.viewers_from_catalog` — an external viewer's registration is catalog
data; canvas/modal rows stay `viewers.yaml`, they're frontend components). `converter`-role
entries declare `converter: {from, to}` — behind `core.catalog.converters_for` ("what converts
X?") and role-aware `ensure_capability` responses. Format example:
`tests/fixtures/installation/catalog/roles_example.yaml`.

Catalog dispatch is by *content*, not filename: a `catalog/*.yaml` with a `capabilities:`
list contributes specs; one with `packages:` contributes R-base — so the system seed and a
future imported pack compose identically (`loader.py:641`). The loader is **defensive**: a
malformed scope (unparseable YAML, unreadable file) is skipped with a provenance warning,
never raised — one broken scope can't down the stack (`loader.py:17`).

## Skills, recipes, and the knowhow tier

A **skill** is a markdown file with YAML frontmatter (`core/skills/loader.py`, `SkillSpec`
at `:29`). The frontmatter *is* the contract; the body is procedure the agent reads.
`when_to_use` / `avoid_when` drive selection; `capabilities_needed` names the libs/CLIs the
procedure uses (the skill→capability linkage that closes the discovery funnel);
`requires_tools` gates runnability; `agents:` restricts a skill to named agents.

- **Two tiers, folder-driven — never per-file** (`loader.py:_skill_tier`, `:360`):
  `skills/core/**` → `visibility='always'` (rendered in the system prompt **every turn**);
  everything else → `'local'` (retrieval-gated). A generated lab recipe outside `core/`
  **cannot promote itself** into the always-on tier — the tier is stamped from the folder,
  not the frontmatter.
- **`recipe` vs `knowhow`** — directory-derived `kind` (`loader.py:459`): the `skills/`
  tree is executable recipes; the `knowhow/` tree is broad decision guides (advice). A
  knowhow is *read, not executed*, so its projection strips `requires_tools` — the read
  gate never hides a decision guide (`skills/loader.py:148`).
- **`search_skills` is BM25 over frontmatter ONLY** (`skills/loader.py:_doc_text`, `:431`
  → `search_skills`, `:524`): name (hyphen- and space-split), aliases, description,
  `when_to_use`, keywords, `capabilities_needed`, domain. **The body is not indexed** —
  body-only edits are search-neutral. `read_skill` / the `Skill` tool (`invoke_skill`,
  `:404`, with `$ARGUMENTS` substitution + bundled `resources`) return the body.
- **The discovery funnel:** `search_skills` surfaces a recipe → its `capabilities_needed`
  names libraries → `ensure_capability` fills any gap. `recipes_for_capability` (`:553`)
  drives the run_python/run_r uptake nudge (code with a library a recipe covers, but didn't
  read it → remind).

Tool-surfacing of skills (the in-prompt catalog, tiering, `search_skills` presentation) is
owned by [`tools-and-mcp.md`](tools-and-mcp.md) — this doc owns their *content and
composition*.

## Capabilities

A **capability** is a provisionable tool/library/env — a first-class entity in the same
store as figures/results (`core/catalog/catalog.py`), its full spec (name, version,
archetype, `provisioning`, scope, status; `capabilities.md §4.2`) living in `metadata`.
`ensure_capability(name)` (`content/bio/tools/discovery.py:702`) resolves the spec and
`materialize()`s it by provisioning kind. Because capabilities are **per-project** entities,
the seed can't run at import (no project DB yet) — see the projection seam below.
`propose_capability` drafts a discovery hit through the `proposed → published` lifecycle
(auto- or ask-approval). **Collections** (`core/catalog/collections.py`) are file-backed,
process-global capability bundles (e.g. biomni), searchable without being seeded as entities
so a large catalogue doesn't bloat every project DB.

Capability **provisioning mechanics** (pip into the project overlay, the ABI anchor, conda
tools env, GPU variants) are owned by [`envs.md`](envs.md) — this doc owns only the
*catalog entity and its bundle composition*.

## The projection seam

`content/bio` is the *only* code that projects `EffectiveBundle` into live `core` registries,
in three shapes matched to each field's lifecycle:

1. **Skills — eager, at import.** `content/bio/skills/__init__.py:register_from_bundle()`
   (called at module import, `:58`) reads `EffectiveBundle.skills` and registers each as a
   `SkillSpec` in the in-process `core.skills` registry — **no second discovery**. It
   projects broadest→narrowest so a narrower scope's alias-override registers *after* the
   base it hijacks (`:30`).
2. **Capabilities — lazy, per project DB.** `content/bio/capabilities/__init__.py`
   registers `load_seed` as a *seed provider* (`register_seed_provider(load_seed)`, `:77`);
   `core.catalog._ensure_seeded` (`catalog.py:34`) runs it the first time a project's
   catalog is queried and found empty, keyed by DB path — dependency inversion, content
   registering into core. Collections are process-global, so
   `register_collections_from_bundle()` runs at import (`:75`).
3. **Hooks/tools/prompts — the `ContentPack` singleton.** `BioPack`
   (`content/bio/pack.py:29`) implements the `core.runtime.content_pack.ContentPack`
   protocol. `main.py` startup calls `set_active_pack(BIO_PACK)` +
   `register_hooks()` (`main.py:37`); the orchestrator queries `active_pack()` for
   `prompts()` / `tools()` / `cards()` / `execute_tool()` and **never imports
   `content.bio`** (`core/runtime/content_pack.py:37`). Swapping the pack in `main.py` is
   the whole of adding a vertical.

Skills projection and catalog projection are **the exact same shape** — both flow from
`EffectiveBundle` into a core registry — which is what makes the seam a *property* rather
than a convention. The remaining inversion channels are `core/services.py` (value
providers — e.g. `language_sniffer`), `core/prompts` (named prompt text), and `core/hooks`
(fire-and-forget events).

## Key implementation references

| Where | What |
|---|---|
| `core/bundle/scope_resolver.py` | `resolve_scopes` → ordered `ScopeBundle` chain; scope-count-agnostic; site.yaml + env resolution |
| `core/bundle/loader.py` | `load_bundle`, `EffectiveBundle`, all `_compose_*` (precedence rules), `Provenance` |
| `core/bundle/active.py` | `get_bundle()` (cached-once), `reload_bundle()`, `get_resolution()` |
| `core/bundle/cli.py` | `aba bundle inspect` — resolution + composition dump |
| `core/skills/loader.py` | `SkillSpec`, `register_skill_spec`, `search_skills` (BM25 over frontmatter), `read_skill`/`invoke_skill`, tiering |
| `core/catalog/` | capability entity API (`register_capability`, `resolve_capability`, `propose_capability`, `register_seed_provider`), collections |
| `content/bio/skills/__init__.py` | `register_from_bundle` — eager skills projection |
| `content/bio/capabilities/__init__.py` | `load_seed` seed provider + collections projection |
| `content/bio/pack.py` · `core/runtime/content_pack.py` | `BioPack` / `ContentPack` protocol + `active_pack()` |
| `core/services.py` · `core/prompts` · `core/hooks` | the three inversion channels (value / prompt-text / event) |
| `scripts/check_seam.sh` | CI seam enforcement (via `.github/workflows/invariants.yml`) |
| `backend/system_bundle/` | the system scope's content (rules/, skills/core/ + vendor_skills/, knowhow/refsources/, settings.yaml) |

**Related seams:** entity-type registration is owned by
[`entity-model.md`](entity-model.md); scope isolation / access by
[`deployment-and-access.md`](deployment-and-access.md); provisioning mechanics by
[`envs.md`](envs.md); tool surfacing by [`tools-and-mcp.md`](tools-and-mcp.md).

## Known gaps

- **The system scope ships no `catalog/` today.** `backend/system_bundle/` has no
  `catalog/` dir at HEAD, so on a bare repo the **capability catalog, R-base list, and
  collections are empty** — they arrive only from the imported recipe pack in the
  *installation* scope (`$ABA_HOME/installation`, populated by the install-time import). The
  code comments in `content/bio/capabilities/__init__.py` and `.../skills/__init__.py`
  describing `system_bundle/catalog/` (`bio_seed.yaml` + `r_base.yaml` + `biomni/`) and
  `content/bio/library/` are **stale/aspirational** — neither path exists; skills live
  directly under `system_bundle/skills/`. A recipe-pack-less server is *incomplete*.
- **`institution` → `installation` rename deferred.** The scope is authored, labelled, and
  documented as "installation" but the chain still names it `"institution"`
  (`scope_resolver.py:254`, `SkillSpec.layer`) — a cosmetic split until the rename lands.
- **Frontmatter parser duplicated with divergent error semantics.**
  `bundle/loader.py:_parse_frontmatter` is lenient (returns `{}` for both "none" and
  "malformed"); `skills/loader.py:_split_frontmatter` **raises** on malformed. The bundle
  path parses once to discover, then the projection re-parses via `_spec_from_parsed` — two
  parsers that can disagree on a broken file.
- **`refstore` reaches private `scope_resolver` internals.** `core/data/refstore.py:71`
  imports `_read_site_yaml`, `_resolve_group`, `_expand_placeholders`, `_user_id` — a
  cross-module reach into underscored helpers, coupling ref-store to resolver internals.
- **Capability specs are projected as unvalidated dicts.** `CatalogEntry.spec`
  (`loader.py:71`) is a raw dict; `load_seed` passes it straight to `register_capability`
  with no schema check — a malformed spec surfaces only at `ensure_capability` time, not at
  composition.
