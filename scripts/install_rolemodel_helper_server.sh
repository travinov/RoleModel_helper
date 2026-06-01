#!/usr/bin/env bash
set -Eeuo pipefail

# ZIP-contained installer for:
#   CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru
#
# Run from the extracted RoleModel_helper repository root:
#   bash scripts/install_rolemodel_helper_server.sh

TARGET_SSH_USER="CI09479675-lnx-travinov"
TARGET_HOST="tsles-assai0001.esrt.sber.ru"

DEFAULT_DB_HOST="10.135.162.149"
DEFAULT_DB_PORT="5433"
DEFAULT_DB_NAME="bdtest"
DEFAULT_DB_SCHEMA="rolemodel_helper"
DEFAULT_DB_USER="CI09479675-pg-travinov"
DEFAULT_APP_HOST="0.0.0.0"
DEFAULT_APP_PORT="8000"
DEFAULT_INSTALL_DIR=""

SKIP_DB_INIT=0
SKIP_WORKBOOK_LOAD=0
SKIP_SERVICE=0
FORCE_SYSTEMD=0
RESET_DB=0
NONINTERACTIVE="${NONINTERACTIVE:-0}"

usage() {
  cat <<'USAGE'
Install RoleModel Helper on a Linux server from the extracted ZIP archive.

Defaults are prepared for:
  CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru
  PostgreSQL: 10.135.162.149:5433/bdtest
  PostgreSQL user: CI09479675-pg-travinov

Environment variables:
  RM_DB_USER       default: CI09479675-pg-travinov
  RM_DB_PASSWORD   required unless entered interactively
  RM_DB_HOST       default: 10.135.162.149
  RM_DB_PORT       default: 5433
  RM_DB_NAME       default: bdtest
  RM_DB_SCHEMA     default: rolemodel_helper
  RM_APP_HOST      default: 0.0.0.0
  RM_APP_PORT      default: 8000
  RM_INSTALL_DIR   default: extracted repository root
  NONINTERACTIVE=1 fail instead of prompting for missing credentials

Options:
  --install-dir PATH     copy/install into PATH before setup
  --skip-db-init         do not run: python -m rolemodel_etl db.init
  --skip-workbook-load   do not validate/load the bundled workbook
  --skip-service         do not create/start a user systemd service
  --force-systemd        fail if user systemd setup is unavailable
  --reset-db             drop and recreate the configured DB schema before loading
  -h, --help             show this help

After installation:
  ./run_rolemodel_helper.sh
  ./stop_rolemodel_helper.sh
USAGE
}

log() {
  printf '[rolemodel-install] %s\n' "$*"
}

fail() {
  printf '[rolemodel-install] ERROR: %s\n' "$*" >&2
  exit 1
}

shell_quote() {
  printf '%q' "$1"
}

while (($#)); do
  case "$1" in
    --install-dir)
      [[ $# -ge 2 ]] || fail "--install-dir requires PATH"
      DEFAULT_INSTALL_DIR="$2"
      shift 2
      ;;
    --skip-db-init)
      SKIP_DB_INIT=1
      shift
      ;;
    --skip-workbook-load)
      SKIP_WORKBOOK_LOAD=1
      shift
      ;;
    --skip-service)
      SKIP_SERVICE=1
      shift
      ;;
    --force-systemd)
      FORCE_SYSTEMD=1
      shift
      ;;
    --reset-db)
      RESET_DB=1
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

if [[ "$RESET_DB" == "1" && "$SKIP_DB_INIT" == "1" ]]; then
  fail "--reset-db cannot be combined with --skip-db-init"
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

[[ -f "$SOURCE_ROOT/requirements.txt" ]] || fail "requirements.txt not found; run from extracted RoleModel_helper ZIP"
[[ -f "$SOURCE_ROOT/app/__main__.py" ]] || fail "app/__main__.py not found; invalid repository archive"
[[ -f "$SOURCE_ROOT/rolemodel_etl/__main__.py" ]] || fail "rolemodel_etl entrypoint not found; invalid repository archive"

INSTALL_DIR="${RM_INSTALL_DIR:-${DEFAULT_INSTALL_DIR:-$SOURCE_ROOT}}"
INSTALL_DIR="$(mkdir -p "$INSTALL_DIR" && cd -- "$INSTALL_DIR" && pwd)"

