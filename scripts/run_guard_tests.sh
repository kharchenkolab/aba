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
  # the PROPERTY guard for the dominant instrument defect
  tests/test_gates_are_armed.py
  # shared-agent-input guards (tool catalog / prompts / cache placement)
  tests/test_tool_conventions.py
  tests/test_tool_catalog_liveness.py
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
  tests/test_console_events.py
  # output / env-layer unit guards
  tests/test_harvest_identity.py
  tests/test_harvest_honesty.py
  tests/test_project_locate.py
  tests/test_vision_refs.py
  tests/test_env_integrity.py
  tests/test_env_resolution.py
  tests/test_env_resolution_honesty.py
  tests/test_capability_install_conflict.py
  tests/test_env_session_repair.py
  tests/test_entity_search.py
  tests/test_remote_view_artifact.py
  tests/test_output_hygiene.py
  tests/test_dataset_mirror.py
  tests/test_location_surfacing.py
  tests/test_dataset_scratch_binding.py
  tests/test_dataset_integrity_gate.py
  tests/test_nested_env_mount_path.py
  tests/test_cap_request.py
  tests/test_ensure_envelope_contract.py
  tests/test_verify_memo.py
  tests/test_path_resolution.py
  tests/test_keep_outputs.py
  tests/test_register_dataset_per_project.py
  tests/test_substrate_error_surfacing.py
  tests/test_substrate_identity.py
  tests/test_substrate_kwarg_compat.py
  tests/test_syslib_env_routing.py
  tests/test_r_install_lanes.py
  tests/test_live_session_smallfixes.py
  tests/test_modules_pack_ready.py
  tests/test_fetch_url_integrity.py
  tests/test_env_agency.py
  tests/test_capability_language.py
  tests/test_remote_output_resolution.py
  tests/test_output_search_uncapped.py
  tests/test_output_addr_index.py
  tests/test_remote_kernel_lane.py
  tests/test_kernel_cwd_guard.py
  tests/test_remote_setup_purity.py
  tests/test_exec_argv_activation.py
  tests/test_handle_round_trip.py
  tests/test_interactive_timeout_note.py
  tests/test_job_failure_surfacing.py
  tests/test_shared_fs_inference.py
  tests/test_project_binding_propagation.py
  tests/test_cross_language_sandbox.py
  tests/test_exec_record_project_binding.py
  tests/test_base_pack_completeness.py
  tests/test_lstar_lockstep.py
  tests/test_short_transfer_refusal.py
  tests/test_turn_stream_termination.py
  tests/test_kernel_transport_resilience.py
  tests/test_store_cors.py
  tests/test_study_registration.py
  tests/test_restart_orphan_stamp.py
  tests/test_transport_outage_poll.py
  tests/test_artifact_url_doors.py
  tests/test_range_channel.py
  tests/test_range_segments.py
  tests/test_data_ledger.py
  tests/test_provenance_evidence.py
  tests/test_delete_blockers.py
  # deployment shape + substrate contract (the 2026-08 field incidents). These
  # guard facts about the configuration we SHIP — a cluster site being declared
  # and registered, the site contract measured rather than guessed, the node's
  # activation proof, the substrate refusals an agent can act on, and the
  # release naming what it was built from. Every one of them exists because a
  # green instrument reported on something other than what it appeared to.
  tests/test_site_declared_compute_sites.py
  tests/test_slurm_lane_declared.py
  tests/test_detached_contract_inference.py
  tests/test_job_wrap_reachability.py
  tests/test_substrate_error_levers.py
  tests/test_release_provenance.py
  tests/test_running_build_visible.py
  tests/test_session_rebuild_on_new_base.py
  tests/test_post_link_acknowledgment.py
  tests/test_lived_in_fixture.py
  tests/test_verify_reproduces_the_launcher.py
  tests/test_llm_sdk_pin.py
  # install-sweep instruments (see misc/install_sweep.md): the cost vocabulary,
  # the probe's own gates, and the placement oracle. Pure — no live server.
  tests/test_regtest_cost_ceilings.py
  tests/test_regtest_job_placement.py
  tests/test_regtest_coverage_matrix.py
  tests/test_install_probe_gate.py
  # agent-facing surfaces hardened alongside them
  tests/test_bug_report_composer.py
  tests/test_credential_truthfulness.py
  tests/test_inspect_upload_paths.py
  # regtest oracle + probe evaluators (pure, no live server)
  tests/test_transport_oracle.py
  tests/test_live_surface_probe_eval.py
  tests/test_sweep_baseline_honesty.py
  tests/test_regtest_requirements.py
  # backend/tests/ — the env-realization + runtime-shape guards. This whole
  # DIRECTORY was unrunnable from the repo root (its files import `core.*`,
  # which needs backend/ on sys.path), so none of it had ever been listed here
  # and two guards in test_named_envs_ready.py had been red since
  # _run_realize_task grew a second return value, with nothing to notice.
  # backend/tests/conftest.py now bootstraps the path for the directory.
  backend/tests/test_realize_verb.py
  backend/tests/test_named_envs_ready.py
  backend/tests/test_named_envs_run_in.py
  backend/tests/test_config_runtime_dir.py
  backend/tests/test_nextflow_weft_lane.py
  backend/tests/test_slurm_entry_activation.py
  backend/tests/test_project_delete_reclaims.py
  backend/tests/test_addition_records_its_repos.py
  backend/tests/test_orphan_reclaim.py
  backend/tests/test_source_builds_have_a_toolchain.py
  backend/tests/test_failures_name_their_cause.py
  backend/tests/test_lane_spellings.py
  backend/tests/test_tool_dispatch_is_concurrent.py
  backend/tests/test_reattach_boundary.py
  # runtime translation
  tests/test_openai_runtime_translation.py
  tests/test_openai_runtime_skeleton.py
  tests/test_chat_scenarios.py
  # ── promoted 2026-07-22 (suite-census audit: green per-file on a bare
  #    box; see tests/test_suite_census.py for the accounting rules) ──
  tests/test_add_result_member.py
  tests/test_agent_sees_parity.py
  tests/test_annotation_note_ephemeral.py
  tests/test_artifacts.py
  tests/test_background_timeout.py
  tests/test_batch_submitter.py
  tests/test_behavior_surprise_carveout.py
  tests/test_bring_back.py
  tests/test_bundle_envs.py
  tests/test_by_title_dispatcher.py
  tests/test_by_title_primitives.py
  tests/test_by_title_scribe.py
  tests/test_cancel_notifies.py
  tests/test_cell_entity.py
  tests/test_cell_mcp.py
  tests/test_cleanup_shadows.py
  tests/test_close_run_retention.py
  tests/test_compute_env.py
  tests/test_compute_inference.py
  tests/test_compute_preflight.py
  tests/test_compute_router.py
  tests/test_continuation_message.py
  tests/test_convert_cache.py
  tests/test_credential_gate.py
  tests/test_credentials.py
  tests/test_cutover_invariant.py
  tests/test_dataset_exec_link.py
  tests/test_datasets_mechanism.py
  tests/test_deploy_forward_loop.py
  tests/test_direct_api_runtime_skeleton.py
  tests/test_discovery_env_gate.py
  tests/test_empty_turn_defense.py
  tests/test_ensure_capability_candidates.py
  tests/test_ensure_capability_module_gate.py
  tests/test_ensure_capability_multi.py
  tests/test_ensure_capability_r_gate.py
  tests/test_env_gate_pref.py
  tests/test_env_packs.py
  tests/test_env_profile.py
  tests/test_env_registry_concurrency.py
  tests/test_env_registry.py
  tests/test_exec_modules.py
  tests/test_exec_records_cutover1.py
  tests/test_exec_records_cutover4.py
  tests/test_exec_records_stage2.py
  tests/test_exec_records_stage3.py
  tests/test_exec_records_stage4.py
  tests/test_exec_records.py
  tests/test_exec_streaming.py
  tests/test_external_ref.py
  tests/test_fake_runtime.py
  tests/test_find_file_node.py
  tests/test_followon_promotion_rule.py
  tests/test_fresh_kernel_preamble.py
  tests/test_frontmatter.py
  tests/test_generation_metrics.py
  tests/test_harvest_helpers.py
  tests/test_history_prep_empty_text.py
  tests/test_hpc_config.py
  tests/test_hpc_qos_live.py
  tests/test_inject_accelerator.py
  tests/test_invariants.py
  tests/test_job_env_canary.py
  tests/test_job_hpc_info_routing.py
  tests/test_job_run_attribution.py
  tests/test_job_runlog.py
  tests/test_jobs_archive.py
  tests/test_keep_disk_truth.py
  tests/test_kernel_pool_eviction.py
  tests/test_kernel_setup_env_parity.py
  tests/test_lazy_session_lane.py
  tests/test_llm_default.py
  tests/test_llm_errors_no_credential.py
  tests/test_llm_logging.py
  tests/test_llm_proxy.py
  tests/test_manifest_nfs_resilience.py
  tests/test_module_python_guard.py
  tests/test_modules_api.py
  tests/test_modules_first_use.py
  tests/test_modules_manifests.py
  tests/test_modules_probes.py
  tests/test_modules_reconciler.py
  tests/test_modules_registry.py
  tests/test_nextflow_provisioning.py
  tests/test_nextflow_resources.py
  tests/test_nextflow_schema.py
  tests/test_nf_task_estimate.py
  tests/test_no_workspace_hardcode.py
  tests/test_oauth_flow.py
  tests/test_oauth_per_provider_gating.py
  tests/test_oauth_refresh_store.py
  tests/test_ood_template_contracts.py
  tests/test_enroll_group.py
  tests/test_gpu_env_routing.py
  tests/test_open_viewer.py
  tests/test_option_b_phase6.py
  tests/test_output_manifest_stores.py
  tests/test_pagoda3_session_exec.py
  tests/test_patch_metadata.py
  tests/test_per_project_model.py
  tests/test_phase1_1c.py
  tests/test_phase2_backfill.py
  tests/test_phase2_derivation.py
  tests/test_phase2_promote.py
  tests/test_phase2_tool_actor.py
  tests/test_phase3_registry.py
  tests/test_phase3_store.py
  tests/test_pin_artifact.py
  tests/test_pin_idempotency.py
  tests/test_plan_prefill.py
  tests/test_present_plan_schema_and_recovery.py
  tests/test_prewarm_status.py
  tests/test_project_pinning_coverage.py
  tests/test_projects_concurrent_delete.py
  tests/test_provider_credentials.py
  tests/test_r_install_recovery.py
  tests/test_r_validate_install.py
  tests/test_reasoning_port.py
  tests/test_recipe_produces_in_scope.py
  tests/test_reclaim_env_evict.py
  tests/test_register_dataset_remote.py
  tests/test_regen_completeness.py
  tests/test_regtest_seed_guard.py
  tests/test_release_component_identity.py
  tests/test_release_lifecycle.py
  tests/test_relink.py
  tests/test_reload_exclude.py
  tests/test_responses_translate.py
  tests/test_restart_orphan.py
  tests/test_result_naming_synthesis.py
  tests/test_retired_env_vars.py
  tests/test_revision_pin_follows.py
  tests/test_revisions_http.py
  tests/test_revisions_mcp.py
  tests/test_route_table.py
  tests/test_router_env.py
  tests/test_run_archive.py
  tests/test_output_door_census.py
  tests/test_durable_route_convoy.py
  tests/test_run_durable_view.py
  tests/test_run_keep.py
  tests/test_run_manifest_artifacts.py
  tests/test_run_purge_on_delete.py
  tests/test_runtime_protocols.py
  tests/test_runtime_selector.py
  tests/test_scribe_bulk_recover.py
  tests/test_scribe_compaction.py
  tests/test_scribe_compat_report.py
  tests/test_scribe_drift.py
  tests/test_scribe_entity_hooks.py
  tests/test_scribe_id_collision.py
  tests/test_scribe_walker_robustness.py
  tests/test_scribe_writers.py
  tests/test_sdk_runtime_skeleton.py
  tests/test_search_tier_note.py
  tests/test_selfcheck.py
  tests/test_services_seam.py
  tests/test_serving_spine.py
  tests/test_settle_deferred.py
  tests/test_sif_glibc_floor.py
  tests/test_sif_ships_operator_scripts.py
  tests/test_store_serve.py
  tests/test_store_zip.py
  tests/test_summary_budget_precedence.py
  tests/test_surface_parity_oracle.py
  tests/test_tool_dedup_p3.py
  tests/test_tool_routing_docstrings.py
  tests/test_tool_smoke.py
  tests/test_update_entity_fields_broadcast.py
  tests/test_update_member_caption.py
  tests/test_verify.py
  tests/test_viewer_launchers.py
  tests/test_viewer_link_resolution.py
  tests/test_viewer_link_run_key.py
  tests/test_viewer_prepare.py
  tests/test_viewer_store_contract.py
  tests/test_remote_store_contract.py
  tests/test_pagoda3_launcher.py
  tests/test_viewer_weft_resolution.py
  tests/test_weft_kernel_session.py
  tests/test_weft_sync_finalize.py
)

