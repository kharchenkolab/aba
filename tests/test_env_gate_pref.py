"""P3: user-scope env_gate preference. set_user_pref persists discovery.env_gate;
_env_gate_policy resolves user-pref > env-var > bundle > auto(soft); gate_counts
reports the effect for the settings card."""
from __future__ import annotations
import os, sys, tempfile

_tmp = tempfile.mkdtemp(prefix="aba_gatepref_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import pytest  # noqa: E402
import core.skills.loader as L  # noqa: E402
from core.skills.loader import (SkillSpec, register_skill_spec, gate_counts,  # noqa: E402
                                 _env_gate_policy)
from core.config import get_user_pref, set_user_pref  # noqa: E402
from core.exec import compute_env as ce  # noqa: E402

NF = SkillSpec(name="bp-nf", requires_tools=("run_nextflow",), visibility="local", domain="genomics")
PY1 = SkillSpec(name="py1", requires_tools=("run_python",), visibility="local", domain="genomics")
PY2 = SkillSpec(name="py2", requires_tools=("run_python",), visibility="local", domain="genomics")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    saved = dict(L._REGISTRY)
    L._REGISTRY.clear(); L._INDEX = None
    for s in (NF, PY1, PY2):
        register_skill_spec(s)
    monkeypatch.setattr(ce, "tool_viable", lambda t, profile=None: t != "run_nextflow")  # laptop
    monkeypatch.delenv("ABA_DISCOVERY_ENV_GATE", raising=False)
    set_user_pref("discovery.env_gate", "")   # start clean
    yield
    set_user_pref("discovery.env_gate", "")
    L._REGISTRY.clear(); L._REGISTRY.update(saved); L._INDEX = None


def test_pref_roundtrip():
    assert get_user_pref("discovery.env_gate") is None
    set_user_pref("discovery.env_gate", "hard")
    assert get_user_pref("discovery.env_gate") == "hard"
    set_user_pref("discovery.env_gate", "")            # clear
    assert get_user_pref("discovery.env_gate") is None


def test_policy_default_is_soft():
    assert _env_gate_policy() == "soft"                # no pref, no env → auto → soft


def test_user_pref_drives_policy():
    set_user_pref("discovery.env_gate", "hard"); assert _env_gate_policy() == "hard"
    set_user_pref("discovery.env_gate", "off");  assert _env_gate_policy() == "off"
    set_user_pref("discovery.env_gate", "auto"); assert _env_gate_policy() == "soft"  # auto→soft


def test_user_pref_beats_env_var(monkeypatch):
    monkeypatch.setenv("ABA_DISCOVERY_ENV_GATE", "off")
    set_user_pref("discovery.env_gate", "hard")
    assert _env_gate_policy() == "hard"                # user pref wins over env var
    set_user_pref("discovery.env_gate", "")
    assert _env_gate_policy() == "off"                 # falls back to env var when no pref


def test_gate_counts():
    c = gate_counts("hard")
    assert c["total"] == 3 and c["blocked"] == 1 and c["runnable"] == 2 and c["policy"] == "hard"
    # soft still reports the blocked count (they're de-prioritized, not gone)
    assert gate_counts("soft")["blocked"] == 1
