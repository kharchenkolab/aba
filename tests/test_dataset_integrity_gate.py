"""A registered dataset whose files do not decompress must SAY SO.

Second half of the same field failure (the first is
`test_dataset_scratch_binding.py`, which fixes the wrong-sandbox binding that
produced the damaged copy). Even with the binding correct, an interrupted
download, a half-flushed copy or a truncated transfer can put a broken member
in a registered dataset. The register result is the LAST place the platform
speaks before the caller starts reading, and in the field it said
"Registered as a Dataset entity" over two truncated files — so the agent
proceeded, hit an unintelligible parser error four calls later, and concluded
the DATA was corrupt at its source. It was intact at its source.

The container carries its own end-of-stream marker, so this costs milliseconds
and needs no knowledge of the payload.
"""
import gzip
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.bio


@pytest.fixture()
def members(tmp_path):
    from content.bio.tools.curation import _corrupt_members
    d = tmp_path / "ds"
    d.mkdir()
    good = d / "good.tsv.gz"
    with gzip.open(good, "wb") as f:
        f.write(b"a\tb\tc\n" * 5000)
    # truncated mid-stream: the exact shape of an interrupted transfer
    trunc = d / "truncated.part.gz"
    trunc.write_bytes(good.read_bytes()[: len(good.read_bytes()) // 2])
    # corrupt body with an intact header — survives a size check, not a read
    bad = d / "damaged.part.gz"
    raw = bytearray(good.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    bad.write_bytes(bytes(raw))
    return d, _corrupt_members


def test_truncated_and_damaged_members_are_named(members):
    d, _corrupt_members = members
    bad = _corrupt_members(str(d))
    assert set(bad) == {"truncated.part.gz", "damaged.part.gz"}, (
        f"the integrity scan reported {bad} — a truncated member and a member "
        f"with a corrupt body must BOTH be caught; a size check alone misses "
        f"the second")


def test_intact_dataset_is_silent(tmp_path):
    """ARMED the other way: this must not cry wolf, or the warning becomes
    noise the agent learns to skip."""
    from content.bio.tools.curation import _corrupt_members
    d = tmp_path / "ok"
    d.mkdir()
    with gzip.open(d / "a.tsv.gz", "wb") as f:
        f.write(b"x\n" * 1000)
    (d / "plain.txt").write_bytes(b"not compressed, not our business\n")
    (d / "opaque.bin").write_bytes(bytes(range(256)) * 10)
    assert _corrupt_members(str(d)) == []


def test_unreadable_member_counts_as_a_finding(tmp_path):
    """DEGENERATE: a member that cannot be opened at all (permissions, a dead
    link, a vanished hardlink target) is not 'fine because we could not check'
    — silence there is how an empty dataset reads as a healthy one."""
    from content.bio.tools.curation import _corrupt_members
    d = tmp_path / "dead"
    d.mkdir()
    (d / "gone.tsv.gz").symlink_to(tmp_path / "does_not_exist.gz")
    assert _corrupt_members(str(d)) == ["gone.tsv.gz"]


def test_single_file_dataset_is_scanned(tmp_path):
    """DEGENERATE: registration takes a file as readily as a directory."""
    from content.bio.tools.curation import _corrupt_members
    f = tmp_path / "solo.part.gz"
    f.write_bytes(b"\x1f\x8b\x08\x00 truncated garbage")
    assert _corrupt_members(str(f)) == ["solo.part.gz"]


def test_scan_is_bounded(tmp_path, monkeypatch):
    """A dataset can be tens of thousands of files; the gate must stay cheap
    enough that nobody is tempted to turn it off."""
    from content.bio.tools import curation
    d = tmp_path / "many"
    d.mkdir()
    for i in range(20):
        (d / f"f{i}.tsv.gz").write_bytes(b"\x1f\x8b\x08\x00bad")
    monkeypatch.setattr(curation, "_CORRUPT_SCAN_CAP", 5)
    assert len(curation._corrupt_members(str(d))) == 5
