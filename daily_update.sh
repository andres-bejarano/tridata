#!/usr/bin/env bash
set -euo pipefail

TRIDATA=/home/ankalagon/anaconda3/envs/tridata/bin/tridata
LOG=/home/ankalagon/projects/tridata/sync.log
EXPORT=/home/ankalagon/projects/tridata/garmin_export.md

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"

if "$TRIDATA" sync >> "$LOG" 2>&1; then
    echo "sync OK" >> "$LOG"
else
    echo "sync FAILED (exit $?)" >> "$LOG"
    exit 1
fi

if "$TRIDATA" export --format markdown --out "$EXPORT" >> "$LOG" 2>&1; then
    echo "export OK" >> "$LOG"
else
    echo "export FAILED (exit $?)" >> "$LOG"
    exit 1
fi

echo "Done." >> "$LOG"
