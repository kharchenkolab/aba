"""Startup self-check registry + the ENVS_DIR-shared check (finding F6b, HIGH).

Behavioral guard for the loud-but-boot safety net: the registry aggregates
ok/degraded/worst correctly (including a raising check -> critical), and
`check_envs_dir_shared` fires ONLY under a Slurm submitter and classifies
shared / node-local / unknown into the right severity.

Standalone-runnable (base env lacks pytest): `python tests/test_selfcheck.py`.
"""
from __future__ import annotations

import os
import sys
import tempfile

# Make backend/ importable + pin a throwaway runtime (mirrors conftest for the
# standalone path so we never touch a sourced .env's live runtime).
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_selfcheck_test_"))

from core.runtime import selfcheck          # noqa: E402
from core.exec import env_integrity         # noqa: E402


def _reset():
    selfcheck._checks.clear()
    selfcheck._results.clear()


# ── registry ──────────────────────────────────────────────────────────────
def test_registry_aggregates():
    _reset()
    selfcheck.register("a", lambda: {"ok": True, "severity": "info", "detail": "fine"})
    selfcheck.register("b", lambda: {"ok": False, "severity": "high", "detail": "bad"})
    res = selfcheck.run()
    assert len(res) == 2
    assert selfcheck.degraded() is True
    assert selfcheck.worst_severity() == "high"
    assert [w["name"] for w in selfcheck.warnings()] == ["b"]
    s = selfcheck.summary()
    assert s["degraded"] and s["worst"] == "high" and len(s["checks"]) == 2


def test_registry_healthy():
    _reset()
    selfcheck.register("a", lambda: {"ok": True})
    selfcheck.run()
    assert selfcheck.degraded() is False
    assert selfcheck.worst_severity() is None
    assert selfcheck.warnings() == []


def test_raising_check_is_critical():
    _reset()

    def boom():
        raise RuntimeError("kaboom")

    selfcheck.register("boom", boom)
    res = selfcheck.run()
    assert res[0]["ok"] is False and res[0]["severity"] == "critical"
    assert "kaboom" in res[0]["detail"]


def test_worst_severity_ordering():
    _reset()
    selfcheck.register("w", lambda: {"ok": False, "severity": "warning", "detail": ""})
    selfcheck.register("h", lambda: {"ok": False, "severity": "high", "detail": ""})
    selfcheck.register("i", lambda: {"ok": True, "severity": "info", "detail": ""})
    selfcheck.run()
    assert selfcheck.worst_severity() == "high"


def test_register_is_idempotent_by_name():
    _reset()
    selfcheck.register("x", lambda: {"ok": False})
    selfcheck.register("x", lambda: {"ok": True})   # replaces
    selfcheck.run()
    assert selfcheck.degraded() is False


# ── ENVS_DIR-shared check ─────────────────────────────────────────────────
def _patch_kind(kind, detail="d"):
    orig = env_integrity.envs_dir_fs_kind
    env_integrity.envs_dir_fs_kind = lambda: (kind, detail)
    return orig


def test_envs_check_local_submitter_ok():
    os.environ["ABA_BATCH_SUBMITTER"] = "local"
    try:
        r = env_integrity.check_envs_dir_shared()
        assert r["ok"] is True
    finally:
        os.environ.pop("ABA_BATCH_SUBMITTER", None)


def test_envs_check_slurm_node_local_fires_high():
    os.environ["ABA_BATCH_SUBMITTER"] = "slurm"
    orig = _patch_kind("node_local", "/workspace/aba-runtime/envs on tmpfs (node-local)")
    try:
        r = env_integrity.check_envs_dir_shared()
        assert r["ok"] is False and r["severity"] == "high"
        assert "node-local" in r["detail"]
    finally:
        env_integrity.envs_dir_fs_kind = orig
        os.environ.pop("ABA_BATCH_SUBMITTER", None)


def test_envs_check_slurm_shared_passes():
    os.environ["ABA_BATCH_SUBMITTER"] = "slurm"
    orig = _patch_kind("shared", "/nfs/envs on nfs (shared)")
    try:
        r = env_integrity.check_envs_dir_shared()
        assert r["ok"] is True
    finally:
        env_integrity.envs_dir_fs_kind = orig
        os.environ.pop("ABA_BATCH_SUBMITTER", None)


def test_envs_check_slurm_unknown_warns():
    os.environ["ABA_BATCH_SUBMITTER"] = "slurm"
    orig = _patch_kind("unknown", "?? on zfs (fs type not classified)")
    try:
        r = env_integrity.check_envs_dir_shared()
        assert r["ok"] is False and r["severity"] == "warning"
    finally:
        env_integrity.envs_dir_fs_kind = orig
        os.environ.pop("ABA_BATCH_SUBMITTER", None)


def test_fstype_classification_sets():
    # empirical classifier must know the canonical shared/local fstypes
    assert "nfs" in env_integrity._SHARED_FS and "lustre" in env_integrity._SHARED_FS
    assert "beegfs" in env_integrity._SHARED_FS
    assert "tmpfs" in env_integrity._LOCAL_FS and "ext4" in env_integrity._LOCAL_FS


def test_fs_type_for_path_root():
    # '/' must resolve to SOME fstype on Linux (str), None only off-procfs
    ft = env_integrity._fs_type_for_path("/")
    assert ft is None or isinstance(ft, str)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {fn.__name__}: {e!r}")
    print(f"\n{'ALL PASS' if not failed else str(failed) + ' FAILED'} ({len(fns)} tests)")
    sys.exit(1 if failed else 0)
