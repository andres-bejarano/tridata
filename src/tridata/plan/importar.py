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

Unit 115 (R5, R-346, R-351, R-352): the same path saves a plan the coach designs. A «semana» row takes an eighth
field, its planned km (`previsto_km`; the rows of before, without it, read the same). `proponer_ordenado` refuses
(`ErrorPlan`, naming the week) a proposal with a week that ends before it starts or two weeks that overlap, instead
of keeping that row as a note, and, given the plans (`planes`), says in `avisos` each race already in another plan the
same day (it is saved only if Andrés says «sí» seeing it).

Unit 116 (REC-58, R-358..R-362): nothing is lost or duplicated. `proponer_ordenado` keeps once a race that comes twice
in the .md (the same day, one name inside the other, `_juntar_carreras`), the one of the objectives, and says it in
`avisos`; a key session of a week said with its day («miércoles: cuestas») becomes a session of that day of the week,
unless the proposal already has it (`_sesiones_de_la_clave`). An «incidencia» row takes its state («abierta», or
«cerrada el F») and its lesson, and «visita|fecha|quién|qué dijo» rows go with the live incident before them, so the
incidents of a .md downloaded from the app come back alive (`Planes.crear`). And every line of the text of a section
no row covers goes to the notes as it is (`_leer_apartado`).

Nothing here logs a text of the plan (R9).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ..storage import DataStore
from ..nutricion.recetas import normalizar
from . import ABIERTA, CERRADA, Planes, comprobar_semanas, hora_de, semanas as semanas_del_plan

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
    """Unit 115: a week that ends before it starts is read too, so `proponer_ordenado` refuses it naming it (R-352);
    its eighth field, the planned km, only when it has them."""
    numero, desde, hasta, previsto, clave, hecho_s, hecho_km, previsto_km = _campos(c, 8)
    n = re.search(r"\d+", numero)
    desde_iso, hasta_iso = _fecha(desde), _fecha(hasta)
    if n is None or desde_iso is None or hasta_iso is None:
        return None
    semana = {"n": int(n[0]), "desde": desde_iso, "hasta": hasta_iso, "previsto_s": duracion(previsto),
              "clave": clave, "hecho_s": duracion(hecho_s), "hecho_km": kilometros(hecho_km)}
    km = kilometros(previsto_km) if previsto_km.strip() else None
    return semana | {"previsto_km": km} if km is not None else semana


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


_CERRADA = re.compile(r"cerrada(?:\s+(?:el\s+)?(\d{4}-\d{2}-\d{2}))?")


def _incidencia(c: list[str]) -> dict | None:
    """Unit 116 (R-361): with a state («abierta», or «cerrada» and its day) it is a live incident, with its lesson and
    the visits of the rows after it; without one, history as before. A state that is none of them: not read."""
    fecha, texto, estado, leccion = _campos(c, 4)
    if not texto and _fecha(fecha) is None:
        fecha, texto = "", fecha
    if not texto:
        return None
    incidencia = {"fecha": _fecha(fecha), "texto": texto}
    if not estado.strip():
        return incidencia
    dicho = normalizar(estado)
    cerrada = _CERRADA.fullmatch(dicho)
    if dicho == ABIERTA:
        viva = {"estado": ABIERTA, "cerrada": None}
    elif cerrada is not None and (cerrada[1] is None or _fecha(cerrada[1]) is not None):
        viva = {"estado": CERRADA, "cerrada": _fecha(cerrada[1]) if cerrada[1] else None}
    else:
        return None
    return incidencia | viva | {"leccion": leccion.strip() or None, "visitas": []}


def _visita(c: list[str]) -> dict | None:
    """Unit 116 (R-361): «visita|fecha|quién|qué dijo», of the live incident before it."""
    fecha, quien, que_dijo = _campos(c, 3)
    if _fecha(fecha) is None or not quien:
        return None
    return {"fecha": _fecha(fecha), "quien": quien, "que_dijo": que_dijo}


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
# unit 116 (R-362): the names of the fields of each kind of row, as `plan_forma` gives them to the coach; a line of the
# text that only says them with the values of the rows («Estado: abierta») is covered by those rows
_NOMBRES = {"carrera": "carrera nombre fecha objetivo estado resultado principal",
            "semana": "semana numero desde hasta previsto clave hecho km", "sesion": "sesion fecha titulo bloques clave hora",
            "sesión": "sesion fecha titulo bloques clave hora", "nutricion": "nutricion producto pauta",
            "nutrición": "nutricion producto pauta", "incidencia": "incidencia fecha texto estado leccion",
            "visita": "visita fecha quien que dijo", "comparativa": "comparativa", "fase": "fase nombre semana desde hasta",
            "nota": "nota"}
# a separator row of a Markdown table: «|---|:---:|»
_SEPARADOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _palabras(texto: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalizar(texto))


