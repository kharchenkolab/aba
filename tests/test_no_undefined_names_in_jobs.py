"""No function in the jobs layer may read a name it never binds.

WHAT THIS CAUGHT. `_build_detached_task` referenced `pid` — which is a local of
a DIFFERENT method on the same class. Python only notices at runtime, so every
detached job died at submit with

    background submit failed: name 'pid' is not defined

i.e. 100% of cluster background jobs, from the commit that added a `data_dir`
line until a live smoke session hit it. Nothing in the unit suite executes that
builder against a real submit, so nothing saw it. The second such outage in two
days from the same shape: a name that exists somewhere nearby but not here.

WHY A LINTER-STYLE TEST RATHER THAN MORE UNIT TESTS. Coverage of this class is
not achievable by example — you cannot write a test per name. It is a PROPERTY
of the module, cheap to check over the AST, and it holds for names nobody has
thought of yet. (pyflakes/ruff would do this and more; neither is installed in
the guard interpreter, so the check is inlined.)
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

JOBS = Path(__file__).resolve().parents[1] / "backend" / "core" / "jobs"
_BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__",
                                  "__package__", "__spec__"}


def _module_level_names(tree: ast.AST) -> set[str]:
    """Names visible at module scope.

    This used `ast.walk(tree)`, which descends INTO function bodies — so a local
    like `pid = params.get(...)` inside one method was collected as a
    module-level name and then allowed in every other function. That is exactly
    the bug this file exists to catch, so the guard could not catch it: the
    red-proof passed. Recurse through module-level control flow (if/try/with)
    but STOP at any function boundary."""
    out: set[str] = set()

    def visit(nodes):
        for n in nodes:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(n.name)
                continue                      # do NOT descend into its body
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                out.update((a.asname or a.name).split(".")[0] for a in n.names)
            elif isinstance(n, ast.Assign):
                for tgt in n.targets:
                    out.update(s.id for s in ast.walk(tgt) if isinstance(s, ast.Name))
            elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
                out.update(s.id for s in ast.walk(n.target) if isinstance(s, ast.Name))
            elif isinstance(n, ast.Global):
                out.update(n.names)
            for field in ("body", "orelse", "finalbody", "handlers"):
                sub = getattr(n, field, None)
                if isinstance(sub, list):
                    visit(sub)
    visit(tree.body)
    return out


def _bound_in(fn: ast.AST) -> set[str]:
    """Every name this function could legitimately read: its params, anything it
    assigns, loop/with/except targets, comprehension vars, and nested defs."""
    b: set[str] = set()
    a = fn.args
    for grp in (a.posonlyargs, a.args, a.kwonlyargs):
        b.update(x.arg for x in grp)
    if a.vararg:
        b.add(a.vararg.arg)
    if a.kwarg:
        b.add(a.kwarg.arg)
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                b.update(s.id for s in ast.walk(t) if isinstance(s, ast.Name))
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
            b.update(s.id for s in ast.walk(n.target) if isinstance(s, ast.Name))
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            b.update(s.id for s in ast.walk(n.target) if isinstance(s, ast.Name))
        elif isinstance(n, ast.comprehension):
            b.update(s.id for s in ast.walk(n.target) if isinstance(s, ast.Name))
        elif isinstance(n, ast.withitem) and n.optional_vars is not None:
            b.update(s.id for s in ast.walk(n.optional_vars) if isinstance(s, ast.Name))
        elif isinstance(n, ast.ExceptHandler) and n.name:
            b.add(n.name)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            b.update((x.asname or x.name).split(".")[0] for x in n.names)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            b.update(n.names)
        elif isinstance(n, ast.Lambda):
            la = n.args
            for grp in (la.posonlyargs, la.args, la.kwonlyargs):
                b.update(x.arg for x in grp)
    return b


def _loads_in_own_scope(fn: ast.AST):
    """Name reads belonging to THIS function, not to a nested def/lambda.

    Walking blindly attributed a nested helper's parameter to its enclosing
    function — two false positives on the first run. A guard that cries wolf is
    worse than no guard: it gets muted, and then it is not guarding anything."""
    nested = {id(n) for top in ast.iter_child_nodes(fn)
              for n in ast.walk(top)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))}
    skip: set[int] = set()
    for top in ast.walk(fn):
        if id(top) in nested:
            for sub in ast.walk(top):
                skip.add(id(sub))
    for n in ast.walk(fn):
        if id(n) in skip:
            continue
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            yield n


@pytest.mark.parametrize("path", sorted(p.name for p in JOBS.glob("*.py")))
def test_every_function_binds_the_names_it_reads(path):
    src = (JOBS / path).read_text()
    tree = ast.parse(src)
    outer = _module_level_names(tree) | _BUILTINS
    problems: list[str] = []
    fns = [n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not fns:
        return          # a re-export __init__ has nothing to check; the suite-
                        # level coverage test below is where arming belongs
    for fn in fns:
        # a nested def can read its enclosing function's names
        enclosing: set[str] = set()
        for other in fns:
            if other is not fn and any(x is fn for x in ast.walk(other)):
                enclosing |= _bound_in(other)
        allowed = outer | enclosing | _bound_in(fn)
        for n in _loads_in_own_scope(fn):
            if n.id not in allowed:
                problems.append(f"{path}:{n.lineno} {fn.name}() reads unbound {n.id!r}")
    assert not problems, (
        "these reads raise NameError the first time the line executes:\n  "
        + "\n  ".join(sorted(set(problems))))


def test_the_scan_actually_covers_the_jobs_layer():
    """ARMED, at the level where arming means something.

    Per-FILE arming was wrong: `__init__.py` legitimately defines no functions,
    so it failed for being itself. But dropping the check entirely would let the
    whole scan degrade to nothing — a parse change, a moved package — and report
    green. Assert the layer-wide totals instead."""
    files = sorted(JOBS.glob("*.py"))
    total = 0
    for f in files:
        tree = ast.parse(f.read_text())
        total += sum(1 for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    assert len(files) >= 5, f"only {len(files)} files found in {JOBS}"
    assert total >= 50, f"only {total} functions scanned — the guard has gone blind"
    names = {f.name for f in files}
    for must in ("weft_submitter.py", "runner.py", "submit.py", "detached_entry.py"):
        assert must in names, f"{must} is not being scanned"
