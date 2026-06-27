"""aba_preflight scope rooting — envs are PER-USER (not a lab-shared group/.envs)."""
import importlib
from pathlib import Path


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
