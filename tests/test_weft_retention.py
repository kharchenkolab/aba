"""Retention module against real weft (weft/misc/retention.md, aba side).

Drives the full aba-facing lifecycle on a real weft kernel: run a cell that writes a
file, stop the kernel (retention operates on FINISHED targets), then inventory → retain
(labeled) → verify the bytes at location.path → forget (bytes gone, retained index empty).

Opt-in: ABA_WEFT_KERNEL_IT=1 (needs weft + a realized local python env). Auto-skips.
Standalone: `ABA_WEFT_KERNEL_IT=1 python tests/test_weft_retention.py`.
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_ret_rt_"))
_BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

try:
    import pytest
    pytestmark = pytest.mark.platform
except ImportError:  # pragma: no cover
    pytest = None

_ENABLED = os.environ.get("ABA_WEFT_KERNEL_IT") == "1"


def _skip(msg: str):
    if pytest is not None:
        pytest.skip(msg, allow_module_level=False)
    raise SystemExit(f"SKIP: {msg}")


def _realized_python_env_id():
    from core.compute import adapter as admod
    envs_dir = admod.weft_workspace() / "site-local" / "envs"
    if not envs_dir.exists():
        return None
    for d in sorted(envs_dir.iterdir()):
        if (d / ".weft-ready").exists() and (d / ".pixi" / "envs" / "default"
                                             / "bin" / "python").exists():
            return d.name
    return None


def test_it_retention_lifecycle():
    if not _ENABLED:
        _skip("set ABA_WEFT_KERNEL_IT=1 to run the retention integration test")
    from core.compute import adapter as admod
    from core.compute import retention
    import core.exec.kernels.weft as wmod

    st = admod.configure()
    if not st.get("ok"):
        _skip(f"weft substrate not configured: {st.get('detail')}")
    env_id = _realized_python_env_id()
    if not env_id:
        _skip("no realized local python env")

    label = "aba-ret-test"
    # clean any prior run under this label
    try:
        retention.forget(label=label)
    except Exception:  # noqa: BLE001
        pass

    s = wmod.WeftKernelSession("ret", "python", env_id=env_id, site="local")
    kid = s.kernel_id
    r = s.execute("open('keep_me.txt','w').write('precious')\n"
                  "open('scratch.tmp','w').write('junk')\nprint('WROTE')", timeout_s=60)
    assert r.returncode == 0 and "WROTE" in r.stdout, r
    # retention operates on a FINISHED target — stop the kernel (records the inventory)
    s.shutdown()

    # 1. inventory — recorded ASYNC by the poller at kernel stop ("off-tick"), so
    # poll for it (aba can't assume it's immediately readable after stop).
    import time as _t
    from core.compute.errors import ComputeError
    inv = None
    for _ in range(30):
        try:
            inv = retention.inventory(kid)
            break
        except ComputeError as e:
            if "no inventory" in str(e).lower():
                _t.sleep(0.5); continue
            raise
    assert inv is not None, "inventory never recorded after kernel stop (poller)"
    paths = [e.get("path") for e in (inv.get("entries") or [])]
    assert any("keep_me.txt" in p for p in paths), f"inventory missing keep_me.txt: {paths}"

    # 2. retain only the keeper, labeled to the (pretend) Run; exclude the temp
    res = retention.retain(kid, include=["keep_me.txt", "*.txt"], exclude=["*.tmp"],
                           label=label, background=False)
    assert res.get("files", 0) >= 1, f"retain kept nothing: {res}"
    assert res.get("state") == "done", f"foreground retain should be done: {res}"
    loc = res.get("location") or {}
    assert loc.get("path"), f"retain result missing location.path: {res}"
    # 3. the bytes are durable at location.path (local site → on-disk here)
    if loc.get("site") in (None, "local", "@workspace") and loc.get("path"):
        import glob as _glob
        hits = _glob.glob(os.path.join(loc["path"], "**", "keep_me.txt"), recursive=True)
        assert hits, f"retained keep_me.txt not found under {loc['path']}"

    # 4. the central index shows it under the label
    idx = retention.retained(label=label)
    assert idx, f"retained_runs empty for label {label}: {idx}"

    # 5. forget by label reclaims the bytes; knowledge (inventory) survives
    rec = retention.forget(label=label)
    assert rec is not None
    idx2 = retention.retained(label=label)
    assert not idx2, f"retained_runs should be empty after forget: {idx2}"
    # inventory still readable (knowledge outlives bytes)
    inv2 = retention.inventory(kid)
    assert inv2.get("entries") is not None, "inventory should survive run_forget"


def _standalone() -> int:
    if not _ENABLED:
        print("SKIP: set ABA_WEFT_KERNEL_IT=1")
        return 0
    try:
        test_it_retention_lifecycle()
        print("  [PASS] test_it_retention_lifecycle")
        return 0
    except SystemExit as e:
        print(f"  [SKIP] {e}")
        return 0
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"  [FAIL] {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_standalone())
