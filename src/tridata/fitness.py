"""Fitness (unit 031, REC-19 of `ver-fitness`): what the Fitness tab reads. Read only: nothing here writes.

- `carga`: form (CTL), fatigue (ATL) and freshness (TSB) of today and the 14 days that end today, with
  `metrics.compute_pmc` over the whole history (the EMAs need it) and each day computed with the HR in force THAT
  day in Ajustes (G-49); without any confirmed, the environment's, and it says so (R-108).
- `ultima_actividad` and `detalle_actividad`: the last activity up to today, and its laps (distance, time, pace or
  speed, mean HR and the zone of that HR, with the zones in force on the activity's day) and its weather.
- `recuperacion`: last night's sleep, today's resting HR and last night's HRV, each next to its 7-day mean.

Every Garmin datum carries its state, as in `hoy.py` (G-47, never an invented 0): `ok`, `no_ha_llegado` (not given
yet) or `incompleto` (given half-done: a resting HR of a day whose totals Garmin cached before finishing it, or a
night of sleep without its score yet).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .hoy import INCOMPLETO, NO_HA_LLEGADO, OK, _deporte, _hrv, _por_dia, dia_a_medias
from .metrics import compute_pmc, daily_tss, pmc_hasta, trimp_for_activity
from .nutricion.ajustes import DESDE_SIEMPRE, HistorialFC, PulsoVigente
from .storage import DataStore
from .sync import ALL_SPORT_TYPES, CYCLING_TYPES, SWIMMING_TYPES

DIAS_CARGA = 14
DIAS_MEDIA = 7
SIN_CONFIRMAR = "de la configuración: confírmala en Ajustes"


def _r1(valor: float) -> float:
    return round(valor, 1)


def _desde(dia: date) -> str:
    return "desde siempre" if dia == DESDE_SIEMPRE else f"desde el {dia.day}/{dia.month}"


def texto_pulso(pulso: PulsoVigente | None) -> dict[str, Any]:
    """Where the HR of a day comes from (R6): «de tu prueba de esfuerzo de 2025-11, desde el 1/11», or that it is the
    configuration's still to be confirmed."""
    if pulso is None:
        return {"confirmada": False, "texto": SIN_CONFIRMAR}
    origen = pulso.origen or {}
    if origen.get("tipo") == "prueba":
        return {"confirmada": True, "texto": f"de tu prueba de esfuerzo de {origen['mes']}, {_desde(pulso.desde)}"}
    return {"confirmada": True, "texto": f"de la configuración, confirmada en Ajustes {_desde(pulso.desde)}"}


def zona(pulso: float | None, zonas: tuple[tuple[int, int], ...] | None) -> int | None:
    """The zone (1-5) of a mean HR: the highest whose «from» it reaches; below zone 1, none; above the max, 5."""
    if pulso is None or not zonas or pulso < zonas[0][0]:
        return None
    return max(n for n, (desde, _) in enumerate(zonas, start=1) if pulso >= desde)


def carga(store: DataStore, historial: HistorialFC | None, hoy: date) -> dict[str, Any]:
    """`{hoy, serie, pulso}`: today's CTL/ATL/TSB, the 14 days up to today and where today's HR comes from (R1, R6).
    Rounded to one decimal, as `tridata export-pmc` writes them. No activities at all: `hoy` None, `serie` []."""
    limites = historial.limites if historial is not None else None
    serie = pmc_hasta(compute_pmc(daily_tss(store.get_activities_with_laps(ALL_SPORT_TYPES), limites)), hoy)
    puntos = [{"fecha": p["date"].isoformat(), "tss": _r1(p["tss"]), "ctl": _r1(p["ctl"]), "atl": _r1(p["atl"]),
               "tsb": _r1(p["tsb"])} for p in serie[-DIAS_CARGA:]]
    return {"hoy": puntos[-1] if puntos and puntos[-1]["fecha"] == hoy.isoformat() else None, "serie": puntos,
            "pulso": texto_pulso(historial.vigente(hoy) if historial is not None else None)}


def _resumen(store: DataStore, historial: HistorialFC | None, a: dict) -> dict[str, Any]:
    """The card of one activity (R2): never a 0 where Garmin gave nothing."""
    laps = store.get_laps(a["activity_id"])
    dia = date.fromisoformat(a["activity_date"])
    limites = historial.limites(dia) if historial is not None else None
    trimp = trimp_for_activity(a, laps, limites) if a.get("avg_hr") or any(v.get("avg_hr") for v in laps) else 0.0
    return {"actividad_id": a["activity_id"], "nombre": a.get("name") or _deporte(a.get("activity_type")),
            "deporte": _deporte(a.get("activity_type")), "fecha": a["activity_date"],
            "distancia_m": a.get("distance_meters") or None, "duracion_s": a.get("duration_seconds") or None,
            "pulso_medio": a.get("avg_hr") or None, "carga": _r1(trimp) if trimp else None,
            "vueltas": len(laps), "clima": store.get_weather(a["activity_id"]) is not None}


