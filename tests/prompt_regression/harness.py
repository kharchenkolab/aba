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
from typing import Optional

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


# Internal-only schema fields that ABA reads off TOOL_SCHEMAS in-process but the
# Anthropic API rejects on tool definitions (mirrors core/llm.py:_INTERNAL_KEYS).
_INTERNAL_TOOL_KEYS = {"approval_policy"}


def _api_tools(tools: list) -> list:
    return [{k: v for k, v in t.items() if k not in _INTERNAL_TOOL_KEYS} for t in (tools or [])]


def render_system(case: dict, variant: dict) -> tuple[str, list]:
    """Re-render the system prompt via the LIVE build_system under the variant.
    variant keys: arm (ABA_PROMPT_ARM), ablate (list of block names to drop),
    sys_sub (list of (old,new) string swaps applied after render)."""
    os.environ["ABA_PROMPT_ARM"] = variant.get("arm", "nonneg")
    # CC-convergence Phase 4 study: variant['tier'] toggles whether recipes
    # render in the system prompt ('all', the additive default) or only in the
    # <system-reminder> ('core' = recipes-only-in-reminder, the strict variant).
    os.environ["ABA_SKILLS_TIER"] = variant.get("tier", "all")
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
        stable, dynamic = B.build_system(TOOL_SCHEMAS,
                                         role=case.get("render", {}).get("role", "primary"),
                                         intent=intent,
                                         ctx=case.get("render", {}).get("ctx", {}))
    finally:
        if ablate:
            B._BLOCKS = orig
    # Apply legacy sys_sub on the full (stable + dynamic) text view, since
    # anchors may live in either part. We re-join them by "\n\n" same way the
    # API will see them concatenated content-wise.
    system = stable + ("\n\n" + dynamic if dynamic else "")
    for old, new in variant.get("sys_sub", []):
        if old not in system:
            raise RuntimeError(f"sys_sub anchor not found: {old[:60]!r}")
        system = system.replace(old, new)
    # CASE-AWARE variant: inject the case's target recipe body into the system
    # prompt (the eval-side approximation of "re-inject the recipe at code-gen
    # time"). Only fires when the variant declares append_recipe_body=True AND
    # the case carries the recipe in env_stubs.read_skill.body.
    if variant.get("append_recipe_body"):
        rb_raw = (case.get("env_stubs") or {}).get("read_skill")
        rb: dict = {}
        if isinstance(rb_raw, str):
            try: rb = json.loads(rb_raw)
            except Exception: rb = {}
        elif isinstance(rb_raw, dict):
            rb = rb_raw
        body = rb.get("body") if isinstance(rb, dict) else None
        if body:
            system = system + (
                "\n\n## Recipe for this turn — keep it salient when generating code\n"
                f"You just read the `{rb.get('name','')}` recipe. Here is its body:\n\n"
                f"{body}\n\n"
                "Stay faithful to this recipe's APIs and step ordering when you write code.")
    return system, _api_tools(TOOL_SCHEMAS)


def _stub(name: str, env_stubs: dict, tool_input: dict | None = None) -> str:
    # Skill/read_skill: serve the CURRENT registry's body, not the case's
    # frozen env_stub. The captures predate the -vN rename; without this fix
    # the harness would return a V1-labeled body for a V2 lookup, polluting
    # the model's context with stale recipe names and confusing downstream
    # plan-step / recipe-following predicates.
    if name in ("Skill", "read_skill") and isinstance(tool_input, dict):
        skill_name = tool_input.get("skill") or tool_input.get("name") or ""
        if skill_name:
            try:
                from core.skills import invoke_skill
                inv = invoke_skill(skill_name, tool_input.get("args") or "")
                if inv is not None:
                    spec = inv["spec"]
                    out = {
                        "status": "ok",
                        "name": spec.name,
                        "description": spec.description,
                        "when_to_use": spec.when_to_use,
                        "requires_tools": list(spec.requires_tools),
                        "capabilities_needed": list(spec.capabilities_needed),
                        "produces": list(spec.produces),
                        "resources": list(inv["resources"]),
                        "body": inv["body"],
                    }
                    return json.dumps(out)
            except Exception:  # noqa: BLE001 — fall through to env_stub on any registry error
                pass
            # Unknown name → return the same unknown_skill shape live tools do.
            return json.dumps({"status": "unknown_skill",
                               "note": f"No skill named {skill_name!r}."})
    v = env_stubs.get(name)
    # CC-convergence Phase 1: cases captured before Skill replaced read_skill key
    # their stubs under "read_skill". When the agent now calls `Skill`, fall back
    # to the legacy key so historical stubs keep serving the same body.
    if v is None and name == "Skill":
        v = env_stubs.get("read_skill")
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


def _load_recipe_body(name: str) -> str | None:
    """Look up a recipe body by name via the live skills registry. Returns None if absent."""
    if not name: return None
    try:
        sys.path.insert(0, "/workspace/aba/backend")
        from core.skills import get_skill
        spec = get_skill(name)
        return getattr(spec, "body", None) if spec else None
    except Exception:
        return None


