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

## Multinode scenario coverage plan (misc/detached_compute.md S4+)

Current (`multinode.py`): size-up + data-gravity placement · node→local→node
hop chain · status surfaces (two-axis badges / ledger / bring-back, asserted
on the exact JSON the cards render) · bogus-site honesty.

Planned slate, priority order (each doubles as a permanent regression guard;
all content strictly generic — numeric series, parameter sweeps, csv/binary
blocks; no domain examples):

1. **isolated_env_remote** — agent makes an isolated env for a named package
   and runs it on the node: named-env re-lock → realize-on-site → import
   works remotely. (Named-env re-lock has fast tests, no live pass.)
2. **crash_fix_rerun** — remote job dies on a code error; agent reads the
   log from the result, fixes, resubmits to the SAME site, succeeds.
3. **fanout_gather** — 3 independent parameter-variant jobs in parallel
   (mix of node + local), then a local gather step over all outputs;
   exercises concurrent detached jobs + continuation ordering.
4. **conflicting_gravity** — data home on machine A, compute requested on
   machine B: agent states the tradeoff and either computes at the data or
   moves it DELIBERATELY (guardrail-priced); never a silent bulk transfer.
5. **preflight_disconnect** — remote keeps + a data home exist; "anything
   at risk if I disconnect?": data_safety_summary-grounded answer naming
   items → bring-back → "now safe". The ledger story as conversation.
6. **platform_unsolvable** — env package with no build for the node's
   platform: re-lock fails with the named cause; agent relays honestly and
   offers real options (no retry loops, no fabrication).
7. **rerun_asis_recomputes** — "re-run stage 1 as-is" forces a REAL
   recompute (fresh weft task id — the memo nonce), while re-run WITH
   CHANGES records `scenario_of` lineage.
8. **provenance_after_chain** — "where did this number come from?" after a
   hop chain: agent names each stage's machine from recorded state,
   matching the cards.
9. **stay_local** — remote available but the step is trivial: no
   gratuitous remote job.
10. **gpu_routing** — est_gpu step lands on the fixture's (fake-)GPU
    partition with gpu resources in the task; agent says where and why.
11. **cancel_midflight** — user cancels a running remote job: propagates,
    Run reflects cancelled, partial outputs stay honest (temporary).
12. **status_while_running** — "how's it going?" mid-job: honest state,
    no fabricated progress.

Harness disciplines (learned live): unambiguous seeds (header rows — a
headerless csv cost a run to a defensible parse difference); settle before
asserting (wait_jobs_settled + durable-view settle — continuations land
after the driven turn's stream ends); assert results against the FULL
thread text, not just captured streams.

### Multi-turn context-durability additions (agent provisioning probes)

The slate above tests decisions; these test whether the CONTEXT we provision
(rules, catalog prose, ambient compute line, continuation payloads) survives
realistic multi-turn use without confusing the agent:

13. **long_gap_recall** — a remote chain, then several turns of unrelated
    small work, then "where does the stage-2 result live and which machine
    computed it?" — answered from context/records without re-derivation.
14. **cross_thread_separation** — two threads working against different
    sites; no cross-wiring of where data/results live.
15. **mid_chain_steering** — the user changes the target machine between
    steps; the agent re-plans without losing prior stage state.
16. **no_polling_compliance** — after submitting a background job the agent
    ENDS ITS TURN (the deferred contract) instead of get_job_status loops
    (observed: ~100 polls in one turn across several runs — an
    instruction-compliance gap; may require tightening the tool note).
17. **context_line_sufficiency** — the ambient per-turn compute context
    names declared remote sites (or we fix it so it does): the agent should
    know its placement options WITHOUT having to think of describe_compute.

## Multinode coverage — implemented (13 scenarios)

size_up · hop_chain · status_surfaces · honesty · isolated_env_remote ·
crash_fix_rerun · fanout_gather · pin_remote_result · external_ref_inject ·
background_monitor · provenance_after_chain · preflight_disconnect ·
reference_drift.

These cover the named core workflows: data injection (external_ref,
reference_drift), run routing/submission (size_up, hop_chain, fanout),
monitoring + no-poll compliance (background_monitor), error handling
(crash_fix_rerun), result interpretation (throughout), Run-card status
(status_surfaces), pinning to Results (pin_remote_result), provenance
(provenance_after_chain), safety ledger (preflight_disconnect), and
remote env realization (isolated_env_remote).

Still planned: planning→approval→execute with a remote step (needs the
approval-POST flow driven from the harness); conflicting_gravity + a
multi-site preflight (needs a SECOND real remote site alongside the
fixture); gpu_routing (fixture fake-GPU partition); cancel_midflight;
rerun_asis_recomputes (memo-nonce forces a fresh task); and the multi-turn
context-durability probes (long_gap_recall, cross_thread_separation,
mid_chain_steering, context_line_sufficiency).

## Multinode coverage — 16 scenarios, all validated live

Definitive: the primary 13 passed together in one clean run; the newest 3
(gpu_routing, rerun_asis_recomputes, data_gravity_recall) passed together in
another. Full list:

size_up · hop_chain · status_surfaces · honesty · isolated_env_remote ·
crash_fix_rerun · fanout_gather · pin_remote_result · external_ref_inject ·
background_monitor · provenance_after_chain · preflight_disconnect ·
reference_drift · gpu_routing · rerun_asis_recomputes · data_gravity_recall.

Product bugs this study + an adversarial review surfaced and fixed (each with
a fast regression guard): unsized-walltime eternal pend (PartitionTimeLimit);
default-env platform re-lock gap; single-DB weft-poll blindness (jobs stuck
'queued'); wrong-site resubmit from the generic poller; sync-remote
cancel/timeout no-op orphaning the remote task; substrate-cancel reported as
success; a harvested remote figure being unpinnable (no exec record ->
no artifact_id); and the ambient context line not naming remote machines.

Harness disciplines learned live: isolated per-scenario data dirs (a shared
dir let leftover files create ambiguity the agent correctly refused to guess
through); wait_for_text polls for DEFERRED-continuation results (jobs-settled
+ fixed sleep is too early — the gather/report runs later); number-normalize
checks so '12,070,100' matches '12070100'.

Follow-ups (not built): planning->approval->execute with a remote step (needs
the resume/approval POST driven from the harness); a SECOND real site for
conflicting-gravity + multi-site preflight; cancel-midflight (sync cancel has
unit coverage); cross_thread_separation + mid_chain_steering.

## Two-site coverage (mendel + docker fixture) — validated live

Added the second real remote site (mendel over ssh) alongside the dockerized
cluster fixture, gated on reachability:

- **conflicting_gravity** — a large file (3M integers, ~20 MB) lives on
  mendel; the user leans toward "run on hpc" but the compute needs the whole
  file. The agent computes AT the data on mendel, reasons about the transfer,
  reports the correct sum, and never hauls the big file across. (18-scenario
  total now.)
- **cross_thread_separation** — thread A works on hpc, thread B on mendel;
  the agent never cross-wires which thread's work ran where.

18 scenarios total, all passing live across single-remote, two-site, and
local compute. Remaining follow-ups unchanged: planning->approval->execute
with a remote step; mid_chain_steering (retarget mid-chain); cancel_midflight.
