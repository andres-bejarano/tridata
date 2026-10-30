"""Expenditure of the day with what Garmin already has and the planned training (unit 008, R-13..R-15).

Pure functions over the rows `DataStore` keeps (daily stats and activities of the day):

    gasto = basal + kcal/step × steps of the day + kcal of today's activities + planned training

Steps of the day = the larger of the steps already done without running (Garmin steps − running
steps estimated as minutes × cadence) and the steps of the day (planned by unit 007 or the default
of the settings: 10,000 in total, the missing ones are assumed, C-13). If Garmin's step counter is
lower than what the running alone estimates (the «⚠ incompleto» rule of the exporter), those steps
are not used and it is said. Garmin's own total kcal of the day is never used here (that is the
close of the day, unit 007 of the roadmap).

Unit 047: the planned training may be several (a list); every one adds. The breakdown keeps `previsto` (the
first or None) and `previsto_kcal` (the sum) as before, and adds `previstos` (the list).
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from ..exporters import MarkdownExporter
from .margen import ConfigGasto

MINUTOS_PREVISTO = (1, 600)


def _numero_es(n: float) -> str:
    return f"{n:,.0f}".replace(",", ".") if float(n).is_integer() else f"{n:g}".replace(".", ",")


def _minutos(actividad: Mapping[str, Any]) -> float:
    return round(float(actividad.get("duration_seconds") or 0) / 60, 1)


def kcal_previstas(actividad: Mapping[str, Any], minutos: Any) -> float:
    """Kcal of `minutos` of a training like `actividad`, scaled by duration (R-15, C-15)."""
    if minutos is None or isinstance(minutos, bool):
        raise ValueError("faltan los minutos del entreno previsto")
    try:
        n = float(minutos)
    except (TypeError, ValueError):
        raise ValueError(f"los minutos del entreno previsto {minutos!r} no son un número") from None
    if not math.isfinite(n) or not MINUTOS_PREVISTO[0] <= n <= MINUTOS_PREVISTO[1]:
        raise ValueError(f"los minutos del entreno previsto tienen que estar entre {MINUTOS_PREVISTO[0]} "
                         f"y {MINUTOS_PREVISTO[1]}")
    duracion = float(actividad.get("duration_seconds") or 0) / 60
    if duracion <= 0:
        raise ValueError("esa actividad no tiene duración en Garmin: no se puede escalar")
    return round(float(actividad.get("calories") or 0) * n / duracion, 1)


def _previstos(previsto: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None) -> list[dict]:
    """The planned trainings as a list: one object (as before unit 047), a list, or None."""
    if not previsto:
        return []
    if isinstance(previsto, Mapping):
        return [dict(previsto)]
    return [dict(p) for p in previsto]


def nombres_juntos(nombres: list[str]) -> str:
    """«Natación», «Natación y Bici», «Natación, Bici y Carrera»."""
    return nombres[0] if len(nombres) == 1 else f"{', '.join(nombres[:-1])} y {nombres[-1]}"


def estimar_gasto(
    config: ConfigGasto, stats: Mapping[str, Any] | None, actividades: Iterable[Mapping[str, Any]],
    previsto: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None, *, cuando: str = "de hoy",
) -> dict:
    """Expenditure of the day with its breakdown and warnings.

    `config` is the `ConfigGasto` of the day (basal of `basal_del_dia`, planned or default steps,
    kcal per step in force). `stats` and `actividades` are the `DataStore` payloads of that day.
    `previsto` is the planned training saved in `objetivo_dia` (with its scaled `kcal`): one, a list (unit 047)
    or None.
    """
    previstos = _previstos(previsto)
    lista: list[dict] = [dict(a) for a in actividades]
    avisos: list[str] = []
    agregados = MarkdownExporter._daily_activity_aggregates(lista)
    carrera = round(sum(a["running_steps_est"] for a in agregados.values()))
    pasos_garmin = stats.get("steps") if stats else None

    pasos_hechos: int | None = None
    if pasos_garmin is not None and carrera > pasos_garmin:
        avisos.append(f"Los pasos de Garmin {cuando} están incompletos ({_numero_es(pasos_garmin)}, pero la carrera "
                      f"sola estima ~{_numero_es(carrera)}): cuento los {_numero_es(config.pasos)} del día.")
    elif pasos_garmin is not None:
        pasos_hechos = int(pasos_garmin) - carrera
    pasos = max(pasos_hechos or 0, config.pasos)
    supuestos = pasos - (pasos_hechos or 0)

    hechos: list[dict[str, Any]] = []
    for a in lista:
        sin_kcal = a.get("calories") is None
        nombre = a.get("name") or a.get("activity_type") or "actividad"
        if sin_kcal:
            avisos.append(f"«{nombre}» no trae kcal en Garmin: suma 0.")
        hechos.append({"actividad_id": a.get("activity_id"), "nombre": nombre, "tipo": a.get("activity_type"),
                       "minutos": _minutos(a), "kcal": 0.0 if sin_kcal else round(float(a["calories"]), 1),
                       "sin_kcal": sin_kcal})
    hecho_kcal = round(sum(float(h["kcal"]) for h in hechos), 1)
    previsto_kcal = round(sum(float(p["kcal"]) for p in previstos), 1)
    pasos_kcal = round(pasos * config.kcal_por_paso, 1)

    hay_datos = stats is not None or bool(lista)
    if not hay_datos:
        texto = f"Sin datos de Garmin {cuando}: cuento {_numero_es(config.pasos)} pasos y ningún entreno hecho."
        texto = texto[0].lower() + texto[1:]
    else:
        partes = [f"{_numero_es(pasos_garmin)} pasos" if pasos_garmin is not None else "sin pasos"]
        if hechos:
            partes.append(", ".join(f"{h['nombre']} {_numero_es(float(h['kcal']))} kcal" for h in hechos))
        else:
            partes.append("ningún entreno")
        texto = f"Garmin {cuando}: {' y '.join(partes)}."
    return {
        "kcal": round(config.basal_kcal + pasos_kcal + hecho_kcal + previsto_kcal, 1),
        "pasos": pasos,
        "desglose": {
            "basal_kcal": config.basal_kcal,
            "pasos_garmin": pasos_garmin,
            "pasos_carrera_estimados": carrera,
            "pasos_hechos": pasos_hechos,
            "pasos_supuestos": supuestos,
            "pasos_del_dia": config.pasos,
            "kcal_por_paso": config.kcal_por_paso,
            "pasos_kcal": pasos_kcal,
            "entreno_hecho": hechos,
            "entreno_hecho_kcal": hecho_kcal,
            "previsto": previstos[0] if previstos else None,
            "previstos": previstos,
            "previsto_kcal": previsto_kcal,
        },
        "avisos": avisos,
        "garmin": {"hay_datos": hay_datos, "texto": texto},
    }


def gasto_hecho(estimado: Mapping[str, Any], *, cuando: str = "de hoy") -> dict:
    """Unit 017 (R-23, asked on 2026-10-22): what is spent with what is already DONE, from the breakdown alone.

    basal + steps done × kcal/step + kcal of the activities Garmin already has: no assumed steps and no
    planned training. It reads nothing and adds no steps of its own, so it is never above `estimado["kcal"]`.
    The basal counts the whole day (it is not split by hours). With Garmin's steps incomplete (the «⚠
    incompleto» rule above) or without Garmin data it counts no steps and says so in `aviso`.
    """
    d = estimado["desglose"]
    pasos = d["pasos_hechos"]
    aviso = None
    if not estimado.get("garmin", {}).get("hay_datos", True):
        aviso = f"sin datos de Garmin {cuando}: lo hecho es solo el basal"
    elif d["pasos_garmin"] is not None and pasos is None:
        aviso = f"los pasos de Garmin {cuando} están incompletos: lo hecho no cuenta pasos"
    elif d["pasos_garmin"] is None:
        aviso = f"Garmin aún no trae los pasos {cuando}: lo hecho no cuenta pasos"
    kcal = d["basal_kcal"] + (pasos or 0) * d["kcal_por_paso"] + d["entreno_hecho_kcal"]
    previstos = d.get("previstos") or ([d["previsto"]] if d.get("previsto") else [])
    nombres = [p.get("nombre") or "entreno" for p in previstos]
    return {"kcal": round(min(kcal, estimado["kcal"]), 1), "pasos_hechos": pasos, "pasos_del_dia": d["pasos_del_dia"],
            "entreno_hecho_kcal": d["entreno_hecho_kcal"], "previsto_kcal": d["previsto_kcal"],
            "previsto_nombre": (previstos[0].get("nombre") if len(previstos) == 1 else nombres_juntos(nombres))
            if previstos else None, "aviso": aviso}
