#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")" || exit 1

TRIDATA=/home/ankalagon/anaconda3/envs/tridata/bin/tridata
LOG=/home/ankalagon/projects/tridata/sync.log
EXPORT=/home/ankalagon/projects/tridata/exports/garmin_export.md
METRICS=/home/ankalagon/projects/tridata/exports/metrics.md
RUNNING=/home/ankalagon/projects/tridata/exports/running.md
CYCLING=/home/ankalagon/projects/tridata/exports/cycling.md
SWIMMING=/home/ankalagon/projects/tridata/exports/swimming.md
GYM=/home/ankalagon/projects/tridata/exports/gym.md
PMC=/home/ankalagon/projects/tridata/exports/pmc.md

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

if "$TRIDATA" sync --weather >> "$LOG" 2>&1; then
    echo "sync --weather OK" >> "$LOG"
else
    echo "sync --weather FAILED (exit $?)" >> "$LOG"
    exit 1
fi

if "$TRIDATA" export-metrics --out "$METRICS" >> "$LOG" 2>&1; then
    echo "export-metrics OK" >> "$LOG"
else
    echo "export-metrics FAILED (exit $?)" >> "$LOG"
    exit 1
fi

if "$TRIDATA" export-running --out "$RUNNING" >> "$LOG" 2>&1; then
    echo "export-running OK" >> "$LOG"
else
    echo "export-running FAILED (exit $?)" >> "$LOG"
    exit 1
fi

if "$TRIDATA" export-cycling --out "$CYCLING" >> "$LOG" 2>&1; then
    echo "export-cycling OK" >> "$LOG"
else
    echo "export-cycling FAILED (exit $?)" >> "$LOG"
    exit 1
fi

if "$TRIDATA" export-swimming --out "$SWIMMING" >> "$LOG" 2>&1; then
    echo "export-swimming OK" >> "$LOG"
else
    echo "export-swimming FAILED (exit $?)" >> "$LOG"
    exit 1
fi

if "$TRIDATA" export-gym --out "$GYM" >> "$LOG" 2>&1; then
    echo "export-gym OK" >> "$LOG"
else
    echo "export-gym FAILED (exit $?)" >> "$LOG"
    exit 1
fi

if "$TRIDATA" export-pmc --out "$PMC" --chart >> "$LOG" 2>&1; then
    echo "export-pmc OK" >> "$LOG"
else
    echo "export-pmc FAILED (exit $?)" >> "$LOG"
    exit 1
fi

echo "Done." >> "$LOG"
