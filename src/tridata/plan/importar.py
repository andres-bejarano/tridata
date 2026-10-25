"""Import the plan's .md (unit 040, R5, R6, G-54): cut it, order it with the AI, cross it with Garmin, propose.

1. `trocear`: the code cuts the .md by its sections (its «## » headings; without any, its «# » ones; without any «#»,
   the numbered lines «1. Objetivos», «2. …» of a plan pasted from a document, bug 046). The pieces put together are
   the whole .md, character by character: nothing is lost on the way.
2. `proponer`: each section with text goes to the AI (`ia.plan.OrdenadorPlan`, one call each, several at a time),
   which says its part and its rows of text; the code reads the rows. A section that is no part, or whose rows the
   code cannot read, is kept as a note of the plan with its text as it was (R6); a single row it cannot read, as a
   note with that row. Then each week already done is crossed with Garmin (its running, in time in movement) and
   every difference beyond the tolerance (`TOLERANCIA_S`, `TOLERANCIA_KM`) is proposed with what the plan says,
   because it comes from Strava. The spending of every call answered goes to `al_gastar(modelo, uso)`, also when
   another one fails. If any call fails, `ErrorPlan` and nothing is proposed: it can be tried again.
3. `confirmar`: only then is the plan saved (`Planes.crear`), with what Andrés chose for each difference: «plan»
   (by default) keeps the plan's done as «hecho según el plan», which wins over Garmin; «garmin» leaves Garmin's.

Nothing here logs a text of the plan (R9).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ..ia.plan import ErrorPlan
from ..storage import DataStore
from . import Planes, semanas as semanas_del_plan

TOLERANCIA_S = 120  # ±2 min per week: Strava and Garmin round differently, that is no difference
TOLERANCIA_KM = 0.3  # ±0,3 km per week
LLAMADAS_A_LA_VEZ = 4
NOMBRE_POR_DEFECTO = "Plan importado"


@dataclass(frozen=True)
class Trozo:
    titulo: str  # the heading of the section, "" for what comes before the first one
    texto: str  # the piece of the .md exactly as it was, its heading line included
    cuerpo: str  # its text without the heading (nor the «# » title of the plan), without blank ends


_FENCE = re.compile(r"^\s*(```|~~~)")
# a section of a plan pasted from a document: «3. Previsto (se construye semana a semana)», short, capital after the dot
_NUMERADO = re.compile(r"^(\d{1,2})\.[ \t]+([A-ZÁÉÍÓÚÑ¿¡].{0,78})$")
_ITEM = re.compile(r"^\d+[.)][ \t]")  # a numbered line of any kind: next to another one, it is a list, not a section


def _titulos(lineas: list[str], marca: str) -> list[int]:
    dentro, indices = False, []
    for i, linea in enumerate(lineas):
        if _FENCE.match(linea):
            dentro = not dentro
        elif not dentro and linea.startswith(marca):
            indices.append(i)
    return indices


def _numerados(lineas: list[str]) -> dict[int, str]:
    """The numbered sections «1. …», «2. …» in order (1, 2, 3… with no jump, and never with another numbered line
    just above or below: that is a list), so a list or a «1 día» inside a section never cuts it; as {line index:
    title}; only when the .md has no «#» heading at all."""
    llenas = [i for i, x in enumerate(lineas) if x.strip()]
    vecinas = {i: [lineas[j].strip() for j in llenas[k - 1:k] + llenas[k + 1:k + 2]] for k, i in enumerate(llenas)}
    dentro, siguiente, cortes = False, 1, {}
    for i, linea in enumerate(lineas):
        if _FENCE.match(linea):
            dentro = not dentro
            continue
        m = None if dentro else _NUMERADO.match(linea.strip())
        en_lista = m is not None and any(_ITEM.match(v) for v in vecinas[i])
        if m and not en_lista and int(m.group(1)) == siguiente:
            cortes[i] = m.group(2).strip()
            siguiente += 1
    return cortes


def _primera_linea(lineas: list[str]) -> int | None:
    return next((i for i, x in enumerate(lineas) if x.strip()), None)


