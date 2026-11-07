#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
PYTHON="$ROOT/.venv/bin/python"
STATE="${PMQE_STATE_DIR:-$HOME/pmqe-state}"
HOST="${PMQE_WEB_HOST:-0.0.0.0}"
PORT="${PORT:-8765}"
LOG_FILE="${PMQE_WEB_LOG:-$STATE/logs/web.log}"

if [ ! -x "$PYTHON" ]; then
  printf '[pmqe] missing virtualenv interpreter: %s\n' "$PYTHON" >&2
  exit 1
fi

mkdir -p "$(dirname "$LOG_FILE")"
cd "$ROOT"

args=(--host "$HOST" --port "$PORT" --state-dir "$STATE")
if [ "${PMQE_WEB_ALLOW_CONTROLS:-0}" != "1" ]; then
  args+=(--read-only)
fi

exec > >(tee -a "$LOG_FILE") 2>&1
exec "$PYTHON" -m webapp.server "${args[@]}"
