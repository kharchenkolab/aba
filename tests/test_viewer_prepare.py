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


def test_job_thread_inherits_caller_context():
    """The worker runs under a COPY of the caller's context: contextvars do
    not propagate into raw threads, and the project binding is a contextvar —
    without propagation, any runner that reads the project graph resolves
    against the DEFAULT project (live failure: the streaming ref arm and the
    source-not-found honesty bridge both silently degraded inside the job)."""
    import contextvars
    v = contextvars.ContextVar("prepare_probe", default="unset")
    v.set("caller-context")
    seen = {}
    jid = prepare.start(lambda set_phase: (seen.__setitem__("v", v.get()), _Res("/u/"))[1])
    s = _wait(jid, "ready")
    assert s["status"] == "ready"
    assert seen["v"] == "caller-context", \
        f"job thread must see the caller's context, saw {seen['v']!r}"


def test_prepare_emits_console_events(monkeypatch):
    """Instrumentation guard (ARMED): a finished prepare job emits exactly one
    `console` event (serve / viewer prepare) with status+duration; a failed
    job emits severity=error carrying the message. Red-proven by removing the
    obs.emit calls in _work."""
    from core.runtime import notifications
    got = []
    monkeypatch.setattr(notifications, "broadcast", got.append)
    def _events(n, timeout=2.0):
        # status flips inside the lock; the emit follows on the job thread —
        # poll the bus capture rather than racing it.
        end = time.time() + timeout
        while time.time() < end:
            evs = [e for e in got if e.get("type") == "console"]
            if len(evs) >= n:
                return evs
            time.sleep(0.01)
        return [e for e in got if e.get("type") == "console"]

    jid = prepare.start(lambda set_phase: _Res("/u/"), label="Explore")
    assert _wait(jid, "ready")["status"] == "ready"
    evs = _events(1)
    assert len(evs) == 1, "one job = one event"
    ev = evs[0]
    assert ev["category"] == "serve" and ev["verb"] == "viewer prepare"
    assert ev["status"] == "ok" and ev["ref"] == jid
    assert ev["summary"] == "Explore" and ev["dur_ms"] >= 0

    got.clear()
    def boom(set_phase):
        raise ValueError("store shape unusable")
    jid2 = prepare.start(boom)
    assert _wait(jid2, "error")["status"] == "error"
    evs = _events(1)
    assert len(evs) == 1 and evs[0]["severity"] == "error"
    assert "store shape unusable" in evs[0]["status"]
