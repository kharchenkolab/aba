# Scenario-test findings register

Living defect/friction register produced by the scenario test passes (runner:
`regtest/harness/runner.py`; forensic: `regtest/harness/forensic.py`). One row
per distinct finding; carried across passes so we don't re-discover.

**Cycle:** Sweep (Haiku, broad — no fixing) → Triage (refresh this register; rank by
severity×frequency) → **Deep-dive** (forensic, *verify root cause against the run*) →
**Fix** (test-infra first, then ABA core→recipes→agent; each gated by a re-run) →
Confirm (re-run + Opus science). Deep-dive only AFTER triage; fix only AFTER deep-dive.

**Severity:** High = blocks real work / data loss / fabrication · Med = degrades results
or weak-agent-amplified · Low = cosmetic / brittle-test noise.
**Status:** open · diagnosed (forensic-verified root cause) · fixing · fixed · verified
(fix confirmed by re-run).
**Layer / owner:** core/recipes/agent = ABA platform · harness/scenario/checks = test-side.

_Last pass: 2026-06-30 (Opus SCIENCE pass over the 7 new domains — science solid everywhere; only real
defect = H6 harness gap; blast_seq clean at Opus, confirming the harness-accumulation diagnosis)._

## ABA findings (platform)

| ID | Layer | Finding | Sev | Status | Evidence |
|----|-------|---------|-----|--------|----------|
| C1 | core | `list_data_files` non-recursive + extension allowlist → folder/imaging datasets (`coloc/`, `.tif`/`.nii`) invisible; agent sees "no datasets" | High | **verified** (commit `1810058`, deployed; coloc 1/12→11/12) | coloc forensic |
| C2 | core | **Figure-harvest was path-dependent**: harvesting scanned only the exec scratch/cwd, so figures the agent `savefig`s straight into the store dir (`/artifacts/...`) were orphaned — on disk but `plots:[]`/`produced=[]`, unpinnable + GC-reaped. FIX: harvest also registers off-convention store-writes during the exec (path-agnostic; absolute-path handle preserved). | High | **fixed** (commit `e98adc1`) — unit 4/4 (`tests/test_harvest_artifacts.py`, A deterministically proven); e2e msa **2/14→13/14**, figures 0→6 (NB: that run went via the normal cwd path — A not triggered in-situ, agent non-determinism). **Deployed** to :8000. | msa forensic; unit test |
| C3 | core | run_python orientation banner didn't surface the **resolved** DATA_DIR | Med | **verified** (deployed w/ C1) | coloc forensic |
| R1 | recipes | figure-producing recipes don't enforce a save-to-harvested-dir + register convention → feeds C2 | Med | open (tentative) | recurs in structure/msa |
| R2 | recipes | (positive) no recipe-absence failures — science rubric 2.3–2.9; recipes solve the problems | — | — | Opus diagnostic-5 |
| A1 | agent | defers a **mandatory deliverable** via an optional clarifying question ("before I render, one decision…" → never renders) | High | **fixed** (figures.md rule `395f7e9`, deployed; spot-check SUPPORTIVE — structure_superpose 4/12→10/12, s2 now delivers the figure) | structure_superpose s2 |
| ~~A2~~→C4 | core (was mis-filed as agent) | **RECLASSIFIED — not a fabrication.** Deep-dive (blast_seq s6) showed: the retry returned **rc=0** + the code's own "✓ figure saved" print + the figure WAS on disk — but the agent had `savefig`'d an **absolute path to the project work dir** (`…/projects/<pid>/work/x.png`, the PARENT of the per-thread exec cwd `…/work/thread-<tid>`). Harvest scanned only the cwd + (C2) the store dir → `plots:[]`/`produced=[]` → it LOOKED like the agent lied. Same family as C2 (path-dependent harvest); the agent reported correctly. | Med-High | **fixed (C4)** — harvest now ALSO captures the project work dir (non-recursive, `since_ts`-gated, mirrors C2). Unit 6/6 (`tests/test_harvest_artifacts.py`) + real-config integration (bare-cwd AND absolute-to-workdir saves both harvested). Pending :8000 bounce. | blast_seq s6 forensic (rc=0 + file on disk + plots:[]) |
| A3 | agent | **drops user-requested data** — "line up ALL", agent silently excluded `seq_outlier` | Med-High | **fixed** (behavior.md rule `395f7e9`, deployed; behavioral verify probabilistic) | msa Opus |
| A4 | agent | **recalls** a prior figure instead of re-emitting on "show me" | Med | **fixed** (figures.md rule `395f7e9`, deployed; behavioral verify probabilistic) | gwas s6 |
| A5 | agent | concludes "no data — ask user to upload" / unbounded `find` instead of resolving paths | Med | diagnosed (C1 mitigates) | coloc |
| A6 | agent | install-ordering: uses a module/CLI before `ensure_capability` (Bio, mafft) | Med | diagnosed (Haiku-amplified) | msa Haiku |
| P1 | core | **latent**: `JupyterKernelSession.__init__` ran `start_kernel()`+`start_channels()` **with no timeout WHILE holding the pool lock** (pool.py:58). Every step after has `wait_for_ready(timeout=60)` + exec `timeout_s=90` + a dead-kernel watchdog, so this was the only unbounded step — if startup ever *blocked* (vs. raised) under resource pressure, every other kernel request blocked behind it. NOT the blast_seq cause (exec path is otherwise robust) — a latent robustness gap. | Low-Med | **fixed** (`_start_bounded` caps each startup step w/ a thread deadline + best-effort reap → fails fast + releases the lock. Real-kernel smoke test 2.2s + reuse OK; unit 6/6 incl. timeout-and-reap. NB: lock-serialization is now BOUNDED, not eliminated — full lock-release across construction deferred) | blast_seq deep-dive (code read) |

