#!/usr/bin/env bash
# THE LAUNCH CONTRACT — one implementation of `apptainer run` for every consumer.
#
# WHY THIS FILE EXISTS. ABA was launched two ways that were supposed to be the
# same launch: the OOD card (template/script.sh.erb — what users actually get)
# and the deployment gate (aba-vbc/verify.sh — what decides a release is fit to
# promote). They were written independently and drifted, so the gate booted a
# configuration production never runs:
#
#   * no ABA_BATCH_SUBMITTER  -> submitter_name() read an unset var inside
#     --containall, returned "local", and every "background job" ran IN-PROCESS
#     on the gate's own node. The wf_slurm_batch lane reported a passing Slurm
#     offload having never submitted anything to Slurm.
#   * no scheduler binds      -> sbatch/munge/passwd absent, so even with the
#     submitter set the lane could only fail, and each attempt to fix it
#     reinvented a bind set that the card had already had right for months.
#   * no ABA_JOBS_GPU_ENV_PACK-> GPU routing (_gpu_env_for) never fired.
#   * no module plumbing      -> in-session `module load` untested.
#   * no ABA_EXTRA_BINDS      -> a site's own paths invisible.
#
# So the gate certified a launch nobody performs. That is the green-gate failure
# in its purest form, and the fix is structural, not another patch: there is now
# ONE place that turns a resolved ABA environment into `apptainer run` argv, and
# both consumers source it. A bind the card needs is a bind the gate exercises,
# automatically and forever.
#
# CONTRACT. Source this file, then call `aba_launch_args`. It APPENDS to the
# caller's `binds` and `envs` arrays (both must already exist, possibly empty):
#
#     binds=(); envs=()
#     . "<dir>/aba_launch.sh"
#     aba_launch_args
#     apptainer run --containall "${binds[@]}" "${envs[@]}" "$ABA_SIF"
#
# INPUTS are the environment aba_preflight.py resolved from site.yaml and wrote
# to aba-env.sh — ABA_SITE_CONFIG, ABA_SHARE, ABA_RUNTIME_DIR, ABA_EXTRA_BINDS,
# ABA_BASE_DIR, ABA_TOOLS_DIR, ABA_MODULE_*, ABA_BATCH_SUBMITTER and the rest of
# the forward list. Callers get that env the same way: run preflight.sh, source
# aba-env.sh. Nothing here reads site.yaml itself; nothing here invents a value.
#
# WORKDIR. Two small files are synthesized (nss/passwd+group, .modenv) under
# $ABA_LAUNCH_WORKDIR, default $PWD — the OOD session dir for the card, the
# throwaway workspace for the gate.
#
# OUTPUT. Sets ABA_LAUNCH_TMP_CLEANUP to a session tmp dir the caller created
# and must remove on exit (empty when TMPDIR came from Slurm, which purges it).
#
# Sourced, never executed: no `set -e` here, and every probe is a no-op when the
# thing it looks for is absent, so the same file works on a Slurm node, a bare
# node, and a laptop.

