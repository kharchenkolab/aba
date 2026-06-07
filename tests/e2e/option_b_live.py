"""Live-agent end-to-end test for the Option B lazy-materialization + figure
versioning features (misc/exec_records_and_versioning.md).

Exercises the live Guide loop (Haiku via oauth_cc by default) over four
scenarios:

  1. CREATE: agent makes a simple plot → ZERO shadow entities + the
     artifact is reachable via /api/exec_records/{id}/artifacts.
  2. PIN-FROM-CHAT: POST /api/artifacts/{exec_id}/figure/0/pin
     materializes the figure entity with the right pointers.
  3. AGENT-REVISE: with the figure focused, ask the agent to make a
     revision; verify it calls `make_revision` and a wasRevisionOf
     edge appears.
  4. AGENT-REPRODUCE: ask the agent to reproduce the figure; verify
     it calls `reproduce_from_exec` and reports drift status.

Runs against the in-process FastAPI app via TestClient (no separate
backend bounce required). Bills the Claude Code subscription via
oauth_cc — Haiku is the default for cost.

Usage:
  .venv/bin/python tests/e2e/option_b_live.py
  .venv/bin/python tests/e2e/option_b_live.py --opus     # opus for stricter agent
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# --- Auth: oauth_cc + Haiku unless overridden ---
os.environ.setdefault("ABA_LLM_CREDENTIAL", "oauth_cc")
if "--opus" in sys.argv:
    os.environ["ABA_MODEL"] = "claude-opus-4-7"
    sys.argv.remove("--opus")
else:
    os.environ.setdefault("ABA_MODEL", "claude-haiku-4-5-20251001")

# --- Isolation ---
_TMP = Path(tempfile.mkdtemp(prefix="aba_optB_live_"))
os.environ["ABA_DB_PATH"]   = str(_TMP / "test.db")
os.environ["ABA_RUNTIME_DIR"] = str(_TMP)
os.environ["ARTIFACTS_DIR"] = str(_TMP / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(_TMP / "work")
os.environ["DATA_DIR"]      = str(_TMP / "data")
# Live envs overlay so the kernel has matplotlib etc.
os.environ.setdefault("ABA_ENVS_DIR", "/workspace/aba-runtime/envs")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


# ── Output helpers ───────────────────────────────────────────────────────────


_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _summ(obj, n=160):
    s = obj if isinstance(obj, str) else json.dumps(obj)
    return s if len(s) <= n else s[:n] + "..."


# ── SSE consumer that records what the agent did ─────────────────────────────


class TurnObserver:
    """Drains the /api/chat SSE stream and accumulates everything the
    agent did during one turn (tools called, results, final text)."""

    def __init__(self, label: str):
        self.label = label
        self.tools_seen: list[str] = []
        self.tool_results: list[dict] = []
        self.final_text: list[str] = []
        self.errors: list[str] = []

    def consume(self, stream) -> None:
        for line in stream.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:  # noqa: BLE001
                continue
            t = ev.get("type")
            if t == "delta":
                self.final_text.append(ev.get("text") or ev.get("delta") or "")
            elif t == "tool_start":
                nm = ev.get("name") or ev.get("tool") or "?"
                self.tools_seen.append(nm)
                print(f"  [{self.label}] TOOL {nm}  {_summ(ev.get('input') or {}, 110)}")
            elif t == "tool_result":
                res = ev.get("result") or {}
                self.tool_results.append({"name": ev.get("name"), "result": res})
                rc = res.get("returncode")
                ok = "✓" if (rc in (0, None) and not res.get("is_error")) else "✗"
                print(f"  [{self.label}] {ok} result  {_summ(res, 180)}")
            elif t == "error":
                self.errors.append(_summ(ev))
                print(f"  [{self.label}] ERROR {_summ(ev, 200)}")

    @property
    def text(self) -> str:
        return "".join(self.final_text).strip()


# ── Scenarios ────────────────────────────────────────────────────────────────


SCENARIO_1_PROMPT = (
    "Use run_python to make a simple scatter plot: x = [1,2,3,4,5], "
    "y = [4,1,3,5,2]. Save the figure to 'scatter.png' with savefig. "
    "After running, briefly say what you plotted. No plan needed."
)

SCENARIO_3_PROMPT = (
    "Please make a revision of the focused figure with y = [40,10,30,50,20] "
    "(values multiplied by 10). I want to keep the original; this is a variant."
)

SCENARIO_4_PROMPT = (
    "Please reproduce the focused figure by re-running its exec. Report "
    "whether the env drifted from the original run."
)


def run_scenario(client, scenario_id: str, text: str,
                 thread_id: str, focus_entity_id: str | None = None) -> TurnObserver:
    """Send `text` to the chat and observe one turn end-to-end."""
    print(f"\n=== Scenario {scenario_id} ==========================================")
    print(f"  user: {text}")
    payload: dict = {"text": text, "thread_id": thread_id}
    if focus_entity_id:
        payload["focus_entity_id"] = focus_entity_id
    obs = TurnObserver(scenario_id)
    t0 = time.time()
    with client.stream("POST", "/api/chat", json=payload) as resp:
        obs.consume(resp)
    print(f"  [{scenario_id}] elapsed: {time.time() - t0:.1f}s  final: {_summ(obs.text, 140)!r}")
    return obs


def main() -> int:
    # Lazy imports — env must be set first.
    from core.graph._schema import init_db, _conn
    import content.bio  # noqa: F401 — registers hooks
    init_db()
    from fastapi.testclient import TestClient
    from main import app

    print("=== option_b_live ===")
    print(f"  auth: {os.environ['ABA_LLM_CREDENTIAL']}, model: {os.environ['ABA_MODEL']}")
    print(f"  isolated tree: {_TMP}")

    with TestClient(app) as client:
        # Sanity check: aba_core connected
        info = client.get("/api/admin/mcp").json()
        aba = next((s for s in info.get("servers", []) if s["name"] == "aba_core"), None)
        check("aba_core MCP server connected",
              aba and aba.get("state") == "connected",
              f"got {aba}")
        if not aba or aba.get("state") != "connected":
            return 1

        # No public catalog endpoint to query from outside; we verify
        # the new tools are reachable by observing the agent actually
        # call them in scenarios S3 / S4 below. aba_core's connected
        # state above already confirms registration completed.

        # Track entity counts so we can verify ZERO new figure rows post-cutover
        def _figure_count() -> int:
            with _conn() as c:
                return c.execute("SELECT COUNT(*) AS n FROM entities WHERE type='figure'").fetchone()["n"]
        fig_count_baseline = _figure_count()

        # Make a fresh thread
        tid = client.post("/api/threads", json={
            "title": "option_b live test", "question": "exercise pin + revision flow",
        }).json().get("id", "default")
        print(f"  thread: {tid}")

        # ── Scenario 1: lazy harvest ────────────────────────────────────
        obs1 = run_scenario(client, "S1", SCENARIO_1_PROMPT, tid)
        check("S1: agent called run_python",
              "run_python" in obs1.tools_seen,
              f"got tools={obs1.tools_seen}")
        check("S1: no errors during turn", not obs1.errors)
        # Find the exec_id from the run_python result
        rp_results = [r for r in obs1.tool_results
                       if r["name"] == "run_python"
                       and isinstance(r["result"], dict)
                       and r["result"].get("exec_id")]
        check("S1: run_python result carries exec_id",
              len(rp_results) >= 1)
        if not rp_results:
            return 1
        exec_id = rp_results[-1]["result"]["exec_id"]
        plots = rp_results[-1]["result"].get("plots") or []
        check("S1: at least 1 plot harvested",
              len(plots) >= 1, f"plots: {plots}")
        # The core lazy-materialization assertion: no new figure entities
        # were minted by the harvest.
        check("S1: ZERO new figure entities (lazy materialization)",
              _figure_count() == fig_count_baseline,
              f"baseline={fig_count_baseline}, now={_figure_count()}")
        # And the artifact IS reachable via the artifact resolver
        arts = client.get(f"/api/exec_records/{exec_id}/artifacts").json().get("artifacts", [])
        fig_arts = [a for a in arts if a["kind"] == "figure"]
        check("S1: figure artifact reachable via /api/exec_records/{id}/artifacts",
              len(fig_arts) >= 1, f"got: {arts}")

        # ── Scenario 2: pin via the artifact endpoint ─────────────────
        print(f"\n=== Scenario S2 (HTTP pin via /api/artifacts/.../pin) ===")
        r = client.post(f"/api/artifacts/{exec_id}/figure/0/pin",
                        json={"title": "S2 pinned figure", "wrap_in_result": False})
        check("S2: pin endpoint 200", r.status_code == 200,
              f"got {r.status_code}: {r.text[:200]}")
        if r.status_code != 200:
            return 1
        body = r.json()
        pinned_entity_id = body.get("entity_id")
        check("S2: entity_id returned", isinstance(pinned_entity_id, str))
        check("S2: was_new = True (first pin)", body.get("was_new") is True)
        check("S2: 1 new figure entity materialized",
              _figure_count() == fig_count_baseline + 1,
              f"baseline={fig_count_baseline}, now={_figure_count()}")
        # Verify the entity carries the right pointers
        ent = body.get("entity") or {}
        check("S2: entity.exec_id matches", ent.get("exec_id") == exec_id)
        check("S2: entity.artifact_kind = figure",
              ent.get("artifact_kind") == "figure")
        check("S2: entity.artifact_idx = 0", ent.get("artifact_idx") == 0)

        # Idempotency
        r2 = client.post(f"/api/artifacts/{exec_id}/figure/0/pin",
                        json={"title": "S2 repin", "wrap_in_result": False})
        check("S2: repin idempotent (was_new = False)",
              r2.json().get("was_new") is False)
        check("S2: repin keeps same entity_id",
              r2.json().get("entity_id") == pinned_entity_id)

        # ── Scenario 3: agent-driven make_revision ────────────────────
        # Pre-condition: count revisions before
        before_revs = client.get(f"/api/entities/{pinned_entity_id}/revisions").json()
        check("S3: pre-revision chain has 1 entry",
              len(before_revs.get("chain", [])) == 1)

        obs3 = run_scenario(client, "S3", SCENARIO_3_PROMPT, tid,
                            focus_entity_id=pinned_entity_id)
        check("S3: agent called make_revision",
              "make_revision" in obs3.tools_seen,
              f"got tools={obs3.tools_seen}")
        # Verify revision chain grew
        after_revs = client.get(f"/api/entities/{pinned_entity_id}/revisions").json()
        chain_after = after_revs.get("chain", [])
        check("S3: revision chain now has 2 entries",
              len(chain_after) == 2,
              f"got chain length={len(chain_after)}")
        # The new entity has wasRevisionOf pointing at the original
        if len(chain_after) >= 2:
            newest = chain_after[0]
            check("S3: new revision is a different entity_id",
                  newest["id"] != pinned_entity_id,
                  f"got {newest['id']}")
            check("S3: newest entity has exec_id (its own exec)",
                  bool(newest.get("exec_id")))

        # ── Scenario 4: agent-driven reproduce_from_exec ──────────────
        obs4 = run_scenario(client, "S4", SCENARIO_4_PROMPT, tid,
                            focus_entity_id=pinned_entity_id)
        check("S4: agent called reproduce_from_exec",
              "reproduce_from_exec" in obs4.tools_seen,
              f"got tools={obs4.tools_seen}")
        rep_results = [r for r in obs4.tool_results
                        if r["name"] == "reproduce_from_exec"]
        if rep_results:
            res = rep_results[-1]["result"]
            check("S4: reproduce_from_exec reported reproduced=True",
                  res.get("reproduced") is True,
                  f"got {res}")
            check("S4: env_drift = False (same kernel)",
                  res.get("env_drift") is False)

    # ── Summary ───────────────────────────────────────────────────────────
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL OPTION-B-LIVE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
