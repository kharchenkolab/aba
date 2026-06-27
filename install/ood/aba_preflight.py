#!/usr/bin/env python3
"""aba-preflight — read site.yaml, discover/auto-create scope dirs (with a
safety check), resolve credentials (api-key OR oauth, incl. a group-shared
key), and emit aba-env.sh (env block) + status.yaml (session card).

Bridges the site's conventions to the env vars ABA expects, and sets
ABA_SITE_CONFIG so ABA's own scope_resolver reads the same site.yaml for the
bundle scopes.

Inputs (env): ABA_SITE_CONFIG (default /cluster/aba/site.yaml), ABA_PF_GROUP,
ABA_PF_USER, ABA_PF_HOME, ABA_PF_TOKEN (pasted key), ABA_PF_STAGED.
Exit 10 = blocked (foreign group folder) — before.sh must NOT launch.
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

    def ex(s):
        return expand(s, user=user, group=group, home=home)

    # ---- group scope (with safety check) ----
    group_state, group_detail, bundle_present, group_root = "disabled", "group scope disabled", False, None
    if gcfg.get("enabled") and group:
        group_root = Path(ex(gcfg.get("root_path") or "/groups/{group}/aba"))
        bundle_dir = group_root / gcfg.get("bundle_subdir", "bundle")
        tmpl = gcfg.get("skeleton_template")
        if group_root.exists():
            looks_ours = any((group_root / m).exists() for m in OURS_MARKERS)
            try:
                is_empty = not any(group_root.iterdir())
            except Exception:  # noqa: BLE001
                is_empty = False
            if looks_ours:
                group_state, group_detail = "ok", str(group_root)
            elif is_empty and gcfg.get("auto_create_skeleton") and tmpl and Path(ex(tmpl)).is_dir():
                shutil.copytree(ex(tmpl), group_root, dirs_exist_ok=True)
                group_state, group_detail = "skeleton_just_created", f"new ABA workspace created at {group_root}"
            else:
                # SAFETY: a same-named folder that isn't an ABA workspace.
                blocked = True
                blocked_reason = (f"{group_root} exists but is not an ABA workspace "
                                  f"(no {'/'.join(OURS_MARKERS)} marker) — refusing to launch")
                group_state, group_detail = "foreign", blocked_reason
        else:
            if gcfg.get("auto_create_skeleton") and tmpl and Path(ex(tmpl)).is_dir():
                shutil.copytree(ex(tmpl), group_root)
                group_state, group_detail = "skeleton_just_created", f"new ABA workspace created at {group_root}"
            else:
                group_root.mkdir(parents=True, exist_ok=True)
                (group_root / ".aba-workspace").touch()
                group_state, group_detail = "skeleton_just_created", f"new ABA workspace created at {group_root}"
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
        # image: if site.yaml configures a SIF, the node launches FROM it; for a
        # slim image, base_dir/tools_dir are the shared base mounts it expects.
        img = site.get("image") or {}
        if img.get("sif"):
            lines.append(f"export ABA_SIF={shq(ex(img['sif']))}")
        if img.get("base_dir"):
            lines.append(f"export ABA_BASE_DIR={shq(ex(img['base_dir']))}")
        if img.get("tools_dir"):
            lines.append(f"export ABA_TOOLS_DIR={shq(ex(img['tools_dir']))}")
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
