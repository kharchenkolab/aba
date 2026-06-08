"""Pytest fixtures for the aba_installer test suite.

Every test gets a fresh tempdir as ABA_HOME so config files, logs, and
the port-state file don't leak between tests or pollute the user's
real ~/Library/Application Support/ABA/ directory.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def tmp_aba_home(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    # Force re-resolution: paths.aba_home() reads env on each call so we
    # don't need to reload the module.
    yield tmp_path


@pytest.fixture
def helper_src_on_path():
    """Add the helper's src/ to sys.path so `import aba_installer` works
    when tests are invoked via plain `pytest` from anywhere."""
    here = Path(__file__).resolve()
    src = here.parents[1] / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    yield src