def trocear(texto: str) -> list[Trozo]:
    """The sections of the .md, in order; `"".join(t.texto for t in trozos) == texto`."""
    lineas = texto.splitlines(keepends=True)
    if _titulos(lineas, "## ") or _titulos(lineas, "# "):
        marca = "## " if _titulos(lineas, "## ") else "# "
        titulos = {i: lineas[i][len(marca):].strip() for i in _titulos(lineas, marca)}
    else:
        marca, titulos = "", _numerados(lineas)
    limites = sorted({0, *titulos, len(lineas)})
    trozos = []
    for inicio, fin in zip(limites, limites[1:], strict=False):
        pedazo = lineas[inicio:fin]
        if inicio in titulos:
            titulo, resto = titulos[inicio], pedazo[1:]
        else:
            titulo = ""
            resto = [x for x in pedazo if not (marca == "## " and x.startswith("# "))]
            if not marca and titulos and (primera := _primera_linea(resto)) is not None:
                resto = resto[:primera] + resto[primera + 1:]  # the plan's title, as «# » is in the «## » shape
        trozos.append(Trozo(titulo=titulo, texto="".join(pedazo), cuerpo="".join(resto).strip()))
    return trozos


def nombre_del_plan(texto: str) -> str | None:
    """The «# » title of the .md when its sections are «## »; the first line before «1. …» when they are numbered;
    else None."""
    lineas = texto.splitlines()
    if _titulos(lineas, "## "):
        return next((x[2:].strip() for x in lineas if x.startswith("# ") and x[2:].strip()), None)
    if _titulos(lineas, "# "):
        return None
    cortes = _numerados(lineas)
    if not cortes:
        return None
    primera = _primera_linea(lineas[:min(cortes)])
    return lineas[primera].strip() if primera is not None else None


# -- reading the rows the AI gives ------------------------------------------------------------------------------------

def _fecha(texto: str) -> str | None:
    try:
        return date.fromisoformat(texto.strip()).isoformat()
    except ValueError:
        return None


def _si(texto: str) -> bool:
    return texto.strip().lower() in {"sí", "si", "s", "yes", "true", "x", "✓", "principal", "clave"}


def duracion(texto: str) -> int | None:
    """Seconds of «3:16», «1:02:30», «3h16», «3h», «48'» or «48 min»; None if it is none of them."""
    t = texto.strip().lstrip("~≈").strip().lower()
    if m := re.fullmatch(r"(\d+):(\d{2})(?::(\d{2}))?", t):
        return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3] or 0)
    if m := re.fullmatch(r"(\d+)\s*h\s*(?:(\d{1,2})\s*(?:'|m|min)?)?", t):
        return int(m[1]) * 3600 + int(m[2] or 0) * 60
    if m := re.fullmatch(r"(\d+)\s*(?:'|min)", t):
        return int(m[1]) * 60
    return None


def kilometros(texto: str) -> float | None:
    t = re.sub(r"\s*km$", "", texto.strip().lstrip("~≈").strip().lower()).replace(",", ".")
    try:
        return round(float(t), 2)
    except ValueError:
        return None


def _campos(campos: list[str], n: int) -> list[str]:
    return [*campos, *[""] * n][:n]


def _carrera(c: list[str]) -> dict | None:
    nombre, fecha, objetivo, estado, principal = _campos(c, 5)
    if not nombre:
        return None
    return {"nombre": nombre, "fecha": _fecha(fecha), "objetivo": objetivo, "estado": estado,
            "principal": _si(principal)}


def _semana(c: list[str]) -> dict | None:
    numero, desde, hasta, previsto, clave, hecho_s, hecho_km = _campos(c, 7)
    n = re.search(r"\d+", numero)
    desde_iso, hasta_iso = _fecha(desde), _fecha(hasta)
    if n is None or desde_iso is None or hasta_iso is None or hasta_iso < desde_iso:
        return None
    return {"n": int(n[0]), "desde": desde_iso, "hasta": hasta_iso, "previsto_s": duracion(previsto), "clave": clave,
            "hecho_s": duracion(hecho_s), "hecho_km": kilometros(hecho_km)}


