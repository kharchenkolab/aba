"""Guard the SIF base-image glibc-floor rule.

Two behavioral guards for the base-OS-mismatch fix:
  1. install/sif/glibc-floor.sh — the single-source comparison (build.sh + the OOD
     preflight both defer to this rule) flags a base whose glibc is NEWER than the
     target's (the debian:12-on-EL7 bug) and stays quiet otherwise.
  2. aba_preflight.py surfaces ABA_PF_GLIBC_WARN (set by preflight.sh on an overshoot)
     into status.yaml warnings, so a mis-based image is visible on the OOD session card.

Standalone-runnable (no bio content / conftest needed):  python tests/test_sif_glibc_floor.py
"""
from __future__ import annotations
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import pytest
    pytestmark = pytest.mark.platform
except ImportError:                     # standalone run (base env has no pytest)
    pytest = None

ROOT = Path(__file__).resolve().parents[1]
FLOOR = ROOT / "install" / "sif" / "glibc-floor.sh"
PREFLIGHT = ROOT / "install" / "ood" / "aba_preflight.py"


def _overshoot(base: str, target: str) -> bool:
    """glibc-floor.sh exits 0 iff base > target (INCOMPATIBLE → caller warns)."""
    return subprocess.run(["bash", str(FLOOR), base, target]).returncode == 0


def test_glibc_floor_truth_table():
    # base NEWER than target → overshoot (this is exactly the debian:12 / EL7 bug)
    assert _overshoot("2.36", "2.17")
    assert _overshoot("glibc 2.36", "glibc 2.17")   # tolerates the raw getconf format
    assert _overshoot("2.28", "2.17")               # EL8 base on EL7 nodes
    # base <= target → OK (older-built runs on newer glibc)
    assert not _overshoot("2.17", "2.36")
    assert not _overshoot("2.17", "2.17")
    assert not _overshoot("2.17", "2.34")           # EL7 base on EL9 nodes
    # unknown on either side → never cry wolf
    assert not _overshoot("", "2.17")
    assert not _overshoot("2.17", "")


def test_preflight_surfaces_glibc_warn():
    import yaml
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        site = tdp / "site.yaml"
        site.write_text(
            "site: {name: t}\n"
            f"scopes:\n  user:\n    state_dir: {tdp}/state\n"
            "credentials: {order: [], on_missing: demo_mode}\n")
        warn = "GLIBC_TEST_WARN base 2.36 exceeds node 2.17"
        env = {**os.environ,
               "ABA_SITE_CONFIG": str(site), "ABA_PF_STAGED": str(tdp),
               "ABA_PF_USER": "u", "ABA_PF_HOME": str(tdp), "ABA_PF_GROUP": "",
               "ABA_PF_GLIBC_WARN": warn}
        r = subprocess.run([sys.executable, str(PREFLIGHT)], env=env,
                           capture_output=True, text=True)
        status = yaml.safe_load((tdp / "status.yaml").read_text())
        assert any(warn in w for w in (status.get("warnings") or [])), (r.stdout, r.stderr, status)


def test_preflight_emits_module_config():
    """A site.yaml `modules:` block → aba-env.sh exports ABA_MODULE_INIT/BINDS/LIBS
    (space-joined), which script.sh.erb consumes on the node to bind the host Lmod +
    modulefiles so in-session `module load` works inside the SIF."""
    import yaml
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "site.yaml").write_text(
            "site: {name: t}\n"
            f"scopes:\n  user:\n    state_dir: {tdp}/state\n"
            "credentials: {order: [], on_missing: demo_mode}\n"
            "modules:\n  enabled: true\n  init: /etc/profile.d/lmod.sh\n"
            "  binds: [/opt/ohpc, /software]\n  libs: [libtcl8.5.so]\n"
            "nextflow:\n  module: nextflow/24.04.4\n  profiles: [cbe]\n"
            "  config: /cluster/aba/nextflow/cbe.config\n")
        env = {**os.environ, "ABA_SITE_CONFIG": str(tdp / "site.yaml"),
               "ABA_PF_STAGED": str(tdp), "ABA_PF_USER": "u", "ABA_PF_HOME": str(tdp), "ABA_PF_GROUP": ""}
        subprocess.run([sys.executable, str(PREFLIGHT)], env=env, capture_output=True, text=True)
        envsh = (tdp / "aba-env.sh").read_text()
        assert "export ABA_MODULE_INIT='/etc/profile.d/lmod.sh'" in envsh, envsh
        assert "export ABA_MODULE_BINDS='/opt/ohpc /software'" in envsh, envsh
        assert "export ABA_MODULE_LIBS='libtcl8.5.so'" in envsh, envsh
        # nf-core: the nextflow block flips run_nextflow on + steers the offloaded head
        assert "export ABA_NEXTFLOW_MODULE='nextflow/24.04.4'" in envsh, envsh
        assert "export ABA_NEXTFLOW_PROFILES='cbe'" in envsh, envsh
        assert "export ABA_NEXTFLOW_CONFIG='/cluster/aba/nextflow/cbe.config'" in envsh, envsh
        # disabled / absent → no emission
        (tdp / "site2.yaml").write_text(
            "site: {name: t}\n"
            f"scopes:\n  user:\n    state_dir: {tdp}/state2\n"
            "credentials: {order: [], on_missing: demo_mode}\n"
            "modules: {enabled: false, init: /x, binds: [/y]}\n")
        env["ABA_SITE_CONFIG"] = str(tdp / "site2.yaml")
        subprocess.run([sys.executable, str(PREFLIGHT)], env=env, capture_output=True, text=True)
        assert "ABA_MODULE_" not in (tdp / "aba-env.sh").read_text()


