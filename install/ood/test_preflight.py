"""aba_preflight scope rooting — envs are PER-USER (not a lab-shared group/.envs)."""
import importlib
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
