"""Guard #32 (strategy-blind isolated-env one-shot). `named_envs.run_in` must run
a named env's code directly ONLY when the env has a real on-disk prefix (directory
strategy); a SQUASHFS env has no `<prefix>/bin/python` at rest, so it must route
THROUGH weft (a task with env=<EnvID> that weft mounts+activates). This pins the
dispatch so a regression can't reintroduce a raw-interp exec that breaks on squashfs.
"""
from pathlib import Path

from core.compute import named_envs


def _common(monkeypatch):
    monkeypatch.setattr(named_envs, "resolve",
                        lambda pid, name: {"env_id": "env:v1:abc", "language": "python"})
    monkeypatch.setattr(named_envs, "ensure_ready", lambda *a, **k: None)


def test_run_in_uses_weft_when_no_raw_prefix(monkeypatch):
    """Squashfs (no on-disk prefix) → route through weft, NOT a raw subprocess."""
    _common(monkeypatch)
    monkeypatch.setattr(named_envs, "_ready_prefix", lambda eid: None)
    called = {}
    monkeypatch.setattr(named_envs, "_run_in_via_weft",
                        lambda eid, lang, code, **kw: called.update(eid=eid, lang=lang)
                        or {"ok": True, "stdout": "via-weft", "stderr": "", "returncode": 0})

    def _boom(*a, **k):
        raise AssertionError("must not exec a raw interpreter under squashfs")
    monkeypatch.setattr(named_envs.subprocess, "run", _boom)

    r = named_envs.run_in("p", "myenv", "print(1)")
    assert r["stdout"] == "via-weft" and called["eid"] == "env:v1:abc"


def test_run_in_uses_subprocess_when_prefix_exists(monkeypatch):
    """Directory strategy (a real prefix) → the fast in-process subprocess path,
    exact interpreter, NOT a weft task."""
    _common(monkeypatch)
    monkeypatch.setattr(named_envs, "_ready_prefix", lambda eid: Path("/site/envs/abc"))

    def _no_weft(*a, **k):
        raise AssertionError("must not go through weft when a raw prefix exists")
    monkeypatch.setattr(named_envs, "_run_in_via_weft", _no_weft)

    seen = {}

    class _P:
        returncode = 0
        stdout = "direct"
        stderr = ""

    def _run(argv, **kw):
        seen["interp"] = argv[0]
        return _P()
    monkeypatch.setattr(named_envs.subprocess, "run", _run)

    r = named_envs.run_in("p", "myenv", "print(1)")
    assert r["ok"] and r["stdout"] == "direct"
    assert seen["interp"] == "/site/envs/abc/bin/python"   # exact prefix interpreter


def test_run_in_unknown_env_errors(monkeypatch):
    monkeypatch.setattr(named_envs, "resolve", lambda pid, name: None)
    r = named_envs.run_in("p", "ghost", "print(1)")
    assert r["ok"] is False and "does not exist" in r["stderr"]
