#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${PMQE_REPO_URL:-https://github.com/Segzman/prediction-market-quant-engine}"
BRANCH="${PMQE_BRANCH:-main}"

REQUESTED_USER="${PMQE_USER:-${SUDO_USER:-}}"
ROOT="${PMQE_HOME:-}"
STATE="${PMQE_STATE_DIR:-}"

if [ -z "$REQUESTED_USER" ]; then
  REQUESTED_USER="$(id -un)"
fi

if [ -z "$ROOT" ] || [ -z "$STATE" ]; then
  BOOT_HOME=""
  if command -v getent >/dev/null 2>&1; then
    BOOT_HOME="$(getent passwd "$REQUESTED_USER" 2>/dev/null | cut -d: -f6 || true)"
  fi
  BOOT_HOME="${BOOT_HOME:-${HOME}}"
  ROOT="${ROOT:-$BOOT_HOME/pmqe}"
  STATE="${STATE:-$BOOT_HOME/pmqe-state}"
fi

if [ -z "${PMQE_USER:-}" ] && [ "$EUID" -eq 0 ]; then
  inferred_owner=""
  for candidate in "$STATE" "$ROOT"; do
    if [ -z "$inferred_owner" ] && [ -e "$candidate" ]; then
      inferred_owner="$(stat -c '%U' "$candidate" 2>/dev/null || true)"
      if [ "$inferred_owner" = "root" ] || [ "$inferred_owner" = "UNKNOWN" ]; then
        inferred_owner=""
      fi
    fi
  done
  if [ -n "$inferred_owner" ]; then
    REQUESTED_USER="$inferred_owner"
  fi
fi

RUN_USER="$REQUESTED_USER"
RUN_HOME=""
if command -v getent >/dev/null 2>&1; then
  RUN_HOME="$(getent passwd "$RUN_USER" 2>/dev/null | cut -d: -f6 || true)"
fi
RUN_HOME="${RUN_HOME:-${HOME}}"
RUN_GROUP="$(id -gn "$RUN_USER" 2>/dev/null || id -gn)"

ROOT="${ROOT:-$RUN_HOME/pmqe}"
STATE="${STATE:-$RUN_HOME/pmqe-state}"

if [ "$EUID" -eq 0 ]; then
  SUDO=()
else
  SUDO=(sudo)
fi

log() {
  printf '[pmqe] %s\n' "$*"
}

die() {
  printf '[pmqe] error: %s\n' "$*" >&2
  exit 1
}

run_root() {
  if [ "${#SUDO[@]}" -gt 0 ]; then
    "${SUDO[@]}" "$@"
  else
    "$@"
  fi
}

apt_env=(DEBIAN_FRONTEND=noninteractive)
for proxy_var in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do
  eval "proxy_value=\${$proxy_var:-}"
  if [ -n "$proxy_value" ]; then
    apt_env+=("$proxy_var=$proxy_value")
  fi
done

apt_get() {
  run_root env "${apt_env[@]}" apt-get "$@"
}

ensure_packages() {
  command -v apt-get >/dev/null 2>&1 || die "apt-get is required (Ubuntu 24.04 target)"
  local missing=()
  command -v git >/dev/null 2>&1 || missing+=(git)
  command -v curl >/dev/null 2>&1 || missing+=(curl)
  command -v lsof >/dev/null 2>&1 || missing+=(lsof)
  if command -v python3 >/dev/null 2>&1; then
    if ! python3 -c 'import venv' >/dev/null 2>&1 || ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
      missing+=(python3-venv)
    fi
  else
    missing+=(python3 python3-venv)
  fi
  if [ "${#missing[@]}" -eq 0 ]; then
    return
  fi
  log "installing packages: ${missing[*]}"
  apt_get update
  apt_get install -y "${missing[@]}"
}

CODE_CHANGED=0

clone_or_update() {
  if [ -d "$ROOT/.git" ]; then
    local before after
    before="$(git -c safe.directory="$ROOT" -C "$ROOT" rev-parse HEAD)"
    log "updating $ROOT from $REPO_URL ($BRANCH)"
    git -c safe.directory="$ROOT" -C "$ROOT" remote set-url origin "$REPO_URL" >/dev/null 2>&1 || true
    git -c safe.directory="$ROOT" -C "$ROOT" fetch --prune --depth 1 origin "$BRANCH"
    git -c safe.directory="$ROOT" -C "$ROOT" merge --ff-only FETCH_HEAD
    after="$(git -c safe.directory="$ROOT" -C "$ROOT" rev-parse HEAD)"
    if [ "$before" != "$after" ]; then
      CODE_CHANGED=1
    fi
  else
    if [ -e "$ROOT" ] && [ -n "$(ls -A "$ROOT" 2>/dev/null || true)" ]; then
      die "$ROOT exists and is not a git checkout"
    fi
    log "cloning $REPO_URL into $ROOT"
    mkdir -p "$(dirname "$ROOT")"
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$ROOT"
    CODE_CHANGED=1
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd -P || true)"
LOCAL_BOOTSTRAP="$ROOT/deploy/vm/bootstrap.sh"