def _declared_recipes_from_plan(plan_input: dict) -> list[str]:
    """Extract the union of `skill` fields from a present_plan call's steps."""
    out: list[str] = []
    seen: set = set()
    for s in (plan_input.get("steps") or []):
        if isinstance(s, dict):
            sk = (s.get("skill") or "").strip()
            if sk and sk not in seen:
                out.append(sk); seen.add(sk)
    return out


def _declared_recipes_with_steps(plan_input: dict) -> list[tuple[str, list[tuple[int, str]]]]:
    """Walk the plan steps and group by declared `skill` — returning
    [(recipe_name, [(step_index, step_title), ...]), ...] preserving plan order.
    Used by step-labeled injection (variant B.1)."""
    by_recipe: dict[str, list[tuple[int, str]]] = {}
    order: list[str] = []
    for i, s in enumerate(plan_input.get("steps") or [], start=1):
        if not isinstance(s, dict): continue
        sk = (s.get("skill") or "").strip()
        if not sk: continue
        if sk not in by_recipe:
            by_recipe[sk] = []; order.append(sk)
        by_recipe[sk].append((i, (s.get("title") or "").strip()))
    return [(rn, by_recipe[rn]) for rn in order]


def _splice_recipes_reminder(messages: list, reminder: str) -> list:
    """Harness mirror of guide.py's reminder splice (CC-convergence Phase 4) —
    prepend the recipes catalog to the latest user-text message of the replay.
    No-op when reminder is empty or the latest message has no user-text block."""
    if not reminder or not messages:
        return messages
    last = messages[-1]
    if last.get("role") != "user":
        return messages
    content = last.get("content")
    if isinstance(content, str):
        new_content = [
            {"type": "text", "text": reminder},
            {"type": "text", "text": content},
        ]
    elif isinstance(content, list):
        has_text = any(isinstance(b, dict) and b.get("type") == "text" for b in content)
        if not has_text:
            return messages
        new_content = [{"type": "text", "text": reminder}, *content]
    else:
        return messages
    out = list(messages[:-1])
    out.append({**last, "content": new_content})
    return out


