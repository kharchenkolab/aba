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


# ── P1/P2: registration resolves through the canonical resolver ─────────────

def test_resolve_dataset_path_consults_canonical_resolver_first(monkeypatch, tmp_path):
    from content.bio.tools import curation as cu
    import content.bio.lifecycle.runs as runs
    f = tmp_path / "made.bin"; f.write_bytes(b"right")
    monkeypatch.setattr(runs, "active_run_id", lambda tid: "run_1")
    monkeypatch.setattr(runs, "locate_run_output",
                        lambda rid, name, **k: {"local_path": str(f),
                                                "target": "krn_9",
                                                "rel": "made.bin"})
    got = cu._resolve_dataset_path("made.bin", {"thread_id": "t"})
    assert got == str(f), (
        f"canonical answer ignored — a scratch scan can outrank it again: {got}")


def test_resolve_dataset_path_no_run_falls_to_ranked_scan(monkeypatch):
    """The most common registration (uploads, out-of-run) has no run — the
    ranked scan must serve it exactly as before (degenerate shape)."""
    from content.bio.tools import curation as cu
    import content.bio.lifecycle.runs as runs
    monkeypatch.setattr(runs, "active_run_id", lambda tid: None)
    monkeypatch.setattr(runs, "locate_run_output",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("resolver consulted with no run")))
    out = cu._resolve_dataset_path("nope.bin", {"thread_id": "t"})
    assert out.endswith("nope.bin")


def test_run_key_captured_via_resolver_for_nonlocal_kernel(monkeypatch, tmp_path):
    """F3's lost handle: a file born on a site-targeted kernel gets its
    durable (run, rel) key from the canonical resolver — the local prefix
    scan cannot see it. Guard proven red pre-P2."""
    from content.bio.tools import curation as cu
    import content.bio.lifecycle.runs as runs
    f = tmp_path / "remote_born.bin"; f.write_bytes(b"x")
    monkeypatch.setattr(runs, "active_run_id", lambda tid: "run_2")
    monkeypatch.setattr(runs, "locate_run_output",
                        lambda rid, name, **k: {"target": "krn_site7",
                                                "rel": "remote_born.bin",
                                                "local_path": None})
    import core.compute.adapter as ad
    class _C:
        def sync_call(self, *a, **k): return {"kernels": []}
        def __getattr__(self, n): return lambda *a, **k: {}
    monkeypatch.setattr(ad, "get_compute", lambda: _C())
    monkeypatch.setattr(cu, "_weft_ingest", lambda *a, **k: {}, raising=False)
    md = {}
    cu._capture_run_key(str(f), md, "th")
    assert md.get("run_key") == {"run": "krn_site7", "rel": "remote_born.bin"}, (
        f"durable key not captured for a non-local kernel: {md.get('run_key')}")


# ── origin: agent-stated provenance, structurally required ───────────────────

def _register_tmp(monkeypatch, tmp_path, **extra):
    from content.bio.tools import curation as cu
    f = tmp_path / "d.csv"; f.write_text("a,b\n1,2\n")
    monkeypatch.setattr(cu, "_capture_run_key",
                        lambda *a, **k: None)
    return cu.register_dataset_tool(
        {"title": "t", "path": str(f), **extra}, {"thread_id": "t"})


def test_origin_kind_is_validated_and_recorded(monkeypatch, tmp_path):
    from core.graph.entities import get_entity
    bad = _register_tmp(monkeypatch, tmp_path, origin="somewhere")
    assert "origin must be one of" in str(bad.get("error") or ""), bad
    ok = _register_tmp(monkeypatch, tmp_path, origin="derived",
                       source="run ana_123")
    assert ok.get("provenance") == "derived", ok
    md = (get_entity(ok["dataset_id"]) or {}).get("metadata") or {}
    assert md.get("origin_kind") == "derived", md


def test_unstated_origin_is_loud_never_silent(monkeypatch, tmp_path):
    out = _register_tmp(monkeypatch, tmp_path)
    assert out.get("provenance") == "unstated", out
    assert "PROVENANCE UNSTATED" in (out.get("note") or ""), (
        f"silent omission — the nag is the structural half of the ask: "
        f"{out.get('note')!r}")


def test_origin_mismatch_is_flagged(monkeypatch, tmp_path):
    """Authored claim vs mechanical evidence: 'upload' bytes carrying a
    kernel run_key contradict — flag, never silently prefer either side."""
    from content.bio.tools import curation as cu
    f = tmp_path / "d.csv"; f.write_text("x\n")
    monkeypatch.setattr(cu, "_capture_run_key",
                        lambda abspath, md, tid=None:
                        md.__setitem__("run_key", {"run": "krn_1", "rel": "d.csv"}))
    out = cu.register_dataset_tool(
        {"title": "t", "path": str(f), "origin": "upload"}, {"thread_id": "t"})
    assert "double-check" in (out.get("note") or ""), out


def test_run_key_capture_resolves_ambient_run_when_none_active(monkeypatch, tmp_path):
    """Link-1 of the live remote-wing red: a no-plan thread has no Run, so
    kernel-start target recording no-op'd and capture had nothing to resolve.
    Capture must mirror keep_outputs' F11: resolve-or-create the ambient run,
    backfill the thread's kernel target, then resolve."""
    from content.bio.tools import curation as cu
    import content.bio.lifecycle.runs as runs
    import content.bio.lifecycle.registry as reg
    f = tmp_path / "b.bin"; f.write_bytes(b"x")
    monkeypatch.setattr(runs, "active_run_id", lambda tid: None)
    monkeypatch.setattr(reg, "_ensure_analysis",
                        lambda foc, plan, tid: "run_ambient")
    recorded: list = []
    monkeypatch.setattr(runs, "record_weft_target",
                        lambda rid, t: recorded.append((rid, t)))
    import core.exec.kernels as kmod
    class _Pool:
        def peek(self, tid, lang):
            return (type("S", (), {"kernel_id": "krn_site9"})()
                    if lang == "python" else None)
    monkeypatch.setattr(kmod, "get_pool", lambda: _Pool())
    monkeypatch.setattr(runs, "locate_run_output",
                        lambda rid, name, **k: {"target": "krn_site9",
                                                "rel": "b.bin"}
                        if rid == "run_ambient" else None)
    md = {}
    cu._capture_run_key(str(f), md, "th_norun")
    assert ("run_ambient", "krn_site9") in recorded, (
        "kernel target never backfilled onto the ambient run")
    assert md.get("run_key") == {"run": "krn_site9", "rel": "b.bin"}, md
