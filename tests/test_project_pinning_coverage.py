"""Gate: every mutating HTTP endpoint pins its project per-request.

Background — modularity_audit.md §3.8 + refactoring2.md §5.2.
Project pinning via `_require_project_context()` was applied
voluntarily per-endpoint. Audit measurement: 4 of 30 mutation
endpoints in backend/main.py used `Depends(require_project)`; the
state-bleed footgun (request lands in wrong project) was still loaded.

This AST test walks every `@app.{post,patch,delete,put}` decorator in
main.py and every `@router.{...}` decorator in bio/web/routes.py and
fails if a mutation handler is missing the project-pin dependency.

Exemption rule: a handler is exempt if it appears in EXEMPT_ENDPOINTS
below. Exemptions are limited to genuinely-global endpoints (project
lifecycle, server-wide config) and must be justified in the table.

Adding a new endpoint:
  - Mutating (POST/PATCH/DELETE/PUT) endpoint on project data:
    add `Depends(require_project)` or use the function-form
    `_require_project_context(req.project_id)` if pid is in the
    request body.
  - Genuinely global (project create/open, server-wide):
    add the route path + method to EXEMPT_ENDPOINTS with a one-line
    justification.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
MAIN = BACKEND / "main.py"
BIO_ROUTES = BACKEND / "content" / "bio" / "web" / "routes.py"


# (path, method) tuples exempted from the pin requirement. Each MUST
# have a one-line justification: pinning is unsafe or pointless.
EXEMPT_ENDPOINTS: dict[tuple[str, str], str] = {
    # Project lifecycle — pin doesn't apply.
    ("/api/projects", "POST"): "create new project — no existing project to pin",
    ("/api/projects/{pid}/open", "POST"): "OPENS a project — sets the global, can't require it",
    ("/api/projects/{pid}", "PATCH"): "rename — pid is in path, pin is implicit",
    ("/api/projects/{pid}", "DELETE"): "delete — pid is in path, pin would be circular",
    ("/api/projects/{pid}/verify-recovery", "POST"): "explicit pid in path — recovery tooling",
    ("/api/projects/{pid}/materialize", "POST"): "explicit pid in path — admin tool",
    # Server-wide admin / global ops — not project-scoped.
    ("/api/admin/backfill-tool-result-thread", "POST"): "global migration script",
    ("/api/admin/purge_orphan_fills", "POST"): "global cleanup",
    ("/api/skills/reload", "POST"): "global skill catalog reload",
    ("/api/run-probe", "POST"): "diagnostic probe — no project ctx",
    ("/api/upload-url", "POST"): "pre-flight URL signing — no project mutation",
    # Body-sourced pid (handler calls _require_project_context(req.project_id) internally).
    # These don't carry the Depends signature but DO pin — call out one-by-one.
    ("/api/chat", "POST"): "body-sourced pid via _require_project_context(req.project_id)",
    ("/api/turns/{run_id}/resume", "POST"): "body-sourced pid",
    ("/api/turns/{run_id}/tool_result/{tool_use_id}", "POST"): "body-sourced pid",
    ("/api/turns/{run_id}/cancel", "POST"): "body-sourced pid",
    ("/api/threads", "POST"): "body-sourced pid",
    ("/api/upload", "POST"): "body-sourced pid (FormData)",
    ("/api/files/promote", "POST"): "body-sourced pid",
    ("/api/files/ai-summary", "POST"): "body-sourced pid",
    ("/api/history", "DELETE"): "body-sourced pid",
}


def _decorators_in(py: Path) -> list[tuple[int, str, str, str]]:
    """Walk every async/sync def's decorator list and return rows for
    those matching FastAPI route decorators.

    Returns: list of (lineno, path, method, func_name).
    """
    tree = ast.parse(py.read_text())
    out: list[tuple[int, str, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            if not isinstance(d, ast.Call):
                continue
            # Look for `app.METHOD(...)` or `router.METHOD(...)`.
            if not isinstance(d.func, ast.Attribute):
                continue
            method = d.func.attr.upper()
            if method not in ("POST", "PATCH", "DELETE", "PUT"):
                continue
            base = d.func.value
            if not isinstance(base, ast.Name) or base.id not in ("app", "router"):
                continue
            # First positional arg is the path string.
            if not d.args or not isinstance(d.args[0], ast.Constant):
                continue
            path = d.args[0].value
            if not isinstance(path, str):
                continue
            out.append((node.lineno, path, method, node.name))
    return out


def _has_require_project_dep(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True iff any positional/kwarg parameter has a default of
    `Depends(require_project)`. Handles `_pid: str = Depends(require_project)`."""
    args = list(func.args.args) + list(func.args.kwonlyargs)
    for a in args:
        pass  # default lookup is on args.defaults; do it below
    # walk args.defaults / args.kw_defaults
    defaults = list(func.args.defaults) + list(func.args.kw_defaults)
    for d in defaults:
        if d is None:
            continue
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "Depends":
            for inner in d.args:
                if isinstance(inner, ast.Name) and inner.id == "require_project":
                    return True
    return False


def _missing_pin(py: Path) -> list[tuple[int, str, str, str]]:
    """Return rows for mutating endpoints in `py` that lack
    Depends(require_project) AND aren't exempt."""
    tree = ast.parse(py.read_text())
    # Index funcs by lineno for lookup.
    funcs_by_line: dict[int, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs_by_line[node.lineno] = node

    missing: list[tuple[int, str, str, str]] = []
    for lineno, path, method, name in _decorators_in(py):
        if (path, method) in EXEMPT_ENDPOINTS:
            continue
        func = funcs_by_line.get(lineno)
        if func is None:
            continue
        if not _has_require_project_dep(func):
            missing.append((lineno, path, method, name))
    return missing


def test_main_py_mutations_all_pinned():
    missing = _missing_pin(MAIN)
    if missing:
        msg = "\n".join(
            f"  main.py:{lineno}  {method} {path}  ({name})"
            for lineno, path, method, name in missing
        )
        pytest.fail(
            f"{len(missing)} mutation endpoint(s) in main.py lack "
            f"Depends(require_project) and aren't in EXEMPT_ENDPOINTS:\n{msg}\n\n"
            f"Either add the dep, or — if genuinely global — add to "
            f"EXEMPT_ENDPOINTS with a one-line justification."
        )


def test_bio_routes_mutations_all_pinned():
    missing = _missing_pin(BIO_ROUTES)
    if missing:
        msg = "\n".join(
            f"  bio/web/routes.py:{lineno}  {method} {path}  ({name})"
            for lineno, path, method, name in missing
        )
        pytest.fail(
            f"{len(missing)} mutation endpoint(s) in bio/web/routes.py lack "
            f"Depends(require_project) and aren't in EXEMPT_ENDPOINTS:\n{msg}\n\n"
            f"Either add the dep, or add to EXEMPT_ENDPOINTS with a one-line "
            f"justification."
        )


def test_exemptions_are_real_endpoints():
    """Every EXEMPT_ENDPOINTS entry must correspond to a real endpoint —
    catches typos that would silently make the gate vacuous."""
    real = set()
    for py in (MAIN, BIO_ROUTES):
        for _lineno, path, method, _name in _decorators_in(py):
            real.add((path, method))
    bogus = set(EXEMPT_ENDPOINTS) - real
    assert not bogus, (
        f"EXEMPT_ENDPOINTS contains entries that don't match any real "
        f"endpoint (typos?): {sorted(bogus)}"
    )
