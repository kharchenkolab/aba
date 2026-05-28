---
name: inspect-upload
description: Probe an unfamiliar uploaded file to recommend the right loader
when_to_use: User uploaded something with an unusual extension, a 10x archive, or any file you can't immediately identify
requires_tools: [inspect_upload]
produces: [inspection_report]
resource_profile: tiny  (<1s)
---

# Inspect upload

Call the `inspect_upload` tool with the path. It returns:

- `format`: e.g. "10x-mtx", "h5ad", "csv-counts", "fastq", "unknown"
- `suggested_loader`: snippet showing how to load it (sc.read_*, pd.read_csv with the right separator, etc.)
- `summary`: brief structural read (shape, column dtypes if tabular)

This is fast and side-effect-free — run it whenever the user uploads
a new file before deciding on a pipeline.
