"""Guard #31 (strategy-blind env execution): a modern weft job carries NO
aba-resolved `interp` — raw `<prefix>/bin/python` paths DON'T EXIST at rest under
the squashfs realization strategy (BeeGFS/parallel-FS/cluster roots). Instead the
task runs with `env=<EnvID>`, weft mounts+activates it on the node, and the entry
resolves the interpreter from `$CONDA_PREFIX` (live during the task). This test
pins that resolution so a regression can't silently re-introduce the raw-prefix
assumption. See core.jobs.slurm_entry._interp_from_activation + weft_submitter.
"""
from core.jobs.slurm_entry import _interp_from_activation


def test_no_activation_and_no_spec_interp_returns_none(monkeypatch):
    # No CONDA_PREFIX (not inside an activated weft task) and no spec interp →
    # None, so run.py raises its loud "no interpreter resolved" error rather than
    # exec'ing a bogus path.
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    assert _interp_from_activation({"kind": "run_python"}) is None


def test_python_interp_from_conda_prefix(monkeypatch):
    monkeypatch.setenv("CONDA_PREFIX", "/mnt/env/.pixi/envs/default")
    assert _interp_from_activation({"kind": "run_python"}) == \
        "/mnt/env/.pixi/envs/default/bin/python"


def test_r_interp_from_conda_prefix(monkeypatch):
    monkeypatch.setenv("CONDA_PREFIX", "/mnt/env/.pixi/envs/default")
    assert _interp_from_activation({"kind": "run_r"}) == \
        "/mnt/env/.pixi/envs/default/bin/Rscript"


def test_explicit_spec_interp_wins_over_activation(monkeypatch):
    # A legacy/explicit interp in the spec overrides activation (back-compat).
    monkeypatch.setenv("CONDA_PREFIX", "/mnt/env/.pixi/envs/default")
    assert _interp_from_activation(
        {"kind": "run_python", "interp": "/legacy/prefix/bin/python"}
    ) == "/legacy/prefix/bin/python"


# ── what actually happens when the resolution above returns None ─────────────

def _run_entry(tmp_path, monkeypatch, spec_extra, conda_prefix=None):
    """Drive slurm_entry.main() with the exec core stubbed, and report both
    the result it wrote and whether user code was ever reached."""
    import json
    import sys

    import core.exec.run as _run
    from core.jobs import slurm_entry
    called: list = []

    def _spy(code, **kw):
        called.append(kw)
        return {"returncode": 0, "stdout": "ran"}
    monkeypatch.setattr(_run, "run_r_code", _spy, raising=False)
    monkeypatch.setattr(_run, "run_python_code", _spy, raising=False)
    if conda_prefix:
        monkeypatch.setenv("CONDA_PREFIX", conda_prefix)
    else:
        monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.delenv("WEFT_ENV_ID", raising=False)

    res = tmp_path / "result.json"
    spec = {"code": "1+1", "kind": "run_r", "project_id": "p1", "run_id": "r1",
            "timeout_s": 60, "result_path": str(res), "env": None,
            "interp": None, **spec_extra}
    sp = tmp_path / "spec.json"
    sp.write_text(json.dumps(spec))
    monkeypatch.setattr(sys, "argv", ["slurm_entry", str(sp)])
    rc = slurm_entry.main()
    return rc, json.loads(res.read_text()), called


def test_env_carrying_job_without_activation_refuses_on_the_node(tmp_path, monkeypatch):
    """The node end of the bug-#1 chain (field report, 2026-08).

    The spec carries a frozen `env_id`, so weft was supposed to mount and
    activate it — but nothing did, and CONDA_PREFIX is unset. The interpreter
    resolution returns None, and the docstring beside it claims "run.py raises
    loudly". It does not. run.py falls into its DEFAULT lane, which asks this
    node for a compute substrate — and slurm_entry runs as `python -m`, so the
    FastAPI lifespan never ran and there IS no substrate here, by design. The
    node therefore reports `substrate_offline: compute substrate not
    configured yet`, which reads as a platform outage. That is the message the
    user's agent turned into a bug report about the cluster being down.

    The node must instead name what actually happened, and must not reach for
    a substrate it was never going to have.
    """
    rc, result, called = _run_entry(tmp_path, monkeypatch,
                                    {"env_id": "env:v1:deadbeef"})
    assert rc == 1
    err = str(result.get("error") or "")
    assert not called, "user code must not run without the env it was given"
    assert "substrate" not in err.lower(), \
        f"node reached for a substrate it never has, by design: {err!r}"
    assert "env:v1:deadbeef" in err, "must name the env that failed to activate"
    assert "activat" in err.lower(), "must name activation as the cause"


def test_bare_job_without_activation_is_not_an_activation_failure(tmp_path, monkeypatch):
    """WIDE: a job submitted with NO env_id is deliberately bare (the
    env='system' lever). That is not an activation failure and must not be
    reported as one — it runs on the node's own interpreter."""
    rc, result, called = _run_entry(tmp_path, monkeypatch, {"env_id": None})
    assert called, "the explicit bare lever must still run"
    assert rc == 0 and "error" not in result


def test_activated_job_runs(tmp_path, monkeypatch):
    """WIDE: the normal path stays open — activation took, so user code runs
    with the activated interpreter."""
    rc, result, called = _run_entry(tmp_path, monkeypatch,
                                    {"env_id": "env:v1:abc"},
                                    conda_prefix=str(tmp_path / "prefix"))
    assert called and rc == 0
    assert called[0]["interp"] == str(tmp_path / "prefix" / "bin" / "Rscript")
