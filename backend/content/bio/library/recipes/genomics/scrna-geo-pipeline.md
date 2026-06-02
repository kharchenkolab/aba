---
name: scrna-geo-pipeline
description: End-to-end scRNA-seq pipeline from a GEO accession to annotated clusters. Orchestrates three Skills in sequence — fetch the processed matrices, run scanpy QC + clustering on one sample, then annotate the clusters. Use when the user names a GEO accession (GSE/GSM) and asks for a "full pipeline", "end-to-end", or "from data to cell types" workflow. NOT for raw FASTQ (use fetch-sequencing-fastq + bp-raw-data-processing first).
when_to_use: User names a GEO study (e.g. GSE192391) and wants the whole pipeline — fetch processed data, QC + cluster ONE sample, name the cell types. The accession is in the user's request OR available in the conversation context. Single sample only (multi-sample integration is a different pipeline).
avoid_when: Raw FASTQ input (no processed matrices available); multi-sample integration / batch correction needed; user wants only one stage (just QC, or just annotation) — in that case invoke that sub-Skill directly.
requires_tools: [Skill, run_python]
capabilities_needed: [scanpy, leidenalg]
keywords: [scrna, scRNA-seq, single cell, GEO, end-to-end, full pipeline, fetch and process, annotated clusters, cell types, GSE, GSM]
produces: [counts/, processed.h5ad, umap_clusters.png, cluster_annotations.csv]
domain: genomics
---

# End-to-end scRNA-seq pipeline (GEO → annotated clusters)

This recipe is an **orchestrator**: it does no analysis itself. It tells you which sub-Skills to invoke in sequence, in which order, with what inputs. Each step is a separate `Skill(...)` call. **Do NOT inline the whole pipeline in run_python.** Each sub-Skill carries its own correct APIs, parameters, and gotchas — invoke them individually.

## The pipeline

1. **Fetch processed matrices from GEO.** Invoke:
   ```
   Skill(skill="fetch-geo-processed-matrices", args="<accession>")
   ```
   where `<accession>` is the GSE or GSM identifier from the user's request (e.g. `GSE192391`, `GSM5746268`). This downloads the supplementary files into the project workspace and returns the layout.

2. **QC + clustering on ONE sample.** Once the fetch step lands a 10x triplet (or h5ad) in the workspace, invoke:
   ```
   Skill(skill="scrna-qc-clustering-v2", args="<dataset_id>")
   ```
   where `<dataset_id>` is the sample directory or h5ad name from step 1's output. This produces `processed.h5ad` with Leiden cluster labels + UMAP + per-cluster top markers.

3. **Annotate the clusters with cell types.** Once step 2 produces `cluster_markers.csv` (or equivalent top-marker table), invoke:
   ```
   Skill(skill="annotate-celltype-scrna", args="<markers_csv>")
   ```
   This LLM-assigns cell-type labels from the marker genes and writes `cluster_annotations.csv`.

## How to use this orchestrator

- Present the plan (`present_plan`) BEFORE invoking step 1 — list all three steps with their bound `skill` field set to the corresponding sub-Skill name. The user approves the whole pipeline once, then you execute steps in order.
- Between steps, briefly state what the previous step produced and what the next step will consume. Don't re-derive — read the sub-Skill's `produces` list to know what file to pass forward.
- If a step fails or produces something unexpected (e.g. fetch returns no matrix files), STOP the pipeline and report — don't substitute a fabricated input for the next step.
- For multiple samples in the GEO study, pick ONE per the user's instruction (or the first if not specified) and run this pipeline on that single sample. Multi-sample integration is a different pipeline.

## What this orchestrator does NOT do

- Raw FASTQ → counts (use `fetch-sequencing-fastq` then `bp-raw-data-processing`).
- Multi-sample integration / batch correction (use `bp-data-integration`).
- Differential expression across conditions (use `bp-differential-expression` after the cluster labels are settled).
- Trajectory inference, RNA velocity (`bp-trajectory-inference`, `bp-rna-velocity`).

Skip this orchestrator if the task is just one of the three stages — go straight to that sub-Skill instead.
