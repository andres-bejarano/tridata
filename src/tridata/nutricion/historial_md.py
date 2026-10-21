"""nutricion.md: the whole food history in one readable file, rebuilt from the base (unit 015, R-60..R-63).

The SQLite base rules; this file is a view of it (G-36), rewritten whole every time and never read back
except to restore a lost base. It uses the section titles and table shapes the deficit.md import already
reads (`importar_deficit.leer` for weights and days, `recetas.leer_cargas` for recipes, fixed foods and
pots), with extra columns the import ignores. Every number of a day (eaten, estimated and real
expenditure, objective, margin) comes from `dia.resumen_completo`, the same function the day screen uses
(R-61): nothing is computed here.

    generar(db_path, store, hoy) -> str        the text
    escribir(texto, ruta)                      to a temporary file in the same folder, then `os.replace`
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .ajustes import Ajustes
from .cierre import Cierres
from .dia import FaltanAjustes, resumen_completo
from .importar_deficit import PESOS, REFERENCIAS, REGISTRO, TARAS, TOTAL_IMPORTADO, numero
from .margen import total_del_dia
from .objetivo_dia import ObjetivosDia
from .peso import RegistroPeso, media_7_dias
from .recetas import Recetario
from .registro import RegistroComidas

RAYA = "—"
DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
DESDE = date(1, 1, 1)

CABECERA = """# Nutrición · historial completo

