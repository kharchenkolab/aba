"""aba_preflight scope rooting — envs are PER-USER (not a lab-shared group/.envs)."""
import importlib
import json as _json
from pathlib import Path

import pytest


def _parse_env(text):
    out = {}
    for line in text.splitlines():
        if line.startswith("export ") and "=" in line:
            k, v = line[len("export "):].split("=", 1)
            out[k] = v.strip().strip("'\"")
    return out


def _run(tmp_path, monkeypatch, site_yaml):
    (tmp_path / "site.yaml").write_text(site_yaml)
    staged = tmp_path / "staged"; staged.mkdir()
    monkeypatch.setenv("ABA_SITE_CONFIG", str(tmp_path / "site.yaml"))
    monkeypatch.setenv("ABA_PF_GROUP", "lab1")
    monkeypatch.setenv("ABA_PF_USER", "alice")
    monkeypatch.setenv("ABA_PF_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ABA_PF_STAGED", str(staged))
    import aba_preflight; importlib.reload(aba_preflight)
    aba_preflight.main()
    return _parse_env((staged / "aba-env.sh").read_text())


def test_envs_dir_is_per_user(tmp_path, monkeypatch):
    g = tmp_path / "groups"
    site = f"""
site: {{name: test}}
scopes:
  group: {{enabled: true, root_path: "{g}/{{group}}/aba", auto_create_skeleton: true}}
  user:  {{state_dir: "{g}/{{group}}/aba/users/{{user}}"}}
credentials: {{order: [], on_missing: demo_mode}}
"""
    env = _run(tmp_path, monkeypatch, site)
    assert env["ABA_RUNTIME_DIR"].endswith("/aba/users/alice"), env["ABA_RUNTIME_DIR"]
    # per-user envs under the runtime — NOT a lab-shared group/.envs
    assert env["ABA_ENVS_DIR"].endswith("/aba/users/alice/envs"), env["ABA_ENVS_DIR"]
    assert "/.envs" not in env["ABA_ENVS_DIR"]
    assert (tmp_path / "groups/lab1/aba/users/alice/envs").is_dir()


def test_image_and_jobs_emitted(tmp_path, monkeypatch):
    g = tmp_path / "groups"
    site = f"""
site: {{name: test}}
image: {{sif: /cluster/aba/aba.sif}}
jobs:  {{submitter: slurm, hpc_config: /cluster/aba/hpc.yaml}}
scopes:
  group: {{enabled: true, root_path: "{g}/{{group}}/aba", auto_create_skeleton: true}}
  user:  {{state_dir: "{g}/{{group}}/aba/users/{{user}}"}}
credentials: {{order: [], on_missing: demo_mode}}
"""
    env = _run(tmp_path, monkeypatch, site)
    assert env["ABA_SIF"] == "/cluster/aba/aba.sif"
    assert env["ABA_BATCH_SUBMITTER"] == "slurm"
    assert env["ABA_HPC_CONFIG"] == "/cluster/aba/hpc.yaml"


# ── subscription sign-in level (Settings → Agent → Subscription) ────────────
# The emission itself is the guard: the container passthrough is forward-if-set, so
# the preflight MUST produce ABA_SUBSCRIPTION_OAUTH or the tab silently never appears
# (the original OOD bug). Also pins the YAML-bool coercion + the OOD paste-cap.
def _sub_site(g, cred_extra=""):
    return f"""
site: {{name: test}}
scopes:
  group: {{enabled: true, root_path: "{g}/{{group}}/aba", auto_create_skeleton: true}}
  user:  {{state_dir: "{g}/{{group}}/aba/users/{{user}}"}}
credentials: {{order: [], on_missing: demo_mode{cred_extra}}}
"""


def test_subscription_signin_always_emitted_default_paste(tmp_path, monkeypatch):
    # no site override → the producer must still emit a value (default paste), else the
    # forward-if-set passthrough has nothing to forward and the Subscription tab vanishes.
    env = _run(tmp_path, monkeypatch, _sub_site(tmp_path / "groups"))
    assert env["ABA_SUBSCRIPTION_OAUTH"] == "paste"


def test_subscription_signin_off_from_yaml_bool(tmp_path, monkeypatch):
    # YAML 1.1 parses bare `off` as False → must map back to "off", NOT fall through to paste.
    env = _run(tmp_path, monkeypatch, _sub_site(tmp_path / "groups", ", subscription_signin: off"))
    assert env["ABA_SUBSCRIPTION_OAUTH"] == "off"


@pytest.mark.parametrize("level", ["all", "on", "1"])
def test_subscription_signin_full_level_capped_to_paste_on_ood(tmp_path, monkeypatch, level):
    # aba-preflight runs ONLY under the proxied OOD launch, where OpenAI's localhost:1455
    # callback can't be reached — so a full/callback level is capped to paste here.
    env = _run(tmp_path, monkeypatch, _sub_site(tmp_path / "groups", f", subscription_signin: {level}"))
    assert env["ABA_SUBSCRIPTION_OAUTH"] == "paste"


