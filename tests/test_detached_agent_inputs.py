"""Detached lane — agent-input guards (change-discipline; misc/detached_compute.md S3).

Behavioral, not structural: the `site=` param reaches the submit layer, the
tool validates it (background-only; unknown site names the real ones), and
describe_compute surfaces declared remote machines so placement can follow
the data. The end-to-end AGENT behavior guard is the live multinode study.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_dai_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "d.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.graph._schema import init_db  # noqa: E402
init_db()


def test_run_python_threads_site_to_submit_kwargs():
    """bg_submit_kwargs carries `site` — the SINGLE source both run_python and
    the guide background-intercept use, so neither drops the placement."""
    from content.bio.tools.run_exec import bg_submit_kwargs
    kw = bg_submit_kwargs({"site": "mendel", "estimated_runtime_min": 5}, "default")
    assert kw["site"] == "mendel"
    assert bg_submit_kwargs({}, "default")["site"] is None


def test_site_without_background_goes_sync_remote(monkeypatch):
    """site= WITHOUT background routes to the SYNCHRONOUS remote path —
    placement is orthogonal to duration (a short remote step behaves like a
    local call; only long steps use background=True)."""
    import content.bio.tools.run_exec as rx
    seen = {}
    monkeypatch.setattr(rx, "_run_remote_sync",
                        lambda inp, ctx, pid, tid, kind: seen.update(
                            site=inp["site"], kind=kind) or {"status": "ok"})
    out = rx.run_python({"code": "x=1", "site": "mendel"}, {"thread_id": "t"})
    assert out == {"status": "ok"}
    assert seen == {"site": "mendel", "kind": "run_python"}


def test_describe_compute_surfaces_remote_sites(monkeypatch):
    """The agent sizes up external machines from describe_compute — declared
    remote sites appear with kind + capacity, and the summary names them."""
    import content.bio.mcp_servers.aba_core.tools.jobs as jobsmod
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(ws, "declared_compute_sites", lambda: [
        {"name": "local", "kind": "local", "contract": "shared-fs"},
        {"name": "mendel", "kind": "ssh", "contract": "detached"}])

    class _Comp:
        def sync_call(self, name, *a, **k):
            if name == "sites_list":
                return [{"name": "mendel", "cpus": 64, "mem_gb": 512,
                         "gpus": 0, "health": "ok"}]
            return []
    import core.compute.adapter as admod
    monkeypatch.setattr(admod, "get_compute", lambda: _Comp())

    captured = {}

    class _MCP:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco
    jobsmod.register_jobs_tools(_MCP())
    e = captured["describe_compute"]()
    remotes = {s["name"]: s for s in e.get("remote_sites", [])}
    assert "mendel" in remotes and "local" not in remotes
    assert remotes["mendel"]["kind"] == "ssh" and remotes["mendel"]["cpus"] == 64
    assert "mendel" in e["summary"]


def _standalone() -> int:
    import traceback

    class _MP:
        def __init__(self): self._u = []
        def setattr(self, t, n, v):
            self._u.append((t, n, getattr(t, n))); setattr(t, n, v)
        def undo(self):
            for t, n, o in reversed(self._u):
                setattr(t, n, o)
            self._u.clear()

    rc = 0
    for t in (test_run_python_threads_site_to_submit_kwargs,
              test_site_without_background_goes_sync_remote,
              test_describe_compute_surfaces_remote_sites):
        mp = _MP()
        try:
            t(mp) if t.__code__.co_argcount else t()
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
        finally:
            mp.undo()
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
