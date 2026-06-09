#!/usr/bin/env bash
# Start ABA backend + frontend dev servers
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "ERROR: ANTHROPIC_API_KEY is not set"
  exit 1
fi

# Load nvm so we can use Node 20
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

echo "Starting backend on :8000 ..."
cd "$ROOT/backend"
# --reload-exclude entries MUST be bare dir names (no '*'-glob): uvicorn's
# loader only routes them into exclude_dirs via Path(value).is_dir(), and
# the matcher then excludes any descendant. With 'envs/*' the value falls
# into pattern-list and Path.match's last-N-components semantics MISSES
# nested paths (e.g. envs/pylib/natsort/x.py), so pip-install fan-out
# during ensure_capability bounces the worker mid-session and kills the
# live LLM stream. See dev/bounce_backend.sh for the longer note.
"$ROOT/.venv/bin/uvicorn" main:app --host 0.0.0.0 --port 8000 --reload \
  --reload-exclude vendor --reload-exclude envs \
  --reload-exclude data --reload-exclude work &
BACKEND_PID=$!

echo "Starting frontend on :5173 ..."
cd "$ROOT/frontend"
npm run dev -- --host 0.0.0.0 &
FRONTEND_PID=$!

echo ""
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:5173"
echo ""
echo "Press Ctrl-C to stop both."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
