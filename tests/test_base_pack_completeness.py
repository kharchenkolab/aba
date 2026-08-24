"""The base packs must not ship a library whose advertised capability is absent.

A dependency that half-works is worse than one that is missing: the agent has no
way to know in advance, so it writes the natural code, fails on the node, and
spends round-trips discovering a gap the pack could simply not have had.

Live (2026-07-27, orbtest). The python pack ships `pandas` but no parquet engine.
Asked to hand a table from R to Python on the same machine, the agent reached for
parquet — the obvious choice — and got:

    ImportError: Unable to find a usable engine; tried using: 'pyarrow',
    'fastparquet'.

three times in a row (two kernel blocks and a background job) before falling back
to CSV. `pyarrow` is now a pack dep.

This guard is about PAIRS, not a package list: each entry names a library in the
pack and the companion its headline API needs. Adding a library with a known
engine/companion requirement means adding a row here, which is the point — the
next `pandas`-shaped omission fails at review instead of on a compute node.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "install" / "core" / "envs"

# library in the pack → (companion that must also be there, what breaks without it)
PYTHON_PAIRS = [
    ("pandas", "pyarrow", "read_parquet/to_parquet raise 'no usable engine'"),
]
R_PAIRS: list[tuple[str, str, str]] = []


def _deps(pack: Path) -> list[str]:
    """Every dep line in the pack, normalized to a bare package name.

    Deliberately a text scan rather than a YAML parse: the packs carry heavy
    inline commentary and version pins, and this guard must not start depending
    on a yaml import to be runnable in the hermetic lane.
    """
    out = []
    for raw in pack.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line.startswith("- "):
            continue
        spec = line[2:].strip().strip('"').strip("'")
        name = spec.split()[0].split("=")[0].split(">")[0].split("<")[0].strip()
        if name:
            out.append(name.lower())
    return out


def test_the_scanner_reads_real_pack_lines():
    """ARMED: a scanner that parses nothing reports every pack complete."""
    deps = _deps(PACKS / "python_bio.yaml")
    assert "pandas" in deps, deps[:20]
    assert "ipykernel" in deps, "the scanner is missing plain entries"
    # pins and quotes must normalize, not leak
    assert "numpy" in deps and not any(d.startswith('"') for d in deps)
    assert not any("<" in d or ">" in d or "=" in d for d in deps), deps


@pytest.mark.parametrize("lib,companion,breaks", PYTHON_PAIRS)
def test_python_pack_companions(lib, companion, breaks):
    deps = _deps(PACKS / "python_bio.yaml")
    if lib not in deps:
        pytest.skip(f"{lib} is not in the python base pack")
    assert companion in deps, (
        f"the python base pack has {lib} but not {companion}: {breaks}")


@pytest.mark.parametrize("lib,companion,breaks", R_PAIRS)
def test_r_pack_companions(lib, companion, breaks):  # pragma: no cover - empty today
    deps = _deps(PACKS / "r_bio.yaml")
    if lib not in deps:
        pytest.skip(f"{lib} is not in the R base pack")
    assert companion in deps, (
        f"the R base pack has {lib} but not {companion}: {breaks}")


def test_kernel_requirement_still_holds():
    """CEILING on this file's own churn: base_env documents that a python pack
    MUST carry ipykernel and an R pack r-irkernel, or the persistent kernel
    cannot start and every run silently degrades to stateless one-shot. Editing
    a pack is the moment that invariant gets broken."""
    assert "ipykernel" in _deps(PACKS / "python_bio.yaml")
    assert "r-irkernel" in _deps(PACKS / "r_bio.yaml")


def test_every_declared_pack_is_scannable():
    """WIDE: a pack that yields NO deps is a parse failure masquerading as a
    complete pack — the failure mode this whole file exists to prevent."""
    packs = sorted(PACKS.glob("*.yaml"))
    assert packs, f"no packs under {PACKS}"
    for p in packs:
        assert _deps(p), f"{p.name}: scanned zero deps"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ── the pack must PROVE what it advertises, not merely list it ──────────────

def _pack_docs():
    yaml = pytest.importorskip("yaml")
    for path in sorted(PACKS.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        if (doc.get("role") or "base") == "base":
            yield path.name, doc


def test_base_packs_verify_what_they_advertise():
    """The pairs above guard "is the companion LISTED". This guards the next
    question, which is the one that bit us: does the listed thing actually
    LOAD?

    Live, found 2026-08 while investigating an unrelated report: the R pack
    lists `bioconductor-deseq2`, `import_names` advertises DESeq2, and DESeq2
    has never once loaded in the published pack. Its dependency
    `bioconductor-genomeinfodbdata` ships as a 8.4 KB pair of scripts whose
    real payload is downloaded by a conda POST-LINK script — and the packer
    stages post-link scripts without running them. The package is present by
    every name-based check and empty in fact. Our scenario suite says
    `must_mention: [DESeq2]`, which an agent satisfies by talking about it.

    weft takes a `verify:` block on the spec ({import, loads, versions}) and
    enforces it as a realize postcondition, so a pack that cannot load what
    it advertises FAILS TO PUBLISH instead of shipping. The packs simply
    never carried one.

    The rule: whatever `import_names` advertises must appear in `verify` —
    that mapping IS the pack's public claim about what a user can load.
    """
    missing_block, unproven = [], []
    for name, doc in _pack_docs():
        spec = doc.get("spec") or {}
        verify = spec.get("verify") or {}
        langs = doc.get("languages") or []
        key = "loads" if "r" in langs else "import"
        proven = {str(x) for x in (verify.get(key) or [])}
        if not proven:
            missing_block.append(f"{name} (needs spec.verify.{key})")
            continue
        for advertised in (doc.get("import_names") or {}):
            if advertised not in proven:
                unproven.append(f"{name}: advertises {advertised!r}, "
                                f"verify.{key} does not prove it")

    assert not missing_block, (
        "base pack(s) ship with no load-check, so a package that is present "
        "in name but empty in fact publishes clean: " + "; ".join(missing_block))
    assert not unproven, "; ".join(unproven)