def rollout(client, model, system, messages, tools, env_stubs, max_steps=10, max_tokens=2048,
            max_code_turns=6, continue_after_plan: bool = False,
            step_labeled_injection: bool = False, plan_recheck_steer: bool = False) -> dict:
    """Replay one trajectory, stubbing tool results so multi-step behaviours can be
    observed. Stops at present_plan (cold-start cases) / text / max_code_turns / max_steps.

    Critical: we do NOT break on the first run_python/run_r call. Agents (especially
    Haiku) code incrementally — load → inspect → cluster → DE → results across 4–6
    chunks. To measure recipe-following / method-validity across the full pipeline,
    we stub the run_* result with '{"status":"ok"}' and let the agent keep going,
    concatenating code chunks into `code`. Capped at max_code_turns code blocks so
    rollouts can't run away.

    continue_after_plan=True: do NOT break on present_plan. Instead, capture the
    declared `skill` field from each plan step, synthesize a "user said Go" tool
    result for the present_plan call, append the declared recipes' bodies to the
    system prompt for the rest of the rollout, and keep going. This tests the
    declared-recipes → injected-at-codegen hypothesis on cold-start cases."""
    msgs = copy.deepcopy(messages)
    # CC-convergence Phase 4: the <system-reminder> recipes-catalog injection
    # is OFF (n=8 study on Haiku 4.5 + Sonnet 4.6 showed both models reject
    # reminder-only catalog placement — different failure modes, both real).
    # The recipes catalog lives in the (now uncached) dynamic system block. The
    # splice helpers stay in the codebase for the future user-invocable
    # slash-palette use case (Phase 5).
    # Honor ABA_CACHE_TTL=1h for long A/B campaigns (amortizes cache writes
    # across many sweep runs over the day). Default 5-min ephemeral.
    _cc = {"type": "ephemeral", "ttl": "1h"} if os.environ.get("ABA_CACHE_TTL") == "1h" else {"type": "ephemeral"}
    # OAuth bearer gates Sonnet/Opus on the Claude Code marker being the FIRST
    # system block (byte-exact, NO cache_control). Mirrors backend/core/llm.py.
    # Haiku passes without it. ABA_EVAL_CREDENTIAL=apikey skips this gate entirely.
    _CC_MARKER = "You are a Claude agent, built on Anthropic's Claude Agent SDK."
    if "haiku" not in (model or "").lower() and os.environ.get("ABA_EVAL_CREDENTIAL", "oauth") != "apikey":
        syslist = [{"type": "text", "text": _CC_MARKER},
                   {"type": "text", "text": system, "cache_control": _cc}]
    else:
        syslist = [{"type": "text", "text": system, "cache_control": _cc}]
    toolsc = [*tools[:-1], {**tools[-1], "cache_control": _cc}] if tools else tools
    steps = []
    reads = []
    traj = []                       # full per-turn record (assistant blocks + stubbed results) for the judge layer
    code_chunks: list[str] = []     # all run_python/run_r code chunks in order
    declared: list[str] = []        # recipes the agent named on its plan steps
    outcome = "maxsteps"
    # Token-usage accumulators (per-rollout sums). Reported in _agg so a sweep
    # surfaces actual cache-hit vs cache-write ratios instead of inferring.
    u_in = u_out = u_cr = u_cw = 0
    for _ in range(max_steps):
        r = _create_with_retry(client, model=model, max_tokens=max_tokens, system=syslist, tools=toolsc, messages=msgs)
        if getattr(r, "usage", None):
            u = r.usage
            u_in += getattr(u, "input_tokens", 0) or 0
            u_out += getattr(u, "output_tokens", 0) or 0
            u_cr += getattr(u, "cache_read_input_tokens", 0) or 0
            u_cw += getattr(u, "cache_creation_input_tokens", 0) or 0
        tu = [b for b in r.content if b.type == "tool_use"]
        names = [b.name for b in tu]
        steps.append(names)
        traj.append({"role": "assistant", "blocks": _ser_blocks(r.content)})
        for b in tu:
            if b.name in ("Skill", "read_skill", "search_skills"):
                # `Skill` carries the skill name in input.skill; the legacy
                # `read_skill` alias uses input.name; search_skills uses input.query.
                reads.append(b.input.get("skill") or b.input.get("name") or b.input.get("query"))
            if b.name in ("run_python", "run_r"):
                cc = b.input.get("code", "") or ""
                if cc: code_chunks.append(cc)
        if "present_plan" in names:
            if not continue_after_plan:
                outcome = "planned"; break
            # Continue-after-plan mode: capture declared recipes, fake the user's
            # Go, inject declared recipe bodies into the system, keep looping.
            plan_block = next(b for b in tu if b.name == "present_plan")
            declared = _declared_recipes_from_plan(plan_block.input or {})
            addendum_parts: list[str] = []
            if step_labeled_injection:
                # (B.1) Per-step labeled injection — each recipe is bound to the
                # explicit step indices that declared it, so the model sees
                # "for step 4 use recipe X" rather than a context-leaking union.
                for rn, slots in _declared_recipes_with_steps(plan_block.input or {}):
                    body = _load_recipe_body(rn)
                    if not body: continue
                    labels = ", ".join(f"step {i} ({t[:60]})" for i, t in slots)
                    addendum_parts.append(
                        f"\n\n## Recipe `{rn}` — declared in your plan for: {labels}\n"
                        f"Apply this recipe's APIs ONLY for the listed step(s); other "
                        f"steps may use a different recipe or none.\n\n{body}")
            else:
                for rn in declared:
                    body = _load_recipe_body(rn)
                    if body:
                        addendum_parts.append(
                            f"\n\n## Recipe `{rn}` — declared in your plan, keep it salient when generating code\n{body}")
            if addendum_parts:
                syslist = [{"type": "text",
                            "text": system + "".join(addendum_parts) +
                                    "\n\nStay faithful to these recipes' APIs and step ordering.",
                            "cache_control": _cc}]
            # (F) Optional mid-plan recheck steer: tacked onto the user's "Go"
            # tool_result for present_plan, asking the agent to reconsider
            # recipe-fit for step 1 BEFORE coding it.
            recheck_text = (" Before starting, look at step 1 one more time: "
                            "what recipe did you bind it to, and what exact API/method does "
                            "that recipe prescribe? If the binding looks off, present_plan "
                            "again with a revision rather than coding around it.") if plan_recheck_steer else ""
            msgs.append({"role": "assistant", "content": r.content})
            msgs.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id,
                 "content": ("User approved the plan. Go ahead and execute it now." + recheck_text
                             if b.name == "present_plan"
                             else _stub(b.name, env_stubs, b.input))} for b in tu]})
            traj.append({"role": "user", "blocks": [
                {"type": "tool_result", "tool": b.name,
                 "content": ("User approved the plan. Go ahead and execute it now." + recheck_text
                             if b.name == "present_plan"
                             else _stub(b.name, env_stubs, b.input))} for b in tu]})
            continue
        if not tu:
            outcome = "text"; break
        # Cap on code-emitting turns: once the agent has emitted N code chunks,
        # treat the trajectory as a coded outcome and stop. (max_code_turns is a
        # turn count, not a chunk count — a turn may legitimately emit multiple
        # run_python blocks; we still count it as one turn against the cap.)
        if any(n in ("run_python", "run_r") for n in names):
            n_code_turns = sum(1 for s in steps if any(n in ("run_python", "run_r") for n in s))
            if n_code_turns >= max_code_turns:
                outcome = "coded"; break
        msgs.append({"role": "assistant", "content": r.content})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": b.id, "content": _stub(b.name, env_stubs, b.input)} for b in tu]})
        traj.append({"role": "user", "blocks": [
            {"type": "tool_result", "tool": b.name, "content": _stub(b.name, env_stubs, b.input)} for b in tu]})
    else:
        # max_steps reached without break — if any code was emitted, mark coded.
        if code_chunks: outcome = "coded"
    code = "\n".join(code_chunks)

    def first(pred):
        return next((i for i, s in enumerate(steps) if pred(s)), None)
    return {
        "steps": steps, "reads": reads, "outcome": outcome, "code": code, "traj": traj,
        "declared_recipes": declared,
        "read_step": first(lambda s: any(n in ("Skill", "read_skill", "search_skills") for n in s)),
        "plan_step": first(lambda s: "present_plan" in s),
        "code_step": first(lambda s: any(n in ("run_python", "run_r") for n in s)),
        "usage": {"in": u_in, "out": u_out, "cache_read": u_cr, "cache_write": u_cw},
    }


