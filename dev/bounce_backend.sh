#!/usr/bin/env bash
# Bounce the ABA backend uvicorn cleanly + fast.
#
# Why this exists: the Claude Code Bash tool deadlocks if a backgrounded
# child still owns shared file descriptors, so plain `nohup foo &` from a
# multi-command chain hangs. `setsid` plus explicit fd redirection breaks
# the link cleanly. Polling the health endpoint (with a hard timeout cap)
# is also faster + more reliable than `sleep N`.
#
# Usage:
#   dev/bounce_backend.sh                 # kill + start, default :8000
#   dev/bounce_backend.sh --port 8001     # alt port
#   PORT=8000 dev/bounce_backend.sh
#
# Reads /workspace/aba/.env automatically (ANTHROPIC_API_KEY +
# ABA_LLM_CREDENTIAL etc).

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
case "${1:-}" in
  --port) PORT="$2" ;;
esac

LOG="/tmp/uvicorn.log"
HEALTH="http://localhost:${PORT}/api/health"
PATTERN="[u]vicorn main:app"
KILL_DEADLINE=3   # seconds to wait for graceful SIGTERM
UP_DEADLINE=15    # seconds to wait for the new process to answer 200

# --- 1. Kill any existing uvicorn ------------------------------------
if pgrep -f "$PATTERN" >/dev/null; then
  pkill -f "$PATTERN" || true
  # poll for death rather than sleep blindly
  for _ in $(seq 1 $((KILL_DEADLINE * 10))); do
    pgrep -f "$PATTERN" >/dev/null || break
    sleep 0.1
  done
  if pgrep -f "$PATTERN" >/dev/null; then
    pkill -9 -f "$PATTERN" || true
    sleep 0.2
  fi
fi

# --- 2. Start the new one --------------------------------------------
# setsid -f: fork into a new session, detach from this shell's process
# group. Without this, the Bash tool wrapper waits for our child even
# with `&` + `disown`.
set -a
# shellcheck disable=SC1091
source "$ROOT/.env"
set +a

cd "$ROOT/backend"
setsid -f "$ROOT/.venv/bin/uvicorn" main:app \
  --host 0.0.0.0 --port "$PORT" --reload \
  --reload-exclude 'vendor/*' --reload-exclude 'envs/*' \
  --reload-exclude 'data/*' --reload-exclude 'work/*' \
  >"$LOG" 2>&1 < /dev/null

# --- 3. Wait for health ----------------------------------------------
t0=$(date +%s)
ok=0
for _ in $(seq 1 $((UP_DEADLINE * 4))); do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 1 "$HEALTH" 2>/dev/null || true)
  if [ "$code" = "200" ]; then
    ok=1
    break
  fi
  sleep 0.25
done
t1=$(date +%s)
elapsed=$((t1 - t0))

# --- 4. Report -------------------------------------------------------
echo "port      : $PORT"
echo "elapsed   : ${elapsed}s"
echo "health    : $([ $ok -eq 1 ] && echo "HTTP 200" || echo "FAILED (no 200 in ${UP_DEADLINE}s)")"
echo "mode line : $(grep -m1 -E "live-agent credential mode" "$LOG" || echo "(not logged yet)")"
echo "pid       : $(pgrep -f "$PATTERN" | head -1)"
echo "log       : $LOG"
[ $ok -eq 1 ] || { echo "--- last 15 log lines ---"; tail -15 "$LOG"; exit 1; }
