"""A byte range costs the segments it covers — not the whole member.

The range channel's cache unit was the MEMBER. A viewer colouring by one gene
reads ~84 KB out of a 176 MB single-chunk array and paid for all 176 MB: 149 s,
measured over a 12 Mbit link (misc/from-aba-first-touch-cost.md). The channel is
bandwidth-bound — ~0.85 s fixed per call, 1.2-2.8 MB/s marginal — so the only
lever that matters is transferring less.

A ranged request now covers its interval from a 1 MiB segment grid. The three
claims this file has to keep honest:

  * **It transfers only what it covers.** The fake substrate records every
    (offset, length) it is asked for, so a read that quietly widened to the
    member — the regression that would silently undo the whole change — fails.
  * **It does not make sequential reads worse.** A fixed grid could turn one
    16 MiB call into sixteen 1 MiB ones, and at 0.85 s of fixed cost each that
    is a large REGRESSION for a whole-member walk. Contiguous missing runs are
    coalesced into calls of up to RANGE_CAP, and there is a test that counts.
  * **It never splices.** Segments are evictable and a back-haul can half-fail,
    so a range whose covering segments are not ALL present must refuse rather
    than concatenate what happens to be on disk. That is the 16 MiB short-read
    failure one level down: silently wrong, sticky, and indistinguishable from
    real data afterwards.

The fake HONOURS `length` and clamps at RANGE_CAP, because a fake that returned
whatever it liked would let a caller that ignores `length` pass — and ignoring
`length` is exactly the bug.
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

_RT = tempfile.mkdtemp(prefix="aba_seg_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "d.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.compute import retention          # noqa: E402
from core.compute.errors import ComputeError  # noqa: E402
from core.viewers import range_cache as rc  # noqa: E402

SEG = rc.SEGMENT_BYTES
PID = "prj_seg"
KEY = "store_a"
REL = "counts/data/0"
ENTRY = {"target": "run_x", "base_rel": "out/s.lstar.zarr", "site": "hpc"}


def _payload(n: int) -> bytes:
    """Deterministic, position-dependent bytes: any splice or off-by-one shows
    up as a content mismatch rather than a plausible-looking buffer."""
    return bytes((i * 7 + (i >> 8) * 13) & 0xFF for i in range(n))


class _Fake:
    """A ranged-read substrate that behaves like the real one.

    REFUSES what the real one refuses and CARRIES what it always carries: it
    honours `length`, clamps it at RANGE_CAP with `capped`, always states the
    member `size`, and raises the typed `data.missing` for an unknown member
    rather than returning empty."""

    def __init__(self, data: bytes, *, cap=None, fail_after=None):
        self.data = data
        self.cap = cap or retention.RANGE_CAP
        self.calls: list = []
        self.fail_after = fail_after

    def read(self, target, rel, *, offset=0, length=None, rels=None):
        if rels is not None:
            raise TypeError("batch not supported by this fake")
        if rel != ENTRY["base_rel"] + "/" + REL:
            raise ComputeError("data.missing", f"no such member: {rel}")
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise ComputeError("internal.error", "substrate blew up")
        self.calls.append((offset, length))
        want = self.cap if length is None else min(length, self.cap)
        chunk = self.data[offset:offset + want]
        return {"nbytes": len(chunk), "size": len(self.data),
                "capped": length is not None and length > self.cap,
                "eof": offset + len(chunk) >= len(self.data),
                "bytes_b64": base64.b64encode(chunk).decode()}

    @property
    def bytes_moved(self) -> int:
        return sum(min(l or self.cap, self.cap) for _o, l in self.calls)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A registered run-arm store over a fake substrate; returns a helper that
    installs a payload and yields the fake."""
    monkeypatch.setattr(rc, "project_root", lambda pid: tmp_path)

    def setup(nbytes, **kw):
        fake = _Fake(_payload(nbytes), **kw)
        monkeypatch.setattr(retention, "file_read_range", fake.read)
        rc.register_remote_store(PID, KEY, site="hpc", target=ENTRY["target"],
                                 base_rel=ENTRY["base_rel"], size=nbytes,
                                 digest="d1")
        return fake
    return setup


def _get(start, end):
    return rc.serve_remote_range(PID, f"{KEY}/{REL}", start, end)


# ── the fake is faithful ─────────────────────────────────────────────────────