def _cubierta(linea: str, de_las_filas: set[str]) -> bool:
    """Unit 116 (R-362): more than half of its words of three or more letters and digits are in the rows (without
    capitals or accents); a line without such words, by all its words; one without any word says nothing."""
    todas = _palabras(linea)
    palabras = [p for p in todas if len(p) >= 3] or todas
    return not palabras or sum(p in de_las_filas for p in palabras) * 2 > len(palabras)


def _sin_cubrir(cuerpo: str, filas: list[str]) -> list[str]:
    """The lines of the text of a section that its rows do not cover (headers and separators of a table apart)."""
    de_las_filas = {p for f in filas for p in _palabras(f)}
    de_las_filas |= {p for f in filas for p in _palabras(_NOMBRES.get(f.split("|")[0].strip().lower(), ""))}
    lineas = cuerpo.splitlines()
    cabeceras = {k - 1 for k, linea in enumerate(lineas) if _SEPARADOR.match(linea)}
    return [linea.strip() for k, linea in enumerate(lineas)
            if linea.strip() and k not in cabeceras and not _SEPARADOR.match(linea)
            and not _cubierta(linea, de_las_filas)]


def _leer_apartado(propuesta: dict[str, Any], i: int, parte: str, filas: list[str], cuerpo: str) -> None:
    """Adds the rows of section `i`; what cannot be read goes to the notes, so no piece of the .md is lost. Unit 116
    (R-362): with rows read, each line of its text no row covers goes to the notes too (a section with a heading and
    nothing else makes no note: its heading is kept as the heading of its section). A «visita» row goes with the live
    incident before it (R-361)."""
    leidas: dict[str, list[dict]] = {k: [] for k in (*PARTES_DEL_PLAN, "fases")}
    sobran: list[str] = []
    semanas_ya = {s["n"] for s in propuesta["semanas"]}
    for fila in filas:
        campos = [c.strip() for c in fila.split("|")]
        tipo = campos[0].lower()
        if tipo == "visita":
            visita = _visita(campos[1:])
            viva = leidas["incidencias"][-1] if leidas["incidencias"] else None
            if visita is not None and viva is not None and "estado" in viva:
                viva["visitas"].append(visita)
            elif fila.strip():
                sobran.append(fila.strip())
            continue
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
        sueltos = [cuerpo] if cuerpo else sobran
        propuesta["notas"].extend({"apartado": i, "tipo": "nota", "texto": t} for t in sueltos)
        return
    for clave, cosas in leidas.items():
        propuesta[clave].extend(cosas)
    propuesta["notas"].extend({"apartado": i, "tipo": "nota", "texto": t} for t in [*sobran, *_sin_cubrir(cuerpo, filas)])


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
    abiertas = len([x for x in p["incidencias"] if x.get("estado") == ABIERTA])
    if abiertas:  # unit 116 (R-361): only when some come in open
        resumen["abiertas"] = abiertas
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


def _mismo_nombre(uno: str, otro: str) -> bool:
    a, b = normalizar(uno), normalizar(otro)
    return bool(a and b and (a in b or b in a))


def _dia_mes(fecha: str) -> str:
    dia = date.fromisoformat(fecha)
    return f"{dia.day}/{dia.month}"


def _juntar_carreras(propuesta: dict[str, Any]) -> list[str]:
    """Unit 116 (R-358): two races of the proposal the same day, one name inside the other, are the same: the one of
    the objectives stays (if neither or both are, the first), with the target of the other if it has none; what to
    say of each other one."""
    de_objetivos = [a["parte"] == "objetivos" for a in propuesta["apartados"]]
    carreras: list[dict] = propuesta["carreras"]
    fuera: set[int] = set()
    avisos = []
    for k, c in enumerate(carreras):
        misma = next((j for j in range(k) if j not in fuera and c.get("fecha") and carreras[j].get("fecha") == c["fecha"]
                      and _mismo_nombre(carreras[j]["nombre"], c["nombre"])), None)
        if misma is None:
            continue
        queda, sale = (k, misma) if de_objetivos[c["apartado"]] and not de_objetivos[carreras[misma]["apartado"]] \
            else (misma, k)
        if not carreras[queda]["objetivo"] and carreras[sale]["objetivo"]:
            carreras[queda]["objetivo"] = carreras[sale]["objetivo"]
        fuera.add(sale)
        avisos.append(f"La carrera «{carreras[sale]['nombre']}» del {_dia_mes(c['fecha'])} era la misma que "
                      f"«{carreras[queda]['nombre']}»: se guarda una sola vez")
    propuesta["carreras"] = [c for k, c in enumerate(carreras) if k not in fuera]
    return avisos


_DIA = r"(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)"
# a piece of the key sessions of a week that starts with its day: at the start, or after «;», «,», «+», «/», a new line
# or «y»; the day followed or not by «:» or «-»
_CON_DIA = re.compile(rf"(?:^|[;,+/\n]|\s+y\s+)\s*{_DIA}\b\s*[:\-–]?\s*", re.IGNORECASE)
_DIAS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}


