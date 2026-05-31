"""Prompt-regression harness — measured message-engineering (suggestion 1).

Core idea (the lessons from the 2026-05-30 recipe-uptake saga, baked in):
  1. Replay REAL captured requests, never hand reconstructions (a clean 3-msg
     reconstruction read 4/6; the real 25-msg history read 0/8).
  2. Re-render the SYSTEM via the live build_system (under a chosen arm/variant),
     but keep the case's REAL captured `messages` (they carry the tool_results +
     execution momentum that actually drive behaviour). So we test prompt CHANGES
     against real histories.
  3. Score on TOOL CALLS (deterministic) at adequate n (>=16; 2-3 is noise), with
     a short stubbed rollout for behaviours that span steps (read -> plan -> stop).

A `case` is a real request + desired behaviours:
  {id, model, messages:[...real...], render:{role,ctx}, intent, env_stubs:{tool:result},
   target_recipe, behaviors:[names]}
A `variant` transforms the system: {arm, sys_sub:[(old,new),...], ablate:[block,...]}.
"""
from __future__ import annotations
import os, sys, json, re, copy
from concurrent.futures import ThreadPoolExecutor

BACKEND = "/workspace/aba/backend"
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def _api_key() -> str:
    for ln in open("/workspace/aba/.env"):
        m = re.match(r'\s*(?:export\s+)?ANTHROPIC_API_KEY\s*=\s*["\']?([^"\'\s]+)', ln)
        if m:
            return m.group(1)
    raise RuntimeError("no ANTHROPIC_API_KEY in /workspace/aba/.env")


def _oauth_token() -> str | None:
    """The Claude Code subscription OAuth bearer: $CLAUDE_CODE_OAUTH_TOKEN (the
    long-lived `claude setup-token` artifact) else the stored CLI credential."""
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if tok:
        return tok.strip()
    cred = os.path.expanduser("~/.claude/.credentials.json")
    if os.path.exists(cred):
        oa = json.load(open(cred)).get("claudeAiOauth") or {}
        t = oa.get("accessToken") or oa.get("access_token")
        if t:
            return t.strip()
    return None


def _client():
    """Anthropic client for replay. DEFAULT = OAuth bearer, which bills the Claude
    Code subscription (Agent-SDK credit), NOT the project .env api-key. The request
    itself is byte-identical either way, so behavior is unchanged — only billing moves.
    Set ABA_EVAL_CREDENTIAL=apikey to opt back into the .env key. Never falls back to
    .env silently: if OAuth is requested but no token is found, it fails loudly."""
    import anthropic
    if os.environ.get("ABA_EVAL_CREDENTIAL", "oauth") == "apikey":
        return anthropic.Anthropic(api_key=_api_key())
    tok = _oauth_token()
    if not tok:
        raise RuntimeError(
            "OAuth credential mode but no token found. Run `claude setup-token` and "
            "`export CLAUDE_CODE_OAUTH_TOKEN=...`, or set ABA_EVAL_CREDENTIAL=apikey "
            "to use the .env key.")
    return anthropic.Anthropic(auth_token=tok)


