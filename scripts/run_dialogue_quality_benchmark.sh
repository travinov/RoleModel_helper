#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Run the RoleModel Helper dialogue quality benchmark on the server.

Usage:
  bash scripts/run_dialogue_quality_benchmark.sh 10
  bash scripts/run_dialogue_quality_benchmark.sh 20
  bash scripts/run_dialogue_quality_benchmark.sh all
  bash scripts/run_dialogue_quality_benchmark.sh random10
  bash scripts/run_dialogue_quality_benchmark.sh random 10 [seed]

Environment variables:
  RM_BENCHMARK_BASE_URL   backend URL, default http://127.0.0.1:8000
  RM_BENCHMARK_REPORT_DIR report directory, default reports/dialogue_quality
  RM_BENCHMARK_SEED       random seed for random10, default 42
USAGE
}

fail() {
  printf '[dialogue-quality] ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[dialogue-quality] %s\n' "$*"
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

[[ -f ".env.server" ]] || fail ".env.server not found; run from the deployed server directory"
[[ -x ".venv/bin/python" ]] || fail ".venv/bin/python not found; run the app update/install first"
[[ -f "tests/run_dialogue_benchmark.py" ]] || fail "tests/run_dialogue_benchmark.py not found"

MODE="${1:-10}"
BASE_URL="${RM_BENCHMARK_BASE_URL:-http://127.0.0.1:8000}"
REPORT_DIR="${RM_BENCHMARK_REPORT_DIR:-reports/dialogue_quality}"
RANDOM_SEED="${RM_BENCHMARK_SEED:-42}"
RUN_ARGS=()
REPORT_LABEL=""

case "$MODE" in
  -h|--help|help)
    usage
    exit 0
    ;;
  all)
    REPORT_LABEL="all"
    ;;
  random10)
    RUN_ARGS=(--random-session-limit 10 --random-seed "$RANDOM_SEED")
    REPORT_LABEL="random10"
    ;;
  random)
    RANDOM_LIMIT="${2:-}"
    [[ "$RANDOM_LIMIT" =~ ^[0-9]+$ ]] || fail "random mode requires numeric limit: random 10 [seed]"
    RANDOM_SEED="${3:-$RANDOM_SEED}"
    [[ "$RANDOM_SEED" =~ ^[0-9]+$ ]] || fail "random seed must be numeric"
    RUN_ARGS=(--random-session-limit "$RANDOM_LIMIT" --random-seed "$RANDOM_SEED")
    REPORT_LABEL="random${RANDOM_LIMIT}"
    ;;
  *)
    [[ "$MODE" =~ ^[0-9]+$ ]] || fail "mode must be a number, all, random10, or random N [seed]"
    RUN_ARGS=(--session-limit "$MODE")
    REPORT_LABEL="first${MODE}"
    ;;
esac

mkdir -p "$REPORT_DIR"
REPORT_PATH="$REPORT_DIR/combined_success_report.${REPORT_LABEL}.json"

set -a
source .env.server
set +a

log "base_url=$BASE_URL"
log "mode=$MODE"
log "report=$REPORT_PATH"

exec .venv/bin/python tests/run_dialogue_benchmark.py \
  --base-url "$BASE_URL" \
  --combined-success-fixture \
  --db-evidence \
  "${RUN_ARGS[@]}" \
  --report "$REPORT_PATH" \
  --strict-exit