def test_the_fake_HONOURS_length(env):
    """If it did not, a caller that ignored `length` — asking for the whole
    member every time — would pass every test below."""
    f = env(4 * SEG)
    r = f.read("t", ENTRY["base_rel"] + "/" + REL, offset=0, length=10)
    assert r["nbytes"] == 10


def test_the_fake_CLAMPS_at_the_cap(env):
    """The member here is smaller than RANGE_CAP, so the clamp shows up as
    `capped` plus a reply bounded by the member — not by the request."""
    f = env(4 * SEG)
    r = f.read("t", ENTRY["base_rel"] + "/" + REL, offset=0,
               length=retention.RANGE_CAP * 2)
    assert r["capped"] is True
    assert r["nbytes"] == min(retention.RANGE_CAP, len(f.data))


def test_the_fake_raises_for_a_missing_member(env):
    f = env(SEG)
    with pytest.raises(ComputeError):
        f.read("t", "out/s.lstar.zarr/nope", offset=0)


# ── THE claim: a small range costs a segment, not a member ───────────────────

def test_a_small_range_transfers_ONE_segment_of_a_huge_member(env):
    """The headline. 84 KB out of a 40 MiB member must not move 40 MiB."""
    f = env(40 * SEG)
    out = _get(10 * SEG + 100, 10 * SEG + 100 + 84_000 - 1)
    assert out.status == "ok"
    assert out.data == f.data[10 * SEG + 100: 10 * SEG + 100 + 84_000]
    assert f.bytes_moved <= SEG + 1, (
        f"moved {f.bytes_moved} bytes for an 84 KB range: {f.calls}")


def test_the_bytes_are_EXACTLY_right_across_a_segment_boundary(env):
    """Off-by-one in the slice arithmetic produces data, not an error — which is
    why the payload is position-dependent."""
    f = env(4 * SEG)
    start, end = SEG - 5, SEG + 5
    out = _get(start, end)
    assert out.status == "ok" and out.data == f.data[start:end + 1]
    assert out.start == start and out.end == end and out.total == len(f.data)


def test_a_second_read_of_a_cached_segment_touches_the_SUBSTRATE_NOT_AT_ALL(env):
    """Reuse is the other half of 'transfer less'. Armed: the fake counts."""
    f = env(4 * SEG)
    _get(100, 200)
    n = len(f.calls)
    _get(300, 400)                    # same segment
    assert len(f.calls) == n, f.calls[n:]


def test_an_OPEN_ENDED_range_is_bounded_by_the_member(env):
    """`bytes=N-` has no end; the member's size is the only bound, and it comes
    from a reply, never from a guess."""
    f = env(2 * SEG)
    out = _get(2 * SEG - 10, None)
    assert out.status == "ok" and out.data == f.data[-10:]
    assert out.end == len(f.data) - 1 and out.total == len(f.data)


def test_a_range_past_the_end_is_416_not_empty_bytes(env):
    env(SEG)
    out = _get(SEG + 5, SEG + 10)
    assert out.status == "reject" and out.http == 416


def test_the_LAST_short_segment_is_served_whole(env):
    """WIDE: members are not segment multiples, so the final segment is short.
    Treating it as 1 MiB would read past the end or refuse to cache it."""
    f = env(2 * SEG + 777)
    out = _get(2 * SEG, None)
    assert out.status == "ok" and out.data == f.data[2 * SEG:]


def test_a_member_SMALLER_than_one_segment_works(env):
    """The degenerate shape — every metadata member in a store is like this."""
    f = env(1000)
    out = _get(10, 20)
    assert out.status == "ok" and out.data == f.data[10:21]


# ── the regression a fixed grid invites ──────────────────────────────────────

def test_a_LARGE_range_is_COALESCED_not_split_into_one_call_per_segment(env):
    """The risk this design had to answer. A 32 MiB range covers 32 segments; at
    ~0.85 s of fixed cost per call, issuing 32 calls would be far SLOWER than
    the whole-member path it replaces. Contiguous missing runs ride calls of up
    to RANGE_CAP, so this is 2 calls, not 32."""
    f = env(64 * SEG)
    out = _get(0, 32 * SEG - 1)
    assert out.status == "ok" and len(out.data) == 32 * SEG
    expected = (32 * SEG + retention.RANGE_CAP - 1) // retention.RANGE_CAP
    assert len(f.calls) <= expected + 1, (
        f"{len(f.calls)} calls for a {32 * SEG}-byte range (expected ~{expected}): "
        f"the missing-run coalescing is not working")


