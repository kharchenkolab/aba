"""
Tier-2 forensic diagnosis (the on-demand deep-dive).

Given a run's forensic bundle (misc/scenarios/_runs/<scenario>-<ts>/), an Opus
agent reads — for each FAILED step — the scenario INTENT (prompt + expected checks
+ planted truth), what the agent ACTUALLY did (response + tools + tool errors +
rubric), and the EXACT API context the model received that turn (the replayable raw
request), then root-causes the failure by LAYER (agent_model / recipe / tool /
context_assembly / harness / scenario_design / data) with evidence + a fix.

    ABA_SCENARIO=gwas_popstruct \
      /home/pkharchenko/aba/aba_runtime/.venv/bin/python -u tests/e2e/diagnose_failure.py [step_id]
    # or point at a bundle directly:
    BUNDLE=misc/scenarios/_runs/gwas_popstruct-20260629-075855 ... diagnose_failure.py s6

Diagnoses land in <bundle>/diagnosis/<step>.json.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "misc" / "scenarios"
CRED_KEYS = ("ABA_LLM_CREDENTIAL", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
             "ABA_HOME", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
MODEL = os.environ.get("ABA_FORENSIC_MODEL", "claude-opus-4-8")


def bootstrap():
    ef = Path(os.environ.get("ABA_LIVE_ENV", "/tmp/aba_8000.env"))
    if ef.exists():
        for kv in ef.read_bytes().split(b"\0"):
            if b"=" in kv:
                k, _, v = kv.partition(b"=")
                try:
                    k, v = k.decode(), v.decode()
                except Exception:
                    continue
                if k in CRED_KEYS and not os.environ.get(k):
                    os.environ[k] = v
    os.environ.setdefault("ABA_RUNTIME_DIR", "/tmp/aba_diag")
    sys.path.insert(0, str(ROOT / "backend"))


def find_bundle() -> Path:
    if os.environ.get("BUNDLE"):
        return Path(os.environ["BUNDLE"])
    scen = os.environ.get("ABA_SCENARIO")
    runs = sorted((LIB / "_runs").glob(f"{scen}-*" if scen else "*-*"))
    if not runs:
        sys.exit("no bundle found (set BUNDLE or ABA_SCENARIO)")
    return runs[-1]


def render_request(payload: dict, cap: int = 900) -> str:
    """Compact view of the EXACT context the model received that turn."""
    sysb = payload.get("system") or []
    sys_chars = sum(len(b.get("text", "")) for b in sysb if isinstance(b, dict))
    tools = [t.get("name") for t in (payload.get("tools") or [])]
    out = [f"system: {len(sysb)} block(s), {sys_chars} chars (full ABA system prompt; omitted here)",
           f"tools available: {len(tools)} — {tools[:25]}{' …' if len(tools) > 25 else ''}",
           f"messages: {len(payload.get('messages') or [])}"]
    for i, m in enumerate(payload.get("messages") or []):
        c = m.get("content")
        if isinstance(c, str):
            parts = [c]
        else:
            parts = []
            for b in (c or []):
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "text":
                    parts.append(b.get("text", ""))
                elif t == "tool_use":
                    parts.append(f"[tool_use {b.get('name')} input={json.dumps(b.get('input'))[:300]}]")
                elif t == "tool_result":
                    rc = b.get("content")
                    rc = json.dumps(rc)[:600] if not isinstance(rc, str) else rc[:600]
                    parts.append(f"[tool_result {rc}]")
                else:
                    parts.append(f"[{t}]")
        body = " ".join(parts)
        out.append(f"  [{i}] {m.get('role')}: {body[:cap]}{'…' if len(body) > cap else ''}")
    return "\n".join(out)


def diagnose(bundle: Path, step_rec: dict, scen_spec: dict) -> dict:
    from core.llm import sync_anthropic_client, _wants_cc_marker, _CC_MARKER_BLOCK
    sid = step_rec["step"]
    step_spec = next((s for s in scen_spec.get("steps", []) if s["id"] == sid), {})
    eo = scen_spec.get("expected_overall") or {}
    reqs = []
    for fn in (step_rec.get("context") or {}).get("req_files", []):
        p = bundle / "rawreq" / fn
        if p.exists():
            try:
                reqs.append(render_request(json.load(open(p))))
            except Exception:
                pass
    ctx = "\n\n--- next LLM call ---\n".join(reqs) or "(no raw request captured)"
    prompt = (
        f"A bioinformatics AI agent ('{scen_spec.get('id')}', {scen_spec.get('domain')}) underperformed on ONE step "
        f"of a realistic multi-turn session. Root-cause it.\n\n"
        f"=== SCENARIO GROUND TRUTH ===\n{eo.get('planted_truth', '')}\n\n"
        f"=== THE STEP (id={sid}, kind={step_spec.get('kind')}, actor={step_spec.get('actor')}) ===\n"
        f"User asked:\n{step_spec.get('prompt', '(user/curation action, not a prompt)')}\n\n"
        f"Expected (the checks):\n{json.dumps(step_spec.get('expect'), indent=2)[:1500]}\n\n"
        f"=== WHAT THE AGENT DID ===\n"
        f"verdict={step_rec.get('verdict')}  failed_checks={step_rec.get('fails')}\n"
        f"tools_used={step_rec.get('tools')}\ntool_errors={step_rec.get('tool_errors')}\n"
        f"errors={step_rec.get('errors')}\ncrash={step_rec.get('crash')}\n"
        f"produced={step_rec.get('produced')}\nrubric={json.dumps(step_rec.get('rubric'))[:800]}\n"
        f"agent_reply:\n{(step_rec.get('response') or '')[:2500]}\n\n"
        f"=== THE EXACT API CONTEXT THE MODEL RECEIVED THIS TURN ===\n{ctx[:14000]}\n\n"
        "Diagnose. Decide which LAYER is responsible: agent_model (the model erred despite good setup), "
        "recipe (a recipe was missing/wrong/not found), tool (a tool failed/was absent), context_assembly "
        "(ABA built the wrong/insufficient context — e.g. a resume dropped needed state), harness "
        "(the test runner mis-drove or mis-checked), scenario_design (the scenario/prompt/check is the "
        "problem), or data. Cite SPECIFIC evidence from the context/response. Distinguish a REAL platform "
        "problem from a mere check artifact. Respond with ONLY JSON: "
        "{layer, root_cause, evidence, suggested_fix, confidence (low|medium|high), is_real_problem (bool)}."
    )
    system = [{"type": "text", "text": "You are a meticulous forensic engineer for an AI bioinformatics "
               "platform. You read full API contexts and pinpoint root causes. Output ONLY JSON."}]
    if _wants_cc_marker():
        system = [_CC_MARKER_BLOCK, *system]
    msg = sync_anthropic_client().messages.create(
        model=MODEL, max_tokens=1500, system=system,
        messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}])
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    try:
        return json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception as e:
        return {"parse_error": str(e), "raw": raw[:600]}


def main() -> int:
    bootstrap()
    bundle = find_bundle()
    binfo = json.loads((bundle / "bundle.json").read_text())
    scen = binfo["scenario"]
    scen_spec = __import__("yaml").safe_load((LIB / scen / "scenario.yaml").read_text())
    want = sys.argv[1] if len(sys.argv) > 1 else None
    steps = [s for s in binfo["steps"]
             if (s["step"] == want if want else s.get("verdict") == "FAIL")]
    if not steps:
        print(f"no {'matching' if want else 'FAILED'} steps in {bundle.name}"); return 0
    print(f"=== forensic diagnosis: {bundle.name}  ({len(steps)} step(s), model={MODEL}) ===\n")
    (bundle / "diagnosis").mkdir(exist_ok=True)
    for s in steps:
        print(f"--- {s['step']} ({s.get('kind')}) fails={s.get('fails')} ---")
        d = diagnose(bundle, s, scen_spec)
        (bundle / "diagnosis" / f"{s['step']}.json").write_text(json.dumps(d, indent=2))
        if d.get("parse_error"):
            print(f"  [judge parse error] {d['raw'][:200]}\n"); continue
        print(f"  layer: {d.get('layer')}  real_problem={d.get('is_real_problem')}  conf={d.get('confidence')}")
        print(f"  root_cause: {d.get('root_cause')}")
        print(f"  evidence:   {d.get('evidence')}")
        print(f"  fix:        {d.get('suggested_fix')}\n")
    print(f"diagnoses written to {bundle/'diagnosis'}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