> Generado por TriData el {ahora} desde la base; se rehace cada día: no lo edites.
> Para restaurar una base perdida: `tridata importar-deficit --md nutricion.md` y
> `tridata importar-recetas --md nutricion.md`. No vuelve al reimportar: las unidades que no son g/ml
> (vuelven como 1 «ración»), las macros, la marca «estimado», los supuestos, los decimales de las kcal,
> los alias y las versiones antiguas de las recetas, los objetivos del día y los ajustes. El gasto, el
> objetivo y el margen no se reimportan: se recalculan desde Garmin y los ajustes.
"""


# -- numbers and names ------------------------------------------------------------------------------

def kcal(n: float) -> str:
    """Whole kcal with a thousands dot, as deficit.md writes them: 2412.5 -> «2.413», −300 -> «−300»."""
    entero = int(abs(float(n)) + 0.5)
    return ("−" if n < 0 and entero else "") + f"{entero:,}".replace(",", ".")


def _cantidad(n: float) -> str:
    """A quantity without thousands dot (the import reads «1.500 g» as 1,5): 450.5 -> «450,5»."""
    return f"{float(n):g}".replace(".", ",")


def _gramos(n: float) -> str:
    """Grams of a recipe or a pot, as the recipe import reads them: 1500 -> «1.500», 1500.5 -> «1500,5»."""
    return kcal(n) if float(n).is_integer() else _cantidad(n)


def _celda(texto: Any) -> str:
    """Any text in a table cell: one line, no «|»."""
    return " ".join(str(texto or "").split()).replace("|", "-")


def sanear(nombre: str) -> str:
    """A food name that cannot break the day cell the import splits (R6): its separators are replaced."""
    texto = _celda(nombre)
    for de, a in (("→", "-"), ("=", "-"), (";", ","), (":", "."), ("[", "("), ("]", ")"), ("**", "*")):
        texto = texto.replace(de, a)
    texto = re.sub(r"\s*/\s*", "/", texto)
    texto = re.sub(r"\s*\+\s*", "+", texto)
    return re.sub(r"\bTOTAL\b", "Total", texto)


def _nombre_guardado(nombre: str) -> str:
    """A recipe, fixed food or pot name: brackets would be read as its ingredients."""
    return _celda(nombre).replace("(", "[").replace(")", "]")


def _pct(p: float) -> str:
    return "0 %" if p == 0 else f"{'+' if p > 0 else '−'}{numero(abs(p))} %"


# -- sections -----------------------------------------------------------------------------------------

def _tabla(titulo: str, columnas: Iterable[str], filas: Iterable[Iterable[str]]) -> str:
    columnas = list(columnas)
    lineas = [f"## {titulo}", "", "| " + " | ".join(columnas) + " |", "|" + "---|" * len(columnas)]
    lineas += ["| " + " | ".join(fila) + " |" for fila in filas]
    return "\n".join(lineas) + "\n"


def _pesos(pesos: list[dict]) -> list[list[str]]:
    filas = []
    for p in pesos:
        dia = date.fromisoformat(p["dia"])
        media = media_7_dias(pesos, dia)
        filas.append([f"{DIAS_SEMANA[dia.weekday()].capitalize()} {dia.day}/{dia.month}/{dia.year}",
                      f"{float(p['kg']):g} kg", _celda(p["nota"]), numero(media.kg) if media else RAYA])
    return filas


def _trozo(a: Mapping[str, Any]) -> str:
    """One eaten food in the day cell: «avena 60 g 230», «huevo (2 ud) 150», «pan (sin kcal)»."""
    nombre, cantidad, unidad = sanear(a["nombre"]), a["cantidad"], (a["unidad"] or "").strip()
    if cantidad is None or (unidad == "ración" and cantidad == 1):
        texto = nombre
    elif unidad.lower() in ("g", "ml"):
        texto = f"{nombre} {_cantidad(cantidad)} {unidad.lower()}"
    else:
        texto = f"{nombre} ({_cantidad(cantidad)}{' ' + unidad if unidad else ''})"
    return f"{texto} (sin kcal)" if a["kcal"] is None else f"{texto} {kcal(a['kcal'])}"


def _comidas(comidos: list[dict], total: float) -> str:
    if len(comidos) == 1 and comidos[0]["nombre"] == TOTAL_IMPORTADO and comidos[0]["kcal"] is not None:
        return kcal(comidos[0]["kcal"])  # a day imported as a total goes back as a total
    return " / ".join(_trozo(a) for a in comidos) + f" → TOTAL {kcal(total)}"


def _entreno(store: Any, dia: date) -> str:
    actividades = store.activities_between(dia, dia) if store is not None else []
    nombres = [sanear(a.get("name") or a.get("activity_type") or "actividad") for a in actividades]
    return ", ".join(nombres) or RAYA


def _fila_dia(dia: date, apuntes: list[dict], resumen: dict | None, entreno: str) -> list[str]:
    comidos = [a for a in apuntes if a["estado"] == "comido"]
    total = resumen["total_kcal"] if resumen is not None else total_del_dia(apuntes, dia)
    if resumen is None:  # «faltan ajustes» on the screen: no expenditure, objective or margin
        numeros = [RAYA] * 4
    else:
        g, objetivo = resumen["gasto"], resumen["objetivo"]
        numeros = [kcal(g["estimado_kcal"]), RAYA if g["real_kcal"] is None else kcal(g["real_kcal"]),
                   f"{_pct(objetivo['porcentaje'])} · {kcal(objetivo['kcal'])}", kcal(objetivo["kcal"] - total)]
    return [dia.isoformat(), DIAS_SEMANA[dia.weekday()], entreno, _comidas(comidos, total), kcal(total), *numeros]


def _ingredientes(receta: dict, version: dict) -> str | None:
    partes = []
    for i in version["ingredientes"]:
        texto = _nombre_guardado(i["nombre"]).replace("+", "y")
        if i.get("cantidad") is not None:
            texto += f" {_cantidad(i['cantidad'])}" + (f" {i['unidad']}" if i.get("unidad") else "")
        partes.append(texto)
    if partes:
        return " + ".join(partes)
    if receta["nota"]:  # a recipe loaded from deficit.md keeps its ingredients as its note
        return _celda(receta["nota"])
    if version["peso_neto_g"]:
        return f"peso neto {_gramos(version['peso_neto_g'])} g"
    return None


def _referencias(recetario: Recetario) -> tuple[list[list[str]], int]:
    filas = []
    recetas = recetario.recetas()
    for r in recetas:
        v = r["en_uso"]
        if v is None:
            continue
        ingredientes = _ingredientes(r, v)
        plato = _nombre_guardado(r["nombre"]) + (f" ({ingredientes})" if ingredientes else "")
        racion = f"receta {_gramos(v['peso_neto_g'])} g total" if v["peso_neto_g"] else "receta entera"
        total = RAYA if v["kcal_total"] is None else f"{kcal(v['kcal_total'])} kcal"
        notas = [{"definitiva": "definitiva", "provisional": "provisional", "sin_kcal": "sin kcal"}[v["estado"]]]
        if v["kcal_100g"] is not None:
            notas.append(f"{numero(v['kcal_100g'])} kcal/100 g")
        if len(r["versiones"]) > 1:
            notas.append(f"tanda {v['numero']}")
        if r["nota"] and v["ingredientes"]:
            notas.append(_celda(r["nota"]))
        filas.append([plato, racion, total, " · ".join(notas)])
    for f in recetario.fijos():
        racion = "100 g" if f["por"] == "100g" else "1 unidad"
        filas.append([_nombre_guardado(f["nombre"]), racion, f"{kcal(f['kcal'])} kcal", _celda(f["nota"])])
    return filas, len(recetas)


def _dias_comidos(db_path: Path, hoy: date) -> list[date]:
    with sqlite3.connect(db_path) as conn:
        filas = conn.execute("SELECT DISTINCT dia FROM alimento_apuntado WHERE estado = 'comido' AND dia <= ? "
                             "ORDER BY dia", (hoy.isoformat(),)).fetchall()
    return [date.fromisoformat(f[0]) for f in filas]


@dataclass
class Historial:
    texto: str
    dias: int
    pesos: int
    recetas: int


def componer(db_path: Path | str, store: Any, hoy: date, *, ahora: datetime | None = None) -> Historial:
    """The file and how many days, weights and recipes it has (the only thing the command prints, R-63)."""
    db_path = Path(db_path)
    registro = RegistroComidas(db_path)
    pesos = RegistroPeso(db_path)
    ajustes = Ajustes(db_path)
    objetivos = ObjetivosDia(db_path)
    recetario = Recetario(db_path)
    cierres = Cierres(db_path, ajustes=ajustes, pesos=pesos, objetivos=objetivos, store=store, registro=registro)

    todos_pesos = pesos.entre(DESDE, hoy)
    filas_dias = []
    dias = _dias_comidos(db_path, hoy)
    for dia in dias:
        try:
            resumen = resumen_completo(dia, registro=registro, pesos=pesos, ajustes=ajustes, objetivos=objetivos,
                                       store=store, cierres=cierres, hoy=hoy)
        except FaltanAjustes:
            resumen = None
        apuntes = resumen["alimentos"] if resumen is not None else registro.apuntes_del_dia(dia)
        filas_dias.append(_fila_dia(dia, apuntes, resumen, _entreno(store, dia)))
    referencias, n_recetas = _referencias(recetario)
    taras = [[_nombre_guardado(o["nombre"]), f"{_gramos(o['tara_g'])} g"] for o in recetario.ollas()]

    momento = (ahora or datetime.now()).strftime("%Y-%m-%d %H:%M")
    texto = "\n".join([
        CABECERA.format(ahora=momento),
        _tabla(PESOS, ("Fecha", "Peso", "Notas", "Media 7 días"), _pesos(todos_pesos)),
        _tabla(REGISTRO, ("Fecha", "Día", "Entreno", "Comidas (kcal)", "Comido", "Gasto estimado", "Gasto real",
                          "Objetivo", "Margen"), filas_dias),
        _tabla(REFERENCIAS, ("Plato", "Ración", "Kcal", "Notas"), referencias),
        _tabla(TARAS, ("Recipiente", "Peso"), taras),
    ])
    return Historial(texto=texto, dias=len(dias), pesos=len(todos_pesos), recetas=n_recetas)


def generar(db_path: Path | str, store: Any, hoy: date, *, ahora: datetime | None = None) -> str:
    """The whole nutricion.md for the base `db_path` and the Garmin data `store`, with the days up to `hoy`."""
    return componer(db_path, store, hoy, ahora=ahora).texto


def escribir(texto: str, ruta: Path | str) -> Path:
    """Write `texto` to `ruta` all at once: a failure halfway leaves the previous file as it was (R9)."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, temporal = tempfile.mkstemp(dir=ruta.parent, prefix=f".{ruta.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporal, ruta)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise
    return ruta


__all__ = ["Historial", "componer", "escribir", "generar", "kcal", "sanear"]
