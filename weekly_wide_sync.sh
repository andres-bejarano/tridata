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

# Barrido semanal amplio: revalida ~45 días aunque ya tengan fila en la base
# de datos, para detectar días que se quedaron con un valor "congelado" antes
# de que Garmin terminara de consolidarlo. El sync diario (daily_update.sh)
# solo revalida los últimos SyncService.RECENT_REVALIDATE_DAYS días (3 por
# defecto) — suficiente para el caso típico, pero no para casos como
# 2026-08-20 / 2026-08-24, que quedaron mal varias semanas seguidas sin que
# nada volviera a comprobarlos.
WIDE_DAYS=45

echo "=== $(date '+%Y-%m-%d %H:%M:%S') (barrido semanal, revalidate-days=$WIDE_DAYS) ===" >> "$LOG"

if "$TRIDATA" sync --revalidate-days "$WIDE_DAYS" >> "$LOG" 2>&1; then
    echo "sync (wide) OK" >> "$LOG"
else
    echo "sync (wide) FAILED (exit $?)" >> "$LOG"
    exit 1
fi

# Regenera todos los exports por si el barrido corrigió algún dato — si nada
# cambió, estas escrituras son idempotentes (mismo contenido).
if "$TRIDATA" export --format markdown --out "$EXPORT" >> "$LOG" 2>&1; then
    echo "export OK" >> "$LOG"
else
    echo "export FAILED (exit $?)" >> "$LOG"
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

echo "Barrido semanal completo." >> "$LOG"
