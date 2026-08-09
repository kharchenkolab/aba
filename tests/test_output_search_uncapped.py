"""Resolving a run output must not depend on HOW MANY runs the project has.

Live, 2026-08-08: a viewer link 404'd — "no file matching … in this project" —
for a store sitting intact and readable on its site. The resolver probed
candidate runs ONE AT A TIME, so it capped the probe list at four; the owning
run was seventh of eight. A hard-coded `[:4]` decided that a file does not
exist, silently: no log, no truncation flag, `None` meaning both "no such
output" and "I stopped looking".

The fix inverts the cost shape so no cap is needed: membership is ONE batched
read of the terminal receipts (recorded knowledge, no site round-trip), and the
expensive per-run resolve runs only where the batch cannot prove absence —
the receipt names the file, is truncated, or does not exist (a kernel records
its receipt only at stop, so a LIVE kernel has none and only the live tier can
answer for it).

THE property: a project with N remote-producing runs resolves an output owned
by the k-th run identically for every k. The shipped code passed at k <= 4 and
failed at k = 5 — no fixture had five remote runs, which is why 42 scenarios
never saw it. This file tests k = 1, 7, 20 at N = 20.

Also guarded, because each is how the fix could quietly rot:
  * a run whose COMPLETE receipt does not name the file is NEVER individually
    probed (the batch is the point — O(matches), not O(runs) round-trips);
  * a TRUNCATED receipt and a MISSING receipt are both "absence unproven" and
    must be confirmed, never skipped;
  * two owners of one bare name stay ambiguous (None) — and the candidates are
    SURFACED via project_run_output_matches, so the refusal can say so.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.bio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

NAME = "processed.data.zarr"


@pytest.fixture(autouse=True)
def _empty_index():
    """These are unit tests of the SCAN — the path taken when the address
    index has nothing. The scan now backfills the index on every confirmed
    hit, so without this reset each test inherits its predecessors' rows and
    resolves from the index instead (first seen as three 'failures' that were
    the index working as designed, 2026-08-08). Index-present behaviour is
    tests/test_output_addr_index.py's job."""
    from core.graph import _schema, output_addr
    _schema.init_db()
    with _schema._conn() as c:
        output_addr._ensure(c)
        c.execute("DELETE FROM output_addr")
    yield


def _wire(monkeypatch, n_runs, owners, *, receipts="complete", live=()):
    """A project with `n_runs` analyses (run_1 oldest … run_N newest), each with
    one weft target t_<k>. `owners` hold NAME remotely. `receipts` shapes the
    batch reply: complete | truncated | missing. `live` runs have NO receipt.
    Returns a call log of per-run locate calls (the expensive path)."""
    from content.bio.lifecycle import runs as R
    from core.compute import retention

    ents = [{"id": f"run_{k}", "type": "analysis",
             "metadata": {"weft_targets": [f"t_{k}"]}}
            for k in range(1, n_runs + 1)]
    # list_entities returns oldest-first; the code reverses to newest-first.
    monkeypatch.setattr(R, "list_entities",
                        lambda type_filter=None, include_archived=False: list(ents))

    def inventories(targets):
        out = {}
        for t in targets:
            k = int(t.split("_")[1])
            rid = f"run_{k}"
            if rid in live:
                out[t] = {"error": "data.missing",
                          "detail": f"no inventory recorded for {t}"}
                continue
            files = [{"path": "scaffold/driver.py", "bytes": 10, "mtime": 1}]
            # "truncated" simulates the file falling PAST the recording budget:
            # the receipt is flagged truncated and does NOT name it — the only
            # shape in which the truncated branch is load-bearing. (A truncated
            # receipt that happens to name the file confirms via the name
            # match, and a test built that way passed with the truncation
            # branch deleted — proven, then fixed, 2026-08-08.)
            if rid in owners and receipts != "truncated":
                files.append({"path": f"{NAME}/zarr.json", "bytes": 999, "mtime": 1})
            entry = {"files": files}
            if receipts == "truncated":
                entry["truncated"] = True
            out[t] = entry
        return {"inventories": out}

    monkeypatch.setattr(retention, "inventories", inventories)

    locate_calls: list = []

    def locate(rid, name, match="name", remote=True, **kw):
        locate_calls.append((rid, remote))
        if remote is False:
            return None                          # nothing local anywhere
        if rid in owners and name == NAME:
            return {"locality": "remote", "site": "siteA", "size": 4096,
                    "kind": "dir", "target": f"t_{rid.split('_')[1]}"}
        return None

    monkeypatch.setattr(R, "locate_run_output", locate)
    return locate_calls


