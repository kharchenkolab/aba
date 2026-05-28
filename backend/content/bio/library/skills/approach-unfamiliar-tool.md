---
name: approach-unfamiliar-tool
description: How to use a library/tool you don't know — orient from skills + its own docs before trial-and-error, and recover smartly when something is broken.
when_to_use: You need a package/tool you're not already fluent in, and there's no specific skill for the task. Skip this for tools you know well.
requires_tools: [run_python]
capabilities_needed: []
keywords: [unfamiliar tool, orient, documentation, vignette, help, introspection, recover, fallback, strategy, meta]
produces: []
domain: meta
---
# Approaching an unfamiliar tool

Brute-forcing an unknown API in the kernel is slow and error-prone (15 failed
calls discovering method names). Orient first; recover smartly.

## Orient before trial-and-error
1. **Is there a skill?** `search_skills(<intent>)` → `read_skill`. If a recipe
   exists, follow it — you're done here.
2. **Read the tool's own docs** — enough to learn the *real* API, then run.
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

## After a successful figure-it-out
If you worked out a non-obvious workflow, it's a good candidate to capture as a
skill so the next run doesn't re-discover it.
