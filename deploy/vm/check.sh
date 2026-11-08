#!/usr/bin/env bash
set -uo pipefail

ROOT="${PMQE_HOME:-$HOME/pmqe}"
STATE="${PMQE_STATE_DIR:-$HOME/pmqe-state}"

if [ -f "$STATE/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$STATE/.env"
  set +a
fi

PORT="${PORT:-8765}"
STATUS_FILE="$STATE/runtime_status.json"

printf 'PMQE health summary\n'
printf 'time:  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'root:  %s\n' "$ROOT"
printf 'state: %s\n' "$STATE"
printf '\nunits:\n'

BOT_ACTIVE="unknown"
WEB_ACTIVE="unknown"
TIMER_ACTIVE="unknown"
LOG_TIMER_ACTIVE="unknown"

if command -v systemctl >/dev/null 2>&1; then
  for unit in pmqe-bot.service pmqe-web.service pmqe-heal.timer pmqe-logsync.timer pmqe-publish.timer; do
    active="$(systemctl is-active "$unit" 2>/dev/null || true)"
    enabled="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
    printf '  %-20s active=%s enabled=%s\n' "$unit" "${active:-unknown}" "${enabled:-unknown}"
    case "$unit" in
      pmqe-bot.service) BOT_ACTIVE="$active" ;;
      pmqe-web.service) WEB_ACTIVE="$active" ;;
      pmqe-heal.timer) TIMER_ACTIVE="$active" ;;
      pmqe-logsync.timer) LOG_TIMER_ACTIVE="$active" ;;
    esac
  done
else
  printf '  systemctl not available\n'
fi

printf '\nruntime:\n'
python3 - "$STATUS_FILE" <<'PY'
import json
import os
import sys
import time

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as handle:
        status = json.load(handle)
except Exception as exc:
    print("  status: missing or unreadable (%s)" % exc)
    print("  health: FAIL")
    sys.exit(2)

now = time.time()
try:
    generated = float(status.get("generated_ts") or 0)
except (TypeError, ValueError):
    generated = 0.0
if generated <= 0:
    try:
        generated = os.path.getmtime(path)
    except OSError:
        generated = 0.0
age = max(0.0, now - generated)
mode = str(status.get("mode") or "?")

freshness = status.get("freshness") or {}
btc_feed = freshness.get("btc_feed") or {}
poly_feed = freshness.get("polymarket_feed") or {}

def btc(key):
    if key in freshness:
        return freshness.get(key)
    return btc_feed.get(key)

def poly(key):
    if key in freshness:
        return freshness.get(key)
    return poly_feed.get(key)

btc_healthy = btc("btc_healthy_source_count")
btc_min = btc("btc_min_healthy_sources")
btc_quorum = btc("btc_price_quorum_met")
btc_stale = btc("btc_price_stale")
btc_age = btc("btc_price_age_seconds")
btc_sources = btc("btc_healthy_sources")
book_stale = poly("polymarket_book_stale")
book_idle = poly("polymarket_book_idle")
book_age = poly("polymarket_book_age_seconds")

print("  last status: %.1fs ago (%s)" % (age, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(generated))))
print("  mode: %s" % mode)
print("  btc feeds: healthy=%s min=%s quorum=%s stale=%s age=%s sources=%s" % (
    btc_healthy, btc_min, btc_quorum, btc_stale, btc_age, btc_sources))
print("  polymarket: stale=%s idle=%s age=%s" % (book_stale, book_idle, book_age))

total = status.get("total_pnl")
if isinstance(total, (int, float)):
    print("  paper pnl: total=%.4f USDC" % total)
else:
    print("  paper pnl: n/a")

tracks = status.get("tracks") or []
for track in tracks:
    win = track.get("win_stats") or {}
    bank = track.get("bankroll") or {}
    history = track.get("pnl_history") or []
    pnl = history[-1] if history else None
    print("    %-16s trades=%s win_rate=%s pnl=%s bankroll=%s" % (
        track.get("name", "?"),
        win.get("trades"),
        win.get("win_rate"),
        pnl,
        bank.get("current"),
    ))

