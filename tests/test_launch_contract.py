"""The launch contract: ONE implementation of `apptainer run`, two consumers.

ABA was launched two ways that were meant to be the same launch — the OOD card
(install/ood/aba/template/script.sh.erb, what users get) and the deployment gate
(aba-vbc/verify.sh, what decides a release may be promoted). Written
independently, they drifted, and the gate ended up certifying a configuration
nobody runs: no ABA_BATCH_SUBMITTER (so `submitter_name()` read an unset var
inside --containall, returned "local", and every "background job" ran in-process
— the Slurm lane passed having never submitted to Slurm), no
ABA_JOBS_GPU_ENV_PACK, no module plumbing, and none of the scheduler binds.

Two properties, and the FIRST is the load-bearing one. A test that only checked
the launcher's OUTPUT would stay green while a consumer quietly appended its own
`--bind` beside it — which is exactly how the drift happened. So the guard is on
the forbidden ACTION: no consumer assembles launch argv of its own.
"""
from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

_BASH = next(b for b in ("/usr/bin/bash", "/bin/bash") if Path(b).exists())

TPL = Path(__file__).resolve().parent.parent / "install" / "ood" / "aba" / "template"
LAUNCHER = TPL / "aba_launch.sh"
CARD = TPL / "script.sh.erb"


# ---------------------------------------------------------------- property 1
def test_launcher_exists_and_is_sourceable():
    assert LAUNCHER.is_file(), f"missing the launch contract: {LAUNCHER}"
    subprocess.run(["bash", "-n", str(LAUNCHER)], check=True)


def test_card_delegates_and_builds_no_argv_of_its_own():
    """The card may bind its per-session SPA dist and nothing else.

    Every other --bind/--env it grows is one the gate cannot certify. Asserting
    the COUNT (not merely that it sources the launcher) is what makes a new
    hand-rolled bind fail here instead of in production six weeks later."""
    text = CARD.read_text()
    # The LAUNCH lane specifically. An earlier `if [ -n "$ABA_SIF" ]` copies the
    # SPA dist out of the image with `apptainer exec --bind` — a legitimate
    # non-launch use that must not be mistaken for launch argv.
    sif_lane = text.rsplit('if [ -n "${ABA_SIF:-}" ]; then', 1)[1].split("\nelse\n", 1)[0]
    assert "apptainer run --containall" in sif_lane, "found the wrong block"

    assert ". \"${PWD}/aba_launch.sh\"" in sif_lane, "the card must SOURCE the contract"
    assert "aba_launch_args" in sif_lane, "the card must CALL the contract"

    binds = [ln.strip() for ln in sif_lane.splitlines()
             if "--bind" in ln and not ln.strip().startswith("#")]
    assert binds == ['binds=( --bind "${PWD}/dist" )'], (
        "the card assembles binds of its own — put them in aba_launch.sh so the "
        f"deployment gate exercises them too:\n  " + "\n  ".join(binds))

    envs = [ln.strip() for ln in sif_lane.splitlines()
            if "--env" in ln and not ln.strip().startswith("#")]
    assert envs == [], (
        "the card forwards env of its own; add it to aba_launch_forward_env (and "
        f"mark it deploy_injected) instead:\n  " + "\n  ".join(envs))


def test_scheduler_plumbing_lives_only_in_the_contract():
    """The 20-line Slurm block was the single largest divergence. It must exist
    in exactly one file — the one both consumers source."""
    assert "sacctmgr" in LAUNCHER.read_text()
    assert "munge.socket.2" in LAUNCHER.read_text()
    for marker in ("sacctmgr", "munge.socket.2", "getent passwd"):
        assert marker not in CARD.read_text(), (
            f"{marker!r} is back in the card — it belongs to aba_launch.sh alone")


# ---------------------------------------------------------------- property 2
DRIVER = textwrap.dedent("""
    set -u
    cd "$RUNDIR"
    binds=(); envs=()
    . "$LAUNCHER"
    aba_launch_args
    printf 'BIND %s\\n' "${binds[@]}" | grep -v 'BIND --bind' || true
    printf 'ENV %s\\n' "${envs[@]}" | grep -v 'ENV --env' || true
    echo "TMPCLEAN ${ABA_LAUNCH_TMP_CLEANUP:-<none>}"
""")


