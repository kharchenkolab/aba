#!/usr/bin/env python3
"""aba-preflight — read site.yaml, discover/auto-create scope dirs (with a
safety check), resolve credentials (api-key OR oauth, incl. a group-shared
key), and emit aba-env.sh (env block) + status.yaml (session card).

Bridges the site's conventions to the env vars ABA expects, and sets
ABA_SITE_CONFIG so ABA's own scope_resolver reads the same site.yaml for the
bundle scopes.

Inputs (env): ABA_SITE_CONFIG (default /cluster/aba/site.yaml), ABA_PF_GROUP,
ABA_PF_USER, ABA_PF_HOME, ABA_PF_TOKEN (pasted key), ABA_PF_STAGED.
Exit 10 = blocked — before.sh must NOT launch. status.yaml `blocked_on` says why:
  group not enrolled (no /groups/<group>/aba workspace), or a foreign same-named folder.
"""
import json, os, shutil, sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("aba-preflight: PyYAML missing\n"); sys.exit(3)

# A group ABA folder is "ours" iff it has one of these markers (the skeleton
# creates .aba-workspace). Prevents launching into / clobbering a same-named
# folder a group made for something else.
OURS_MARKERS = (".aba-workspace", ".bundle", ".envs")
BLOCKED_EXIT = 10


def expand(s, *, user, group, home):
    if not isinstance(s, str):
        return s
    return (s.replace("{user}", user or "").replace("{group}", group or "")
             .replace("{home}", str(home or "")))


def shq(v):
    return "'" + str(v).replace("'", "'\"'\"'") + "'"


def read_cred_file(p):
    """Return ('apikey', key) | ('oauth', token) | (None, None)."""
    try:
        d = json.loads(Path(p).read_text())
    except Exception:  # noqa: BLE001
        return (None, None)
    if not isinstance(d, dict):
        return (None, None)
    if d.get("anthropic_api_key"):
        return ("apikey", d["anthropic_api_key"])
    if d.get("claude_code_oauth_token"):
        return ("oauth", d["claude_code_oauth_token"])
    return (None, None)


def ensure_group_writable(path, group_name, warnings):
    """Make a lab-shared dir group-writable with the setgid bit (mode 2775) so
    members share refs and new files INHERIT the lab group (refs.md §8). chgrp to
    the lab's unix group when it exists and we're a member; otherwise leave
    ownership and warn (non-fatal). Returns the resolved gid or None."""
    import grp
    try:
        os.chmod(path, 0o2775)   # rwxrwsr-x: setgid (children inherit group) + group-writable
    except OSError as e:  # noqa: BLE001
        warnings.append(f"chmod 2775 {path}: {e}")
    if not group_name:
        return None
    try:
        gid = grp.getgrnam(group_name).gr_gid
    except KeyError:
        warnings.append(f"unix group {group_name!r} not found — left refs group ownership as-is")
        return None
    try:
        os.chown(path, -1, gid)
        return gid
    except PermissionError:
        warnings.append(f"not permitted to chgrp {path} to {group_name!r} (not a member?)")
    except OSError as e:  # noqa: BLE001
        warnings.append(f"chgrp {path} -> {group_name!r}: {e}")
    return None


def resolve_release_image(release_root):
    """Pin-on-launch for a VERSIONED (slim) deploy. Resolve `<release_root>/current` → the release
    it names RIGHT NOW and return that session's image env: {ABA_RELEASE_ID, ABA_SHARE, ABA_SIF,
    ABA_BASE_DIR, ABA_TOOLS_DIR} (only keys that resolve). Paths point at the CONCRETE
    `releases/<id>/…` (not via `current`), so if an admin promotes a new release mid-session THIS
    session keeps the one it launched on. {} when there's no versioned layout (→ caller falls back
    to the static site.yaml image paths, i.e. fat / non-versioned slim are unchanged)."""
    import glob
    cur = os.path.join(release_root, "current")
    if not os.path.islink(cur):
        return {}
    rdir = os.path.realpath(cur)               # concrete releases/<id> — the pin
    out = {"ABA_RELEASE_ID": os.path.basename(rdir), "ABA_SHARE": release_root}
    sifs = sorted(glob.glob(os.path.join(rdir, "sif", "*.sif")))
    if sifs:
        out["ABA_SIF"] = sifs[0]
    venv = os.path.join(rdir, "env", "aba-venv")
    if os.path.isdir(venv):
        out["ABA_BASE_DIR"] = venv
    tools = os.path.join(rdir, "env", "aba-tools")
    if os.path.isdir(tools):
        out["ABA_TOOLS_DIR"] = tools
    return out