def _last_user_text(messages) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m["content"]
            return c if isinstance(c, str) else " ".join(
                b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def render_system(case: dict, variant: dict) -> tuple[str, list]:
    """Re-render the system prompt via the LIVE build_system under the variant.
    variant keys: arm (ABA_PROMPT_ARM), ablate (list of block names to drop),
    sys_sub (list of (old,new) string swaps applied after render)."""
    os.environ["ABA_PROMPT_ARM"] = variant.get("arm", "nonneg")
    import importlib
    import content.bio.prompts.build as B
    importlib.reload(B)                       # pick up live .md edits (lru_cache is per-module)
    from content.bio.tools import TOOL_SCHEMAS
    ablate = set(variant.get("ablate", []))
    if ablate:
        orig = B._BLOCKS
        B._BLOCKS = tuple(b for b in orig if b.name not in ablate)
    try:
        intent = case.get("intent") or _last_user_text(case["messages"])
        system = B.build_system(TOOL_SCHEMAS, role=case.get("render", {}).get("role", "primary"),
                                intent=intent, ctx=case.get("render", {}).get("ctx", {}))
    finally:
        if ablate:
            B._BLOCKS = orig
    for old, new in variant.get("sys_sub", []):
        if old not in system:
            raise RuntimeError(f"sys_sub anchor not found: {old[:60]!r}")
        system = system.replace(old, new)
    return system, TOOL_SCHEMAS


def _stub(name: str, env_stubs: dict) -> str:
    v = env_stubs.get(name)
    if v is None:
        return '{"status":"ok"}'
    return v if isinstance(v, str) else json.dumps(v)


def _ser_blocks(content) -> list:
    """Anthropic content blocks -> plain serialisable dicts (text + tool_use)."""
    out = []
    for b in content:
        if getattr(b, "type", None) == "text":
            out.append({"type": "text", "text": b.text})
        elif getattr(b, "type", None) == "tool_use":
            out.append({"type": "tool_use", "name": b.name, "input": b.input})
    return out


def _create_with_retry(client, **kw):
    """messages.create with exponential backoff on transient errors (429 rate-limit, 5xx).
    Subscription rate limits are bursty; without this a single 429 propagates through
    ThreadPoolExecutor.map and nukes a whole sweep (lost a ~2000-request ablation once)."""
    import time
    import anthropic
    delay = 2.0
    for attempt in range(6):
        try:
            return client.messages.create(**kw)
        except anthropic.APIStatusError as e:
            if getattr(e, "status_code", None) not in (429, 500, 502, 503, 529) or attempt == 5:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 30)


def rollout(client, model, system, messages, tools, env_stubs, max_steps=4, max_tokens=2048) -> dict:
    """Replay one trajectory, stubbing tool results so multi-step behaviours
    (read -> plan -> stop) can be observed. Stops at present_plan / run_* / text.

    max_tokens=2048 (was 700): a tool_use whose `code` overruns the cap is captured
    with truncated/empty input -> code-content scorers FALSE-PASS and the judge goes
    blind. Bill is for actual tokens, so this only costs more on genuinely long code."""
    msgs = copy.deepcopy(messages)
    syslist = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    toolsc = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}] if tools else tools
    steps = []
    reads = []
    traj = []                       # full per-turn record (assistant blocks + stubbed results) for the judge layer
    code = ""                       # code of the run_python/run_r the agent commits to
    outcome = "maxsteps"
    for _ in range(max_steps):
        r = _create_with_retry(client, model=model, max_tokens=max_tokens, system=syslist, tools=toolsc, messages=msgs)
        tu = [b for b in r.content if b.type == "tool_use"]
        names = [b.name for b in tu]
        steps.append(names)
        traj.append({"role": "assistant", "blocks": _ser_blocks(r.content)})
        for b in tu:
            if b.name in ("read_skill", "search_skills"):
                reads.append(b.input.get("name") or b.input.get("query"))
        if "present_plan" in names:
            outcome = "planned"; break
        if any(n in ("run_python", "run_r") for n in names):
            code = "\n".join(b.input.get("code", "") for b in tu if b.name in ("run_python", "run_r"))
            outcome = "coded"; break
        if not tu:
            outcome = "text"; break
        msgs.append({"role": "assistant", "content": r.content})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": b.id, "content": _stub(b.name, env_stubs)} for b in tu]})
        traj.append({"role": "user", "blocks": [
            {"type": "tool_result", "tool": b.name, "content": _stub(b.name, env_stubs)} for b in tu]})

    def first(pred):
        return next((i for i, s in enumerate(steps) if pred(s)), None)
    return {
        "steps": steps, "reads": reads, "outcome": outcome, "code": code, "traj": traj,
        "read_step": first(lambda s: any(n in ("read_skill", "search_skills") for n in s)),
        "plan_step": first(lambda s: "present_plan" in s),
        "code_step": first(lambda s: any(n in ("run_python", "run_r") for n in s)),
    }


