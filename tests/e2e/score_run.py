"""Auto-scorer for one agent run — turns a per-run event JSONL (the untruncated
ABA_TURN_LOG_DIR/<run_id>.jsonl) + optionally the harness transcript into the
metrics that discriminate prompt/recipe strategies.

Why this exists: behaviour we care about (recipe uptake, fabrication, scope-creep,
avoidable-bug rate) is intermittent, so strategies must be compared on RATES over
repeated runs — which needs each run scored mechanically, not read by eye.

Usage:
    score_run.py <run.jsonl> [--transcript <scenario.log>] [--scenario <name>]
Returns a JSON dict of metrics.
"""
from __future__ import annotations
import json
import re
import sys
from collections import Counter
from pathlib import Path


# ── scenario idiom rubric ────────────────────────────────────────────────────
# Best-effort regex checks over the concatenated run_python/run_r code. Each is a
# (label, should-be-present regex) — adherence to the recipe's correct idioms vs
# the from-memory fumbles we keep seeing. Extend per scenario as we learn more.
_RUBRIC: dict[str, list[tuple[str, str]]] = {
    "_scanpy_common": [
        ("mito_prefix_robust", r"\.upper\(\)|str\.upper|\bvar_names\b.*[Mm][Tt][-_]|startswith\(\s*\(?\s*['\"](?i:mt)[-_]"),
        ("figdir_set", r"sc\.settings\.figdir|figdir\s*="),
        ("rank_genes_df_helper", r"sc\.get\.rank_genes_groups_df|rank_genes_groups_df\("),
    ],
}
_SCANPY_SCENARIOS = {
    "scanpy_single", "scanpy_annotate", "scrna_cluster_de", "de_wrong_method",
    "trajectory_paga",
}

# Per-scenario acceptable recipe(s) — for the selection-accuracy signal the
# model opinions asked for (retrieval-miss vs selection-error vs application-
# error). A SET because some tasks have >1 defensible recipe (e.g. de_wrong_method:
# the scRNA marker recipe for Wilcoxon, OR the bulk-DE recipe whose scope-guard
# states the single-sample n=1 caveat). NB: only meaningful for arms that READ
# (control/forced_triage/decision_record); inject arms surface the body without a
# read, so correct_recipe_read=False there is expected — judge those by whether
# the RIGHT recipe was injected (a retrieval check) + application idioms.
_CORRECT_RECIPE = {
    "scanpy_single":     {"scrna-qc-clustering"},
    "scanpy_annotate":   {"scrna-qc-clustering", "annotate-celltype-scrna", "bp-annotation"},
    "scrna_cluster_de":  {"scrna-qc-clustering", "bp-differential-expression"},
    "de_wrong_method":   {"scrna-qc-clustering", "bulk-rnaseq-de", "bp-differential-expression"},
    "trajectory_paga":   {"bp-trajectory-inference"},
    "scatac":            {"bp-atac", "scvi-multivi-atac"},
    "citeseq_multimodal": {"bp-cite-seq", "scvi-totalvi-citeseq"},
}

# ── anti-patterns (scope-creep / fabrication signatures over the code) ────────
_HARDCODED_CELLTYPE = re.compile(
    r"\{\s*['\"]?\d+['\"]?\s*:\s*['\"](?:CD4|CD8|T[ _]cell|B[ _]cell|NK|Mono|Dendritic|Platelet)",
    re.I,
)
_SUMMARY_DOC = re.compile(r"summary[_ ](?:document|report|figure)|executive[_ ]summary|comprehensive[_ ]report", re.I)
_FABRICATE = re.compile(
    r"np\.random\.(?:negative_binomial|binomial|poisson|lognormal|gamma|normal|choice)\s*\(|"
    r"synthetic|simulat(?:e|ed|ion)|representative (?:data|dataset)|mimic",
    re.I,
)
_ERR_TOKEN = re.compile(r"Traceback|Error|Exception", re.I)


def _is_error_result(res: dict) -> bool:
    if not isinstance(res, dict):
        return False
    if res.get("returncode") not in (0, None):
        return True
    if "error" in res or res.get("status") == "error":
        return True
    st = res.get("stderr") or ""
    return bool(_ERR_TOKEN.search(st)) and "Warning" not in st[:40]


