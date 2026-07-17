# Dataset-management regression suite (misc/datasets2.md v3)

Three tiers, complementing the pytest tiers (`tests/test_datasets_mechanism.py`
fast/fake; `tests/test_datasets_weft_native.py` live-local;
`tests/test_datasets_cluster.py` mock-slurm, `ABA_WEFT_CLUSTER=1`):

- **`epic_mechanism.py`** — the mixed local↔remote coordination epic at the
  data-plane level, against weft's dockerized slurm fixture (orbstack docker
  on mac): url → remote CAS → ref-staged compute rounds → in-place keeps
  (`retain.dir`) → one result synced home → local round → back to the cluster
  (automatic site-ward byte movement) → memoized resubmit. One checksum is
  threaded through every hop and asserted at the end. Self-cleaning.

      python regtest/datasets/epic_mechanism.py

- **`study.py`** — LIVE agent scenarios (real /api/chat turns via the
  deployment's OAuth, real weft, real remote site on mendel with disposable
  dirs): url registration, source-key reuse, remote in-place registration
  (no copy, lazy identity), drift + missing-home honesty via check_import,
  produced-lane registration. Writes full per-scenario transcripts (tool
  calls + agent text) beside its throwaway home; prints per-check PASS/FAIL.
  Self-cleaning (site unregistered, remote dirs removed).

      python regtest/datasets/study.py [--only name,name]

Known limitation (dilemma D3 in the doc): agent-driven REMOTE compute is not
yet wired (background jobs route only to slurm-kind shared-fs sites, and a
mac controller cannot serve the shared-fs entry into a linux node) — the epic
therefore runs at the weft-task level; the live-agent scenarios cover
everything agent-reachable today.
