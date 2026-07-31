"""Every scenario a live study RUNS must actually be decorated as one.

The studies build their run list as `[(fn._scenario, fn) for fn in [...]]`, so a
function in that list without `@scenario(...)` raises AttributeError at startup —
before any scenario runs. The whole study is dead, and because these are run by
hand rather than by CI, it stays dead until someone tries.

That is exactly what happened. A helper was inserted BETWEEN
`@scenario("mn_status_surfaces")` and its function, so the decorator landed on the
helper, `mn_status_surfaces` was left bare, and `regtest/datasets/multinode.py`
crashed on launch for every one of its 44 scenarios. It shipped that way because
the change was never run.

Static and instant — no substrate, no fixture, no LLM. The point is that this can
never again be discovered by a human trying to use the study.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
STUDIES = sorted((ROOT / "regtest" / "datasets").glob("*.py"))
LIST_RE = re.compile(r"\(fn\._scenario,\s*fn\)\s*for fn in")


def _decorated(tree: ast.Module) -> dict[str, bool]:
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = any(
                (isinstance(d, ast.Call) and getattr(d.func, "id", "") == "scenario")
                or getattr(d, "id", "") == "scenario"
                for d in node.decorator_list)
    return out


def _listed(src: str) -> list[str]:
    """Names inside the `[(fn._scenario, fn) for fn in [ ... ]]` list."""
    m = LIST_RE.search(src)
    if not m:
        return []
    tail = src[m.end():]
    depth, buf = 0, []
    for ch in tail:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth <= 0:
                break
        elif depth >= 1:
            buf.append(ch)
    body = "".join(buf)
    # strip comments so a commented-out name is not counted
    body = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    return [n for n in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", body)]


def _studies_with_lists():
    out = []
    for f in STUDIES:
        src = f.read_text(encoding="utf-8", errors="ignore")
        if LIST_RE.search(src):
            out.append((f, src))
    return out


def test_the_scanner_finds_a_study_with_a_run_list():
    """ARMED: a scanner that matches no study certifies every study correct."""
    found = _studies_with_lists()
    assert found, "no study exposes a `(fn._scenario, fn) for fn in [...]` list"
    f, src = found[0]
    assert len(_listed(src)) > 5, f"{f.name}: parsed {len(_listed(src))} names"


def test_the_scanner_catches_an_undecorated_entry():
    """ARMED the other way: prove the RULE fires on the exact shape that shipped —
    a decorator captured by a helper wedged in front of its function."""
    broken = (
        "def scenario(n):\n    return lambda f: f\n"
        "@scenario('mn_thing')\n"
        "def _helper(a):\n    return a\n"
        "def mn_thing(client, pid, tid):\n    return [], []\n"
        "x = [(fn._scenario, fn) for fn in [mn_thing]]\n"
    )
    dec = _decorated(ast.parse(broken))
    listed = _listed(broken)
    assert listed == ["mn_thing"], listed
    assert dec.get("mn_thing") is False, "the rule missed the real failure shape"


@pytest.mark.parametrize("path", [f for f, _ in _studies_with_lists()],
                         ids=lambda p: p.name)
def test_every_listed_scenario_is_decorated(path):
    src = path.read_text(encoding="utf-8", errors="ignore")
    dec = _decorated(ast.parse(src))
    listed = _listed(src)
    # Names in the list that are defined in this file must carry the decorator;
    # imported ones are out of scope for a single-file scan.
    bad = [n for n in listed if n in dec and not dec[n]]
    assert not bad, (
        f"{path.name}: listed for execution but not @scenario-decorated — the "
        f"study raises AttributeError at startup and NO scenario runs: {bad}")


@pytest.mark.parametrize("path", [f for f, _ in _studies_with_lists()],
                         ids=lambda p: p.name)
def test_every_listed_scenario_is_defined(path):
    """A name in the list that does not exist is a NameError at import — same
    blast radius, different exception."""
    src = path.read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(src)
    defined = set(_decorated(tree))
    imported = {a.asname or a.name
                for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names}
    imported |= {n.id for n in ast.walk(tree)
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    missing = [n for n in _listed(src) if n not in defined and n not in imported]
    assert not missing, f"{path.name}: listed but never defined: {missing}"


# ── a study must not reference a helper it does not define ───────────────────
#
# A slice-based edit removed `_reaped_as_orphan` while leaving three call sites,
# and the only thing that noticed was a 35-second live run against a real slurm
# fixture: three scenarios, each `✗ exception` on a NameError. These studies cost
# minutes per scenario and real substrate time, so a missing name must surface
# statically.
#
# Scope-aware ON PURPOSE. A first cut only collected MODULE-level definitions and
# reported twelve false positives — helpers imported inside a function body
# (`from core.jobs.weft_submitter import _site_platform_for`) and nested defs. A
# check with a dozen false alarms gets silenced, not fixed, so it has to model
# scope: for each function, module names PLUS everything bound anywhere inside it.


def _bound_in(node) -> set[str]:
    """Every name bound anywhere inside `node` — defs, imports, assignments,
    args, comprehension targets, with/except aliases. A superset is correct here:
    this check exists to catch a name bound NOWHERE."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
            a = getattr(n, "args", None)
            if a is not None:
                for arg in (*a.args, *a.posonlyargs, *a.kwonlyargs):
                    out.add(arg.arg)
                for extra in (a.vararg, a.kwarg):
                    if extra is not None:
                        out.add(extra.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                out.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, ast.Lambda):
            for arg in (*n.args.args, *n.args.posonlyargs, *n.args.kwonlyargs):
                out.add(arg.arg)
    return out