def _run(tmp_path, *, slurm: bool = True, modules: bool = True, extra: str = "",
         slurm_tmpdir: str = "", publish_tree: str | None = None,
         base_dir: str = "", tools_dir: str = "") -> tuple[set, set, str]:
    """Source the contract in a controlled environment; return (binds, envs, tmpclean)."""
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    # A CLOSED PATH: only the utilities the contract calls, symlinked in. The
    # host's real /usr/bin holds a real sbatch, so a probe that keeps /usr/bin on
    # PATH cannot express "no scheduler here" — and test_contract_is_a_noop_off_slurm
    # would assert against a scheduler it accidentally found.
    bin_ = tmp_path / "bin"; bin_.mkdir(exist_ok=True)
    for util in ("dirname", "mkdir", "ldd", "awk", "head", "ldconfig",
                 "getent", "id", "grep", "cat"):
        for d in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
            src = Path(d) / util
            if src.exists():
                tgt = bin_ / util
                if not tgt.exists():
                    tgt.symlink_to(src)
                break
    share = tmp_path / "deploy" / "app"; share.mkdir(parents=True, exist_ok=True)
    site = tmp_path / "deploy" / "site.yaml"; site.write_text("site: {}\n")
    store = Path(publish_tree) if publish_tree else (tmp_path / "store")
    store.mkdir(parents=True, exist_ok=True)

    if slurm:
        # A fake that RESOLVES (`command -v`) — the block is gated on that alone.
        for b in ("sbatch", "squeue", "scancel", "sacct", "sacctmgr",
                  "sinfo", "scontrol", "salloc", "srun"):
            f = bin_ / b; f.write_text("#!/bin/sh\nexit 0\n"); f.chmod(0o755)
    conf = tmp_path / "slurm" / "slurm.conf"
    conf.parent.mkdir(parents=True, exist_ok=True); conf.write_text("ClusterName=t\n")

    env = {
        "PATH": str(bin_), "HOME": str(tmp_path),
        "RUNDIR": str(run), "LAUNCHER": str(LAUNCHER),
        "ABA_LAUNCH_WORKDIR": str(run),
        "ABA_SITE_CONFIG": str(site), "ABA_SHARE": str(share),
        "ABA_RUNTIME_DIR": str(run / "rt"),
        "ABA_WEFT_PUBLISH_TREE": str(store),
        "ABA_BATCH_SUBMITTER": "slurm", "ABA_JOBS_GPU_ENV_PACK": "sitepack-gpu",
        "ABA_HPC_CONFIG": str(tmp_path / "hpc.yaml"),
        "ABA_NEXTFLOW_MODULE": "Nextflow/1.2.3",
        "ABA_EXTRA_BINDS": extra, "SLURM_CONF": str(conf),
        "ABA_BASE_DIR": base_dir, "ABA_TOOLS_DIR": tools_dir,
    }
    if slurm_tmpdir:
        env["SLURM_TMPDIR"] = slurm_tmpdir
    if modules:
        init = tmp_path / "modinit.sh"
        init.write_text("export MODULEPATH=/mods\nexport LMOD_CMD=/lmod/cmd\n")
        cvmfs = tmp_path / "cvmfs"; cvmfs.mkdir(exist_ok=True)
        env["ABA_MODULE_INIT"] = str(init)
        env["ABA_MODULE_BINDS"] = str(cvmfs)

    # absolute: the closed PATH above deliberately cannot resolve `bash`
    p = subprocess.run([_BASH, "-c", DRIVER], env=env, capture_output=True, text=True)
    assert p.returncode == 0, f"contract failed to source:\n{p.stderr}"
    binds = {ln[5:] for ln in p.stdout.splitlines() if ln.startswith("BIND ")}
    envs = {ln[4:] for ln in p.stdout.splitlines() if ln.startswith("ENV ")}
    clean = next(ln[9:] for ln in p.stdout.splitlines() if ln.startswith("TMPCLEAN "))
    return binds, envs, clean


