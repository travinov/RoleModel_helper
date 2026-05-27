#!/bin/bash

set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR" || exit 1

if [ ! -f ".venv/bin/python" ]; then
  /usr/bin/python3 -m venv .venv
fi

source ".venv/bin/activate"

if [ -f ".env" ]; then
  set -a
  source ".env"
  set +a
else
  echo "[warn] .env not found in project root: $PROJECT_DIR"
fi

export RM_APP_PORT="${RM_APP_PORT:-8011}"

echo "Starting RoleModel server on http://127.0.0.1:${RM_APP_PORT}"
python -m app