def test_launch_forwards_nextflow_env():
    """script.sh.erb must FORWARD ABA_NEXTFLOW_* into the containall run. aba_preflight
    only EMITS them to aba-env.sh; without the forward the backend never sees
    ABA_NEXTFLOW_MODULE, so run_nextflow silently stays False (the live regression this
    guards — nf-core showed ✗ despite the config being present)."""
    erb = (ROOT / "install" / "ood" / "aba" / "template" / "script.sh.erb").read_text()
    assert "ABA_NEXTFLOW_MODULE" in erb, "script.sh.erb must forward ABA_NEXTFLOW_MODULE into apptainer run"


def _run_preflight_envsh(subscription_signin: str | None = None) -> str:
    """Run the REAL aba_preflight against a minimal site.yaml (optionally with a
    credentials.subscription_signin override); return the emitted aba-env.sh text."""
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        extra = f", subscription_signin: {subscription_signin}" if subscription_signin else ""
        (tdp / "site.yaml").write_text(
            "site: {name: t}\n"
            f"scopes:\n  user:\n    state_dir: {tdp}/state\n"
            f"credentials: {{order: [], on_missing: demo_mode{extra}}}\n")
        env = {**os.environ, "ABA_SITE_CONFIG": str(tdp / "site.yaml"),
               "ABA_PF_STAGED": str(tdp), "ABA_PF_USER": "u", "ABA_PF_HOME": str(tdp), "ABA_PF_GROUP": ""}
        subprocess.run([sys.executable, str(PREFLIGHT)], env=env, capture_output=True, text=True)
        return (tdp / "aba-env.sh").read_text()


def test_preflight_always_produces_subscription_oauth_value():
    """The OOD template MUST emit a NON-EMPTY ABA_SUBSCRIPTION_OAUTH — the container
    passthrough in script.sh.erb is forward-if-set, so with no producer the Subscription
    tab silently never appears. Default is `paste` (aba_preflight only runs under the
    proxied OOD launch → Anthropic paste flow is the safe max). This guards the exact
    'gated on an unset var' gap from silently recurring when a deploy path is added."""
    envsh = _run_preflight_envsh()
    m = re.search(r"export ABA_SUBSCRIPTION_OAUTH='([^']*)'", envsh)
    assert m, f"aba-env.sh must set ABA_SUBSCRIPTION_OAUTH — got:\n{envsh}"
    assert m.group(1).strip(), "ABA_SUBSCRIPTION_OAUTH must be NON-EMPTY (an empty value = hidden tab)"
    assert m.group(1) == "paste", m.group(1)


def test_preflight_subscription_signin_override():
    """site.yaml credentials.subscription_signin overrides the default. `off` forces
    API-key-only; a full/callback level (`all`) is CAPPED to `paste` here because this
    producer runs only under the proxied OOD launch, where OpenAI's localhost callback
    can't be reached (main 7badb538). Canonical coverage: install/ood/test_preflight.py."""
    assert "export ABA_SUBSCRIPTION_OAUTH='off'" in _run_preflight_envsh("off")
    assert "export ABA_SUBSCRIPTION_OAUTH='paste'" in _run_preflight_envsh("all")


def test_launch_forwards_subscription_oauth():
    """script.sh.erb must FORWARD ABA_SUBSCRIPTION_OAUTH into the containall run — aba_preflight
    only EMITS it to aba-env.sh. Without the forward the backend never sees it → oauth.enabled()
    is False → the Subscription tab stays hidden despite the deployment enabling it."""
    erb = (ROOT / "install" / "ood" / "aba" / "template" / "script.sh.erb").read_text()
    assert "ABA_SUBSCRIPTION_OAUTH" in erb, "script.sh.erb must forward ABA_SUBSCRIPTION_OAUTH into apptainer run"


