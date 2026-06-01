"""Dispatch a Sonnet judge over persisted trajectories.

Reads on-disk trajectories from a `--capture` dir, asks Sonnet to score them
against the RUBRIC in judge.py, writes verdicts JSON next to the trajectories,
and prints a rollup. Sonnet is required (Haiku doesn't score reliably) — and
since the subscription OAuth path 429s for non-Haiku models, this script
ALWAYS uses the .env API key (`ABA_EVAL_CREDENTIAL=apikey` semantics).

Usage:
  # Pilot — judge 16 reps per cell, only 2 variants (cheap signal check)
  python judge_run.py --capture results/raw/<ts> \\
      --cells recipe_uptake__seurat_single_plan__current_go,recipe_uptake__seurat_single_plan__pf_restate_before_code \\
      --max-reps 16
  # Full sweep — all cells, all reps
  python judge_run.py --capture results/raw/<ts>

Resume: any cell with `judge_v2.json` already on disk is skipped (the
RUBRIC_VERSION is in the filename). Delete or bump version to re-judge.
"""
import argparse, json, os, sys, glob, time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from judge import RUBRIC, RUBRIC_VERSION, _rubric_text


def _client():
    """Sonnet judging requires the apikey path — OAuth 429s for non-Haiku."""
    from dotenv import load_dotenv
    load_dotenv("/workspace/aba/.env")
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("error: ANTHROPIC_API_KEY required in .env for Sonnet judging")
    return anthropic.Anthropic(api_key=key)


def _load_recipe_body(name: str) -> str | None:
    """Look up the actual recipe body via the live skills registry. Returns
    None if absent. The judge needs the BODY not just the name, otherwise
    Sonnet fills in 'best practices' from training prior and judges against
    a recipe that doesn't exist (verified Jun-01: matplotlib clause was
    hallucinated in 11/16 verdicts on scanpy_plan)."""
    if not name: return None
    try:
        sys.path.insert(0, "/workspace/aba/backend")
        import content.bio  # noqa: F401 — registers skill builders
        from core.skills import get_skill
        s = get_skill(name)
        return getattr(s, "body", None) if s else None
    except Exception:
        return None


def build_judge_messages(trajectories: list[dict], max_code_turns: int = 6) -> tuple[str, list[dict]]:
    """Format the trajectories as a single user message; instructions go in system.
    Returns (system, messages).

    Two methodology fixes (v3, 2026-06-01):
    1. Embed the recipe BODY (not just the name) for any trajectory with a
       target_recipe. Prevents the judge from hallucinating "best practices"
       constraints that aren't actually in the recipe.
    2. Tell the judge about the harness rollout-cap (max_code_turns). The
       trajectory may end at step N due to the cap; the judge should NOT
       interpret early termination as plan abandonment.
    """
    # Collect unique recipe names across the batch + load their bodies once.
    recipe_bodies: dict[str, str] = {}
    for t in trajectories:
        name = (t or {}).get("target_recipe")
        if name and name not in recipe_bodies:
            body = _load_recipe_body(name)
            if body:
                recipe_bodies[name] = body
    recipe_section = ""
    if recipe_bodies:
        parts = []
        for name, body in recipe_bodies.items():
            parts.append(f"### Recipe `{name}`:\n```\n{body}\n```")
        recipe_section = (
            "\n\nRECIPE BODIES (you MUST judge against THESE actual texts, not against "
            "your prior beliefs about what recipes 'usually' say):\n\n"
            + "\n\n".join(parts)
            + "\n\nIMPORTANT: when citing `evidence` for recipe-following, quote either "
            "(a) text from a trajectory turn or (b) text from these recipe bodies. Do NOT "
            "invent a recipe constraint — if a constraint you want to flag isn't in the "
            "recipe body above, don't flag it."
        )

    system = (
        "You are a STRICT evaluation judge for a bioinformatics agent. You read TRAJECTORY "
        "files (agent assistant blocks + stubbed tool_results) and score them against a "
        "fixed rubric, BLIND — you must IGNORE the `deterministic` and `outcome` fields and "
        "judge from the `turns` alone.\n\n"
        "Use ONLY:\n"
        "- intent — the user's request\n"
        "- target_recipe — the recipe name that fits this task (its BODY is below)\n"
        "- declared_recipes — recipes the agent named on plan-step `skill` fields\n"
        "- turns — the agent's actual blocks (text, tool_use with `input.code`) and stubbed tool_results\n\n"
        "**Harness rollout-cap context (read this carefully):** Trajectories were captured "
        f"under a max-code-turns cap of {max_code_turns}. A trajectory may TERMINATE because the cap "
        "was hit (agent emitted {max_code_turns} code-emitting turns then the harness stopped), NOT "
        "because the agent abandoned its plan. Do NOT call this 'drift', 'truncation', or 'plan "
        "abandonment'. Only mark plan_drift_and_recovery as drift if the agent explicitly STOPPED, "
        "PIVOTED to unrelated work, or wrote code that doesn't match the plan. A trajectory that "
        "ran 5-6 plan steps faithfully and then ended is 'no_drift', period.\n\n"
        "Score these dimensions (each gets a verdict from its allowed set, a confidence "
        "high|med|low, an `evidence` quote from the turns OR from the recipe body, and a one-line `why`):\n"
        f"{_rubric_text()}\n\n"
        "Use 'n/a' where a dimension doesn't apply (e.g. recipe_followed_in_code / method_validity / "
        "plan_drift_and_recovery when no analysis code was run; plan_quality when no present_plan; "
        "failure_honesty when nothing failed).\n\n"
        "Output: a JSON ARRAY, one object per trajectory in the order given, each shaped:\n"
        f'{{"case_id":..., "variant_label":..., "rep":..., "rubric_version":"{RUBRIC_VERSION}", '
        '"<dimension>": {"verdict":..., "confidence":..., "evidence":"<quote>", "why":...}, ...}}\n\n'
        "Return ONLY the JSON array — no prose around it, no markdown code fences."
        + recipe_section
    )
    # Pack trajectories into one user message. Trim turns' deterministic / outcome / declared_recipes
    # echoes that might bias the judge; keep only what the rubric actually needs.
    chunks = []
    for t in trajectories:
        # Strip blind-leak fields from the per-trajectory dict
        blind = {k: v for k, v in t.items()
                 if k not in ("deterministic", "outcome", "trace_fields")}
        chunks.append(json.dumps(blind, default=str))
    user_text = "TRAJECTORIES (judge all of them):\n\n[\n" + ",\n".join(chunks) + "\n]"
    messages = [{"role": "user", "content": [{"type": "text", "text": user_text}]}]
    return system, messages


