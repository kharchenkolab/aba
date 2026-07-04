"""Format a placement-study results.json into a readable per-scenario report:
the injected compute cue, the agent's plan/reasoning, the captured run_python/run_r
placement decision(s), and the router's resulting location. Verdicts are added by a
human/LLM reviewer (this only extracts + lays out the signal).

  python regtest/placement/analyze.py [path/to/results.json]
"""
import glob
import json
import sys


def latest():
    fs = sorted(glob.glob("/tmp/aba_placement_study/run-*/results.json"))
    return fs[-1] if fs else None


def fmt_decision(d):
    i = d["input"]
    args = ", ".join(f"{k}={i[k]}" for k in i if i[k] is not None) or "(no est_* / background args)"
    return f"t{d['turn']}: run [{args}] -> router={d['router']['location']} ({d['router']['rationale']})"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else latest()
    if not path:
        print("no results.json found"); return 1
    data = json.load(open(path))
    print(f"# Resource-placement study — {path}\n")
    for r in data:
        print(f"## {r['name']}")
        print(f"- **compute cue**: {r['context_line']}")
        print(f"- **expected**: {r['expected']}")
        print(f"- **turn1 tools**: {r['turn1_tools']}")
        print(f"- **turn2 tools**: {r['turn2_tools']}")
        if r["decisions"]:
            for d in r["decisions"]:
                print(f"- **DECISION** — {fmt_decision(d)}")
        else:
            print("- **DECISION** — none (stayed in text/plan; see replies)")
        if r.get("plan"):
            plan = r["plan"]
            steps = plan.get("steps") if isinstance(plan, dict) else None
            print(f"- **plan**: {json.dumps(steps)[:600] if steps else json.dumps(plan)[:600]}")
        print(f"- **turn1 reply**: {r['turn1_reply'][:700]}")
        print(f"- **turn2 reply**: {r['turn2_reply'][:700]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