def test_preflight_default_yields_anthropic_subscription_gating():
    """End-to-end: the value aba_preflight emits by default must make oauth.enabled() open
    Anthropic (paste, proxy-safe) and keep OpenAI (localhost callback) closed on OOD."""
    envsh = _run_preflight_envsh()
    val = re.search(r"export ABA_SUBSCRIPTION_OAUTH='([^']*)'", envsh).group(1)
    import os as _os
    from core import oauth
    prev = _os.environ.get("ABA_SUBSCRIPTION_OAUTH")
    try:
        _os.environ["ABA_SUBSCRIPTION_OAUTH"] = val
        assert oauth.enabled("anthropic") is True
        assert oauth.enabled("openai") is False
    finally:
        if prev is None:
            _os.environ.pop("ABA_SUBSCRIPTION_OAUTH", None)
        else:
            _os.environ["ABA_SUBSCRIPTION_OAUTH"] = prev


def test_launch_binds_sacctmgr():
    """script.sh.erb must bind `sacctmgr` into the containall SIF, not just the job
    clients. hpc_config.qos_account_live() shells out to sacctmgr to discover the
    user's QOS + account + each QOS's MaxWall; unbound, discovery returns empty in the
    SIF and jobs fall back to the cluster DEFAULT QOS + uncapped walltime — a 24h
    request (the nf-core head) is then rejected QOSMaxWallDurationPerJobLimit (the live
    regression this guards). It belongs in the same bind loop as sbatch/squeue/sacct."""
    erb = (ROOT / "install" / "ood" / "aba" / "template" / "script.sh.erb").read_text()
    import re
    m = re.search(r"for b in ((?:sbatch|squeue|sacct|sacctmgr|scancel|sinfo|scontrol|salloc|srun|\s)+);", erb)
    assert m and "sacctmgr" in m.group(1).split(), (
        "script.sh.erb Slurm-client bind loop must include sacctmgr (QOS/account discovery)")


def test_launch_forwards_job_wrap_env():
    """script.sh.erb must FORWARD ABA_SIF + ABA_JOB_WRAP (+ ABA_MODULE_BINDS) into the
    containall run. Under a fat deployment (ABA_JOB_WRAP=sif) the containerized backend's
    slurm_submitter re-enters THIS image to wrap offloaded env-jobs — it can't do that if
    ABA_SIF isn't visible inside. Guards the fat-wrap plumbing (misc/fatagain.md)."""
    erb = (ROOT / "install" / "ood" / "aba" / "template" / "script.sh.erb").read_text()
    for v in ("ABA_SIF", "ABA_JOB_WRAP", "ABA_MODULE_BINDS"):
        assert v in erb, f"script.sh.erb must forward {v} into the container env"


def test_preflight_emits_job_wrap():
    """aba_preflight derives ABA_JOB_WRAP: a FAT image (image.sif, no base_dir) → 'sif'
    (offloaded env-jobs re-enter the SIF); a SLIM image (sif + base_dir) → unset (bare).
    This is the single-source signal the submitter routes on."""
    import yaml
    base = ("site: {name: t}\nscopes:\n  user: {state_dir: %s/st}\n"
            "credentials: {order: [], on_missing: demo_mode}\n")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        fat = tdp / "fat.yaml"
        fat.write_text(base % td + "image: {sif: /cluster/aba/aba.sif}\n")
        env = {**os.environ, "ABA_SITE_CONFIG": str(fat), "ABA_PF_STAGED": str(tdp),
               "ABA_PF_USER": "u", "ABA_PF_HOME": str(tdp), "ABA_PF_GROUP": ""}
        subprocess.run([sys.executable, str(PREFLIGHT)], env=env, capture_output=True, text=True)
        assert "export ABA_JOB_WRAP='sif'" in (tdp / "aba-env.sh").read_text()
        # slim: sif + base_dir → NO wrap
        slim = tdp / "slim.yaml"
        slim.write_text(base % td + "image: {sif: /cluster/aba/aba.sif, base_dir: /cluster/aba/base}\n")
        env["ABA_SITE_CONFIG"] = str(slim)
        subprocess.run([sys.executable, str(PREFLIGHT)], env=env, capture_output=True, text=True)
        assert "ABA_JOB_WRAP" not in (tdp / "aba-env.sh").read_text()


if __name__ == "__main__":
    test_glibc_floor_truth_table(); print("glibc_floor truth table: PASS")
    test_preflight_surfaces_glibc_warn(); print("preflight surfaces warn: PASS")
    test_preflight_emits_module_config(); print("preflight emits module config: PASS")
    test_launch_forwards_nextflow_env(); print("launch forwards nextflow env: PASS")
    test_launch_binds_sacctmgr(); print("launch binds sacctmgr: PASS")
    test_launch_forwards_job_wrap_env(); print("launch forwards job-wrap env: PASS")
    test_preflight_emits_job_wrap(); print("preflight emits job-wrap: PASS")
