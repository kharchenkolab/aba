"""Unit tests for CondaProgress — the micromamba-create sub-progress parser
that keeps the install bar moving during the long env builds.

Line shapes are taken verbatim from `micromamba create -v` (2.8.0) output.

Run:
    python helper/tests/test_conda_progress.py
or via pytest.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aba_installer.conda_progress import CondaProgress   # noqa: E402


# A fresh install: every package downloads then links.
FRESH = [
    "Transaction",
    "  Package    Version  Build       Channel       Size",
    "  Install:",
    "  + bzip2    1.0.8    hd037594_9  conda-forge   125kB",
    "  + openssl  3.6.2    hd24854e_0  conda-forge   3MB",
    "  + python   3.14.5   h4c637c5_1  conda-forge   14MB",
    "  Summary:",
    "  Install: 3 packages",
    "  Total download: 17MB",
    "Transaction starting",
    "info     libmamba Download finished, tarball available at '/x/a'",
    "info     libmamba Download finished, tarball available at '/x/b'",
    "info     libmamba Download finished, tarball available at '/x/c'",
    "Linking bzip2-1.0.8-hd037594_9",
    "Linking openssl-3.6.2-hd24854e_0",
    "Linking python-3.14.5-h4c637c5_1",
    "Transaction finished",
]

# A fully-cached install: no downloads, only linking.
CACHED = [
    "  Install:",
    "  + ca-certificates  2026.5.20  hbd8a1cb_0  conda-forge  Cached",
    "  + libzlib          1.3.2      h8088a28_2  conda-forge  Cached",
    "  Install: 2 packages",
    "  Total download: 0 B",
    "Transaction starting",
    "Linking ca-certificates-2026.5.20-hbd8a1cb_0",
    "Linking libzlib-1.3.2-h8088a28_2",
    "Transaction finished",
]

NON_CONDA = [
    "Cloning into 'aba'...",
    "remote: Enumerating objects: 1234, done.",
    "Resolving deltas: 100% (900/900), done.",
    "done",
]


def _run(lines):
    cp = CondaProgress()
    return [cp.feed(ln) for ln in lines]


def test_fresh_is_monotonic_reaches_one_and_splits_download_link():
    fracs = _run(FRESH)
    assert fracs == sorted(fracs), f"not monotonic: {fracs}"
    assert abs(fracs[-1] - 1.0) < 1e-9, f"should finish at 1.0: {fracs[-1]}"
    # 3 to_download + 3 total = denom 6; after the 3 downloads => 0.5.
    after_downloads = fracs[FRESH.index("Linking bzip2-1.0.8-hd037594_9") - 1]
    assert abs(after_downloads - 0.5) < 1e-9, f"download phase should reach 0.5, got {after_downloads}"


def test_cached_moves_only_on_linking_and_reaches_one():
    fracs = _run(CACHED)
    assert fracs == sorted(fracs), f"not monotonic: {fracs}"
    assert abs(fracs[-1] - 1.0) < 1e-9, f"cached should finish at 1.0: {fracs[-1]}"
    # No downloads => denom is just total(2); first Linking => 0.5.
    assert abs(fracs[CACHED.index("Linking ca-certificates-2026.5.20-hbd8a1cb_0")] - 0.5) < 1e-9


def test_non_conda_output_reports_zero():
    fracs = _run(NON_CONDA)
    assert all(f == 0.0 for f in fracs), f"non-conda output must stay 0: {fracs}"


def test_partial_cache_uses_correct_denominator():
    # 1 download + 2 links; denom = to_download(1) + total(2) = 3.
    lines = [
        "  + a 1 b conda-forge 1MB",        # downloads
        "  + b 1 b conda-forge Cached",     # cached
        "  Install: 2 packages",
        "info libmamba Download finished, tarball available at '/x/a'",  # 1/3
        "Linking a-1-b",                    # 2/3
        "Linking b-1-b",                    # 3/3
    ]
    fracs = _run(lines)
    assert fracs == sorted(fracs)
    assert abs(fracs[-1] - 1.0) < 1e-9, fracs
    assert abs(fracs[3] - 1/3) < 1e-9, f"after 1 download of denom 3: {fracs[3]}"


def main() -> int:
    tests = [test_fresh_is_monotonic_reaches_one_and_splits_download_link,
             test_cached_moves_only_on_linking_and_reaches_one,
             test_non_conda_output_reports_zero,
             test_partial_cache_uses_correct_denominator]
    failed = []
    for t in tests:
        try:
            t(); print(f"OK  {t.__name__}")
        except AssertionError as e:
            failed.append(t.__name__); print(f"FAIL {t.__name__}: {e}")
    print(f"\n{'all passed' if not failed else f'{len(failed)} failed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
