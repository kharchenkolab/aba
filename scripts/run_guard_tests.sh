#!/usr/bin/env bash
# Curated hermetic guard suite — the behavioral guards for shared agent inputs
# (tool catalog, prompts, cache placement), the output/env-layer unit guards,
# and the runtime translation tests.
#
# Run PER FILE (one pytest process each): the full suite has known cross-file
# import interference, and several files ship their own standalone runners for
# exactly that reason. Per-file is the supported execution mode; a green run
# here must mean every file is green in isolation.
#
# Excluded on purpose: tests needing a CONFIGURED COMPUTE SUBSTRATE
# (e.g. test_env_background's submit-path tests fail substrate_offline on a
# bare box), live-server probes (regtest/harness/live_surface_probe.py), and
# bio-marked content tests. CI runs this script; run it locally before pushing
# changes to any shared agent input.
set -u
PY="${PYTHON:-python3}"

FILES=(
  # suite integrity: every test file gated, excluded-with-rationale, or legacy
  tests/test_suite_census.py
  # shared-agent-input guards (tool catalog / prompts / cache placement)
  tests/test_tool_conventions.py
  tests/test_tool_allowlist.py
  tests/test_tool_presentation.py
  tests/test_lean_catalog_compression.py
  tests/test_lean_summary_budget.py
  tests/test_cache_prefix_determinism.py
  tests/test_catalog_caching.py
  tests/test_runtime_tail_parity.py
  tests/test_cache_control_empty_block.py
  tests/test_history_prefix_stability.py
  tests/test_tier2_synth_real_path.py
  tests/test_wire_contract.py
  # output / env-layer unit guards
  tests/test_harvest_identity.py
  tests/test_project_locate.py
  tests/test_vision_refs.py
  tests/test_env_integrity.py
  tests/test_env_resolution.py
  tests/test_substrate_error_surfacing.py
  tests/test_syslib_env_routing.py
  tests/test_r_install_lanes.py
  tests/test_live_session_smallfixes.py
  tests/test_modules_pack_ready.py
  tests/test_fetch_url_integrity.py
  tests/test_env_agency.py
  tests/test_capability_language.py
  tests/test_remote_output_resolution.py
  tests/test_remote_kernel_lane.py
  tests/test_data_ledger.py
  tests/test_provenance_evidence.py
  # regtest oracle + probe evaluators (pure, no live server)
  tests/test_transport_oracle.py
  tests/test_live_surface_probe_eval.py
  tests/test_sweep_baseline_honesty.py
  # runtime translation
  tests/test_openai_runtime_translation.py
  tests/test_openai_runtime_skeleton.py
  tests/test_chat_scenarios.py
)

# Standalone scripts (no pytest-collectable functions — run via __main__).
STANDALONE=(
  tests/test_chat_attachments.py     # api_messages validity guard
)

fail=0
for f in "${STANDALONE[@]}"; do
  if ! "$PY" "$f"; then
    echo "GUARD FAIL: $f"; fail=1
  fi
done
for f in "${FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "GUARD MISSING: $f (renamed or deleted? update this list)"; fail=1
    continue
  fi
  if ! "$PY" -m pytest "$f" -q --no-header; then
    echo "GUARD FAIL: $f"; fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "GUARD SUITE FAILED"
else
  echo "GUARD SUITE OK (${#FILES[@]} files, each in its own process)"
fi
exit $fail
