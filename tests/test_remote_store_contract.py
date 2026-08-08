"""A STREAMED store gets the same contract verdict — reported, never repaired.

The three local lanes probe a store's counts basis with lstar and repair it. A
remote store served by the range channel can do neither: its bytes are a run's
retained output on another site, and the entire point of streaming is that they
never come home. Repairing would mean rewriting another machine's run output, or
pulling hundreds of MB down and pushing them back — the transfer this design
exists to avoid.

So this lane DETECTS and REPORTS. Detection needs no lstar on the far side and
no materialization, because the answer is metadata: the store root carries
`profiles` and the field list, a field's own metadata carries its `encoding`.
Two small reads (measured live: 103 KB root, a few hundred bytes for the field)
against a store of any size.

Two things this file is really guarding:

  * **It must not GUESS.** lstar's basis-selection rule has a stamped-field case
    and a same-span fallback that metadata alone cannot resolve. This probe
    answers only for the shape it can answer for and returns UNKNOWN otherwise.
    A wrong "your store is broken" on a remote store is worse than silence: the
    local lanes can repair a bad verdict, this one can only mislead.
  * **It must not WRITE.** Not to the store, not anywhere. The fake substrate
    below has no write verb at all, so an attempt would raise.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.bio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


# ── a FAKE store, addressed the way the real registry rows address one ────────

RUN_ENTRY = {"target": "run_x", "base_rel": "out/s.lstar.zarr", "site": "hpc"}
REF_ENTRY = {"ref": "ref_abc", "site": "hpc"}


def _store_docs(*, encoding="csc", profiles=("anndata@0.1", "viewer@0.1"),
                fields=("X", "counts", "counts_cellmajor"),
                v2=False, basis_field=True) -> dict:
    """rel -> metadata document, in the zarr v3 (`attributes.lstar`) shape or
    the v2 (`lstar` at root) shape."""
    def wrap(ls):
        return {"lstar": ls} if v2 else {"zarr_format": 3, "attributes": {"lstar": ls}}
    docs = {"zarr.json": wrap({"spec_version": "0.1", "kind": "sample",
                               "profiles": list(profiles), "fields": list(fields)})}
    if basis_field:
        docs["fields/counts/zarr.json"] = wrap(
            {"encoding": encoding, "role": "measure", "span": ["cells", "genes"]})
    return docs


class _Substrate:
    """A read-only stand-in for the data plane.

    REFUSES what the real one refuses, and carries what it always carries: an
    absent member raises (it does not return empty), an oversized read is
    clamped, and there is NO write verb — so a probe that tried to repair a
    remote store would fail here rather than pass."""

    def __init__(self, docs: dict, *, arm="run"):
        self.docs = docs
        self.arm = arm
        self.reads: list = []

    def _payload(self, rel):
        doc = self.docs.get(rel)
        if doc is None:
            from core.compute.errors import ComputeError
            raise ComputeError("data.missing", f"no such member: {rel}")
        return json.dumps(doc).encode()

    def file_read(self, target, rel, max_bytes=1 << 20):
        assert self.arm == "run", "run-arm verb used for a ref-arm row"
        assert target == RUN_ENTRY["target"], target
        base = RUN_ENTRY["base_rel"] + "/"
        assert rel.startswith(base), f"rel not joined to base_rel: {rel}"
        member = rel[len(base):]
        self.reads.append(member)
        data = self._payload(member)[:max_bytes]
        return {"nbytes": len(data), "bytes_b64": base64.b64encode(data).decode()}

    def data_read_range(self, ref, rel=None, *, offset=0, length=None, site=None):
        assert self.arm == "ref", "ref-arm verb used for a run-arm row"
        assert ref == REF_ENTRY["ref"] and site == REF_ENTRY["site"]
        self.reads.append(rel)
        data = self._payload(rel)[offset:offset + (length or len(self._payload(rel)))]
        return {"nbytes": len(data), "bytes_b64": base64.b64encode(data).decode()}


@pytest.fixture
def substrate(monkeypatch):
    from core.compute import retention

    def install(docs, arm="run"):
        s = _Substrate(docs, arm=arm)
        monkeypatch.setattr(retention, "file_read", s.file_read)
        monkeypatch.setattr(retention, "data_read_range", s.data_read_range)
        return s
    return install


def _contract(entry):
    from content.bio.viewers.launchers import pagoda3
    return pagoda3.remote_viewer_contract(entry)


# ── the verdict ──────────────────────────────────────────────────────────────

def test_a_gene_major_remote_store_is_FINE(substrate):
    substrate(_store_docs(encoding="csc"))
    got = _contract(RUN_ENTRY)
    assert got["gene_major"] is True, got


def test_THE_CASE_a_cell_major_remote_store_is_DEFECTIVE(substrate):
    """The store the pinned viewer refuses — today with no explanation from
    ABA's side, because nothing on this path ever looked."""
    substrate(_store_docs(encoding="csr"))
    got = _contract(RUN_ENTRY)
    assert got["gene_major"] is False and got["encoding"] == "csr", got


def test_it_costs_exactly_TWO_reads(substrate):
    """The whole design claim. A probe that walked the field list, or fell back
    to reading members, would be a per-launch cost on a store of any size."""
    s = substrate(_store_docs())
    _contract(RUN_ENTRY)
    assert s.reads == ["zarr.json", "fields/counts/zarr.json"], s.reads


def test_the_REF_arm_reads_by_ref_with_the_rel_passed_through(substrate):
    """Both arms must answer; the ref IS the tree root, so there is no base to
    join. A row addressed one way and read the other is the asymmetry the
    fake asserts on."""
    s = substrate(_store_docs(encoding="csr"), arm="ref")
    got = _contract(REF_ENTRY)
    assert got["gene_major"] is False, got
    assert s.reads == ["zarr.json", "fields/counts/zarr.json"], s.reads


