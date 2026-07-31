"""register_dataset / add_to_dataset contract fixes (remote-site + URL trap).

Guards three defects found tracing a live remote-node registration failure:
  1. `paths=[…]` + a remote `site=` used to SILENTLY drop `site` and bundle
     into the LOCAL data dir → a hollow/misplaced dataset. Now an explicit,
     actionable error.
  2. The `url=` lane did NO content-type check — a directory-listing / error
     HTML page registered cleanly as a tiny junk dataset. Now a controller-side
     HEAD preflight refuses a positively-HTML (or hard-error) URL, while every
     INCONCLUSIVE outcome (no network, HEAD refused) falls through so a
     site-side fetch on a better-connected machine still works.
  3. `add_to_dataset` on a REMOTE-home dataset used to fail with a misleading
     "not directory-shaped" (local isdir() on a site path). Now it names the
     site and the in-place recipe.

Run: python tests/test_register_dataset_remote.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_regds_"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import content.bio.tools.curation as cur  # noqa: E402


# ── 1. paths=[…] + remote site → explicit refusal, no silent local bundle ────

def test_paths_plus_remote_site_refused():
    r = cur.register_dataset_tool(
        {"title": "shard bundle", "paths": ["/data/a.bin", "/data/b.bin"],
         "site": "mendel"})
    assert "error" in r, r
    msg = r["error"]
    assert "mendel" in msg and "path=" in msg          # names the site + the fix
    assert "director" in msg.lower()                   # points at the dir recipe


def test_paths_with_local_site_is_allowed(monkeypatch, tmp_path):
    # site="local" must NOT trip the guard — it's the normal local bundle path.
    f1 = tmp_path / "a.bin"; f1.write_bytes(b"\x00" * 16)
    f2 = tmp_path / "b.bin"; f2.write_bytes(b"\x01" * 16)
    captured = {}

    def _fake_bundle(srcs, title):
        captured["srcs"] = list(srcs)
        d = tmp_path / "bundle"; d.mkdir()
        return str(d), list(srcs), []

    monkeypatch.setattr(cur, "_bundle_paths_into_data_dir", _fake_bundle)
    monkeypatch.setattr(cur, "_resolve_dataset_path", lambda p, ctx: p)
    # the tool imports these FRESH from their modules inside the function —
    # patch at the source, not on the curation module
    import core.graph.entities as ent
    monkeypatch.setattr(ent, "create_entity", lambda **k: "ent_local")
    import content.bio.lifecycle.runs as runsmod
    monkeypatch.setattr(runsmod, "agent_actor_for_thread", lambda tid: "agent")
    r = cur.register_dataset_tool(
        {"title": "local bundle", "paths": [str(f1), str(f2)], "site": "local"})
    # reached the bundling path (no refusal), both files considered
    assert "srcs" in captured and len(captured["srcs"]) == 2
    assert r.get("error") is None or "cannot target a remote site" not in r.get("error", "")


# ── 2. URL preflight: refuse HTML, pass everything inconclusive ──────────────

class _Resp:
    def __init__(self, ctype):
        self.headers = {"Content-Type": ctype}
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_url_preflight_blocks_html(monkeypatch):
    monkeypatch.setattr(cur, "_get_compute_sync",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched!")))
    from urllib import request as rq
    monkeypatch.setattr(rq, "urlopen", lambda *a, **k: _Resp("text/html; charset=utf-8"))
    r = cur._register_dataset_url("https://host.example/dir/", None, "listing", {}, None)
    assert "error" in r and "HTML" in r["error"]


def test_url_preflight_allows_octet_stream(monkeypatch):
    from urllib import request as rq
    monkeypatch.setattr(rq, "urlopen", lambda *a, **k: _Resp("application/octet-stream"))
    # a real fetch would follow — stub register_source so we only prove preflight passed
    import core.data.datasets as ds
    monkeypatch.setattr(ds, "register_source",
                        lambda url, site=None: (_ for _ in ()).throw(RuntimeError("PREFLIGHT_PASSED")))
    r = cur._register_dataset_url("https://host.example/data.bin", None, "d", {}, None)
    assert "error" in r and "PREFLIGHT_PASSED" in r["error"]     # got past preflight


def test_url_preflight_inconclusive_falls_through(monkeypatch):
    # HEAD raising (no network here) must NOT block — the site may fetch fine.
    from urllib import request as rq
    def _boom(*a, **k):
        raise OSError("no route to host")
    monkeypatch.setattr(rq, "urlopen", _boom)
    import core.data.datasets as ds
    monkeypatch.setattr(ds, "register_source",
                        lambda url, site=None: (_ for _ in ()).throw(RuntimeError("PREFLIGHT_PASSED")))
    r = cur._register_dataset_url("https://host.example/data.bin", "mendel", "d", {}, None)
    assert "error" in r and "PREFLIGHT_PASSED" in r["error"]

    # explicit .html path skips the HEAD entirely (caller asked for a document)
    r2 = cur._register_dataset_url("https://host.example/report.html", None, "d", {}, None)
    assert "error" in r2 and "PREFLIGHT_PASSED" in r2["error"]


def test_url_preflight_http_error_blocks(monkeypatch):
    from urllib import request as rq, error as er
    def _404(*a, **k):
        raise er.HTTPError("https://host.example/gone.bin", 404, "Not Found", {}, None)
    monkeypatch.setattr(rq, "urlopen", _404)
    r = cur._register_dataset_url("https://host.example/gone.bin", None, "d", {}, None)
    assert "error" in r and "404" in r["error"]


def test_url_preflight_405_head_refused_falls_through(monkeypatch):
    from urllib import request as rq, error as er
    def _405(*a, **k):
        raise er.HTTPError("https://host.example/data.bin", 405, "Method Not Allowed", {}, None)
    monkeypatch.setattr(rq, "urlopen", _405)
    import core.data.datasets as ds
    monkeypatch.setattr(ds, "register_source",
                        lambda url, site=None: (_ for _ in ()).throw(RuntimeError("PREFLIGHT_PASSED")))
    r = cur._register_dataset_url("https://host.example/data.bin", None, "d", {}, None)
    assert "error" in r and "PREFLIGHT_PASSED" in r["error"]


# ── 3. add_to_dataset on a remote-home dataset → named refusal ───────────────

def test_remote_lane_mints_and_records_ref(monkeypatch):
    # A remote-site path registration (durable home — bytes never touch this
    # machine) EAGERLY mints the data-plane content ref under the synchronous
    # byte-work budget and RECORDS it in the entity metadata: the recorded ref
    # is what lets the range channel's ref arm stream the dataset later with
    # no resolvable run. This lane used to drop the ref outright (found live:
    # a by-reference remote store whose producing run was deleted could not
    # stream because its registration recorded ref_path but no ref).
    class _Port:
        def __init__(self):
            self.calls = []

        def sync_call(self, name, *a, **kw):
            self.calls.append((name, a, kw))
            if name == "data_fingerprint":
                return {"entries": [
                    {"path": ".zattrs", "bytes": 100, "mtime": 1},
                    {"path": "c/0.0", "bytes": 900, "mtime": 2}],
                    "truncated": False}
            if name == "data_register":
                return {"ref": "sha256:eager", "bytes": 1000, "files": 2}
            raise AssertionError(f"unexpected verb {name}")

    port = _Port()
    import core.compute.adapter as ad
    monkeypatch.setattr(ad, "get_compute", lambda: port)
    captured = {}
    import core.graph.entities as ent
    monkeypatch.setattr(ent, "create_entity",
                        lambda **k: captured.update(k) or "ds_new")
    monkeypatch.setattr(ent, "update_entity", lambda *a, **k: None)
    monkeypatch.setattr(ent, "find_entities", lambda **k: [])
    import content.bio.lifecycle.runs as runsmod
    monkeypatch.setattr(runsmod, "agent_actor_for_thread", lambda tid: "agent")
    monkeypatch.setattr(cur, "_resolve_dataset_path",
                        lambda p, ctx, _remote_out=None: str(p))
    monkeypatch.setattr(cur, "_capture_run_key", lambda *a, **k: None)
    monkeypatch.setattr(cur, "_producing_exec_id", lambda *a, **k: None)
    r = cur.register_dataset_tool({"title": "external tree",
                                   "path": "/ext/data.store", "site": "siteA",
                                   "origin": "collaborator"})
    assert r.get("status") == "ok", r
    md = captured["metadata"]
    assert md["ref"] == "sha256:eager", md          # recorded identity
    assert md["home"] == {"site": "siteA", "path": "/ext/data.store"}
    assert md["by_reference"] is True
    # minted with the registration-lane call shape (reference-in-place)
    reg = [c for c in port.calls if c[0] == "data_register"]
    assert len(reg) == 1, port.calls
    assert reg[0][1] == ("/ext/data.store",)
    assert reg[0][2] == {"site": "siteA", "ingest": False}


def test_remote_lane_mint_failure_still_registers(monkeypatch):
    # Best-effort ceiling: the mint failing (site hiccup mid-registration)
    # leaves ref ABSENT from metadata — the registration itself succeeds
    # unchanged (the launch-time lazy mint covers the gap later).
    class _Port:
        def sync_call(self, name, *a, **kw):
            if name == "data_fingerprint":
                return {"entries": [{"path": "a", "bytes": 10, "mtime": 1},
                                    {"path": "b", "bytes": 10, "mtime": 2}],
                        "truncated": False}
            raise RuntimeError("site hiccup")
    import core.compute.adapter as ad
    monkeypatch.setattr(ad, "get_compute", lambda: _Port())
    captured = {}
    import core.graph.entities as ent
    monkeypatch.setattr(ent, "create_entity",
                        lambda **k: captured.update(k) or "ds_new2")
    monkeypatch.setattr(ent, "update_entity", lambda *a, **k: None)
    monkeypatch.setattr(ent, "find_entities", lambda **k: [])
    import content.bio.lifecycle.runs as runsmod
    monkeypatch.setattr(runsmod, "agent_actor_for_thread", lambda tid: "agent")
    monkeypatch.setattr(cur, "_resolve_dataset_path",
                        lambda p, ctx, _remote_out=None: str(p))
    monkeypatch.setattr(cur, "_capture_run_key", lambda *a, **k: None)
    monkeypatch.setattr(cur, "_producing_exec_id", lambda *a, **k: None)
    r = cur.register_dataset_tool({"title": "external tree 2",
                                   "path": "/ext/data2.store", "site": "siteA",
                                   "origin": "collaborator"})
    assert r.get("status") == "ok", r
    md = captured["metadata"]
    assert "ref" not in md, md                     # absent, not ref:None junk
    assert md["home"]["site"] == "siteA"           # the record stands


def test_add_to_remote_home_dataset_refused(monkeypatch):
    def _get(dsid):
        return {"id": dsid, "type": "dataset", "artifact_path": "/groups/x/bundle",
                "metadata": {"home": {"site": "mendel", "path": "/groups/x/bundle"}}}
    import core.graph.entities as ent
    monkeypatch.setattr(ent, "get_entity", _get)
    r = cur.add_to_dataset_tool({"dataset_id": "ds_1", "paths": ["/tmp/new.bin"]})
    assert "error" in r and "mendel" in r["error"]
    assert "not directory-shaped" not in r["error"]        # not the misleading one


def _run():
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    # minimal monkeypatch shim for the script path (pytest supplies the real one)
    class MP:
        def __init__(self): self._undo = []
        def setattr(self, obj, name, val, raising=True):
            if isinstance(obj, str):
                mod, _, attr = obj.rpartition(".")
                import importlib
                obj = importlib.import_module(mod); name = attr
            old = getattr(obj, name, None)
            self._undo.append((obj, name, old))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)
            self._undo.clear()
    import inspect
    fails = 0
    for name, fn in fns:
        params = inspect.signature(fn).parameters
        kw = {}
        mp = MP()
        if "monkeypatch" in params: kw["monkeypatch"] = mp
        if "tmp_path" in params:
            kw["tmp_path"] = Path(tempfile.mkdtemp(prefix="aba_regds_t_"))
        try:
            fn(**kw)
            print(f"  [PASS] {name}")
        except Exception:  # noqa: BLE001
            fails += 1
            print(f"  [FAIL] {name}")
            traceback.print_exc()
        finally:
            mp.undo()
    print(f"\n{'ALL PASS' if not fails else f'FAILED ({fails})'}: "
          f"{len(fns)} tests")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_run())
