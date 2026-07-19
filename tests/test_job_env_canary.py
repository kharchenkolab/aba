"""Guard: a background python job whose base env is poisoned (numpy won't import)
fails LOUDLY with an actionable message at job start, instead of a cryptic
ImportError deep in the user's code (the prj_6d986f40 symptom). A healthy env
proceeds to run the code normally.
"""
from __future__ import annotations
import os, sys, json, tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import core.jobs.slurm_entry as SE          # noqa: E402
import core.exec.verify as EI               # noqa: E402
import core.exec.run as RUN                 # noqa: E402


def _spec(tmp_path, code="print('hi')", env=None):
    rp = tmp_path / "result.json"
    sp = tmp_path / "job_spec.json"
    sp.write_text(json.dumps({"kind": "run_python", "code": code, "project_id": "p",
                              "run_id": "r", "timeout_s": 60, "result_path": str(rp), "env": env}))
    return sp, rp


def test_poisoned_env_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.setattr(EI, "verify_python_imports",
                        lambda names, **k: (False, "ImportError: numpy.core._multiarray_umath"))
    ran = {"called": False}
    monkeypatch.setattr(RUN, "run_python_code", lambda *a, **k: ran.__setitem__("called", True) or {})
    sp, rp = _spec(tmp_path)
    monkeypatch.setattr(sys, "argv", ["slurm_entry", str(sp)])
    rc = SE.main()
    assert rc == 1
    res = json.loads(rp.read_text())
    assert "environment is broken" in res.get("error", "") and "numpy" in res["error"]
    assert ran["called"] is False, "user code should NOT run on a poisoned env"


def test_healthy_env_runs_code(monkeypatch, tmp_path):
    monkeypatch.setattr(EI, "verify_python_imports", lambda names, **k: (True, ""))
    ran = {"called": False}
    monkeypatch.setattr(RUN, "run_python_code",
                        lambda *a, **k: ran.__setitem__("called", True) or {"returncode": 0})
    sp, rp = _spec(tmp_path)
    monkeypatch.setattr(sys, "argv", ["slurm_entry", str(sp)])
    rc = SE.main()
    assert rc == 0 and ran["called"] is True