# ── Recipe-following deterministic scorer (Phase 1 of #324) ──────────────────
# Each entry: recipe-name -> ordered list of (label, api-regex). The regexes are
# the "scoring tokens" — APIs/calls the recipe canonically uses for that step.
# Hand-annotated from the in-code numbered comments in scrna-qc-clustering.md
# and the section headings in deseq2_r / seurat-scrna / pagoda2-scrna.
_RECIPE_APIS: dict[str, list[tuple[str, str]]] = {
    "scrna-qc-clustering": [
        ("load_10x",       r"sc\.read_(10x_mtx|mtx)\b"),
        ("qc_metrics",     r"sc\.pp\.calculate_qc_metrics\b"),
        ("filter_cells",   r"sc\.pp\.filter_cells\b"),
        ("filter_genes",   r"sc\.pp\.filter_genes\b"),
        ("normalize",      r"sc\.pp\.normalize_total\b"),
        ("log1p",          r"sc\.pp\.log1p\b"),
        ("hvg",            r"sc\.pp\.highly_variable_genes\b"),
        ("pca",            r"sc\.pp\.pca\b|sc\.tl\.pca\b"),
        ("neighbors",      r"sc\.pp\.neighbors\b"),
        ("umap",           r"sc\.tl\.umap\b"),
        ("leiden",         r"sc\.tl\.leiden\b"),
        ("rank_genes",     r"sc\.tl\.rank_genes_groups\b"),
        ("no_scale",       r"^(?!.*sc\.pp\.scale).*$"),  # NEGATIVE — recipe says DO NOT scale
        ("no_seurat_v3",   r"^(?!.*flavor\s*=\s*['\"]seurat_v3['\"]).*$"),  # NEGATIVE — recipe says no seurat_v3
    ],
    "seurat-scrna": [
        ("create_seurat",  r"CreateSeuratObject\("),
        ("qc_subset",      r"PercentageFeatureSet\(|VlnPlot\("),
        ("normalize",      r"NormalizeData\("),
        ("hvg",            r"FindVariableFeatures\("),
        ("scale",          r"ScaleData\("),
        ("pca",            r"RunPCA\("),
        ("neighbors",      r"FindNeighbors\("),
        ("clusters",       r"FindClusters\("),
        ("umap",           r"RunUMAP\("),
        ("markers",        r"FindAllMarkers\(|FindMarkers\("),
    ],
    "deseq2-r": [
        ("dds_construct", r"DESeqDataSetFromMatrix\("),
        ("relevel",       r"relevel\("),       # KEY — recipe insists on explicit reference level
        ("pre_filter",    r"rowSums\(|keep\s*<-"),
        ("deseq",         r"DESeq\("),
        ("results",       r"results\("),
        ("lfc_shrink",   r"lfcShrink\("),
    ],
    "pagoda2-scrna": [
        ("read_10x",      r"read_10x_explicit\(|Read10X\("),
        ("qc_filter",     r"gene\.vs\.molecule\.cell\.filter\("),
        ("make_unique",   r"make\.unique\("),   # KEY — recipe says required, often skipped
        ("p2_new",        r"Pagoda2\$new\("),
        ("adjust_var",    r"adjustVariance\("),
        ("calc_pca",      r"calculatePcaReduction\("),
        ("knn_pca",       r"makeKnnGraph\(.*type\s*=\s*['\"]PCA['\"]"),  # critical: type='PCA'
        ("leiden",        r"getKnnClusters\(.*leiden"),
        ("umap",          r"getEmbedding\("),
    ],
}


