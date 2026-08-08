"""A store written before lstar 0.2.2 must not reach the viewer unrepaired —
and the repair must never rewrite bytes ABA does not own.

lstar 0.2.2 turned a silent defect into a refusal. Before it, the viewer prep
normalized the count basis to gene-major (csc) in MEMORY and never wrote it
back, so a store built from a CSR source carried two cell-major copies and
nothing a gene column could be read from. 0.2.2 writes it back, stamps
`provenance.viewer="basis"`, and errors when it is missing; the pinned pagoda3
dist checks the same stamp client-side. Every older store therefore goes from
quietly wrong to REFUSED, in the browser, where ABA cannot explain it.

Bumping the pin heals ONE of the three lanes:

  * `.h5ad`/`.rds` → `_convert_any` under `ensure_derived`, keyed on the
    lstar-sc version — re-converts, and the new lstar writes the basis. Healed.
  * `.lstar.zarr.zip` → `_unzip_store`, ALSO under `ensure_derived`. It
    re-derives, and re-derives by unpacking the same stale bytes. A
    version-keyed rebuild only heals a lane that RE-COMPUTES.
  * a native `.lstar.zarr` directory → symlinked, no cache, never rebuilt.

The second is the interesting failure: it looked covered. It had the cache key,
the rebuild fired, and the store came out exactly as broken as before.

The load-bearing assertions here are about ACTIONS, not outputs:
  * the zip lane must not modify the ARCHIVE (ABA owns the extraction only);
  * a defective store in the project / weft workspace must come out COPIED and
    repaired with the ORIGINAL untouched — weft is the system of record there,
    and opening a file in a viewer is not a licence to rewrite it;
  * a CLEAN store must still be symlinked, with no repair attempted at all —
    otherwise this "fix" would copy every multi-GB store on every open.

The fake lstar below REFUSES an eager read (`lstar.read(path)` without
lazy=True). That is deliberate: the probe asks a metadata-only question, a real
store can be tens of GB, and a permissive fake would let an eager read ship.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.bio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


# ── a fake lstar that refuses what the real one refuses ──────────────────────

_FAKE_INIT = r'''
"""Stand-in for lstar: enough model surface for the contract probe, and the
same refusals, so a probe that cheats here would cheat in production too."""
import json, sys
from pathlib import Path


class _Field:
    def __init__(self, d):
        self.encoding = d.get("encoding")
        self.role = d.get("role")
        self.span = d.get("span")
        self.provenance = d.get("provenance") or {}

    @property
    def values(self):
        raise AssertionError(
            "the contract probe read field VALUES — it must stay metadata-only; "
            "a real store can be tens of GB")


class _DS:
    def __init__(self, fields):
        self.fields = {n: _Field(d) for n, d in fields.items()}


def read(path, lazy=False):
    if not lazy:
        raise AssertionError(
            "eager read of a store: lstar.read(path) materializes every field. "
            "The contract question is metadata-only — pass lazy=True")
    p = Path(path) / "store.json"
    if not p.exists():
        raise FileNotFoundError(f"not an lstar store: {path}")
    return _DS(json.loads(p.read_text())["fields"])
'''

# `python -m lstar viewer <store>` — lstar's own in-place repair.
_FAKE_MAIN = r'''
import json, sys
from pathlib import Path

argv = sys.argv[1:]
if not argv or argv[0] != "viewer":
    sys.stderr.write("fake lstar: only `viewer` is implemented\n")
    raise SystemExit(2)
store = Path(argv[1])
d = json.loads((store / "store.json").read_text())
if NO_OP:
    raise SystemExit(0)                      # "succeeded" without repairing
for name, f in d["fields"].items():
    if name != "counts_cellmajor" and f.get("role") == "measure":
        f["encoding"] = "csc"                # write the gene-major basis back
        f.setdefault("provenance", {})["viewer"] = "basis"
(store / "store.json").write_text(json.dumps(d))
raise SystemExit(0)
'''


@pytest.fixture
def fake_lstar(tmp_path, monkeypatch):
    """Install the fake on PYTHONPATH and route the launcher's argv builder at
    this interpreter. Returns a call log of every argv the launcher ran."""
    return _install_fake(tmp_path, monkeypatch, no_op_repair=False)


@pytest.fixture
def fake_lstar_broken_repair(tmp_path, monkeypatch):
    """Same, but `lstar viewer` exits 0 WITHOUT repairing — the shape where a
    repair silently does not take."""
    return _install_fake(tmp_path, monkeypatch, no_op_repair=True)


def _install_fake(tmp_path, monkeypatch, *, no_op_repair):
    pkg = tmp_path / "fakelib" / "lstar"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(_FAKE_INIT)
    (pkg / "__main__.py").write_text(f"NO_OP = {no_op_repair!r}\n" + _FAKE_MAIN)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "fakelib"))

    from content.bio.viewers.launchers import pagoda3
    calls: list[list[str]] = []

    def argv(pid, py_args):
        a = [sys.executable, *py_args]
        calls.append(a)
        return a

    monkeypatch.setattr(pagoda3, "_lstar_py_argv", argv)
    return calls


def _store(path: Path, *, gene_major: bool, stamped: bool = True) -> Path:
    """A store fixture in one of the two shapes. `gene_major=False` is the
    pre-0.2.2 output: the basis left in the source's cell-major encoding."""
    path.mkdir(parents=True, exist_ok=True)
    prov = {"viewer": "basis"} if stamped else {}
    (path / "store.json").write_text(json.dumps({"fields": {
        "counts": {"encoding": "csc" if gene_major else "csr", "role": "measure",
                   "span": ["cells", "genes"], "provenance": prov},
        "counts_cellmajor": {"encoding": "csr", "role": "measure",
                             "span": ["cells", "genes"], "provenance": {}},
    }}))
    return path


