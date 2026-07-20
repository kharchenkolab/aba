"""P0 — reusable replay harness for the output-serving / durability work.

Drives the REAL turn flow in-process against an ASGI TestClient — present_plan →
approve/Go → the agent works with NO scripted server-side tool list → plus a
plain follow-up re-run — then lets you assert on both:

  * USER-VISIBLE SURFACES — the Run-card output manifest, the Files-panel durable
    view, and viewer output resolution; and
  * WEFT CATALOG STATE — `retained_runs(label=run_id)` rows, and a poller that
    waits for deferred pins to settle to `done`.

The catalog assert matters because the pin-vs-kept distinction is INVISIBLE on
user surfaces mid-session: a Files panel shows a file as "saving…" whether the
retain selection is the cumulative keeper set or just the last turn's delta, so
a surface-only check sails past the delta data-loss bug (see the P1 work and
memory live-tests-must-replay-real-flow). Only the catalog row's `selection`
tells them apart.

Turns can be driven by the real model (leave ABA_FAKE_SESSION unset, point
ABA_MODEL + creds at a live provider) or by a FakeStream fixture
(ABA_FAKE_SESSION=<jsonl>). The fixture path still executes REAL tools on a REAL
weft kernel, so the turn-end seams, the retain reconciliation, and catalog
settlement are all exercised for real — only token generation is replaced. That
makes it the fast, deterministic wiring guard; the canonical adherence check
(P4) uses the real model.

NO forced kernel restart: a real session keeps one live kernel across turns, and
restarting it mid-replay to force settlement would change the behavior under
test. The harness settles pins only at `settle()` / teardown by STOPPING the
kernel (a legitimate settlement trigger), never by restart-then-continue.

The harness CLEANS UP AFTER ITSELF (`close()` / context-manager exit):
run_forget every label it retained, run_discard + shutdown every kernel it
caused. Jobdir litter from one replay can otherwise pollute the next.

Usage (deterministic wiring guard)::

    from regtest.harness.replay import ReplayHarness, FIXTURES

    with ReplayHarness(fixture=FIXTURES / "replay_reconcile.jsonl") as h:
        h.drive("Please run the two-step export.")   # present_plan → auto-Go
        rid = h.active_run_id()
        h.drive("Also export the final summary.")     # plain follow-up re-run
        sel = h.selection_paths(rid)                   # cumulative keeper set
        assert {"summary_early.csv", "summary_final.csv"} <= sel
        h.settle()                                     # stop kernel → settle pins
        rows = h.wait_for_state(rid, "done")
        kept = h.kept_files(rid)
        assert {"summary_early.csv", "summary_final.csv"} <= kept

The harness is model-agnostic and content-neutral: it hard-codes nothing about
any analysis domain. Fixtures supply the (generic) work.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def _isolate_runtime(name: str) -> Path:
    """Point the process at a FRESH runtime dir (DB / projects / artifacts /
    work) so a replay can't write into the operator's live runtime, and pop the
    per-dir overrides a sourced .env may have set. Crucially we DO NOT touch
    ABA_HOME: the weft workspace + its realized envs resolve from ABA_HOME
    (adapter.weft_workspace → aba_home()/weft), so leaving it alone lets the
    replay reuse the already-realized local env instead of realizing one from
    scratch (minutes + network). Kernels land in the shared weft workspace —
    hence the run_discard/shutdown teardown."""
    rt = Path(tempfile.mkdtemp(prefix=f"aba_replay_{name}_"))
    os.environ["ABA_RUNTIME_DIR"] = str(rt)
    os.environ["ABA_DB_PATH"] = str(rt / "replay.db")
    for k in ("ABA_PROJECTS_DIR", "DATA_DIR", "ARTIFACTS_DIR", "ABA_WORK_DIR",
              "ABA_REFS_DIR"):
        os.environ.pop(k, None)
    # (The weft kernel transport is the only one since the cutover — no
    # ABA_WEFT_KERNELS opt-in needed; every kernel records a weft target.)
    backend = str(ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    return rt


class SubstrateUnavailable(RuntimeError):
    """The weft substrate isn't configured / has no realized local env — a
    real-tool replay can't run. Callers (tests) skip cleanly."""