if age > 120 or mode.upper() == "LIVE":
    print("  health: FAIL")
    sys.exit(2)
if btc_stale is True or btc_quorum is False or (book_stale is True and book_idle is not True):
    print("  health: DEGRADED")
    sys.exit(1)
print("  health: OK")
PY
HEALTH_RC=$?

printf '\npublic status:\n'
if [ -f "$STATE/publish_last_success" ]; then
  printf '  last publish: %s (commit %s)\n' \
    "$(sed -n '1p' "$STATE/publish_last_success")" "$(sed -n '3p' "$STATE/publish_last_success")"
else
  printf '  last publish: never\n'
fi
if [ -f "$STATE/publish_last_error" ]; then
  printf '  last error: %s\n' "$(sed -n '2p' "$STATE/publish_last_error")"
fi

printf '\nrecent errors:\n'
if command -v journalctl >/dev/null 2>&1; then
  journal_errors="$(journalctl -u pmqe-bot.service -p warning -n 8 --no-pager -o cat 2>/dev/null || true)"
  if [ -n "$journal_errors" ]; then
    printf '%s\n' "$journal_errors" | sed 's/^/  /'
  else
    printf '  none\n'
  fi
else
  printf '  journalctl not available\n'
fi

LOG_FILE="$STATE/logs/bot.log"
if [ -f "$LOG_FILE" ]; then
  log_errors="$(tail -n 200 "$LOG_FILE" 2>/dev/null | grep -E 'ERROR|CRITICAL|Traceback' | tail -n 5 || true)"
  if [ -n "$log_errors" ]; then
    printf '  log file:\n'
    printf '%s\n' "$log_errors" | sed 's/^/    /'
  fi
fi

printf '\nbackup:\n'
if [ -s "$STATE/logsync_last_success" ]; then
  push_iso=""
  push_epoch=""
  push_commit=""
  {
    IFS= read -r push_iso || true
    IFS= read -r push_epoch || true
    IFS= read -r push_commit || true
  } < "$STATE/logsync_last_success"
  now="$(date +%s)"
  if [ -n "$push_epoch" ]; then
    push_age=$((now - push_epoch))
    printf '  last push: %s (%ss ago) commit=%s\n' "$push_iso" "$push_age" "${push_commit:-unknown}"
  else
    printf '  last push: %s\n' "${push_iso:-unknown}"
  fi
else
  printf '  last push: none yet\n'
fi
if [ -s "$STATE/logsync_last_error" ]; then
  backup_error="$(tail -n 1 "$STATE/logsync_last_error" 2>/dev/null || true)"
  printf '  last error: %s\n' "$backup_error"
fi

printf '\nhealthz:\n'
if command -v curl >/dev/null 2>&1; then
  healthz="$(curl -fsS --max-time 3 "http://127.0.0.1:$PORT/healthz" 2>/dev/null || true)"
  if [ -n "$healthz" ]; then
    printf '%s' "$healthz" | python3 -c 'import json,sys; print("  " + json.dumps(json.load(sys.stdin), sort_keys=True))' || printf '  %s\n' "$healthz"
  else
    printf '  unreachable on port %s\n' "$PORT"
  fi
else
  printf '  curl not available\n'
fi

if [ "$BOT_ACTIVE" != "active" ] && [ "$HEALTH_RC" -eq 0 ]; then
  HEALTH_RC=1
fi
if [ "$WEB_ACTIVE" != "active" ] && [ "$HEALTH_RC" -eq 0 ]; then
  HEALTH_RC=1
fi
if [ "$TIMER_ACTIVE" != "active" ] && [ "$HEALTH_RC" -eq 0 ]; then
  HEALTH_RC=1
fi
if [ "$LOG_TIMER_ACTIVE" != "active" ] && [ "$HEALTH_RC" -eq 0 ]; then
  HEALTH_RC=1
fi

if [ "$HEALTH_RC" -ge 2 ]; then
  RESULT="FAIL"
elif [ "$HEALTH_RC" -eq 1 ]; then
  RESULT="DEGRADED"
else
  RESULT="OK"
fi

printf '\nresult: %s\n' "$RESULT"
exit "$HEALTH_RC"
