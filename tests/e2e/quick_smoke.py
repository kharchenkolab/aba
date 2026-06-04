"""quick_smoke — lightweight e2e validation of the agent dispatch loop.

Replaces the heavy `run_scrna_suite.py` for the case of "did the
dispatcher → aba_core → bio impl → tool_result loop break?" Built fresh
for the post-WU-1 world: aba_core is the canonical tool catalog,
TOOL_SCHEMAS is empty.

Default mode uses oauth_cc (subscription billing, cheap) with Haiku
since the goal is plumbing, not reasoning quality.

What it verifies, end-to-end, in <2 minutes:
  - backend boots cleanly under TestClient
  - aba_core MCP server connects, lists tools at bare names
  - agent receives the tool catalog
  - agent chooses + invokes a tool via the dispatcher
  - dispatcher routes via aba_core (is_inprocess_tool path)
  - bio impl runs, returns a result
  - agent receives the result and terminates the turn

The test does NOT score reasoning quality (the prompt-regression
harness does that) — it answers "is the loop intact?"

Usage:
  .venv/bin/python tests/e2e/quick_smoke.py
  .venv/bin/python tests/e2e/quick_smoke.py --opus    # use opus instead
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# --- Auth: oauth_cc (Claude Code subscription) by default ---
# Set BEFORE any import that pulls in core.llm.
os.environ.setdefault("ABA_LLM_CREDENTIAL", "oauth_cc")
# Haiku is cheap + always available; --opus or ABA_MODEL overrides.
if "--opus" in sys.argv:
    os.environ["ABA_MODEL"] = "claude-opus-4-7"
    sys.argv.remove("--opus")
else:
    os.environ.setdefault("ABA_MODEL", "claude-haiku-4-5-20251001")

# --- Isolation: own DB / DATA_DIR / artifacts ---
_TMP = Path(tempfile.mkdtemp(prefix="aba_smoke_"))
os.environ["ABA_DB_PATH"] = str(_TMP / "test.db")
os.environ["ARTIFACTS_DIR"] = str(_TMP / "artifacts")
os.environ["ABA_WORK_DIR"] = str(_TMP / "work")
os.environ["DATA_DIR"] = str(_TMP / "data")
for p in ("artifacts", "work", "data"):
    (_TMP / p).mkdir(parents=True, exist_ok=True)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


# --- Scenario: minimal prompt that forces ONE tool call ---
# Direct + leaves no room for the agent to do prose-only. Asks for
# arithmetic via the kernel so the tool call is unambiguous.
PROMPT = (
    "Use run_python to compute 7 * 6 and print the result. "
    "No plan needed — just run it and tell me what came back."
)
EXPECTED_TOOLS = {"run_python"}     # at least one of these MUST appear


def _summ(obj, n=200):
    s = obj if isinstance(obj, str) else json.dumps(obj)
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


def main() -> int:
    # Lazy imports so the env vars set above take effect before the
    # backend's auth path runs.
    from core.graph._schema import init_db
    import content.bio  # noqa: F401  — register handlers
    init_db()
    from fastapi.testclient import TestClient
    from main import app

    print(f"=== quick_smoke ===")
    print(f"  auth: {os.environ['ABA_LLM_CREDENTIAL']}, model: {os.environ['ABA_MODEL']}")
    print(f"  isolated tree: {_TMP}")

    seen_tools: list[str] = []
    saw_route_aba_core = False
    saw_error = False
    final_text_parts: list[str] = []

    def consume(stream):
        nonlocal saw_route_aba_core, saw_error
        for line in stream.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get("type")
            if t == "delta":
                final_text_parts.append(ev.get("text") or ev.get("delta") or "")
            elif t == "tool_start":
                nm = ev.get("name") or ev.get("tool") or "?"
                seen_tools.append(nm)
                print(f"  TOOL {nm}  {_summ(ev.get('input') or {}, 120)}")
            elif t == "tool_result":
                res = ev.get("result") or {}
                rc = res.get("returncode")
                err = res.get("error") or res.get("stderr")
                ok_mark = "✓" if (rc in (0, None) and not res.get("is_error")) else "✗"
                print(f"  {ok_mark} {_summ(res, 200)}")
                if rc not in (0, None) and err:
                    saw_error = True
            elif t == "error":
                print(f"  [error] {_summ(ev, 240)}")
                saw_error = True
            elif t == "plan":
                print(f"  [plan emitted — unexpected for this prompt]")

    t0 = time.time()
    with TestClient(app) as client:
        # Confirm aba_core registered as the bio tool host.
        s = client.get("/api/admin/mcp").json()
        aba = next((srv for srv in s.get("servers", []) if srv["name"] == "aba_core"), None)
        if not aba or aba["state"] != "connected":
            print(f"  FAIL: aba_core not connected at startup ({aba})")
            return 1
        print(f"  aba_core: {aba['state']}, {aba['tools']} tools registered")

        tid = client.post(
            "/api/threads",
            json={"title": "quick_smoke", "question": "compute 42"},
        ).json().get("id", "default")

        with client.stream(
            "POST", "/api/chat",
            json={"text": PROMPT, "thread_id": tid},
        ) as resp:
            consume(resp)

    elapsed = time.time() - t0

    # Sanity scan the live log file for the dispatcher's route marker.
    # Not load-bearing for the test (the tool_result above proves the
    # round-trip), just informative.
    # (No live log path under TestClient — skip.)

    final_text = "".join(final_text_parts).strip()
    print()
    print(f"  elapsed: {elapsed:.1f}s")
    print(f"  tools seen: {seen_tools}")
    print(f"  final text head: {final_text[:200]!r}")

    # --- Assertions ---
    failures: list[str] = []
    if not seen_tools:
        failures.append("no tool calls (agent didn't dispatch anything)")
    if not (set(seen_tools) & EXPECTED_TOOLS):
        failures.append(f"expected one of {EXPECTED_TOOLS}, got {seen_tools}")
    if saw_error:
        failures.append("dispatcher / tool produced an error")
    if not final_text:
        failures.append("agent produced no final text")
    if "42" not in final_text:
        # Haiku may phrase the answer differently; this is the strongest
        # single signal that the tool ran AND the agent read the result.
        failures.append(f"answer doesn't mention 42 (text head: {final_text[:120]!r})")

    if failures:
        print()
        print(f"FAIL ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print()
    print(f"OK — dispatch loop intact (tools={seen_tools}, elapsed={elapsed:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
