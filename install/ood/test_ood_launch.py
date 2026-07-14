"""OOD launch-card preflight bake (cluster_open_ondemand.md §4).

Verifies the deployment-generic preflight path without a real apptainer build:
  - build.sh stages aba_preflight.py into the image (the %files bake);
  - template/preflight.sh resolves the SIF from site.yaml and invokes
    `apptainer exec <binds/env> $SIF python /opt/aba/ood/aba_preflight.py`,
    passes through preflight's exit code (10 = blocked), and aborts cleanly when
    the image can't be resolved.

Run:  .venv/bin/python -m pytest install/ood/test_ood_launch.py -q
"""
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BUILD_SH = REPO / "install/sif/build.sh"
PREFLIGHT_SH = REPO / "install/ood/aba/template/preflight.sh"
SCRIPT_ERB = REPO / "install/ood/aba/template/script.sh.erb"


def test_script_forwards_slurm_selector_into_containall():
    """Regression: the --containall backend only sees env vars this loop forwards.
    ABA_BATCH_SUBMITTER is the local-vs-slurm SELECTOR — if it's not forwarded,
    submitter_name() reads unset → 'local' inside the SIF, so every "background"
    job runs in-process on the session node (never Slurm) and run_nextflow refuses
    nf-core with 'unsupported_environment' (no container engine on PATH inside the
    image). The preflight EMITS it into aba-env.sh (test_preflight_writes_env), but
    that's host-side; this guards the forward step that actually reaches the backend.
    Live incident: nf-core blocked in the fat SIF because this var wasn't in the list."""
    src = SCRIPT_ERB.read_text()
    # isolate the env-forward loop: the `for v in … ; do` block whose body evals --env
    import re
    m = re.search(r"for v in\s+(.+?);\s*do\s*\n\s*eval[^\n]*--env", src, re.DOTALL)
    assert m, "could not find the env-forward `for v in … ; do … --env` loop in script.sh.erb"
    forwarded = set(m.group(1).replace("\\", " ").split())
    for v in ("ABA_BATCH_SUBMITTER", "ABA_HPC_CONFIG", "ABA_SIF", "ABA_JOB_WRAP",
              "ABA_NEXTFLOW_MODULE", "ABA_NEXTFLOW_CONFIG", "ABA_MODULE_INIT"):
        # ABA_MODULE_INIT: an offloaded bare job (nf-core Nextflow head) re-inits the site
        # module system from it; without it `module load` fails and the head exits 127.
        assert v in forwarded, f"script.sh.erb env-forward loop dropped {v} (breaks Slurm offload / nf-core)"


def test_build_bakes_preflight():
    """L1 — build.sh stages the script + emits the /opt/aba/ood %files line.
    (The full `build.sh --stage-only` run is exercised manually; this guards the
    two lines from regressing.)"""
    src = BUILD_SH.read_text()
    assert 'cp "$REPO_ROOT/install/ood/aba_preflight.py" "$STAGE/ood/aba_preflight.py"' in src, \
        "build.sh no longer stages aba_preflight.py"
    assert '$STAGE/ood/aba_preflight.py /opt/aba/ood/aba_preflight.py' in src, \
        "build.sh no longer emits the /opt/aba/ood %files line"


def _run_preflight(tmp_path, *, stub_rc=0, with_sif_key=True, sif_exists=True):
    staged = tmp_path / "staged"; staged.mkdir()
    sif = tmp_path / "aba.sif"
    if sif_exists:
        sif.write_text("fake-sif")
    site = tmp_path / "site.yaml"
    site.write_text(f"image:\n  sif: {sif}\n" if with_sif_key else "scopes: {}\n")
    argv_file = tmp_path / "apptainer.argv"
    bindir = tmp_path / "bin"; bindir.mkdir()
    stub = bindir / "apptainer"
    stub.write_text("#!/usr/bin/env bash\n"
                    f"printf '%s\\n' \"$@\" > {shlex.quote(str(argv_file))}\n"
                    f"exit {stub_rc}\n")
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
           "ABA_SITE_CONFIG": str(site), "ABA_PF_STAGED": str(staged),
           "ABA_PF_GROUP": "lab1", "ABA_PF_USER": "alice", "ABA_PF_HOME": str(tmp_path)}
    r = subprocess.run(["bash", str(PREFLIGHT_SH)], env=env, capture_output=True, text=True)
    argv = argv_file.read_text() if argv_file.exists() else ""
    return r.returncode, argv, staged, sif


