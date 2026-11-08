#!/usr/bin/env bash
# Publish the public paper-run status to the live-status branch of the public repo.
#
# Runs research/live_summary.py, then commits ONLY status/ to an orphan branch.
# Git, network and SSH errors never fail the bot: they are logged, recorded in
# publish_last_error, and retried on the next timer tick.
set -uo pipefail

ROOT="${PMQE_HOME:-$HOME/pmqe}"
STATE="${PMQE_STATE_DIR:-$HOME/pmqe-state}"
REPO_URL="${PMQE_PUBLIC_REPO:-git@pmqe-public-gh:Segzman/prediction-market-quant-engine.git}"
BRANCH="${PMQE_PUBLIC_BRANCH:-live-status}"
PYTHON="${PMQE_PYTHON:-$ROOT/.venv/bin/python}"
SSH_CONFIG="${PMQE_SSH_CONFIG:-$HOME/.ssh/config}"
CHECKOUT="$STATE/publish/repo"
STATUS_FILE="$STATE/publish_last_success"
ERROR_FILE="$STATE/publish_last_error"
LOG_FILE="$STATE/logs/publish.log"

mkdir -p "$STATE/logs" "$STATE/publish" 2>/dev/null || true
exec > >(tee -a "$LOG_FILE") 2>&1

say() {
  printf '[pmqe-publish] %s\n' "$*"
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

gitp() {
  git -c safe.directory="$CHECKOUT" -C "$CHECKOUT" "$@"
}

TMP_OUT="$(mktemp -d "${TMPDIR:-/tmp}/pmqe-status.XXXXXX")" || fail "cannot create temp dir; will retry next tick" 0
trap 'rm -rf "$TMP_OUT"' EXIT

if ! "$PYTHON" "$ROOT/research/live_summary.py" "$STATE" --out "$TMP_OUT/status"; then
  fail "live_summary.py failed; nothing published" 1
fi

ensure_checkout() {
  local rc=0 lsrc=0
  if [ ! -d "$CHECKOUT/.git" ]; then
    mkdir -p "$CHECKOUT" || return 1
    git init -q "$CHECKOUT" || return 1
    gitp remote add origin "$REPO_URL" || return 1
  fi
  gitp remote set-url origin "$REPO_URL" >/dev/null 2>&1 || true
  gitp fetch --quiet origin "$BRANCH" >/dev/null 2>&1 || rc=$?
  if [ "$rc" -ne 0 ]; then
    # Exit 2 means the branch does not exist yet; anything else is a network or auth error.
    gitp ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1 || lsrc=$?
    [ "$lsrc" -eq 2 ] || return 1
  fi
  if gitp rev-parse --verify --quiet "refs/remotes/origin/$BRANCH" >/dev/null; then
    gitp checkout -q -B "$BRANCH" "refs/remotes/origin/$BRANCH" || return 1
  elif gitp rev-parse --verify --quiet "refs/heads/$BRANCH" >/dev/null; then
    gitp checkout -q "$BRANCH" || return 1
  else
    gitp checkout -q --orphan "$BRANCH" || return 1
  fi
  return 0
}

if ! ensure_checkout; then
  fail "cannot prepare $REPO_URL checkout; will retry next tick" 0
fi

# Only status/ is replaced; the live-status branch carries nothing else.
rm -rf "$CHECKOUT/status"
mkdir -p "$CHECKOUT/status"
cp -f "$TMP_OUT"/status/* "$CHECKOUT/status/" || fail "copy failed" 1

if ! gitp add -- status; then
  fail "git add failed; will retry next tick" 0
fi
if gitp diff --cached --quiet -- status; then
  say "no status change"
  rm -f "$ERROR_FILE"
  exit 0
fi

iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if ! gitp -c user.name=pmqe-bot -c user.email=pmqe-bot@users.noreply.github.com \
  commit -q -m "Update paper status $iso"; then
  fail "commit failed; will retry next tick" 0
fi

if ! gitp push -q origin "HEAD:refs/heads/$BRANCH"; then
  fail "push to $REPO_URL failed; will retry next tick" 0
fi

commit="$(gitp rev-parse --short HEAD 2>/dev/null || true)"
printf '%s\n%s\n%s\n' "$iso" "$(date +%s)" "$commit" > "$STATUS_FILE.tmp"
mv -f "$STATUS_FILE.tmp" "$STATUS_FILE"
rm -f "$ERROR_FILE"
say "published commit $commit at $iso"
exit 0