## Test-infra findings (surfaced *by* the harness; not ABA)

| ID | Layer | Finding | Sev | Status | Evidence |
|----|-------|---------|-----|--------|----------|
| H1 | harness | `stage_into` didn't copy subdirs → folder datasets never staged (real coloc/foci blocker) | High | fixed | coloc data-dir empty |
| H2 | harness | `msgs_grow` invalid invariant (ABA uses bounded/rehydrated context) | Med | fixed | gwas resume 45→16 |
| H3 | harness | a step crash voided the whole run/bundle | Med | fixed | gwas Opus zmq crash |
| H4 | harness | judge false-flagged fabrication (no evidence trail) | Med | fixed | gwas s6 |
| H5 | harness | a **hung turn voided the whole sweep**: a wedged exec emits no SSE → `consume()`'s `iter_lines()` blocks forever → no bundle written, sweep stuck (blast_seq s2) | Med | **fixed** (`call_with_timeout` wall-clock ceiling `ABA_TURN_TIMEOUT_S`=600s + best-effort httpx read-timeout; a hang now becomes a recorded `step_crash:TurnTimeout` + restart_client recovery → OBSERVABLE. Unit 4/4) | blast_seq deep-dive |
| H6 | harness | **user `drop` action unhandled** → fell to `else: SKIP`, so the entity was never archived and the downstream `entity_archived` check failed for a HARNESS reason (looked like a platform lifecycle bug but wasn't). `drop` is an *agent* step kind; the user curation action is `delete` | Med | **fixed** (runner now treats user `kind in (delete, drop)` as a soft-archive via the same DELETE route; resolves `ref`/`from_step`) | microbiome s11 (Opus science pass) |
| H7 | harness | **`modify_figure` read the wrong response key**: the HTTP `make_revision` route returns the new id at `out["entity"]["id"]`, but the runner read `out["new_entity_id"]` (the lifecycle fn's key) → `entity_id=None` → CHAINED `modify_figure` steps couldn't resolve `{ref}` and SKIPped. Latent until a scenario chained revisions | Med | **fixed** (read `out["entity"]["id"]` w/ fallback; surface a 400 supersede-guard detail) | version_revert s4→s5 |
| H8 | harness | **prompt-less agent step crashed**: a pure `resume` step defaults to `actor=agent` but has no `prompt` → `KeyError: 'prompt'` on `drive_turn`. Existing resume steps always carried a post-resume turn, so it was latent | Low | **fixed** (state-only agent step w/ no prompt skips the turn) | reproduce_expr s5 |
| S1 | scenario | `variant_annotation` orphaned s4 figure / missing s5 pin → pin counts off-by-one | Low | fixed | va 8/11→12/12 |
| S2 | scenario | network-bound scenarios (per-turn VEP / InterProScan) = bad tests | Med | fixed (redesigned) | wave-1 |
| K1 | checks | brittle `must_mention`/`must_not`/`manifest_contains` (UMAP, transmembrane, register, identical, rs_struct, 39 nuclei, 274…) inflating false fails | Low | **fixed** (22 gates stripped across 11 scenarios) | scorecard gather |
| K2 | checks | `must_not` gates on **lifecycle-acknowledgement terms** (`Lys274Glu`, `permissive`, `four fields`) wrongly fail correct behaviour — the agent MUST name what it drops/deletes to acknowledge it | Low | **fixed** (9 gates stripped; forensic showed the agent dropped correctly. Found via the Confirm re-sweep regression check) | variant_to_structure s6 forensic |
| K3 | checks | two patterns found in the Opus science pass: (a) **pin-count over-specification** — `pinned_results_min` assumed a restyle-pin adds a NEW Result, but pinning a re-derived/restyled figure correctly **dedups to the existing Result** (cheminformatics s7→same result as s5); (b) **brittle `must_mention` on revise/resume steps** — the agent builds on established context and doesn't re-type a keyword (`Lipinski`/`BRCA1`/specific gene-IDs) it already delivered; rubric grades the science there | Low | **fixed** (chem pin-counts corrected 3/2/3→2/1/2; K2 terms stripped from chem s8; `Lipinski`/`BRCA1`/`GENE0098`/`GENE0488` dropped from revise/resume steps in chem/tpm/crispr — kept on primary-deliverable steps) | Opus science pass |

## Confirm re-sweep (close of first pass, 2026-06-29)
Full Haiku re-sweep on the fixed+deployed code: **148/178 → 166/178** (→ ~169/178 after K2
clears 3 false-drops). Big recoveries: msa 2→13, foci 1→12, coloc 1→11, variant_annotation
killed→12/12, protein 8→11, scrna 10→13.
**No real code regressions** from C1/C2/A. The 3 apparent drops were investigated: all were
a check bug (K2 — lifecycle-acknowledgement `must_not`) or Haiku must_mention variance, NOT
the fixes (forensic-confirmed on variant_to_structure s6: agent dropped correctly).
**Artifact-delivery cluster (C2 + A1/A3/A4) resolved** — the structural `produces[figure]`/
pin-cascade failures are gone across the suite.

## Coverage expansion (2026-06-30): 7 v1 → v2 new-domain scenarios
Suite 14 → 21 (cheminformatics, genome-engineering, clinical survival, microbiome,
sequence-ID, structure-prediction, expression-norm). Haiku sweep: **65/76**. Clean:
cheminformatics 12/12, survival 9/9, alphafold 12/12; strong: tpm 10/11, crispr 10/11,
microbiome 10/12. Outlier: **blast_seq 2/9** — s2 figure step flaky at the kernel level.

**blast_seq deep-dive (2026-06-30) — DIAGNOSED:** The agent's s2 code is benign — the exact
1665-char bar-chart runs in ~1s standalone (backend=agg, `plt.show()` no-op, data well-formed),
so the hang is NOT the code. The platform exec path is well-bounded: `execute()` has a 90s
timeout + a dead-kernel watchdog (reap+fail), startup has `wait_for_ready(timeout=60)`. The
sweep's zmq *"Resource temporarily unavailable"* (EAGAIN) is **per-process resource accumulation
in the long-lived in-process test runner** — it holds every scenario's kernels/zmq-sockets for
the whole sweep, and under 2-way concurrency that piles up → a kernel dies (or can't start) →
A2 hallucination on the restart. This is a **harness-scale artifact, not a production bug**
(production reaps idle kernels + bounds concurrent kernels + is one long-lived process). Two
deliverables: **H5** (the harness now records a hung turn instead of voiding the sweep — makes
any recurrence observable) and **P1** (a latent platform gap: unbounded kernel startup under the
pool lock — flagged, fix proposed). Brittle gates to triage: crispr s4 `no guides`,
microbiome s11 delete-state, tpm `GENE0098`.

## Opus SCIENCE pass (2026-06-30): 7 new domains, sequential fresh-process, Opus agent + Opus rubric
Run on stable code (commits up to `e4bf52c`), sequential/fresh-process (no resource accumulation —
also the live test of the blast_seq diagnosis). **Science is solid across all 7**: every agent-step
rubric is 2–3; `correctness` ≥2.83 and `no_fabrication` ≥2.83 in every scenario (most 3.0). This
extends R2 (no recipe-absence failures) to the new domains — **the recipes/tools solve the science.**

| scenario | mechanical | rubric overall | note |
|----------|-----------|----------------|------|
| survival | **9/9** | 3.0 | perfect |
| blast_seq | **9/9** | 2.8 | **clean at Opus** — confirms the s2 flakiness was harness resource-accumulation, NOT a platform bug |
| microbiome | 11/12 | 2.88 | only fail = H6 (harness drop gap) |
| alphafold | 11/12 | 2.86 | only fail = pin-count over-spec (K3) |
| tpm | 10/11 | 2.83 | only fail = brittle gene-ID must_mention (K3) |
| crispr_guides | 9/11 | 2.83 | fails = brittle BRCA1 must_mention on revises (K3) |
| cheminformatics | 6/12 | **3.0** | rubric perfect; all 6 mech fails = K3 (pin-count cascade) + K2 + H6-adjacent — **zero science failures** |

**Conclusion:** the only REAL defect surfaced was **H6** (a test-harness gap); every other mechanical
fail was a check/scenario artifact (K3/K2). No core/recipe/agent science failures. All fixed; validation
re-run in progress. The cheminformatics 6/12-mech-but-3.0-rubric divergence is the headline proof that
the mechanical drops were check brittleness, not science.

## Operational breadth expansion (2026-06-30): provenance / reproduce / versioning
New focus per user steer (reproduce results · modify · go back to old versions). Mapped the
op surface (reproduce + make_revision + delete-revision + list-revisions have HTTP routes;
**revert-to-version `set_current_revision` is agent-MCP-only**), extended the runner (new
`reproduce` + `delete_revision` user-actions; provenance checks `reproduced`/`env_drift`/
`superseded_min`/`revisions_min`), and authored 2 scenarios. Suite 21 → **23 v2 scenarios**.

| scenario | Haiku | Opus | what it proves |
|----------|-------|------|----------------|
| reproduce_expr | 7/7 | **7/7, rubric 3.0** | `reproduce` (reproduced=True, no drift) + **memory-wipe recovery** (agent uses `reproduce_from_exec` on a fresh thread to recover the analysis from its record, same genes) + TPM→CPM modify |
| version_revert | 7/8 | **8/8** | restyle chain v1→v4 (`make_revision`) + **revert to v2** (agent `set_current_revision`, v3/v4 superseded, nothing deleted) + continue from restored v2 |
| revision_delete | 8/8 | **8/8 mech** (rubric was a planted-truth artifact — see K4) | `delete_revision` hard-deletes a MIDDLE revision, **re-parents the child to the grandparent** (re_parented=1), chain stays connected — 3-pass op sound |
| provenance_export | — | **4/4, rubric 3.0** | `export_reproduction_bundle` (Phase 5: code + pinned requirements + record) + `diff_env` (Phase 4: agent reports "unchanged" on the same box, does NOT fabricate version drift) — both agent-driven, correctly invoked |

Bugs surfaced + fixed by this breadth: **H7** (modify_figure response key → chained revisions
SKIPped), **H8** (prompt-less resume crash). Both latent until operations were chained. New runner
checks added: `reproduced`/`env_drift`/`superseded_min`/`revisions_min`/`revision_deleted`/`tools_used`.
Suite **21 → 25 v2 scenarios**. ALL provenance/reproduction/versioning ops now exercised + working;
`reproduce_run` is the only one untested (designed but NOT implemented — a real gap, not a defect).

| ID | Layer | Finding | Sev | Status | Evidence |
|----|-------|---------|-----|--------|----------|
| A7 | agent | **contradictory narration on a correct action**: at version_revert s7 the agent narrated "I don't have a version 2 … the only version" — but forensic proved the PLATFORM was correct: `list_revisions(fig_4dbdcd0b)` returned the full 4-version chain (`total:4`, v2=fig_060072f9 labeled) AND the agent then correctly called `set_current_revision(v2)` + superseded v3/v4. So the data + action were right; only the prose was wrong (likely a premature "only version" claim before/while reading the chain). Judge caught it (no_fabrication=2). | Low-Med | **diagnosed — NOT a platform bug** (platform exonerated by forensic). Agent-narration slip; fix candidate = a behavior rule (don't claim "only/no version" without checking `list_revisions`), but single Opus instance — monitor for reproducibility before acting | version_revert s7 forensic (tool_result + DB) |
| P2 | core | **latent edge**: `figure_history`/`list_revisions` from a **superseded** entry node returns only that node (`figure_history(v4_superseded)=[v4]`), vs the full active chain from any active node. Harmless in practice (callers enter from an active/current revision), but a revert-then-inspect-an-old-hidden-rev path could see a truncated chain. | Low | open (noted; not blocking — entry from active nodes is correct) | version_revert DB probe |
| P3 | core/ux | **delete_revision re-numbers version labels** (positional `version=total-idx`): deleting the MIDDLE of [v1,v2,v3,v4] leaves a CONTIGUOUS [v1,v2,v3] — so "version 3" now refers to what was v4. Chain integrity is correct (verified), but the label shift can confuse a user who deleted "v2" and finds "v3" is a different figure. Design/UX consideration, not a bug. | Low | open (noted; consider stable/immutable version ids or a "deleted" tombstone label) | revision_delete forensic |
| K4 | checks | **planted_truth error (mine)**: revision_delete's ground-truth said deleting v2 leaves "v1, v3, v4" (keeping original numbers) — but the platform re-numbers to contiguous "v1, v2, v3" (P3). The Opus judge scored the agent's CORRECT answer `1` (correctness/no_fabrication/lifecycle) against my wrong truth. Forensic showed `list_revisions` returned total=3 + the agent reported it accurately. Distinct from A7 (there the agent genuinely contradicted its data). | Low | **fixed** (planted_truth corrected to reflect re-numbering) | revision_delete s8 forensic (tool_result + reply) |

## Still open (next cycle's triage)
A5 (path-resolution — C1 mitigates), A6 (install-ordering — Haiku-amplified), R1 (recipe
figure-save convention — subsumed by C2/C4), pseudobulk `Myeloid` must_mention (borderline;
likely variance), P2/P3 (minor revision-chain edges), A7 (single agent-narration slip — monitor).
All Med/Low. **A2 RESOLVED** → it was C4 (work-dir harvest gap, fixed), not an agent fabrication.
reproduce_run = unimplemented feature gap (flag to team). **Verify-don't-assume: 3 apparent
agent/platform bugs (A7, K4, A2) flipped to test-side or harness on forensic — always trace to
the data before filing.**
</content>


## regtest Haiku baseline seeded (2026-07-02)
First full sweep in the new `regtest/` home → `baselines/haiku.json` (commit d6155bd):
**16/26 scenarios full-pass, 269/288 steps**, under OAuth/Haiku, mechanical-only (no judge).
This is the regression reference — weekly `sweep.py` now flags any per-scenario DROP from it.

The 19 sub-full steps are NOT platform regressions — two known classes:
- **Agent-driven provenance ops Haiku can't do** (need Opus, by design): `provenance_export`
  s4 (`diff_env` not called), `version_revert` s7 (`set_current_revision` not called). These
  pass at Opus; the Opus baseline (when seeded) will show them green.
- **Brittle checks** (K1/K2/K3 classes, the same we've trimmed elsewhere): pin-count
  over-spec (msa_phylo `pinned_results>=N`), lifecycle-ack `must_not`/`must_mention`
  (`superseded`, `drop`, `hypomethylated`), and Haiku must_mention phrasing variance
  (`P04637`, `GRCh38`, `donor`, `F169`, `TAF3`, `tetramerization`, `f3`, `topology`, `rebuilt`).

**Backlog (a check-hygiene cycle, not blocking):** trim the brittle gates in msa_phylo /
alphafold / atac_peaks / methylation_dmr / structure_superpose / colocalization /
pseudobulk_de / variant_annotation, then re-`--accept` to raise the baseline. The
agent-driven-op fails stay (Haiku-tier) — they belong to the Opus baseline.

## Haiku variance characterized + check-hygiene (2026-07-02)
Re-seeding exposed that **Haiku mechanical `must_mention` gates jitter ±2–3 steps run-to-run**:
different brittle phrasing trips each run (f3/"Field 3", Myeloid, moving/fixed, buried, GRCh38…)
plus occasional intermittent kernel hangs (nuclei_count s11 `TurnTimeout` — H5 caught it, the
blast_seq/msa flakiness class). So a strict single-run Haiku baseline produces FALSE regressions.

Mitigations applied:
- **Two rounds of brittle-gate trims (22 gates)**, each verified a false-fail against the sweep
  bundle replies (agent did it right; the substring/count check was too literal). K1/K2/K3 classes.
- **Mode-aware mech tolerance** in `sweep.py` (`ABA_REGTEST_MECH_TOL`): Haiku=2 (coarse robustness
  net — flags only real breakage), Opus=0 (deterministic → strict). Rubric drop > 0.3 always flags.
- **Best-of composite baseline** (`baselines/haiku.json`): each scenario's ACHIEVABLE mech_pass
  across runs, so a normal dip doesn't read as a regression. Haiku baseline = 22/26 full, 280/288.

**Takeaway:** Haiku is the COARSE robustness tier (crashes, big drops); **Opus + rubric is the
precise science-regression signal.** Remaining Haiku sub-fulls are honest: msa_phylo (figure/pin
Haiku-tier), version_revert (agent-driven revert needs Opus). Persistent kernel-hang under Haiku
load (nuclei/blast/msa) is an open flakiness item, not a check artifact.

## Opus baseline seed FAILED — OAuth expiry + rate limits (2026-07-02) [OPEN]
The full Opus sweep (~2–3 h) outlived the OAuth token and hit account rate limits, so
~5 scenarios cratered on `OAuthTokenUnavailable` / `RateLimitError: 429` — NOT science
failures (the tell: high rubric + 0 figures + empty replies + zero tool_errors). Affected:
gwas_popstruct, image_registration, methylation_dmr, microbiome, msa_phylo (± structure_
superpose partial). The garbage baselines/opus.json was discarded (never committed).

Hardened sweep.py: it now DETECTS infra errors (OAuth/rate/overload) per scenario, EXCLUDES
them from the baseline on --accept (keeps the prior/absent entry), and flags them for re-run.

**To seed the Opus baseline (needs fresh, longer-lived creds — the deferred Phase 4 concern,
now concretely justified):** refresh the OAuth token (`claude` once → ~/.claude/.credentials.json),
make the runner read the FRESH token (not the stale /tmp/aba_8000.env snapshot), and re-run —
ideally in SMALLER BATCHES to stay under the rate limit, e.g.
`python regtest/harness/sweep.py --opus --accept --only <8 scenarios>` repeated. The sweep now
merges clean results into the baseline and skips any that still hit infra errors.

## Opus baseline SEEDED cleanly (2026-07-02, after main merge) [RESOLVED]
Re-ran the full Opus sweep on a fresh OAuth token (~3.2 h window) against merged main
(commit 87134c9) — **no infra failures this run** (the hardening + fresh token held).
`baselines/opus.json`: **15/26 full-pass, 258/288 steps**, 26 scenarios, 0 infra-skipped.

Key confirmations:
- **Agent-driven provenance ops pass at Opus**: version_revert 8/8, provenance_export 4/4
  (diff_env + set_current_revision) — they genuinely needed the Opus tier (Haiku can't).
- Perfect science on cheminformatics/gwas/survival/reproduce_expr/revision_delete (rubric 3.0).
- msa_phylo 13/14 at Opus (vs 8-9 at Haiku) — the Haiku sub-full was model-tier, not a defect.

**Opus check-hygiene backlog (future round):** sub-full scenarios whose HIGH rubric confirms
the mech fails are brittle, not science — crispr_guides 9/11 (rubric 3.0!), structure_superpose
11/12 (2.86), variant_annotation 5/12 (2.71), + nuclei_count 7/13 (2.0, also the kernel-hang
class), foci_count, methylation_dmr, pseudobulk_de, variant_to_structure, image_registration,
scrna_qc, msa_phylo. Same K1/K2/K3 pattern as the Haiku pass. Both baselines now committed;
the regtest system is fully operational (Haiku coarse robustness net + Opus precise signal).