def test_preflight_sh_invokes_apptainer_from_the_image(tmp_path):
    rc, argv, staged, sif = _run_preflight(tmp_path)
    assert rc == 0, argv
    # runs preflight FROM the image, with the image's python
    assert "exec" in argv and "/opt/aba/ood/aba_preflight.py" in argv
    assert "/opt/aba-venv/bin/python" in argv
    assert str(sif) in argv                                  # the SIF resolved from site.yaml
    # binds: the staged dir + the site-config root
    assert "--bind" in argv and f"{staged}:{staged}" in argv and "/cluster/aba:/cluster/aba" in argv
    # the preflight inputs are forwarded explicitly (apptainer scrubs host env)
    assert "ABA_SITE_CONFIG=" in argv and "ABA_PF_GROUP=lab1" in argv and "ABA_PF_USER=alice" in argv


def test_preflight_sh_passes_through_block_rc(tmp_path):
    # preflight exit 10 (foreign group folder) must propagate so before.sh aborts
    rc, _argv, _s, _sif = _run_preflight(tmp_path, stub_rc=10)
    assert rc == 10


def test_preflight_sh_aborts_when_sif_unresolvable(tmp_path):
    rc, argv, _s, _sif = _run_preflight(tmp_path, with_sif_key=False)
    assert rc == 1 and argv == ""        # never calls apptainer


def _apptainer():
    for c in (os.environ.get("APPTAINER"), "apptainer", "singularity",
              "/home/pkharchenko/aba/tools/apptainer-env/bin/apptainer"):
        if c and (shutil.which(c) or (os.sep in c and os.path.exists(c))):
            return c
    return None


@pytest.mark.skipif(not os.environ.get("ABA_OOD_INTEGRATION"),
                    reason="opt-in (ABA_OOD_INTEGRATION=1) — builds a real SIF, needs apptainer + network")
def test_baked_preflight_runs_in_a_real_sif(tmp_path):
    """L4 — the actual chain: bake aba_preflight.py into a real apptainer image
    (as build.sh does, at /opt/aba/ood/) and run it FROM the image; it must emit
    aba-env.sh + status.yaml + auto-create the lab workspace from inside."""
    ap = _apptainer()
    if not ap:
        pytest.skip("no apptainer/singularity available")
    pf = REPO / "install/ood/aba_preflight.py"
    (tmp_path / "tmp").mkdir()
    defp = tmp_path / "pf.def"
    defp.write_text(f"Bootstrap: docker\nFrom: python:3.12-slim\n%files\n"
                    f"    {pf} /opt/aba/ood/aba_preflight.py\n%post\n"
                    f"    pip install --no-cache-dir pyyaml >/dev/null\n"
                    f"    mkdir -p /opt/aba-venv/bin && ln -sf \"$(command -v python)\" /opt/aba-venv/bin/python\n")
    sif = tmp_path / "test.sif"
    env = {**os.environ, "APPTAINER_TMPDIR": str(tmp_path / "tmp")}
    b = subprocess.run([ap, "build", str(sif), str(defp)], env=env, capture_output=True, text=True, timeout=600)
    assert sif.exists(), b.stderr[-800:]

    staged = tmp_path / "staged"; staged.mkdir(); (tmp_path / "home").mkdir()
    site = tmp_path / "site.yaml"
    site.write_text(  # block style — flow `{...}` collides with the {group} template
        f"image:\n  sif: {sif}\nscopes:\n  group:\n    enabled: true\n"
        f"    root_path: {tmp_path}/groups/{{group}}/aba\n    bundle_subdir: bundle\n"
        f"    auto_create_skeleton: false\n  user:\n    state_dir: {tmp_path}/state/{{user}}\n"
        f"credentials:\n  order: [user_form_paste]\njobs:\n  submitter: slurm\n")
    r = subprocess.run(
        [ap, "exec", "--bind", f"{tmp_path}:{tmp_path}",
         "--env", f"ABA_SITE_CONFIG={site}", "--env", "ABA_PF_GROUP=lab1", "--env", "ABA_PF_USER=alice",
         "--env", f"ABA_PF_HOME={tmp_path}/home", "--env", f"ABA_PF_STAGED={staged}",
         "--env", "ABA_PF_TOKEN=sk-ant-api-XXXX",
         str(sif), "/opt/aba-venv/bin/python", "/opt/aba/ood/aba_preflight.py"],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    env_sh = (staged / "aba-env.sh").read_text()
    for v in ("ABA_SITE_CONFIG", "ABA_SIF", "ABA_RUNTIME_DIR", "ABA_BATCH_SUBMITTER", "ANTHROPIC_API_KEY"):
        assert f"export {v}=" in env_sh, f"aba-env.sh missing {v}"
    assert "ready: true" in (staged / "status.yaml").read_text()
    assert (tmp_path / "groups/lab1/aba/.aba-workspace").exists()
