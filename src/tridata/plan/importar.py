"""Import the plan's .md (unit 040, R5, R6, G-54): cut it, order it, cross it with Garmin, propose.

1. `trocear`: the code cuts the .md by its sections (its «## » headings; without any, its «# » ones; without any «#»,
   the numbered lines «1. Objetivos», «2. …» of a plan pasted from a document, bug 046). The pieces put together are
   the whole .md, character by character: nothing is lost on the way.
2. `proponer`: each section with text goes to an orderer (one call each, several at a time), which says its part and
   its rows of text; the code reads the rows. A section that is no part, or whose rows the
   code cannot read, is kept as a note of the plan with its text as it was (R6); a single row it cannot read, as a
   note with that row. Then each week already done is crossed with Garmin (its running, in time in movement) and
   every difference beyond the tolerance (`TOLERANCIA_S`, `TOLERANCIA_KM`) is proposed with what the plan says,
   because it comes from Strava. If any call fails, `ErrorPlan` and nothing is proposed: it can be tried again.
3. `confirmar`: only then is the plan saved (`Planes.crear`), with what Andrés chose for each difference: «plan»
   (by default) keeps the plan's done as «hecho según el plan», which wins over Garmin; «garmin» leaves Garmin's.

Unit 081 (R-131, R-132): step 2 is two halves. `ordenar` is an orderer's (each section to its part and its rows, as
`Apartado`); `proponer_ordenado` is the code's (read the rows, keep what cannot be read as notes, cross with Garmin).
The coach of the PC orders the .md itself and gives its sections (`apartados_del_coach`), so the app never calls the
paid AI for it.

Two rows the app's AI never wrote are read too: «fase|nombre|semana desde|semana hasta» (a phase over weeks the plan
has) and a fifth field of «sesion», its hour («20:00»).

Unit 083: the orderer of the paid API (`ia.plan.OrdenadorPlan`) is gone; the coach's path is the only one in use.
`ordenar` / `proponer` stay, generic over any `ordenador` with `ordenar(titulo, texto, hoy) -> (apartado, uso,
modelo)`, because tests outside that unit build their plans through them with a double
(`test_plan_importar.OrdenadorFalso`). The phases are saved with the plan in ONE write (`Planes.crear(fases=…)`, R4).

Nothing here logs a text of the plan (R9).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ..storage import DataStore
from . import Planes, hora_de, semanas as semanas_del_plan

TOLERANCIA_S = 120  # ±2 min per week: Strava and Garmin round differently, that is no difference
TOLERANCIA_KM = 0.3  # ±0,3 km per week
LLAMADAS_A_LA_VEZ = 4
NOMBRE_POR_DEFECTO = "Plan importado"


class ErrorPlan(Exception):
    """An orderer could not order a section (it says why, never a text of the plan)."""


@dataclass(frozen=True)
class Apartado:
    """One section ordered: its heading, its part (`_PARTES`), its rows of text and its text as it was."""

    titulo: str
    parte: str
    filas: tuple[str, ...]
    cuerpo: str


@dataclass(frozen=True)
class Trozo:
    titulo: str  # the heading of the section, "" for what comes before the first one
    texto: str  # the piece of the .md exactly as it was, its heading line included
    cuerpo: str  # its text without the heading (nor the «# » title of the plan), without blank ends


_FENCE = re.compile(r"^\s*(```|~~~)")
# a section of a plan pasted from a document: «3. Previsto (se construye semana a semana)», short, capital after the dot
_NUMERADO = re.compile(r"^(\d{1,2})\.[ \t]+([A-ZÁÉÍÓÚÑ¿¡].{0,78})$")
_ITEM = re.compile(r"^\d+[.)][ \t]")
# unit 077: «20:00 · Rodaje», the hour «Bajar .md» writes before the title of a session (R-220)
_HORA_DELANTE = re.compile(r"^(\d{1,2}:\d{2})\s*·\s*(\S.*)$")  # a numbered line of any kind: next to another one, it is a list, not a section


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
    fecha, titulo, bloques, clave, hora_dicha = _campos(c, 5)
    if _fecha(fecha) is None or not titulo:
        return None
    sesion = {"fecha": _fecha(fecha), "titulo": titulo, "bloques": [b.strip() for b in bloques.split(";") if b.strip()],
              "clave": _si(clave)}
    if hora_dicha:  # unit 081: its hour in a field of its own; one that is no hour, the row is not read (to notes)
        try:
            return sesion | {"hora": hora_de(hora_dicha)}
        except ValueError:
            return None
    delante = _HORA_DELANTE.match(titulo)
    if delante is not None:  # unit 077: the hour goes back to the session; one that is no hour stays in the title
        try:
            hora = hora_de(delante[1])
        except ValueError:
            hora = None
        if hora is not None:
            sesion |= {"titulo": delante[2].strip(), "hora": hora}
    return sesion


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


def _fase(c: list[str]) -> dict | None:
    """Unit 081: «fase|Base|1|4», a phase over weeks 1 to 4 (checked against the weeks in `_leer_fases`)."""
    nombre, desde, hasta = _campos(c, 3)
    if not nombre or not desde.strip().isdigit() or not hasta.strip().isdigit():
        return None
    return {"nombre": nombre, "desde_n": int(desde), "hasta_n": int(hasta)}


_LECTORES: dict[str, tuple[str, Callable[[list[str]], dict | None]]] = {
    "carrera": ("carreras", _carrera), "semana": ("semanas", _semana), "sesion": ("sesiones", _sesion),
    "sesión": ("sesiones", _sesion), "nutricion": ("nutricion", _nutricion), "nutrición": ("nutricion", _nutricion),
    "incidencia": ("incidencias", _incidencia), "comparativa": ("notas", _comparativa), "fase": ("fases", _fase),
}
PARTES_DEL_PLAN = ("carreras", "semanas", "sesiones", "nutricion", "incidencias", "notas")


def _leer_apartado(propuesta: dict[str, Any], i: int, parte: str, filas: list[str], cuerpo: str) -> None:
    """Adds the rows of section `i`; what cannot be read goes to the notes, so no piece of the .md is lost."""
    leidas: dict[str, list[dict]] = {k: [] for k in (*PARTES_DEL_PLAN, "fases")}
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


def _leer_fases(propuesta: dict[str, Any]) -> None:
    """Unit 081: a phase stays only over weeks the plan has and in no other phase (as `Planes.anadir_fase` checks);
    any other one goes to the notes with its row, so nothing is lost and saving it never fails."""
    tiene = {s["n"] for s in propuesta["semanas"]}
    buenas: list[dict] = []
    for f in propuesta["fases"]:
        pisa = any(f["desde_n"] <= b["hasta_n"] and b["desde_n"] <= f["hasta_n"] for b in buenas)
        if f["desde_n"] in tiene and f["hasta_n"] in tiene and f["desde_n"] <= f["hasta_n"] and not pisa:
            buenas.append(f)
        else:
            propuesta["notas"].append({"apartado": f["apartado"], "tipo": "nota",
                                       "texto": f"fase | {f['nombre']} | {f['desde_n']} | {f['hasta_n']}"})
    propuesta["fases"] = buenas


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
    resumen = {"carreras": len(p["carreras"]), "semanas_hechas": len(hechas),
               "previstas": len(p["semanas"]) - len(hechas),
               "sesiones_clave": len([s for s in p["sesiones"] if s["clave"]]),
               "nutricion": len(p["nutricion"]), "incidencias": len(p["incidencias"]),
               "comparativa": len([n for n in p["notas"] if n["tipo"] == "comparativa"]),
               "notas": len([n for n in p["notas"] if n["tipo"] == "nota"])}
    if p.get("fases"):  # unit 081: only when it has them, so a plan without them reads as before
        resumen["fases"] = len(p["fases"])
    return resumen


def ordenar(texto: str, ordenador: Any, hoy: date) -> list[Apartado]:
    """The orderer's half (unit 081): each section of the .md with text, ordered by `ordenador` (one call each, several at
    a time); one with only its heading is a «nota» without rows. Raises `ErrorPlan` if any call failed."""
    con_texto = [t for t in trocear(texto) if t.cuerpo or t.titulo]

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
    if fallo is not None:
        raise ErrorPlan(str(fallo))
    return [Apartado(t.titulo, r[0].parte if r is not None else "nota", tuple(r[0].filas) if r is not None else (),
                     t.cuerpo)
            for t, r in zip(con_texto, respuestas, strict=True)]


def proponer_ordenado(apartados: list[Apartado], store: DataStore | None, hoy: date,
                      nombre: str | None = None) -> dict[str, Any]:
    """The code's half (unit 081): the proposal of sections already ordered (nothing saved). What cannot be read goes
    to the notes as it was (R6, R-132); each week done is crossed with Garmin (G-54)."""
    propuesta: dict[str, Any] = {"nombre": nombre or NOMBRE_POR_DEFECTO, "apartados": [],
                                 **{k: [] for k in PARTES_DEL_PLAN}, "fases": []}
    for i, a in enumerate(apartados):
        propuesta["apartados"].append({"titulo": a.titulo, "parte": a.parte})
        _leer_apartado(propuesta, i, a.parte, list(a.filas), a.cuerpo)
    _leer_fases(propuesta)
    propuesta["diferencias"] = _diferencias(propuesta["semanas"], store, hoy)
    propuesta["resumen"] = _resumen(propuesta)
    return propuesta


def proponer(texto: str, ordenador: Any, store: DataStore | None, hoy: date,
             nombre: str | None = None) -> dict[str, Any]:
    """The proposal of the .md (nothing saved). Raises `ErrorPlan` if the orderer could not order every section."""
    return proponer_ordenado(ordenar(texto, ordenador, hoy), store, hoy, nombre_del_plan(texto) or nombre)


_PARTES = ("objetivos", "semanas_hechas", "previsto", "sesiones_clave", "nutricion", "incidencias", "comparativa",
           "nota")


def apartados_del_coach(entrada: Any) -> tuple[str | None, list[Apartado]]:
    """Unit 081: `{nombre?, apartados: [{titulo, parte, filas, texto}]}` from the coach of the PC, as `ordenar` gives
    them: (the name, the sections). ValueError saying which fields to mend (never what was sent)."""
    if not isinstance(entrada, dict):
        raise ValueError("plan (un objeto con nombre y apartados)")
    crudos = entrada.get("apartados")
    if not isinstance(crudos, list) or not crudos:
        raise ValueError("apartados (una lista con al menos un apartado)")
    nombre = entrada.get("nombre")
    if nombre is not None and not isinstance(nombre, str):
        raise ValueError("nombre")
    apartados, malos = [], []
    for i, a in enumerate(crudos):
        if not isinstance(a, dict):
            malos.append(f"apartados.{i}")
            continue
        titulo, parte, filas, cuerpo = a.get("titulo") or "", a.get("parte"), a.get("filas") or [], a.get("texto") or ""
        campos = [c for c, bien in (("titulo", isinstance(titulo, str)), ("parte", parte in _PARTES),
                                    ("filas", isinstance(filas, list) and all(isinstance(f, str) for f in filas)),
                                    ("texto", isinstance(cuerpo, str))) if not bien]
        malos += [f"apartados.{i}.{c}" for c in campos]
        if not campos:
            apartados.append(Apartado(titulo.strip(), str(parte), tuple(filas), cuerpo.strip()))
    if malos:
        raise ValueError(", ".join(malos))
    return (nombre.strip() or None) if nombre else None, apartados


def confirmar(propuesta: dict[str, Any], elecciones: dict[str, str], planes: Planes, ahora: datetime) -> int:
    """Saves the proposal: for each difference, «garmin» leaves Garmin's done; anything else keeps the plan's.
    Unit 081: and its phases, if it has any (checked when proposed, `_leer_fases`); unit 083 (R4): in the same write
    as the plan, so a failure saves nothing."""
    del_plan = {d["semana"] for d in propuesta["diferencias"] if elecciones.get(str(d["semana"])) != "garmin"}
    semanas = [{k: s[k] for k in ("apartado", "n", "desde", "hasta", "previsto_s", "clave")}
               | {"hecho_plan_s": s["hecho_s"] if s["n"] in del_plan else None,
                  "hecho_plan_km": s["hecho_km"] if s["n"] in del_plan else None}
               for s in propuesta["semanas"]]
    datos = {k: propuesta[k] for k in ("nombre", "apartados", *PARTES_DEL_PLAN)} | {"semanas": semanas}
    return planes.crear(datos, ahora, fases=propuesta.get("fases") or [])


__all__ = ["TOLERANCIA_KM", "TOLERANCIA_S", "Apartado", "ErrorPlan", "Trozo", "apartados_del_coach", "confirmar", "duracion",
           "kilometros", "nombre_del_plan", "ordenar", "proponer", "proponer_ordenado", "trocear"]
