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
    estimate must NOT collapse to the 600s interactive default in either."""
    text = open(RE.__file__).read()
    # both run_python and run_r derive bg_timeout_s from the estimate…
    assert text.count("_background_timeout_s(input_, est_min)") >= 2
    # …and neither background submit passes the raw interactive `timeout_s`.
    assert "submit_r_job" in text and "submit_python_job" in text
    assert text.count("timeout_s=bg_timeout_s") >= 2
