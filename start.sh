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
"$ROOT/.venv/bin/uvicorn" main:app --host 0.0.0.0 --port 8000 --reload &
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