def test_contract_forwards_the_selectors_the_gate_was_missing(tmp_path):
    """The three that made the gate certify a launch nobody performs."""
    _b, envs, _ = _run(tmp_path)
    assert "ABA_BATCH_SUBMITTER=slurm" in envs, (
        "unset inside --containall, submitter_name() returns 'local' and every "
        "background job runs in-process — the Slurm lane then passes vacuously")
    assert "ABA_JOBS_GPU_ENV_PACK=sitepack-gpu" in envs
    assert any(e.startswith("ABA_HPC_CONFIG=") for e in envs)


def test_contract_binds_the_scheduler(tmp_path):
    """ARMED: if the fake sbatch does not resolve, the block never runs and every
    assertion below is satisfied by an empty subject set."""
    binds, envs, _ = _run(tmp_path, slurm=True)
    sbatch = [b for b in binds if b.endswith("/sbatch")]
    assert sbatch, ("PRECONDITION: no sbatch was bound, so this run measured "
                    "NOTHING about scheduler plumbing")
    for tool in ("squeue", "sacctmgr", "scontrol", "srun"):
        assert any(b.endswith("/" + tool) for b in binds), f"{tool} not bound"
    assert any(e.startswith("SLURM_CONF=") for e in envs)
    assert any(b.endswith("/passwd:/etc/passwd") for b in binds), (
        "no synthesized NSS passwd — LDAP users are absent from the image and "
        "--containall has no SSSD, so sbatch cannot resolve the submitter")
    assert any(b.endswith("/group:/etc/group") for b in binds)


def test_contract_binds_the_module_system(tmp_path):
    binds, envs, _ = _run(tmp_path, modules=True)
    assert "MODULEPATH=/mods" in envs, "the resolved Lmod env is not forwarded"
    assert "LMOD_CMD=/lmod/cmd" in envs
    assert any(b.endswith("/cvmfs") for b in binds), "ABA_MODULE_BINDS not applied"


def test_contract_binds_the_env_store_when_it_sits_outside_the_share(tmp_path):
    """One store serves staging and production, so it is NOT a sibling of
    site.yaml. Unbound, sessions cannot adopt any published pack."""
    binds, _e, _ = _run(tmp_path)
    assert any(b == str(tmp_path / "store") for b in binds)


def test_contract_skips_the_store_bind_when_already_covered(tmp_path):
    """WIDE — the other side. Binding a path already inside a bound tree is a
    duplicate mount apptainer may reject."""
    inside = tmp_path / "deploy" / "envs"
    binds, _e, _ = _run(tmp_path, publish_tree=str(inside))
    assert str(inside) not in binds, "store is under the deployment root; already covered"
    assert str(tmp_path / "deploy") in binds


# ------------------------------------------------------- WIDE: degenerate shapes
def test_contract_is_a_noop_off_slurm(tmp_path):
    """A laptop, a bare node, a container build host: no scheduler, no crash."""
    binds, envs, _ = _run(tmp_path, slurm=False)
    assert not [b for b in binds if b.endswith("/sbatch")]
    assert not [e for e in envs if e.startswith("SLURM_CONF=")]
    assert "ABA_BATCH_SUBMITTER=slurm" in envs   # still forwarded; the site decides


def test_contract_is_a_noop_without_a_module_system(tmp_path):
    binds, envs, _ = _run(tmp_path, modules=False)
    assert not [e for e in envs if e.startswith("MODULEPATH=")]
    assert not [e for e in envs if e.startswith("LD_LIBRARY_PATH=")]


def test_session_tmpdir_defers_to_slurm_and_claims_nothing_to_clean(tmp_path):
    """The extreme of the tunable: when Slurm owns TMPDIR we must NOT hand the
    caller a path to rm -rf. Deleting $SLURM_TMPDIR under a running job is the
    kind of cleanup that eats another job's scratch."""
    node_local = tmp_path / "nodelocal"; node_local.mkdir()
    _b, envs, clean = _run(tmp_path, slurm_tmpdir=str(node_local))
    assert f"TMPDIR={node_local}" in envs
    assert clean == "<none>", "claimed ownership of Slurm's scratch dir"


def test_session_tmpdir_falls_back_and_is_owned(tmp_path):
    """And the other side: when WE create it, the caller must be told to remove
    it, or per-session debris accrues against the user's quota forever."""
    _b, envs, clean = _run(tmp_path)
    tmpdir = next(e[7:] for e in envs if e.startswith("TMPDIR="))
    assert clean == tmpdir and Path(tmpdir).is_dir()