# ── enrollment gate ────────────────────────────────────────────────────────
import yaml as _yaml  # noqa: E402


def _run_pf(tmp_path, monkeypatch, site_yaml):
    """Run preflight; return (exit_code, status_dict, staged_dir). Catches the
    SystemExit(10) the blocked path raises."""
    (tmp_path / "site.yaml").write_text(site_yaml)
    staged = tmp_path / "staged"; staged.mkdir(exist_ok=True)
    monkeypatch.setenv("ABA_SITE_CONFIG", str(tmp_path / "site.yaml"))
    monkeypatch.setenv("ABA_PF_GROUP", "lab1")
    monkeypatch.setenv("ABA_PF_USER", "alice")
    monkeypatch.setenv("ABA_PF_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ABA_PF_STAGED", str(staged))
    import aba_preflight; importlib.reload(aba_preflight)
    code = 0
    try:
        aba_preflight.main()
    except SystemExit as e:
        code = e.code or 0
    status = _yaml.safe_load((staged / "status.yaml").read_text())
    return code, status, staged


def _gate_site(g, *, auto):
    return f"""
site: {{name: VBC}}
scopes:
  group: {{enabled: true, root_path: "{g}/{{group}}/aba", auto_create_skeleton: {str(auto).lower()}}}
  user:  {{state_dir: "{g}/{{group}}/aba/users/{{user}}"}}
credentials: {{order: [], on_missing: demo_mode}}
"""


def test_absent_and_not_auto_blocks_not_enrolled(tmp_path, monkeypatch):
    g = tmp_path / "groups"
    code, status, staged = _run_pf(tmp_path, monkeypatch, _gate_site(g, auto=False))
    assert code == 10 and status["ready"] is False
    assert status["scopes"]["group"]["state"] == "not_enrolled"
    assert "not enrolled" in (status["blocked_on"] or "").lower()
    assert not (staged / "aba-env.sh").exists()        # no env block when blocked
    assert not (g / "lab1" / "aba").exists()             # nothing created (no auto-provision)


def test_empty_folder_not_auto_blocks_not_enrolled(tmp_path, monkeypatch):
    g = tmp_path / "groups"; (g / "lab1" / "aba").mkdir(parents=True)   # exists but empty
    code, status, _ = _run_pf(tmp_path, monkeypatch, _gate_site(g, auto=False))
    assert code == 10 and status["scopes"]["group"]["state"] == "not_enrolled"


def test_enrolled_marker_launches(tmp_path, monkeypatch):
    g = tmp_path / "groups"; aba = g / "lab1" / "aba"; aba.mkdir(parents=True)
    (aba / ".aba-workspace").touch()                    # the enrollment stamp
    code, status, staged = _run_pf(tmp_path, monkeypatch, _gate_site(g, auto=False))
    assert code == 0 and status["ready"] is True
    assert status["scopes"]["group"]["state"] == "ok"
    assert (staged / "aba-env.sh").exists()


def test_foreign_folder_blocks(tmp_path, monkeypatch):
    g = tmp_path / "groups"; aba = g / "lab1" / "aba"; aba.mkdir(parents=True)
    (aba / "somefile.txt").write_text("not ours")       # non-empty, no marker
    code, status, _ = _run_pf(tmp_path, monkeypatch, _gate_site(g, auto=False))
    assert code == 10 and status["scopes"]["group"]["state"] == "foreign"


def test_auto_create_still_provisions(tmp_path, monkeypatch):
    """Back-compat: auto_create_skeleton:true mints a workspace (no enrollment gate)."""
    g = tmp_path / "groups"
    code, status, staged = _run_pf(tmp_path, monkeypatch, _gate_site(g, auto=True))
    assert code == 0 and status["scopes"]["group"]["state"] == "skeleton_just_created"
    assert (g / "lab1" / "aba" / ".aba-workspace").exists()
    assert (staged / "aba-env.sh").exists()


