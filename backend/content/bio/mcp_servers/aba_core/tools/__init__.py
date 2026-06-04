"""Per-cluster tool modules for aba_core. Each module exposes a
`register_<cluster>(mcp)` function that the server's make_server()
calls. Sub-phasing groups (6.B simple, 6.C ctx-read, 6.D curation,
6.E discovery, 6.F file I/O, 6.G plan/scenario, 6.H run-python/run_r)
each get their own module here as they're migrated."""