def test_extra_binds_absent_and_present(tmp_path):
    """`absent` is the COMMON shape (site.yaml `binds: []`), and it takes a
    different path through the word-split loop than a populated one."""
    binds_empty, _e, _ = _run(tmp_path, extra="")
    extra = tmp_path / "scratchtree"; extra.mkdir()
    binds_set, _e2, _ = _run(tmp_path, extra=str(extra))
    assert str(extra) not in binds_empty
    assert str(extra) in binds_set


def test_slim_base_remaps_only_when_declared(tmp_path):
    """A FAT image bakes its own venv and declares neither; a slim one declares
    both. Binding an empty path would fail the launch outright."""
    binds_fat, _e, _ = _run(tmp_path)
    assert not [b for b in binds_fat if b.endswith(":/opt/aba-venv")]
    base = tmp_path / "base"; base.mkdir()
    tools = tmp_path / "tools"; tools.mkdir()
    binds_slim, _e2, _ = _run(tmp_path, base_dir=str(base), tools_dir=str(tools))
    assert f"{base}:/opt/aba-venv" in binds_slim
    assert f"{tools}:/opt/aba-envs/tools" in binds_slim


@pytest.mark.parametrize("path", ["/usr/bin:/bin", "/usr/bin:/bin:/usr/sbin:/sbin"])
def test_contract_survives_set_e_and_pipefail(path, tmp_path):
    """The contract must be safe for a caller running `set -euo pipefail`.

    The card does NOT set -e; the deployment gate does. That asymmetry bit twice
    in one afternoon, both times SILENTLY — the gate exited with no message at
    the instant the launch environment had assembled correctly:

      * a function whose last statement is a guarded append returns that guard's
        false status, and `set -e` treats a non-zero function call as fatal;
      * `_ml=$(ldconfig -p | awk …)` takes the PIPELINE's status under `pipefail`,
        so a node where ldconfig is not on PATH aborts the launch rather than
        skipping an optional bind.

    Both are invisible without `set -e`, which is exactly why the file that only
    the card exercised could carry them for as long as it liked."""
    r = subprocess.run(
        [_BASH, "--noprofile", "--norc", "-c",
         f'set -euo pipefail\nbinds=(); envs=()\n. "{LAUNCHER}"\n'
         f'aba_launch_args\necho "OK ${{#binds[@]}} ${{#envs[@]}}"'],
        # ABA_LAUNCH_WORKDIR, or the contract synthesizes nss/ under the CWD —
        # which for a pytest run is the repo, and a guard that dirties the tree it
        # guards gets ignored or deleted, and then it guards nothing.
        env={"PATH": path, "HOME": os.environ.get("HOME", "/tmp"),
             "ABA_LAUNCH_WORKDIR": str(tmp_path), "ABA_RUNTIME_DIR": str(tmp_path)},
        cwd=str(tmp_path), capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.startswith("OK "), (
        f"the contract aborts a `set -euo pipefail` caller (rc={r.returncode})\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr[-400:]!r}")
    # ARMED: it must have actually assembled something, or "survived" is vacuous.
    n_binds = int(r.stdout.split()[1])
    assert n_binds > 0, "no binds assembled — this run proves nothing about surviving"


def test_every_contract_function_ends_in_an_explicit_success():
    """The property behind the test above, asserted structurally so a NEW function
    cannot reintroduce it. `set -e` makes a function's trailing guarded append its
    return value; every entry point here must end `return 0`."""
    body = LAUNCHER.read_text()
    fns = re.findall(r"^(aba_launch_\w+)\(\) \{(.*?)^\}", body, re.S | re.M)
    assert len(fns) >= 5, f"expected the contract's functions, found {[f[0] for f in fns]}"
    for name, src in fns:
        last = [ln for ln in src.strip().splitlines()
                if ln.strip() and not ln.strip().startswith("#")][-1]
        assert last.strip() == "return 0", (
            f"{name}() ends with {last.strip()!r}; a guarded append as the last "
            f"statement makes the function return that guard's status and kills a "
            f"`set -e` caller")
