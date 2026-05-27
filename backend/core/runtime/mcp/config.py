"""YAML config loader for MCP server definitions.

Schema:
  servers:
    - name: lakefs                # required; becomes the tool prefix
      command: python              # required; executable to spawn
      args: [-m, lakefs_mcp]       # optional; args after command
      env: {LAKEFS_URL: ...}       # optional; env overrides
      cwd: /opt/lakefs             # optional
      enabled: true                # optional; default true
      default_timeout_s: 30        # optional; per-call default timeout
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass(frozen=True)
class ServerConfig:
    name:    str
    command: str
    args:    tuple[str, ...] = ()
    env:     dict[str, str] = field(default_factory=dict)
    cwd:     Optional[str] = None
    enabled: bool = True
    default_timeout_s: int = 30


def load(path: Path) -> list[ServerConfig]:
    """Parse a servers.yaml. Missing file or empty `servers` → empty list
    (i.e. gateway becomes a no-op). Invalid entries raise ValueError so
    typos fail loudly at startup."""
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text()) or {}
    servers = raw.get("servers") or []
    if not isinstance(servers, list):
        raise ValueError(f"{path}: 'servers' must be a list")
    out: list[ServerConfig] = []
    for entry in servers:
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: server entry must be a mapping")
        name = (entry.get("name") or "").strip()
        if not name:
            raise ValueError(f"{path}: server is missing required 'name'")
        cmd = (entry.get("command") or "").strip()
        if not cmd:
            raise ValueError(f"{path}: server {name!r} missing required 'command'")
        out.append(ServerConfig(
            name=name,
            command=cmd,
            args=tuple(str(a) for a in (entry.get("args") or [])),
            env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
            cwd=entry.get("cwd"),
            enabled=bool(entry.get("enabled", True)),
            default_timeout_s=int(entry.get("default_timeout_s", 30)),
        ))
    return out
