#!/usr/bin/env bash
set -euo pipefail

dry_run="${DRY_RUN:-True}"
dry_lower="$(printf '%s' "$dry_run" | tr '[:upper:]' '[:lower:]')"

case "$dry_lower" in
  1|true|yes|on)
    exit 0
    ;;
esac

if [ "${ALLOW_LIVE:-0}" = "1" ]; then
  printf '[pmqe] WARNING: DRY_RUN=%s with ALLOW_LIVE=1. This kit still starts the bot in --shadow-live mode; live trading is out of scope.\n' "$dry_run" >&2
  exit 0
fi

printf '[pmqe] refusing to start: DRY_RUN=%s. Set DRY_RUN=True or explicitly set ALLOW_LIVE=1.\n' "$dry_run" >&2
exit 78
