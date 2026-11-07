#!/usr/bin/env bash
set -euo pipefail

ROOT="${PMQE_HOME:-$HOME/pmqe}"
STATE="${PMQE_STATE_DIR:-$HOME/pmqe-state}"
BOOTSTRAP="$ROOT/deploy/vm/bootstrap.sh"
FALLBACK_URL="${PMQE_BOOTSTRAP_URL:-https://raw.githubusercontent.com/Segzman/prediction-market-quant-engine/main/deploy/vm/bootstrap.sh}"
UNITS_DIR="/etc/systemd/system"

missing=0
[ -x "$ROOT/.venv/bin/python" ] || missing=1
[ -f "$BOOTSTRAP" ] || missing=1
for unit in pmqe-bot.service pmqe-web.service pmqe-heal.service pmqe-heal.timer; do
  [ -f "$UNITS_DIR/$unit" ] || missing=1
done

if [ "$missing" -eq 1 ]; then
  printf '[pmqe-heal] missing deployment pieces, running bootstrap\n'
  if [ -f "$BOOTSTRAP" ]; then
    exec env PMQE_HOME="$ROOT" PMQE_STATE_DIR="$STATE" bash "$BOOTSTRAP"
  fi
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  curl -fsSL "$FALLBACK_URL" -o "$tmp"
  exec env PMQE_HOME="$ROOT" PMQE_STATE_DIR="$STATE" bash "$tmp"
fi

if ! systemctl is-enabled --quiet pmqe-bot.service; then
  printf '[pmqe-heal] pmqe-bot.service is disabled, leaving it stopped\n'
  exit 0
fi

if ! systemctl is-active --quiet pmqe-bot.service; then
  printf '[pmqe-heal] pmqe-bot.service inactive, starting it\n'
  systemctl start pmqe-bot.service || true
fi

STATUS_FILE="$STATE/runtime_status.json"
stale=1
if [ -f "$STATUS_FILE" ]; then
  age="$(python3 - "$STATUS_FILE" <<'PY'
import json
import os
import sys
import time

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        payload = json.load(handle)
    generated = float(payload.get("generated_ts") or 0)
    if generated <= 0:
        generated = os.path.getmtime(sys.argv[1])
    print(int(max(0, time.time() - generated)))
except Exception:
    print(999999)
PY
)"
  if [ "$age" -le 120 ]; then
    stale=0
  fi
fi

if [ "$stale" -eq 1 ]; then
  printf '[pmqe-heal] runtime status is stale, restarting pmqe-bot.service\n'
  if systemctl is-active --quiet pmqe-bot.service; then
    systemctl restart pmqe-bot.service
  else
    systemctl start pmqe-bot.service
  fi
fi
