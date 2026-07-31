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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
