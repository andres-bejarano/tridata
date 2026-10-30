"""The plan written as a .md (unit 040, R7, «Bajar .md»): its sections, in the order and with the titles of the .md
it came from, each one with its rows.

Races, weeks and race nutrition go as tables; sessions as «### fecha · título» with a line per block; incidents and
the comparison as lists; notes with their text as it was. What was done of each finished week is what the plan shows
(what Andrés accepted from the plan, or Garmin's running in time in movement). Imported again, it gives the same
plan: that round trip is the promise of R7.

Unit 048 (R-133): under each incident opened in the app (unit 044) go its state («Estado: abierta», or «cerrada el
X»), each visit («Visita fecha · quién: qué dijo») and its lesson, as sub-items of its line. The incidents of the .md
have no state and say nothing more, so a plan without incidents of the app gives the same .md as before. They come
from `Planes.incidencias`, given or read from the same SQLite as `store` (the app keeps the plans there).

Unit 059 (R4): under the table of the races goes the strategy of each one that has it («Estrategia de Behobia:» and
a line per stretch), and a time written by hand is its «Estado» («✅ 1:12:30»), so imported again it still wins.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from ..storage import DataStore
from . import ABIERTA, HECHA, Planes, semanas as semanas_del_plan


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


def _vivas(plan: dict, store: DataStore | None) -> list[dict]:
    """The incidents of the app of this plan, read from the SQLite of `store` ([] without one)."""
    ruta = getattr(store, "_db_path", None)
    if ruta is None or not ruta.exists():
        return []
    return [x for x in Planes(ruta).incidencias(plan["id"]) if not x["importada"]]


def _incidencia(x: dict, vivas: list[dict]) -> str:
    """The line of an incident and, if it is one of the app, its state, visits and lesson below."""
    linea = f"- {x['fecha']}: {x['texto']}" if x["fecha"] else f"- {x['texto']}"
    viva = next((v for v in vivas if (v["desde"], v["texto"]) == (x["fecha"], x["texto"])), None)
    if viva is None:
        return linea
    vivas.remove(viva)  # two equal lines, each with its own
    estado = "abierta" if viva["estado"] == ABIERTA else f"cerrada el {viva['cerrada']}"
    lineas = [linea, f"  - Estado: {estado}"]
    lineas += [f"  - Visita {v['fecha']} · {_celda(v['quien'])}: {_celda(v['que_dijo'])}" for v in viva["visitas"]]
    if viva["leccion"]:
        lineas.append(f"  - Lección: {_celda(viva['leccion'])}")
    return "\n".join(lineas)


def _apartado(plan: dict, i: int, store: DataStore | None, hoy: date, vivas: list[dict]) -> str:
    de = {k: [x for x in plan[k] if x["apartado"] == i]
          for k in ("carreras", "semanas", "sesiones", "nutricion", "incidencias", "notas")}
    bloques: list[str] = []
    if de["carreras"]:
        bloques.append(_tabla(("Carrera", "Fecha", "Objetivo", "Estado", "Principal"),
                              [(c["nombre"], c["fecha"], c["objetivo"],
                                f"{HECHA} {c['resultado']}" if c.get("resultado") else c["estado"],
                                "sí" if c["principal"] else "no") for c in de["carreras"]]))
        bloques += ["\n".join([f"Estrategia de {_celda(c['nombre'])}:", *(f"- {_celda(x)}" for x in c["estrategia"])])
                    for c in de["carreras"] if c.get("estrategia")]
    if de["semanas"]:
        bloques.append(_tabla(("Semana", "Desde", "Hasta", "Previsto", "Clave", "Hecho", "Km"),
                              [(s["n"], s["desde"], s["hasta"], hmm(s["previsto_s"]), s["clave"], *_hecho(s, store, hoy))
                               for s in de["semanas"]]))
    for s in de["sesiones"]:
        bloques.append("\n".join([f"### {s['fecha']} · {_celda(s['titulo'])}", *(f"- {b}" for b in s["bloques"])]))
    if de["nutricion"]:
        bloques.append(_tabla(("Producto", "Pauta"), [(n["producto"], n["pauta"]) for n in de["nutricion"]]))
    if de["incidencias"]:
        bloques.append("\n".join(_incidencia(x, vivas) for x in de["incidencias"]))
    comparativa = [n for n in de["notas"] if n["tipo"] == "comparativa"]
    if comparativa:
        bloques.append("\n".join(f"- {n['texto']}" for n in comparativa))
    bloques += [n["texto"] for n in de["notas"] if n["tipo"] == "nota"]
    return "\n\n".join(bloques)


def escribir(plan: dict, store: DataStore | None, hoy: date, incidencias: list[dict] | None = None) -> str:
    """The whole plan as a .md; `incidencias` (`Planes.incidencias` of the plan), else read next to `store`."""
    vivas = [x for x in incidencias if not x["importada"]] if incidencias is not None else _vivas(plan, store)
    partes = [f"# {plan['nombre']}"]
    for i, apartado in enumerate(plan["apartados"]):
        cuerpo = _apartado(plan, i, store, hoy, vivas)
        if apartado["titulo"]:
            partes.append(f"## {apartado['titulo']}")
        if cuerpo:
            partes.append(cuerpo)
    return "\n\n".join(partes) + "\n"


__all__ = ["escribir", "hmm", "km"]
