---
name: run-pipeline
description: Run a Nextflow / nf-core pipeline as a planned background job (fanned out on Slurm, or run locally on one node for small/test runs) — discover it, inspect its parameters, present an editable plan, launch on the user's Go, then interpret the results
when_to_use: A task needs a standardized / production / large-scale bioinformatics workflow (bulk RNA-seq, single-cell, variant calling, fetch SRA reads, ATAC/ChIP, methylation, …) where a maintained community pipeline is more correct and reproducible than improvising the steps in the kernel
keywords: [nextflow, nf-core, pipeline, workflow, rnaseq, scrnaseq, sarek, fetchngs, atacseq, chipseq, methylseq, ampliseq, run pipeline, multiqc, fastq]
---

# Run a pipeline (Nextflow / nf-core)

For heavy, standardized, reproducible processing, hand the work to a community
**nf-core pipeline** rather than improvising the steps in-kernel. The in-kernel
recipes stay the fast, **interactive / exploratory** path; **escalate to a
pipeline** when the run is production-scale, standardized across samples, or must
be reproducible (e.g. fastq→counts for a whole study, germline/somatic variant
calling, a 10x cohort).

## The loop

1. **Find** — `search_registry(query, source='nf_core')` to choose the pipeline (e.g. `nf-core/rnaseq`).
2. **Inspect** — `describe_pipeline(pipeline)` returns the run parameters (required,
   types, allowed values, help), the latest release, **`input_format`** — the exact
   **samplesheet columns** (name, required, type, allowed values) the pipeline expects —
   **`docs`** (fetchable links: `usage.md`, `output.md`, README, the nf-co.re page), and
   **`resources`** (the run's resource footprint + a `recommended_execution` of local vs
   slurm). If you already know the profile, pass it (`describe_pipeline(pipeline, profile="test")`)
   so that recommendation reflects the actual run.
   **Never guess the params or the input format.** If anything is unclear (an unusual
   param, the exact input layout, what an output file means), `fetch_url` the relevant
   `docs` link and read it before proceeding.
3. **Prepare the input** — build the `--input` file from the user's data using
   `input_format`: write a CSV with EXACTLY those columns (one row per sample) into the
   project workspace via `run_python`/`run_r`, then pass its path as `input`. (If the user
   already has a samplesheet, use it.) Then prefill ONLY the other essentials the user
   named (e.g. `genome`/`fasta`, maybe the aligner) — **leave everything else to the
   pipeline's defaults**; the launch form shows the rest under "Show all". Don't prefill
   dozens of params (it makes the form scary). `--outdir` is set automatically — do **not** pass it.
4. **Present a plan** — call `present_plan` with a dedicated step for the pipeline,
   carrying the pipeline + your prefilled params in `parameters`:
   ```json
   {"n": 3, "title": "Run nf-core/rnaseq", "skill": "run-pipeline",
    "parameters": {"pipeline": "nf-core/rnaseq", "revision": "<release>",
                   "params": {"input": "samplesheet.csv", "genome": "GRCh38"}}}
   ```
   Use `"skill": "run-pipeline"` (this skill); the pipeline goes in `parameters`.
   Keep `params` to the essentials so the launch form stays short.
   The plan card renders this step as an **editable launch form** (the user can
   adjust the params before running). A pipeline is long and resource-heavy, so
   always present it in a plan — with its expected time/cost — and **stop** after
   `present_plan`; wait for the user's decision.
5. **Launch on Go** — when the user approves, their reply carries the FINAL params
   (`"Use these final pipeline parameters (verbatim): [...]"`). Call
   `run_nextflow(pipeline=…, revision=…, params=<those final params>, execution=<mode>, background=True)`
   (pass `estimated_runtime_min` if you can). **Choose `execution` deliberately** — don't
   just take the default:
   - **`"local"`** for a **`-profile test`** smoke run or any small/quick pipeline — runs the
     whole pipeline on ONE node. For tiny test tasks, per-task Slurm **queue latency dwarfs the
     seconds of real compute**, so fanning out to Slurm is the slow choice.
   - **`"slurm"`** for full real-data runs — each task becomes its own properly-sized Slurm
     job, parallel across the cluster. Right when per-task compute ≫ scheduling overhead.
   - **`"auto"`** to let ABA route from the pipeline's declared resources
     (`describe_pipeline` → `resources.recommended_execution`); for `-profile test` it picks local.

   When in doubt for a `test`/smoke run, use **`"local"`** (or `"auto"`). It runs as a
   background job — you're resumed when it finishes. If `run_nextflow` returns
   `invalid_params`, fix them and re-present.
6. **Interpret** — on completion the result carries `task_summary` (per-task status/
   resources) and **`multiqc`** — the per-sample QC table (headline metrics + their
   meanings), flagged statistical `outliers`, and a link to the full MultiQC report.
   Each metric carries a `direction` (`higher_better`/`higher_worse`, from MultiQC's own
   colour scale) and each outlier a `side` + `concern` flag: **`concern:true` is on the bad
   side (lead with these); `concern:false` is an outlier in the good direction (note, don't
   alarm); `concern:null` means direction is unknown — judge from the value.** Summarize the
   run and **flag QC concerns** (low mapping, high duplication, outlier samples) before any
   downstream analysis. If unsure what a metric or output file means, read the `docs.output`
   page from describe_pipeline.

Keep the in-kernel recipe as the alternative for small/interactive work; name the
trade-off when you propose the pipeline so the user can choose.
