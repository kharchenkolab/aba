"""In-process prepare-job tracker (core/viewers/prepare.py)."""
import time

from core.viewers import prepare


class _Res:
    def __init__(self, url, sls=None, label=None):
        self.url = url; self.set_local_storage = sls; self.label = label


def _wait(job_id, want, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        s = prepare.status(job_id)
        if s and s["status"] == want:
            return s
        time.sleep(0.01)
    return prepare.status(job_id)


def test_ready_carries_url_and_storage():
    jid = prepare.start(lambda set_phase: (set_phase("Converting…"), _Res("/pagoda3/?store=/s/", {"p3-agent-proxy": "/pagoda3-api"}, "Explore"))[1],
                        label="Explore")
    s = _wait(jid, "ready")
    assert s["status"] == "ready"
    assert s["url"] == "/pagoda3/?store=/s/"
    assert s["set_local_storage"] == {"p3-agent-proxy": "/pagoda3-api"}
    assert s["label"] == "Explore"


def test_error_carries_message():
    def boom(set_phase):
        set_phase("Converting…")
        raise ValueError("no counts measure")
    jid = prepare.start(boom)
    s = _wait(jid, "error")
    assert s["status"] == "error"
    assert "no counts measure" in s["error"]


def test_phase_updates_before_completion():
    import threading
    gate = threading.Event()
    def slow(set_phase):
        set_phase("Converting the dataset…")
        gate.wait(1.0)
        return _Res("/u/")
    jid = prepare.start(slow)
    # observe the preparing phase before we release the gate
    time.sleep(0.05)
    s = prepare.status(jid)
    assert s["status"] == "preparing"
    assert s["phase"] == "Converting the dataset…"
    gate.set()
    assert _wait(jid, "ready")["url"] == "/u/"


def test_unknown_job_is_none():
    assert prepare.status("nope") is None
