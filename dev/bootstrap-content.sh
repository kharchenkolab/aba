#!/usr/bin/env bash
# Bootstrap the content overlays declared in deployment.yaml. Idempotent
# — already-cloned overlays are left alone. For each layer with a `git:`
# URL and a missing path, runs `git clone`.
#
# Usage:
#   dev/bootstrap-content.sh
#
# Config lookup (first existing wins):
#   $ABA_DEPLOYMENT_YAML
#   /etc/aba/deployment.yaml
#   ~/.aba/deployment.yaml
#   <repo-root>/dev/deployment.yaml.dev
#
# Run from anywhere — paths resolve against the platform repo root.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${ABA_DEPLOYMENT_YAML:-}"
if [[ -z "${CONFIG}" ]]; then
  for candidate in /etc/aba/deployment.yaml "$HOME/.aba/deployment.yaml" "$ROOT/dev/deployment.yaml.dev"; do
    if [[ -f "$candidate" ]]; then CONFIG="$candidate"; break; fi
  done
fi
if [[ -z "${CONFIG}" ]] || [[ ! -f "$CONFIG" ]]; then
  echo "bootstrap-content: no deployment.yaml found in the candidate chain — nothing to do"
  exit 0
fi

echo "bootstrap-content: using $CONFIG"

"$ROOT/.venv/bin/python" - <<'PY' "$CONFIG" "$ROOT"
import sys, subprocess, yaml
from pathlib import Path
config = Path(sys.argv[1])
repo_root = Path(sys.argv[2])
data = yaml.safe_load(config.read_text()) or {}
for entry in (data.get("layers") or []):
    name = (entry.get("name") or "").strip()
    p = (entry.get("path") or "").strip()
    git_url = (entry.get("git") or "").strip()
    if not name or not p:
        continue
    path = Path(p).expanduser()
    if not path.is_absolute():
        path = (repo_root / path).resolve()
    if path.exists():
        print(f"[{name}] OK at {path} (already present)")
        continue
    if not git_url:
        print(f"[{name}] MISSING at {path} — no git: URL declared; skipping")
        continue
    print(f"[{name}] cloning {git_url} → {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["git", "clone", git_url, str(path)])
    print(f"[{name}] cloned.")
PY
