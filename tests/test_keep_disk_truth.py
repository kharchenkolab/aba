"""F10 (PK-approved): EXPLICIT keep = DISK TRUTH. The automatic harvest
allowlist decides what gets TRACKED, never what CAN be kept.

Guards (set_keep_decision → _retain_run_outputs → retention.retain selection):
  1. A GLOB include resolves against the run's real on-disk sandbox listing —
     untracked files (extension outside the harvest allowlist) matching the
     glob enter the retain selection as concrete rels.
  2. A broad glob whose UNTRACKED matches exceed the size gate is NOT silently
     kept — it is surfaced as `size_gated` (files + bytes) instead.
  3. A LITERAL include that exists on disk is reported via `disk_seen` (the
     keep tool then does NOT warn NOT-COVERED for it); a literal that exists
     NOWHERE stays unmatched.
  4. The automatic path is untouched: with no includes, nothing untracked
     enters the selection (allowlist governs automatic harvest only).

Run: python tests/test_keep_disk_truth.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_dtk_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "k.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import main  # noqa: E402,F401  (loads the app + type registry)
from core.graph._schema import init_db  # noqa: E402
from core.graph.entities import create_entity  # noqa: E402
import core.compute.retention as retmod  # noqa: E402
from content.bio.lifecycle import runs as runsmod  # noqa: E402

init_db()

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def _mk_run(sandbox: Path) -> str:
    rid = create_entity(entity_type="analysis", title="DT Run",
                        metadata={"thread_id": "t", "run_state": "open",
                                  "weft_targets": ["krn_x"]})
    rid = rid if isinstance(rid, str) else rid["id"]
    from core.graph.entities import update_entity
    update_entity(rid, artifact_path=str(sandbox))
    return rid


def _patch(monkey_calls: list):
    """retention.retain capture + empty prior state + no remote inventory."""
    orig = (retmod.retain, retmod.retained)
    retmod.retain = (lambda target, **kw:
                     monkey_calls.append((target, kw)) or {"state": "pinned-pending"})
    retmod.retained = lambda **kw: []
    return orig


def test_glob_disk_truth_and_gate_and_literals():
    sandbox = Path(tempfile.mkdtemp(prefix="dtk_sbx_"))
    # untracked extensions (outside the harvest allowlist):
    (sandbox / "out").mkdir()
    (sandbox / "out" / "alpha.dat").write_bytes(b"x" * 1000)
    (sandbox / "out" / "beta.dat").write_bytes(b"y" * 2000)
    (sandbox / "huge.blob").write_bytes(b"z" * 10)      # size faked via gate patch
    (sandbox / "named.custom").write_bytes(b"n" * 10)
    rid = _mk_run(sandbox)

    calls: list = []
    orig = _patch(calls)
    # shrink the size gate so huge.blob (10 bytes) exceeds it for check 2
    import core.data.datasets as ds
    orig_gate = ds.FETCH_GUARDRAIL_BYTES
    try:
        # ── 1. glob matches untracked disk files → in the retain selection ──
        info = runsmod.set_keep_decision(rid, keep=["out/*.dat"])
        sel = sorted(sum((kw["include"] for _t, kw in calls), []))
        check("glob-matched untracked rels entered the retain selection",
              "out/alpha.dat" in sel and "out/beta.dat" in sel, str(sel))
        check("disk_kept reported", set(info.get("disk_kept") or [])
              >= {"out/alpha.dat", "out/beta.dat"}, str(info.get("disk_kept")))

        # ── 2. size gate: matches above the gate are surfaced, not kept ──
        ds.FETCH_GUARDRAIL_BYTES = 5          # huge.blob (10b) now exceeds it
        calls.clear()
        info2 = runsmod.set_keep_decision(rid, keep=["*.blob"])
        gated = info2.get("size_gated") or []
        sel2 = sorted(sum((kw["include"] for _t, kw in calls), []))
        check("oversized glob NOT auto-kept", "huge.blob" not in sel2, str(sel2))
        check("oversized glob surfaced as size_gated",
              bool(gated) and gated[0]["glob"] == "*.blob"
              and gated[0]["files"] == 1, str(gated))

        # ── 3. literal on disk → disk_seen; nonexistent literal → not seen ──
        ds.FETCH_GUARDRAIL_BYTES = orig_gate
        calls.clear()
        info3 = runsmod.set_keep_decision(rid, keep=["named.custom",
                                                     "ghost.file"])
        seen = set(info3.get("disk_seen") or [])
        check("literal on disk reported as disk_seen", "named.custom" in seen,
              str(seen))
        check("nonexistent literal NOT disk_seen", "ghost.file" not in seen,
              str(seen))
        sel3 = sorted(sum((kw["include"] for _t, kw in calls), []))
        check("literal include still enters the selection (existing behavior)",
              "named.custom" in sel3, str(sel3))
    finally:
        ds.FETCH_GUARDRAIL_BYTES = orig_gate
        retmod.retain, retmod.retained = orig


def test_automatic_path_untouched():
    """No includes → nothing untracked enters the selection (allowlist still
    governs AUTOMATIC harvest; disk truth applies only to explicit keeps)."""
    sandbox = Path(tempfile.mkdtemp(prefix="dtk_auto_"))
    (sandbox / "stray.dat").write_bytes(b"s" * 100)
    rid = _mk_run(sandbox)
    calls: list = []
    orig = _patch(calls)
    try:
        runsmod.set_keep_decision(rid, drop=["nothing-real"])
        sel = sorted(sum((kw["include"] for _t, kw in calls), []))
        check("untracked disk file NOT kept without an explicit include",
              "stray.dat" not in sel, str(sel))
    finally:
        retmod.retain, retmod.retained = orig


def main_():
    test_glob_disk_truth_and_gate_and_literals()
    test_automatic_path_untouched()
    print(f"\n{'ALL PASS' if not _failures else f'FAILED ({len(_failures)})'}")
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main_())