def ultima_actividad(store: DataStore, historial: HistorialFC | None, hoy: date) -> dict[str, Any] | None:
    """The newest activity up to `hoy` (one of tomorrow is not the last one), or None if there is none."""
    actividades = store.activities_between(DESDE_SIEMPRE, hoy)
    return _resumen(store, historial, actividades[-1]) if actividades else None


def _vuelta(n: int, v: dict, tipo: str | None, zonas: tuple[tuple[int, int], ...] | None) -> dict[str, Any]:
    distancia, segundos = v.get("distance_meters") or None, v.get("duration_seconds") or None
    ritmo_km = velocidad = ritmo_100 = None
    if distancia and segundos:
        if tipo in CYCLING_TYPES:
            velocidad = _r1(distancia / segundos * 3.6)
        elif tipo in SWIMMING_TYPES:
            ritmo_100 = round(segundos / distancia * 100)
        else:
            ritmo_km = round(v.get("avg_pace_seconds_per_km") or segundos / distancia * 1000)
    pulso = v.get("avg_hr") or None
    return {"n": n, "distancia_m": distancia, "duracion_s": segundos, "ritmo_s_km": ritmo_km,
            "ritmo_s_100m": ritmo_100, "velocidad_kmh": velocidad, "pulso_medio": pulso, "zona": zona(pulso, zonas)}


def detalle_actividad(store: DataStore, historial: HistorialFC | None, actividad_id: str) -> dict[str, Any] | None:
    """`{actividad, vueltas, clima, zonas, pulso}` of one activity (R3), or None if it does not exist. `zonas` says
    whether zones were confirmed for its day (without them no lap gets a zone); `clima` None: none synced."""
    a = store.get_activity(actividad_id)
    if a is None:
        return None
    pulso = historial.vigente(date.fromisoformat(a["activity_date"])) if historial is not None else None
    zonas = pulso.zonas if pulso is not None else None
    vueltas = [_vuelta(i, v, a.get("activity_type"), zonas)
               for i, v in enumerate(store.get_laps(actividad_id), start=1)]
    return {"actividad": _resumen(store, historial, a), "vueltas": vueltas, "clima": store.get_weather(actividad_id),
            "zonas": zonas is not None, "pulso": texto_pulso(pulso)}


def _media(valores: list[Any], decimales: int | None = 1) -> float | None:
    hay = [v for v in valores if v]
    return round(sum(hay) / len(hay), decimales) if hay else None


def recuperacion(store: DataStore, hoy: date) -> dict[str, dict[str, Any]]:
    """`{sueno, pulso_reposo, hrv}` of `hoy`, each with its `estado` and its 7-day mean (R4, R5)."""
    datos = store.export_all()
    noches = _por_dia(datos["sleep"], "sleep_date")
    semana = [hoy - timedelta(days=i) for i in range(DIAS_MEDIA)]

    sueno = noches.get(hoy.isoformat()) or {}
    segundos = sueno.get("total_sleep_seconds") or None
    media_sueno = _media([(noches.get(d.isoformat()) or {}).get("total_sleep_seconds") for d in semana], None)
    estado_sueno = NO_HA_LLEGADO if not segundos else (OK if sueno.get("sleep_score") else INCOMPLETO)

    stats = [store.daily_stats_on(d) or {} for d in semana]
    valor = stats[0].get("resting_hr") or None
    estado_pulso = NO_HA_LLEGADO if not valor else (
        INCOMPLETO if dia_a_medias(stats[0], store.activities_between(hoy, hoy)) else OK)

    return {
        "sueno": {"estado": estado_sueno, "segundos": round(segundos) if segundos else None,
                  "puntuacion": sueno.get("sleep_score") or None, "media_7d": media_sueno},
        "pulso_reposo": {"estado": estado_pulso, "valor": valor, "media_7d": _media([s.get("resting_hr") for s in stats])},
        "hrv": _hrv(_por_dia(datos["hrv"], "hrv_date"), hoy),
    }


def fitness(store: DataStore, historial: HistorialFC | None, hoy: date) -> dict[str, Any]:
    """What GET /api/fitness answers: the three blocks of the tab."""
    return {"fecha": hoy.isoformat(), "carga": carga(store, historial, hoy),
            "ultima_actividad": ultima_actividad(store, historial, hoy), "recuperacion": recuperacion(store, hoy)}