def test_no_call_ever_exceeds_the_substrate_cap(env):
    """CEILING on the coalescing: it must widen up to RANGE_CAP and no further,
    or the substrate silently clamps and the loop mis-accounts progress."""
    f = env(64 * SEG)
    _get(0, 40 * SEG - 1)
    assert all((l is None or l <= retention.RANGE_CAP) for _o, l in f.calls), f.calls


# ── the splice guard ─────────────────────────────────────────────────────────

def test_a_range_spanning_an_EVICTED_segment_REFUSES(env):
    """THE correctness guard, armed precisely.

    An earlier version of this test killed the substrate outright, so the
    back-haul RAISED and the generic error path answered — the refusal looked
    right while the splice check itself never ran. Instead let the re-fetch
    "succeed" and return no bytes for that offset (the past-EOF shape a
    substrate uses legitimately). Now nothing raises, the segment is still
    missing afterwards, and only the post-fetch presence check stands between
    the caller and a spliced buffer."""
    f = env(8 * SEG)
    _get(0, 4 * SEG - 1)                       # populate segments 0..3
    sdir = rc._seg_dir(PID, "hpc", KEY, REL)
    os.remove(rc._seg_path(sdir, 2))           # evict one in the MIDDLE

    real = f.read

    def blackhole(target, rel, *, offset=0, length=None, rels=None):
        if 2 * SEG <= offset < 3 * SEG:        # "read" it, return nothing
            return {"nbytes": 0, "size": len(f.data), "capped": False,
                    "eof": False, "bytes_b64": ""}
        return real(target, rel, offset=offset, length=length, rels=rels)

    import core.compute.retention as _r
    old, _r.file_read_range = _r.file_read_range, blackhole
    try:
        out = _get(0, 4 * SEG - 1)
    finally:
        _r.file_read_range = old
    assert out.status == "error" and out.http == 502, out
    assert "spliced" in out.detail, out.detail
    assert out.data is None, "returned bytes for a range it could not cover"


def test_the_splice_guard_also_holds_when_the_site_is_DEAD(env):
    """WIDE, the other failure shape: the re-fetch raises instead of returning
    nothing. Different path, same requirement — no partial bytes."""
    env(8 * SEG)
    _get(0, 4 * SEG - 1)
    sdir = rc._seg_dir(PID, "hpc", KEY, REL)
    os.remove(rc._seg_path(sdir, 2))

    def dead(*a, **k):
        raise ComputeError("internal.error", "site down")
    import core.compute.retention as _r
    old, _r.file_read_range = _r.file_read_range, dead
    try:
        out = _get(0, 4 * SEG - 1)
    finally:
        _r.file_read_range = old
    assert out.status == "error" and out.data is None, out


def test_an_evicted_segment_is_simply_RE_FETCHED_when_the_site_is_up(env):
    """The other side of the same coin: eviction is not corruption, it is a
    miss. A guard that only proved the refusal would reward never caching."""
    f = env(8 * SEG)
    _get(0, 4 * SEG - 1)
    sdir = rc._seg_dir(PID, "hpc", KEY, REL)
    os.remove(rc._seg_path(sdir, 2))
    out = _get(0, 4 * SEG - 1)
    assert out.status == "ok" and out.data == f.data[:4 * SEG]


