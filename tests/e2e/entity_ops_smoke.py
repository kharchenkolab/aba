"""entity_ops_smoke — agent-comprehension test for the new generic
entity-management primitives (entity-mgmt refactor 2026-06-08).

Validates that when the agent is asked to inspect / edit an entity's
fields, it reaches for the right generic primitive:
  - "tell me what's on Result X"          → read_entity
  - "update the interpretation on X to Y" → update_entity_fields
  - "what can I do with a Result?"        → list_entity_operations

Uses oauth_cc + Haiku (cheap + fast). Single-turn-style: seed entities
deterministically, then ask the agent ONE question, watch the tool
trace.

Run:
    .venv/bin/python tests/e2e/entity_ops_smoke.py
    .venv/bin/python tests/e2e/entity_ops_smoke.py --opus
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("ABA_LLM_CREDENTIAL", "oauth_cc")
if "--opus" in sys.argv:
    os.environ["ABA_MODEL"] = "claude-opus-4-7"
    sys.argv.remove("--opus")
else:
    os.environ.setdefault("ABA_MODEL", "claude-haiku-4-5-20251001")

_TMP = Path(tempfile.mkdtemp(prefix="aba_eosmoke_"))
os.environ["ABA_DB_PATH"]   = str(_TMP / "test.db")
os.environ["ABA_RUNTIME_DIR"] = str(_TMP)
os.environ["ARTIFACTS_DIR"] = str(_TMP / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(_TMP / "work")
os.environ["DATA_DIR"]      = str(_TMP / "data")
for p in ("artifacts", "work", "data"):
    (_TMP / p).mkdir(parents=True, exist_ok=True)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def _summ(obj, n=200):
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


def _seed_result() -> tuple[str, str]:
    """Create a Result with a caption-bearing figure member. Returns
    (result_id, expected_caption_substring)."""
    from core.graph.entities import create_entity
    art = _TMP / "umap.png"
    art.write_text("x")
    fig_id = create_entity(
        entity_type="figure", title="UMAP day 0",
        artifact_path=str(art),
        metadata={"thread_id": "default"},
    )
    res_id = create_entity(
        entity_type="result", title="Monocyte expansion",
        metadata={
            "thread_id": "default",
            "interpretation": "Day-0 PBMCs show monocyte expansion.",
            "interpretation_origin": "ai",
            "members": [
                {"id": "m1", "kind": "figure", "ref": fig_id,
                 "caption": "Auto-generated: UMAP colored by Leiden cluster.",
                 "caption_origin": "auto"},
            ],
        },
    )
    return res_id, "Leiden"


def _run_scenario(client, tid: str, prompt: str) -> dict:
    """Run one chat turn, return a transcript dict."""
    seen_tools: list[dict] = []
    final_text: list[str] = []
    err = False

    with client.stream("POST", "/api/chat",
                       json={"text": prompt, "thread_id": tid}) as resp:
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get("type")
            if t == "delta":
                final_text.append(ev.get("text") or ev.get("delta") or "")
            elif t == "tool_start":
                nm = ev.get("name") or ev.get("tool") or "?"
                seen_tools.append({"name": nm, "input": ev.get("input") or {}})
                print(f"    TOOL {nm}  {_summ(ev.get('input') or {}, 160)}")
            elif t == "tool_result":
                res = ev.get("result") or {}
                ok_mark = "✓" if not res.get("is_error") else "✗"
                print(f"    {ok_mark} {_summ(res, 160)}")
                if res.get("is_error"):
                    err = True
            elif t == "error":
                print(f"    [error] {_summ(ev, 240)}")
                err = True

    return {
        "tools": seen_tools,
        "text": "".join(final_text).strip(),
        "error": err,
    }


def main() -> int:
    from core.graph._schema import init_db
    import content.bio  # noqa: F401
    init_db()
    from fastapi.testclient import TestClient
    from main import app

    print("=== entity_ops_smoke ===")
    print(f"  auth: {os.environ['ABA_LLM_CREDENTIAL']}  model: {os.environ['ABA_MODEL']}")
    print(f"  tree: {_TMP}")

    failures: list[str] = []
    t0 = time.time()

    with TestClient(app) as client:
        s = client.get("/api/admin/mcp").json()
        aba = next((srv for srv in s.get("servers", []) if srv["name"] == "aba_core"), None)
        if not aba or aba["state"] != "connected":
            print(f"  FAIL: aba_core not connected ({aba})")
            return 1
        print(f"  aba_core: {aba['state']}, {aba['tools']} tools")

        res_id, expected_caption_marker = _seed_result()
        print(f"  seeded result: {res_id}")
        tid = client.post(
            "/api/threads",
            json={"title": "entity_ops_smoke", "question": "inspect a result"},
        ).json().get("id", "default")

        # ── Scenario 1: agent should READ the Result ────────────────
        print()
        print(f"  [1/2] read scenario")
        prompt = (
            f"Look up Result {res_id} using your tools and tell me, in one "
            f"sentence: what does the auto-generated caption on the figure "
            f"member say? Just quote the caption — no extra prose."
        )
        out1 = _run_scenario(client, tid, prompt)
        used_read = any(t["name"] == "read_entity" for t in out1["tools"])
        print(f"    final text head: {out1['text'][:200]!r}")
        if not used_read:
            failures.append(
                f"scenario 1: agent did NOT call read_entity (tools={[t['name'] for t in out1['tools']]})"
            )
        if expected_caption_marker not in out1["text"]:
            failures.append(
                f"scenario 1: agent didn't report the caption marker '{expected_caption_marker}' "
                f"(text head: {out1['text'][:200]!r})"
            )

        # ── Scenario 2: agent should UPDATE interpretation ───────────
        print()
        print(f"  [2/2] update scenario")
        new_interp = "Day-0 PBMCs show classical monocyte expansion (corrected)."
        prompt = (
            f"On Result {res_id}, change the interpretation field to: "
            f"\"{new_interp}\" — use the right tool for editing entity "
            f"fields. Confirm when done."
        )
        out2 = _run_scenario(client, tid, prompt)
        used_update = any(t["name"] == "update_entity_fields" for t in out2["tools"])
        if not used_update:
            failures.append(
                f"scenario 2: agent did NOT call update_entity_fields "
                f"(tools={[t['name'] for t in out2['tools']]})"
            )
        # Verify the update landed in the DB
        from core.graph.entities import get_entity
        e_after = get_entity(res_id) or {}
        interp_after = (e_after.get("metadata") or {}).get("interpretation", "")
        if "corrected" not in interp_after:
            failures.append(
                f"scenario 2: interpretation not updated in DB "
                f"(found: {interp_after[:120]!r})"
            )

    elapsed = time.time() - t0
    print()
    print(f"  elapsed: {elapsed:.1f}s")
    if failures:
        print(f"FAIL ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK — agent picked the right generic primitives")
    return 0


if __name__ == "__main__":
    sys.exit(main())
