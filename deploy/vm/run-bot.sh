#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
PYTHON="$ROOT/.venv/bin/python"
STATE="${PMQE_STATE_DIR:-$HOME/pmqe-state}"
LOG_FILE="${PMQE_BOT_LOG:-$STATE/logs/bot.log}"

if [ ! -x "$PYTHON" ]; then
  printf '[pmqe] missing virtualenv interpreter: %s\n' "$PYTHON" >&2
  exit 1
fi

bash "$ROOT/deploy/vm/guard.sh"
mkdir -p "$(dirname "$LOG_FILE")"

# tee writes the file log while its stdout stays attached to the journal.
exec > >(tee -a "$LOG_FILE") 2>&1
exec "$PYTHON" "$ROOT/bot.py" \
  --shadow-live \
  --models "${PMQE_MODELS:-A,B,C}" \
  --no-dashboard