# Standalone scripts (no pytest-collectable functions — run via __main__).
STANDALONE=(
  tests/test_chat_attachments.py     # api_messages validity guard
  # promoted 2026-07-22: substrate-clean script-style guards (pytest
  # collects 0 in these — running them via pytest would green-wash)
  tests/test_bundle_refsources.py
  tests/test_delete_revision.py
  tests/test_delete_revision_http.py
  tests/test_list_revisions.py
  tests/test_ood_session_refs.py
  tests/test_provenance_depth.py
  tests/test_refs_placement.py
  tests/test_refs_project_tier.py
  tests/test_refs_resolve.py
  tests/test_refs_tiers.py
  tests/test_revision_language.py
  tests/test_set_current_revision.py
  tests/test_sidebar_dataset_hint.py
  tests/test_turn_serialization.py
)

# ── parallel runner ──────────────────────────────────────────────────────────
# Per-file processes are the ISOLATION contract (cross-file import
# interference is why this suite exists); parallelism is free on top of it.
# JOBS=<n> overrides; JOBS=1 restores strictly serial execution for
# debugging a suspected inter-test collision.
JOBS="${JOBS:-$( (command -v nproc >/dev/null 2>&1 && nproc) \
                 || sysctl -n hw.ncpu 2>/dev/null || echo 4 )}"
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT
export PY TMPD