def _called_private(node) -> set[str]:
    """`_foo(...)` call targets — the private helpers a study defines for itself.
    Attribute calls (`mod._foo`) live in someone else's namespace."""
    return {n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id.startswith("_") and not n.func.id.startswith("__")}


def _undefined_private(src: str) -> list[str]:
    """Private helpers called but bound NOWHERE in the module.

    A deliberate SUPERSET of "bound in an enclosing scope": names bound anywhere
    in the file count as known. That misses the narrow case of a helper defined
    in one function and called from another — a real NameError, but one the first
    live run catches anyway.

    The precise version needs parent-scope tracking, and two attempts without it
    produced twelve false positives (a helper imported inside a function body,
    then a nested def using its parent's import). A check with a dozen false
    alarms gets switched off rather than fixed, so the superset — which still
    catches the defect that shipped, a name bound nowhere at all — is the better
    instrument.
    """
    tree = ast.parse(src)
    known = _bound_in(tree)
    return sorted(c for c in _called_private(tree) if c not in known)


def test_the_undefined_helper_rule_fires():
    """ARMED on the exact shape that shipped — called, defined nowhere."""
    assert _undefined_private("def f():\n    return _gone(1)\n") == ["_gone"]


def test_the_rule_does_not_fire_on_the_false_positives_it_first_produced():
    """CEILING: every shape the two earlier versions wrongly flagged — a helper
    imported INSIDE a function body, a nested def, and a nested function using its
    PARENT's import. A check that cries wolf gets turned off, not fixed."""
    ok = ("def f():\n"
          "    from core.jobs.weft_submitter import _site_platform_for\n"
          "    return _site_platform_for('hpc')\n"
          "def g():\n"
          "    def _inner(x):\n        return x\n"
          "    return _inner(1)\n"
          "def h():\n"
          "    from mod import _outer\n"
          "    def _nested():\n        return _outer(2)\n"
          "    return _nested()\n")
    assert _undefined_private(ok) == []


@pytest.mark.parametrize("path", STUDIES, ids=lambda p: p.name)
def test_no_study_calls_an_undefined_private_helper(path):
    missing = _undefined_private(path.read_text(encoding="utf-8", errors="ignore"))
    assert not missing, (
        f"{path.name}: calls private helper(s) it never defines — a NameError at "
        f"scenario runtime, i.e. after the fixture and the substrate are already "
        f"paid for: {missing}")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
