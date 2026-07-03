# ABA — project notes

## What ABA is
ABA is an AI-orchestrated, **entity-oriented** workspace for biological data analysis: biologists and an AI agent ("Guide") collaborate through a shared, typed, persistent entity graph to carry out **long-term research projects** across the full cycle — data → analysis → results → conclusions → manuscript. Scientific outputs (datasets, runs, results, findings, claims) are first-class typed entities with provenance, so both humans and agents can focus at any level of abstraction and build on prior work — a research *partner*, not a notebook with a chatbot stapled on.

## Conventions
- this webapp will be used by biologists: the UI/UX should operate with familiar scientific entities and concepts
- build robust, modular architecture
- suggest opportunities to implement more general or flexible solution by engaging AI agents on different levels
- use short git commit messages with no signature

## Basic truths (where things live)
- Recipes + know-how (references) live in the `kharchenkolab/aba-recipe-pack` repo: `recipes/<domain>/` (executable `bp-*`/named recipes) and `knowhow/` (advisory method/decision + reference docs). They're brought in at install / `aba update` into `$ABA_HOME/installation/` — that deployed copy is what the server reads; the repo is the source, so edit + PR there (branch work ships once it lands on `main`, pulled via `RECIPES_REF`). `search_skills` (BM25) indexes frontmatter only, so body-only edits are search-neutral.
- System prompts / rules compose bundle scopes system → installation → lab → user (narrowest-wins): universal always-on rules (e.g. `behavior.md`) live in `backend/system_bundle/rules/` (this repo); site/lab/user rules go in an `aba-bundle-starter`-derived bundle's `rules/`.
- Deployments update via `aba update` (ABA code from `main`); install paths/hosts are per-deployment, not recorded here.
