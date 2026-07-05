"""Tests for the focused-Result redesign — backend supersede behavior.

Verifies:
  - make_revision on the latest revision works (no supersede needed)
  - make_revision on a non-latest revision REFUSES without supersede_newer
  - make_revision on a non-latest with supersede_newer=True succeeds,
    marks the newer entries as status='superseded'
  - figure_history filters superseded entries by default; visible chain
    stays linear after the supersession
  - figure_history(include_superseded=True) shows all entries for debug

Run: .venv/bin/python tests/test_revisions_supersede.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_supersede_")
os.environ["ABA_DB_PATH"]   = str(Path(_tmp) / "ss.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]      = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]  = str(Path(_tmp) / "envs")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                  # noqa: E402
from core.graph import entities                         # noqa: E402
import content.bio  # noqa: F401, E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _make_seed(thread_id="thr_ss", y=1.0):
    """Create a real seed figure: run_python → pin_artifact → entity."""
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    code = (
        f"import matplotlib\nmatplotlib.use('Agg')\n"
        f"import matplotlib.pyplot as plt\n"
        f"plt.figure(); plt.plot([1,2,3],[{y},{y+1},{y+2}])\n"
        f"plt.savefig('seed.png'); plt.close('all')\n"
    )
    res = run_python({"code": code}, ctx={"thread_id": thread_id,
                                          "tool_use_id": f"tu_seed_{y}"})
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id=thread_id,
    )
    out = pin_artifact(res["exec_id"], "figure", 0,
                       wrap_in_result=False, thread_id=thread_id)
    return out["entity_id"]


def _revise(entity_id: str, y_scale: float, **kwargs):
    from content.bio.lifecycle.revisions import make_revision
    code = (
        f"import matplotlib\nmatplotlib.use('Agg')\n"
        f"import matplotlib.pyplot as plt\n"
        f"plt.figure(); plt.plot([1,2,3],[{y_scale*1},{y_scale*2},{y_scale*3}])\n"
        f"plt.savefig('rev.png'); plt.close('all')\n"
    )
    return make_revision(entity_id, code, **kwargs)


def test_revise_latest_no_supersede_needed():
    print("\n[1] revising the latest revision works without supersede_newer")
    init_db()
    a = _make_seed(thread_id="thr_ss_a", y=1.0)
    # First revision (off the latest = the seed itself) — should just work
    out = _revise(a, 10.0)
    check("new_entity_id returned", isinstance(out.get("new_entity_id"), str))
    check("superseded is empty", out.get("superseded") == [])


def test_revise_non_latest_refuses_without_flag():
    print("\n[2] revising a non-latest revision REFUSES (default behavior)")
    a = _make_seed(thread_id="thr_ss_b", y=2.0)
    b = _revise(a, 5.0)["new_entity_id"]   # chain: a -> b (b is latest)
    # Try to revise 'a' (the older one, not latest)
    try:
        _revise(a, 7.0)
        check("ValueError raised", False, "no exception")
    except ValueError as e:
        check("ValueError raised", True)
        check("error mentions newer entries",
              "newer" in str(e).lower(),
              f"got {str(e)[:80]}")
        check("error includes the newer id",
              b in str(e), f"got {str(e)[:120]}")


def test_revise_non_latest_supersede_newer():
    print("\n[3] supersede_newer=True allows it; marks newer as superseded")
    a = _make_seed(thread_id="thr_ss_c", y=3.0)
    b = _revise(a, 4.0)["new_entity_id"]   # chain: a -> b (latest)
    # Now revise 'a' with supersede_newer=True
    out = _revise(a, 9.0, supersede_newer=True)
    c = out["new_entity_id"]
    check("revision succeeded", isinstance(c, str))
    check("response.superseded includes b",
          b in (out.get("superseded") or []),
          f"got {out.get('superseded')}")
    # b's status is now 'superseded'
    b_rec = entities.get_entity(b)
    check("b is now status='superseded'",
          b_rec.get("status") == "superseded", f"got {b_rec.get('status')}")
    # a + c remain active
    check("a is still active",
          (entities.get_entity(a) or {}).get("status") == "active")
    check("c is active",
          (entities.get_entity(c) or {}).get("status") == "active")


def test_figure_history_filters_superseded():
    print("\n[4] figure_history hides superseded entries by default")
    a = _make_seed(thread_id="thr_ss_d", y=4.0)
    b = _revise(a, 5.0)["new_entity_id"]
    c = _revise(a, 9.0, supersede_newer=True)["new_entity_id"]
    # Now: chain (visible) = a -> c (b is superseded, dropped)
    from content.bio.graph.figure_history import figure_history
    chain = figure_history(a)
    chain_ids = [e["id"] for e in chain]
    check("default chain has 2 entries (a + c)", len(chain) == 2,
          f"got {chain_ids}")
    check("b is NOT in default chain", b not in chain_ids)
    check("c is the newest in chain", chain_ids[0] == c)
    check("a is the oldest in chain", chain_ids[-1] == a)
    # include_superseded=True shows all
    full = figure_history(a, include_superseded=True)
    full_ids = {e["id"] for e in full}
    check("include_superseded=True surfaces b",
          b in full_ids, f"got {full_ids}")


def test_supersede_cascade_multiple_newer():
    print("\n[5] supersede cascades to ALL newer-than-parent revisions")
    a = _make_seed(thread_id="thr_ss_e", y=5.0)
    b = _revise(a, 5.0)["new_entity_id"]
    c = _revise(b, 7.0)["new_entity_id"]   # chain: a -> b -> c (linear so far)
    # Now revise 'a' with supersede — both b AND c should be superseded
    out = _revise(a, 11.0, supersede_newer=True)
    sup = set(out.get("superseded") or [])
    check("both b and c marked superseded",
          {b, c}.issubset(sup), f"got {sup}")
    check("b status = superseded",
          (entities.get_entity(b) or {}).get("status") == "superseded")
    check("c status = superseded",
          (entities.get_entity(c) or {}).get("status") == "superseded")


def test_http_revise_non_latest_returns_400_with_newer_list():
    print("\n[6] HTTP /make_revision returns 400 with newer list when non-latest")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from content.bio.web.routes import router
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)

    a = _make_seed(thread_id="thr_ss_f", y=6.0)
    _revise(a, 5.0)
    # Try HTTP revise on a (not latest), no supersede_newer
    r = client.post(f"/api/entities/{a}/make_revision",
                    json={"modified_code": "plt.figure(); plt.savefig('x.png')"})
    check("HTTP 400", r.status_code == 400, f"got {r.status_code}: {r.text[:200]}")
    check("response mentions 'newer'",
          "newer" in r.text.lower(), f"got: {r.text[:200]}")


def test_make_revision_broadcasts_entity_updated():
    """Regression guard for 2026-06-07: without this broadcast, the UI's
    SSE listener never wakes and the chevrons stay stale until the user
    manually reloads the page. Verified by patching core.runtime.
    notifications.broadcast and asserting it's called with the right
    envelope on a real make_revision call."""
    print("\n[7] make_revision broadcasts entity_updated for SSE refresh")
    init_db()
    from core.runtime import notifications as _notif
    captured: list[dict] = []
    orig = _notif.broadcast
    _notif.broadcast = lambda ev: captured.append(ev)  # type: ignore[assignment]
    try:
        a = _make_seed(thread_id="thr_ss_broadcast", y=7.0)
        out = _revise(a, 6.0)
        new_id = out["new_entity_id"]
        # Find the entity_updated event for the new revision
        matched = [ev for ev in captured
                   if ev.get("type") == "entity_updated"
                   and ev.get("entity_id") == new_id]
        check("broadcast fired with entity_updated for new revision",
              len(matched) >= 1, f"captured events: {captured}")
        if matched:
            ev = matched[0]
            check("event carries reason='revision_created'",
                  ev.get("reason") == "revision_created", f"got {ev}")
            check("event carries wasRevisionOf=parent",
                  ev.get("wasRevisionOf") == a, f"got {ev}")
            check("event carries superseded=[] for first-revision case",
                  ev.get("superseded") == [], f"got {ev}")
    finally:
        _notif.broadcast = orig  # type: ignore[assignment]


def main() -> int:
    test_revise_latest_no_supersede_needed()
    test_revise_non_latest_refuses_without_flag()
    test_revise_non_latest_supersede_newer()
    test_figure_history_filters_superseded()
    test_supersede_cascade_multiple_newer()
    test_http_revise_non_latest_returns_400_with_newer_list()
    test_make_revision_broadcasts_entity_updated()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL REVISIONS-SUPERSEDE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