def judge_cell(client, cell_dir: str, max_reps: int, model: str) -> tuple[str, list[dict] | None, str | None]:
    """Score one cell's reps. Returns (cell_label, verdicts|None, err|None)."""
    cell_label = os.path.basename(cell_dir)
    out_path = os.path.join(cell_dir, f"judge_{RUBRIC_VERSION}.json")
    if os.path.exists(out_path):
        try:
            return cell_label, json.load(open(out_path)), None    # resume hit
        except Exception:
            pass    # corrupt — re-judge
    rep_files = sorted(glob.glob(os.path.join(cell_dir, "rep*.json")))[:max_reps]
    if not rep_files:
        return cell_label, None, "no rep files"
    trajectories = [json.load(open(f)) for f in rep_files]
    system, messages = build_judge_messages(trajectories)
    # Cache_control marker on system so multiple cells benefit from prefix cache
    sys_list = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    try:
        r = client.messages.create(model=model, max_tokens=16000, system=sys_list, messages=messages)
    except Exception as e:  # noqa: BLE001
        return cell_label, None, f"{type(e).__name__}: {str(e)[:200]}"
    text = " ".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    # Strip optional markdown fences just in case
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("\n", 1)[0]
    try:
        verdicts = json.loads(text)
    except json.JSONDecodeError as e:
        return cell_label, None, f"JSON parse: {e} (first 200 chars: {text[:200]!r})"
    with open(out_path, "w") as f:
        json.dump(verdicts, f, indent=1, default=str)
    return cell_label, verdicts, None


def aggregate(all_verdicts: dict[str, list[dict]]):
    """Roll up verdicts per (cell, dimension). Cell label encodes case + variant."""
    dims = list(RUBRIC)
    print(f"\n=== judge rollup — rubric {RUBRIC_VERSION} ===")
    # Group by case_id, then variant
    by_case_variant: dict = {}
    for label, vs in all_verdicts.items():
        if not vs: continue
        # label is "<case_id>__<variant_label>" by directory convention
        for v in vs:
            cid = v.get("case_id", "?")
            vlbl = v.get("variant_label", "?")
            by_case_variant.setdefault(cid, {}).setdefault(vlbl, []).append(v)
    for cid in sorted(by_case_variant):
        print(f"\n--- {cid} ---")
        for vlbl in sorted(by_case_variant[cid]):
            verdicts = by_case_variant[cid][vlbl]
            n = len(verdicts)
            print(f"  [{vlbl}]  (n={n})")
            for dim in dims:
                dist: dict[str, int] = {}
                lowconf = 0
                for v in verdicts:
                    cell = v.get(dim) or {}
                    verdict = cell.get("verdict", "?")
                    dist[verdict] = dist.get(verdict, 0) + 1
                    if cell.get("confidence") == "low":
                        lowconf += 1
                summary = " ".join(f"{k}={c}" for k, c in sorted(dist.items()))
                tail = f"  ({lowconf} low-conf)" if lowconf else ""
                print(f"    {dim:28} {summary}{tail}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", required=True, help="results/raw/<ts> directory")
    ap.add_argument("--cells", default=None, help="comma-sep cell labels to judge (default: all)")
    ap.add_argument("--max-reps", type=int, default=16, help="cap reps judged per cell")
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    client = _client()
    all_cell_dirs = sorted(d for d in glob.glob(os.path.join(a.capture, "*")) if os.path.isdir(d))
    if a.cells:
        wanted = set(a.cells.split(","))
        all_cell_dirs = [d for d in all_cell_dirs if os.path.basename(d) in wanted]
    print(f"judging {len(all_cell_dirs)} cells from {a.capture}, max-reps={a.max_reps}, "
          f"model={a.model}, workers={a.workers}")
    all_verdicts: dict[str, list[dict]] = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(judge_cell, client, d, a.max_reps, a.model): os.path.basename(d)
                for d in all_cell_dirs}
        for fut in as_completed(futs):
            label = futs[fut]
            try:
                _, verdicts, err = fut.result()
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                verdicts = None
            if err:
                print(f"  ✗ {label}  {err}")
            else:
                print(f"  ✓ {label}  ({len(verdicts)} verdicts)")
                all_verdicts[label] = verdicts
    dt = time.time() - t0
    print(f"\nall cells judged in {dt:.1f}s")
    aggregate(all_verdicts)


if __name__ == "__main__":
    main()
