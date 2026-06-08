"""Live agent-comprehension test for view_artifact.

The user friction this targets: agent thrashes on figure revisions
without ever looking at what it produced. Test the loop:

  1. Seed a Result with a figure whose title says one thing but whose
     pixels show another — e.g. title="Bar chart" but content is a sine
     curve.
  2. Ask the agent: "Is this Result figure consistent with its title?
     Look at it and report what you actually see."
  3. Verify: agent calls view_artifact AND its final text mentions the
     ACTUAL content of the image (sine / wave / curve / oscillation),
     NOT just the title.

This proves the model receives the image bytes through tool_result and
can reason from them — i.e. the vision-envelope dispatch path works.

Run:
    .venv/bin/python tests/e2e/view_artifact_smoke.py
    .venv/bin/python tests/e2e/view_artifact_smoke.py --opus
"""
from __future__ import annotations
import json, os, sys, tempfile, time
from pathlib import Path

os.environ.setdefault("ABA_LLM_CREDENTIAL", "oauth_cc")
if "--opus" in sys.argv:
    os.environ["ABA_MODEL"] = "claude-opus-4-7"
    sys.argv.remove("--opus")
else:
    os.environ.setdefault("ABA_MODEL", "claude-haiku-4-5-20251001")

_TMP = Path(tempfile.mkdtemp(prefix="aba_view_smoke_"))
os.environ["ABA_DB_PATH"]     = str(_TMP / "test.db")
os.environ["ABA_RUNTIME_DIR"] = str(_TMP)
os.environ["ARTIFACTS_DIR"]   = str(_TMP / "artifacts")
os.environ["ABA_WORK_DIR"]    = str(_TMP / "work")
os.environ["DATA_DIR"]        = str(_TMP / "data")
for p in ("artifacts", "work", "data"):
    (_TMP / p).mkdir(parents=True, exist_ok=True)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def _summ(obj, n=160):
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


def main() -> int:
    from core.graph._schema import init_db
    import content.bio  # noqa: F401
    init_db()
    from fastapi.testclient import TestClient
    from main import app

    print("=== view_artifact_smoke ===")
    print(f"  auth: {os.environ['ABA_LLM_CREDENTIAL']}  model: {os.environ['ABA_MODEL']}")
    print(f"  tree: {_TMP}")

    failures: list[str] = []

    with TestClient(app) as client:
        s = client.get("/api/admin/mcp").json()
        aba = next((srv for srv in s.get("servers", []) if srv["name"] == "aba_core"), None)
        if not aba or aba["state"] != "connected":
            print(f"  FAIL: aba_core not connected ({aba})")
            return 1
        print(f"  aba_core: {aba['tools']} tools")

        # Seed: title misleading; pixels show a sine curve. The agent
        # has to LOOK to discover the mismatch.
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        fig_path = _TMP / "misleading.png"
        plt.figure(figsize=(6, 3.5))
        plt.plot(np.linspace(0, 4*np.pi, 400), np.sin(np.linspace(0, 4*np.pi, 400)),
                 linewidth=2)
        plt.title("(no title in plot)"); plt.xlabel("x"); plt.ylabel("sin(x)")
        plt.savefig(fig_path, dpi=120, bbox_inches="tight"); plt.close()
        from core.graph.entities import create_entity
        fig_id = create_entity(
            entity_type="figure",
            title="Bar chart of cluster sizes",   # MISLEADING title
            artifact_path=str(fig_path),
            metadata={"thread_id": "thr_view"},
        )
        print(f"  seeded misleading figure: {fig_id} ('{Path(fig_path).name}')")

        tid = client.post("/api/threads",
                          json={"title": "view_artifact_smoke",
                                "question": "verify figure"}).json().get("id", "default")
        # Force the test scenario via a deliberately direct prompt.
        prompt = (
            f"Look at figure {fig_id} — use the right tool to view its "
            f"actual contents, then tell me in one sentence what the image "
            f"actually shows. Do NOT just repeat the entity title; describe "
            f"what's in the rendered pixels."
        )
        seen_tools: list[dict] = []
        final_text: list[str] = []
        err = False
        with client.stream("POST", "/api/chat",
                           json={"text": prompt, "thread_id": tid}) as resp:
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                try: ev = json.loads(line[6:])
                except Exception: continue
                t = ev.get("type")
                if t == "delta":
                    final_text.append(ev.get("text") or ev.get("delta") or "")
                elif t == "tool_start":
                    nm = ev.get("name") or ev.get("tool") or "?"
                    seen_tools.append({"name": nm, "input": ev.get("input") or {}})
                    print(f"    TOOL {nm}  {_summ(ev.get('input') or {}, 120)}")
                elif t == "tool_result":
                    res = ev.get("result") or {}
                    ok = "✓" if not res.get("is_error") else "✗"
                    print(f"    {ok} {_summ(res, 140)}")
                    if res.get("is_error"): err = True
                elif t == "error":
                    print(f"    [error] {_summ(ev, 200)}")
                    err = True

        text = "".join(final_text).strip().lower()
        print(f"  final text: {text[:300]!r}")

        used_view = any(t["name"] == "view_artifact" for t in seen_tools)
        if not used_view:
            failures.append(f"agent did NOT call view_artifact "
                            f"(tools={[t['name'] for t in seen_tools]})")
        # Did the model SEE the image? Test for content keywords
        # describing a sine wave — none of which appear in the title.
        sine_words = ("sine", "sinusoid", "wave", "oscill", "curve", "periodic",
                      "trigonomet", "wavy", "sin")
        bar_words = ("bar", "barplot")
        saw_sine = any(w in text for w in sine_words)
        saw_bar  = any(w in text for w in bar_words)
        if not saw_sine:
            failures.append(f"agent didn't describe pixel content "
                            f"(no sine/wave/curve word in text head: {text[:200]!r})")
        # If the agent ONLY says "bar chart" and DIDN'T mention the actual
        # content, that's a failure — it's just parroting the misleading
        # title rather than looking.
        if saw_bar and not saw_sine:
            failures.append("agent only repeated the misleading title 'bar chart' "
                            "(didn't actually look at the pixels)")
        if err:
            failures.append("dispatcher/tool error")

    if failures:
        print()
        print(f"FAIL ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK — agent used view_artifact + described pixel content")
    return 0


if __name__ == "__main__":
    sys.exit(main())
