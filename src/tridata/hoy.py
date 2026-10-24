"""«Hoy de un vistazo» (unit 030, REC-18 of `ver-dia-movil`): what the cards Entreno and «Cómo llegas» read.

Read only: nothing here writes. Every Garmin datum of «Cómo llegas» carries its state (G-47, never an invented 0):

- `ok`: it is there;
- `no_ha_llegado`: Garmin has not given it yet (no row, or a row without that value);
- `incompleto`: today's daily totals were cached by Garmin half-done (the rule of `exporters.py`: the day's steps
  or calories are lower than what its activities alone add up), so the resting HR of that day is not final.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .exporters import MarkdownExporter
from .metrics import compute_pmc, daily_tss
from .storage import DataStore
from .sync import ALL_SPORT_TYPES, CYCLING_TYPES, GYM_TYPES, RUNNING_TYPES, SWIMMING_TYPES, _cuando

OK, NO_HA_LLEGADO, INCOMPLETO = "ok", "no_ha_llegado", "incompleto"
NOCHES_MEDIA_HRV = 7
DEPORTES = ((RUNNING_TYPES, "Carrera"), (CYCLING_TYPES, "Bici"), (SWIMMING_TYPES, "Natación"), (GYM_TYPES, "Fuerza"))


def _por_dia(filas: list[dict], clave: str) -> dict[str, dict]:
    return {f[clave]: f for f in filas if f.get(clave)}


def _sueno(noches: dict[str, dict], dia: date) -> dict[str, Any]:
    """Last night's sleep: the record of `dia` (Garmin dates a night by the morning it ends)."""
    s = noches.get(dia.isoformat()) or {}
    segundos = s.get("total_sleep_seconds")
    if not segundos:
        return {"estado": NO_HA_LLEGADO, "segundos": None, "puntuacion": None}
    return {"estado": OK, "segundos": round(segundos), "puntuacion": s.get("sleep_score") or None}


def _hrv(noches: dict[str, dict], dia: date) -> dict[str, Any]:
    """Last night's HRV and the mean of the nights there are among the 7 that end last night."""
    valores = [(noches.get((dia - timedelta(days=i)).isoformat()) or {}).get("last_night_avg")
               for i in range(NOCHES_MEDIA_HRV)]
    hay = [v for v in valores if v]
    media = round(sum(hay) / len(hay), 1) if hay else None
    if not valores[0]:
        return {"estado": NO_HA_LLEGADO, "valor": None, "media_7d": media}
    return {"estado": OK, "valor": valores[0], "media_7d": media}


def dia_a_medias(stats: dict, actividades: list[dict]) -> bool:
    """The rule of `exporters.py` (MarkdownExporter._render_daily_stats): the day's steps are fewer than the runs
    alone estimate, or its calories fewer than the exercise alone: Garmin cached it before finishing it."""
    suma = MarkdownExporter._daily_activity_aggregates(actividades).get(stats.get("stat_date") or "", {})
    pasos, kcal = stats.get("steps"), stats.get("total_calories")
    corriendo, ejercicio = round(suma.get("running_steps_est") or 0), suma.get("exercise_calories") or 0
    return bool((pasos is not None and corriendo and corriendo > pasos)
                or (kcal is not None and ejercicio and ejercicio > kcal))


def _pulso(store: DataStore, dia: date, actividades: list[dict]) -> dict[str, Any]:
    stats = store.daily_stats_on(dia) or {}
    valor = stats.get("resting_hr")
    if not valor:
        return {"estado": NO_HA_LLEGADO, "valor": None}
    return {"estado": INCOMPLETO if dia_a_medias(stats, actividades) else OK, "valor": valor}


def _frescura(store: DataStore, dia: date) -> dict[str, Any]:
    """TSB of `dia` with `metrics.compute_pmc` over the whole history (the EMAs need it), carried over the rest days
    up to `dia` the way `mcp_server.get_training_load` does."""
    serie = compute_pmc(daily_tss(store.get_activities_with_laps(ALL_SPORT_TYPES)))
    serie = [p for p in serie if p["date"] <= dia]
    if not serie:
        return {"estado": NO_HA_LLEGADO, "valor": None}
    ultimo = serie[-1]
    ctl, atl, tsb, actual = ultimo["ctl"], ultimo["atl"], ultimo["tsb"], ultimo["date"]
    while actual < dia:  # rest days: today's form is yesterday's fitness minus yesterday's fatigue
        tsb = ctl - atl
        ctl -= ctl / 42.0
        atl -= atl / 7.0
        actual += timedelta(days=1)
    return {"estado": OK, "valor": round(tsb, 1)}


def como_llegas(store: DataStore, dia: date) -> dict[str, dict[str, Any]]:
    """`{sueno, pulso_reposo, hrv, frescura}` of `dia`, each with its `estado` (R-97, R-102)."""
    datos = store.export_all()
    actividades = store.activities_between(dia, dia)
    return {
        "sueno": _sueno(_por_dia(datos["sleep"], "sleep_date"), dia),
        "pulso_reposo": _pulso(store, dia, actividades),
        "hrv": _hrv(_por_dia(datos["hrv"], "hrv_date"), dia),
        "frescura": _frescura(store, dia),
    }


def _deporte(tipo: str | None) -> str:
    for tipos, nombre in DEPORTES:
        if tipo in tipos:
            return nombre
    return (tipo or "entreno").replace("_", " ").capitalize()


def entreno(store: DataStore, dia: date) -> list[dict[str, Any]]:
    """The activities of `dia`, oldest first: sport, minutes and kcal (None when Garmin gave none, never 0)."""
    return [{"actividad_id": a.get("activity_id"), "nombre": a.get("name") or _deporte(a.get("activity_type")),
             "deporte": _deporte(a.get("activity_type")),
             "minutos": round(float(a.get("duration_seconds") or 0) / 60), "kcal": a.get("calories") or None}
            for a in store.activities_between(dia, dia)]


def texto_sync(ok_en: str | None, ahora: datetime) -> str:
    """R-99: «Garmin: sincronizado a las 9:05» (another day: «el martes 20 a las 8:01»), or that there is none."""
    if not ok_en:
        return "Garmin: aún no se ha sincronizado"
    momento = datetime.fromisoformat(ok_en)
    hora = f"{momento.hour}:{momento.minute:02d}"
    if momento.date() == ahora.date():
        return f"Garmin: sincronizado a las {hora}"
    dia = _cuando(momento, ahora).rsplit(" a las ", 1)[0]
    return f"Garmin: sincronizado {dia} a las {hora}"
