"""Guard: a Nextflow HEAD is no longer special-cased onto the legacy sbatch lane
— on a SHARED-FS site it rides the SAME weft task as python/R (validated live on
VBC: the head runs as a bare weft task, `slurm_entry` dispatches `run_nextflow`
on the node). A regression that re-adds a `kind != "run_nextflow"` fork would
silently route heads back to sbatch.

The one deliberate fork (site_contract, 2026-08-25): on a DETACHED site the head
stays on the LOCAL lane — the detached harness has no `run_nextflow` branch, and
a nextflow head is an orchestrator, not compute — while python/R still ride the
cluster site. Both sides of that fork are guarded here. See
core.jobs.submitter._slurm_lane.
"""
from core.jobs import submitter


def test_nextflow_rides_weft_lane_on_a_shared_fs_site(monkeypatch):
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(ws, "weft_slurm_site", lambda: "cluster")
    # hermetic: "cluster" is declared nowhere, and site_contract treats an
    # undeclared site as detached BY DESIGN — pin the shared-fs answer so this
    # test asserts the ride-together property, not the declaration machinery
    monkeypatch.setattr(ws, "site_contract", lambda s: "shared-fs")
    for kind in ("run_nextflow", "run_python", "run_r", None):
        lane = submitter._slurm_lane(kind)
        assert type(lane).__name__ == "WeftSubmitter", \
            f"kind={kind!r} should ride the weft lane, got {type(lane).__name__}"
        assert lane.site == "cluster"


def test_nextflow_head_stays_local_on_a_detached_site(monkeypatch):
    # The detached lane ships a stdlib-only harness with no run_nextflow
    # branch; the head orchestrates from the controller and fans work out via
    # Nextflow's own executor. Everything else still rides the cluster site.
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(ws, "weft_slurm_site", lambda: "cluster")
    monkeypatch.setattr(ws, "site_contract", lambda s: "detached")
    head = submitter._slurm_lane("run_nextflow")
    assert type(head).__name__ == "WeftSubmitter" and head.site == "local", \
        f"nextflow head must stay on the local lane, got {type(head).__name__} " \
        f"site={getattr(head, 'site', None)!r}"
    for kind in ("run_python", "run_r", None):
        lane = submitter._slurm_lane(kind)
        assert type(lane).__name__ == "WeftSubmitter" and lane.site == "cluster"


def test_no_site_degrades_to_local_weft_lane_never_sbatch(monkeypatch):
    # Weft-only (W3.4 tail): the legacy sbatch lane is DELETED. With no slurm-kind
    # weft site declared, _slurm_lane degrades to the LOCAL weft lane (WeftSubmitter
    # when the substrate is up, else the in-process LocalSubmitter) — never sbatch,
    # for nextflow AND python alike.
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(ws, "weft_slurm_site", lambda: None)
    for kind in ("run_nextflow", "run_python"):
        name = type(submitter._slurm_lane(kind)).__name__
        assert name in ("WeftSubmitter", "LocalSubmitter"), name
        assert name != "SlurmSubmitter"
