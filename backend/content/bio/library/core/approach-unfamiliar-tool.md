---
name: approach-unfamiliar-tool
description: How to get external data or use a library/tool you don't have — discover the recipe + maintained package first, orient from its docs, recover smartly, and never fabricate.
when_to_use: You need to acquire data from an external source/database (GEO, SRA, ENA, ArrayExpress, UniProt, …), OR use a package/tool you're not already fluent in and there's no obvious recipe. Skip only for tools you know well.
requires_tools: [run_python, search_skills, search_pypi, ensure_capability]
capabilities_needed: []
keywords: [unfamiliar tool, get data, download, fetch, database, GEO, SRA, library, package, discover, search_skills, search_pypi, orient, documentation, vignette, recover, fallback, fabricate, meta]
produces: []
domain: meta
---
# Getting data, or approaching an unfamiliar tool

Two reflexes that prevent the common failure mode — flailing with `fetch_url` +
hand-parsing (or, worse, *fabricating* the answer) instead of using the curated
path that already exists.

## Discover first — do NOT hand-roll
Before writing code to fetch external data or drive a library you don't already
have loaded:
1. **Is there a recipe?** `search_skills(<intent>)` → `read_skill`. For a public
   database (GEO/SRA/ENA/ArrayExpress/…) there's almost always one. Follow it.
2. **Find the maintained package.** `search_pypi` / `search_bioconda` /
   `list_capabilities` (e.g. GEO → `GEOparse`; SRA → `pysradb`/`sra-tools`) →
   `ensure_capability(name)` → use it. A purpose-built library beats scraping a
   webpage every time.
3. Only if neither exists do you write fetch/parse code yourself — and even then,
   prefer a documented API (eutils, a REST endpoint) over screen-scraping HTML.

**Anti-patterns that mean you skipped discovery:** parsing GEO/EBI HTML by regex,
`curl`-and-grep for metadata, or installing R/BiocManager from scratch to get a
table a one-line `GEOparse`/`pysradb` call returns.

## Orient before trial-and-error (once you have the tool)
Brute-forcing an unknown API in the kernel is slow and error-prone.
1. **Read the tool's own docs** — enough to learn the *real* API, then run.
   Fastest path is one call to **`inspect_package(name, language=...)`** (exported
   symbols, signatures, R6 methods, docstrings, vignette list). Otherwise, in the
   kernel:
   - **Python** (`run_python`): `help(obj)`, `inspect.signature(fn)`, `obj.__doc__`,
     `dir(mod)`; read the package's README/docs.
   - **R** (`run_r`): `library(help=pkg)`, `ls("package:pkg")`, `args(fn)`,
     `?fn`; for R6 objects `obj$methods` / `names(gen$public_methods)`; vignettes
     via `vignette(package=pkg)` / `browseVignettes(pkg)` — these are the canonical
     tutorials and usually show the exact workflow.
   - **CLI**: `<tool> --help`, `<tool> <subcmd> --help`.
3. **Still stuck → ask the user before searching the web** (`ask_clarification`):
   they often know the canonical tutorial/reference. Web search is a last resort;
   once they point you at a URL, `fetch_url` it. Don't scrape blindly.

Scope the reading to what the task needs — don't read everything.

## Recover smartly when something breaks
- **Installs but won't run / won't import** (e.g. an old binary, a glibc/symbol
  error): prefer the **maintained alternative** (e.g. a `macs2` failure → try
  `macs3`) or **diagnose the error** (missing system lib → `ensure_capability`
  the conda dep and retry). Hand-rolling your own substitute is a *last* resort,
  not the first move — say so if you do it.
- **Missing system library** during a source build: `ensure_capability` the conda
  package that provides it (userspace), then retry — don't give up or fake a result.
- When you genuinely can't proceed (needs root, an unavailable backend, etc.),
  say so plainly rather than improvising something that looks like it worked.
- **Never fabricate data or metadata.** Sample attributes, study design,
  identifiers, and values must come from a real tool result — never inferred from
  filenames, prior knowledge, or the project summary. If retrieval fails, report
  "I couldn't get X" and stop. A plausible made-up table is far worse than no table.

## After a successful figure-it-out
If you worked out a non-obvious workflow, it's a good candidate to capture as a
skill so the next run doesn't re-discover it.
