"""The R kernel env must default `future` to sequential + a generous globals cap,
so Seurat IntegrateLayers (and friends) don't trip future's 500 MiB
future.globals.maxSize on real data — in-process AND in Slurm jobs (slurm_entry →
run_r_code → this same kernel env). Verified separately that R's `future` honors
these env vars (R_FUTURE_PLAN / R_FUTURE_GLOBALS_MAXSIZE).
"""
import os
import sys
import tempfile

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_kf_"))
_BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.exec.kernels.jupyter import _kernel_env  # noqa: E402


def test_r_kernel_sets_future_defaults(monkeypatch):
    import _packmode
    _packmode.enable(monkeypatch)          # W3.5: the R kernel env requires an R pack
    env = _kernel_env("r", "/tmp")
    assert env["R_FUTURE_PLAN"] == "sequential"
    assert int(env["R_FUTURE_GLOBALS_MAXSIZE"]) >= 2 * 1024 ** 3   # well above the 500 MiB default
    # python kernel doesn't get the R-only vars
    assert "R_FUTURE_PLAN" not in _kernel_env("python", "/tmp")


def test_future_defaults_overridable(monkeypatch):
    import _packmode
    _packmode.enable(monkeypatch)
    os.environ["ABA_R_FUTURE_PLAN"] = "multicore"
    os.environ["ABA_R_FUTURE_GLOBALS_MAXSIZE"] = "123"
    try:
        env = _kernel_env("r", "/tmp")
        assert env["R_FUTURE_PLAN"] == "multicore"
        assert env["R_FUTURE_GLOBALS_MAXSIZE"] == "123"
    finally:
        os.environ.pop("ABA_R_FUTURE_PLAN", None)
        os.environ.pop("ABA_R_FUTURE_GLOBALS_MAXSIZE", None)
