---
name: manage-references
description: Find / fetch / register / resolve reusable reference data (genomes, indices, annotations) in the shared reference store
when_to_use: A task needs a genome assembly, transcriptome, annotation (GTF/GFF), or a pre-built aligner index — to use one, acquire one, or share one with the lab
keywords: [reference, genome, assembly, index, annotation, gtf, gff, fasta, transcriptome, fetch, igenomes, ensembl, ncbi, star index, salmon index, bowtie, reference genome]
---

# Manage references

Reusable reference data (genomes, transcriptomes, annotations, and the indices
built from them) lives in the **shared reference store** — separate from a
project's own outputs, deduplicated, and sharable across projects/users. The
loop is **resolve-or-acquire**; always check before fetching or building.

1. **`find_reference(organism, role, assembly)`** — does it already exist? Use
   natural names (matching is normalized: `human` / `Homo sapiens` /
   `homo_sapiens` all hit; `genome` == `Genome`). If found, skip to step 4.
2. If missing, **acquire** — cheapest first:
   - **`fetch_reference(provider, organism, assembly, role)`** — pull a standard
     assembly/annotation or, ideally, a **pre-built index** from a known provider
     (`aws-indexes`, `ncbi`, …). **Prefer fetching a pre-built index over
     building one.**
   - else **build** it (`run_python`/`run_r`), then register the output;
   - or, for a reference already on the cluster, **`register_reference(path,
     mode="link")`** — adopt it in place, no copy;
   - or, if it's behind auth / only the user has it, **ask the user** to upload
     or point at a path, then register.
3. **`register_reference(path, organism, role, assembly[, scope, mode])`** —
   keep a fetched/built file as a reusable reference. Scope defaults by signal
   (fetched → group, run-derived → project, else personal); pass `scope` to
   override. For a derived index, pass `derived_from` so lineage is recorded.
4. **`resolve_reference(id | organism/role/assembly)`** — get a local path **and
   pin the run-lock** before a run reads the reference, so the run records
   exactly which version it used.

**Scopes are install-dependent — check before you promise a tier.** A single-user
install has only **project** + **personal** (here `personal` *is* the shared store,
reusable across all the user's projects); **group**/**institution** exist only on a
cluster/OOD deployment. `register_reference` and `promote_reference` return
`available_scopes` — use it: only promote to a tier that's actually there, and
report the scope from the result, not your assumption. A `promote` to an
unconfigured tier returns `status:"noop"` (it did NOT move) — say so honestly; on
single-user, `personal` already gives cross-project reuse, so there's usually
nothing to promote.

**Conventions:** `role` is a controlled vocab — `genome`, `transcriptome`,
`gtf`, `gff`, `fai_index`, `star_index`, `salmon_index`, `bowtie2_index`, … (see
`knowhow/refs/NAMING.md`). `describe_reference` inspects facets + lineage;
`promote_reference` shares a project/personal reference more widely (institution
is curator-only — a denied write falls back to your personal store with a hint).