def test_a_PARTIAL_reply_never_installs_a_short_segment(env):
    """A segment is installed only when provably whole. A truncated one cached
    as complete is the 16 MiB short read again — sticky and undetectable."""
    f = env(4 * SEG, cap=SEG // 2)             # every reply is half a segment
    out = _get(0, 2 * SEG - 1)
    assert out.status == "ok" and out.data == f.data[:2 * SEG]
    sdir = rc._seg_dir(PID, "hpc", KEY, REL)
    for i in (0, 1):
        assert os.path.getsize(rc._seg_path(sdir, i)) == SEG, \
            f"segment {i} cached at the wrong size"


def test_a_backhaul_failure_MIDWAY_does_not_serve_partial_data(env):
    """The failure must reach the caller as an error, not as fewer bytes."""
    f = env(8 * SEG, fail_after=1)
    out = _get(0, 4 * SEG - 1)
    assert out.status != "ok", out


# ── confinement + registry ceilings, unchanged by the grid ───────────────────

def test_a_traversal_rel_is_refused_BEFORE_any_backhaul(env):
    f = env(4 * SEG)
    out = rc.serve_remote_range(PID, f"{KEY}/../../etc/passwd", 0, 10)
    assert out.status == "reject" and out.http == 403
    assert f.calls == [], "back-hauled for a traversal path"


def test_an_unregistered_store_returns_None(env):
    env(SEG)
    assert rc.serve_remote_range(PID, "other_key/x", 0, 10) is None


def test_a_member_ALREADY_CACHED_WHOLE_defers_to_the_existing_path(env):
    """CEILING. The whole-member cache still exists and FileResponse answers
    ranges from it locally; taking the grid would re-fetch bytes already on this
    disk. Returning None is how the route falls through to that path."""
    f = env(4 * SEG)
    whole = os.path.join(rc._cache_root(PID, "hpc", KEY), REL)
    os.makedirs(os.path.dirname(whole), exist_ok=True)
    with open(whole, "wb") as fh:
        fh.write(f.data)
    assert rc.serve_remote_range(PID, f"{KEY}/{REL}", 0, 10) is None
    assert f.calls == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ── the route: Range in, 206 out ─────────────────────────────────────────────
#
# The parser degrades rather than rejects: any header it does not understand
# falls through to the whole-member path, which is always a CORRECT answer to a
# range request (200 instead of 206). A 400 here would turn a header quirk into
# a broken viewer.

def _route():
    import types
    from main import pagoda3_store

    def call(pid, relpath, *, rng=None):
        hdrs = {}
        if rng:
            hdrs["range"] = rng
        return pagoda3_store(pid, relpath, types.SimpleNamespace(headers=hdrs))
    return call


def _parse(h):
    from main import _parse_byte_range
    return _parse_byte_range(h)


@pytest.mark.parametrize("header,expect", [
    ("bytes=0-99", (0, 99)),
    ("bytes=100-", (100, None)),
    (" bytes=5-5 ", (5, 5)),
    ("BYTES=1-2", (1, 2)),
    (None, None),
    ("", None),
    ("items=0-9", None),          # not bytes
    ("bytes=-500", None),         # suffix form: whole member is the safe answer
    ("bytes=0-9,20-29", None),    # multi-range
    ("bytes=abc-9", None),
    ("bytes=9-0", None),          # inverted
    ("bytes=", None),
])
def test_the_range_parser(header, expect):
    assert _parse(header) == expect


def test_the_route_answers_a_range_with_206_and_content_range(env):
    f = env(4 * SEG)
    resp = _route()(PID, f"{KEY}/{REL}", rng="bytes=100-199")
    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == f"bytes 100-199/{len(f.data)}"
    assert resp.body == f.data[100:200]
    assert resp.headers["Accept-Ranges"] == "bytes"


def test_the_route_moves_only_a_segment_for_that_range(env):
    """The end-to-end form of the headline claim, through the real route."""
    f = env(40 * SEG)
    _route()(PID, f"{KEY}/{REL}", rng="bytes=1000-2000")
    assert f.bytes_moved <= SEG + 1, f.calls


def test_an_UNRANGED_request_keeps_the_whole_member_path(env):
    """CEILING. No Range header → the assembly path, byte for byte as before.
    This is what protects the sequential walk and every metadata read."""
    f = env(2 * SEG)
    resp = _route()(PID, f"{KEY}/{REL}")
    assert getattr(resp, "path", None), "expected a FileResponse from the whole-member path"
    with open(resp.path, "rb") as fh:
        assert fh.read() == f.data


def test_a_MALFORMED_range_degrades_to_the_whole_member(env):
    """Not a 400: the whole member answers a range request correctly."""
    f = env(SEG)
    resp = _route()(PID, f"{KEY}/{REL}", rng="bytes=not-a-range")
    assert getattr(resp, "path", None), "malformed Range should degrade, not fail"


def test_a_range_past_the_end_is_a_416_through_the_route(env):
    from fastapi import HTTPException
    env(SEG)
    with pytest.raises(HTTPException) as ei:
        _route()(PID, f"{KEY}/{REL}", rng=f"bytes={SEG + 10}-{SEG + 20}")
    assert ei.value.status_code == 416


def test_a_traversal_range_url_is_403_through_the_route(env):
    from fastapi import HTTPException
    f = env(SEG)
    with pytest.raises(HTTPException) as ei:
        _route()(PID, f"{KEY}/../../etc/passwd", rng="bytes=0-9")
    assert ei.value.status_code == 403
    assert f.calls == []