def realized_local_python_env() -> Optional[str]:
    """Id of a realized local weft python env (has `.weft-ready` + a built
    interpreter), or None. Mirrors tests/test_weft_retention.py."""
    from core.compute import adapter as admod
    envs = admod.weft_workspace() / "site-local" / "envs"
    if not envs.exists():
        return None
    for d in sorted(envs.iterdir()):
        if (d / ".weft-ready").exists() and (
                d / ".pixi" / "envs" / "default" / "bin" / "python").exists():
            return d.name
    return None


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
class ReplayHarness:
    """One replay session against an in-process TestClient. See module docstring."""

    def __init__(self, *, name: str = "replay", fixture: Optional[Path] = None,
                 data: Optional[list] = None, require_substrate: bool = True,
                 turn_timeout_s: float = 600.0):
        self.name = name
        self.turn_timeout_s = turn_timeout_s
        self._labels: set[str] = set()      # run ids we retained under → run_forget
        self._targets: set[str] = set()     # weft kernel/job ids we caused → discard
        self._closed = False

        if fixture is not None:
            os.environ["ABA_FAKE_SESSION"] = str(fixture)
        self._runtime = _isolate_runtime(name)

        # Import AFTER env is set so module-level captures (FAKE_SESSION, the
        # fake open_stream, config dirs) see the replay's runtime.
        import content.bio  # noqa: F401 — pack registration side effect
        import content.bio.lifecycle.registry  # noqa: F401
        from core.graph._schema import init_db
        init_db()

        # Configure the compute substrate (main.py does this at startup).
        from core.compute import adapter as admod
        st = admod.configure()
        if require_substrate and not st.get("ok"):
            raise SubstrateUnavailable(f"weft not configured: {st.get('detail')}")
        if require_substrate and not realized_local_python_env():
            raise SubstrateUnavailable("no realized local python env")

        from fastapi.testclient import TestClient
        from main import app
        self._cm = TestClient(app)
        self.client = self._cm.__enter__()
        self.pid = self.client.post("/api/projects",
                                    json={"name": self.name}).json().get("id", "single")
        self.client.post(f"/api/projects/{self.pid}/open")
        self.tid = self.client.post(
            "/api/threads", json={"project_id": self.pid, "title": self.name}
        ).json().get("id")
        if data:
            self._stage(data)

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "ReplayHarness":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- staging -----------------------------------------------------------
    def _stage(self, items: list) -> None:
        """Copy files/dirs into the project's data dir + the global fallback."""
        import shutil
        from core.config import project_data_dir, DATA_DIR as GLOBAL_DATA
        dests = [Path(project_data_dir(self.pid)), Path(str(GLOBAL_DATA))]
        for d in dests:
            d.mkdir(parents=True, exist_ok=True)
        for f in items:
            f = Path(f)
            for d in dests:
                if f.is_dir():
                    shutil.copytree(f, d / f.name, dirs_exist_ok=True)
                elif f.is_file():
                    shutil.copy(f, d / f.name)

    # -- driving turns -----------------------------------------------------
    def drive(self, text: str, *, resume: str = "Yes, go ahead.") -> dict:
        """Drive one turn end-to-end: POST /api/chat, consume the SSE stream,
        and while the turn is `awaiting_user` (a present_plan halt), POST
        /resume with `resume` — the real Go/approve loop, no scripted tools.
        Returns a capture dict {run_id, text, tools, entities, errors,
        tool_errors, resume_hops}. After it returns, records the turn's weft
        target(s) for teardown."""
        cap = self._drive_once(text, resume)
        self._record_targets_from_run()
        return cap

    def _drive_once(self, text: str, resume: str) -> dict:
        cap: dict = {"run_id": None, "text": [], "tools": [], "entities": [],
                     "errors": [], "tool_errors": [], "resume_hops": 0}
        deadline = time.time() + self.turn_timeout_s
        with self.client.stream("POST", "/api/chat", timeout=self.turn_timeout_s,
                                json={"text": text, "project_id": self.pid,
                                      "thread_id": self.tid}) as r:
            self._consume(r, cap)
        for _ in range(8):
            rid = cap["run_id"]
            if not rid or time.time() > deadline:
                break
            try:
                st = self.client.get(f"/api/turns/{rid}").json().get("state")
            except Exception:
                break
            if st != "awaiting_user":
                break
            cap["resume_hops"] += 1
            with self.client.stream("POST", f"/api/turns/{rid}/resume",
                                    timeout=self.turn_timeout_s,
                                    json={"user_text": resume}) as r2:
                self._consume(r2, cap)
        cap["text"] = "".join(cap["text"]).strip()
        return cap

    @staticmethod
    def _consume(stream, cap: dict) -> None:
        for line in stream.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get("type")
            if ev.get("run_id"):
                cap["run_id"] = ev["run_id"]
            if t == "delta":
                cap["text"].append(ev.get("text") or ev.get("delta") or "")
            elif t == "tool_start":
                cap["tools"].append(ev.get("name") or ev.get("tool") or "?")
            elif t == "tool_result":
                r = ev.get("result") or {}
                if isinstance(r, dict) and r.get("returncode") not in (None, 0):
                    cap["tool_errors"].append(
                        {"tool": ev.get("name"), "rc": r.get("returncode"),
                         "stderr": str(r.get("stderr", ""))[:400]})
            elif t == "entity_registered":
                cap["entities"].append(ev.get("entity") or {})
            elif t in ("error", "cancelled"):
                cap["errors"].append(str(ev)[:400])

    # -- run identity ------------------------------------------------------
    def active_run_id(self) -> Optional[str]:
        from content.bio.lifecycle.runs import active_run_id
        return active_run_id(self.tid)

    def run_meta(self, run_id: str) -> dict:
        from core.graph.entities import get_entity
        return (get_entity(run_id) or {}).get("metadata") or {}

    def targets(self, run_id: str) -> list[str]:
        return list(self.run_meta(run_id).get("weft_targets") or [])

    def _record_targets_from_run(self) -> None:
        rid = self.active_run_id()
        if not rid:
            return
        self._labels.add(rid)
        for t in self.targets(rid):
            self._targets.add(t)

    # -- USER SURFACES -----------------------------------------------------
    def run_card_outputs(self, run_id: str) -> list[dict]:
        """The Run-card output manifest (`metadata.run.outputs`) — the preview
        strip the Run card renders. P2 sources this from harvest; today it
        scans the scratch dir. Returns the list of output entries (possibly [])."""
        return list((self.run_meta(run_id).get("run") or {}).get("outputs") or [])

    def durable_view(self, run_id: str) -> dict:
        """The Files-panel durable view — each produced file with its weft-truth
        durability state (retained / saving / at-risk / …)."""
        from content.bio.lifecycle.runs import run_durable_view
        return run_durable_view(run_id)

    def resolve_output(self, run_id: str, name: str) -> Optional[str]:
        """Viewer resolution: absolute local path for a Run output (file OR
        directory store), across the retained tree / live jobdir / sandbox."""
        from content.bio.lifecycle.runs import resolve_run_output_path
        return resolve_run_output_path(run_id, name)

    # -- WEFT CATALOG ------------------------------------------------------
    def retained_rows(self, run_id: str) -> list[dict]:
        from core.compute import retention
        return retention.retained(label=run_id) or []

    def selection_paths(self, run_id: str) -> set[str]:
        """The union of literal include paths across every retain row for this
        Run (pinned-pending or done). This is the CUMULATIVE keeper set the
        retain currently carries — the surface the delta data-loss bug shows up
        on (a delta retain leaves only the last turn's paths here)."""
        out: set[str] = set()
        for row in self.retained_rows(run_id):
            try:
                sel = json.loads(row.get("selection") or "{}")
            except Exception:
                sel = {}
            for g in (sel.get("include") or []):
                if not any(c in g for c in "*?["):
                    out.add(g)
        return out

    def kept_files(self, run_id: str) -> set[str]:
        """Relpaths physically present in a `done` retained tree (from the
        `.weft-run.json` sidecar, else a walk). Empty until settlement."""
        from content.bio.lifecycle.runs import _sidecar_files
        from core.compute import retention
        out: set[str] = set()
        for row in self.retained_rows(run_id):
            if row.get("state") != "done":
                continue
            out |= _sidecar_files(retention.location_path(row))
        return out

    def wait_for_state(self, run_id: str, state: str, *, tries: int = 60,
                       delay: float = 0.5) -> list[dict]:
        """Poll until at least one retained row for this Run reaches `state`
        (settlement is async via weft's stop hook / poller). Returns the last
        seen rows."""
        rows: list[dict] = []
        for _ in range(tries):
            rows = self.retained_rows(run_id)
            if any(r.get("state") == state for r in rows):
                return rows
            time.sleep(delay)
        return rows

    # -- settlement --------------------------------------------------------
    def settle(self) -> None:
        """Stop every kernel this replay started so weft settles the deferred
        pins (capturing each file's eventual version into the retained tree).
        This is NOT a forced restart — the session is over; we stop, we don't
        stop-then-continue."""
        try:
            from core.exec.kernels import get_pool
            get_pool().shutdown_all()
        except Exception:
            pass

    # -- teardown ----------------------------------------------------------
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # 1) stop kernels (also settles anything still pending)
        self.settle()
        # 2) discard sandboxes for targets we caused
        from core.compute import retention
        for t in list(self._targets):
            try:
                retention.discard(t)
            except Exception:
                pass
        # 3) forget retained bytes for every label we retained under
        for label in list(self._labels):
            try:
                retention.forget(label=label)
            except Exception:
                pass
        # 4) close the client
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Standalone smoke: drive the reconcile fixture, print surfaces + catalog.
# ---------------------------------------------------------------------------
def _smoke() -> int:
    fx = FIXTURES / "replay_reconcile.jsonl"
    if not fx.exists():
        print(f"missing fixture {fx}")
        return 2
    try:
        h = ReplayHarness(name="smoke", fixture=fx)
    except SubstrateUnavailable as e:
        print(f"SKIP: {e}")
        return 0
    try:
        h.drive("Please run the two-step export.")
        rid = h.active_run_id()
        print(f"run={rid} targets={h.targets(rid)}")
        h.drive("Also export the final summary.")
        print(f"selection (cumulative keeper set): {sorted(h.selection_paths(rid))}")
        print(f"run-card outputs: {len(h.run_card_outputs(rid))} entries")
        dv = h.durable_view(rid)
        print(f"durable summary: {dv.get('summary')}")
        h.settle()
        h.wait_for_state(rid, "done")
        print(f"kept after settlement: {sorted(h.kept_files(rid))}")
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    raise SystemExit(_smoke())
