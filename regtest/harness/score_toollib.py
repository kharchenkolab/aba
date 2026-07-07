"""Extract tool_library discovery metrics from a regtest run bundle.

Usage: python regtest/harness/score_toollib.py <run_dir> [<run_dir> ...]

Per run it reports, from the bundle's turn_events + report.json:
  - aba.*  read calls (from the [aba.<verb>] stdout markers the in-kernel lib prints)
  - list_entities / read_entity JSON-tool reads (the tools aba replaces)
  - task success (mechanical pass, rubric overall)
  - aba error signals (NameError etc. mentioning aba — a discovery/wiring miss)

The key comparison (LIBRARY vs CONTROL arm): does aba_total rise and json_read_total
fall, with success held? High json_read_total in the LIBRARY arm = the agent ignored the
library (undiscovery); aba_errors > 0 = it tried and mis-invoked.
"""
import glob
import json
import sys
from pathlib import Path


def score(run_dir: str) -> dict:
    run = Path(run_dir)
    out: dict = {"run": run.name}
    rp = run / "report.json"
    if rp.exists():
        d = json.load(open(rp))
        out["model"] = d.get("agent_model")
        out["mode"] = d.get("mode")
        out["mech"] = d.get("mechanical")
        out["rubric_overall"] = (d.get("rubric_mean") or {}).get("overall")
        out["n_steps"] = len(d.get("report") or [])
    aba = {"find": 0, "get": 0, "types": 0, "exists": 0}
    json_reads = {"list_entities": 0, "read_entity": 0}
    tool_total = 0
    aba_errors = 0
    for te in glob.glob(str(run / "turn_events" / "*.jsonl")):
        for ln in open(te, errors="ignore"):
            try:
                e = json.loads(ln)
            except Exception:
                continue
            p = e.get("payload", e)
            t = p.get("type")
            if t == "tool_start":
                tool_total += 1
                nm = p.get("name")
                if nm in json_reads:
                    json_reads[nm] += 1
            txt = ""
            if t == "tool_result":
                r = p.get("result") or {}
                txt = r.get("stdout", "") if isinstance(r, dict) else str(r)
            elif t == "tool_chunk":
                txt = p.get("text", "") or ""
            if txt:
                for verb in aba:
                    aba[verb] += txt.count(f"[aba.{verb}]")
                if "aba" in txt and ("NameError" in txt or "AttributeError" in txt):
                    aba_errors += 1
    out["aba_calls"] = aba
    out["aba_total"] = sum(aba.values())
    out["json_reads"] = json_reads
    out["json_read_total"] = sum(json_reads.values())
    out["tool_total"] = tool_total
    out["aba_errors"] = aba_errors
    return out


if __name__ == "__main__":
    rows = [score(a) for a in sys.argv[1:]]
    for r in rows:
        m = r.get("mech") or {}
        print(f"{r['run']:<40} model={str(r.get('model')):<32} "
              f"mech={m.get('pass')}/{m.get('total')} rubric={r.get('rubric_overall')} "
              f"| aba={r['aba_total']} {r['aba_calls']} "
              f"| json_reads={r['json_read_total']} {r['json_reads']} "
              f"| aba_err={r['aba_errors']}")
    print(json.dumps(rows, indent=1))