if [[ "$INSTALL_DIR" != "$SOURCE_ROOT" ]]; then
  log "Copying source into $INSTALL_DIR"
  (
    cd "$SOURCE_ROOT"
    tar \
      --exclude='.git' \
      --exclude='.venv' \
      --exclude='logs' \
      --exclude='__pycache__' \
      -cf - .
  ) | tar -xf - -C "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

RM_DB_HOST="${RM_DB_HOST:-$DEFAULT_DB_HOST}"
RM_DB_PORT="${RM_DB_PORT:-$DEFAULT_DB_PORT}"
RM_DB_NAME="${RM_DB_NAME:-$DEFAULT_DB_NAME}"
RM_DB_SCHEMA="${RM_DB_SCHEMA:-$DEFAULT_DB_SCHEMA}"
RM_DB_USER="${RM_DB_USER:-$DEFAULT_DB_USER}"
RM_APP_HOST="${RM_APP_HOST:-$DEFAULT_APP_HOST}"
RM_APP_PORT="${RM_APP_PORT:-$DEFAULT_APP_PORT}"
RM_ROLEMODEL_UPLOAD_DIR="${RM_ROLEMODEL_UPLOAD_DIR:-$INSTALL_DIR/uploads/rolemodel}"
RM_INSTRUCTION_UPLOAD_DIR="${RM_INSTRUCTION_UPLOAD_DIR:-$INSTALL_DIR/uploads/instruction}"
RM_DB_BACKUP_DIR="${RM_DB_BACKUP_DIR:-$INSTALL_DIR/backups}"

if [[ -z "${RM_DB_PASSWORD:-}" ]]; then
  if [[ "$NONINTERACTIVE" == "1" ]]; then
    fail "RM_DB_PASSWORD is required in NONINTERACTIVE mode"
  fi
  read -r -s -p "RM_DB_PASSWORD: " RM_DB_PASSWORD
  printf '\n'
fi

[[ -n "$RM_DB_USER" ]] || fail "RM_DB_USER must not be empty"
[[ -n "$RM_DB_PASSWORD" ]] || fail "RM_DB_PASSWORD must not be empty"

CURRENT_USER="$(id -un 2>/dev/null || true)"
CURRENT_HOST="$(hostname -f 2>/dev/null || hostname 2>/dev/null || true)"
if [[ "$CURRENT_USER" != "$TARGET_SSH_USER" || "$CURRENT_HOST" != "$TARGET_HOST" ]]; then
  log "Warning: expected $TARGET_SSH_USER@$TARGET_HOST, current ${CURRENT_USER:-unknown}@${CURRENT_HOST:-unknown}"
fi

command -v python3 >/dev/null 2>&1 || fail "python3 is required"

log "Checking TCP access to PostgreSQL at $RM_DB_HOST:$RM_DB_PORT"
python3 - "$RM_DB_HOST" "$RM_DB_PORT" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
try:
    with socket.create_connection((host, port), timeout=8):
        pass
except OSError as exc:
    raise SystemExit(f"cannot reach PostgreSQL {host}:{port}: {exc}")
PY

log "Creating Python virtual environment"
python3 -m venv .venv
".venv/bin/python" -m pip install --upgrade pip
".venv/bin/python" -m pip install -r requirements.txt

mkdir -p logs uploads backups "$RM_ROLEMODEL_UPLOAD_DIR" "$RM_INSTRUCTION_UPLOAD_DIR" "$RM_DB_BACKUP_DIR"
chmod 700 logs uploads backups "$RM_ROLEMODEL_UPLOAD_DIR" "$RM_INSTRUCTION_UPLOAD_DIR" "$RM_DB_BACKUP_DIR"