# ── THE property: N-independence ─────────────────────────────────────────────

@pytest.mark.parametrize("k", [1, 7, 20])
def test_the_kth_runs_output_resolves_regardless_of_k(monkeypatch, k):
    """k=7 IS the live failure: with the [:4] cap it returned None and the
    user's click 404'd on a file that existed."""
    from content.bio.lifecycle import runs as R
    _wire(monkeypatch, 20, owners={f"run_{k}"})
    got = R._locate_project_run_output(NAME)
    assert got == (f"run_{k}", "siteA", 4096, True), (
        f"output owned by run {k}/20 did not resolve — "
        f"resolution depends on the project's size again")


def test_max_runs_is_inert(monkeypatch):
    """The old knob must not be able to reintroduce the bug through a caller
    that still passes it."""
    from content.bio.lifecycle import runs as R
    _wire(monkeypatch, 20, owners={"run_18"})
    assert R._locate_project_run_output(NAME, max_runs=3) is not None


# ── cost shape: batch decides, locate confirms only where needed ─────────────

def test_non_owners_with_complete_receipts_are_never_probed(monkeypatch):
    """ARMED against the O(N) regression. 20 runs, one owner: exactly ONE
    remote locate call. A complete receipt that does not name the file proves
    absence — probing anyway would be the per-candidate scan creeping back,
    and with it the pressure to cap."""
    from content.bio.lifecycle import runs as R
    calls = _wire(monkeypatch, 20, owners={"run_7"})
    R._locate_project_run_output(NAME)
    remote_probes = [rid for rid, remote in calls if remote is not False]
    assert remote_probes == ["run_7"], remote_probes


def test_a_TRUNCATED_receipt_is_absence_unproven(monkeypatch):
    """Receipts are budgeted (`max_entries`); a truncated one that does not
    name the file must be CONFIRMED, not trusted — the file may be past the
    recording budget."""
    from content.bio.lifecycle import runs as R
    _wire(monkeypatch, 6, owners={"run_2"}, receipts="truncated")
    assert R._locate_project_run_output(NAME) == ("run_2", "siteA", 4096, True)


def test_a_LIVE_kernel_with_no_receipt_is_still_found(monkeypatch):
    """Kernels record their receipt at STOP. A store produced by a running
    kernel has no receipt row at all — the batch says data.missing — and only
    the per-run live tier can answer. Skipping receipt-less candidates would
    make every live kernel's outputs unresolvable, which is EXACTLY the shape
    of the store that 404'd."""
    from content.bio.lifecycle import runs as R
    calls = _wire(monkeypatch, 8, owners={"run_6"}, live={"run_6"})
    assert R._locate_project_run_output(NAME) == ("run_6", "siteA", 4096, True)
    remote_probes = [rid for rid, remote in calls if remote is not False]
    assert "run_6" in remote_probes


def test_a_dead_batch_verb_degrades_to_confirming_everything(monkeypatch):
    """WIDE: an old substrate without the batch (or a raising adapter) must
    degrade to per-run confirmation — slower, never wrong."""
    from content.bio.lifecycle import runs as R
    from core.compute import retention
    _wire(monkeypatch, 6, owners={"run_5"})

    def boom(targets):
        raise RuntimeError("no batch verb")
    monkeypatch.setattr(retention, "inventories", boom)
    assert R._locate_project_run_output(NAME) == ("run_5", "siteA", 4096, True)


# ── ambiguity: refuse, and SAY SO ────────────────────────────────────────────

def test_two_owners_of_one_name_stay_ambiguous(monkeypatch):
    """Run A's output must never silently answer a request that could be run
    B's — unchanged from the old contract."""
    from content.bio.lifecycle import runs as R
    _wire(monkeypatch, 10, owners={"run_3", "run_8"})
    assert R._locate_project_run_output(NAME) is None


