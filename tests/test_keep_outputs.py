"""R2: the level-2 keep decision — `keep_outputs` tool → set_keep_decision → _keeper_set.

Level-1 (obvious scratch by folder/glob) stays automatic; level-2 is the agent's ambiguous-set
triage: `drop` excludes a large intermediate even though it looks like a keeper; `keep` rescues a
file the folder heuristic would drop (or names a literal final). The decision persists on the Run
so the plan-end + close auto-retains honor it too. misc/output_durability.md §6.1, A1.

Run: python tests/test_keep_outputs.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_keep_"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from content.bio.lifecycle import runs as runsmod  # noqa: E402
import content.bio.tools.curation as curmod  # noqa: E402


def test_keeper_set_level1_baseline():
    produced = {"umap.png", "big.h5ad", "tmp/scratch.dat", "chunk_003.parquet", "run.tmp"}
    keep = runsmod._keeper_set(produced)
    assert keep == {"umap.png", "big.h5ad"}          # transient dir + globs dropped, keepers stay


def test_keeper_set_exclude_drops_a_keeper():
    produced = {"umap.png", "huge_intermediate.h5ad", "model.pt"}
    keep = runsmod._keeper_set(produced, exclude=["huge_intermediate.h5ad"])
    assert keep == {"umap.png", "model.pt"}          # agent drop wins over "looks like a keeper"


def test_keeper_set_include_rescues_transient_and_adds_literal():
    produced = {"cache/embeddings.npy", "umap.png"}
    keep = runsmod._keeper_set(produced,
                               include=["cache/embeddings.npy", "declared/final.rds"])
    assert "cache/embeddings.npy" in keep            # rescued from level-1 transient
    assert "umap.png" in keep
    assert "declared/final.rds" in keep              # literal include not among produced → added


def test_keeper_set_exclude_glob():
    produced = {"a.csv", "sweep/run1.csv", "sweep/run2.csv"}
    keep = runsmod._keeper_set(produced, exclude=["sweep/*"])
    assert keep == {"a.csv"}


def test_set_keep_decision_persists_and_applies(monkeypatch):
    """Records the merged decision on the Run + retains the resulting keeper set, honoring drop."""
    import core.exec.artifacts as artmod
    import core.compute.retention as retmod
    store = {"run-1": {"id": "run-1", "metadata": {"weft_targets": ["krn_a"],
                                                   "keep_decision": {"exclude": ["old.tmp"]}}}}
    monkeypatch.setattr(runsmod, "get_entity", lambda rid: store.get(rid))
    monkeypatch.setattr(runsmod, "update_entity",
                        lambda rid, **kw: store[rid].update(kw) or store[rid])
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "umap.png"},
        {"original_name": "big.h5ad"},
        {"original_name": "huge_intermediate.h5ad"},   # agent will DROP this
    ])
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    monkeypatch.setattr(retmod, "inventory", lambda t: {"entries": []})
    monkeypatch.setattr(retmod, "file_stat", lambda t, rel: {"exists": False})
    calls = []
    monkeypatch.setattr(retmod, "retain",
                        lambda target, **kw: calls.append(kw) or {"state": "pinned-pending"})

    out = runsmod.set_keep_decision("run-1", keep=["notes/keep_me.md"],
                                    drop=["huge_intermediate.h5ad"])
    # decision merged with the pre-existing exclude, deduped + sorted
    dec = store["run-1"]["metadata"]["keep_decision"]
    assert dec["include"] == ["notes/keep_me.md"]
    assert dec["exclude"] == ["huge_intermediate.h5ad", "old.tmp"]
    # retain issued for the keepers, honoring the drop (intermediate excluded)
    assert len(calls) == 1
    inc = calls[0]["include"]
    assert "umap.png" in inc and "big.h5ad" in inc and "notes/keep_me.md" in inc
    assert "huge_intermediate.h5ad" not in inc
    assert out["decision"] == dec and "summary" in out


def test_set_keep_decision_unknown_run(monkeypatch):
    monkeypatch.setattr(runsmod, "get_entity", lambda rid: None)
    assert runsmod.set_keep_decision("nope")["error"]


def test_keep_outputs_tool_resolves_active_run(monkeypatch):
    seen = {}
    monkeypatch.setattr(curmod, "_ctx_thread", lambda ctx: "t1")
    from content.bio.lifecycle import runs as R
    monkeypatch.setattr(R, "active_run_id", lambda tid: "run-9")
    monkeypatch.setattr(R, "set_keep_decision",
                        lambda rid, keep, drop: seen.update(rid=rid, keep=keep, drop=drop)
                        or {"decision": {}, "summary": {"retained": 3, "saving": 1, "at_risk": 0}})
    out = curmod.keep_outputs_tool({"drop": ["scratch.bin"]}, ctx={"thread_id": "t1"})
    assert out["status"] == "ok"
    assert seen == {"rid": "run-9", "keep": [], "drop": ["scratch.bin"]}
    assert "retained=3" in out["note"]


def test_keep_outputs_tool_no_run(monkeypatch):
    monkeypatch.setattr(curmod, "_ctx_thread", lambda ctx: "t1")
    from content.bio.lifecycle import runs as R
    monkeypatch.setattr(R, "active_run_id", lambda tid: None)
    out = curmod.keep_outputs_tool({}, ctx={"thread_id": "t1"})
    assert "error" in out


def test_keep_outputs_tool_coerces_str_to_list(monkeypatch):
    seen = {}
    monkeypatch.setattr(curmod, "_ctx_thread", lambda ctx: "t1")
    from content.bio.lifecycle import runs as R
    monkeypatch.setattr(R, "active_run_id", lambda tid: "run-9")
    monkeypatch.setattr(R, "set_keep_decision",
                        lambda rid, keep, drop: seen.update(keep=keep, drop=drop) or {"summary": {}})
    curmod.keep_outputs_tool({"keep": "final.rds", "drop": "junk.tmp"}, ctx={})
    assert seen["keep"] == ["final.rds"] and seen["drop"] == ["junk.tmp"]


_TESTS = [
    test_keeper_set_level1_baseline,
    test_keeper_set_exclude_drops_a_keeper,
    test_keeper_set_include_rescues_transient_and_adds_literal,
    test_keeper_set_exclude_glob,
    test_set_keep_decision_persists_and_applies,
    test_set_keep_decision_unknown_run,
    test_keep_outputs_tool_resolves_active_run,
    test_keep_outputs_tool_no_run,
    test_keep_outputs_tool_coerces_str_to_list,
]


def _standalone() -> int:
    import inspect
    import traceback

    class _MP:
        def __init__(self): self._u = []
        def setattr(self, t, n, v, raising=True):
            self._u.append((t, n, getattr(t, n))); setattr(t, n, v)
        def undo(self):
            for t, n, o in reversed(self._u):
                setattr(t, n, o)
            self._u.clear()

    rc = 0
    for t in _TESTS:
        mp = _MP()
        try:
            t(mp) if "monkeypatch" in inspect.signature(t).parameters else t()
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
        finally:
            mp.undo()
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
