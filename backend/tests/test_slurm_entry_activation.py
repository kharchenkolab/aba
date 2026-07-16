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