def recipe_apis_used(code: str, recipe: str) -> dict:
    """Score code against a recipe's canonical API tokens.

    Returns {hit: set[label], missed: set[label], coverage: float in [0,1]}.
    Negative checks (NEGATIVE comment in the regex) PASS when the pattern does
    NOT match (the recipe says "do NOT X" — the agent honored it). Treat each
    label as a 0/1 hit; coverage = hit / total labels.
    """
    import re as _re
    spec = _RECIPE_APIS.get(recipe)
    if not spec:
        return {"hit": set(), "missed": set(), "coverage": 0.0, "n_labels": 0}
    hit, missed = set(), set()
    for label, pat in spec:
        # negative-checks live as look-ahead-only regexes; for them we check
        # the ABSENCE of the underlying API. Detect by the magic prefix `^(?!`.
        rx = _re.compile(pat, _re.M | _re.I)
        is_negative = pat.startswith("^(?!")
        if is_negative:
            # PASS if the forbidden token is NOT present anywhere in code.
            inner = pat[4:pat.rindex(").*$")]   # extract the look-ahead body
            forbidden = _re.compile(inner, _re.I)
            if forbidden.search(code):
                missed.add(label)
            else:
                hit.add(label)
        else:
            if rx.search(code):
                hit.add(label)
            else:
                missed.add(label)
    return {"hit": hit, "missed": missed, "coverage": len(hit) / max(1, len(spec)), "n_labels": len(spec)}


# Behaviour predicates over a rollout trace + the case. Deterministic, tool-call based.
def _read_target(t, c):
    tgt = c.get("target_recipe")
    if not tgt:
        return t["read_step"] is not None
    # Recipes versioned with a `-v<digit>+` suffix (e.g. scrna-qc-clustering →
    # scrna-qc-clustering-v2) are the same recipe for scoring purposes. Match
    # base-name so cases survive in-place rename to a later version.
    import re as _re
    def base(n: str) -> str:
        return _re.sub(r"-v\d+$", "", n or "")
    tgt_b = base(tgt)
    return any(base(r) == tgt_b for r in t["reads"])

# Phase 6 — composition predicates. An orchestrator-shaped recipe body
# explicitly tells the agent to invoke other Skills in sequence; we want to know
# whether the agent actually chains those calls or inlines the whole pipeline
# in run_python. The sub-Skill names are extracted by name from the
# orchestrator body — no per-case configuration needed.
import re as _re_for_compose

def _orchestrator_subskills(target: str) -> list[str]:
    """Pull the ordered list of `Skill(skill="name")` references out of the
    target recipe body. Returns the first occurrences only (subsequent dupes
    dropped) so a recipe that mentions a sub-Skill twice doesn't double-count.

    Defensive against unregistered registries: lazy-registers the live recipe
    library if the target isn't found, so rescore.py and other callers that
    skip the bio package import still get correct sub-Skill extraction."""
    if not target:
        return []
    sys.path.insert(0, BACKEND)
    try:
        from core.skills import get_skill, register_skill_dir
        spec = get_skill(target)
        if spec is None:
            # Lazy-register both tiers — registry is keyed by skill name so
            # this is idempotent if anyone else already registered.
            register_skill_dir(os.path.join(BACKEND, "system_bundle/skills/core"),
                              visibility="always")
            register_skill_dir(os.path.join(BACKEND, "system_bundle/skills/recipes"),
                              visibility="local")
            spec = get_skill(target)
    except Exception:
        return []
    if spec is None:
        return []
    seen: dict[str, int] = {}
    for m in _re_for_compose.finditer(r'Skill\(skill\s*=\s*["\']([a-z0-9-]+)["\']', spec.body or ""):
        n = m.group(1)
        if n not in seen and n != target:        # don't count self-references
            seen[n] = len(seen)
    return list(seen.keys())


def _composes_orchestrator(t: dict, c: dict) -> bool:
    """The FIRST Skill the agent invokes is the case's target_recipe (the
    orchestrator). False if it skipped the orchestrator and went straight to a
    sub-Skill, or never read anything."""
    tgt = c.get("target_recipe")
    reads = t.get("reads") or []
    if not tgt or not reads:
        return False
    import re as _re
    base = lambda n: _re.sub(r"-v\d+$", "", n or "")
    return base(reads[0]) == base(tgt)


def _chains_sub_skills(t: dict, c: dict) -> bool:
    """The agent invoked ALL the sub-Skills the orchestrator body lists, in
    SOME order. Strict — partial chaining (some sub-Skills missing) is
    captured by composes_any_subskill instead."""
    tgt = c.get("target_recipe")
    reads = t.get("reads") or []
    if not tgt:
        return False
    subs = _orchestrator_subskills(tgt)
    if not subs:
        return False         # no sub-Skills declared → predicate not applicable
    import re as _re
    base = lambda n: _re.sub(r"-v\d+$", "", n or "")
    read_bases = {base(r) for r in reads}
    return all(base(s) in read_bases for s in subs)


def _composes_any_subskill(t: dict, c: dict) -> bool:
    """Softer composition signal: the agent invoked at least ONE sub-Skill
    named in the orchestrator body. Useful when rollouts are capped before the
    full chain finishes — distinguishes 'agent inlined everything' (False) from
    'agent started chaining but ran out of room' (True)."""
    tgt = c.get("target_recipe")
    reads = t.get("reads") or []
    if not tgt:
        return False
    subs = _orchestrator_subskills(tgt)
    if not subs:
        return False
    import re as _re
    base = lambda n: _re.sub(r"-v\d+$", "", n or "")
    read_bases = {base(r) for r in reads}
    return any(base(s) in read_bases for s in subs)