aba_launch_scope_binds() {
  # /groups + the deployment root also cover the reference-store tiers (refs.md):
  # the group refs at /groups/<group>/aba/refs and the optional institution refs
  # at <share>/refs are already inside these mounts, and ABA_SITE_CONFIG +
  # ABA_GROUP are forwarded below — so the backend resolves + reads refs with no
  # refs-specific bind or env. (If a site puts institution refs OUTSIDE the
  # deployment root, list it in site.yaml `binds:`.)
  [ -d /groups ] && binds+=( --bind /groups )
  # /dev/fuse: --containall does NOT expose it, but weft needs it to MOUNT published
  # squashfs env packs read-only (its squashfs_mode probe checks `-e /dev/fuse`).
  # Without it — even with squashfuse/mksquashfs baked in the image — weft falls back
  # to realizing each pack from its lockfile PER USER (slow, unshared). Bind the host
  # node's device in (it's world-rw).
  [ -e /dev/fuse ] && binds+=( --bind /dev/fuse )
  # /dev/shm: node-local shared memory. Kernels and rattler/pixi caches want a real
  # one; --containall otherwise gives a tiny private tmpfs.
  [ -d /dev/shm ] && binds+=( --bind /dev/shm )
  # The share root (site.yaml, image, skeleton, institution bundle + its refs tier).
  # Derived, not hardcoded: a site may root the deployment anywhere its nodes can read.
  _share="${ABA_SHARE:-$(dirname "${ABA_SITE_CONFIG:-/cluster/aba/site.yaml}")}"
  [ -d "${_share}" ] && binds+=( --bind "${_share}" )
  # Also bind the DEPLOYMENT root (the dir holding site.yaml). Under the versioned
  # layout ABA_SHARE is the release_root (…/app), so this is what covers the siblings
  # envs/ (the published weft images consumers adopt), installation/, refs/. Skip if
  # it's the same dir or already under a bound tree like /groups.
  _deploy_root="$(dirname "${ABA_SITE_CONFIG:-/cluster/aba/site.yaml}")"
  case "${_deploy_root}/" in
    "${_share}/"*|/groups/*) ;;                                  # already covered
    *) [ -d "${_deploy_root}" ] && binds+=( --bind "${_deploy_root}" ) ;;
  esac
  # The published env store, when a deployment keeps it outside both roots above.
  # One store serves staging and production (docs/arch/envs.md), so it is NOT
  # necessarily a sibling of site.yaml.
  for _t in "${ABA_WEFT_PUBLISH_TREE:-}" "${ABA_ENVS_DIR:-}"; do
    [ -n "${_t}" ] && [ -d "${_t}" ] || continue
    case "${_t}/" in
      "${_share}/"*|"${_deploy_root}/"*|/groups/*) ;;
      *) binds+=( --bind "${_t}" ) ;;
    esac
  done
  # site.yaml `binds:` (→ ABA_EXTRA_BINDS): any further host paths the deployment
  # needs visible under --containall — e.g. a group/user tree outside /groups.
  for _b in ${ABA_EXTRA_BINDS:-}; do [ -e "$_b" ] && binds+=( --bind "$_b" ); done
  # slim image: mount the shared conda base (+ R base) the image expects at /opt.
  [ -n "${ABA_BASE_DIR:-}" ] && binds+=( --bind "${ABA_BASE_DIR}:/opt/aba-venv" )
  [ -n "${ABA_TOOLS_DIR:-}" ] && [ -d "${ABA_TOOLS_DIR}" ] \
    && binds+=( --bind "${ABA_TOOLS_DIR}:/opt/aba-envs/tools" )
  # `return 0` is LOAD-BEARING, not tidiness. Every append here is guarded by a
  # test, so when the last guard is false the function's status is that false
  # test — and a caller running `set -e` (the deployment gate does; the card does
  # not) dies at the point the contract finished its job correctly. Same defect
  # as the one aba-env.sh had: a file or function whose exit status reports "was
  # the last optional thing present?" instead of "did this succeed?".
  return 0
}

aba_launch_session_tmp() {
  # In-session pip/uv (weft session_install) unpack wheels under $TMPDIR. Under
  # --containall /tmp is a small (~64MB) in-memory tmpfs, so a scientific-scale add
  # (pyarrow, polars, torch, scipy) ENOSPCs mid-unpack and surfaces as a confusing
  # env.solve_conflict "No space left on device". Prefer NODE-LOCAL job scratch
  # ($SLURM_TMPDIR: fast local disk, purged by Slurm at job end); fall back to a
  # PER-SESSION dir under the user's runtime tree (real disk via the /groups bind —
  # parallel-FS, so slower for many-small-file unpacks, and not auto-purged: the
  # CALLER removes ABA_LAUNCH_TMP_CLEANUP from its single EXIT handler, so debris
  # can't accrue against quota across sessions). The tmpfs /tmp stays for
  # sockets/small files. weft's LocalAdapter inherits this env when it shells out.
  ABA_LAUNCH_TMP_CLEANUP=""
  if [ -n "${SLURM_TMPDIR:-}" ] && [ -d "${SLURM_TMPDIR}" ]; then
    _sess_tmp="${SLURM_TMPDIR}"                       # node-local; Slurm purges it
  else
    _sess_tmp="${ABA_RUNTIME_DIR:-${ABA_LAUNCH_WORKDIR:-$PWD}}/tmp/session-${SLURM_JOB_ID:-$$}"
    mkdir -p "$_sess_tmp"
    ABA_LAUNCH_TMP_CLEANUP="$_sess_tmp"               # only WE made it → caller rm -rf's it
  fi
  envs+=( --env "TMPDIR=${_sess_tmp}" )
  # `return 0` is LOAD-BEARING, not tidiness. Every append here is guarded by a
  # test, so when the last guard is false the function's status is that false
  # test — and a caller running `set -e` (the deployment gate does; the card does
  # not) dies at the point the contract finished its job correctly. Same defect
  # as the one aba-env.sh had: a file or function whose exit status reports "was
  # the last optional thing present?" instead of "did this succeed?".
  return 0
}

aba_launch_forward_env() {
  # ABA_SIF is forwarded so the backend knows which image it is running from.
  # ABA_JOB_WRAP rides along for deployments that DECLARE image.job_wrap; it is no
  # longer derived from the image shape, and no weft submit lane wraps. Under this
  # (weft) profile the controller runtime exists only inside the image, which is why
  # a cluster site here resolves to the DETACHED contract: offloaded jobs ship their
  # code as data and run the node's own interpreter inside a weft-mounted science
  # env. No re-entry, by design — see docs/arch/envs.md §Shared-FS reachability.
  # ABA_BATCH_SUBMITTER is the SELECTOR that decides local-vs-slurm; it MUST be forwarded
  # or submitter_name() reads an unset var inside --containall, returns "local", and every
  # "background" job silently runs IN-PROCESS on the session node instead of on Slurm —
  # which also makes the scheduler block below dead code, and makes run_nextflow's
  # env-blocker (keyed on submitter==slurm) wrongly refuse nf-core because no container
  # engine is on PATH *inside* the image (the engine lives on the compute node). ABA_HPC_CONFIG
  # rides along (partition/QOS catalog) for the same reason. ABA_MODULE_INIT is forwarded so an
  # OFFLOADED bare job (e.g. the nf-core Nextflow head) can re-init the site's module system on
  # its compute node — job.sh's first `module`-init candidate is "${ABA_MODULE_INIT:-}"; without
  # it the head fails `module: command not found` → `nextflow: command not found` (exit 127).
  #
  # SOURCE OF TRUTH for this list = the backend settings registry's deploy_injected
  # set (config.deploy_injected_keys(), = `aba settings --deploy-env`). ERB can't call
  # Python at render, so this list is a MIRROR kept in sync by
  # tests/test_deploy_forward_loop.py — add a var here AND mark it deploy_injected in
  # core/config.py, or the guard fails.
  for v in ABA_RUNTIME_DIR ABA_ENVS_DIR ABA_SITE_CONFIG ABA_GROUP ABA_MODEL ABA_HOME \
           ABA_SHARE ABA_RELEASE_ID ABA_SIF ABA_JOB_WRAP ABA_APPTAINER_TMPDIR \
           ABA_BATCH_SUBMITTER ABA_HPC_CONFIG ABA_MODULE_INIT ABA_MODULE_BINDS ABA_SUBSCRIPTION_OAUTH \
           ABA_NEXTFLOW_MODULE ABA_NEXTFLOW_PROFILES ABA_NEXTFLOW_CONFIG ABA_NEXTFLOW_CACHEDIR \
           ABA_WEFT_PUBLISH_TREE ABA_COMPUTE_SELF_SERVICE ABA_JOBS_GPU_ENV_PACK \
           ABA_FRONTEND_DIST ABA_PORT \
           ANTHROPIC_API_KEY ABA_LLM_CREDENTIAL CLAUDE_CODE_OAUTH_TOKEN; do
    eval "_v=\${$v:-}"; [ -n "${_v}" ] && envs+=( --env "$v=${_v}" )
  done
  # `return 0` is LOAD-BEARING, not tidiness. Every append here is guarded by a
  # test, so when the last guard is false the function's status is that false
  # test — and a caller running `set -e` (the deployment gate does; the card does
  # not) dies at the point the contract finished its job correctly. Same defect
  # as the one aba-env.sh had: a file or function whose exit status reports "was
  # the last optional thing present?" instead of "did this succeed?".
  return 0
}

aba_launch_scheduler() {
  # --- Slurm offload plumbing: expose the host scheduler to the --containall
  # backend so it can `sbatch` background jobs. Bind the client binaries + the
  # libslurm plugin dir + SLURM_CONF + the munge socket/lib, plus a SYNTHESIZED
  # passwd/group holding the runtime user + slurm/munge (via getent — LDAP users
  # aren't in the image passwd and containall has no SSSD). Host RHEL binaries run
  # on the image's newer glibc (backward-compatible). No-op off Slurm.
  # NB `sacctmgr` is in the list, not just for symmetry: hpc_config.qos_account_live()
  # shells out to it to discover the user's QOS + account + each QOS's MaxWall cap.
  # Without it bound, discovery returns empty inside the SIF, jobs fall back to the
  # cluster's DEFAULT QOS + uncapped walltime, and a long request (e.g. the 24h
  # nextflow head) is rejected QOSMaxWallDurationPerJobLimit. Binding it lets the
  # slim SIF self-configure QOS/account on any accounting cluster — no hpc.yaml.
  command -v sbatch >/dev/null 2>&1 || return 0
  for _b in sbatch squeue scancel sacct sacctmgr sinfo scontrol salloc srun; do
    _p=$(command -v "$_b" 2>/dev/null) && binds+=( --bind "$_p" )
  done
  # `|| true` on every OPTIONAL probe. A caller with `pipefail` (the deployment
  # gate) takes the pipeline's status from the first failing stage, so a node
  # where `ldd`/`ldconfig` is simply not on PATH aborts the launch instead of
  # skipping a bind that was never required.
  _sld=$(ldd "$(command -v sbatch)" 2>/dev/null | awk '/libslurm/{print $3}' | head -1 || true)
  [ -n "$_sld" ] && binds+=( --bind "$(dirname "$_sld")" )
  _scf="${SLURM_CONF:-/etc/slurm/slurm.conf}"
  [ -f "$_scf" ] && { binds+=( --bind "$(dirname "$_scf")" ); envs+=( --env "SLURM_CONF=$_scf" ); }
  for _m in /run/munge /var/run/munge; do [ -S "$_m/munge.socket.2" ] && binds+=( --bind "$_m" ); done
  _ml=$(ldconfig -p 2>/dev/null | awk '$1=="libmunge.so.2"{print $NF; exit}' || true)
  [ -n "$_ml" ] && binds+=( --bind "$_ml" )
  _nss="${ABA_LAUNCH_WORKDIR:-$PWD}/nss"; mkdir -p "$_nss"
  getent passwd "$(id -un)" slurm munge root nobody > "$_nss/passwd" 2>/dev/null || true
  getent group  "$(id -gn)" slurm munge root nobody > "$_nss/group"  2>/dev/null || true
  [ -s "$_nss/passwd" ] && binds+=( --bind "$_nss/passwd:/etc/passwd" )
  [ -s "$_nss/group" ]  && binds+=( --bind "$_nss/group:/etc/group" )
  # `return 0` is LOAD-BEARING, not tidiness. Every append here is guarded by a
  # test, so when the last guard is false the function's status is that false
  # test — and a caller running `set -e` (the deployment gate does; the card does
  # not) dies at the point the contract finished its job correctly. Same defect
  # as the one aba-env.sh had: a file or function whose exit status reports "was
  # the last optional thing present?" instead of "did this succeed?".
  return 0
}

aba_launch_modules() {
  # --- Host environment-modules plumbing (site.yaml `modules:` → ABA_MODULE_* in
  # aba-env.sh): bind the cluster's Lmod + modulefiles + tool trees so IN-SESSION
  # `module load` works inside the SIF. Only sound when the base image's glibc matches
  # the nodes' (build.sh enforces) — then these host EL libs are ABI-compatible; the
  # backend's core/exec/modules.py then finds LMOD_PKG/init/bash (bound) and activates.
  # Resolve on THIS node: source the init for MODULEPATH/LMOD_*, ldconfig the libs.
  # No-op when unset. (Offloaded Slurm jobs load modules natively on the bare node.)
  [ -n "${ABA_MODULE_INIT:-}" ] && [ -f "${ABA_MODULE_INIT}" ] || return 0
  _modenv="${ABA_LAUNCH_WORKDIR:-$PWD}/.modenv"
  ( . "${ABA_MODULE_INIT}" >/dev/null 2>&1
    for v in MODULEPATH MODULESHOME LMOD_CMD LMOD_DIR LMOD_PKG LMOD_ROOT LMOD_SYSTEM_DEFAULT_MODULES \
             LMOD_RC LMOD_PACKAGE_PATH LMOD_SYSHOST; do
      eval "_x=\${$v:-}"; [ -n "$_x" ] && echo "$v=$_x"; done ) > "${_modenv}" || true
  while IFS='=' read -r _k _v; do [ -n "$_v" ] && envs+=( --env "${_k}=${_v}" ); done < "${_modenv}"
  for _b in ${ABA_MODULE_BINDS:-}; do [ -e "$_b" ] && binds+=( --bind "$_b" ); done
  _hostlib=0
  for _l in ${ABA_MODULE_LIBS:-}; do
    _f=$(ldconfig -p 2>/dev/null | awk -v L="$_l" '$1==L{print $NF; exit}' || true)
    [ -n "$_f" ] && { binds+=( --bind "$_f:/opt/hostlibs/$_l" ); _hostlib=1; }
  done
  [ "$_hostlib" = 1 ] && envs+=( --env "LD_LIBRARY_PATH=/opt/hostlibs" )
  return 0
}

aba_launch_args() {
  aba_launch_scope_binds
  aba_launch_session_tmp
  aba_launch_forward_env
  aba_launch_scheduler
  aba_launch_modules
  return 0
}