def test_the_ambiguous_candidates_are_SURFACED(monkeypatch):
    """The other half the old code lacked: None meant both "no such file" and
    "two files" — indistinguishable to the 404 that renders it. The refusing
    surface can now name the candidates."""
    from content.bio.lifecycle import runs as R
    _wire(monkeypatch, 10, owners={"run_3", "run_8"})
    got = R.project_run_output_matches(NAME)
    assert sorted(r for r, _s in got) == ["run_3", "run_8"]
    assert all(s == "siteA" for _r, s in got)


def test_no_owner_resolves_to_None_with_no_candidates(monkeypatch):
    """CEILING: genuinely-absent stays absent, and the candidate list is empty
    rather than inventing anything."""
    from content.bio.lifecycle import runs as R
    _wire(monkeypatch, 10, owners=set())
    assert R._locate_project_run_output(NAME) is None
    assert R.project_run_output_matches(NAME) == []


# ── shapes ────────────────────────────────────────────────────────────────────

def test_a_store_is_matched_by_its_member_paths(monkeypatch):
    """A directory store appears in a receipt as MEMBERS (`<name>/zarr.json`),
    never as a bare entry — the pre-filter must match the prefix or every store
    lookup degrades to the confirm-everything path."""
    from content.bio.lifecycle import runs as R
    calls = _wire(monkeypatch, 12, owners={"run_9"})
    R._locate_project_run_output(NAME)
    assert [rid for rid, rem in calls if rem is not False] == ["run_9"]


def test_a_local_copy_of_the_OWNER_wins_without_any_remote_confirm(monkeypatch):
    """CEILING: the owner's local copy (fetched mirror, harvest copy) answers
    before the ~12 s remote confirm ever runs — armed by a confirm that
    raises."""
    from content.bio.lifecycle import runs as R
    _wire(monkeypatch, 5, owners={"run_2"})

    def locate(rid, name, match="name", remote=True, **kw):
        if remote is False:
            return ({"local_path": "/x/mirror/" + NAME, "size": 7}
                    if rid == "run_2" else None)
        raise AssertionError("paid the remote confirm despite a local copy")
    monkeypatch.setattr(R, "locate_run_output", locate)
    assert R._locate_project_run_output(NAME) == ("run_2", "local", 7, False)


def test_a_TARGETLESS_local_run_still_wins(monkeypatch):
    """A purely local run (no weft target, no receipt anywhere) is outside the
    candidacy machinery entirely; its local tiers must stay reachable or every
    local-substrate project loses name resolution."""
    from content.bio.lifecycle import runs as R
    _wire(monkeypatch, 4, owners=set())
    from core.graph import entities  # noqa: F401 — for parallel shape only
    ents = [{"id": f"run_{k}", "type": "analysis",
             "metadata": {"weft_targets": [f"t_{k}"]}} for k in (1, 2)]
    ents.append({"id": "run_local", "type": "analysis", "metadata": {}})
    monkeypatch.setattr(R, "list_entities",
                        lambda type_filter=None, include_archived=False: ents)

    def locate(rid, name, match="name", remote=True, **kw):
        if remote is False and rid == "run_local":
            return {"local_path": "/x/out/" + NAME, "size": 3}
        return None
    monkeypatch.setattr(R, "locate_run_output", locate)
    assert R._locate_project_run_output(NAME) == ("run_local", "local", 3, False)


def test_a_stray_copy_in_a_NON_owner_no_longer_shadows(monkeypatch):
    """The F1 doctrine (paths.md): a same-named file lying in a run whose
    COMPLETE receipt proves it never produced the output must not answer —
    that stale-sandbox shadow once bound a registration to a partial download
    and cost two agents four minutes on intact data. Provenance beats a copy:
    the real owner's remote address wins."""
    from content.bio.lifecycle import runs as R
    _wire(monkeypatch, 5, owners={"run_2"})

    def locate(rid, name, match="name", remote=True, **kw):
        if remote is False:
            # the stray: run_4 holds a local file it provably never produced
            return ({"local_path": "/x/stray/" + NAME, "size": 9}
                    if rid == "run_4" else None)
        if rid == "run_2":
            return {"locality": "remote", "site": "siteA", "size": 4096,
                    "kind": "dir", "target": "t_2"}
        return None
    monkeypatch.setattr(R, "locate_run_output", locate)
    assert R._locate_project_run_output(NAME) == ("run_2", "siteA", 4096, True)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
