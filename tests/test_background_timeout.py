"""Background-job timeout must be sized from the agent's `estimated_runtime_min`,
not the interactive 600s default. `run_python` did this; `run_r` did NOT — so an
R job with a 30-min estimate (e.g. a Seurat IntegrateLayers retry) was killed at
600s. These lock the derivation and the run_r/run_python parity.
"""
import os
import sys
import tempfile

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_bt_"))
_BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import content.bio.tools.run_exec as RE  # noqa: E402
from content.bio.tools.run_exec import _background_timeout_s  # noqa: E402
from core.jobs.runner import BACKGROUND_DEFAULT_TIMEOUT_S, BACKGROUND_MAX_TIMEOUT_S  # noqa: E402


def test_background_timeout_derivation():
    assert _background_timeout_s({"timeout_s": 1200}, 0) == 1200          # explicit honored
    assert _background_timeout_s({}, 30) == 30 * 60 * 2                   # estimate → 2× margin
    assert _background_timeout_s({}, 0) == max(60, min(BACKGROUND_DEFAULT_TIMEOUT_S,
                                                       BACKGROUND_MAX_TIMEOUT_S))
    assert _background_timeout_s({}, 10 ** 9) == BACKGROUND_MAX_TIMEOUT_S  # bounded by the backstop


def test_run_r_and_python_background_parity():
    """Both background paths must size the timeout from the estimate — a 30-min
    estimate must NOT collapse to the 600s interactive default in either. run_python's
    background submit routes through the shared `bg_submit_kwargs` helper (which sizes
    the timeout via `_background_timeout_s` AND carries the estimate/execution/env);
    run_r still sizes it inline. Neither forwards the raw interactive `timeout_s`."""
    text = open(RE.__file__).read()
    # run_python background submit → the shared helper (single source with guide.py)
    assert "**bg_submit_kwargs(input_, project_id)" in text
    # the helper sizes the timeout from the estimate
    assert '"timeout_s": _background_timeout_s(input_, est_min)' in text
    # run_r still sizes its background timeout from the estimate (inline)
    assert "bg_timeout_s = _background_timeout_s(input_, est_min)" in text
    assert "timeout_s=bg_timeout_s" in text
    # both background submitters remain wired
    assert "submit_r_job" in text and "submit_python_job" in text


def test_bg_submit_kwargs_threads_estimate():
    """The BACKGROUND-submit kwargs shared by run_python() and guide.py's intercept
    must carry the agent's full resource estimate — est_gpu especially (prj_6d986f40:
    the intercept dropped it, so a GPU job could not be GPU-placed) — plus execution,
    the isolated env, and an estimate-sized timeout (not the interactive default)."""
    from content.bio.tools.run_exec import bg_submit_kwargs
    kw = bg_submit_kwargs({"estimated_runtime_min": 20, "est_cores": 8, "est_mem_gb": 32,
                           "est_gpu": True, "execution": "slurm", "env": "default"}, "p")
    assert kw["estimate"] == {"runtime_min": 20.0, "cores": 8, "mem_gb": 32, "gpu": True}, kw
    assert kw["execution"] == "slurm"
    assert kw["env"] is None                       # "default" → base env
    assert kw["timeout_s"] == 20 * 60 * 2          # estimate-sized, not the interactive cap
    # a named isolated env passes through; unset estimate fields → None
    kw2 = bg_submit_kwargs({"env": "sc"}, "p")
    assert kw2["env"] == "sc"
    assert kw2["estimate"] == {"runtime_min": 0.0, "cores": None, "mem_gb": None, "gpu": None}
    assert kw2["execution"] is None
    assert kw2["timeout_s"] == max(60, min(BACKGROUND_DEFAULT_TIMEOUT_S, BACKGROUND_MAX_TIMEOUT_S))
