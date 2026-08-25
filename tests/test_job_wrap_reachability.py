"""The SIF-wrap exemption must not outlive the SIF wrap.

`check_base_dir_shared` exists to catch one thing: under the Slurm submitter,
can an offloaded job reach the interpreter? It exempts a deployment that sets
`ABA_JOB_WRAP=sif`, on this reasoning (its own words):

    WRAPPED offload (`ABA_JOB_WRAP=sif`, a fat OR weft SIF): the job RE-ENTERS
    the image via `apptainer exec` (slurm_submitter._job_body) ...

`slurm_submitter._job_body` was DELETED when the sbatch lane was retired
(880604c0). The weft lane that replaced it builds its node command as

    f"{sys.executable} -u -m core.jobs.slurm_entry {spec_path}"

with the comment "an absolute path valid on every node via the deployment's
shared FS" — true for a slim SIF with a shared base_dir, false for the weft
profile, where sys.executable is /opt/aba-venv/bin/python and exists only
inside the image. `ABA_JOB_WRAP` now has exactly one consumer in the tree, and
it is not the submitter.

Measured 2026-08-25 on the real deployment shape: the job reached Slurm
(sacct: weft-jb_… COMPLETED), the node ran it bare, and

    cmd.sh: line 34: /opt/aba-venv/bin/python: No such file or directory
    exit 127

…while /api/health reported ok, degraded=false, warnings=[] — because the
exemption above silenced the one check that would have said so.

An exemption is a promise about a mechanism. When the mechanism goes, the
promise has to go with it, or the check actively hides the failure it was
written to catch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


def test_job_wrap_exemption_requires_a_lane_that_actually_wraps(monkeypatch):
    """THE regression. submitter=slurm + job_wrap=sif + a lane that does not
    wrap must NOT report healthy."""
    from core.exec import env_integrity
    from core.jobs import submitter as sub

    monkeypatch.setattr(sub, "submitter_name", lambda: "slurm")
    monkeypatch.setenv("ABA_JOB_WRAP", "sif")
    monkeypatch.setattr(env_integrity, "base_fs_kind",
                        lambda: ("node_local", "/opt/aba-venv (in-image)"))

    r = env_integrity.check_base_dir_shared()
    assert r["ok"] is False, (
        "job_wrap=sif still exempts the deployment, but nothing in the weft "
        "submitter implements the re-entry it promises — every offloaded job "
        f"exits 127 while this reports healthy: {r}")
    assert "127" in r["detail"] or "re-ent" in r["detail"].lower(), r["detail"]


def test_the_mechanism_the_exemption_named_is_really_gone():
    """ARMED. If a wrap is implemented later, this test is what says the
    exemption may come back — rather than someone re-adding it on faith."""
    ws = (REPO / "backend" / "core" / "jobs" / "weft_submitter.py").read_text()
    assert "apptainer exec" not in ws, (
        "weft_submitter now wraps jobs — re-examine check_base_dir_shared's "
        "job_wrap exemption, which this test asserts is currently unearned")


def test_shared_base_is_still_fine(monkeypatch):
    """WIDE: the configuration that genuinely works must stay quiet."""
    from core.exec import env_integrity
    from core.jobs import submitter as sub
    monkeypatch.setattr(sub, "submitter_name", lambda: "slurm")
    monkeypatch.setattr(env_integrity, "base_fs_kind",
                        lambda: ("shared", "/shared/aba-venv on nfs"))
    assert env_integrity.check_base_dir_shared()["ok"] is True


def test_local_submitter_still_exempt(monkeypatch):
    """WIDE: none of this applies without Slurm."""
    from core.exec import env_integrity
    from core.jobs import submitter as sub
    monkeypatch.setattr(sub, "submitter_name", lambda: "local")
    assert env_integrity.check_base_dir_shared()["ok"] is True


def test_preflight_does_not_manufacture_a_wrap_promise(tmp_path):
    """The deployment must not DERIVE a wrap mode it has no lane for.

    aba_preflight used to emit `ABA_JOB_WRAP=sif` for any image.sif without an
    image.base_dir — which under the weft profile is every deployment. Nothing
    consumed it except the exemption above, so the derivation's only effect was
    to silence the check. The weft answer to sif-without-base is the DETACHED
    site contract, not a wrap; an operator who has a wrapping lane declares
    `image.job_wrap` by hand."""
    import re
    src = (REPO / "install" / "ood" / "aba_preflight.py").read_text()
    # the assignment that computes _wrap must not consult the image shape
    m = re.search(r"^\s*_wrap = .*$", src, re.M)
    assert m, "the _wrap assignment moved — re-read this guard"
    line = m.group(0)
    assert "job_wrap" in line, line
    for derived in ("ABA_SIF", "base_dir", "ABA_BASE_DIR", '"sif"', "'sif'"):
        assert derived not in line, (
            f"aba_preflight derives the job-wrap mode from {derived!r} again: {line.strip()!r}. "
            "A wrap mode that no submit lane implements does not make jobs work; "
            "it only suppresses check_base_dir_shared.")
