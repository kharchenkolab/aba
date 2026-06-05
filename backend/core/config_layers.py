"""Content-layer configuration loader (misc/content_layers.md).

A *content layer* is a directory tree with the same shape as
`backend/content/bio/library/` — `core/`, `recipes/`, `vendor/`,
optionally `CONTEXT.md` + `policies.yaml` + `catalog/`. ABA stacks
layers lowest-to-highest precedence; higher layers override on
name collision.

L-A scope (this file): just READ the list of layers from a YAML
config (default empty → today's single-layer behaviour); return a
list of `ContentLayer` records. No git pull, no auto-clone, no
policies merge — those come in L-B / L-D / L-C respectively.

Config sources, in order of precedence:
  1. `$ABA_DEPLOYMENT_YAML` env var → path to a YAML file
  2. `/etc/aba/deployment.yaml`
  3. `~/.aba/deployment.yaml`
  4. `<repo_root>/dev/deployment.yaml.dev`  (for local development)
  5. None → empty list → only the system layer loads

YAML shape:

    layers:
      - name: aba-recipes
        path: /srv/aba/content/aba-recipes
        git: https://github.com/kharchenkolab/aba-recipes   # L-D
        ref: v2024-q3                                       # L-D
      - name: institution
        path: /srv/aba/content/institution
        git: https://github.com/<inst>/aba-overlay
        ref: main

Order in the YAML = order in registration = precedence (later wins).
The implicit *system* layer is always first; it lives in the source
repo and is never declared in the YAML.

A layer whose `path` doesn't exist on disk is *skipped silently* with
a startup log line — keeps `aba-bootstrap` and the actual server
decoupled (bootstrap can clone; if it hasn't run yet, ABA still
boots, just with fewer layers).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ContentLayer:
    """One content layer. L-A: just identity + path. L-D will add
    git_url / ref / pulled_sha so refresh-skills can git pull per layer."""
    name: str
    path: Path
    git_url: Optional[str] = None      # L-D
    ref: Optional[str] = None          # L-D

    def exists(self) -> bool:
        return self.path.exists() and self.path.is_dir()


def _candidate_config_paths(repo_root: Path) -> list[Path]:
    """Ordered candidates for the deployment.yaml — first existing wins."""
    out: list[Path] = []
    env = os.environ.get("ABA_DEPLOYMENT_YAML")
    if env:
        out.append(Path(env))
    out += [
        Path("/etc/aba/deployment.yaml"),
        Path.home() / ".aba" / "deployment.yaml",
        repo_root / "dev" / "deployment.yaml.dev",
    ]
    return out


def load_content_layers(repo_root: Optional[Path] = None) -> list[ContentLayer]:
    """Read the layer list from the first existing deployment.yaml in the
    candidate chain. Returns [] when no file is found (single-layer
    fallback — today's behaviour, all content from the source repo's
    `library/`)."""
    if repo_root is None:
        # backend/core/config_layers.py → repo_root is parents[2]
        repo_root = Path(__file__).resolve().parents[2]
    for candidate in _candidate_config_paths(repo_root):
        if candidate.exists():
            return _parse_yaml(candidate, repo_root=repo_root)
    return []


def _parse_yaml(path: Path, *, repo_root: Path) -> list[ContentLayer]:
    """Parse a deployment.yaml. Malformed file logs + returns [] (degraded
    operation > refusing to boot).

    Relative `path:` values are resolved against `repo_root` (the platform
    repo's top-level directory). This makes a sibling layout — clone
    aba-recipes next to the platform repo — work with `path: ../aba-recipes`
    regardless of which directory the operator invoked from. Absolute paths
    pass through unchanged."""
    try:
        import yaml
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as e:  # noqa: BLE001
        print(f"[layers] failed to read {path}: {e}; running with system layer only",
              flush=True)
        return []
    raw_layers = data.get("layers") or []
    out: list[ContentLayer] = []
    for entry in raw_layers:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip()
        p = (entry.get("path") or "").strip()
        if not name or not p:
            print(f"[layers] skipping malformed entry in {path}: {entry!r}", flush=True)
            continue
        resolved = Path(p).expanduser()
        if not resolved.is_absolute():
            resolved = (repo_root / resolved).resolve()
        out.append(ContentLayer(
            name=name,
            path=resolved,
            git_url=(entry.get("git") or "").strip() or None,
            ref=(entry.get("ref") or "").strip() or None,
        ))
    return out
