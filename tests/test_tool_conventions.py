"""Tool-catalog convention guard (docs/arch/tool_conventions.md). Enumerates the LIVE
aba_core catalog and checks each tool's param names + verb prefix against the canonical
conventions.

REPORT-ONLY today (`ENFORCE=False`): it PRINTS violations so the reorg (P2 params, P5
renames) can drive off the exact list, and never fails CI yet. Flip ENFORCE=True after
P2/P5 land so a future tool that breaks convention fails.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

ENFORCE = True    # P2 param normalization landed → structural rules now enforced.
# (Semantic-misnomer renames — list_capabilities→search_capabilities etc. — are P5;
#  they have valid prefixes so aren't caught here. P5 adds a misnomer denylist.)

# ---- canonical rules (mirror docs/arch/tool_conventions.md) ----
VERB_PREFIXES = ("search_", "list_", "get_", "read_", "describe_", "inspect_",
                 "create_", "register_", "open_", "make_", "propose_",
                 "update_", "set_", "run_", "fetch_", "view_",
                 "add_", "remove_", "annotate_", "archive_", "close_", "delete_",
                 "resolve_", "promote_", "pin_", "reproduce_", "rebuild_",
                 "diff_", "export_", "restart_", "check_", "cancel_",
                 "present_", "ask_", "build_", "write_", "edit_", "find_",
                 "ensure_", "import_", "lookup_", "keep_", "evict_")
# Deliberate exceptions (keep-list) — not required to match any rule.
KEEP = {"Skill", "run_python", "run_r", "present_plan", "ask_clarification",
        "view_artifact", "view_file", "describe_tool"}
# Typed-id params allowed where the type genuinely constrains the argument.
TYPED_IDS = {"result_id", "dataset_id", "reference_id", "exec_id", "member_id", "job_id"}
# Params that LOOK like an entity id / path / query concept but use a wrong name.
ID_VIOLATORS = {"figure_id": "entity_id"}
PATH_VIOLATORS = {"file_path": "path"}                 # input path only
TYPE_VIOLATORS = {"entity_type": "type"}
LIMIT_VIOLATORS = {"max_results": "limit"}
# Tools with two params meaning ONE concept (query/name, name/capability, …).
TWIN_PARAMS = {"search_pypi": ("query", "name"), "read_capability": ("name", "capability")}
# filename used as an INPUT path (should be `path`); fetch_url.filename is a dest (OK).
FILENAME_AS_PATH = {"read_csv_info"}


def _catalog():
    """name -> [param names] for every registered aba_core tool."""
    captured = {}

    class FakeMCP:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    import content.bio  # noqa: F401
    mcp = FakeMCP()
    for modname in ("simple", "ctx_read", "curation", "discovery", "file_io", "plan_etc",
                    "run_exec", "revisions", "cells", "entity_ops", "feedback", "viewers", "jobs"):
        mod = __import__(f"content.bio.mcp_servers.aba_core.tools.{modname}", fromlist=["x"])
        for r in [f for f in dir(mod) if f.startswith("register_")]:
            try:
                getattr(mod, r)(mcp)
            except Exception:
                pass
    return {n: [p for p in inspect.signature(f).parameters if p not in ("aba_ctx_id", "ctx")]
            for n, f in captured.items()}


def find_violations():
    cat = _catalog()
    v = {"param_id": [], "param_path": [], "param_type": [], "param_limit": [],
         "twin_param": [], "filename_path": [], "verb_prefix": []}
    for name, params in sorted(cat.items()):
        if name in KEEP:
            continue
        for p in params:
            if p in ID_VIOLATORS:
                v["param_id"].append(f"{name}.{p} → {ID_VIOLATORS[p]}")
            if p in PATH_VIOLATORS:
                v["param_path"].append(f"{name}.{p} → {PATH_VIOLATORS[p]}")
            if p in TYPE_VIOLATORS:
                v["param_type"].append(f"{name}.{p} → {TYPE_VIOLATORS[p]}")
            if p in LIMIT_VIOLATORS:
                v["param_limit"].append(f"{name}.{p} → {LIMIT_VIOLATORS[p]}")
        if name in TWIN_PARAMS and all(t in params for t in TWIN_PARAMS[name]):
            v["twin_param"].append(f"{name}{TWIN_PARAMS[name]} → keep one")
        if name in FILENAME_AS_PATH and "filename" in params:
            v["filename_path"].append(f"{name}.filename → path")
        if not any(name.startswith(pre) for pre in VERB_PREFIXES):
            v["verb_prefix"].append(name)
    return cat, v


def test_report_conventions():
    cat, v = find_violations()
    total = sum(len(x) for x in v.values())
    print(f"\n=== tool-convention report: {len(cat)} tools, {total} violations ===")
    for cat_name, items in v.items():
        if items:
            print(f"\n[{cat_name}] ({len(items)})")
            for it in items:
                print(f"   {it}")
    if ENFORCE:
        assert total == 0, f"{total} convention violations (see report above)"


if __name__ == "__main__":
    test_report_conventions()
    print("\nok  tool-convention report (report-only mode)")
