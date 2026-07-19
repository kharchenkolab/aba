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


def test_site_env_skips_local_realization(monkeypatch):
    """A site= job must never fall into the local env lanes (the wrong-machine
    bug): the named-env block realized the env LOCALLY (minutes of waste — the
    site realizes its own copy via weft), and with kernels disabled the local
    one-shot fallback ran the code HERE while the agent believed it ran on the
    remote. The site branch must fire FIRST, like run_r's."""
    import content.bio.tools.run_exec as rx
    from core.compute import named_envs
    import core.config as cfg

    def _boom(*a, **k):
        raise AssertionError("local env lane must not run for site= jobs")
    monkeypatch.setattr(named_envs, "ensure_ready", _boom)
    monkeypatch.setattr(cfg, "KERNEL_ENABLED", False)
    monkeypatch.setattr(rx, "_run_in_named_env", _boom)
    monkeypatch.setattr(rx, "_run_remote_sync",
                        lambda inp, ctx, pid, tid, kind: {"status": "ok",
                                                          "kind": kind})
    out = rx.run_python({"code": "x=1", "site": "mendel", "env": "steps"},
                        {"thread_id": "t"})
    assert out == {"status": "ok", "kind": "run_python"}


def test_site_background_skips_local_realization(monkeypatch):
    """site= + background routes to the queued submit WITHOUT realizing the
    named env locally — weft realizes the EnvID at the site."""
    import content.bio.tools.run_exec as rx
    from core.compute import named_envs
    import core.exec.router as router
    import core.exec.compute_env as ce
    import core.jobs.runner as runner

    def _boom(*a, **k):
        raise AssertionError("local realization must not run for site= jobs")
    monkeypatch.setattr(named_envs, "ensure_ready", _boom)
    monkeypatch.setattr(named_envs, "resolve",
                        lambda pid, name: {"env_id": "env-1"})
    monkeypatch.setattr(ce, "compute_env", lambda: {})

    class _Choice:
        location = "background"
        rationale = "test"
    monkeypatch.setattr(router, "decide", lambda **k: _Choice())
    seen = {}
    monkeypatch.setattr(runner, "submit_python_job",
                        lambda code, **kw: seen.update(kw) or {"id": "job_t1"})
    out = rx.run_python({"code": "x=1", "site": "mendel", "env": "steps",
                         "background": True}, {"thread_id": "t"})
    assert out.get("deferred")
    assert seen["site"] == "mendel" and seen["env"] == "steps"


def test_sync_remote_env_identity(monkeypatch):
    """The sync path resolves env identity by the SAME rules as the background
    lane: env=None follows the project's active env, 'default' normalizes to
    None, and a nonexistent named env errors helpfully instead of silently
    running on the node's system runtime."""
    import content.bio.tools.run_exec as rx
    from core.compute import named_envs
    import core.jobs.submit as sub

    monkeypatch.setattr(named_envs, "get_active", lambda pid, lang: "proj-env")
    monkeypatch.setattr(named_envs, "resolve",
                        lambda pid, name: {"env_id": "e"} if name == "proj-env"
                        else None)
    seen = {}

    def _capture(code, **kw):
        seen.update(kw)
        raise ValueError("stop at submit")
    monkeypatch.setattr(sub, "submit_python_job", _capture)
    out = rx._run_remote_sync({"code": "x", "site": "m"}, {}, "default", "t",
                              "run_python")
    assert out["status"] == "error" and seen["env"] == "proj-env"
    seen.clear()
    out = rx._run_remote_sync({"code": "x", "site": "m", "env": "default"}, {},
                              "default", "t", "run_python")
    assert out["status"] == "error" and seen["env"] is None
    seen.clear()
    out = rx._run_remote_sync({"code": "x", "site": "m", "env": "ghost"}, {},
                              "default", "t", "run_python")
    assert "ghost" in out["note"] and not seen


def test_run_r_kernel_selfheals_to_stateless(monkeypatch):
    """run_r must self-heal like run_python: a transient kernel-boot failure
    gets a hard reset + ONE retry, then the stateless Rscript one-shot with a
    LOUD kernel_warning — it previously hard-errored on the first hiccup."""
    import content.bio.tools.run_exec as rx
    import core.config as cfg
    from core.compute import base_env, project_env
    import core.exec.kernels as kern
    import core.exec.run as execrun

    monkeypatch.setattr(cfg, "KERNEL_ENABLED", True)
    monkeypatch.setattr(base_env, "require", lambda lang: None)
    monkeypatch.setattr(project_env, "ensure", lambda pid, lang: None)
    calls = {"boots": 0, "restarts": 0}

    class _Pool:
        def get_or_start(self, tid, lang, cwd=None):
            calls["boots"] += 1
            raise RuntimeError("slow first boot")

        def restart(self, tid, lang):
            calls["restarts"] += 1
    monkeypatch.setattr(kern, "get_pool", lambda: _Pool())
    monkeypatch.setattr(execrun, "run_r_code",
                        lambda code, **kw: {"stdout": "fallback ok",
                                            "returncode": 0})
    out = rx.run_r({"code": "x <- 1"}, {"thread_id": "t"})
    assert calls["boots"] == 2 and calls["restarts"] == 2   # exactly one retry
    assert out["stdout"] == "fallback ok"
    assert "WITHOUT the persistent R session" in out["kernel_warning"]


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
              test_site_env_skips_local_realization,
              test_site_background_skips_local_realization,
              test_sync_remote_env_identity,
              test_run_r_kernel_selfheals_to_stateless,
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