def main():
    site_path = Path(os.environ.get("ABA_SITE_CONFIG") or "/cluster/aba/site.yaml")
    group = (os.environ.get("ABA_PF_GROUP") or "").strip()
    user = (os.environ.get("ABA_PF_USER") or os.environ.get("USER") or "user").strip()
    home = os.environ.get("ABA_PF_HOME") or os.path.expanduser("~")
    token = (os.environ.get("ABA_PF_TOKEN") or "").strip()
    staged = Path(os.environ.get("ABA_PF_STAGED") or os.getcwd())

    site = {}
    if site_path.is_file():
        try:
            site = yaml.safe_load(site_path.read_text()) or {}
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"aba-preflight: malformed {site_path}: {e}\n")
            sys.exit(2)

    scopes = site.get("scopes") or {}
    gcfg, ucfg, icfg = scopes.get("group") or {}, scopes.get("user") or {}, scopes.get("institution") or {}
    creds = site.get("credentials") or {}
    warnings, blocked, blocked_reason = [], False, None
    # glibc-floor: preflight.sh (on the node) compares the image's base glibc to the
    # node's and passes a message here when the base is too new — surface it on the
    # session card so a mis-based image is visible at launch, not mid-calculation.
    _glibc_warn = (os.environ.get("ABA_PF_GLIBC_WARN") or "").strip()
    if _glibc_warn:
        warnings.append(_glibc_warn)

    def ex(s):
        return expand(s, user=user, group=group, home=home)

    # ---- group scope (enrollment gate + safety check) ----
    # A group is ENROLLED when /groups/<group>/aba exists with an ABA marker
    # (.aba-workspace) — stamped by an admin (enroll-group, or auto_create_skeleton).
    #   ours marker            → ok
    #   absent / empty + auto  → stamp it on the fly (opt-in convenience)
    #   absent / empty, no auto → NOT ENROLLED → block with an actionable message
    #                             (so a launch doesn't burn a Slurm slot to fail)
    #   exists, non-empty, no marker → FOREIGN → block (never clobber a same-named dir)
    group_state, group_detail, bundle_present, group_root = "disabled", "group scope disabled", False, None
    if gcfg.get("enabled") and group:
        site_name = (site.get("site") or {}).get("name")
        group_root = Path(ex(gcfg.get("root_path") or "/groups/{group}/aba"))
        bundle_dir = group_root / gcfg.get("bundle_subdir", "bundle")
        tmpl = gcfg.get("skeleton_template")
        auto = bool(gcfg.get("auto_create_skeleton"))
        can_stamp = bool(tmpl and Path(ex(tmpl)).is_dir())
        not_enrolled = (gcfg.get("not_enrolled_message")
                        or (f"Group '{group}' is not enrolled in ABA"
                            + (f" at {site_name}" if site_name else "")
                            + f". Ask an admin to enroll it (creates {group_root})."))

        def _create():
            # auto_create_skeleton (opt-in): stamp the skeleton if configured, else
            # drop the bare .aba-workspace marker. (Manual enrollment uses the same
            # skeleton via the enroll-group helper.)
            group_root.mkdir(parents=True, exist_ok=True)
            if can_stamp:
                shutil.copytree(ex(tmpl), group_root, dirs_exist_ok=True)
            else:
                (group_root / ".aba-workspace").touch()
            return "skeleton_just_created", f"new ABA workspace created at {group_root}"

        if group_root.exists():
            looks_ours = any((group_root / m).exists() for m in OURS_MARKERS)
            try:
                is_empty = not any(group_root.iterdir())
            except Exception:  # noqa: BLE001
                is_empty = False
            if looks_ours:
                group_state, group_detail = "ok", str(group_root)
            elif is_empty and auto:
                group_state, group_detail = _create()
            elif is_empty:
                blocked, blocked_reason = True, not_enrolled          # empty, not auto → not enrolled
                group_state, group_detail = "not_enrolled", not_enrolled
            else:
                # SAFETY: a same-named folder that isn't an ABA workspace.
                blocked = True
                blocked_reason = (f"{group_root} exists but is not an ABA workspace "
                                  f"(no {'/'.join(OURS_MARKERS)} marker) — refusing to launch")
                group_state, group_detail = "foreign", blocked_reason
        elif auto:
            group_state, group_detail = _create()
        else:
            blocked, blocked_reason = True, not_enrolled              # absent, not auto → not enrolled
            group_state, group_detail = "not_enrolled", not_enrolled
        if not blocked:
            bundle_present = bundle_dir.is_dir() and any(
                p.name not in (".gitkeep",) for p in bundle_dir.iterdir())
            group_detail += "  (lab bundle present)" if bundle_present else "  (no lab bundle yet)"

    # ---- user scope ----
    state_dir = Path(ex(ucfg.get("state_dir") or f"{home}/.aba/state"))
    # Envs are PER-USER (the global + project growth over the shared read-only
    # base) — rooted under the user's own runtime, NOT a lab-shared group/.envs.
    # Configurable via user.envs_dir; defaults to <state_dir>/envs.
    envs_dir = Path(ex(ucfg["envs_dir"])) if ucfg.get("envs_dir") else (state_dir / "envs")
    if not blocked:
        state_dir.mkdir(parents=True, exist_ok=True)
        envs_dir.mkdir(parents=True, exist_ok=True)

    # ---- group refs tier (shared lab reference store, refs.md §3.3) ----
    # The backend reads the refs paths from site.yaml itself (via ABA_SITE_CONFIG
    # + its scope resolver), so there's no refs env var to emit — the preflight's
    # job is just to make the GROUP refs dir exist, group-writable + setgid, owned
    # by the lab group, so members can register and new files inherit the group.
    refs_cfg = site.get("refs") or {}
    refs_state, refs_detail, group_refs = "disabled", "no refs.group configured", None
    if not blocked and refs_cfg.get("group"):
        group_refs = Path(ex(refs_cfg["group"]))
        try:
            group_refs.mkdir(parents=True, exist_ok=True)
            ensure_group_writable(group_refs, group, warnings)
            refs_state, refs_detail = "ok", str(group_refs)
        except OSError as e:  # noqa: BLE001
            warnings.append(f"group refs dir {group_refs}: {e}")
            refs_state, refs_detail = "error", str(e)

    inst_path = icfg.get("bundle_path")
    inst_state = "absent" if not inst_path else ("ok" if Path(ex(inst_path)).is_dir() else "missing")

    # ---- credentials (chain from site.yaml; api-key OR oauth) ----
    user_key = Path(ex(creds.get("user_key_path") or f"{home}/.aba/credentials.json"))
    group_key = ex(creds.get("group_key_path")) if creds.get("group_key_path") else None
    order = creds.get("order") or ["user_saved", "user_form_paste"]
    cred_mode = cred_val = cred_source = None
    if not blocked:
        for src in order:
            if cred_mode:
                break
            if src == "user_saved":
                m, v = read_cred_file(user_key)
                if m:
                    cred_mode, cred_val, cred_source = m, v, "user_saved"
            elif src == "group_shared" and group_key:
                m, v = read_cred_file(group_key)
                if m:
                    cred_mode, cred_val, cred_source = m, v, "group_shared"
            elif src == "user_oauth":
                for p in (f"{home}/.aba/oauth.json", f"{home}/.claude/.credentials.json"):
                    if Path(p).is_file():
                        cred_mode, cred_source = "oauth_env", "user_oauth"
                        break
            elif src == "user_form_paste" and token:
                # Auto-detect what was pasted: Claude Code OAuth tokens are
                # `sk-ant-oat…`, API keys `sk-ant-api…`. An OAuth token pasted
                # here is the long-lived `claude setup-token` value — used as a
                # static bearer (CLAUDE_CODE_OAUTH_TOKEN), NOT auto-refreshable
                # (a single pasted string has no refresh token; tier-1 refresh
                # needs the seeded access+refresh store). API key → x-api-key.
                if token.startswith("sk-ant-oat"):
                    cred_mode, cred_val, cred_source = "oauth", token, "user_form_paste"
                    saved = {"claude_code_oauth_token": token}
                else:
                    cred_mode, cred_val, cred_source = "apikey", token, "user_form_paste"
                    saved = {"anthropic_api_key": token}
                user_key.parent.mkdir(parents=True, exist_ok=True)
                old = os.umask(0o077)
                try:
                    user_key.write_text(json.dumps(saved) + "\n")
                finally:
                    os.umask(old)
        if not cred_mode and creds.get("on_missing") != "demo_mode":
            warnings.append("no credentials resolved — paste a key on the launch form")

    # ---- write aba-env.sh (unless blocked) ----
    if not blocked:
        lines = [f"# generated by aba-preflight from {site_path}",
                 f"export ABA_SITE_CONFIG={shq(site_path)}"]
        if group:
            lines.append(f"export ABA_GROUP={shq(group)}")
        lines.append(f"export ABA_RUNTIME_DIR={shq(state_dir)}")
        lines.append(f"export ABA_ENVS_DIR={shq(envs_dir)}")
        # Subscription sign-in (Settings → Agent → Subscription): which OAuth flows the UI
        # offers. aba-preflight ONLY runs under the OOD launch — a reverse-proxied session —
        # so the safe maximum is `paste` (Anthropic's copy-code flow; core.oauth.enabled()
        # still refuses OpenAI's localhost:1455 callback here, which the remote browser can't
        # reach). ALWAYS emitted (not conditional): the container passthrough is forward-if-set,
        # so a producer must exist or the Subscription tab silently never appears. site.yaml
        # `credentials.subscription_signin` overrides — `off` to force API-key-only, `paste`
        # (default), or `all` (a NON-proxied deploy only; enabled() gates OpenAI regardless).
        # YAML 1.1 reads bare on/off/yes/no as BOOLEANS, so `subscription_signin: off`
        # arrives as False (not "off") — map it back, same as registry does for
        # default_state. Without this, `off` would silently fall through to the paste default.
        _sub = creds.get("subscription_signin")
        _sub = {True: "all", False: "off"}.get(_sub, _sub)
        sub_signin = str(_sub).strip().lower() if _sub not in (None, "") else "paste"
        lines.append(f"export ABA_SUBSCRIPTION_OAUTH={shq(sub_signin)}")
        # image: if site.yaml configures a SIF, the node launches FROM it; for a
        # slim image, base_dir/tools_dir are the shared base mounts it expects.
        # image.release_root (versioned deploy) takes precedence: pin this session to
        # <release_root>/current's release and derive sif/base/tools from it (see
        # misc/slim_sif_deploy.md). Absent → the static sif/base_dir/tools_dir below
        # (fat + non-versioned slim, unchanged).
        img = site.get("image") or {}
        relenv = resolve_release_image(ex(img["release_root"])) if img.get("release_root") else {}
        if relenv:
            for _k, _v in relenv.items():
                lines.append(f"export {_k}={shq(_v)}")
        else:
            if img.get("sif"):
                lines.append(f"export ABA_SIF={shq(ex(img['sif']))}")
            if img.get("base_dir"):
                lines.append(f"export ABA_BASE_DIR={shq(ex(img['base_dir']))}")
            if img.get("tools_dir"):
                lines.append(f"export ABA_TOOLS_DIR={shq(ex(img['tools_dir']))}")
        # background-job offload: site.yaml jobs.submitter (local|slurm) +
        # jobs.hpc_config (partition/QOS catalog). Lets backgrounded work sbatch
        # its own Slurm job instead of running in-process on the session node.
        jobs = site.get("jobs") or {}
        if jobs.get("submitter"):
            lines.append(f"export ABA_BATCH_SUBMITTER={shq(jobs['submitter'])}")
        if jobs.get("hpc_config"):
            lines.append(f"export ABA_HPC_CONFIG={shq(ex(jobs['hpc_config']))}")
        # host environment-modules (site.yaml `modules:`) — the site's Lmod init +
        # the paths/libs to bind so in-session `module load` works inside the SIF.
        # Emitted as plain space-joined strings (YAML parsed HERE, applied by
        # script.sh.erb on the node: source init → bind → forward MODULEPATH/LMOD_*).
        # Requires a base image whose glibc matches the nodes (see build.sh); no-op
        # when absent. See docs/install + core/exec/modules.py.
        mods = site.get("modules") or {}
        if mods.get("enabled"):
            if mods.get("init"):
                lines.append(f"export ABA_MODULE_INIT={shq(ex(mods['init']))}")
            if mods.get("binds"):
                lines.append(f"export ABA_MODULE_BINDS={shq(' '.join(ex(b) for b in mods['binds']))}")
            if mods.get("libs"):
                lines.append(f"export ABA_MODULE_LIBS={shq(' '.join(str(l) for l in mods['libs']))}")
        # nf-core / Nextflow (site.yaml `nextflow:`) — the cluster's nextflow module +
        # site profile/config so the OFFLOADED head runs pipelines: detection flips
        # run_nextflow True, and the Slurm head `module load`s nextflow + appends the
        # site profile (e.g. cbe → tasks via apptainer on the nodes). nextflow_config()
        # also reads hpc.yaml; this is the site.yaml route (consistent with modules:).
        # No-op when absent → run_nextflow stays False (in-workspace only).
        nf = site.get("nextflow") or {}
        if nf.get("module"):
            lines.append(f"export ABA_NEXTFLOW_MODULE={shq(nf['module'])}")
        if nf.get("profiles"):
            prof = nf["profiles"] if isinstance(nf["profiles"], str) else ",".join(nf["profiles"])
            lines.append(f"export ABA_NEXTFLOW_PROFILES={shq(prof)}")
        if nf.get("config"):
            lines.append(f"export ABA_NEXTFLOW_CONFIG={shq(ex(nf['config']))}")
        if nf.get("cachedir"):
            lines.append(f"export ABA_NEXTFLOW_CACHEDIR={shq(ex(nf['cachedir']))}")
        if cred_mode == "apikey":
            lines += [f"export ANTHROPIC_API_KEY={shq(cred_val)}", "export ABA_LLM_CREDENTIAL=apikey"]
        elif cred_mode == "oauth":          # explicit oauth token from a cred file
            lines += [f"export CLAUDE_CODE_OAUTH_TOKEN={shq(cred_val)}", "export ABA_LLM_CREDENTIAL=oauth"]
        elif cred_mode == "oauth_env":      # user_oauth — ABA finds the bearer (~/.claude, ~/.aba)
            lines.append("export ABA_LLM_CREDENTIAL=oauth")
        if group_root:
            genv = group_root / ".env"
            lines.append(f'[ -f {shq(genv)} ] && set -a && . {shq(genv)} && set +a')
        (staged / "aba-env.sh").write_text("\n".join(lines) + "\n")

    # ---- status.yaml ----
    status = {
        "version": 1, "ready": not blocked, "user": user, "group": group or None,
        "mode": "direct", "blocked_on": blocked_reason,
        "scopes": {
            "institution": {"state": inst_state, "detail": ex(inst_path) if inst_path else "not configured"},
            "group": {"state": group_state, "detail": group_detail, "bundle_present": bundle_present},
            "user": {"state": ("blocked" if blocked else "ok"), "detail": str(state_dir)},
            "refs": {"state": refs_state, "detail": refs_detail},
        },
        "credentials": {"resolved": bool(cred_mode), "source": cred_source,
                        "mode": ("oauth" if cred_mode in ("oauth", "oauth_env") else cred_mode)},
        "warnings": warnings,
    }
    (staged / "status.yaml").write_text(yaml.safe_dump(status, sort_keys=False))
    print(f"aba-preflight: site={site.get('site',{}).get('name')} group={group} "
          f"{'BLOCKED: '+blocked_reason if blocked else 'runtime='+str(state_dir)} "
          f"cred={cred_source or 'none'}({status['credentials']['mode']})")
    if blocked:
        sys.exit(BLOCKED_EXIT)


if __name__ == "__main__":
    main()