# ── credential cleanup for the OOD pilot (group_dir mapping + user-store unification +
#    launch-card reset). The card drops the paste field; the group credential is the default,
#    a personal override (Settings/subscription, persisted to $ABA_HOME) wins, and the reset
#    checkbox clears it. ──────────────────────────────────────────────────────────────────
def _run_creds(tmp_path, monkeypatch, *, group="lab1.grp", reset=False,
               personal=None, group_key="sk-ant-GROUP"):
    """Enrolled `.grp` group at <g>/<group_dir>/aba with a group credentials.json; optional
    personal override in the session $ABA_HOME (<state_dir>/.home/.aba). Returns parsed
    aba-env.sh env dict (or None if the launch was blocked)."""
    g = tmp_path / "groups"
    gdir = group[:-4] if group.endswith(".grp") else group
    groot = g / gdir / "aba"
    groot.mkdir(parents=True)
    (groot / ".aba-workspace").write_text("enrolled\n")            # enrollment marker
    if group_key:
        (groot / "credentials.json").write_text(_json.dumps({"anthropic_api_key": group_key}))
    aba_home = groot / "users" / "alice" / ".home" / ".aba"         # session $ABA_HOME
    if personal:
        aba_home.mkdir(parents=True)
        kind, val = personal
        if kind == "config_apikey":
            (aba_home / "config.env").write_text(
                "export ABA_MODEL='claude-x'\nexport ANTHROPIC_API_KEY='%s'\n" % val)
        elif kind == "config_oauth":
            (aba_home / "config.env").write_text("export CLAUDE_CODE_OAUTH_TOKEN='%s'\n" % val)
        elif kind == "oauth_json":
            (aba_home / "oauth.json").write_text(_json.dumps({"access_token": val}))
    site = f"""
site: {{name: VBC}}
scopes:
  group: {{enabled: true, root_path: "{g}/{{group_dir}}/aba", strip_suffix: ".grp"}}
  user:  {{state_dir: "{g}/{{group_dir}}/aba/users/{{user}}"}}
credentials:
  order: [user_saved, group_shared]
  group_key_path: "{g}/{{group_dir}}/aba/credentials.json"
  on_missing: demo_mode
"""
    (tmp_path / "site.yaml").write_text(site)
    staged = tmp_path / "staged"; staged.mkdir(exist_ok=True)
    monkeypatch.setenv("ABA_SITE_CONFIG", str(tmp_path / "site.yaml"))
    monkeypatch.setenv("ABA_PF_GROUP", group)
    monkeypatch.setenv("ABA_PF_USER", "alice")
    monkeypatch.setenv("ABA_PF_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ABA_PF_STAGED", str(staged))
    if reset:
        monkeypatch.setenv("ABA_PF_RESET_CREDENTIAL", "1")
    else:
        monkeypatch.delenv("ABA_PF_RESET_CREDENTIAL", raising=False)
    monkeypatch.delenv("ABA_PF_TOKEN", raising=False)
    import aba_preflight; importlib.reload(aba_preflight)
    try:
        aba_preflight.main()
    except SystemExit as e:
        if (e.code or 0) != 0:
            return None
    return _parse_env((staged / "aba-env.sh").read_text()), aba_home


def test_group_dir_strips_suffix_and_group_cred_default(tmp_path, monkeypatch):
    """`.grp` unix group → on-disk folder strips it (tanaka.grp → /groups/tanaka). With no
    personal override, the GROUP credential resolves."""
    env, _ = _run_creds(tmp_path, monkeypatch)
    assert env["ABA_RUNTIME_DIR"].endswith("/groups/lab1/aba/users/alice"), env["ABA_RUNTIME_DIR"]
    assert "lab1.grp" not in env["ABA_RUNTIME_DIR"]              # suffix stripped for the folder
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-GROUP"
    assert env["ABA_LLM_CREDENTIAL"] == "apikey"


def test_personal_config_env_override_wins_over_group(tmp_path, monkeypatch):
    """A personal key saved by Settings → Agent (config.env) beats the group credential."""
    env, _ = _run_creds(tmp_path, monkeypatch, personal=("config_apikey", "sk-ant-PERSONAL"))
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-PERSONAL"


def test_personal_oauth_json_resolves_as_oauth_env(tmp_path, monkeypatch):
    """A subscription store (oauth.json) → oauth_cc: no static key emitted; the backend
    finds + refreshes it via $ABA_HOME. Wins over the group key. Mode is oauth_cc (NOT plain
    oauth): these are Claude Code subscription bearers, and oauth_cc prepends the CC system
    marker so non-Haiku models (the Opus default) work — plain oauth is Haiku-only and 429s."""
    env, _ = _run_creds(tmp_path, monkeypatch, personal=("oauth_json", "acc-tok"))
    assert env.get("ABA_LLM_CREDENTIAL") == "oauth_cc"
    assert "ANTHROPIC_API_KEY" not in env                       # no static key for a subscription
    assert "sk-ant-GROUP" not in "".join(env.values())


def test_reset_clears_personal_and_falls_back_to_group(tmp_path, monkeypatch):
    """The launch-card reset (ABA_PF_RESET_CREDENTIAL) wipes the personal override so the
    session uses the GROUP credential — and the personal config.env cred key is gone."""
    env, aba_home = _run_creds(tmp_path, monkeypatch,
                               personal=("config_apikey", "sk-ant-PERSONAL"), reset=True)
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-GROUP"           # reverted to group
    ce = (aba_home / "config.env").read_text()
    assert "ANTHROPIC_API_KEY" not in ce                        # personal key stripped
    assert "ABA_MODEL" in ce                                    # non-credential settings kept


def test_reset_removes_oauth_json(tmp_path, monkeypatch):
    """Reset also removes a subscription store, so a subscription user reverts to the group."""
    env, aba_home = _run_creds(tmp_path, monkeypatch, personal=("oauth_json", "acc"), reset=True)
    assert not (aba_home / "oauth.json").exists()
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-GROUP"