# Behaviour predicates over a rollout trace + the case. Deterministic, tool-call based.
def _read_target(t, c):
    tgt = c.get("target_recipe")
    return any(r == tgt for r in t["reads"]) if tgt else (t["read_step"] is not None)

BEHAVIORS = {
    "reads_recipe":               lambda t, c: t["read_step"] is not None,
    "reads_target_recipe":        _read_target,
    "plans":                      lambda t, c: t["outcome"] == "planned",
    "no_premature_code":          lambda t, c: t["outcome"] != "coded",
    "reads_then_plans_then_stops": lambda t, c: (
        t["outcome"] == "planned" and t["read_step"] is not None
        and (t["plan_step"] is None or t["read_step"] < t["plan_step"])),
}


# Code-content behaviors — inspect the code the agent commits to (t["code"]), reusing
# the validated #305 detectors. Framed as GOOD (no_X) so higher rate = better, like above.
def _load_code_detectors():
    from content.bio.tools import (_SYNTH_DATA_RE, _DE_CONSTRUCT_RE, _PERCELL_DESIGN_RE,
                                   _PSEUDOBULK_AGG_RE, _STRONG_FAB_RE, _DE_CTX_RE,
                                   _RANDOM_OP_RE, _REP_TOKEN_RE)
    return {
        "pseudorep": lambda code: bool(_DE_CONSTRUCT_RE.search(code) and _PERCELL_DESIGN_RE.search(code)
                                       and not _PSEUDOBULK_AGG_RE.search(code)),
        "synth": lambda code: bool(_SYNTH_DATA_RE.search(code)),
        "fabrep": lambda code: bool(_STRONG_FAB_RE.search(code) or
                                    (_DE_CTX_RE.search(code) and _RANDOM_OP_RE.search(code) and _REP_TOKEN_RE.search(code))),
    }

_DET = None
def _det(name, code):
    global _DET
    if _DET is None:
        _DET = _load_code_detectors()
    return _DET[name](code or "")

BEHAVIORS.update({
    "no_pseudoreplication":     lambda t, c: not _det("pseudorep", t.get("code", "")),
    "no_synthetic_data":        lambda t, c: not _det("synth", t.get("code", "")),
    "no_fabricated_replicates": lambda t, c: not _det("fabrep", t.get("code", "")),
})


def _variant_label(v: dict) -> str:
    if not v:
        return "current"
    if v.get("ablate"):
        return "ablate_" + "_".join(v["ablate"])
    if v.get("arm"):
        return "arm_" + v["arm"]
    return "variant"


def _persist_traj(capture_dir, cid, vlabel, rep, case, variant, trace):
    """Write one rollout's full trajectory + deterministic flags for the judge layer."""
    d = os.path.join(capture_dir, f"{cid}__{re.sub(r'[^A-Za-z0-9_.-]', '_', vlabel)}")
    os.makedirs(d, exist_ok=True)
    bnames = case.get("behaviors") or list(BEHAVIORS)
    det = {b: bool(BEHAVIORS[b](trace, case)) for b in bnames}
    rec = {"case_id": cid, "variant": variant, "variant_label": vlabel, "rep": rep,
           "intent": case.get("intent") or _last_user_text(case["messages"]),
           "target_recipe": case.get("target_recipe"),
           "outcome": trace["outcome"], "deterministic": det, "turns": trace["traj"]}
    with open(os.path.join(d, f"rep{rep:02d}.json"), "w") as f:
        json.dump(rec, f, indent=1, default=str)


