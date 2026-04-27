#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${HOME}/Library/Logs/pulpit"
LOCK_DIR="${TMPDIR:-/tmp}/pulpit-ingest.lock"
ENV_FILE="${PULPIT_ENV_FILE:-${HOME}/.config/pulpit-ingest.env}"
MODE="${1:-steady}"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

mkdir -p "$LOG_DIR"

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

if [ -z "${PULPIT_TRANSCRIPT_BUCKET:-}" ] || [ -z "${PULPIT_YOUTUBE_CHANNEL_ID:-}" ] || [ -z "${PULPIT_YOUTUBE_API_KEY:-}" ]; then
  echo "Missing required env vars. Set PULPIT_TRANSCRIPT_BUCKET, PULPIT_YOUTUBE_CHANNEL_ID, and PULPIT_YOUTUBE_API_KEY in $ENV_FILE." >&2
  exit 1
fi

export AWS_REGION="${AWS_REGION:-us-east-1}"
export PULPIT_REBUILD_INDEX="${PULPIT_REBUILD_INDEX:-1}"

case "$MODE" in
  backlog)
    export PULPIT_MAX_NEW_PER_RUN="${PULPIT_MAX_NEW_PER_RUN:-3}"
    export PULPIT_MAX_TRANSCRIPT_ATTEMPTS="${PULPIT_MAX_TRANSCRIPT_ATTEMPTS:-6}"
    export PULPIT_SLEEP_SEC="${PULPIT_SLEEP_SEC:-8}"
    export PULPIT_CONSECUTIVE_EXIST_STOP="${PULPIT_CONSECUTIVE_EXIST_STOP:-999999}"
    ;;
  steady)
    export PULPIT_MAX_NEW_PER_RUN="${PULPIT_MAX_NEW_PER_RUN:-2}"
    export PULPIT_MAX_TRANSCRIPT_ATTEMPTS="${PULPIT_MAX_TRANSCRIPT_ATTEMPTS:-4}"
    export PULPIT_SLEEP_SEC="${PULPIT_SLEEP_SEC:-10}"
    export PULPIT_CONSECUTIVE_EXIST_STOP="${PULPIT_CONSECUTIVE_EXIST_STOP:-5}"
    ;;
  *)
    echo "Unknown mode: $MODE (use 'steady' or 'backlog')" >&2
    exit 1
    ;;
esac

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another ingest job is already running. Exiting."
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

timestamp="$(date +"%Y-%m-%d_%H-%M-%S")"
log_file="$LOG_DIR/ingest-${MODE}-${timestamp}.log"

{
  echo "[$(date)] Starting pulpit ingest in '$MODE' mode"
  echo "Using env file: $ENV_FILE"
  echo "python3=$(command -v python3 || true)"
  echo "yt-dlp=$(command -v yt-dlp || true)"
  echo "MAX_NEW_PER_RUN=$PULPIT_MAX_NEW_PER_RUN"
  echo "MAX_TRANSCRIPT_ATTEMPTS=$PULPIT_MAX_TRANSCRIPT_ATTEMPTS"
  echo "SLEEP_SEC=$PULPIT_SLEEP_SEC"
  echo "CONSECUTIVE_EXIST_STOP=$PULPIT_CONSECUTIVE_EXIST_STOP"
  echo
  cd "$ROOT"
  python3 scripts/ingest-local.py
} >>"$log_file" 2>&1

echo "Done. Log: $log_file"
