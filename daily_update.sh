#!/usr/bin/env bash
set -euo pipefail

TRIDATA=/home/ankalagon/anaconda3/envs/tridata/bin/tridata
LOG=/home/ankalagon/projects/tridata/sync.log
EXPORT=/home/ankalagon/projects/tridata/exports/garmin_export.md

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

if "$TRIDATA" sync --laps >> "$LOG" 2>&1; then
    echo "sync --laps OK" >> "$LOG"
else
    echo "sync --laps FAILED (exit $?)" >> "$LOG"
    exit 1
fi

if "$TRIDATA" export-metrics >> "$LOG" 2>&1; then
    echo "export-metrics OK" >> "$LOG"
else
    echo "export-metrics FAILED (exit $?)" >> "$LOG"
    exit 1
fi

if "$TRIDATA" export-running >> "$LOG" 2>&1; then
    echo "export-running OK" >> "$LOG"
else
    echo "export-running FAILED (exit $?)" >> "$LOG"
    exit 1
fi

if "$TRIDATA" export-cycling >> "$LOG" 2>&1; then
    echo "export-cycling OK" >> "$LOG"
else
    echo "export-cycling FAILED (exit $?)" >> "$LOG"
    exit 1
fi

if "$TRIDATA" export-swimming >> "$LOG" 2>&1; then
    echo "export-swimming OK" >> "$LOG"
else
    echo "export-swimming FAILED (exit $?)" >> "$LOG"
    exit 1
fi

if "$TRIDATA" export-gym >> "$LOG" 2>&1; then
    echo "export-gym OK" >> "$LOG"
else
    echo "export-gym FAILED (exit $?)" >> "$LOG"
    exit 1
fi

echo "Done." >> "$LOG"