def _sesion(c: list[str]) -> dict | None:
    fecha, titulo, bloques, clave = _campos(c, 4)
    if _fecha(fecha) is None or not titulo:
        return None
    return {"fecha": _fecha(fecha), "titulo": titulo, "bloques": [b.strip() for b in bloques.split(";") if b.strip()],
            "clave": _si(clave)}


def _nutricion(c: list[str]) -> dict | None:
    producto, pauta = _campos(c, 2)
    return {"producto": producto, "pauta": pauta} if producto else None


def _incidencia(c: list[str]) -> dict | None:
    fecha, texto = _campos(c, 2)
    if not texto and _fecha(fecha) is None:
        fecha, texto = "", fecha
    return {"fecha": _fecha(fecha), "texto": texto} if texto else None


def _comparativa(c: list[str]) -> dict | None:
    texto = " | ".join(x for x in c if x)
    return {"tipo": "comparativa", "texto": texto} if texto else None


_LECTORES: dict[str, tuple[str, Callable[[list[str]], dict | None]]] = {
    "carrera": ("carreras", _carrera), "semana": ("semanas", _semana), "sesion": ("sesiones", _sesion),
    "sesión": ("sesiones", _sesion), "nutricion": ("nutricion", _nutricion), "nutrición": ("nutricion", _nutricion),
    "incidencia": ("incidencias", _incidencia), "comparativa": ("notas", _comparativa),
}
PARTES_DEL_PLAN = ("carreras", "semanas", "sesiones", "nutricion", "incidencias", "notas")


def _leer_apartado(propuesta: dict[str, Any], i: int, parte: str, filas: list[str], cuerpo: str) -> None:
    """Adds the rows of section `i`; what cannot be read goes to the notes, so no piece of the .md is lost."""
    leidas: dict[str, list[dict]] = {k: [] for k in PARTES_DEL_PLAN}
    sobran: list[str] = []
    semanas_ya = {s["n"] for s in propuesta["semanas"]}
    for fila in filas:
        campos = [c.strip() for c in fila.split("|")]
        tipo = campos[0].lower()
        lector = _LECTORES.get(tipo)
        cosa = lector[1](campos[1:]) if lector is not None else None
        if lector is None or cosa is None or (lector[0] == "semanas" and cosa["n"] in semanas_ya):
            texto = " | ".join(x for x in campos[1:] if x) if tipo == "nota" else fila.strip()
            if texto:
                sobran.append(texto)
            continue
        if lector[0] == "semanas":
            semanas_ya.add(cosa["n"])
        leidas[lector[0]].append({"apartado": i, **cosa})
    if parte == "nota" or not any(leidas.values()):
        if cuerpo:
            propuesta["notas"].append({"apartado": i, "tipo": "nota", "texto": cuerpo})
        return
    for clave, cosas in leidas.items():
        propuesta[clave].extend(cosas)
    propuesta["notas"].extend({"apartado": i, "tipo": "nota", "texto": t} for t in sobran)


def _diferencias(semanas: list[dict], store: DataStore | None, hoy: date) -> list[dict]:
    """The weeks done whose running in Garmin is not what the plan says (beyond the tolerance)."""
    diferencias = []
    for s in semanas:
        if (s["hecho_s"] is None and s["hecho_km"] is None) or date.fromisoformat(s["desde"]) > hoy:
            continue
        hecho = semanas_del_plan.hecho(store, date.fromisoformat(s["desde"]), date.fromisoformat(s["hasta"]), hoy)
        carrera = hecho["carrera"] if hecho is not None else {"n": 0}
        garmin = ({"segundos": carrera["segundos"], "km": carrera["km"]} if carrera["n"]
                  else {"segundos": None, "km": None})
        distinto = garmin["segundos"] is None or (
            s["hecho_s"] is not None and abs(s["hecho_s"] - garmin["segundos"]) > TOLERANCIA_S) or (
            s["hecho_km"] is not None and abs(s["hecho_km"] - garmin["km"]) > TOLERANCIA_KM + 1e-9)
        if distinto:
            diferencias.append({"semana": s["n"], "desde": s["desde"], "hasta": s["hasta"],
                                "plan": {"segundos": s["hecho_s"], "km": s["hecho_km"]}, "garmin": garmin,
                                "propuesta": "plan"})
    return diferencias