run_one() {  # $1 = pytest|script, $2 = file
  local mode="$1" f="$2"
  local log="$TMPD/$(echo "$f" | tr '/' '_').log"
  if [ ! -f "$f" ]; then
    echo "$f  (MISSING — renamed or deleted? update this list)" >> "$TMPD/fails"
    return
  fi
  local rc=0
  if [ "$mode" = script ]; then
    "$PY" "$f" > "$log" 2>&1 || rc=$?
  else
    # per-process basetemp: concurrent pytests race on the shared
    # /tmp/pytest-of-<user>/pytest-current symlink otherwise
    "$PY" -m pytest "$f" -q --no-header \
        --basetemp="$TMPD/bt_$(echo "$f" | tr '/' '_')" > "$log" 2>&1 || rc=$?
  fi
  if [ "$rc" -ne 0 ]; then
    echo "$f" >> "$TMPD/fails"
  fi
}
export -f run_one

{ printf 'pytest %s\n' "${FILES[@]}"; printf 'script %s\n' "${STANDALONE[@]}"; } \
  | xargs -P "$JOBS" -n 2 bash -c 'run_one "$1" "$2"' _

if [ -s "$TMPD/fails" ]; then
  echo "──── failing files ────"
  while IFS= read -r f; do
    echo "GUARD FAIL: $f"
    log="$TMPD/$(echo "${f%% *}" | tr '/' '_').log"
    [ -f "$log" ] && tail -25 "$log" | sed 's/^/    /'
  done < <(sort -u "$TMPD/fails")
  echo "GUARD SUITE FAILED ($(sort -u "$TMPD/fails" | wc -l | tr -d ' ') of ${#FILES[@]}+${#STANDALONE[@]} files)"
  exit 1
fi
echo "GUARD SUITE OK (${#FILES[@]} pytest + ${#STANDALONE[@]} standalone files, each in its own process, JOBS=$JOBS)"
exit 0
