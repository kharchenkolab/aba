"""Make `backend/tests/` importable from the REPO ROOT, not just from `backend/`.

These tests import platform modules by their in-package names (`from core.compute
import named_envs`), which only resolves when `backend/` is on sys.path — i.e.
when pytest is invoked with `backend` as the working directory. `scripts/
run_guard_tests.sh` runs every file from the repo root, so without this the whole
directory was unrunnable there and therefore unlistable in the guard suite.

That is not hypothetical: two guards in `test_named_envs_ready.py` had been red
since `_run_realize_task` grew a second return value, and nothing anywhere ran
them to notice. Same class as the frontend suite executing one file of forty.
Repo-root `tests/` bootstraps sys.path per-file instead; this does it once for
the directory, so a new file here is reachable without remembering anything.
"""
import sys
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parents[1])
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
