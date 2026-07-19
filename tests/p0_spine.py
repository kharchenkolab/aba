"""
P0 spine unit test — exercises the data/exec/catalog interfaces against an
isolated DB. No backend, no model, no live project (DB-safety memory).

Covers:
  - data.store: register -> resolve round-trip + lineage edge; promote scope flip
  - exec.local: LocalSubprocessExecutor.exec success / timeout / cancel;
                materialize base venv + NotImplementedError for conda
  - exec.router: LocalRouter default local + override-requires-approval
  - catalog: register_capability / list_capabilities / resolve_capability / propose

Run:
    .venv/bin/python tests/p0_spine.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Isolate the DB before importing any core module (DB-safety).
_tmp = tempfile.mkdtemp(prefix="aba_p0_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "p0.db")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
from core.graph.entities import create_entity, get_entity    # noqa: E402
from core.graph.provenance import upstream                   # noqa: E402
from core.data import DataHandle, ExecContext, resolve, register, promote  # noqa: E402
from core.exec import (                                       # noqa: E402
    LocalSubprocessExecutor, Provisioning, decide,
)
from core.catalog import (                                    # noqa: E402
    register_capability, list_capabilities, resolve_capability, propose_capability,
)

_failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


class FakeCancelToken:
    """Minimal stand-in for the runtime CancelToken: register(cb)->unregister,
    cancel(reason) fires callbacks + sets .cancelled/.reason."""
    def __init__(self):
        self.cancelled = False
        self.reason = ""
        self._cbs: list = []

    def register(self, cb):
        self._cbs.append(cb)
        def _unregister():
            try:
                self._cbs.remove(cb)
            except ValueError:
                pass
        return _unregister

    def cancel(self, reason="user"):
        self.cancelled = True
        self.reason = reason
        for cb in list(self._cbs):
            cb()


def test_data_store():
    print("data.store: register / resolve / promote")
    # A source dataset with a real file on disk.
    ds_path = Path(_tmp) / "counts.csv"
    ds_path.write_text("gene,a,b\nGAPDH,1,2\n")
    ds_id = create_entity(entity_type="dataset", title="counts.csv",
                          artifact_path=str(ds_path))

    # Register a derived figure (store-by-reference) with lineage to the dataset.
    fig_path = Path(_tmp) / "volcano.png"
    fig_path.write_text("PNGDATA")
    fig_id = register(str(fig_path), kind="figure",
                      lineage={"wasDerivedFrom": [ds_id]}, scope="project",
                      title="volcano")

    # resolve the figure handle -> its path + a version lock.
    staged = resolve(DataHandle(fig_id))
    check("resolve returns the registered path", staged.local_path == str(fig_path),
          f"{staged.local_path} != {fig_path}")
    check("resolve carries a version lock", bool(staged.version_lock))

    # lineage edge exists: figure wasDerivedFrom dataset (upstream of figure).
    up_ids = {n["id"] for n in upstream(fig_id)}
    check("lineage edge recorded (figure -> dataset)", ds_id in up_ids)

    # scope stored on the entity metadata.
    ent = get_entity(fig_id)
    check("scope recorded in metadata", (ent.get("metadata") or {}).get("scope") == "project")

    # promote flips scope, no byte movement.
    promote(fig_id, "institution")
    ent2 = get_entity(fig_id)
    check("promote flips scope to institution",
          (ent2.get("metadata") or {}).get("scope") == "institution")
    check("promote leaves bytes in place", fig_path.read_text() == "PNGDATA")

    # resolve of a pathless entity raises.
    bare = create_entity(entity_type="result", title="bare")
    try:
        resolve(DataHandle(bare))
        check("resolve raises on pathless entity", False, "no exception")
    except ValueError:
        check("resolve raises on pathless entity", True)
    # resolve of unknown entity raises.
    try:
        resolve(DataHandle("ent_does_not_exist"))
        check("resolve raises on unknown entity", False, "no exception")
    except KeyError:
        check("resolve raises on unknown entity", True)


def test_executor():
    print("exec.local: LocalSubprocessExecutor")
    ex = LocalSubprocessExecutor()
    env = ex.materialize(Provisioning())
    check("materialize base venv -> kind venv", env.kind == "venv" and bool(env.python))
    try:
        ex.materialize(Provisioning(conda={"channel": "bioconda", "spec": "salmon=1.10.3"}))
        check("conda materialization raises NotImplementedError", False, "no exception")
    except NotImplementedError:
        check("conda materialization raises NotImplementedError", True)

    cwd = tempfile.mkdtemp(prefix="aba_p0_run_")

    # success
    r = ex.exec(env, [env.python, "-c", "print('hello-spine')"], cwd=cwd, timeout_s=30)
    check("exec success returncode 0", r.returncode == 0, f"rc={r.returncode}")
    check("exec captures stdout", "hello-spine" in r.stdout, repr(r.stdout))
    check("exec not flagged cancelled/timed_out", not r.cancelled and not r.timed_out)

    # cwd is honored: write a file relative to cwd, see it land there.
    r2 = ex.exec(env, [env.python, "-c", "open('made.txt','w').write('x')"], cwd=cwd, timeout_s=30)
    check("exec runs in given cwd", (Path(cwd) / "made.txt").exists() and r2.returncode == 0)

    # timeout
    r3 = ex.exec(env, [env.python, "-c", "import time; time.sleep(10)"], cwd=cwd, timeout_s=1)
    check("exec timeout flagged", r3.timed_out and r3.returncode == -1)

    # cancel: fire cancel mid-run from a timer; exec should return cancelled.
    tok = FakeCancelToken()
    threading.Timer(0.3, lambda: tok.cancel("stop-button")).start()
    t0 = time.time()
    r4 = ex.exec(env, [env.python, "-c", "import time; time.sleep(10)"],
                 cwd=cwd, cancel_token=tok, timeout_s=30)
    elapsed = time.time() - t0
    check("exec cancel flagged", r4.cancelled, f"cancelled={r4.cancelled}")
    check("exec cancel returns promptly (killed, not waited out)", elapsed < 5, f"{elapsed:.1f}s")


def test_router():
    print("exec.router: decide()")
    c = decide(estimate={"ram_gb": 2, "runtime_min": 1})
    check("router defaults to local", c.location == "local" and not c.requires_approval)
    c2 = decide(override="hpc:short")
    check("router records override + requires approval",
          c2.location == "hpc:short" and c2.requires_approval)


def test_catalog():
    print("catalog: register / list / resolve / propose")
    salmon = {
        "name": "salmon", "version": "1.10.3", "archetype": "cli",
        "domain_tags": ["rna-seq", "quantification"],
        "summary": "Transcript-level quantification by selective alignment",
        "provisioning": {"conda": {"channel": "bioconda", "spec": "salmon=1.10.3"}},
        "scope": "institution", "status": "published",
    }
    cid = register_capability(salmon)
    check("register_capability returns id", bool(cid))

    found = list_capabilities(query="salmon")
    check("list_capabilities finds by query", any(c["name"] == "salmon" for c in found))
    tagged = list_capabilities(tags=["rna-seq"])
    check("list_capabilities filters by tag", any(c["name"] == "salmon" for c in tagged))
    miss = list_capabilities(query="iqtree")
    check("list_capabilities misses absent query", all(c["name"] != "salmon" for c in miss))

    by_name = resolve_capability("salmon")
    check("resolve_capability by name", by_name is not None and by_name["version"] == "1.10.3")
    by_id = resolve_capability(cid)
    check("resolve_capability by id", by_id is not None and by_id["name"] == "salmon")

    # scope visibility: institution-scope cap hidden from a project-only ctx.
    ctx = ExecContext(scope_chain=["project:p1"])
    check("scope filter hides out-of-scope capability",
          resolve_capability("salmon", ctx=ctx) is None)
    ctx2 = ExecContext(scope_chain=["project:p1", "institution"])
    check("scope filter shows in-scope capability",
          resolve_capability("salmon", ctx=ctx2) is not None)

    pid = propose_capability({"name": "iqtree", "version": "2.3.6", "archetype": "cli",
                              "domain_tags": ["phylogenetics"]})
    proposed = resolve_capability(pid)
    # Default approval mode is "auto" (P2′): a proposal is published immediately.
    check("propose_capability auto-publishes (auto mode)",
          proposed is not None and proposed["status"] == "published")


def main() -> int:
    init_db()
    test_data_store()
    test_executor()
    test_router()
    test_catalog()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL P0 SPINE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
