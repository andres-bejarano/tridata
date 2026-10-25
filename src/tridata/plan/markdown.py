"""The plan written as a .md (unit 040, R7, «Bajar .md»): its sections, in the order and with the titles of the .md
it came from, each one with its rows.

Races, weeks and race nutrition go as tables; sessions as «### fecha · título» with a line per block; incidents and
the comparison as lists; notes with their text as it was. What was done of each finished week is what the plan shows
(what Andrés accepted from the plan, or Garmin's running in time in movement). Imported again, it gives the same
plan: that round trip is the promise of R7.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from ..storage import DataStore
from . import semanas as semanas_del_plan


def _celda(valor: Any) -> str:
    return "" if valor is None else str(valor).replace("|", "/").replace("\n", " ").strip()


def _tabla(cabecera: tuple[str, ...], filas: list[tuple[Any, ...]]) -> str:
    lineas = ["| " + " | ".join(cabecera) + " |", "|" + "---|" * len(cabecera)]
    lineas += ["| " + " | ".join(_celda(v) for v in fila) + " |" for fila in filas]
    return "\n".join(lineas)


def hmm(segundos: float | None) -> str:
    if segundos is None:
        return ""
    minutos = round(segundos / 60)
    return f"{minutos // 60}:{minutos % 60:02d}"


def km(valor: float | None) -> str:
    return "" if valor is None else f"{valor:.1f}".replace(".", ",")


def _hecho(s: dict, store: DataStore | None, hoy: date) -> tuple[str, str]:
    """What was done of a finished week (or the plan's accepted one), as the table writes it; else nothing."""
    if s.get("hecho_plan_s") is not None or s.get("hecho_plan_km") is not None:
        return hmm(s.get("hecho_plan_s")), km(s.get("hecho_plan_km"))
    hasta = date.fromisoformat(s["hasta"])
    if hasta >= hoy:
        return "", ""
    hecho = semanas_del_plan.hecho(store, date.fromisoformat(s["desde"]), hasta, hoy)
    if hecho is None or not hecho["carrera"]["n"]:
        return "", ""
    return hmm(hecho["carrera"]["segundos"]), km(hecho["carrera"]["km"])


def _apartado(plan: dict, i: int, store: DataStore | None, hoy: date) -> str:
    de = {k: [x for x in plan[k] if x["apartado"] == i]
          for k in ("carreras", "semanas", "sesiones", "nutricion", "incidencias", "notas")}
    bloques: list[str] = []
    if de["carreras"]:
        bloques.append(_tabla(("Carrera", "Fecha", "Objetivo", "Estado", "Principal"),
                              [(c["nombre"], c["fecha"], c["objetivo"], c["estado"], "sí" if c["principal"] else "no")
                               for c in de["carreras"]]))
    if de["semanas"]:
        bloques.append(_tabla(("Semana", "Desde", "Hasta", "Previsto", "Clave", "Hecho", "Km"),
                              [(s["n"], s["desde"], s["hasta"], hmm(s["previsto_s"]), s["clave"], *_hecho(s, store, hoy))
                               for s in de["semanas"]]))
    for s in de["sesiones"]:
        bloques.append("\n".join([f"### {s['fecha']} · {_celda(s['titulo'])}", *(f"- {b}" for b in s["bloques"])]))
    if de["nutricion"]:
        bloques.append(_tabla(("Producto", "Pauta"), [(n["producto"], n["pauta"]) for n in de["nutricion"]]))
    if de["incidencias"]:
        bloques.append("\n".join(f"- {x['fecha']}: {x['texto']}" if x["fecha"] else f"- {x['texto']}"
                                 for x in de["incidencias"]))
    comparativa = [n for n in de["notas"] if n["tipo"] == "comparativa"]
    if comparativa:
        bloques.append("\n".join(f"- {n['texto']}" for n in comparativa))
    bloques += [n["texto"] for n in de["notas"] if n["tipo"] == "nota"]
    return "\n\n".join(bloques)


def escribir(plan: dict, store: DataStore | None, hoy: date) -> str:
    """The whole plan as a .md."""
    partes = [f"# {plan['nombre']}"]
    for i, apartado in enumerate(plan["apartados"]):
        cuerpo = _apartado(plan, i, store, hoy)
        if apartado["titulo"]:
            partes.append(f"## {apartado['titulo']}")
        if cuerpo:
            partes.append(cuerpo)
    return "\n\n".join(partes) + "\n"


__all__ = ["escribir", "hmm", "km"]
