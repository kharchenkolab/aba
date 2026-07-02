# ABA — project notes

## What ABA is
ABA is an AI-orchestrated, **entity-oriented** workspace for biological data analysis: biologists and an AI agent ("Guide") collaborate through a shared, typed, persistent entity graph to carry out **long-term research projects** across the full cycle — data → analysis → results → conclusions → manuscript. Scientific outputs (datasets, runs, results, findings, claims) are first-class typed entities with provenance, so both humans and agents can focus at any level of abstraction and build on prior work — a research *partner*, not a notebook with a chatbot stapled on.

## Conventions
- this webapp will be used by biologists: the UI/UX should operate with familiar scientific entities and concepts
- build robust, modular architecture
- suggest opportunities to implement more general or flexible solution by engaging AI agents on different levels
- use short git commit messages with no signature

## Basic truths (where things live)
- Knowhows/recipes = `bp-*` skills in the `kharchenkolab/aba-recipe-pack` repo (`recipes/genomics/`); `search_skills` (BM25) indexes frontmatter only, so body-only edits are search-neutral.
- The system prompt composes bundle scopes system → installation → lab → user (narrowest-wins): universal default rules live in `backend/system_bundle/rules/` (this repo); site/lab/user rules go in an `aba-bundle-starter`-derived bundle's `rules/`.
- Live install runs from `~/data/aba/install` (code rsync'd from the working tree; recipe pack pulled via `RECIPES_REF`); `aba update` pulls code from `main`.