def _repairs(calls) -> list:
    return [c for c in calls if "-m" in c and "viewer" in c]


# ── the fake is faithful (if it is not, everything below is theatre) ──────────

def test_the_fake_REFUSES_an_eager_read(fake_lstar, tmp_path):
    """A fake more permissive than reality blesses the bug. An eager read of a
    multi-GB store is the thing the probe must never do, so the fake dies on
    it — which is what makes the lazy=True in the probe load-bearing."""
    _store(tmp_path / "s.lstar.zarr", gene_major=False)
    r = subprocess.run([sys.executable, "-c",
                        "import lstar; lstar.read(%r)" % str(tmp_path / "s.lstar.zarr")],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "lazy=True" in r.stderr


def test_the_fake_REFUSES_a_values_read(fake_lstar, tmp_path):
    """Same, one level down: touching `.values` is how lstar.validate() would
    have materialized the store, and why the probe cannot just call it."""
    _store(tmp_path / "s.lstar.zarr", gene_major=True)
    r = subprocess.run(
        [sys.executable, "-c",
         "import lstar; d=lstar.read(%r, lazy=True); d.fields['counts'].values"
         % str(tmp_path / "s.lstar.zarr")], capture_output=True, text=True)
    assert r.returncode != 0 and "metadata-only" in r.stderr


# ── the probe ────────────────────────────────────────────────────────────────

def test_the_probe_calls_a_PRE_0_2_2_store_defective(fake_lstar, tmp_path):
    from content.bio.viewers.launchers import pagoda3
    got = pagoda3._viewer_contract(_store(tmp_path / "s.lstar.zarr",
                                          gene_major=False, stamped=False))
    assert got["gene_major"] is False, got


def test_the_probe_calls_a_CURRENT_store_fine(fake_lstar, tmp_path):
    from content.bio.viewers.launchers import pagoda3
    got = pagoda3._viewer_contract(_store(tmp_path / "s.lstar.zarr", gene_major=True))
    assert got["gene_major"] is True, got


def test_an_UNREADABLE_store_is_unknown_not_defective(fake_lstar, tmp_path):
    """WIDE, the absent shape. None must never collapse into False: that would
    copy-and-rewrite a store on a probe failure."""
    from content.bio.viewers.launchers import pagoda3
    got = pagoda3._viewer_contract(tmp_path / "nope.lstar.zarr")
    assert got["gene_major"] is None and got.get("why")


def test_a_MISSING_lstar_is_unknown_not_defective(tmp_path, monkeypatch):
    """The pack-less / not-yet-realized deploy. No lstar means no verdict."""
    from content.bio.viewers.launchers import pagoda3
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "empty"))
    monkeypatch.setattr(pagoda3, "_lstar_py_argv",
                        lambda pid, a: [sys.executable, "-S", *a])
    got = pagoda3._viewer_contract(_store(tmp_path / "s.lstar.zarr", gene_major=False))
    assert got["gene_major"] is None, got


