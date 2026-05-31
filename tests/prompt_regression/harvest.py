"""Turn a captured raw request (ABA_RAW_REQUEST_DIR/req_*.json) into a case.

A captured request = {model, system, tools, messages}. We KEEP the real messages
(+ derive intent) and DROP the frozen system/tools (re-rendered at run time so we
test prompt changes). Behaviours / target_recipe / env_stubs are added by hand —
that's the curation step (using what we already diagnosed in the findings docs).

Usage:
  python harvest.py <req.json> <case_id> [--intent "..."] [--target-recipe NAME]
"""
import sys, json, argparse, os
sys.path.insert(0, os.path.dirname(__file__))
from harness import _last_user_text

ap = argparse.ArgumentParser()
ap.add_argument("req"); ap.add_argument("case_id")
ap.add_argument("--intent", default=None)
ap.add_argument("--target-recipe", default=None)
a = ap.parse_args()

req = json.load(open(a.req))
case = {
    "id": a.case_id,
    "source": os.path.basename(a.req),
    "model": req.get("model", "claude-haiku-4-5-20251001"),
    "render": {"role": "primary", "ctx": {}},
    "intent": a.intent or _last_user_text(req["messages"]),
    "target_recipe": a.target_recipe,
    "messages": req["messages"],          # REAL captured history (kept)
    "behaviors": ["reads_then_plans_then_stops"],
    "env_stubs": {},                      # fill in: read_skill -> recipe body, fetch_url -> 403, etc.
}
out = os.path.join(os.path.dirname(__file__), "cases", a.case_id + ".json")
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump(case, open(out, "w"), indent=1)
print(f"wrote {out}  ({len(case['messages'])} messages, intent={case['intent']!r})")
print("NOW: add env_stubs (read_skill body, fetch failures…) + confirm behaviors/target_recipe.")
