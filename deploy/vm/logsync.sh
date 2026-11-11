#!/usr/bin/env bash
set -uo pipefail

MODE="${1:-sync}"

ROOT="${PMQE_HOME:-$HOME/pmqe}"
STATE="${PMQE_STATE_DIR:-$HOME/pmqe-state}"
LOGS_ROOT="${PMQE_LOGS_HOME:-$HOME/pmqe-logs}"
REPO_URL="${PMQE_LOGS_REPO:-git@pmqe-logs-gh:Segzman/pmqe-paper-logs.git}"
BRANCH="${PMQE_LOGS_BRANCH:-main}"
HOST="${PMQE_LOG_HOST:-$(hostname -s 2>/dev/null || hostname)}"
DATE="${PMQE_LOG_DATE:-$(date -u +%Y-%m-%d)}"
SSH_CONFIG="${PMQE_SSH_CONFIG:-$HOME/.ssh/config}"
STATUS_FILE="$STATE/logsync_last_success"
ERROR_FILE="$STATE/logsync_last_error"
LOG_FILE="$STATE/logs/logsync.log"

mkdir -p "$STATE/logs" 2>/dev/null || true
if [ "$MODE" != "--restore" ]; then
  exec > >(tee -a "$LOG_FILE") 2>&1
fi

say() {
  printf '[pmqe-logsync] %s\n' "$*"
}

fail() {
  local message="$1"
  local code="${2:-0}"
  say "ERROR: $message"
  printf '%s\n%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$message" > "$ERROR_FILE" 2>/dev/null || true
  exit "$code"
}

if [ -z "${GIT_SSH_COMMAND:-}" ]; then
  if [ -f "$SSH_CONFIG" ]; then
    export GIT_SSH_COMMAND="ssh -F $SSH_CONFIG -o BatchMode=yes -o ConnectTimeout=20"
  else
    export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=20"
  fi
fi

gitx() {
  git -c safe.directory="$LOGS_ROOT" -C "$LOGS_ROOT" "$@"
}

ensure_repo() {
  if [ ! -d "$LOGS_ROOT/.git" ]; then
    if [ -e "$LOGS_ROOT" ] && [ -n "$(ls -A "$LOGS_ROOT" 2>/dev/null || true)" ]; then
      say "logs path exists but is not a git checkout: $LOGS_ROOT"
      return 1
    fi
    say "cloning $REPO_URL into $LOGS_ROOT"
    mkdir -p "$(dirname "$LOGS_ROOT")"
    git clone --branch "$BRANCH" "$REPO_URL" "$LOGS_ROOT" || return 1
  fi
  gitx remote set-url origin "$REPO_URL" >/dev/null 2>&1 || true
  gitx fetch --prune origin "$BRANCH" || return 1
  if gitx show-ref --verify --quiet "refs/heads/$BRANCH"; then
    gitx checkout "$BRANCH" >/dev/null 2>&1 || return 1
  else
    gitx checkout -B "$BRANCH" FETCH_HEAD >/dev/null 2>&1 || return 1
  fi
  return 0
}

pull_rebase() {
  gitx pull --rebase --autostash origin "$BRANCH"
}

copy_file() {
  local src="$1"
  local rel="$2"
  [ -f "$src" ] || return 0
  mkdir -p "$(dirname "$DEST/$rel")"
  cp -f "$src" "$DEST/$rel"
}

scrub_dest() {
  [ -d "$DEST" ] || return 0
  local removed
  removed="$(find "$DEST" -type f \( \
    -name '.env' -o -name '.env.*' -o \
    -name '*_ed25519*' -o -name 'id_rsa*' -o -name 'id_ed25519*' -o \
    -name '*.pem' -o -name '*.key' \
    \) -print -delete 2>/dev/null | wc -l | tr -d ' ')"
  if [ "${removed:-0}" -gt 0 ]; then
    say "removed $removed sensitive file(s) from the backup tree"
  fi

  local file
  while IFS= read -r file; do
    [ -f "$file" ] || continue
    case "$file" in
      *.gz|*.zip) continue ;;
    esac
    if grep -IqE 'BEGIN (OPENSSH |RSA |EC |DSA )?PRIVATE KEY' "$file" 2>/dev/null; then
      rm -f "$file"
      say "removed file containing a private key: $(basename "$file")"
      continue
    fi
    scrub_tmp="$file.pmqe-scrub.$$"
    if sed -E       -e 's/(TYPESAFE_API_KEY|POLYMARKET_PRIVATE_KEY|POLYMARKET_SECRET|POLYMARKET_PASSPHRASE)([=:][[:space:]]*)"[^"]*"/\1\2"[REDACTED]"/g'       -e 's/(TYPESAFE_API_KEY|POLYMARKET_PRIVATE_KEY|POLYMARKET_SECRET|POLYMARKET_PASSPHRASE)([=:][[:space:]]*)[^"[:space:],}][^[:space:],}]*/\1\2[REDACTED]/g'       "$file" > "$scrub_tmp" 2>/dev/null; then
      mv -f "$scrub_tmp" "$file"
    else
      rm -f "$scrub_tmp"
    fi
  done < <(find "$DEST" -type f 2>/dev/null)
}

