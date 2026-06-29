"""Gap 2: a background/Slurm job must be tailable LIVE. The executor's stream
mode tees the child's stdout/stderr to this process's stdout (which sbatch -o
captures to job.log) AS IT RUNS, while still returning the captured output.
Without stream, communicate() buffers everything to exit and job.log stays empty.
"""
import contextlib
import io
import os
import sys
import tempfile

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_strm_"))
_BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.exec.base import Provisioning          # noqa: E402
from core.exec.local import LocalSubprocessExecutor  # noqa: E402

_EMIT = "import sys\nfor i in range(3):\n    print('LINE%d' % i, flush=True)\n"


def test_stream_tees_live_and_captures():
    ex = LocalSubprocessExecutor()
    env = ex.materialize(Provisioning())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = ex.exec(env, [sys.executable, "-c", _EMIT],
                      cwd=tempfile.mkdtemp(), timeout_s=30, stream=True)
    teed = buf.getvalue()
    assert "LINE0" in teed and "LINE2" in teed          # tee'd live to our stdout
    assert "LINE0" in res.stdout and "LINE2" in res.stdout   # AND captured for result
    assert res.returncode == 0
    assert not res.timed_out


def test_no_stream_does_not_tee():
    ex = LocalSubprocessExecutor()
    env = ex.materialize(Provisioning())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = ex.exec(env, [sys.executable, "-c", "print('HELLO')"],
                      cwd=tempfile.mkdtemp(), timeout_s=30, stream=False)
    assert "HELLO" not in buf.getvalue()     # default path is silent (no tee)
    assert "HELLO" in res.stdout             # still captured


def test_stream_timeout_is_reported():
    ex = LocalSubprocessExecutor()
    env = ex.materialize(Provisioning())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = ex.exec(env, [sys.executable, "-c", "import time; time.sleep(30)"],
                      cwd=tempfile.mkdtemp(), timeout_s=1, stream=True)
    assert res.timed_out is True             # streaming path still honors the wall clock
