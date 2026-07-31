"""One kernel = one sandbox, so a language switch moves the directory that bare
filenames resolve against — and the orientation banner has to say so.

Live (2026-07-27, orbtest). Asked to compute a table in R and read it from Python
on the SAME machine, the agent wrote it with a bare name from R and read it with a
bare name from Python: FileNotFoundError. It recovered in three extra calls
(find_files, then a glob under the other kernel's directory), and the recovery is
itself the untracked-write shape — reaching across sandboxes by absolute path.

Nothing was broken; the location was merely unsayable. The banner promised "bare
names land in this kernel's sandbox", which is true and still reads as a
per-machine working directory. So the remote banner now names this thread's other
live sandboxes on the same site.

Guarded here at the banner seam, on the exact live shape, with the negative cases
that keep it from becoming noise: same language, other site, no sibling at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from content.bio.tools import run_exec  # noqa: E402

R_BOX = "/home/someone/.weft/kernels/krn_rrrrrrrrrr"
PY_BOX = "/home/someone/.weft/kernels/krn_pppppppppp"
SITE = "siteA"
TID = "thr_test"


class FakeSession:
    """Only what the banner reads. Deliberately NOT more permissive than the
    real session: a session with no `_aba_sandbox_cwd` (never probed) must be
    representable, because that is the shape on a kernel's very first block."""

    def __init__(self, lang, site, sandbox, alive=True):
        self.lang = lang
        self.site = site
        self.alive = alive
        if sandbox is not None:
            self._aba_sandbox_cwd = sandbox


@pytest.fixture
def pool(monkeypatch):
    """Swap the kernel pool for a list of fake sessions."""
    live: list = []

    class FakePool:
        def sessions_for_thread(self, tid):
            return list(live)

    import core.exec.kernels.pool as pool_mod
    monkeypatch.setattr(pool_mod, "get_pool", lambda: FakePool())
    return live


def _siblings(pool_list, lang="python", site=SITE):
    return run_exec._sibling_language_sandboxes(TID, site, lang)


# ── the resolver ─────────────────────────────────────────────────────────────

def test_the_live_shape_an_R_sandbox_is_visible_from_python(pool):
    pool.append(FakeSession("r", SITE, R_BOX))
    pool.append(FakeSession("python", SITE, PY_BOX))
    assert _siblings(pool, lang="python") == [("r", R_BOX)]


def test_symmetric_python_sandbox_visible_from_R(pool):
    pool.append(FakeSession("r", SITE, R_BOX))
    pool.append(FakeSession("python", SITE, PY_BOX))
    assert _siblings(pool, lang="r") == [("python", PY_BOX)]


def test_own_language_is_never_its_own_sibling(pool):
    """CEILING: naming your own sandbox as the place the OTHER language's files
    live would be worse than saying nothing."""
    pool.append(FakeSession("python", SITE, PY_BOX))
    assert _siblings(pool, lang="python") == []


def test_a_kernel_on_another_site_is_not_a_sibling(pool):
    """Its path exists on a different machine; offering it is the
    controller-path-leakage mistake in another costume."""
    pool.append(FakeSession("r", "siteB", R_BOX))
    assert _siblings(pool, lang="python") == []


def test_dead_sessions_are_skipped(pool):
    pool.append(FakeSession("r", SITE, R_BOX, alive=False))
    assert _siblings(pool, lang="python") == []


def test_a_never_probed_sibling_is_skipped(pool):
    """WIDE — the degenerate shape: the sandbox is learned from a probe, so a
    kernel that has not run one yet has no path. Emitting an empty/None path
    would send the reader to `None/x.csv`."""
    pool.append(FakeSession("r", SITE, None))
    assert _siblings(pool, lang="python") == []
    pool.clear()
    pool.append(FakeSession("r", SITE, ""))
    assert _siblings(pool, lang="python") == []


def test_a_broken_pool_does_not_break_the_banner(monkeypatch):
    """Orientation is best-effort; a substrate hiccup must not cost the agent
    the whole banner."""
    import core.exec.kernels.pool as pool_mod

    class Boom:
        def sessions_for_thread(self, tid):
            raise RuntimeError("pool unavailable")

    monkeypatch.setattr(pool_mod, "get_pool", lambda: Boom())
    assert run_exec._sibling_language_sandboxes(TID, SITE, "python") == []


# ── the banner ───────────────────────────────────────────────────────────────

def _banner(**kw):
    return run_exec._prior_run_files_preamble(
        "prj_test", TID, current_run_id=None, cwd="/some/cwd",
        fresh_kernel=True, **kw)


def test_the_banner_names_the_sibling_sandbox(pool):
    """THE regression: on the turn where the handoff happens, the reader is told
    where the other language's files are."""
    pool.append(FakeSession("r", SITE, R_BOX))
    text = _banner(remote_site=SITE, lang="python")
    assert "CROSS-LANGUAGE" in text
    assert R_BOX in text, "the absolute path is the whole point"
    assert "own sandbox" in text


def test_the_banner_stays_quiet_with_no_sibling(pool):
    """ARMED against noise: every single-language remote turn would otherwise
    carry a paragraph about a handoff that isn't happening. A guard that only
    checked the positive case would not notice."""
    text = _banner(remote_site=SITE, lang="python")
    assert "CROSS-LANGUAGE" not in text
    # ...and the rest of the remote guidance is untouched
    assert "BARE RELATIVE filenames" in text


def test_a_LOCAL_turn_never_gets_the_cross_language_line(pool):
    """The local lane shares one working directory across languages, so the
    warning would be false there."""
    pool.append(FakeSession("r", SITE, R_BOX))
    text = _banner(remote_site=None, lang="python")
    assert "CROSS-LANGUAGE" not in text


def test_lang_absent_degrades_to_silence(pool):
    """WIDE — the absent-optional shape: a caller that cannot say which language
    it is must not get a line claiming EVERY live sandbox is a sibling."""
    pool.append(FakeSession("r", SITE, R_BOX))
    pool.append(FakeSession("python", SITE, PY_BOX))
    text = _banner(remote_site=SITE)          # lang omitted
    assert "CROSS-LANGUAGE" not in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
