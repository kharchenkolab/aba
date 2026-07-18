"""Gate: every mutating HTTP endpoint pins its project per-request.

Background — modularity_audit.md §3.8 + refactoring2.md §5.2.
Project pinning via `_require_project_context()` was applied
voluntarily per-endpoint. Audit measurement: 4 of 30 mutation
endpoints in backend/main.py used `Depends(require_project)`; the
state-bleed footgun (request lands in wrong project) was still loaded.

This AST test walks every mutating FastAPI route decorator across ALL
web-route files and fails if a mutation handler is missing the
project-pin dependency.

Item 2 hardening (2A.0): route files are DISCOVERED (main.py + every
*.py under core/web/ and content/bio/web/), not hardcoded, and the
decorator base may be ANY name (not just `app`/`router`) — so moving a
route onto a differently-named `APIRouter` in a new module can NEVER
make this gate silently vacuous. The companion `test_route_table.py`
snapshots the live route set so a move that drops/renames a path fails
loudly too.

Exemption rule: a handler is exempt if it appears in EXEMPT_ENDPOINTS
below. Exemptions are limited to genuinely-global endpoints (project
lifecycle, server-wide config) or body-sourced-pid handlers, and must
be justified in the table.

Runs under pytest (CI) OR standalone (`python tests/test_project_pinning_coverage.py`)
since the base env may lack pytest.
"""
from __future__ import annotations

import ast
from pathlib import Path

try:
    import pytest
    pytestmark = pytest.mark.platform
    def _fail(msg): pytest.fail(msg)
except ImportError:  # standalone (base env has no pytest)
    pytest = None
    def _fail(msg): raise AssertionError(msg)

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
MAIN = BACKEND / "main.py"


def _route_files() -> list[Path]:
    """All files that may register FastAPI routes: the composition root
    plus every module under core/web/ and content/bio/web/. Discovered,
    not hardcoded, so route extraction into new router modules stays
    covered (2A.0). Legacy single-file bio routes.py is included if present."""
    files = [MAIN]
    for base in (BACKEND / "core" / "web", BACKEND / "content" / "bio" / "web"):
        if base.is_dir():
            files += [p for p in base.rglob("*.py") if p.name != "__init__.py"]
    legacy = BACKEND / "content" / "bio" / "web" / "routes.py"
    if legacy.exists() and legacy not in files:
        files.append(legacy)
    # stable order, de-duped
    return sorted(set(files))


# main.py is always scanned; bio route files kept as a named list for the
# per-group failure messages (back-compat with the original two tests).
_BIO_ROUTES_PKG = BACKEND / "content" / "bio" / "web" / "routes"
_BIO_ROUTES_FILE = BACKEND / "content" / "bio" / "web" / "routes.py"
if _BIO_ROUTES_PKG.is_dir():
    BIO_ROUTES_FILES = sorted(
        p for p in _BIO_ROUTES_PKG.glob("*.py") if p.name != "__init__.py"
    )