# ── the zip lane: re-derives, and re-derived the same broken bytes ────────────

def test_THE_ZIP_LANE_repairs_the_extraction(fake_lstar, tmp_path):
    """THE bug in this lane: it sits under ensure_derived and keys on the
    lstar-sc version, so a pin bump DOES re-derive — by unpacking the same
    pre-0.2.2 bytes. Version-keyed rebuild only heals a lane that recomputes."""
    from content.bio.viewers.launchers import pagoda3
    src = _store(tmp_path / "src.lstar.zarr", gene_major=False, stamped=False)
    zp = tmp_path / "s.lstar.zarr.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.write(src / "store.json", "store.json")

    out = tmp_path / "out.lstar.zarr"
    pagoda3._unzip_store(zp, out)
    assert pagoda3._viewer_contract(out)["gene_major"] is True, \
        "the extracted store was served with a cell-major-only basis"


def test_the_zip_lane_does_not_touch_the_ARCHIVE(fake_lstar, tmp_path):
    """ACTION, not output: ABA owns the extraction, never the source archive."""
    from content.bio.viewers.launchers import pagoda3
    src = _store(tmp_path / "src.lstar.zarr", gene_major=False, stamped=False)
    zp = tmp_path / "s.lstar.zarr.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.write(src / "store.json", "store.json")
    before = zp.read_bytes()

    pagoda3._unzip_store(zp, tmp_path / "out.lstar.zarr")
    assert zp.read_bytes() == before, "the source archive was modified"


def test_a_CLEAN_zip_is_not_repaired(fake_lstar, tmp_path):
    """CEILING. A repair re-emits the store; doing it on every open would be a
    large, pointless cost — and this guard is the reason the probe exists at
    all rather than repairing unconditionally."""
    from content.bio.viewers.launchers import pagoda3
    src = _store(tmp_path / "src.lstar.zarr", gene_major=True)
    zp = tmp_path / "s.lstar.zarr.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.write(src / "store.json", "store.json")

    fake_lstar.clear()
    pagoda3._unzip_store(zp, tmp_path / "out.lstar.zarr")
    assert _repairs(fake_lstar) == [], "a clean store was re-emitted for nothing"


def test_a_repair_that_does_NOT_take_fails_loudly(fake_lstar_broken_repair, tmp_path):
    """A repair that exits 0 and changes nothing must not pass for success — the
    store would reach the browser and be refused there instead."""
    from content.bio.viewers.launchers import pagoda3
    src = _store(tmp_path / "src.lstar.zarr", gene_major=False, stamped=False)
    zp = tmp_path / "s.lstar.zarr.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.write(src / "store.json", "store.json")

    with pytest.raises(RuntimeError, match="cell-major"):
        pagoda3._unzip_store(zp, tmp_path / "out.lstar.zarr")


# ── the native lane: symlinked, so never rebuilt at all ───────────────────────

def _root(tmp_path) -> Path:
    r = tmp_path / "project"
    (r / "pagoda3").mkdir(parents=True)
    return r


def test_THE_NATIVE_LANE_does_not_mutate_the_ORIGINAL(fake_lstar, tmp_path):
    """THE load-bearing action assertion. A defective store inside the project
    or the weft workspace is a run's retained output — weft's system of record.
    Repairing it in place would be the easy implementation and the wrong one."""
    from content.bio.viewers.launchers import pagoda3
    root = _root(tmp_path)
    orig = _store(root / "runs" / "d.lstar.zarr", gene_major=False, stamped=False)
    before = (orig / "store.json").read_bytes()

    out = pagoda3._serve_native_store(orig, root / "pagoda3", "o.lstar.zarr", root)
    assert (orig / "store.json").read_bytes() == before, \
        "the original store was rewritten in place"
    assert pagoda3._viewer_contract(out)["gene_major"] is True
    assert not out.is_symlink(), "a repaired copy must not be a link to the original"


def test_a_CLEAN_native_store_is_still_SYMLINKED(fake_lstar, tmp_path):
    """CEILING, and the point of the whole lane: not copying a possibly-multi-GB
    tree on every open. Only a legacy store may pay the copy."""
    from content.bio.viewers.launchers import pagoda3
    root = _root(tmp_path)
    orig = _store(root / "runs" / "d.lstar.zarr", gene_major=True)

    fake_lstar.clear()
    out = pagoda3._serve_native_store(orig, root / "pagoda3", "o.lstar.zarr", root)
    assert out.is_symlink() and out.resolve() == orig.resolve()
    assert _repairs(fake_lstar) == []


