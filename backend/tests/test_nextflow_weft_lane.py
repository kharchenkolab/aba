"""Guard: a Nextflow HEAD is no longer special-cased onto the legacy sbatch lane
— it rides the SAME weft task as python/R (validated live on VBC: the head runs as
a bare weft task, `slurm_entry` dispatches `run_nextflow` on the node). So
`_slurm_lane("run_nextflow")` must return the WeftSubmitter whenever a slurm-kind
weft site is declared, identically to a python job. A regression that re-adds the
`kind != "run_nextflow"` fork would silently route heads back to sbatch. See
core.jobs.submitter._slurm_lane.
"""
from core.jobs import submitter


def test_nextflow_rides_weft_lane_when_site_declared(monkeypatch):
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(ws, "weft_slurm_site", lambda: "cluster")
    for kind in ("run_nextflow", "run_python", "run_r", None):
        lane = submitter._slurm_lane(kind)
        assert type(lane).__name__ == "WeftSubmitter", \
            f"kind={kind!r} should ride the weft lane, got {type(lane).__name__}"
        assert lane.site == "cluster"


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