else:
    BIO_ROUTES_FILES = [_BIO_ROUTES_FILE]


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
    ("/api/settings/credential", "POST"): "server-wide credential — not project-scoped",
    ("/api/settings/environment", "POST"): "user-scope discovery.env_gate preference — not project-scoped",
    ("/api/admin/backfill-tool-result-thread", "POST"): "global migration script",
    ("/api/admin/purge_orphan_fills", "POST"): "global cleanup",
    ("/api/skills/reload", "POST"): "global skill catalog reload",
    ("/api/run-probe", "POST"): "diagnostic probe — no project ctx",
    ("/api/feedback/client-context", "POST"): "stashes a transient global browser snapshot for bug reports — no project ctx",
    ("/pagoda3-api/agent/stream", "POST"): "co-hosted pagoda3 copilot proxy — uses ABA's global Anthropic credential, not project-scoped",
    # Compute-site (remote cluster) management — a site is a deployment-wide
    # connection SHARED across every project, never owned by one, so there is
    # no project to pin. All mutate the global site registry / SSH trust.
    ("/api/compute/preflight", "POST"): "site connectivity preflight — global cluster op",
    ("/api/compute/hostkey", "POST"): "accept a cluster SSH host key — global trust store",
    ("/api/compute/keysetup", "POST"): "install the cluster access key — global",
    ("/api/compute/probe", "POST"): "probe a prospective site — global cluster op",
    ("/api/compute/sites", "POST"): "connect/register a site — global site registry",
    ("/api/compute/sites/{name}/verify", "POST"): "re-verify a registered site — global",
    ("/api/compute/sites/{name}/reprobe", "POST"): "re-probe a site's capabilities — global",
    ("/api/compute/sites/{name}", "PATCH"): "edit a site's connection config — global",
    ("/api/compute/sites/{name}", "DELETE"): "disconnect a site — global site registry",
    ("/api/compute/sites/{name}/gc", "POST"): "reclaim a site's disk — global cluster op",
    # Module (capability pack) lifecycle — deployment-wide, not project-scoped.
    ("/api/modules/{module_id}/mode", "POST"): "set a module's mode — global module registry",
    ("/api/modules/{module_id}/enable", "POST"): "enable a module — global",
    ("/api/modules/{module_id}/disable", "POST"): "disable a module — global",
    ("/api/modules/{module_id}/retry", "POST"): "retry a module install — global",
    # Server-wide LLM config + credential setup — not project-scoped
    # (sibling /api/settings/credential is already exempt above).
    ("/api/settings/llm", "POST"): "server-wide LLM model config — not project-scoped",
    ("/api/settings/llm/ping", "POST"): "probe the server LLM credential — not project-scoped",
    ("/api/settings/credential/oauth/start", "POST"): "begin server OAuth — global credential",
    ("/api/settings/credential/oauth/submit", "POST"): "complete server OAuth — global credential",
    # Body-sourced pid (handler calls _require_project_context(req.project_id) internally).
    # These don't carry the Depends signature but DO pin — call out one-by-one.
    ("/api/chat", "POST"): "body-sourced pid via _require_project_context(req.project_id)",
    ("/api/turns/{run_id}/resume", "POST"): "body-sourced pid",
    ("/api/turns/{run_id}/tool_result/{tool_use_id}", "POST"): "body-sourced pid",
    ("/api/turns/{run_id}/cancel", "POST"): "body-sourced pid",
    ("/api/files/promote", "POST"): "body-sourced pid",
    ("/api/files/ai-summary", "POST"): "body-sourced pid",
}


def _decorators_in(py: Path) -> list[tuple[int, str, str, str]]:
    """Walk every async/sync def's decorator list and return rows for
    those matching FastAPI route decorators.

    Matches `<name>.METHOD("/path", ...)` for ANY `<name>` (app, router, or a
    module-specific APIRouter like `chat_router`) — over-inclusion is safe (we
    only scan web-route files), under-inclusion would silently drop the pin
    gate for a moved route.

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
            if not isinstance(d.func, ast.Attribute):
                continue
            method = d.func.attr.upper()
            if method not in ("POST", "PATCH", "DELETE", "PUT"):
                continue
            # base must be a bare Name (a router/app object), not e.g. a call chain.
            if not isinstance(d.func.value, ast.Name):
                continue
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


def test_all_mutations_pinned():
    """Every mutating route across ALL web-route files (discovered, not
    hardcoded) is pinned or explicitly exempt."""
    rows: list[tuple[Path, int, str, str, str]] = []
    for py in _route_files():
        for lineno, path, method, name in _missing_pin(py):
            rows.append((py, lineno, path, method, name))
    if rows:
        msg = "\n".join(
            f"  {py.relative_to(ROOT)}:{lineno}  {method} {path}  ({name})"
            for py, lineno, path, method, name in rows
        )
        _fail(
            f"{len(rows)} mutation endpoint(s) lack Depends(require_project) and "
            f"aren't in EXEMPT_ENDPOINTS:\n{msg}\n\n"
            f"Either add the dep, use body-sourced _require_project_context() (and "
            f"exempt it), or — if genuinely global — add to EXEMPT_ENDPOINTS with a "
            f"one-line justification."
        )


def test_exemptions_are_real_endpoints():
    """Every EXEMPT_ENDPOINTS entry must correspond to a real endpoint —
    catches typos that would silently make the gate vacuous."""
    real = set()
    for py in _route_files():
        for _lineno, path, method, _name in _decorators_in(py):
            real.add((path, method))
    bogus = set(EXEMPT_ENDPOINTS) - real
    assert not bogus, (
        f"EXEMPT_ENDPOINTS contains entries that don't match any real "
        f"endpoint (typos?): {sorted(bogus)}"
    )


if __name__ == "__main__":
    print(f"scanning {len(_route_files())} web-route file(s):")
    for p in _route_files():
        print("  ", p.relative_to(ROOT))
    test_all_mutations_pinned()
    print("PASS test_all_mutations_pinned")
    test_exemptions_are_real_endpoints()
    print("PASS test_exemptions_are_real_endpoints")
    print("all passed")
