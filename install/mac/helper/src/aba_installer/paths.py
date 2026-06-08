"""Where things live on disk.

All paths under ABA_HOME (default: ~/Library/Application Support/ABA/).
Override via ABA_HOME env for tests.
"""
from __future__ import annotations
import os
from pathlib import Path


def aba_home() -> Path:
    """Root directory for ABA on this Mac."""
    p = os.environ.get("ABA_HOME")
    if p:
        return Path(p)
    return Path.home() / "Library" / "Application Support" / "ABA"


def installer_dir() -> Path:
    d = aba_home() / "installer"
    d.mkdir(parents=True, exist_ok=True)
    return d


def env_dir() -> Path:
    """The micromamba env where ABA runs."""
    return aba_home() / "env"


def repo_dir() -> Path:
    return aba_home() / "repo"


def runtime_dir() -> Path:
    return aba_home() / "runtime"


def logs_dir() -> Path:
    d = aba_home() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_env() -> Path:
    """The single config file sourced by the launcher; contains API key +
    runtime paths. Mode 0600 once written."""
    return aba_home() / "config.env"


def port_file() -> Path:
    """Persists the helper's chosen port across restarts so the LaunchAgent
    and any browser bookmark resolve consistently."""
    return installer_dir() / "port.txt"