def _sesiones_de_la_clave(propuesta: dict[str, Any]) -> None:
    """Unit 116 (R-359, R-360): each piece of the key sessions of a week that starts with a day of the week is a key
    session of that day of the week, its text as its title, without blocks, in the section of the week; unless that
    day falls outside the week, or the proposal already has a session that day with that text."""
    for s in propuesta["semanas"]:
        cortes = list(_CON_DIA.finditer(s["clave"] or ""))
        desde, hasta = date.fromisoformat(s["desde"]), date.fromisoformat(s["hasta"])
        for k, corte in enumerate(cortes):
            fin = cortes[k + 1].start() if k + 1 < len(cortes) else len(s["clave"])
            titulo = s["clave"][corte.end():fin].strip().rstrip(";,.+/").strip()
            dia = _DIAS[normalizar(corte[1])]
            fecha = next((desde + timedelta(days=d) for d in range((hasta - desde).days + 1)
                          if (desde + timedelta(days=d)).weekday() == dia), None)
            if not titulo or fecha is None:
                continue
            if any(x["fecha"] == fecha.isoformat() and _mismo_nombre(x["titulo"], titulo) for x in propuesta["sesiones"]):
                continue
            propuesta["sesiones"].append({"apartado": s["apartado"], "fecha": fecha.isoformat(), "titulo": titulo,
                                          "bloques": [], "clave": True})


def _repetidas(carreras: list[dict], planes: Planes) -> list[str]:
    """Unit 115 (R-351): what to say of each race of the proposal already in a plan the same day (by its name: one
    is the other, or one has the other inside, without capitals or accents)."""
    avisos = []
    for c in carreras:
        if not c.get("fecha"):
            continue
        nombre = normalizar(c["nombre"])
        for otra in planes.carreras_el_dia(c["fecha"]):
            suya = normalizar(otra["nombre"])
            if nombre and suya and (nombre in suya or suya in nombre):
                dia = date.fromisoformat(c["fecha"])
                avisos.append(f"La carrera «{c['nombre']}» del {dia.day}/{dia.month} ya está en el plan «{otra['plan']}»"
                              f" (como «{otra['nombre']}»): si dices que sí, estará en los dos planes")
                break
    return avisos


def proponer_ordenado(apartados: list[Apartado], store: DataStore | None, hoy: date,
                      nombre: str | None = None, planes: Planes | None = None) -> dict[str, Any]:
    """The code's half (unit 081): the proposal of sections already ordered (nothing saved). What cannot be read goes
    to the notes as it was (R6, R-132); each week done is crossed with Garmin (G-54). Unit 115: `ErrorPlan`, naming
    the week, for a week that ends before it starts or overlaps another (R-352); with `planes`, `avisos` says each race
    already in another plan that day (R-351). Unit 116: a race twice in the .md is kept once and said in `avisos`
    (R-358, before the races in another plan, so each one is said once); the key sessions with their day are sessions
    (R-359, R-360)."""
    propuesta: dict[str, Any] = {"nombre": nombre or NOMBRE_POR_DEFECTO, "apartados": [],
                                 **{k: [] for k in PARTES_DEL_PLAN}, "fases": []}
    for i, a in enumerate(apartados):
        propuesta["apartados"].append({"titulo": a.titulo, "parte": a.parte})
        _leer_apartado(propuesta, i, a.parte, list(a.filas), a.cuerpo)
    try:
        comprobar_semanas(propuesta["semanas"])
    except ValueError as exc:
        raise ErrorPlan(str(exc)) from None
    juntadas = _juntar_carreras(propuesta)
    _sesiones_de_la_clave(propuesta)
    _leer_fases(propuesta)
    propuesta["diferencias"] = _diferencias(propuesta["semanas"], store, hoy)
    propuesta["resumen"] = _resumen(propuesta)
    if planes is not None or juntadas:
        propuesta["avisos"] = juntadas + (_repetidas(propuesta["carreras"], planes) if planes is not None else [])
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
                  "hecho_plan_km": s["hecho_km"] if s["n"] in del_plan else None,
                  "previsto_km": s.get("previsto_km")}  # unit 115
               for s in propuesta["semanas"]]
    datos = {k: propuesta[k] for k in ("nombre", "apartados", *PARTES_DEL_PLAN)} | {"semanas": semanas}
    return planes.crear(datos, ahora, fases=propuesta.get("fases") or [])


__all__ = ["TOLERANCIA_KM", "TOLERANCIA_S", "Apartado", "ErrorPlan", "Trozo", "apartados_del_coach", "confirmar", "duracion",
           "kilometros", "nombre_del_plan", "ordenar", "proponer", "proponer_ordenado", "trocear"]