def score(jsonl_path, transcript_path: str | None = None,
          scenario: str | None = None) -> dict:
    paths = [jsonl_path] if isinstance(jsonl_path, (str, Path)) else list(jsonl_path)
    events = []
    for p in paths:
        if not Path(p).exists():
            continue
        for line in Path(p).read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                pass

    tools = Counter()
    recipes_read: list[str] = []
    code_chunks: list[str] = []
    n_code_cells = 0
    n_errors = 0
    errors_before_first_success = 0
    seen_success = False
    blank_fig_warnings = 0
    fetch_warnings = 0
    recipe_hint_emitted = False
    figs = tables = 0
    plots_harvested = 0

    for ev in events:
        t = ev.get("type")
        if t == "tool_start":
            nm = ev.get("name") or "?"
            tools[nm] += 1
            inp = ev.get("input") or {}
            if nm == "read_skill" and isinstance(inp, dict) and inp.get("name"):
                recipes_read.append(inp["name"])
            if nm in ("run_python", "run_r"):
                n_code_cells += 1
                code = inp.get("code") if isinstance(inp, dict) else None
                if code:
                    code_chunks.append(str(code))
        elif t == "tool_result":
            res = ev.get("result") or {}
            nm = ev.get("name") or ""
            if isinstance(res, dict):
                if res.get("recipe_hint"):
                    recipe_hint_emitted = True
                fw = res.get("figure_warnings")
                if fw:
                    blank_fig_warnings += len(fw)
                if res.get("fetch_warning"):
                    fetch_warnings += 1
                plots_harvested += len(res.get("plots") or [])
                if nm in ("run_python", "run_r"):
                    if _is_error_result(res):
                        n_errors += 1
                        if not seen_success:
                            errors_before_first_success += 1
                    else:
                        seen_success = True
        elif t == "entity_registered":
            e = ev.get("entity") or {}
            if e.get("type") in ("figure", "view"):
                figs += 1
            elif e.get("type") in ("table", "result"):
                tables += 1

    code = "\n".join(code_chunks)
    recipe_read = bool(recipes_read)

    # scenario idiom adherence (best-effort)
    rubric = []
    if scenario in _SCANPY_SCENARIOS:
        rubric = _RUBRIC["_scanpy_common"]
    idioms = {label: bool(re.search(rx, code)) for label, rx in rubric}

    metrics = {
        "scenario": scenario,
        # recipe uptake — the headline lever
        "recipe_read": recipe_read,
        "recipes_read": recipes_read,
        "recipe_hint_emitted": recipe_hint_emitted,
        "recipe_hint_ignored": recipe_hint_emitted and not recipe_read,
        # selection accuracy (meaningful for READ arms; see _CORRECT_RECIPE note)
        "correct_recipe": sorted(_CORRECT_RECIPE.get(scenario or "", set())) or None,
        "correct_recipe_read": (bool(_CORRECT_RECIPE.get(scenario or "", set())
                                & set(recipes_read))
                                if _CORRECT_RECIPE.get(scenario or "") else None),
        # cost / effort
        "n_code_cells": n_code_cells,
        "n_tool_calls": sum(tools.values()),
        "tools": dict(tools),
        # avoidable-bug / friction rate
        "n_errors": n_errors,
        "errors_before_first_success": errors_before_first_success,
        "blank_fig_warnings": blank_fig_warnings,   # blank-fig guardrail fires
        "fetch_warnings": fetch_warnings,           # fetch-fail guardrail fires
        # scope-creep / fabrication signatures
        "hardcoded_celltype_dict": bool(_HARDCODED_CELLTYPE.search(code)),
        "produced_summary_doc": bool(_SUMMARY_DOC.search(code)),
        "fabrication_signature": bool(_FABRICATE.search(code)),
        # output volume
        "figs_registered": figs,
        "tables_registered": tables,
        "plots_harvested": plots_harvested,
        # recipe idiom adherence (scanpy scenarios)
        "idioms": idioms,
    }

    # outcome + cost from the harness transcript, if given
    if transcript_path and Path(transcript_path).exists():
        ttxt = Path(transcript_path).read_text()
        m = re.search(r"AUTOCHECK \[(PASS|CHECK)\].*?missing=(\[[^\]]*\])", ttxt)
        if m:
            metrics["autocheck_pass"] = m.group(1) == "PASS"
            metrics["missing_keywords"] = m.group(2)
        m = re.search(r"elapsed:\s*(\d+)s", ttxt)
        if m:
            metrics["elapsed_s"] = int(m.group(1))
        m = re.search(r"guide model:\s*(\S+)", ttxt)
        if m:
            metrics["model"] = m.group(1)
    return metrics


def score_scenario(base_dir: str, scenario: str) -> dict:
    """Score a whole scenario run from its harness sidecar — merges every turn's
    JSONL (main + continue-nudges) and folds in the transcript outcome."""
    base = Path(base_dir)
    meta_p = base / "transcripts" / f"{scenario}.meta.json"
    transcript = base / "transcripts" / f"{scenario}.log"
    jsonls: list[str] = []
    arm = "control"
    if meta_p.exists():
        meta = json.loads(meta_p.read_text())
        arm = meta.get("arm", "control")
        tl = Path(meta.get("turnlog") or (base / "turnlog"))
        jsonls = [str(tl / f"{rid}.jsonl") for rid in meta.get("run_ids", [])]
    m = score(jsonls, str(transcript) if transcript.exists() else None, scenario)
    m["arm"] = arm
    return m


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("usage: score_run.py <run.jsonl> [--transcript <log>] [--scenario <name>]")
        return 2
    jsonl = args[0]
    transcript = scenario = None
    for i, a in enumerate(args):
        if a == "--transcript" and i + 1 < len(args):
            transcript = args[i + 1]
        elif a == "--scenario" and i + 1 < len(args):
            scenario = args[i + 1]
    print(json.dumps(score(jsonl, transcript, scenario), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