latest_paper_dir() {
  local host_dir="$LOGS_ROOT/paper/$HOST"
  if [ ! -d "$host_dir" ]; then
    host_dir="$(find "$LOGS_ROOT/paper" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -n 1)"
  fi
  [ -n "$host_dir" ] && [ -d "$host_dir" ] || return 1
  find "$host_dir" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -n 1
}

restore_latest() {
  local latest
  latest="$(latest_paper_dir)" || return 1
  [ -n "$latest" ] && [ -d "$latest" ] || return 1

  local restored=0
  local src dest
  if [ -d "$latest/ledgers" ]; then
    mkdir -p "$STATE/ledgers"
    for src in "$latest"/ledgers/*.jsonl; do
      [ -f "$src" ] || continue
      dest="$STATE/ledgers/$(basename "$src")"
      if [ ! -s "$dest" ]; then
        cp -f "$src" "$dest"
        restored=$((restored + 1))
      fi
    done
  fi
  if [ -f "$latest/jev_calls.jsonl" ]; then
    dest="$STATE/jev_calls.jsonl"
    if [ ! -s "$dest" ]; then
      cp -f "$latest/jev_calls.jsonl" "$dest"
      restored=$((restored + 1))
    fi
  fi

  say "restored $restored file(s) from $latest"
  return 0
}

write_success() {
  local commit iso epoch
  commit="$(gitx rev-parse --short HEAD 2>/dev/null || true)"
  iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  epoch="$(date +%s)"
  printf '%s\n%s\n%s\n' "$iso" "$epoch" "$commit" > "$STATUS_FILE.tmp"
  mv -f "$STATUS_FILE.tmp" "$STATUS_FILE"
  rm -f "$ERROR_FILE"
  say "pushed commit $commit at $iso"
}

if [ "$MODE" = "--restore" ]; then
  if ! ensure_repo; then
    if [ -d "$LOGS_ROOT/.git" ]; then
      say "cannot refresh remote; restoring from the local checkout"
    else
      fail "cannot access logs repo $REPO_URL" 1
    fi
  elif ! pull_rebase; then
    gitx rebase --abort >/dev/null 2>&1 || true
    say "pull failed; restoring from the local checkout"
  fi
  restore_latest || fail "no backup data found under $LOGS_ROOT/paper" 1
  exit 0
fi

if ! ensure_repo; then
  fail "cannot access logs repo $REPO_URL; will retry next tick" 0
fi
if ! pull_rebase; then
  gitx rebase --abort >/dev/null 2>&1 || true
  fail "pull --rebase failed; will retry next tick" 0
fi

DEST="$LOGS_ROOT/paper/$HOST/$DATE"
mkdir -p "$DEST"

for src in "$STATE"/ledgers/*.jsonl; do
  [ -f "$src" ] || continue
  copy_file "$src" "ledgers/$(basename "$src")"
done
copy_file "$STATE/jev_calls.jsonl" "jev_calls.jsonl"

# Market tape (backtests/training): one gzipped file per UTC day, shared across dates and hosts.
TAPE_DEST="$LOGS_ROOT/market-data/$HOST"
for src in "$STATE"/tape/*.jsonl; do
  [ -f "$src" ] || continue
  mkdir -p "$TAPE_DEST"
  gzip -nc "$src" > "$TAPE_DEST/$(basename "$src").gz.tmp" && mv -f "$TAPE_DEST/$(basename "$src").gz.tmp" "$TAPE_DEST/$(basename "$src").gz"
done
copy_file "$STATE/runtime_status.json" "runtime_status.json"

for src in "$STATE"/logs/bot.log.*; do
  [ -f "$src" ] || continue
  copy_file "$src" "logs/$(basename "$src")"
done

scrub_dest

gitx add -- "paper/$HOST/$DATE"
[ -d "market-data/$HOST" ] && gitx add -- "market-data/$HOST"
if ! gitx diff --cached --quiet -- "paper/$HOST/$DATE" "market-data"; then
  gitx commit -m "paper logs: $HOST $DATE $(date -u +%H:%M:%SZ)" >/dev/null || \
    fail "commit failed; will retry next tick" 0
fi

if ! pull_rebase; then
  gitx rebase --abort >/dev/null 2>&1 || true
  fail "pull before push failed; local commit kept for next tick" 0
fi

if ! gitx push origin "$BRANCH"; then
  fail "push failed; will retry next tick" 0
fi

write_success
exit 0
