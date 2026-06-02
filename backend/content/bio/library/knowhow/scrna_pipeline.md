# scRNA-seq — principles + where the canonical flow lives

The executable single-cell QC + first-pass clustering pipeline is the
**`scrna-qc-clustering`** recipe (find it with `search_skills` / `Skill(skill=...)`).
That is the ONE canonical flow — load → QC → filter → normalize → HVG → PCA →
UMAP → leiden → markers, with the plots interleaved. Use it for a single sample;
do not re-derive the steps here.

## Multiple samples: keep them SEPARATE — do NOT naively concatenate

This principle is broader than QC — it governs every multi-sample task, so it
lives here rather than in one recipe.

For scRNA-seq, several samples/donors/runs are **multiple datasets, not one
matrix**. Concatenating raw count matrices into a single AnnData and treating it
as one sample is **wrong** — it confounds batch with biology, and the joint
PCA/clustering then just separates cells by sample (batch), not by cell type.

- "Register them **together**" / "as one dataset" = ONE dataset entity (or
  collection) spanning the per-sample files — NOT `sc.concat`/`merge` into a
  single matrix. Keep the per-sample files; register the bundle (a directory is fine).
- Combine samples into one object **only** as the explicit first step of a
  **batch-aware integration** that models the sample/batch covariate — Harmony
  (`harmony-integration`), scVI (`scvi-integration`), or conos
  (`conos-integration`). The "merge" in those recipes is always immediately
  followed by batch correction; never lift it out as a standalone step.
- One sample → the `scrna-qc-clustering` recipe. Two+ samples to analyze jointly
  → an integration recipe, not a concatenate-then-cluster shortcut.

**Honor the requested scope — do NOT upsell integration.** This guardrail exists
to stop *naive concatenation*, not to push integration. If the user asks to
process a single sample (or "the second sample"), run the single-sample recipe on
exactly that sample and stop. The fact that other samples exist is NOT a reason to
propose batch correction / joint clustering. Integration is a separate step the
user requests explicitly ("integrate the controls", "joint analysis") — wait for
that ask; don't pivot to it or make the plan about it.
