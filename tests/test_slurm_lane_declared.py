"""A deployment that asks for Slurm must not run jobs locally in silence.

Live, 2026-08-25 (OOD, cbe-next). site.yaml declared `jobs: {submitter: slurm}`
and the cluster was ready, but no slurm-kind weft site was declared anywhere.
`core/jobs/submitter._slurm_lane` handles that by printing one line to the
server log and returning the LOCAL lane, so every background job ran inside
the user's session container — no scheduler, no allocation, competing with
the interactive kernel.

The user watched a "background" DESeq job, saw the status behave oddly, and
was then told by the agent that it had not been a Slurm job at all. Nothing in
the product said so until they asked. A degrade this large has to be visible
where misconfiguration is visible — the boot self-check — not only in a log
line nobody reads.

This is the same shape as the env-resolution swallow fixed earlier the same
day: work quietly relocated somewhere the user did not ask for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


def test_selfcheck_flags_slurm_submitter_with_no_slurm_site(monkeypatch):
    """THE regression: submitter=slurm + no slurm site => a visible warning."""
    from core.jobs import submitter as sub
    monkeypatch.setattr(sub, "submitter_name", lambda: "slurm")
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(ws, "weft_slurm_site", lambda: None)

    r = sub.check_slurm_site_declared()
    assert r["ok"] is False, r
    assert r["severity"] in ("high", "critical"), r
    d = r["detail"].lower()
    assert "slurm" in d and ("local" in d or "session" in d), r["detail"]


def test_selfcheck_quiet_when_the_site_IS_declared(monkeypatch):
    """WIDE: a correctly configured cluster must not nag."""
    from core.jobs import submitter as sub
    monkeypatch.setattr(sub, "submitter_name", lambda: "slurm")
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(ws, "weft_slurm_site", lambda: "cluster")
    assert sub.check_slurm_site_declared()["ok"] is True


def test_selfcheck_quiet_on_a_deliberately_local_deployment(monkeypatch):
    """WIDE: asking for the local submitter is a choice, not a misconfig."""
    from core.jobs import submitter as sub
    monkeypatch.setattr(sub, "submitter_name", lambda: "local")
    assert sub.check_slurm_site_declared()["ok"] is True


def test_it_is_actually_registered_at_boot():
    """ARMED: a check nobody registers cannot warn. Pin the wiring, not just
    the function — the whole defect was a signal that existed and never
    reached anyone."""
    src = (REPO / "backend" / "lifespan.py").read_text()
    assert "check_slurm_site_declared" in src, (
        "the check exists but lifespan never registers it — it would warn "
        "nobody, which is the bug it is meant to fix")
