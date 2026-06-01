#!/usr/bin/env bash
set -Eeuo pipefail

# Run this script from a local machine with SSH access to the app server.
# It uploads the current repository contents and runs the server-local installer there.

DEFAULT_APP_SSH_TARGET="CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru"
DEFAULT_APP_REMOTE_DIR="RoleModelHelper2"

DEFAULT_DB_HOST="10.135.162.149"
DEFAULT_DB_PORT="5433"
DEFAULT_DB_NAME="bdtest"
DEFAULT_DB_SCHEMA="rolemodel_helper"
DEFAULT_DB_USER="CI09479675-pg-travinov"
DEFAULT_APP_PORT="8000"

APP_SSH_TARGET="${APP_SSH_TARGET:-$DEFAULT_APP_SSH_TARGET}"
APP_REMOTE_DIR="${APP_REMOTE_DIR:-$DEFAULT_APP_REMOTE_DIR}"
RM_DB_HOST="${RM_DB_HOST:-$DEFAULT_DB_HOST}"
RM_DB_PORT="${RM_DB_PORT:-$DEFAULT_DB_PORT}"
RM_DB_NAME="${RM_DB_NAME:-$DEFAULT_DB_NAME}"
RM_DB_SCHEMA="${RM_DB_SCHEMA:-$DEFAULT_DB_SCHEMA}"
RM_DB_USER="${RM_DB_USER:-$DEFAULT_DB_USER}"
RM_APP_PORT="${RM_APP_PORT:-$DEFAULT_APP_PORT}"

INSTALL_ARGS=()

usage() {
  cat <<'USAGE'
Deploy RoleModel Helper from this local checkout to the corporate Linux app server.

Defaults:
  App server: CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru
  Remote dir: ~/RoleModelHelper2
  PostgreSQL: 10.135.162.149:5433/bdtest
  PostgreSQL schema: rolemodel_helper
  PostgreSQL user: CI09479675-pg-travinov
  App port: 8000

Environment variables:
  APP_SSH_TARGET    SSH target for the app server
  APP_REMOTE_DIR    remote directory relative to the SSH user's home, or absolute path
  RM_DB_USER        database user
  RM_DB_PASSWORD    required unless entered interactively
  RM_DB_HOST        database host
  RM_DB_PORT        database port
  RM_DB_NAME        database name
  RM_DB_SCHEMA      database schema
  RM_APP_PORT       application port

Options:
  --target SSH_TARGET     override app server SSH target
  --remote-dir DIR        override remote install directory
  --skip-db-init          pass through to the server installer
  --skip-workbook-load    pass through to the server installer
  --skip-service          pass through to the server installer
  --force-systemd         pass through to the server installer
  --reset-db              pass through full DB schema reset to the server installer
  -h, --help              show this help

After deployment, open the app through an SSH tunnel from your local machine:
  ssh -L 8000:127.0.0.1:8000 CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru
  http://127.0.0.1:8000/
USAGE
}

log() {
  printf '[rolemodel-deploy] %s\n' "$*"
}

fail() {
  printf '[rolemodel-deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

shell_quote() {
  local value="$1"
  printf "'"
  printf "%s" "$value" | sed "s/'/'\\\\''/g"
  printf "'"
}

quote_args() {
  local quoted=""
  local arg
  for arg in "$@"; do
    quoted+=" $(shell_quote "$arg")"
  done
  printf '%s' "$quoted"
}

while (($#)); do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || fail "--target requires SSH_TARGET"
      APP_SSH_TARGET="$2"
      shift 2
      ;;
    --remote-dir)
      [[ $# -ge 2 ]] || fail "--remote-dir requires DIR"
      APP_REMOTE_DIR="$2"
      shift 2
      ;;
    --skip-db-init|--skip-workbook-load|--skip-service|--force-systemd|--reset-db)
      INSTALL_ARGS+=("$1")
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

[[ -f "$REPO_ROOT/requirements.txt" ]] || fail "requirements.txt not found; run from repository root or extracted ZIP"
[[ -f "$REPO_ROOT/scripts/install_rolemodel_helper_server.sh" ]] || fail "server installer not found"
[[ -f "$REPO_ROOT/app/__main__.py" ]] || fail "app/__main__.py not found; invalid repository archive"

command -v ssh >/dev/null 2>&1 || fail "ssh is required"
command -v tar >/dev/null 2>&1 || fail "tar is required"

if [[ -z "${RM_DB_PASSWORD:-}" ]]; then
  read -r -s -p "RM_DB_PASSWORD: " RM_DB_PASSWORD
  printf '\n'
fi

[[ -n "$RM_DB_PASSWORD" ]] || fail "RM_DB_PASSWORD must not be empty"

REMOTE_DIR_Q="$(shell_quote "$APP_REMOTE_DIR")"
INSTALL_ARGS_Q="$(quote_args "${INSTALL_ARGS[@]}")"

REMOTE_PREPARE_COMMAND="mkdir -p -- $REMOTE_DIR_Q && find $REMOTE_DIR_Q -mindepth 1 -maxdepth 1 ! -name '.env.server' ! -name 'uploads' ! -name 'backups' ! -name 'logs' -exec rm -rf -- {} + && tar -xzf - -C $REMOTE_DIR_Q"

log "Uploading current checkout to $APP_SSH_TARGET:$APP_REMOTE_DIR"
(
  cd "$REPO_ROOT"
  tar \
    --exclude='./.git' \
    --exclude='./.venv' \
    --exclude='./logs' \
    --exclude='./uploads' \
    --exclude='./backups' \
    --exclude='./__pycache__' \
    --exclude='*.pyc' \
    --exclude='./.env' \
    --exclude='./.env.server' \
    -czf - .
) | ssh "$APP_SSH_TARGET" "$REMOTE_PREPARE_COMMAND"

PASSWORD_STDIN_ASSIGNMENT='RM_DB_PASSWORD=$(cat)'
REMOTE_INSTALL_COMMAND="cd $REMOTE_DIR_Q && RM_DB_HOST=$(shell_quote "$RM_DB_HOST") RM_DB_PORT=$(shell_quote "$RM_DB_PORT") RM_DB_NAME=$(shell_quote "$RM_DB_NAME") RM_DB_SCHEMA=$(shell_quote "$RM_DB_SCHEMA") RM_DB_USER=$(shell_quote "$RM_DB_USER") RM_APP_PORT=$(shell_quote "$RM_APP_PORT") NONINTERACTIVE=1 $PASSWORD_STDIN_ASSIGNMENT bash scripts/install_rolemodel_helper_server.sh$INSTALL_ARGS_Q"

log "Running server installer on $APP_SSH_TARGET"
printf '%s' "$RM_DB_PASSWORD" | ssh "$APP_SSH_TARGET" "$REMOTE_INSTALL_COMMAND"

log "Deployment finished"
log "The app is listening on the remote server at: http://127.0.0.1:$RM_APP_PORT/"
log "From your local machine, open it with an SSH tunnel:"
log "  ssh -L $RM_APP_PORT:127.0.0.1:$RM_APP_PORT $APP_SSH_TARGET"
log "Then open: http://127.0.0.1:$RM_APP_PORT/"
log "Direct URL may work only if the corporate network/firewall exposes the port:"
log "  http://tsles-assai0001.esrt.sber.ru:$RM_APP_PORT/"
