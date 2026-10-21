#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")" || exit 1

# Sin entorno, las rutas de siempre. El test de la unidad 015 las cambia (un `tridata`
# falso, log y exports en una carpeta temporal) para ejecutar este script de verdad.
TRIDATA="${TRIDATA:-/home/ankalagon/anaconda3/envs/tridata/bin/tridata}"
LOG="${LOG:-/home/ankalagon/projects/tridata/sync.log}"
EXPORTS_DIR="${EXPORTS_DIR:-/home/ankalagon/projects/tridata/exports}"
EXPORT="$EXPORTS_DIR/garmin_export.md"
METRICS="$EXPORTS_DIR/metrics.md"
RUNNING="$EXPORTS_DIR/running.md"
CYCLING="$EXPORTS_DIR/cycling.md"
SWIMMING="$EXPORTS_DIR/swimming.md"
GYM="$EXPORTS_DIR/gym.md"
PMC="$EXPORTS_DIR/pmc.md"
NUTRICION="$EXPORTS_DIR/nutricion.md"

# --- Barrido amplio automático -------------------------------------------
# En vez de un segundo script/timer semanal separado, esta misma corrida
# diaria decide si "toca" revalidar ~45 días (en vez de la ventana corta de
# siempre) comparando hoy con la fecha del último barrido amplio exitoso,
# guardada en WIDE_MARKER. Así un solo script cubre ambos casos y no hace
# falta acordarse de nada por separado.
WIDE_DAYS=45
WIDE_INTERVAL_DAYS=7
WIDE_MARKER="${WIDE_MARKER:-/home/ankalagon/.tridata/last_wide_sync}"

# --- Reintentos ante fallos transitorios de login ------------------------
# El login a Garmin ha fallado varias veces con "Failed to retrieve social
# profile" sin relación con la contraseña (el mismo intento minutos/horas
# después funciona) — reintentamos antes de rendirnos. Esto también evita
# que un barrido amplio pierda su única oportunidad de la semana por un
# hipo pasajero: si falla incluso tras los reintentos, no se actualiza
# WIDE_MARKER y mañana se vuelve a intentar automáticamente.
MAX_ATTEMPTS=3
RETRY_DELAY_SECONDS="${RETRY_DELAY_SECONDS:-60}"

run_step() {
    local label="$1"; shift
    local attempt=1
    while true; do
        if "$@" >> "$LOG" 2>&1; then
            if [ "$attempt" -gt 1 ]; then
                echo "$label OK (intento $attempt/$MAX_ATTEMPTS)" >> "$LOG"
            else
                echo "$label OK" >> "$LOG"
            fi
            return 0
        fi
        echo "$label FAILED en intento $attempt/$MAX_ATTEMPTS" >> "$LOG"
        if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
            return 1
        fi
        attempt=$((attempt + 1))
        sleep "$RETRY_DELAY_SECONDS"
    done
}

# --- nutricion.md (unidad 015) ------------------------------------------
# Rehace entero exports/nutricion.md desde la base (no habla con Garmin).
# Se llama dos veces: tras la copia de seguridad, para que exista aunque
# Garmin falle, y al final, ya con el gasto real del día cerrado.
export_nutricion() {
    if "$TRIDATA" export-nutricion --out "$NUTRICION" >> "$LOG" 2>&1; then
        echo "export-nutricion OK" >> "$LOG"
        return 0
    else
        local code=$?
        echo "export-nutricion FAILED (exit $code)" >> "$LOG"
        return "$code"
    fi
}

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"

# --- Copia de seguridad, antes de tocar nada -----------------------------
# Copia verificada de la base de datos (y del diario de déficit, que no se
# puede volver a bajar de Garmin) en ~/.tridata/backups/, guardando 14.
# Va antes del sync para que un sync que estropee algo no arrastre también
# la copia. Si falla, se avisa pero no se bloquea el resto.
BACKUP_EXTRAS=()
for f in deficit/deficit.md deficit/grafica_peso_deficit.html; do
    [ -f "$f" ] && BACKUP_EXTRAS+=(--extra "$f")
done
if "$TRIDATA" backup "${BACKUP_EXTRAS[@]}" >> "$LOG" 2>&1; then
    echo "backup OK" >> "$LOG"
else
    echo "backup FAILED (exit $?) — se sigue con el sync" >> "$LOG"
fi

# Antes del sync y sin depender de él: si falla, se avisa y se sigue.
export_nutricion || echo "export-nutricion: se sigue con el sync" >> "$LOG"

# ¿Toca barrido amplio hoy?
do_wide=false
days_since=""
if [ ! -s "$WIDE_MARKER" ]; then
    do_wide=true
else
    last_wide=$(cat "$WIDE_MARKER")
    if days_since=$(( ( $(date -u +%s) - $(date -u -d "$last_wide" +%s 2>/dev/null || echo 0) ) / 86400 )); then
        if [ "$days_since" -ge "$WIDE_INTERVAL_DAYS" ]; then
            do_wide=true
        fi
    else
        do_wide=true
    fi
fi

if [ "$do_wide" = true ]; then
    echo "Barrido amplio: sí (revalidate-days=$WIDE_DAYS)" >> "$LOG"
    if run_step "sync (wide)" "$TRIDATA" sync --revalidate-days "$WIDE_DAYS"; then
        mkdir -p "$(dirname "$WIDE_MARKER")"
        date -u '+%Y-%m-%d' > "$WIDE_MARKER"
    else
        echo "sync (wide) FAILED tras $MAX_ATTEMPTS intentos — se reintentará mañana" >> "$LOG"
        exit 1
    fi
else
    echo "Barrido amplio: no (último hace $days_since día(s); toca cada $WIDE_INTERVAL_DAYS)" >> "$LOG"
    if ! run_step "sync" "$TRIDATA" sync; then
        echo "sync FAILED tras $MAX_ATTEMPTS intentos" >> "$LOG"
        exit 1
    fi
fi

if "$TRIDATA" export --format markdown --out "$EXPORT" >> "$LOG" 2>&1; then
    echo "export OK" >> "$LOG"
else
    echo "export FAILED (exit $?)" >> "$LOG"
    exit 1
fi

if ! run_step "sync --laps" "$TRIDATA" sync --laps; then
    echo "sync --laps FAILED tras $MAX_ATTEMPTS intentos" >> "$LOG"
    exit 1
fi

if ! run_step "sync --weather" "$TRIDATA" sync --weather; then
    echo "sync --weather FAILED tras $MAX_ATTEMPTS intentos" >> "$LOG"
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

# Otra vez al final, ya con lo que el sync acaba de cerrar.
if ! export_nutricion; then
    exit 1
fi

echo "Done." >> "$LOG"
