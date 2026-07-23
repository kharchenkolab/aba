"""The SIF must carry the operator scripts it tells operators to run.

`INSTALL.md` §4 says the science env packs are published "from an aba checkout
OR RUNTIME" — but the image shipped `backend/ bin/ install/ installation/ ood/
system_bundle/ tools/ vendor/` and NO `scripts/`, so the runtime half did not
exist: `publish_base_packs.py` was absent and a git checkout was mandatory.
Live 2026-07-23, standing up /resources/aba: publishing r-bio required
bind-mounting a checkout into the container.

Two independent things must hold, and BOTH are needed — the trap here is that
`%files` in the generated def is an EXPLICIT list, so staging a directory
without adding a copy line silently ships nothing:

  1. the build STAGES scripts/ out of the repo, and
  2. the generated `%files` block COPIES it to /opt/aba/scripts.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "install" / "sif" / "build.sh"

pytestmark = pytest.mark.platform


def _src() -> str:
    return BUILD.read_text()


def test_the_command_install_md_documents_actually_exists():
    """Arming: this whole guard is pointless if the script were renamed."""
    assert (ROOT / "scripts" / "publish_base_packs.py").is_file(), (
        "scripts/publish_base_packs.py is gone — INSTALL.md §4 references it; "
        "update both or this guard protects nothing")


def test_build_stages_the_scripts_dir():
    s = _src()
    assert re.search(r'cp -a "\$REPO_ROOT/scripts" "\$STAGE/scripts"', s), (
        "install/sif/build.sh no longer stages scripts/ — the deployment loses "
        "publish_base_packs.py and an operator needs a git checkout")


def test_files_block_copies_scripts_into_the_image():
    """THE trap: %files is an explicit list. Staging without a copy line here
    ships nothing, and the failure is invisible until someone looks inside the
    image."""
    s = _src()
    assert re.search(r'\$STAGE/scripts /opt/aba/scripts', s), (
        "%files has no line copying $STAGE/scripts to /opt/aba/scripts — "
        "staging alone does NOT put a directory in the image")


def test_pycache_is_not_shipped():
    """Degenerate input: the repo's scripts/ carries a __pycache__ after any
    local run. Shipping stale .pyc into an image is noise at best."""
    s = _src()
    assert 'rm -rf "$STAGE/scripts/__pycache__"' in s, (
        "scripts/__pycache__ is staged into the image; drop it after the copy")


def test_every_files_entry_has_a_staged_source():
    """WIDE: the inverse error — a %files line whose $STAGE source is never
    produced. Apptainer fails the build on a missing source, so this catches a
    broken def before a 15-minute build does."""
    s = _src()
    listed = set(re.findall(r'"\s*\$STAGE/([A-Za-z0-9_.\-]+)', s))
    # names that appear ONLY inside %files echo lines
    copied = set(re.findall(r'echo "\s+\$STAGE/([A-Za-z0-9_.\-]+)', s))
    orphans = sorted(c for c in copied if c not in listed)
    assert not orphans, f"%files copies sources nothing stages: {orphans}"