if [ "${PMQE_BOOTSTRAPPED:-0}" != "1" ]; then
  ensure_packages
  clone_or_update
  if [ "$SCRIPT_DIR" != "$ROOT/deploy/vm" ]; then
    exec env PMQE_BOOTSTRAPPED=1 \
      PMQE_HOME="$ROOT" \
      PMQE_STATE_DIR="$STATE" \
      PMQE_USER="$RUN_USER" \
      bash "$LOCAL_BOOTSTRAP" "$@"
  fi
  export PMQE_BOOTSTRAPPED=1
fi

[ -f "$ROOT/requirements.lock" ] || die "missing $ROOT/requirements.lock"
[ -f "$ROOT/deploy/vm/pmqe.env.example" ] || die "missing environment template"

log "state directory: $STATE"
mkdir -p "$STATE/logs" "$STATE/ledgers" "$STATE/data" "$STATE/.web_runtime"
chmod 700 "$STATE" 2>/dev/null || true
touch "$STATE/jev_calls.jsonl"

if [ ! -f "$STATE/.env" ]; then
  sed \
    -e "s|__PMQE_HOME__|$ROOT|g" \
    -e "s|__PMQE_STATE_DIR__|$STATE|g" \
    "$ROOT/deploy/vm/pmqe.env.example" > "$STATE/.env"
  chmod 600 "$STATE/.env"
  log "wrote $STATE/.env; add operator secrets there"
fi

VENV="$ROOT/.venv"
VENV_CHANGED=0
if [ ! -x "$VENV/bin/python" ]; then
  log "creating virtualenv at $VENV"
  python3 -m venv "$VENV"
  rm -f "$STATE/.requirements.sha256"
fi

if command -v sha256sum >/dev/null 2>&1; then
  LOCK_HASH="$(sha256sum "$ROOT/requirements.lock" | awk '{print $1}')"
else
  LOCK_HASH="$(shasum -a 256 "$ROOT/requirements.lock" | awk '{print $1}')"
fi
PREV_HASH="$(cat "$STATE/.requirements.sha256" 2>/dev/null || true)"
if [ "$LOCK_HASH" != "$PREV_HASH" ]; then
  log "installing locked Python requirements"
  "$VENV/bin/python" -m pip install \
    --disable-pip-version-check \
    --quiet \
    -r "$ROOT/requirements.lock"
  printf '%s\n' "$LOCK_HASH" > "$STATE/.requirements.sha256"
  VENV_CHANGED=1
fi

ln -sfn "../logs/bot.log" "$STATE/.web_runtime/external.log"
ln -sfn "../ledgers/both.jsonl" "$STATE/.web_runtime/external_trades_both.jsonl"
ln -sfn "../ledgers/conviction.jsonl" "$STATE/.web_runtime/external_trades_conviction.jsonl"
ln -sfn "../ledgers/mispricing.jsonl" "$STATE/.web_runtime/external_trades_mispricing.jsonl"

TMP_UNITS="$(mktemp -d)"
trap 'rm -rf "$TMP_UNITS"' EXIT

UNIT_CHANGED=0
install_unit() {
  local src="$1"
  local name="$2"
  sed \
    -e "s|__PMQE_HOME__|$ROOT|g" \
    -e "s|__PMQE_STATE_DIR__|$STATE|g" \
    -e "s|__PMQE_USER__|$RUN_USER|g" \
    -e "s|__PMQE_GROUP__|$RUN_GROUP|g" \
    "$src" > "$TMP_UNITS/$name"
  if [ -f "/etc/systemd/system/$name" ] && cmp -s "$TMP_UNITS/$name" "/etc/systemd/system/$name"; then
    return 1
  fi
  run_root install -m 0644 "$TMP_UNITS/$name" "/etc/systemd/system/$name"
  return 0
}

for unit in pmqe-bot.service pmqe-web.service pmqe-heal.service pmqe-heal.timer; do
  if install_unit "$ROOT/deploy/vm/systemd/$unit" "$unit"; then
    UNIT_CHANGED=1
  fi
done

if [ "$EUID" -eq 0 ] && [ "$RUN_USER" != "root" ]; then
  run_root chown -R "$RUN_USER:$RUN_GROUP" "$ROOT" "$STATE" 2>/dev/null || true
fi

# Load the operator file before starting anything. The guard refuses to run
# outside paper mode unless ALLOW_LIVE=1 is set explicitly.
set -a
# shellcheck disable=SC1090
. "$STATE/.env"
set +a
bash "$ROOT/deploy/vm/guard.sh"

run_root systemctl daemon-reload
run_root systemctl enable pmqe-bot.service pmqe-web.service pmqe-heal.timer >/dev/null
run_root systemctl start pmqe-heal.timer

if [ "$UNIT_CHANGED" -eq 1 ] || [ "$VENV_CHANGED" -eq 1 ] || [ "$CODE_CHANGED" -eq 1 ]; then
  log "restarting services to apply changes"
  run_root systemctl restart pmqe-bot.service pmqe-web.service
else
  run_root systemctl start pmqe-bot.service pmqe-web.service
fi

log "bot:    sudo systemctl status pmqe-bot.service"
log "web:    http://<tailscale-ip>:${PORT:-8765}"
log "health: bash $ROOT/deploy/vm/check.sh"