def test_a_v2_store_is_read_too(substrate):
    """WIDE: v2 puts the lstar block at the document root, v3 under
    `attributes`. Reading only one shape would report every store of the other
    kind as unknown — a probe that is silently blind."""
    substrate(_store_docs(encoding="csr", v2=True))
    assert _contract(RUN_ENTRY)["gene_major"] is False


# ── abstention: the half that keeps a remote verdict honest ──────────────────

def test_a_NON_VIEWER_store_is_unknown_not_defective(substrate):
    """A bare store may legitimately hold counts in either encoding — the
    orientation is only load-bearing under the viewer profile."""
    substrate(_store_docs(encoding="csr", profiles=("anndata@0.1",)))
    got = _contract(RUN_ENTRY)
    assert got["gene_major"] is None and "viewer" in got["why"], got


def test_a_basis_that_is_not_named_counts_is_unknown(substrate):
    """lstar selects the basis by a stamp, then by name, then by span. Metadata
    alone cannot resolve the first and third — so answer for the second and
    abstain otherwise, rather than mistaking another measure for the basis."""
    substrate(_store_docs(encoding="csr", fields=("X", "logcounts"),
                          basis_field=False))
    got = _contract(RUN_ENTRY)
    assert got["gene_major"] is None, got


def test_the_field_LIST_is_what_decides_not_a_member_that_happens_to_exist(substrate):
    """ARMING for the abstention above, which the previous test cannot provide.

    There, no `counts` member existed either, so removing the guard clause
    entirely still produced 'unknown' — via a downstream accident (the second
    read simply failed) rather than via the decision under test. Give the store
    a readable `counts` member that the root does NOT list as a field: now the
    clause is the only thing standing between abstaining and declaring the store
    broken on the strength of a member lstar would never have chosen."""
    docs = _store_docs(encoding="csr", fields=("X", "logcounts"))
    assert "fields/counts/zarr.json" in docs        # readable, and cell-major
    s = substrate(docs)
    got = _contract(RUN_ENTRY)
    assert got["gene_major"] is None, (
        "judged a store by a member the root does not list as a field — that is "
        "guessing, and a wrong verdict here is unfixable from ABA's side")
    assert s.reads == ["zarr.json"], (
        f"read the basis member after deciding it was not identifiable: {s.reads}")


def test_an_unreadable_root_is_unknown(substrate):
    """An older substrate with no read verb, a swept sandbox, a vanished run."""
    substrate({})
    assert _contract(RUN_ENTRY)["gene_major"] is None


def test_an_unreadable_BASIS_member_is_unknown(substrate):
    """WIDE: the root read succeeds and the second one does not. Half an answer
    must not become a whole verdict."""
    docs = _store_docs()
    docs.pop("fields/counts/zarr.json")
    substrate(docs)
    assert _contract(RUN_ENTRY)["gene_major"] is None


def test_malformed_metadata_is_unknown_not_a_raise(substrate):
    """A diagnostic that raises into a launch is worse than one that abstains."""
    substrate({"zarr.json": {"attributes": {"lstar": "not-a-dict"}}})
    assert _contract(RUN_ENTRY)["gene_major"] is None


def test_a_dead_substrate_does_not_raise(monkeypatch):
    from core.compute import retention
    from content.bio.viewers.launchers import pagoda3

    def boom(*a, **k):
        raise RuntimeError("adapter down")
    monkeypatch.setattr(retention, "file_read", boom)
    assert pagoda3.remote_viewer_contract(RUN_ENTRY)["gene_major"] is None


# ── the report: it must name the FIX, and only when there IS one ─────────────

def _warning(entry):
    from content.bio.viewers.launchers import pagoda3
    return pagoda3.remote_contract_warning(entry, store_rel="out/s.lstar.zarr")


def test_the_warning_names_the_command_AND_the_site(substrate):
    """The whole deliverable for this lane. 'Something is wrong' is what the
    browser already says; what it cannot say is what to run and where."""
    substrate(_store_docs(encoding="csr"))
    w = _warning(RUN_ENTRY)
    assert w and "lstar viewer out/s.lstar.zarr" in w and "hpc" in w, w


def test_the_warning_says_ABA_CANNOT_repair_it(substrate):
    """Otherwise the obvious next question — 'why didn't you just fix it?' —
    goes unanswered, and the honest answer is a design decision, not a gap."""
    substrate(_store_docs(encoding="csr"))
    assert "cannot repair" in (_warning(RUN_ENTRY) or "")


def test_a_FINE_store_produces_no_warning(substrate):
    """CEILING: a warning on a healthy store would block every remote launch."""
    substrate(_store_docs(encoding="csc"))
    assert _warning(RUN_ENTRY) is None


def test_an_UNKNOWN_verdict_produces_no_warning(substrate):
    """The load-bearing abstention. Unknown is the COMMON answer on this path
    (older substrate, unstamped basis, non-viewer store); if it warned, every
    such launch would be blocked by a probe that knew nothing."""
    substrate(_store_docs(encoding="csr", profiles=("anndata@0.1",)))
    assert _warning(RUN_ENTRY) is None


def test_the_probe_never_writes(substrate):
    """ACTION, not output. The fake exposes no write verb at all, so any repair
    attempt raises rather than quietly succeeding — the guarantee that makes
    'report only' a property instead of a promise."""
    s = substrate(_store_docs(encoding="csr"))
    _contract(RUN_ENTRY)
    _warning(RUN_ENTRY)
    assert not hasattr(s, "file_write") and not hasattr(s, "data_write")
    assert all(r.endswith("zarr.json") for r in s.reads), s.reads


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
