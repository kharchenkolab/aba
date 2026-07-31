"""Format a placement-study results.json into a readable per-scenario report:
the injected compute cue, the agent's plan/reasoning, the captured run_python/run_r
placement decision(s), and the router's resulting location. Verdicts are added by a
human/LLM reviewer (this only extracts + lays out the signal).

  python regtest/placement/analyze.py [path/to/results.json]
"""
import glob
import json
import os
import sys


def latest():
    # Same base as study.py (ABA_PLACEMENT_STUDY_DIR) — one env var, no path coupling.
    base = os.environ.get("ABA_PLACEMENT_STUDY_DIR",
                          f"{os.environ.get('TMPDIR', '/tmp')}/aba_placement_study")
    fs = sorted(glob.glob(f"{base}/run-*/results.json"))
    return fs[-1] if fs else None


def fmt_decision(d):
    i = d["input"]
    args = ", ".join(f"{k}={i[k]}" for k in i if i[k] is not None) or "(no est_* / background args)"
    return f"t{d['turn']}: run [{args}] -> router={d['router']['location']} ({d['router']['rationale']})"


# ── signals view (--signals) ──────────────────────────────────────────────────
# The default view prints the agent's replies, which is what a human reviewer
# reads. `--signals` prints DERIVED FLAGS only — no reply text — so the same run
# can be triaged where quoting a transcript is not wanted, and so a contrast pair
# (e.g. short-clock vs long-clock, same job) can be compared at a glance instead
# of by re-reading two walls of prose.
#
# These are lexical probes, not judgements: they say a topic was RAISED, never
# that the reasoning was right. A reviewer still decides. Their value is the
# contrast — `informs_time` true in both halves of a pair means the agent says it
# every time and the clock is not what moved it.
import re

_PROBES = {
    "informs_time":   r"walltime|time limit|time left|hours? (?:remaining|left)|"
                      r"minutes? (?:remaining|left)|session (?:is |will )?(?:too short|end|expir)|"
                      r"run out of time|not enough time|before the session",
    # Split deliberately: on a bounded session these are NOT interchangeable.
    # A CLUSTER job outlives the session; a plain "background" job on the local
    # lane runs on the session's own node and dies with it. An agent offering
    # the second as protection against the clock is giving advice that loses the
    # work — invisible if both collapse into one "offers to defer" flag.
    "offers_cluster": r"\bslurm\b|\bsbatch\b|\bcluster\b|compute node|"
                      r"submit(?:ting)? (?:it |this )?(?:as |to )(?:a )?(?:job|batch)",
    "offers_bg":      r"\bbackground\b|queue it|detached",
    "offers_relaunch": r"relaunch|restart the session|new session|longer (?:time |wall)?limit|"
                       r"larger time limit|request more time",
    "offers_smaller": r"fewer iterations|reduce the (?:number|iteration)|subsample|"
                      r"smaller (?:subset|run)|cut (?:it |the )?down",
}


def signals(data):
    print(f"{'scenario':<34} {'wt':>5}  {'exec':<22} {'router':<10} "
          f"{'time':<5} {'clust':<5} {'bg':<5} {'relau':<5} {'small':<5} {'asks':<4}")
    print("-" * 110)
    for r in data:
        m = re.search(r"~([\d.]+)h walltime left", r.get("context_line") or "")
        wt = f"{float(m.group(1)):g}h" if m else "-"
        # ARMED: if the cue carried no clock, this row cannot test the clock.
        if not m:
            wt = "NONE"
        ds = r.get("decisions") or []
        if ds:
            d = ds[-1]
            bg = d["input"].get("background")
            ert = d["input"].get("estimated_runtime_min")
            ex = f"bg={bg} ert={ert}"
            router = d["router"]["location"]
        else:
            ex, router = "(no exec call)", "-"
        text = f"{r.get('turn1_reply','')}\n{r.get('turn2_reply','')}".lower()
        f = {k: bool(re.search(p, text)) for k, p in _PROBES.items()}
        asks = text.count("?")
        print(f"{r['name']:<34} {wt:>5}  {ex:<22} {router:<10} "
              f"{str(f['informs_time'])[0]:<5} {str(f['offers_cluster'])[0]:<5} "
              f"{str(f['offers_bg'])[0]:<5} {str(f['offers_relaunch'])[0]:<5} "
              f"{str(f['offers_smaller'])[0]:<5} {asks:<4}")
    print("\nwt=injected walltime in the cue (NONE = clock absent, row proves nothing about it)")
    print("time/clust/bg/relau/small = the reply RAISED that topic (lexical probe, not a verdict)")
    print("clust vs bg: a CLUSTER job outlives the session; a local-lane background job does not")
    print("asks = count of '?' across both replies")
    return 0


def main():
    argv = [a for a in sys.argv[1:] if a != "--signals"]
    path = argv[0] if argv else latest()
    if not path:
        print("no results.json found"); return 1
    data = json.load(open(path))
    if "--signals" in sys.argv:
        return signals(data)
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
