#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$ROOT/scripts/run-ingest-batch.sh"
MODE="${1:-backlog}"
SCHEDULE="${2:-*/30 * * * *}"
MARKER="# pulpit-ingest-cron"

tmpfile="$(mktemp)"
crontab -l 2>/dev/null | grep -v "$MARKER" > "$tmpfile" || true
{
  cat "$tmpfile"
  echo "$SCHEDULE $RUNNER $MODE $MARKER"
} | crontab -
rm -f "$tmpfile"

echo "Installed cron entry:"
echo "$SCHEDULE $RUNNER $MODE"
echo
echo "To inspect:"
echo "  crontab -l"
echo
echo "To remove:"
echo "  crontab -l | grep -v '$MARKER' | crontab -"
