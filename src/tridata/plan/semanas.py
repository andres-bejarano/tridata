"""Lo previsto frente a lo hecho of each week of a plan (unit 040, R1-R3, G-54). Read only: nothing here writes.

What was done comes from the Garmin activities of those dates (`DataStore`), computed when it is shown, never copied:
running apart (its time in movement and its km, as Strava counts them) and, aside, bike, swimming and gym, with the
types of `sync.py`. An activity without time in movement counts its total time, and the week says so
(`tiempo_total`). What Andrés accepted from the plan when importing (`hecho_plan_s`, `hecho_plan_km`) wins over
Garmin for the running of that week. A week that has not started has nothing done (None), never a 0.

Each day of a week carries its planned sessions and its activities: `hecho` if there is any, `no_hecho` if it is
over without any, `hoy` today without any yet, `pendiente` after today. Unit 044: and the notes of how that day
went (`notas`, R-150), as `Planes.notas_del_dia` gives them.

Unit 059 (R1, R2, R5): a race of the plan whose day has come and has an activity of running, bike, swimming or
multisport is done, with the time and distance of Garmin (`resultado`, `origen` "garmin"): the longest in distance
of the sport its name says («10K», «media», «maratón», «Behobia», «trail»: running; «triatlón», «half», «ironman»:
the multisport, or the sum of the day), else the longest of the day, so the warm-up, the cool-down and the ride to
get there do not count. What was written by hand (`resultado`, or the «✅ …» of the .md) always wins (`origen`
"a mano"). Each race also carries its strategy (`estrategia`, unit 045).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import re

from ..calendario.sincronizar import _DEPORTES, _plano
from ..hoy import _deporte
from ..storage import DataStore
from ..sync import CYCLING_TYPES, GYM_TYPES, RUNNING_TYPES, SWIMMING_TYPES
from . import HECHA

OTROS = (("bici", CYCLING_TYPES), ("natacion", SWIMMING_TYPES), ("gimnasio", GYM_TYPES))


def _segundos(a: dict) -> tuple[float, bool]:
    """The time in movement of an activity and whether it had to be the total one instead."""
    movimiento = a.get("moving_duration_seconds")
    if movimiento:
        return float(movimiento), False
    return float(a.get("duration_seconds") or 0), True


def _actividades(store: DataStore | None, desde: date, hasta: date) -> list[dict]:
    return store.activities_between(desde, hasta) if store is not None else []


def hecho(store: DataStore | None, desde: date, hasta: date, hoy: date,
          plan_s: int | None = None, plan_km: float | None = None) -> dict[str, Any] | None:
    """What was done from `desde` to `hasta` (up to today), or None if the week has not started."""
    if desde > hoy:
        return None
    fin = min(hasta, hoy)
    actividades = _actividades(store, desde, fin)
    carreras = [a for a in actividades if a.get("activity_type") in RUNNING_TYPES]
    tiempos = [_segundos(a) for a in carreras]
    carrera = {"n": len(carreras), "segundos": round(sum(s for s, _ in tiempos)),
               "km": round(sum(float(a.get("distance_meters") or 0) for a in carreras) / 1000, 1),
               "tiempo_total": any(total for _, total in tiempos)}
    fuente = "garmin"
    if plan_s is not None or plan_km is not None:
        fuente = "plan"
        carrera = {**carrera, "segundos": plan_s if plan_s is not None else carrera["segundos"],
                   "km": plan_km if plan_km is not None else carrera["km"], "tiempo_total": False}
    resultado: dict[str, Any] = {"carrera": carrera, "fuente": fuente, "hasta": fin.isoformat()}
    for nombre, tipos in OTROS:
        estas = [a for a in actividades if a.get("activity_type") in tipos]
        resultado[nombre] = {"n": len(estas), "segundos": round(sum(_segundos(a)[0] for a in estas)),
                             "distancia_m": round(sum(float(a.get("distance_meters") or 0) for a in estas))}
    return resultado


# unit 059 (R5): what a race is by its name; the types of the sports are the ones of the calendar (`_DEPORTES`)
_A_PIE = re.compile(r"\b(\d+(?:[.,]\d+)?\s?k|media|maraton|behobia|trail)\b")
_TRIATLON = re.compile(r"\b(triatlon|half|ironman)\b")
MULTIDEPORTE = ("multi_sport", "triathlon")
_TIPOS = {clave: tipos for tipos, _, clave in _DEPORTES}
_RESISTENCIA = _TIPOS["carrera"] + _TIPOS["bici"] + _TIPOS["natacion"]


def _metros(a: dict) -> float:
    return float(a.get("distance_meters") or 0)


def _tiempo(segundos: float) -> str:
    """«1:05:00», or «38:10» under one hour."""
    s = round(segundos)
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _de_garmin(nombre: str, actividades: list[dict]) -> dict[str, Any] | None:
    """The result of the race `nombre` among the activities of its day (R1, R5), or None if none is of its sport."""
    plano = _plano(nombre)
    a_pie = bool(_A_PIE.search(plano))
    if _TRIATLON.search(plano) and "maraton" not in plano:
        multi = [a for a in actividades if a.get("activity_type") in MULTIDEPORTE]
        if multi:
            elegidas = [max(multi, key=_metros)]
        else:
            elegidas = [a for a in actividades if a.get("activity_type") in _RESISTENCIA]
    else:
        tipos = _TIPOS["carrera"] if a_pie else _RESISTENCIA + MULTIDEPORTE
        candidatas = [a for a in actividades if a.get("activity_type") in tipos]
        elegidas = [max(candidatas, key=_metros)] if candidatas else []
    if not elegidas:
        return None
    segundos = sum(float(a.get("duration_seconds") or 0) or _segundos(a)[0] for a in elegidas)
    return {"tiempo": _tiempo(segundos), "km": round(sum(_metros(a) for a in elegidas) / 1000, 1),
            "origen": "garmin"}


def _carrera(c: dict, store: DataStore | None, hoy: date) -> dict[str, Any]:
    """A race as the plan shows it: its state, its result (by hand, else from Garmin, R1, R2) and its strategy."""
    a_mano = c.get("resultado") or None
    estado = str(c.get("estado") or "")
    if not a_mano and estado.startswith(HECHA):
        a_mano = estado[len(HECHA):].strip() or None
    resultado: dict[str, Any] | None = None
    if a_mano:
        resultado = {"tiempo": a_mano, "km": None, "origen": "a mano"}
        estado = f"{HECHA} {a_mano}"
    elif not estado.startswith(HECHA):
        dia = date.fromisoformat(c["fecha"]) if c.get("fecha") else None
        if dia is not None and dia <= hoy:
            resultado = _de_garmin(c.get("nombre") or "", _actividades(store, dia, dia))
        if resultado is not None:
            estado = f"{HECHA} {resultado['tiempo']} · {str(resultado['km']).replace('.', ',')} km"
        elif not estado:
            estado = "hoy" if c.get("fecha") == hoy.isoformat() else "pendiente"
    return {**{k: c.get(k) for k in ("nombre", "fecha", "objetivo", "principal")}, "estado": estado,
            "resultado": resultado, "estrategia": list(c.get("estrategia") or [])}


def _semana(s: dict, store: DataStore | None, hoy: date) -> dict[str, Any]:
    desde, hasta = date.fromisoformat(s["desde"]), date.fromisoformat(s["hasta"])
    return {"n": s["n"], "desde": s["desde"], "hasta": s["hasta"], "previsto_s": s.get("previsto_s"),
            "clave": s.get("clave") or "", "hoy": desde <= hoy <= hasta,
            "hecho": hecho(store, desde, hasta, hoy, s.get("hecho_plan_s"), s.get("hecho_plan_km"))}


def vista(plan: dict, store: DataStore | None, hoy: date) -> dict[str, Any]:
    """The plan as Fitness › Plan shows it (R1, R2): its races and its weeks with planned and done."""
    return {"id": plan["id"], "nombre": plan["nombre"],
            "carreras": [_carrera(c, store, hoy) for c in plan["carreras"]],
            "semanas": [_semana(s, store, hoy) for s in sorted(plan["semanas"], key=lambda s: s["n"])],
            "incidencias": len(plan["incidencias"]), "notas": len([n for n in plan["notas"] if n["tipo"] == "nota"])}


def _hecha(a: dict) -> dict[str, Any]:
    segundos, _ = _segundos(a)
    metros = a.get("distance_meters") or None
    ritmo = round(segundos / metros * 1000) if metros and segundos and a.get("activity_type") in RUNNING_TYPES else None
    return {"actividad_id": a["activity_id"], "nombre": a.get("name") or _deporte(a.get("activity_type")),
            "deporte": _deporte(a.get("activity_type")), "distancia_m": metros, "segundos": round(segundos) or None,
            "ritmo_s_km": ritmo, "pulso_medio": a.get("avg_hr") or None}


def semana(plan: dict, n: int, store: DataStore | None, hoy: date,
           notas: dict[str, list[str]] | None = None) -> dict[str, Any] | None:
    """One week with its seven days (R3) and the notes of each day (`notas`, unit 044), or None if the plan has no
    week `n`."""
    s = next((s for s in plan["semanas"] if s["n"] == n), None)
    if s is None:
        return None
    resumen = _semana(s, store, hoy)
    desde = date.fromisoformat(s["desde"])
    dias = []
    for i in range((date.fromisoformat(s["hasta"]) - desde).days + 1):
        dia = desde + timedelta(days=i)
        hechas = [_hecha(a) for a in _actividades(store, dia, dia)] if dia <= hoy else []
        estado = "hecho" if hechas else ("no_hecho" if dia < hoy else ("hoy" if dia == hoy else "pendiente"))
        dias.append({"fecha": dia.isoformat(), "estado": estado, "actividades": hechas,
                     "notas": list((notas or {}).get(dia.isoformat(), [])),
                     "sesiones": [{"titulo": x["titulo"], "bloques": x["bloques"], "clave": x["clave"]}
                                  for x in plan["sesiones"] if x["fecha"] == dia.isoformat()]})
    return {**resumen, "dias": dias}


__all__ = ["hecho", "semana", "vista"]