def _agg(case: dict, traces: list, reps: int) -> dict:
    names = case.get("behaviors") or list(BEHAVIORS)
    n = len(traces) or 1
    rates = {b: round(sum(bool(BEHAVIORS[b](t, case)) for t in traces) / n, 3) for b in names}
    return {"rates": rates, "n": len(traces), "outcomes": _counter(t["outcome"] for t in traces)}


def run_case(case: dict, variant: dict, reps: int = 16, workers: int = 6,
             capture_dir: str | None = None) -> dict:
    """One (case, variant) cell at n reps. For whole sweeps prefer run_matrix (parallel)."""
    client = _client()
    system, tools = render_system(case, variant)
    model = case.get("model", "claude-haiku-4-5-20251001")
    env = case.get("env_stubs", {})
    cid, vlabel = case.get("id", "case"), _variant_label(variant)

    def one(i):
        t = rollout(client, model, system, case["messages"], tools, env)
        if capture_dir:
            _persist_traj(capture_dir, cid, vlabel, i, case, variant, t)
        return t
    with ThreadPoolExecutor(max_workers=workers) as ex:
        traces = list(ex.map(one, range(reps)))
    return _agg(case, traces, reps)


def run_matrix(cases: list, variants: list, reps: int = 16, workers: int = 12,
               capture_dir: str | None = None) -> dict:
    """Parallel sweep over the FULL (case x variant x rep) matrix.

    render_system mutates module globals (importlib.reload + _BLOCKS swap) and is NOT
    thread-safe, so we render every (case, variant) system SERIALLY first, then fan the
    independent rollouts out across one flat thread pool — near-linear speedup up to the
    API rate limit. Returns {case_id: {variant_label: {rates, n, outcomes}}}.
    """
    client = _client()
    cells = []                                          # phase 1: render serially
    for case in cases:
        for label, variant in variants:                 # variants: list of (label, variant_dict)
            system, tools = render_system(case, variant)
            cells.append({"case": case, "variant": variant, "system": system,
                          "tools": tools, "vlabel": label})
    tasks = [(ci, rep) for ci in range(len(cells)) for rep in range(reps)]

    def run_one(task):
        ci, rep = task
        cell = cells[ci]
        case = cell["case"]
        try:
            t = rollout(client, case.get("model", "claude-haiku-4-5-20251001"), cell["system"],
                        case["messages"], cell["tools"], case.get("env_stubs", {}))
        except Exception:  # noqa: BLE001 — one rollout dying must NOT nuke the whole sweep
            return ci, None
        if capture_dir:
            _persist_traj(capture_dir, case.get("id", "case"), cell["vlabel"], rep,
                          case, cell["variant"], t)
        return ci, t

    from concurrent.futures import as_completed
    buckets: dict = {}                                  # phase 2: fan out all rollouts
    fails, done, total = 0, 0, len(tasks)
    print(f"[run_matrix] starting {total} rollouts ({len(cells)} cells x {reps} reps, "
          f"workers={workers})", file=sys.stderr, flush=True)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(run_one, t) for t in tasks]   # as_completed -> true live progress
        for fut in as_completed(futs):
            ci, t = fut.result()
            done += 1
            if t is None:
                fails += 1
            else:
                buckets.setdefault(ci, []).append(t)
            if done % max(1, total // 20) == 0 or done == total:
                print(f"[run_matrix] progress {done}/{total} rollouts ({fails} failed)",
                      file=sys.stderr, flush=True)
    if fails:
        print(f"[run_matrix] WARNING: {fails}/{total} rollouts failed after retries "
              f"(skipped; rates are over completed reps only)", file=sys.stderr, flush=True)
    out: dict = {}
    for ci, cell in enumerate(cells):
        out.setdefault(cell["case"]["id"], {})[cell["vlabel"]] = _agg(cell["case"], buckets.get(ci, []), reps)
    return out


def _counter(it):
    d = {}
    for x in it:
        d[x] = d.get(x, 0) + 1
    return d