def _planned_after_orchestrator(t: dict, c: dict) -> bool:
    """The agent invoked present_plan AFTER reading the orchestrator. Required
    when `continue_after_plan=True` — the `plans` predicate is False in that
    mode (outcome=='planned' never fires) so we need a way to verify planning
    actually happened. Walks the step-name sequence."""
    steps = t.get("steps") or []
    saw_orchestrator = False
    tgt = c.get("target_recipe")
    if not tgt:
        return any("present_plan" in s for s in steps)
    import re as _re
    base = lambda n: _re.sub(r"-v\d+$", "", n or "")
    tgt_b = base(tgt)
    for step in steps:
        # `step` is a list of tool_use names from one assistant turn
        if "Skill" in step or "read_skill" in step:
            saw_orchestrator = True
        if saw_orchestrator and "present_plan" in step:
            return True
    return False


BEHAVIORS = {
    "reads_recipe":               lambda t, c: t["read_step"] is not None,
    "reads_target_recipe":        _read_target,
    "plans":                      lambda t, c: t["outcome"] == "planned",
    "no_premature_code":          lambda t, c: t["outcome"] != "coded",
    "reads_then_plans_then_stops": lambda t, c: (
        t["outcome"] == "planned" and t["read_step"] is not None
        and (t["plan_step"] is None or t["read_step"] < t["plan_step"])),
    "composes_orchestrator":      _composes_orchestrator,
    "chains_sub_skills":          _chains_sub_skills,
    "composes_any_subskill":      _composes_any_subskill,
    "planned_after_orchestrator": _planned_after_orchestrator,
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

def _recipe_following(t: dict, c: dict, threshold: float = 0.5) -> bool:
    """Did the executed code follow >= `threshold` of the target recipe's canonical
    API tokens? Only applies to coded outcomes — non-coded rollouts return False
    (you can't follow a recipe in code you didn't write). Cases without a
    target_recipe in _RECIPE_APIS return False as well (no spec to score against)."""
    if not t.get("code"):
        return False
    tgt = c.get("target_recipe")
    if not tgt or tgt not in _RECIPE_APIS:
        return False
    return recipe_apis_used(t["code"], tgt)["coverage"] >= threshold


def _declared_recipe(t: dict, c: dict) -> bool:
    """Did the agent name at least one recipe on a plan step's `skill` field?"""
    return bool(t.get("declared_recipes"))


def _declared_then_followed(t: dict, c: dict, threshold: float = 0.5) -> bool:
    """Did the agent declare a recipe AND did its code cover >= threshold of that
    recipe's canonical APIs? Scores against the FIRST declared recipe (most plans
    bind one analysis recipe). If no recipe was declared, returns False (it can't
    have "declared and followed" what was never declared)."""
    if not t.get("code"): return False
    declared = t.get("declared_recipes") or []
    # Score against any declared recipe with a spec — pass if any meets threshold.
    for rn in declared:
        if rn in _RECIPE_APIS and recipe_apis_used(t["code"], rn)["coverage"] >= threshold:
            return True
    return False


BEHAVIORS.update({
    "recipe_following":          lambda t, c: _recipe_following(t, c, threshold=0.5),
    "recipe_following_strict":   lambda t, c: _recipe_following(t, c, threshold=0.8),
    "declared_recipe":           _declared_recipe,
    "declared_then_followed":    lambda t, c: _declared_then_followed(t, c, threshold=0.5),
    "declared_then_followed_strict": lambda t, c: _declared_then_followed(t, c, threshold=0.8),
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


def _rep_path(capture_dir: str, cid: str, vlabel: str, rep: int) -> str:
    """Where a single rep's trajectory lands on disk. Stable across resume runs
    so an existing file is a definitive 'this rep already completed' signal."""
    safe_label = re.sub(r'[^A-Za-z0-9_.-]', '_', vlabel)
    return os.path.join(capture_dir, f"{cid}__{safe_label}", f"rep{rep:02d}.json")


def _persist_traj(capture_dir, cid, vlabel, rep, case, variant, trace):
    """Write one rollout's full trajectory + deterministic flags for the judge layer.
    Also persists the LIVE trace fields (code, reads, steps, usage, declared_recipes,
    read_step/plan_step/code_step) so resume runs can re-score without re-spending
    API tokens."""
    path = _rep_path(capture_dir, cid, vlabel, rep)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bnames = case.get("behaviors") or list(BEHAVIORS)
    det = {b: bool(BEHAVIORS[b](trace, case)) for b in bnames}
    rec = {"case_id": cid, "variant": variant, "variant_label": vlabel, "rep": rep,
           "intent": case.get("intent") or _last_user_text(case["messages"]),
           "target_recipe": case.get("target_recipe"),
           "declared_recipes": trace.get("declared_recipes") or [],
           "outcome": trace["outcome"], "deterministic": det, "turns": trace["traj"],
           # Resume-needed fields (everything _agg + BEHAVIORS consumes from trace):
           "trace_fields": {
               "steps": trace.get("steps", []),
               "reads": trace.get("reads", []),
               "code": trace.get("code", ""),
               "read_step": trace.get("read_step"),
               "plan_step": trace.get("plan_step"),
               "code_step": trace.get("code_step"),
               "usage": trace.get("usage") or {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0},
           }}
    with open(path, "w") as f:
        json.dump(rec, f, indent=1, default=str)


def _load_traj(capture_dir: str, cid: str, vlabel: str, rep: int) -> Optional[dict]:
    """Load a previously persisted rep into the live `trace` shape (the dict
    rollout() returns). Returns None if not on disk OR if it was saved before
    the resume-needed fields were added (older runs). The trace will be
    re-rolled in that case."""
    path = _rep_path(capture_dir, cid, vlabel, rep)
    if not os.path.exists(path):
        return None
    try:
        rec = json.load(open(path))
    except Exception:  # noqa: BLE001
        return None
    tf = rec.get("trace_fields")
    if not tf:
        return None    # pre-resume schema; can't reuse without re-rolling
    return {
        "steps": tf.get("steps") or [],
        "reads": tf.get("reads") or [],
        "outcome": rec.get("outcome"),
        "code": tf.get("code") or "",
        "traj": rec.get("turns") or [],
        "declared_recipes": rec.get("declared_recipes") or [],
        "read_step": tf.get("read_step"),
        "plan_step": tf.get("plan_step"),
        "code_step": tf.get("code_step"),
        "usage": tf.get("usage") or {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0},
    }


def _agg(case: dict, traces: list, reps: int) -> dict:
    names = case.get("behaviors") or list(BEHAVIORS)
    n = len(traces) or 1
    rates = {b: round(sum(bool(BEHAVIORS[b](t, case)) for t in traces) / n, 3) for b in names}
    # Sum token usage across the cell's reps so a sweep can surface actual
    # cache-hit ratios (saved in the matrix-level summary by run.py).
    usage = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}
    for t in traces:
        u = t.get("usage") or {}
        for k in usage:
            usage[k] += int(u.get(k, 0) or 0)
    return {"rates": rates, "n": len(traces),
            "outcomes": _counter(t["outcome"] for t in traces),
            "usage": usage}


def run_case(case: dict, variant: dict, reps: int = 16, workers: int = 6,
             capture_dir: str | None = None) -> dict:
    """One (case, variant) cell at n reps. For whole sweeps prefer run_matrix (parallel)."""
    client = _client()
    system, tools = render_system(case, variant)
    model = os.environ.get("ABA_EVAL_MODEL") or case.get("model", "claude-haiku-4-5-20251001")
    env = case.get("env_stubs", {})
    cid, vlabel = case.get("id", "case"), _variant_label(variant)

    def one(i):
        # Case- or variant-level overrides for rollout caps so multi-stage
        # pipelines (Phase 6 composition trials) don't run out of code turns
        # before the second/third sub-Skill chain. Defaults match the
        # cold-start cases that drove the original cap design.
        _ms = int(case.get("max_steps") or variant.get("max_steps") or 10)
        _mc = int(case.get("max_code_turns") or variant.get("max_code_turns") or 6)
        t = rollout(client, model, system, case["messages"], tools, env,
                    max_steps=_ms, max_code_turns=_mc,
                    continue_after_plan=bool(variant.get("continue_after_plan", False)),
                    step_labeled_injection=bool(variant.get("step_labeled_injection", False)),
                    plan_recheck_steer=bool(variant.get("plan_recheck_steer", False)))
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
    # Resume mode: if a prior run was killed mid-sweep, pre-populate buckets
    # from already-on-disk rep files; the live phases below skip those reps
    # (no API call). Toggle via ABA_RESUME=1 (set by run.py --resume) — when
    # off, an existing rep file is overwritten (legacy behavior).
    resume = (capture_dir and os.environ.get("ABA_RESUME") == "1")
    resumed_count = 0
    pre_buckets: dict = {}
    if resume:
        for ci, cell in enumerate(cells):
            cid = cell["case"].get("id", "case")
            for rep in range(reps):
                t = _load_traj(capture_dir, cid, cell["vlabel"], rep)
                if t is not None:
                    pre_buckets.setdefault(ci, {})[rep] = t
                    resumed_count += 1
        if resumed_count:
            print(f"[run_matrix] RESUME: loaded {resumed_count} previously-completed "
                  f"reps from {capture_dir}", file=sys.stderr, flush=True)

    def already_done(ci: int, rep: int) -> bool:
        return resume and rep in pre_buckets.get(ci, {})

    # Warm-then-flood for prompt-cache efficiency: run rep 0 of EACH cell
    # serially in parallel (one per cell, distinct prefixes don't contend),
    # let those writes land, THEN fan out reps 1..N-1 across the worker pool.
    # Without this, the flat task list dispatched 12 reps of cell-0 at once
    # and they all raced past each other's cache writes — ~12x wasted writes
    # per cell. Toggle off with ABA_NO_WARMUP=1 for the legacy single-phase
    # behavior.
    warm = (os.environ.get("ABA_NO_WARMUP") != "1") and reps > 1
    warm_tasks = [(ci, 0) for ci in range(len(cells)) if not already_done(ci, 0)] if warm else []
    flood_start = 1 if warm else 0
    flood_tasks = [(ci, rep) for ci in range(len(cells))
                   for rep in range(flood_start, reps) if not already_done(ci, rep)]
    tasks = warm_tasks + flood_tasks    # legacy "tasks" still used for total count

    def run_one(task):
        ci, rep = task
        cell = cells[ci]
        case = cell["case"]
        try:
            _v = cell["variant"]
            _ms = int(case.get("max_steps") or _v.get("max_steps") or 10)
            _mc = int(case.get("max_code_turns") or _v.get("max_code_turns") or 6)
            t = rollout(client, os.environ.get("ABA_EVAL_MODEL") or case.get("model", "claude-haiku-4-5-20251001"), cell["system"],
                        case["messages"], cell["tools"], case.get("env_stubs", {}),
                        max_steps=_ms, max_code_turns=_mc,
                        continue_after_plan=bool(_v.get("continue_after_plan", False)),
                        step_labeled_injection=bool(_v.get("step_labeled_injection", False)),
                        plan_recheck_steer=bool(_v.get("plan_recheck_steer", False)))
        except Exception:  # noqa: BLE001 — one rollout dying must NOT nuke the whole sweep
            return ci, None
        if capture_dir:
            _persist_traj(capture_dir, case.get("id", "case"), cell["vlabel"], rep,
                          case, cell["variant"], t)
        return ci, t

    from concurrent.futures import as_completed
    buckets: dict = {}                                  # phase 2: fan out all rollouts
    # Seed with resumed reps so the aggregator sees them — but keep them in a
    # set-by-rep-index style so duplicates can't happen if a resumed cell also
    # gets re-rolled.
    for ci, reps_map in pre_buckets.items():
        buckets[ci] = list(reps_map.values())
    fails, done, total = 0, 0, len(tasks)
    # Continuous summary flush — re-aggregate + write _summary.json after each
    # cell completes its share so even a killed sweep has a usable partial
    # picture, and so the user can `tail -f` partial rates while a slow run is
    # still in flight.
    summary_path = os.path.join(capture_dir, "_summary.json") if capture_dir else None
    completed_per_cell = {ci: len(reps_map) for ci, reps_map in pre_buckets.items()}
    def _flush_partial_summary():
        if not summary_path: return
        snap: dict = {}
        for ci, cell in enumerate(cells):
            traces = buckets.get(ci, [])
            agg = _agg(cell["case"], traces, len(traces) or 1) if traces else {
                "rates": {}, "n": 0, "outcomes": {},
                "usage": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}}
            snap.setdefault(cell["case"]["id"], {})[cell["vlabel"]] = {
                **agg, "completed": len(traces), "target": reps}
        try:
            with open(summary_path, "w") as f:
                json.dump(snap, f, indent=1, default=str)
        except Exception:  # noqa: BLE001 — flush is best-effort
            pass
    _flush_partial_summary()    # initial snapshot incl. resumed reps
    print(f"[run_matrix] starting {total} rollouts ({len(cells)} cells x {reps} reps, "
          f"workers={workers}, warmup={'yes' if warm else 'no'}, "
          f"resumed={resumed_count})", file=sys.stderr, flush=True)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        # Phase A: warm — one rep per cell, in parallel across cells (distinct
        # prefixes, no contention). Wait for ALL to land before flood begins.
        if warm_tasks:
            warm_futs = [ex.submit(run_one, t) for t in warm_tasks]
            for fut in as_completed(warm_futs):
                ci, t = fut.result()
                done += 1
                if t is None:
                    fails += 1
                else:
                    buckets.setdefault(ci, []).append(t)
                if done % max(1, len(warm_tasks) // 3) == 0 or done == len(warm_tasks):
                    print(f"[run_matrix] warmup {done}/{len(warm_tasks)} ({fails} failed)",
                          file=sys.stderr, flush=True)
            _flush_partial_summary()    # warmup phase landed; flush once
        # Phase B: flood — remaining reps fanned out, hits the now-warm cache.
        futs = [ex.submit(run_one, t) for t in flood_tasks]
        flush_every = max(1, total // 10)   # ~10 snapshots over the flood phase
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
            if done % flush_every == 0:
                _flush_partial_summary()
    _flush_partial_summary()    # final snapshot
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
