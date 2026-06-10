"""Regression test for the dev/bounce_backend.sh + start.sh reload-exclude args.

The old pattern `'envs/*'` did NOT exclude nested files (e.g.
`envs/pylib/natsort/x.py`), because Python's `Path.match` uses
last-N-components semantics — and uvicorn's loader only routes the
value into `exclude_dirs` (which excludes ALL descendants) when
`Path(value).is_dir()` is True. With the wildcard, it isn't a real
path → goes into the pattern list → misses nested paths.

Live consequence: an `ensure_capability` pip install touches files
deep under `envs/pylib/lib/pythonX.Y/site-packages/...`, WatchFiles
fires, uvicorn reloads the worker mid-session, the LLM stream and
any background jobs die. Diagnosed 2026-06-09 in prj_0ea773b4 — the
backend produced a `WARNING: WatchFiles detected changes in
'envs/pylib/natsort/...'` line and shut down right in the middle of
the user's chat.

Fix: pass bare directory names (`envs`, `vendor`, `data`, `work`) so
the loader takes the is_dir() branch and `exclude_dir in path.parents`
matches every descendant.
"""
from pathlib import Path

import pytest

# Platform-tier: this test exercises uvicorn's reload filter, which is
# domain-neutral (no bio content imports). Wave 2 §5.1 marks platform
# tests so a CI run `pytest -m platform` exercises just this tier.
pytestmark = pytest.mark.platform


def _make_filter(exclude_args, cwd=None):
    """Stand up uvicorn's FileFilter with the given --reload-exclude values.

    Uvicorn resolves paths relative to the launcher's cwd (since the
    args are passed as relative globs/names). We chdir into a temp
    layout that mirrors backend/'s symlinked envs/ for fidelity.
    """
    from uvicorn.config import Config
    from uvicorn.supervisors.watchfilesreload import FileFilter
    cfg_kwargs = dict(app="x:y", reload=True, reload_excludes=list(exclude_args))
    c = Config(**cfg_kwargs)
    if cwd:
        import os
        os.chdir(cwd)
    return FileFilter(c)


@pytest.fixture
def envs_tree(tmp_path, monkeypatch):
    """Build a fake project tree with an envs/ dir mirroring the live layout."""
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "main.py").write_text("# stand-in for the real main.py\n")
    # envs/ exists as a real directory (in prod it's a symlink to the
    # runtime envs root; for the filter test only the is_dir() check
    # matters and works the same either way).
    deep = backend / "envs" / "pylib" / "lib" / "python3.12" / "site-packages" / "natsort"
    deep.mkdir(parents=True)
    (deep / "compat.py").write_text("# fake\n")
    monkeypatch.chdir(backend)
    return backend


def test_old_pattern_misses_nested(envs_tree):
    """The OLD `'envs/*'` arg silently fails to exclude `envs/pylib/.../x.py`."""
    f = _make_filter(["envs/*", "vendor/*", "data/*", "work/*"])
    nested = Path("envs/pylib/lib/python3.12/site-packages/natsort/compat.py")
    # `True` from FileFilter means "watch this path" — the bug we
    # diagnosed: a deep envs file still triggers the reload watcher.
    assert f(nested) is True, "OLD pattern is buggy; expected watcher to keep watching nested envs/ paths"


def test_new_pattern_excludes_nested(envs_tree):
    """The NEW bare-dir-name arg DOES exclude any descendant under envs/."""
    f = _make_filter(["envs", "vendor", "data", "work"])
    nested = Path("envs/pylib/lib/python3.12/site-packages/natsort/compat.py")
    assert f(nested) is False, "NEW pattern must exclude descendants of envs/"


def test_new_pattern_still_watches_source(envs_tree):
    """Sanity: source .py files outside the excluded dirs are STILL watched."""
    src = envs_tree / "core" / "runtime" / "agent.py"
    src.parent.mkdir(parents=True)
    src.write_text("# fake source\n")
    f = _make_filter(["envs", "vendor", "data", "work"])
    assert f(Path("core/runtime/agent.py")) is True


def test_new_pattern_excludes_direct_child_too(envs_tree):
    """The original `'envs/*'` behavior (direct child only) is preserved as a subset."""
    f = _make_filter(["envs", "vendor", "data", "work"])
    direct = Path("envs/some_top.py")
    assert f(direct) is False