def test_an_UNKNOWN_verdict_still_symlinks(tmp_path, monkeypatch):
    """WIDE. When the probe cannot tell (no lstar, unreadable store), the safe
    move is the status quo — linking. Duplicating a tree on a guess is worse
    than serving a store that may well be fine."""
    from content.bio.viewers.launchers import pagoda3
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "empty"))
    monkeypatch.setattr(pagoda3, "_lstar_py_argv",
                        lambda pid, a: [sys.executable, "-S", *a])
    root = _root(tmp_path)
    orig = _store(root / "runs" / "d.lstar.zarr", gene_major=False, stamped=False)

    out = pagoda3._serve_native_store(orig, root / "pagoda3", "o.lstar.zarr", root)
    assert out.is_symlink(), "an unknown verdict copied the tree anyway"


def test_the_idempotent_fast_path_does_not_re_probe(fake_lstar, tmp_path):
    """A repeat open of an already-linked store must cost nothing: the probe is
    a subprocess, and paying it per launch would be a new latency floor."""
    from content.bio.viewers.launchers import pagoda3
    root = _root(tmp_path)
    orig = _store(root / "runs" / "d.lstar.zarr", gene_major=True)
    pagoda3._serve_native_store(orig, root / "pagoda3", "o.lstar.zarr", root)

    fake_lstar.clear()
    pagoda3._serve_native_store(orig, root / "pagoda3", "o.lstar.zarr", root)
    assert fake_lstar == [], f"the fast path ran {len(fake_lstar)} subprocess(es)"


def test_an_OUTSIDE_store_is_copied_then_repaired(fake_lstar, tmp_path):
    """The lane already copies a store registered from outside the project; the
    copy is ABA's, so it may be repaired — and the outside original may not."""
    from content.bio.viewers.launchers import pagoda3
    root = _root(tmp_path)
    orig = _store(tmp_path / "elsewhere" / "d.lstar.zarr",
                  gene_major=False, stamped=False)
    before = (orig / "store.json").read_bytes()

    out = pagoda3._serve_native_store(orig, root / "pagoda3", "o.lstar.zarr", root)
    assert not out.is_symlink()
    assert pagoda3._viewer_contract(out)["gene_major"] is True
    assert (orig / "store.json").read_bytes() == before


# ── the convert lane: healed by the pin, but only if the DEPLOYMENT moved ─────

def _stub_convert_run_probe_for_real():
    """Stub the `lstar convert` subprocess, but let the `-c` contract probe run
    against the fake for real.

    A blanket stub of subprocess.run is how the first draft of the test below
    passed while measuring nothing: it silenced the PROBE too, which then
    returned "unknown" and skipped the very branch under test."""
    real_run = subprocess.run

    def run(args, **kw):
        if "-c" in args:
            return real_run(args, **kw)
        return __import__("types").SimpleNamespace(returncode=0, stdout="", stderr="")
    return run

def test_a_convert_that_leaves_cell_major_names_the_STALE_SESSION_ENV(
        fake_lstar, tmp_path, monkeypatch):
    """The convert lane self-heals via the cache key — but only when the session
    env actually has 0.2.2. `aba update` never overwrites
    $ABA_HOME/installation/envs, so a long-lived deployment keeps its old lstar
    and converts a defective store with no error anywhere. Make that legible
    here rather than in the browser."""
    from content.bio.viewers.launchers import pagoda3
    out = _store(tmp_path / "o.lstar.zarr", gene_major=False, stamped=False)
    monkeypatch.setattr(subprocess, "run", _stub_convert_run_probe_for_real())
    with pytest.raises(RuntimeError, match="session env"):
        pagoda3._convert_any(tmp_path / "src.h5ad", out)


def test_a_convert_that_produces_a_GOOD_store_is_silent(fake_lstar, tmp_path,
                                                        monkeypatch):
    """CEILING: the normal path must not acquire a new way to fail."""
    from content.bio.viewers.launchers import pagoda3
    out = _store(tmp_path / "o.lstar.zarr", gene_major=True)
    monkeypatch.setattr(subprocess, "run", _stub_convert_run_probe_for_real())
    pagoda3._convert_any(tmp_path / "src.h5ad", out)     # must not raise


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