def _resumen(p: dict[str, Any]) -> dict[str, int]:
    hechas = [s for s in p["semanas"] if s["hecho_s"] is not None or s["hecho_km"] is not None]
    return {"carreras": len(p["carreras"]), "semanas_hechas": len(hechas),
            "previstas": len(p["semanas"]) - len(hechas), "sesiones_clave": len([s for s in p["sesiones"] if s["clave"]]),
            "nutricion": len(p["nutricion"]), "incidencias": len(p["incidencias"]),
            "comparativa": len([n for n in p["notas"] if n["tipo"] == "comparativa"]),
            "notas": len([n for n in p["notas"] if n["tipo"] == "nota"])}


def proponer(texto: str, ordenador: Any, store: DataStore | None, hoy: date,
             al_gastar: Callable[[str, dict[str, int]], Any] | None = None, nombre: str | None = None) -> dict[str, Any]:
    """The proposal of the .md (nothing saved). Raises `ErrorPlan` if the AI could not order every section."""
    trozos = trocear(texto)
    con_texto = [t for t in trozos if t.cuerpo or t.titulo]

    def ordenar(t: Trozo) -> Any:
        if not t.cuerpo:
            return None
        try:
            return ordenador.ordenar(t.titulo, t.cuerpo, hoy)
        except ErrorPlan as exc:
            return exc

    with ThreadPoolExecutor(max_workers=LLAMADAS_A_LA_VEZ) as hilos:
        respuestas = list(hilos.map(ordenar, con_texto))
    fallo = next((r for r in respuestas if isinstance(r, ErrorPlan)), None)
    for r in respuestas:
        if r is not None and not isinstance(r, ErrorPlan) and al_gastar is not None:
            al_gastar(r[2], r[1])
    if fallo is not None:
        raise ErrorPlan(str(fallo))

    propuesta: dict[str, Any] = {"nombre": nombre_del_plan(texto) or nombre or NOMBRE_POR_DEFECTO, "apartados": [],
                                 **{k: [] for k in PARTES_DEL_PLAN}}
    for i, (t, r) in enumerate(zip(con_texto, respuestas, strict=True)):
        parte = r[0].parte if r is not None else "nota"
        propuesta["apartados"].append({"titulo": t.titulo, "parte": parte})
        _leer_apartado(propuesta, i, parte, list(r[0].filas) if r is not None else [], t.cuerpo)
    propuesta["diferencias"] = _diferencias(propuesta["semanas"], store, hoy)
    propuesta["resumen"] = _resumen(propuesta)
    return propuesta


def confirmar(propuesta: dict[str, Any], elecciones: dict[str, str], planes: Planes, ahora: datetime) -> int:
    """Saves the proposal: for each difference, «garmin» leaves Garmin's done; anything else keeps the plan's."""
    del_plan = {d["semana"] for d in propuesta["diferencias"] if elecciones.get(str(d["semana"])) != "garmin"}
    semanas = [{k: s[k] for k in ("apartado", "n", "desde", "hasta", "previsto_s", "clave")}
               | {"hecho_plan_s": s["hecho_s"] if s["n"] in del_plan else None,
                  "hecho_plan_km": s["hecho_km"] if s["n"] in del_plan else None}
               for s in propuesta["semanas"]]
    datos = {k: propuesta[k] for k in ("nombre", "apartados", *PARTES_DEL_PLAN)} | {"semanas": semanas}
    return planes.crear(datos, ahora)


__all__ = ["TOLERANCIA_KM", "TOLERANCIA_S", "Trozo", "confirmar", "duracion", "kilometros", "nombre_del_plan",
           "proponer", "trocear"]
