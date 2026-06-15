#!/usr/bin/env bash
set -Eeuo pipefail

# App-only update script. It uploads the current checkout and restarts the app
# without DB schema initialization, workbook validation, or workbook loading.
#
# Run from the extracted RoleModel_helper repository root:
#   bash scripts/update_rolemodel_helper_app_remote.sh

DEFAULT_APP_SSH_TARGET="CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru"
DEFAULT_APP_REMOTE_DIR="RoleModelHelper2"
DEFAULT_APP_PORT="8000"

APP_SSH_TARGET="${APP_SSH_TARGET:-$DEFAULT_APP_SSH_TARGET}"
APP_REMOTE_DIR="${APP_REMOTE_DIR:-$DEFAULT_APP_REMOTE_DIR}"
RM_APP_PORT="${RM_APP_PORT:-$DEFAULT_APP_PORT}"

usage() {
  cat <<'USAGE'
Update RoleModel Helper application files on the corporate Linux app server.

This script does not touch PostgreSQL: no DB password, no schema initialization,
no workbook validation/load, and no schema reset.

Defaults:
  App server: CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru
  Remote dir: ~/RoleModelHelper2
  App port: 8000

Environment variables:
  APP_SSH_TARGET           SSH target for the app server
  APP_REMOTE_DIR           remote directory relative to the SSH user's home, or absolute path
  RM_APP_PORT              application port for status output
  RM_SKIP_WHEELHOUSE=1     skip local wheelhouse build

Options:
  --target SSH_TARGET      override app server SSH target
  --remote-dir DIR         override remote install directory
  -h, --help               show this help

The update preserves these remote paths:
  .env.server
  logs/
  reports/
  uploads/
  backups/
  certs/                   including certs/gigachat/*.crt and *.key

After update, open the app through an SSH tunnel from your local machine:
  ssh -L 8000:127.0.0.1:8000 CI09479675-lnx-travinov@tsles-assai0001.esrt.sber.ru
  http://127.0.0.1:8000/
USAGE
}

log() {
  printf '[rolemodel-update] %s\n' "$*"
}

fail() {
  printf '[rolemodel-update] ERROR: %s\n' "$*" >&2
  exit 1
}

shell_quote() {
  local value="$1"
  printf "'"
  printf "%s" "$value" | sed "s/'/'\\\\''/g"
  printf "'"
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
[[ -f "$REPO_ROOT/app/__main__.py" ]] || fail "app/__main__.py not found; invalid repository archive"
[[ -f "$REPO_ROOT/scripts/update_rolemodel_helper_app_remote.sh" ]] || fail "scripts/update_rolemodel_helper_app_remote.sh not found"

command -v ssh >/dev/null 2>&1 || fail "ssh is required"
command -v tar >/dev/null 2>&1 || fail "tar is required"

LOCAL_WHEELHOUSE_DIR="$REPO_ROOT/.rolemodel_wheelhouse"
if [[ "${RM_SKIP_WHEELHOUSE:-0}" != "1" ]]; then
  command -v python3 >/dev/null 2>&1 || fail "python3 is required to build the local wheelhouse"
  log "Preparing Linux Python wheelhouse at $LOCAL_WHEELHOUSE_DIR"
  rm -rf "$LOCAL_WHEELHOUSE_DIR"
  mkdir -p "$LOCAL_WHEELHOUSE_DIR"
  python3 -m pip download \
    --dest "$LOCAL_WHEELHOUSE_DIR" \
    --only-binary=:all: \
    --platform manylinux2014_x86_64 \
    --implementation cp \
    --python-version 39 \
    --abi cp39 \
    -r "$REPO_ROOT/requirements.txt"
else
  log "Skipping local wheelhouse build"
fi

REMOTE_DIR_Q="$(shell_quote "$APP_REMOTE_DIR")"
REMOTE_PREPARE_COMMAND="mkdir -p -- $REMOTE_DIR_Q && find $REMOTE_DIR_Q -mindepth 1 -maxdepth 1 ! -name '.env.server' ! -name 'uploads' ! -name 'backups' ! -name 'logs' ! -name 'reports' ! -name 'certs' -exec rm -rf -- {} + && tar -xzf - -C $REMOTE_DIR_Q"

log "Uploading application update to $APP_SSH_TARGET:$APP_REMOTE_DIR"
(
  cd "$REPO_ROOT"
  tar \
    --exclude='./.git' \
    --exclude='./.venv' \
    --exclude='./logs' \
    --exclude='./reports' \
    --exclude='./uploads' \
    --exclude='./backups' \
    --exclude='./__pycache__' \
    --exclude='*.pyc' \
    --exclude='./.env' \
    --exclude='./.env.server' \
    --exclude='./certs/gigachat/*.crt' \
    --exclude='./certs/gigachat/*.key' \
    --exclude='./certs/gigachat/*.pem' \
    -czf - .
) | ssh "$APP_SSH_TARGET" "$REMOTE_PREPARE_COMMAND"

log "Installing dependencies and restarting application on $APP_SSH_TARGET"
ssh "$APP_SSH_TARGET" "APP_REMOTE_DIR=$REMOTE_DIR_Q bash -s" <<'REMOTE'
set -Eeuo pipefail

cd "$APP_REMOTE_DIR"

if [[ ! -f ".env.server" ]]; then
  echo "[rolemodel-update] ERROR: .env.server not found. Run full installer once before app-only updates." >&2
  exit 1
fi

python3 -m venv .venv
if [[ -d ".rolemodel_wheelhouse" ]] && compgen -G ".rolemodel_wheelhouse/*.whl" >/dev/null; then
  echo "[rolemodel-update] Installing Python dependencies from local wheelhouse"
  .venv/bin/python -m pip install --no-index --find-links ".rolemodel_wheelhouse" -r requirements.txt
else
  echo "[rolemodel-update] Installing Python dependencies from package index"
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

mkdir -p logs
chmod 700 logs
mkdir -p reports/dialogue_quality

INSTALL_DIR="$(pwd)"
ENV_FILE="$INSTALL_DIR/.env.server"
RUN_SCRIPT="$INSTALL_DIR/run_rolemodel_helper.sh"
STOP_SCRIPT="$INSTALL_DIR/stop_rolemodel_helper.sh"
PID_FILE="$INSTALL_DIR/logs/rolemodel-helper.pid"

cat > "$RUN_SCRIPT" <<RUN
#!/usr/bin/env bash
set -Eeuo pipefail
cd "$INSTALL_DIR"
set -a
source "$ENV_FILE"
set +a
exec "$INSTALL_DIR/.venv/bin/python" -m app
RUN
chmod 700 "$RUN_SCRIPT"

cat > "$STOP_SCRIPT" <<STOP
#!/usr/bin/env bash
set -Eeuo pipefail
if command -v systemctl >/dev/null 2>&1 && systemctl --user status rolemodel-helper.service >/dev/null 2>&1; then
  systemctl --user stop rolemodel-helper.service
  exit 0
fi
PID_FILE="$PID_FILE"
if [[ -f "\$PID_FILE" ]]; then
  kill "\$(cat "\$PID_FILE")"
  rm -f "\$PID_FILE"
else
  echo "No PID file found: \$PID_FILE" >&2
fi
STOP
chmod 700 "$STOP_SCRIPT"

if command -v systemctl >/dev/null 2>&1 && systemctl --user status rolemodel-helper.service >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user restart rolemodel-helper.service
  echo "[rolemodel-update] Restarted user service: rolemodel-helper.service"
else
  echo "[rolemodel-update] User service not found; restarting fallback process"
  "$STOP_SCRIPT" >/dev/null 2>&1 || true
  nohup "$RUN_SCRIPT" > "$INSTALL_DIR/logs/rolemodel-helper.out.log" 2> "$INSTALL_DIR/logs/rolemodel-helper.err.log" &
  printf '%s\n' "$!" > "$PID_FILE"
  chmod 600 "$PID_FILE"
fi

echo "[rolemodel-update] App-only update finished"
REMOTE

log "Application update finished"
log "Health check on server: curl -s http://127.0.0.1:$RM_APP_PORT/api/v1/health"
log "From your local machine, open it with an SSH tunnel:"
log "  ssh -L $RM_APP_PORT:127.0.0.1:$RM_APP_PORT $APP_SSH_TARGET"
log "Then open: http://127.0.0.1:$RM_APP_PORT/"
