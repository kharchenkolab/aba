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
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUILD_SH = REPO / "install/sif/build.sh"
PREFLIGHT_SH = REPO / "install/ood/aba/template/preflight.sh"


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
