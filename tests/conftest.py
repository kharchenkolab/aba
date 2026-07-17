"""Shared pytest setup.

Wave 2 A.3: guide.py now reads `active_pack()` at the top of
stream_response. Tests that exercise stream_response (or import guide.py
in a way that runs it) would fail with "no content pack registered" if
nothing set it up. The production path is main.py startup; for tests
we do the same here, once per process.

Tests marked @pytest.mark.platform that don't need bio CAN still run
without this — they just shouldn't be importing guide.py. The
platform-purity gate (tests/test_platform_test_imports.py) catches
that case.
"""
from __future__ import annotations

import os
import sys

# Make backend/ importable from tests/ — mirrors what main.py does as
# the live entry point. The standalone-script tests do this via a
# `sys.path.insert(0, ...)` block at the top of each file; pytest-
# discovered tests benefit from a single conftest line.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# Force the test session onto a throwaway runtime so a sourced .env's
# ABA_RUNTIME_DIR can't make test runs create projects / scribe-mirrors under the
# OPERATOR's live runtime — the source of hundreds of junk projects polluting the
# live instance. With lazy core.config this takes effect on every dir access; the
# per-module fixture re-pins from each script-style module's own _tmp on top.
import tempfile  # noqa: E402
os.environ["ABA_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="aba_test_runtime_")
for _k in ("ABA_PROJECTS_DIR", "DATA_DIR", "ARTIFACTS_DIR", "ABA_WORK_DIR",
           "ABA_ENVS_DIR", "ABA_REFS_DIR"):
    os.environ.pop(_k, None)


import pytest


@pytest.fixture(autouse=True, scope="module")
def _isolated_module_db(request, tmp_path_factory):
    """Give each test MODULE its own fresh, initialized runtime + SQLite DB.

    Many tests are script-style: they configure the runtime via `os.environ`
    (`ABA_RUNTIME_DIR`, `ABA_DB_PATH`, …) at module-import time, expecting to be
    the first thing imported. Under pytest this conftest imports `content.bio`
    first (for pack registration), and all test modules are imported during
    collection — so the global env ends up as the *last* module's, and each
    module's own captured `_tmp` (e.g. `PROOT = _tmp/'projects'`) diverges from
    it. Two-part fix, working WITH lazy core.config (which resolves dirs from the
    live env):
      1. Re-pin this module's runtime dirs from its `_tmp` so lazy config
         resolves the same paths the module captured at import.
      2. Re-point the process-global DB to a fresh per-module file (the
         one-DB-per-script model). Project-binding tests override via a
         context-var (`projects.bind`), so they're unaffected."""
    import os
    tmp = getattr(request.module, "_tmp", None)
    if tmp:
        tmp = str(tmp)
        os.environ["ABA_RUNTIME_DIR"] = tmp
        os.environ["ABA_PROJECTS_DIR"] = os.path.join(tmp, "projects")
        os.environ["ARTIFACTS_DIR"] = os.path.join(tmp, "artifacts")
        os.environ["DATA_DIR"] = os.path.join(tmp, "data")
        os.environ["ABA_WORK_DIR"] = os.path.join(tmp, "work")
        os.environ["ABA_ENVS_DIR"] = os.path.join(tmp, "envs")
        os.environ["ABA_REFS_DIR"] = os.path.join(tmp, "refs")
    try:
        from core.graph import _schema
    except Exception:  # backend not importable yet
        yield
        return
    db = tmp_path_factory.mktemp("aba_module_db") / "test.db"
    _schema.set_db_path(db)
    try:
        _schema.init_db()
    except Exception:
        pass
    yield


def _register_bio_pack_once() -> None:
    """Idempotent: registering the same pack twice is a no-op; trying
    to register a DIFFERENT pack raises (test would have to clear
    state via clear_active_pack_for_testing first)."""
    try:
        from core.runtime.content_pack import active_pack, set_active_pack
    except ImportError:
        return  # backend not on path yet (very early collection)
    try:
        active_pack()
        return  # already set — fine
    except RuntimeError:
        pass
    try:
        from content.bio import BIO_PACK
    except ImportError:
        # No bio? Then nothing to register. Platform-tier tests run.
        return
    set_active_pack(BIO_PACK)
    BIO_PACK.register_hooks()


_register_bio_pack_once()


# Point the process-global DB at a valid, writable test DB at conftest LOAD time,
# so script-style modules that do DB work at IMPORT time (`init_db()` /
# create_entity at module scope) don't hit "unable to open database file" before
# the per-module fixture runs. The fixture re-points to a fresh per-module DB for
# actual test isolation; this is just the import-time floor.
def _seed_valid_global_db() -> None:
    try:
        import os
        import tempfile
        from core.graph import _schema
        _schema.set_db_path(os.path.join(
            tempfile.mkdtemp(prefix="aba_conftest_db_"), "conftest.db"))
        _schema.init_db()
    except Exception:
        pass


_seed_valid_global_db()


# Deterministic credentials in tests: never probe the developer's macOS
# Keychain for the Claude Code CLI token — real dev machines have one, CI
# doesn't, and "no credentials configured" assertions must mean it.
try:
    from core import llm as _llm_for_tests
    _llm_for_tests._CLI_KEYCHAIN_ENABLED = False
    _llm_for_tests._CLI_FILE_ENABLED = False   # ~/.claude/.credentials.json — real on dev boxes;
    #                                            reading it breaks "no credentials" tests AND leaks
    #                                            the developer's token into the assertion diff.
except Exception:  # noqa: BLE001 — llm import problems surface in real tests
    pass