ENV_FILE="$INSTALL_DIR/.env.server"
log "Writing protected environment file: $ENV_FILE"
{
  printf 'export RM_DB_HOST=%s\n' "$(shell_quote "$RM_DB_HOST")"
  printf 'export RM_DB_PORT=%s\n' "$(shell_quote "$RM_DB_PORT")"
  printf 'export RM_DB_NAME=%s\n' "$(shell_quote "$RM_DB_NAME")"
  printf 'export RM_DB_USER=%s\n' "$(shell_quote "$RM_DB_USER")"
  printf 'export RM_DB_PASSWORD=%s\n' "$(shell_quote "$RM_DB_PASSWORD")"
  printf 'export RM_DB_SCHEMA=%s\n' "$(shell_quote "$RM_DB_SCHEMA")"
  printf 'export RM_APP_HOST=%s\n' "$(shell_quote "$RM_APP_HOST")"
  printf 'export RM_APP_PORT=%s\n' "$(shell_quote "$RM_APP_PORT")"
  printf 'export RM_ROLEMODEL_UPLOAD_DIR=%s\n' "$(shell_quote "$RM_ROLEMODEL_UPLOAD_DIR")"
  printf 'export RM_INSTRUCTION_UPLOAD_DIR=%s\n' "$(shell_quote "$RM_INSTRUCTION_UPLOAD_DIR")"
  printf 'export RM_DB_BACKUP_DIR=%s\n' "$(shell_quote "$RM_DB_BACKUP_DIR")"
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

log "Checking authenticated PostgreSQL connection"
".venv/bin/python" - <<'PY'
from app.config import AppConfig
from rolemodel_etl.db import get_connection

config = AppConfig.from_env().db
with get_connection(config) as conn:
    with conn.cursor() as cursor:
        cursor.execute("select version()")
        print(cursor.fetchone()[0])
PY

check_db_schema() {
  ".venv/bin/python" - <<'PY'
import json
import re

from app.config import AppConfig
from rolemodel_etl.db import get_connection, read_schema_sql

config = AppConfig.from_env().db
schema_sql = read_schema_sql()
required_tables = sorted(set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+([a-zA-Z_][a-zA-Z0-9_]*)", schema_sql)))

with get_connection(config) as conn:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE'
            """,
            (config.schema,),
        )
        existing_tables = {row[0] for row in cursor.fetchall()}

missing_tables = sorted(set(required_tables) - existing_tables)
print(
    json.dumps(
        {
            "schema": config.schema,
            "required_tables": len(required_tables),
            "existing_required_tables": len(required_tables) - len(missing_tables),
            "missing_tables": missing_tables,
        },
        ensure_ascii=False,
        indent=2,
    )
)
raise SystemExit(2 if missing_tables else 0)
PY
}

if [[ "$RESET_DB" == "1" ]]; then
  log "Resetting PostgreSQL schema '$RM_DB_SCHEMA' before initialization"
  ".venv/bin/python" - <<'PY'
from app.config import AppConfig
from psycopg2 import sql
from rolemodel_etl.db import get_connection

config = AppConfig.from_env().db
with get_connection(config) as conn:
    conn.autocommit = True
    with conn.cursor() as cursor:
        cursor.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(config.schema)))
PY
  DB_SCHEMA_READY=0
else
  log "Checking whether PostgreSQL schema '$RM_DB_SCHEMA' is initialized"
  set +e
  DB_SCHEMA_CHECK_OUTPUT="$(check_db_schema)"
  DB_SCHEMA_CHECK_STATUS=$?
  set -e
  printf '%s\n' "$DB_SCHEMA_CHECK_OUTPUT"
  if [[ "$DB_SCHEMA_CHECK_STATUS" == "0" ]]; then
    DB_SCHEMA_READY=1
    log "PostgreSQL schema '$RM_DB_SCHEMA' already contains all required tables"
  elif [[ "$DB_SCHEMA_CHECK_STATUS" == "2" ]]; then
    DB_SCHEMA_READY=0
    log "PostgreSQL schema '$RM_DB_SCHEMA' is incomplete; initialization will create missing objects"
  else
    fail "DB schema check failed"
  fi
fi

if [[ "$SKIP_DB_INIT" != "1" ]]; then
  if [[ "$RESET_DB" == "1" ]]; then
    log "Running: python -m rolemodel_etl db.init after full schema reset"
  elif [[ "$DB_SCHEMA_READY" == "1" ]]; then
    log "Running: python -m rolemodel_etl db.init to apply idempotent schema updates"
  else
    log "Running: python -m rolemodel_etl db.init"
  fi
  ".venv/bin/python" -m rolemodel_etl db.init
else
  if [[ "$DB_SCHEMA_READY" != "1" ]]; then
    fail "DB schema is incomplete and --skip-db-init was requested"
  fi
  log "Skipping DB init"
fi

log "Verifying PostgreSQL schema '$RM_DB_SCHEMA' after initialization"
check_db_schema

WORKBOOK_PATH="$INSTALL_DIR/Doc/ЦРМ_ПЦП_ЦКРР_(ролевая).xlsx"
if [[ "$SKIP_WORKBOOK_LOAD" != "1" ]]; then
  [[ -f "$WORKBOOK_PATH" ]] || fail "workbook not found: $WORKBOOK_PATH"
  log "Running: python -m rolemodel_etl validate"
  ".venv/bin/python" -m rolemodel_etl validate --file "$WORKBOOK_PATH"
  log "Running: python -m rolemodel_etl load"
  ".venv/bin/python" -m rolemodel_etl load --file "$WORKBOOK_PATH" --snapshot-label "ci-server-initial"
else
  log "Skipping workbook validation/load"
fi

RUN_SCRIPT="$INSTALL_DIR/run_rolemodel_helper.sh"
STOP_SCRIPT="$INSTALL_DIR/stop_rolemodel_helper.sh"
PID_FILE="$INSTALL_DIR/logs/rolemodel-helper.pid"

cat > "$RUN_SCRIPT" <<RUN
#!/usr/bin/env bash
set -Eeuo pipefail
cd $(shell_quote "$INSTALL_DIR")
set -a
source $(shell_quote "$ENV_FILE")
set +a
exec $(shell_quote "$INSTALL_DIR/.venv/bin/python") -m app
RUN
chmod 700 "$RUN_SCRIPT"

cat > "$STOP_SCRIPT" <<STOP
#!/usr/bin/env bash
set -Eeuo pipefail
if command -v systemctl >/dev/null 2>&1 && systemctl --user status rolemodel-helper.service >/dev/null 2>&1; then
  systemctl --user stop rolemodel-helper.service
  exit 0
fi
PID_FILE=$(shell_quote "$PID_FILE")
if [[ -f "\$PID_FILE" ]]; then
  kill "\$(cat "\$PID_FILE")"
  rm -f "\$PID_FILE"
else
  echo "No PID file found: \$PID_FILE" >&2
fi
STOP
chmod 700 "$STOP_SCRIPT"

if [[ "$SKIP_SERVICE" == "1" ]]; then
  log "Skipping user service setup"
elif command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  SERVICE_DIR="$HOME/.config/systemd/user"
  SERVICE_FILE="$SERVICE_DIR/rolemodel-helper.service"
  mkdir -p "$SERVICE_DIR"
  cat > "$SERVICE_FILE" <<UNIT
[Unit]
Description=RoleModel Helper
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$INSTALL_DIR/.venv/bin/python -m app
Restart=on-failure
RestartSec=5
StandardOutput=append:$INSTALL_DIR/logs/rolemodel-helper.out.log
StandardError=append:$INSTALL_DIR/logs/rolemodel-helper.err.log

[Install]
WantedBy=default.target
UNIT
  chmod 600 "$SERVICE_FILE"
  systemctl --user daemon-reload
  systemctl --user enable --now rolemodel-helper.service
  log "Started user service: rolemodel-helper.service"
else
  if [[ "$FORCE_SYSTEMD" == "1" ]]; then
    fail "user systemd is unavailable"
  fi
  log "User systemd is unavailable; starting fallback background process"
  nohup "$RUN_SCRIPT" > "$INSTALL_DIR/logs/rolemodel-helper.out.log" 2> "$INSTALL_DIR/logs/rolemodel-helper.err.log" &
  printf '%s\n' "$!" > "$PID_FILE"
  chmod 600 "$PID_FILE"
fi

log "Installation finished"
log "App URL on server: http://127.0.0.1:$RM_APP_PORT/"
log "Health check: curl -s http://127.0.0.1:$RM_APP_PORT/api/v1/health"
log "Run script: $RUN_SCRIPT"
log "Stop script: $STOP_SCRIPT"
