"""Path addressing: one resolver, honest answers (misc/paths.md, adopted plan).

Three live failures, one shape: four doors each re-derived "path → bytes"
with their own site handling and no durability answer, while the documented
canonical resolver (`locate_run_output`) sat unused by three of them. A wrong
answer here is a VALID path to real bytes — nothing raises — so the classes
are held closed structurally:

  census  — no new `site == "local"` literal or scratch scan outside the
            annotated allowlist (the fifth door never gets written);
  P3      — every find_files tier answers durability; ephemeral is named;
  P4      — the not-found error names the input that was actually missing;
  P1/P2   — registration resolves through the canonical resolver first;
            the durable run-key is captured site-agnostically.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.platform

# ── census: site-literals are looked up, never hardcoded ────────────────────
# Every `site == "local"` comparison in the addressing/exec surface must be
# here, with the reason it is CORRECT — or the census fails. "local" literals
# in unrelated vocabularies (execution mode strings) are out of scope by file.
_SCOPE = ("content/bio/tools/curation.py", "content/bio/lifecycle/runs.py",
          "content/bio/project_locate.py")
_ALLOW = {
    ("content/bio/lifecycle/runs.py", "note_run_site"):
        "records REMOTE placement only — 'local' is the default story",
    ("content/bio/lifecycle/runs.py", "_run_jobdirs"):
        "the canonical resolver's LOCAL-bytes tier; remote coverage is the "
        "resolver's remote tier, not this scan",
    ("content/bio/tools/curation.py", "_scratch_bases"):
        "an os.path.exists scan is inherently local; remote registration "
        "routes through the canonical resolver (P1)",
    ("content/bio/tools/curation.py", "_register_dataset_url"):
        "URL fetch lands in the project data dir only when the target is "
        "this controller; remote sites keep the CAS reference",
    ("content/bio/project_locate.py", "_live_sandbox_tier"):
        "branches local-vs-remote presentation; both branches answered",
}


def _site_literals(root: Path) -> list:
    out = []
    for rel in _SCOPE:
        src = (root / "backend" / rel).read_text()
        fn = "<module>"
        for i, ln in enumerate(src.splitlines(), 1):
            m = re.match(r"\s*def\s+(\w+)", ln)
            if m:
                fn = m.group(1)
            if re.search(r'''site.{0,12}==\s*['"]local['"]''', ln) or \
               re.search(r'''['"]local['"]\s*==.{0,12}site''', ln):
                out.append((rel, fn, i))
    return out


def test_census_every_site_literal_is_allowlisted():
    problems = [f"{rel}:{line} in {fn}() — a site literal outside the "
                f"allowlist: look the site up, or annotate WHY the gate is "
                f"correct (tests/test_path_resolution.py)"
                for rel, fn, line in _site_literals(ROOT)
                if (rel, fn) not in _ALLOW]
    assert not problems, "\n".join(problems)


def test_census_is_armed_and_scanner_catches_offenders(tmp_path):
    hits = _site_literals(ROOT)
    assert len(hits) >= 3, "census examined nothing — scope drifted"
    # scanner self-proof on a synthetic offender
    mod = tmp_path / "backend" / "content" / "bio" / "tools" / "curation.py"
    mod.parent.mkdir(parents=True)
    mod.write_text("def sneaky():\n    if k.get('site') == \"local\":\n        pass\n")
    for rel in _SCOPE[1:]:
        f = tmp_path / "backend" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("")
    got = _site_literals(tmp_path)
    assert ("content/bio/tools/curation.py", "sneaky", 2) in got


def test_allowlist_entries_still_exist():
    """A ratchet in both directions: a stale allowlist row (function renamed
    or fixed) must be pruned so the list stays honest."""
    live = {(rel, fn) for rel, fn, _ in _site_literals(ROOT)}
    stale = [k for k in _ALLOW if k not in live]
    assert not stale, f"allowlist rows with no matching literal: {stale}"


# ── P3: every find_files tier answers durability ────────────────────────────

def test_walk_match_tiers_carry_durability(tmp_path):
    from content.bio import project_locate as pl
    hits: list = []
    d = tmp_path / "x"; d.mkdir(); (d / "out.bin").write_bytes(b"z")
    pl._walk_match(d, "out.bin", hits, "live sandbox", cap=5)
    assert hits and hits[0].get("durability") == "ephemeral", hits
    assert "swept" in hits[0]["opens"].lower(), (
        f"an ephemeral address must SAY it dies: {hits[0]['opens']!r}")
    # the false-positive side: a durable tier must NOT cry ephemeral
    hits2: list = []
    pl._walk_match(d, "out.bin", hits2, "project data", cap=5)
    assert hits2 and hits2[0].get("durability") == "durable", hits2
    assert "swept" not in hits2[0]["opens"].lower()


# ── P4: the not-found error names the missing input ─────────────────────────

def _register_missing(monkeypatch, path):
    from content.bio.tools import curation as cu
    monkeypatch.setattr(cu, "_resolve_dataset_path", lambda p, ctx: p)
    return cu.register_dataset_tool({"path": path, "title": "t"},
                                    {"thread_id": "t"})


def test_notfound_error_branches_on_isabs(monkeypatch):
    out_abs = _register_missing(monkeypatch, "/definitely/not/here.bin")
    msg_abs = str(out_abs.get("error") or out_abs.get("note") or "")
    assert "site=" in msg_abs, (
        f"absolute-path miss must name site= (the input actually missing), "
        f"got: {msg_abs!r}")
    out_rel = _register_missing(monkeypatch, "not_here.bin")
    msg_rel = str(out_rel.get("error") or out_rel.get("note") or "")
    assert "absolute" in msg_rel.lower(), (
        f"relative-path miss keeps the absolute-path advice: {msg_rel!r}")
