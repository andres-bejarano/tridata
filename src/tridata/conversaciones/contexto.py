"""The context the team sees (unit 032, R3, R5 · R-114, G-46, S-36, S-39): ALL of it, always, built on this PC.

Unit 083: without the paid AI, who reads it is the coach of the PC (`get_contexto` of `tridata-mcp`,
`conversaciones/desde_pc.py`). Its blocks (`Contexto.bloques`), in this order (P3):

1. the fixed instructions and the catalogue of the actions (`nutricion.asistente.texto_acciones`, G-37);
2. the medical transcription (`conversaciones/medicos.py`): the blood test and the stress test, or why they are not;
3. the data, in JSON with sorted keys: the last 8 weeks day by day (foods, weight, steps, resting HR, sleep, HRV,
   trainings and the load), the earlier weeks summed up by week (never the names of the foods), the settings in
   force today, the races and the HR of Ajustes with its origin (031, G-49).

Unit 041 (REC-23, R2 · G-46): the data also carry the plan of the current objective (`plan.Planes.en_curso`: its
races, its weeks with what was planned and what was done — `plan/semanas.py` —, its sessions with their blocks and
the race nutrition) and its incidents. The trainings carry their id, so the coach can open their laps
(`get_activity` of the MCP): the laps never go here (S-54).

Unit 044 (REC-24, R-149 · G-46): "incidencias" carries the open incidents of every plan, always, and the closed ones of
the plan in course, each one with its state, its visits to the physio or the doctor and its lesson (the ones of the
.md, as before: date and text); and the plan carries the notes of each day (`notas_del_dia`).

Unit 048: each session of the plan carries its id (`Planes.sesiones_reloj`), so a role can name one of two with the
same title the same day in the changes that move or remove it.

Unit 059 (R4): each race of the plan carries its result (by hand or from Garmin, `plan/semanas.py`) and the strategy
by stretches it has saved, so the team knows it without Andrés pasting it.

Unit 076 (R-220): a session with an hour carries its `hora` («HH:MM»); one without it, the same keys as before.

Unit 067 (R-204): the plan carries its phases (name and the weeks they span) and each week inside one its `fase`.

Unit 082 (R-230): each day carries `suplementacion` (the coffees and the B12 by hour with their mg and their share of
the milk, the caffeine of the day, the last dose with caffeine and the milk of the coffees) and `horas_comidas` (the
hour of each section with food that day, «~08:00» when it is the typical one), next to its sleep. Read without
creating anything (`nutricion.suplementacion.leer_todo`).

Unit 099 (R-279): each day with eaten foods that carry their three macros has `macros` (the grams eaten of protein,
carbohydrates and fat and the share of its kcal they cover), and the block `macros` is the card of Hoy
(`nutricion.macros.vista`: today with its objective and a `texto`, yesterday with its verdict, the 8 weeks).
Unit 101 (R-285): `alimentos_sin_macros` says how many foods of the last 14 days have none, only when some.

Unit 103 (REC-47 of `ver-fitness`, S-100): `carreras_sin_zapatilla`, the runs of Garmin without a shoe, read only.
Unit 102 (REC-46 of `ver-fitness`, R-291): `zapatillas`, the shoes in use with their km, cap, what is left, their
mark and where the km come from (Strava or said by Andrés) with its date and weekday; only when there are some, read
without creating anything (`zapatillas.leer_sin_crear`).

Unit 100 (R-280..R-282): only the last `DIAS_CON_ALIMENTOS` days (today and the 13 before) carry their foods
(`comidas`), their `horas_comidas` and their whole `suplementacion`; the earlier days of the 8 weeks carry everything
else but, of the supplementation, only the caffeine of the day. Their detail is asked by dates (`detalle_dias`, the
tool `get_comidas` of the MCP), built by the same `_detalle` as the recent days.

Unit 111 (R-336, C-428): each day of "dias" carries its `objetivo` (its % and where it comes from: «de Ajustes»,
«mantenimiento por la carrera <nombre>», «cambiado a mano» or «sin plan en Ajustes», `margen.origen_del_objetivo`), and
`objetivos_no_de_ajustes` the days from today to `DIAS_OBJETIVOS` ahead whose objective does not come from Ajustes.

Unit 112 (R-344, C-438): a food marked as dessert carries `postre: true` (only that one); `variantes_de_recetas`, the
variants of each saved recipe with their kcal/100 g and macros per 100 g, only when there are some (read without
creating anything).

Unit 114 (R4, R5): the plan carries its `comparativa` (its notes of that type, apart from `notas`) and each race not
yet run its `anteriores`: the activities of earlier years whose name shares a word of its own with the name of the
race (`_PALABRAS_GENERICAS` left out) and whose date falls within `DIAS_EDICION` of the same day of the year, with its
id, so the coach opens its laps with `get_activity`.

None of it carries the hour: with the same data it is the same text byte to byte. It changes only when a datum
changes (a food, a weight, a sync) or the day does. Read only: nothing here writes.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean
from typing import Any

from ..calendario import DESDE_HECHOS
from ..fitness import texto_pulso
from ..hoy import _deporte
from ..metrics import compute_pmc, daily_tss, pmc_hasta
from ..nutricion.ajustes import DESDE_SIEMPRE, Ajustes
from ..nutricion.asistente import texto_acciones
from ..nutricion.dia import plan_del_dia
from ..nutricion.macros import texto as texto_macros
from ..nutricion.macros import vista as vista_macros
from ..nutricion.margen import objetivo_del_dia, origen_del_objetivo
from ..nutricion.objetivo_dia import ObjetivosDia
from ..nutricion.peso import RegistroPeso
from ..nutricion.recetas import normalizar
from ..nutricion.registro import DIAS_MACROS, MACROS, SECCIONES, RegistroComidas, sin_macros_desde
from ..nutricion.suplementacion import horas_de_secciones, leer_productos, leer_todo, resumen_dia
from ..plan import ABIERTA, Planes
from ..plan import semanas as semanas_del_plan
from ..storage import DataStore
from ..sync import ALL_SPORT_TYPES, CYCLING_TYPES, RUNNING_TYPES, SWIMMING_TYPES
from ..zapatillas import (carreras_para_el_contexto, leer_sin_crear as zapatillas_sin_crear, para_el_contexto,
                          pendientes_sin_crear)

DIAS_DIA_A_DIA = 56  # the last 8 weeks day by day; the earlier ones by week (S-39)
DIAS_CON_ALIMENTOS = 14  # unit 100 (R-280): today and the 13 before carry each food; the earlier ones, by dates
VE_SIEMPRE = ("comidas", "peso", "entrenos", "sueño", "pulso", "HRV")
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
# unit 054 (R7): what «TriData» of Google Calendar carries, so the team does not promise what does not happen
CALENDARIO_CONECTADO = (f"las sesiones previstas del plan en curso (todas, también las de días pasados, como eventos de "
                        f"todo el día) y los entrenos hechos según Garmin desde el {DESDE_HECHOS.isoformat()} (a su "
                        f"hora, solo deporte, duración y distancia); la app los pone sola al arrancar y cada 10 "
                        f"minutos, y los entrenos nuevos llegan en la vuelta siguiente a cada sincronización con Garmin")
CALENDARIO_SIN_CONECTAR = "nada: Andrés no ha conectado Google Calendar (se conecta en Ajustes › Google Calendar)"


def _dia(fecha: str) -> str:
    """Bug 052: the weekday of an ISO date, so the model reads it instead of working it out (and getting it wrong)."""
    return _DIAS[date.fromisoformat(fecha).weekday()]

INSTRUCCIONES = """\
Formas parte del equipo de Andrés, un triatleta aficionado que apunta lo que come y su peso en la app TriData y entrena \
con un reloj Garmin: el entrenador, el fisio y el nutricionista. Le hablas desde su PC, con las herramientas de \
TriData.

Ves siempre todo su contexto, que va aquí debajo: sus datos médicos (analítica y prueba de esfuerzo, transcritos de \
sus PDF) y sus datos en JSON: "hoy" es la fecha de hoy y "dia_semana", qué día de la semana es; "calendario", las \
fechas de esta semana y de la siguiente con su día; cada fecha lleva su día de la semana al lado ("dia", "desde_dia", \
"hasta_dia"): léelo de ahí y no lo calcules nunca; "dias", las últimas 8 semanas día a día (comidas con sus \
kcal, peso, pasos, pulso en reposo, sueño, HRV, entrenos con su id y carga; "suplementacion", sus cafés y su B12 con \
la hora, los mg de cafeína, el total del día, la última toma y la leche de los cafés repartida, y sus geles con \
cafeína y pastillas de sal (por hora, o en "en_carrera" por minuto de carrera, con su cafeína y su sodio; con geles, \
la cafeína del día por origen, "cafeina_cafe_mg" y "cafeina_geles_mg", y "sodio_mg"); "horas_comidas", la \
hora de cada comida del día, con «~» si es la típica y no la dijo). Solo los últimos 14 días (hoy y \
los 13 anteriores) llevan sus alimentos ("comidas"), sus "horas_comidas" y su "suplementacion" entera; los días \
anteriores de esas 8 semanas llevan todo lo demás (sus kcal, macros, peso, sueño, entrenos…) pero sin sus alimentos ni \
sus horas de comida, y de su suplementación solo la cafeína del día. Si necesitas saber qué comió un día de esos, \
pídelo por fechas con la herramienta get_comidas (desde y hasta, como mucho 31 días cada vez): te los da con el mismo \
detalle. "semanas", lo anterior resumido por \
semanas; "ajustes", sus objetivos de hoy; cada día de "dias" lleva su "objetivo": su % y de dónde sale («de \
Ajustes», «mantenimiento por la carrera …» o «cambiado a mano»); "objetivos_no_de_ajustes", de hoy a 60 días, los días \
cuyo objetivo no sale de Ajustes (mantenimiento por una carrera o cambiado a mano) con su %; "carreras", sus carreras; "pulso", su frecuencia cardiaca máxima, de reposo \
y sus zonas, y de dónde salen; "plan", su plan de entrenamiento en curso (sus carreras con su objetivo, su resultado —"a mano" si lo escribió él, \
"garmin" si sale de su actividad de ese día— y la estrategia por tramos que guardó, tal cual; sus semanas \
con lo previsto en segundos y sus km previstos si los tiene, su fase si la tiene y lo hecho según Garmin, sus fases con las semanas que abarcan, sus sesiones por día con su id, sus bloques y su hora de empezar (HH:MM) si la tiene, y la nutrición en carrera); los demás planes guardados, pasados o futuros, \
los lees enteros con la herramienta get_plan; \
"incidencias", sus molestias y lesiones apuntadas en el plan: las abiertas (estado abierta) siguen ahí, tenlas en \
cuenta antes de proponerle cualquier sesión; las cerradas de su plan en curso, con su lección; cada una con sus \
visitas al fisio o al médico (fecha, quién y qué le dijo); las que no dicen estado vienen de su .md; \
"entrenos_previstos", los entrenos previstos que tiene apuntados (con «entreno previsto») de hoy a 14 días, \
cada día con su nombre, sus minutos y sus kcal; "productos_suplemento", sus geles y pastillas de sal guardados con su \
cafeína y su sodio por unidad (para apuntar uno con apuntar_toma basta su nombre y cuántos); los días después de hoy \
con geles o sal (una carrera o una tirada que aún no ha llegado) los llevan en "planeado_despues_de_hoy", en su \
"suplementacion"; "macros", sus proteínas, hidratos y grasa: "hoy" con lo que \
lleva comido, su objetivo según su último peso y sus \
horas de entreno (las hechas o las del plan, las que sean más) y "texto", lo mismo en una línea; "ayer" con su \
veredicto por macro; y "semanas", sus últimas semanas de lunes a domingo con la media al día y los días en rango; un \
día sin veredicto es «aproximado» (poco de lo comido lleva macros) o «sin macros», y cada día de "dias" con macros \
lleva los suyos ("macros", sus gramos comidos y "pct_kcal", qué parte de sus kcal los lleva); \
"alimentos_sin_macros", cuántos alimentos de los últimos catorce días (comidos o planeados) no llevan macros: si \
está, ofrécele que el nutricionista se los estime con preparar_macros, que los calcula solos si salen de algo \
guardado; \
"zapatillas", sus zapatillas de correr en activo con sus km, su tope, lo que les queda ("quedan"), su "estado" \
(«pronto» si le quedan cincuenta km o menos, «pasada» si llegó al tope) y de dónde salen los km ("fuente": strava, \
leídos de Strava una vez al día, andres, dichos por él, o aqui, contados aquí con sus carreras de Garmin desde esa \
fecha) con su fecha y su "uso" (suave, calidad o competicion: con ese uso se le suman las carreras según la sesión del \
plan de ese día; trail: se le suman las carreras que Garmin guardó como trail, diga lo que diga el plan); si te dice cuántos km lleva una, prepáralo con preparar_zapatilla y, solo con su «sí», guárdalo con \
apuntar_zapatilla; con esas mismas, accion alta da de alta una zapatilla nueva y accion carrera dice con qué \
zapatilla corrió una carrera; \
"carreras_sin_zapatilla", sus carreras de Garmin que no se sabe con qué zapatilla corrió (no suman a ninguna hasta \
que lo diga): pregúntaselo y, con su «sí», apúntalo con accion carrera; \
"google_calendar", si su Google Calendar está conectado y qué lleva su calendario «TriData». Tú no puedes poner ni \
quitar nada allí: lo pone la app sola. Si te pregunta si algo está en el calendario, contesta con lo que dice \
"google_calendar" y nunca que «debería aparecer»; si algo falta, puede tocar «Sincronizar ahora» en Ajustes › \
Google Calendar.

Cómo responder:
- En español, de tú, breve y concreto, con sus números reales. En texto plano: sin Markdown (nada de asteriscos, \
almohadillas ni tablas); listas cortas con guiones si hacen falta.
- Usa solo sus datos. Si falta un dato, dilo; no inventes valores, y menos los médicos.
- "tss" es el TRIMP de Banister por pulso (con su FC máxima y de reposo), no TSS por potencia. ctl (forma), atl \
(fatiga) y tsb (frescura) son medias exponenciales de 42 y 7 días.
- No eres médico: ante un valor médico fuera de rango o un síntoma, explícalo con prudencia y recomienda consultarlo \
con su médico.
- Cambios: si Andrés pide cambiar algo de sus datos, o le propones un cambio concreto (apuntar o quitar algo, el \
objetivo o los pasos de un día, un ajuste, una carrera, el peso de un día, una receta o un ingrediente, una incidencia \
o una nota de cómo fue un entreno), prepáralo con la herramienta preparar_cambio y una acción de la lista de abajo, \
un cambio por llamada: dice qué se haría, sin hacerlo. Enséñaselo tal cual y pregúntale; solo con su «sí», hazlo \
con hacer_cambio (o, para una comida, un café o su peso, con apuntar_comida, apuntar_toma o apuntar_peso). Di que \
está hecho solo cuando la herramienta conteste «Hecho». Lo que no esté en la lista, dile dónde se hace.
"""


@dataclass(frozen=True)
class Contexto:
    """The fixed part (`bloques`), what it sees (`ve`, for the line of R5) and why the medical part is missing."""

    bloques: tuple[str, ...]
    ve: tuple[str, ...]
    aviso: str | None = None
    plan: str | None = None  # unit 041: the name of the plan in course, for the line
    incidencias: int = 0


def _limpio(d: dict[str, Any]) -> dict[str, Any]:
    """Without the empty keys: what is not there is not sent (fewer tokens, nothing invented)."""
    return {k: v for k, v in d.items() if v not in (None, [], {})}


def _r(valor: float | None, decimales: int = 1) -> float | int | None:
    if valor is None:
        return None
    r = round(float(valor), decimales)
    return int(r) if decimales == 0 or r.is_integer() else r


def _comidas(registro: RegistroComidas) -> dict[str, list[dict[str, Any]]]:
    """Every logged food, by day, in the order it was logged; unit 112: a dessert says so (`postre: true`)."""
    conn = sqlite3.connect(registro.db_path)
    conn.row_factory = sqlite3.Row
    try:
        columnas = {f[1] for f in conn.execute("PRAGMA table_info(alimento_apuntado)")}
        postre = "postre" if "postre" in columnas else "0 AS postre"
        filas = conn.execute(f"SELECT dia, nombre, cantidad, unidad, kcal, estado, {postre} "  # noqa: S608
                             "FROM alimento_apuntado ORDER BY dia, id").fetchall()
    finally:
        conn.close()
    por_dia: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in filas:
        por_dia[f["dia"]].append(_limpio({"nombre": f["nombre"], "cantidad": _r(f["cantidad"]), "unidad": f["unidad"],
                                          "kcal": _r(f["kcal"], 0), "estado": f["estado"],
                                          "postre": True if f["postre"] else None}))
    return por_dia


def _variantes_de_recetas(registro: RegistroComidas) -> list[dict[str, Any]]:
    """Unit 112 (R-344): the variants of each saved recipe (name, kcal/100 g and macros per 100 g if they have them),
    by recipe; read only, and nothing if the table does not exist yet."""
    conn = sqlite3.connect(registro.db_path)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'receta_variante'").fetchone() \
                is None:
            return []
        filas = conn.execute("SELECT r.id AS receta_id, r.nombre AS receta, v.nombre, v.kcal_100g, v.proteina, "
                             "v.hidratos, v.grasa FROM receta_variante v JOIN receta r ON r.id = v.receta_id "
                             "ORDER BY r.nombre COLLATE NOCASE, r.id, v.nombre COLLATE NOCASE, v.id").fetchall()
    finally:
        conn.close()
    por_receta: dict[int, dict[str, Any]] = {}
    for f in filas:
        receta = por_receta.setdefault(f["receta_id"], {"receta": f["receta"], "variantes": []})
        macros = None if any(f[k] is None for k in ("proteina", "hidratos", "grasa")) else {
            "proteina_g": _r(f["proteina"]), "hidratos_g": _r(f["hidratos"]), "grasa_g": _r(f["grasa"])}
        receta["variantes"].append(_limpio({"nombre": f["nombre"], "kcal_100g": _r(f["kcal_100g"]),
                                            "macros_100g": macros}))
    return list(por_receta.values())


def _macros(registro: RegistroComidas) -> dict[str, dict[str, Any]]:
    """Unit 099 (R-279): by day, the grams of each macro eaten with its three macros and the share of the kcal eaten
    they cover; only the days where some eaten food carries them."""
    conn = sqlite3.connect(registro.db_path)
    conn.row_factory = sqlite3.Row
    try:
        filas = conn.execute(f"SELECT dia, kcal, {', '.join(MACROS)} FROM alimento_apuntado "  # noqa: S608
                             "WHERE estado = 'comido' ORDER BY dia, id").fetchall()
    finally:
        conn.close()
    total: dict[str, float] = defaultdict(float)
    con: dict[str, dict[str, float]] = {}
    for f in filas:
        total[f["dia"]] += f["kcal"] or 0
        if f["kcal"] is not None and all(f[k] is not None for k in MACROS):
            dia = con.setdefault(f["dia"], {"kcal": 0.0, **dict.fromkeys(MACROS, 0.0)})
            dia["kcal"] += f["kcal"]
            for k in MACROS:
                dia[k] += f[k]
    return {d: {**{k: _r(v[k]) for k in MACROS},
                "pct_kcal": _r(v["kcal"] / total[d] * 100, 0) if total[d] > 0 else None}
            for d, v in con.items() if v["kcal"] > 0}


def _sin_macros(registro: RegistroComidas, hoy: date) -> str | None:
    """Unit 101 (R-285, C-361): «9 alimentos de los últimos 14 días sin macros» — the same foods `preparar_macros`
    lists (`registro.sin_macros_desde`, eaten and planned) — or None when none is missing."""
    conn = sqlite3.connect(registro.db_path)
    try:
        n = len(sin_macros_desde(conn, hoy - timedelta(days=DIAS_MACROS - 1)))
    finally:
        conn.close()
    if not n:
        return None
    return f"{n} {'alimento' if n == 1 else 'alimentos'} de los últimos {DIAS_MACROS} días sin macros"


def _secciones_con_comida(registro: RegistroComidas) -> dict[str, set[str]]:
    """Unit 082: the sections with some food, by day (for the hour of each meal, R-230)."""
    conn = sqlite3.connect(registro.db_path)
    try:
        filas = conn.execute("SELECT DISTINCT dia, seccion FROM alimento_apuntado WHERE seccion IS NOT NULL").fetchall()
    finally:
        conn.close()
    por_dia: dict[str, set[str]] = defaultdict(set)
    for dia, seccion in filas:
        por_dia[dia].add(seccion)
    return por_dia


def _toma_del_contexto(t: dict) -> dict[str, Any]:
    if t["que"] in ("gel", "sal"):  # unit 109 (R-322): its product, number, minute and sodium
        return _limpio({"hora": t["hora"], "minuto": t.get("minuto"), "que": t["texto"],
                        "tipo": "gel" if t["que"] == "gel" else "sal", "cantidad": _r(t.get("cantidad"), 1),
                        "mg": _r(t["mg"], 0), "sodio_mg": _r(t.get("sodio_mg"), 0)})
    return _limpio({"hora": t["hora"], "que": t["texto"], "mg": _r(t["mg"], 0), "mg_estimado": t["mg_estimado"]
                    if t["mg"] is not None else None, "leche_ml": _r(t["leche_ml"], 0), "solo": t["solo"] or None})


def _suplementacion(tomas: list[dict], leche: dict | None) -> dict[str, Any] | None:
    """Unit 082 (R-230): the doses of a day by hour with their mg and milk, the total, the last one and the milk.
    Unit 109 (R-322, C-410): the gels and salts by minute in `en_carrera`, the caffeine by origin and the sodium."""
    if not tomas and not leche:
        return None
    r = resumen_dia(tomas, leche)
    return _limpio({
        "tomas": [_toma_del_contexto(t) for t in r["tomas"]],
        "en_carrera": [_toma_del_contexto(t) for t in r.get("en_carrera", [])],
        "cafeina_mg": _r(r["cafeina_mg"], 0) or None, "ultima": r["ultima"],
        "cafeina_cafe_mg": _r(r.get("cafeina_cafe_mg"), 0), "cafeina_geles_mg": _r(r.get("cafeina_geles_mg"), 0),
        "sodio_mg": _r(r.get("sodio_mg"), 0),
        "leche": {"ml": _r(leche["ml"], 0), "kcal": _r(leche["kcal"], 0), "repartida": r["leche"]["repartida"]}
        if leche and r["leche"] else None})


def _horas_comidas(puestas: dict[str, str], con_comida: set[str]) -> dict[str, str] | None:
    """Unit 082 (R-230): the hour of each section with food, in the order of the day; «~» when it is the typical."""
    horas = horas_de_secciones(puestas)
    salida = {s: ("~" if horas[s]["aproximada"] else "") + horas[s]["hora"] for s in SECCIONES
              if s in con_comida and horas[s]["hora"]}
    return salida or None


def _detalle(dia: str, comidas: dict[str, list[dict[str, Any]]], secciones: dict[str, set[str]],
             tomas_de: dict[str, list[dict]], horas_de: dict[str, dict[str, str]],
             leche_de: dict[str, dict]) -> dict[str, Any]:
    """Unit 100 (R-280, R-281): what only the recent days carry, and `get_comidas` gives of any day: its foods, the
    hours of its meals and its whole supplementation (the same for both, so they never drift apart)."""
    return {"comidas": comidas.get(dia, []),
            "suplementacion": _suplementacion(tomas_de.get(dia, []), leche_de.get(dia)),
            "horas_comidas": _horas_comidas(horas_de.get(dia, {}), secciones.get(dia, set()))}


def _sin_alimentos(fila: dict[str, Any]) -> dict[str, Any]:
    """Unit 100 (R-280, C-352): a day older than `DIAS_CON_ALIMENTOS` without its foods and the hours of its meals,
    and of its supplementation only the caffeine of the day (if it has any)."""
    resto = {k: v for k, v in fila.items() if k not in ("comidas", "horas_comidas", "suplementacion")}
    cafeina = (fila.get("suplementacion") or {}).get("cafeina_mg")
    if cafeina:
        resto["suplementacion"] = {"cafeina_mg": cafeina}
    return resto


def detalle_dias(registro: RegistroComidas, desde: date, hasta: date) -> list[dict[str, Any]]:
    """Unit 100 (R-281, C-353): each day from `desde` to `hasta` with its date, its weekday and the same detail the
    recent days carry in the context (`_detalle`); a day with nothing logged, only its date. Read only."""
    comidas = _comidas(registro)
    secciones = _secciones_con_comida(registro)
    tomas_de, horas_de, leche_de = leer_todo(registro.db_path)  # nothing created
    salida = []
    for i in range((hasta - desde).days + 1):
        dia = (desde + timedelta(days=i)).isoformat()
        salida.append(_limpio({"fecha": dia, "dia": _dia(dia),
                               **_detalle(dia, comidas, secciones, tomas_de, horas_de, leche_de)}))
    return salida


def _entreno(store: DataStore, a: dict[str, Any]) -> dict[str, Any]:
    segundos = float(a.get("duration_seconds") or 0)
    distancia = float(a.get("distance_meters") or 0)
    tipo = a.get("activity_type")
    ritmo = velocidad = None
    if distancia and segundos:
        if tipo in RUNNING_TYPES:
            s = round(segundos / distancia * 1000)
            ritmo = f"{s // 60}:{s % 60:02d}"
        elif tipo in CYCLING_TYPES:
            velocidad = _r(distancia / segundos * 3.6)
        elif tipo in SWIMMING_TYPES:
            s = round(segundos / distancia * 100)
            ritmo = f"{s // 60}:{s % 60:02d}/100m"
    clima = store.get_weather(a["activity_id"]) or {}
    return _limpio({"id": a["activity_id"], "nombre": a.get("name") or _deporte(tipo), "deporte": _deporte(tipo),
                    "minutos": _r(segundos / 60, 0), "km": _r(distancia / 1000, 2) if distancia else None,
                    "fc_media": _r(a.get("avg_hr"), 0), "fc_max": _r(a.get("max_hr"), 0),
                    "kcal": _r(a.get("calories"), 0), "ritmo_km": ritmo if tipo in RUNNING_TYPES else None,
                    "ritmo_100m": ritmo if tipo in SWIMMING_TYPES else None, "kmh": velocidad,
                    "desnivel_m": _r(a.get("elevation_gain_m"), 0), "temp_c": _r(clima.get("temp_c"))})


def _media(valores: list[float]) -> float | None:
    return mean(valores) if valores else None


def plan_en_curso(planes: Planes | None, store: DataStore | None, hoy: date) -> tuple[dict | None, list[dict]]:
    """The plan of the current objective as the team sees it (unit 041, R2) and its incidents; (None, []) without one."""
    ident = planes.en_curso(hoy) if planes is not None else None
    plan = planes.leer(ident) if planes is not None and ident is not None else None
    if plan is None:
        return None, []
    return plan_del_equipo(planes, plan, store, hoy), _incidencias(planes, plan["id"]) if planes is not None else []


def plan_del_equipo(planes: Planes | None, plan: dict, store: DataStore | None, hoy: date) -> dict[str, Any]:
    """Any plan (`Planes.leer`) as the team sees the plan in course (unit 115, R-345: `get_plan` reads any of them the
    same way): races, weeks with planned (time and, unit 115, km) and done, phases, sessions by day with their id,
    nutrition, notes, comparison and notes of a day."""
    vista = semanas_del_plan.vista(plan, store, hoy)
    kms = {s["n"]: s["previsto_km"] for s in plan["semanas"] if s.get("previsto_km") is not None}
    datos = {
        "nombre": plan["nombre"],
        "carreras": [_limpio({**c, "resultado": _limpio(c["resultado"]) if c["resultado"] else None,
                              "anteriores": _ediciones_anteriores(store, c) if not c["resultado"] else None})
                     for c in vista["carreras"]],
        "semanas": [{**s, **({"previsto_km": _r(kms[s["n"]])} if s["n"] in kms else {}),
                     "desde_dia": _dia(s["desde"]), "hasta_dia": _dia(s["hasta"])} for s in vista["semanas"]],
        "fases": [{k: f[k] for k in ("nombre", "desde_n", "hasta_n")} for f in vista["fases"]],  # unit 067
        "sesiones": [{"id": ident, "fecha": x["fecha"], "dia": _dia(x["fecha"]), "titulo": x["titulo"],
                      "bloques": x["bloques"], "clave": x["clave"],
                      **({"hora": x["hora"]} if x.get("hora") else {})} for x, ident in sorted(_con_id(planes, plan), key=lambda x: x[0]["fecha"])],
        "nutricion": [{"producto": n["producto"], "pauta": n["pauta"]} for n in plan["nutricion"]],
        "notas": [n["texto"] for n in plan["notas"] if n["tipo"] == "nota"],
        "comparativa": [n["texto"] for n in plan["notas"] if n["tipo"] == "comparativa"],  # unit 114 (R5)
    }
    notas_del_dia = planes.notas_del_dia(plan["id"]) if planes is not None else {}
    datos["notas_del_dia"] = [{"fecha": dia, "texto": texto} for dia, textos in sorted(notas_del_dia.items())
                              for texto in textos]
    return _limpio(datos)


# unit 114 (R4): the earlier editions of a race, by a word of its own and the same days of the year
DIAS_EDICION = 30
_PALABRAS_GENERICAS = frozenset({
    "carrera", "carreras", "media", "medio", "maraton", "trail", "popular", "triatlon", "duatlon", "sprint",
    "olimpico", "olimpica", "half", "ironman", "cross", "nocturna", "solidaria", "internacional", "edicion", "vuelta",
    "prueba", "desde", "para", "hasta", "entre", "running", "race", "marathon"})
_PALABRA = re.compile(r"[a-z]+")


def _palabras_propias(nombre: str | None) -> set[str]:
    """The words of a name that say which race it is: 4 letters or more, no digits, not a generic word."""
    return {p for p in _PALABRA.findall(normalizar(nombre)) if len(p) >= 4 and p not in _PALABRAS_GENERICAS}


def _mismo_dia(dia: date, ano: int) -> date:
    """The same day of the year in `ano` (the 29th of February, the 28th)."""
    return dia.replace(year=ano, day=min(dia.day, 28)) if (dia.month, dia.day) == (2, 29) else dia.replace(year=ano)


def _hms(segundos: float) -> str:
    s = round(segundos)
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _ediciones_anteriores(store: DataStore | None, carrera: dict) -> list[dict[str, Any]]:
    """The activities of earlier years that are this race (R4): a word of its own in common with its name and a date
    within `DIAS_EDICION` days of its day of the year; the newest first. Without Garmin's history, none."""
    propias = _palabras_propias(carrera.get("nombre"))
    if store is None or not propias or not carrera.get("fecha"):
        return []
    dia = date.fromisoformat(str(carrera["fecha"]))
    primera = store.primera_fecha_actividad()
    if primera is None:
        return []
    salida = []
    for ano in range(dia.year - 1, primera.year - 1, -1):
        centro = _mismo_dia(dia, ano)
        for a in reversed(store.activities_between(centro - timedelta(days=DIAS_EDICION),
                                                   centro + timedelta(days=DIAS_EDICION))):
            if not propias & _palabras_propias(a.get("name")):
                continue
            segundos, metros = float(a.get("duration_seconds") or 0), float(a.get("distance_meters") or 0)
            ritmo = None
            if segundos and metros and a.get("activity_type") in RUNNING_TYPES:
                s = round(segundos / metros * 1000)
                ritmo = f"{s // 60}:{s % 60:02d}"
            salida.append(_limpio({"id": a["activity_id"], "fecha": a.get("activity_date"), "nombre": a.get("name"),
                                   "km": _r(metros / 1000, 2) if metros else None,
                                   "tiempo": _hms(segundos) if segundos else None, "ritmo_km": ritmo}))
    return salida


def _con_id(planes: Planes | None, plan: dict) -> list[tuple[dict, int | None]]:
    """Each session of the plan with its id (unit 048, the one of `sesiones_reloj`, in the same order as `leer`), so
    the team can name one of two with the same title the same day."""
    ids = [s["id"] for s in planes.sesiones_reloj(plan["id"])] if planes is not None else []
    return [(s, ids[i] if i < len(ids) else None) for i, s in enumerate(plan["sesiones"])]


def _incidencias(planes: Planes, plan_id: int) -> list[dict]:
    """The open incidents of every plan and the closed ones of the plan in course, the open ones first (R-149)."""
    abiertas = [i for i in planes.abiertas() if i["plan_id"] != plan_id]
    del_plan = planes.incidencias(plan_id)  # its open ones first
    todas = [i for i in del_plan if i["estado"] == ABIERTA] + abiertas + [i for i in del_plan if i["estado"] != ABIERTA]
    return [_incidencia(i) for i in todas]


def incidencias_del_plan(planes: Planes, plan_id: int) -> list[dict]:
    """Unit 115 (R-345): the incidents of that plan only, the open ones first, as the context gives them."""
    return [_incidencia(i) for i in planes.incidencias(plan_id)]


def _incidencia(i: dict) -> dict:
    if i["importada"]:  # the ones of the .md, as before (unit 041)
        return {"fecha": i["desde"], "texto": i["texto"]}
    return _limpio({"fecha": i["desde"], "texto": i["texto"], "zona": i["zona"], "lado": i["lado"],
                    "estado": i["estado"], "visitas": i["visitas"], "leccion": i["leccion"], "cerrada": i["cerrada"]})


DIAS_PREVISTOS = 14  # unit 071: the planned trainings from today to two weeks ahead
DIAS_OBJETIVOS = 60  # unit 111 (R-336): the days ahead whose objective does not come from Ajustes


def _objetivo(objetivos: ObjetivosDia, ajustes: Ajustes, dia: date) -> dict[str, Any]:
    """Unit 111 (R-336): the % of the day and where it comes from, with the rule of the screen of the day."""
    propio, plan = objetivos.del_dia(dia), plan_del_dia(ajustes.vigentes(dia))
    pct = objetivo_del_dia(propio, plan)["porcentaje"]
    return {"pct": int(pct) if float(pct).is_integer() else pct, "origen": origen_del_objetivo(propio, plan)}


def _objetivos_no_de_ajustes(objetivos: ObjetivosDia, ajustes: Ajustes, hoy: date) -> list[dict[str, Any]]:
    """From `hoy` to `DIAS_OBJETIVOS` ahead, the days whose objective is maintenance for a race or changed by hand."""
    salida = []
    for i in range(DIAS_OBJETIVOS + 1):
        dia = hoy + timedelta(days=i)
        objetivo = _objetivo(objetivos, ajustes, dia)
        if objetivo["origen"] not in ("de Ajustes", "sin plan en Ajustes"):
            salida.append({"fecha": dia.isoformat(), "dia": _dia(dia.isoformat()), **objetivo})
    return salida


def _entrenos_previstos(objetivos: ObjetivosDia, hoy: date) -> list[dict[str, Any]]:
    """Unit 071: the planned trainings of each day from `hoy` to `hoy + DIAS_PREVISTOS`, only the days with any."""
    salida = []
    for i in range(DIAS_PREVISTOS + 1):
        dia = hoy + timedelta(days=i)
        previstos = (objetivos.del_dia(dia) or {}).get("previstos") or []
        if previstos:
            salida.append({"fecha": dia.isoformat(), "dia": _dia(dia.isoformat()),
                           "previstos": [{"nombre": p.get("nombre"), "minutos": p.get("minutos"), "kcal": _r(p.get("kcal"), 0)}
                                         for p in previstos]})
    return salida


def montar(*, registro: RegistroComidas, pesos: RegistroPeso, ajustes: Ajustes, store: DataStore | None,
           transcripciones: list[dict[str, Any]], aviso_medico: str | None, hoy: date,
           planes: Planes | None = None, calendario_conectado: bool = False) -> Contexto:
    """The fixed part of every request of every conversation, with the data up to `hoy` (R3); unit 041: with the
    plan in course and its incidents (`planes`); unit 054: whether Google Calendar is connected and what it carries."""
    desde = hoy - timedelta(days=DIAS_DIA_A_DIA - 1)
    comidas = _comidas(registro)
    macros_de = _macros(registro)  # unit 099
    secciones = _secciones_con_comida(registro)
    tomas_de, horas_de, leche_de = leer_todo(registro.db_path)  # unit 082: read only, nothing created
    peso_de = {p["dia"]: p for p in pesos.entre(DESDE_SIEMPRE, hoy)}
    historial = ajustes.historial_fc()
    objetivos = ObjetivosDia(registro.db_path)  # the same SQLite (unit 071, unit 111)

    stats: dict[str, dict] = {}
    noches: dict[str, dict] = {}
    hrv: dict[str, dict] = {}
    entrenos: dict[str, list[dict[str, Any]]] = defaultdict(list)
    carga: dict[str, dict[str, Any]] = {}
    if store is not None:
        datos = store.export_all()
        stats = {s["stat_date"]: s for s in datos["daily_stats"] if s.get("stat_date")}
        noches = {s["sleep_date"]: s for s in datos["sleep"] if s.get("sleep_date")}
        hrv = {h["hrv_date"]: h for h in datos["hrv"] if h.get("hrv_date")}
        for a in store.activities_between(DESDE_SIEMPRE, hoy):
            entrenos[a["activity_date"]].append(_entreno(store, a))
        serie = pmc_hasta(compute_pmc(daily_tss(store.get_activities_with_laps(ALL_SPORT_TYPES), historial.limites)),
                          hoy)
        carga = {p["date"].isoformat(): {"tss": _r(p["tss"]), "ctl": _r(p["ctl"]), "atl": _r(p["atl"]),
                                         "tsb": _r(p["tsb"])} for p in serie}

    def del_dia(dia: str) -> dict[str, Any]:
        s, n, h = stats.get(dia) or {}, noches.get(dia) or {}, hrv.get(dia) or {}
        comido = [c for c in comidas.get(dia, []) if c.get("estado") == "comido"]
        planeado = [c for c in comidas.get(dia, []) if c.get("estado") == "planeado"]
        sueno = n.get("total_sleep_seconds")
        return {
            **_detalle(dia, comidas, secciones, tomas_de, horas_de, leche_de),
            "kcal_comidas": _r(sum(c.get("kcal") or 0 for c in comido), 0) if comido else None,
            "kcal_planeadas": _r(sum(c.get("kcal") or 0 for c in planeado), 0) if planeado else None,
            "peso": _r(peso_de[dia]["kg"], 2) if dia in peso_de else None,
            "nota_peso": peso_de[dia].get("nota") if dia in peso_de else None,
            "pasos": s.get("steps"), "kcal_garmin": s.get("total_calories"), "pulso_reposo": s.get("resting_hr"),
            "sueno_h": _r(sueno / 3600, 2) if sueno else None, "sueno_puntos": n.get("sleep_score"),
            "hrv": _r(h.get("last_night_avg"), 0), "entrenos": entrenos.get(dia, []), "carga": carga.get(dia),
            "macros": macros_de.get(dia),  # unit 099 (R-279)
        }

    con_alimentos = (hoy - timedelta(days=DIAS_CON_ALIMENTOS - 1)).isoformat()  # unit 100 (R-280)
    dias = []
    for i in range(DIAS_DIA_A_DIA):
        dia = (desde + timedelta(days=i)).isoformat()
        fila = {"fecha": dia, "dia": _dia(dia), **del_dia(dia),
                "objetivo": _objetivo(objetivos, ajustes, date.fromisoformat(dia))}  # unit 111 (R-336)
        dias.append(_limpio(fila if dia >= con_alimentos else _sin_alimentos(fila)))

    # -- before the 8 weeks: by week (Monday), only sums and means ---------------------------------------------------
    antes = sorted({d for d in (*comidas, *peso_de, *stats, *noches, *hrv, *entrenos, *carga) if d < desde.isoformat()})
    semanas: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for dia in antes:
        d = date.fromisoformat(dia)
        semanas[(d - timedelta(days=d.weekday())).isoformat()].append({"fecha": dia, **del_dia(dia)})
    resumen_semanas = []
    for lunes, filas in sorted(semanas.items()):
        con_comida = [f["kcal_comidas"] for f in filas if f.get("kcal_comidas") is not None]
        hechos = [e for f in filas for e in f["entrenos"]]
        resumen_semanas.append(_limpio({
            "lunes": lunes, "dias_con_comida": len(con_comida) or None, "kcal_media": _r(_media(con_comida), 0),
            "peso_medio": _r(_media([f["peso"] for f in filas if f.get("peso")]), 2),
            "entrenos": len(hechos) or None, "minutos": _r(sum(e.get("minutos", 0) for e in hechos), 0) or None,
            "km": _r(sum(e.get("km", 0) for e in hechos), 1) or None,
            "pulso_reposo_medio": _r(_media([f["pulso_reposo"] for f in filas if f.get("pulso_reposo")])),
            "sueno_h_medio": _r(_media([f["sueno_h"] for f in filas if f.get("sueno_h")]), 2),
            "hrv_medio": _r(_media([f["hrv"] for f in filas if f.get("hrv")])),
            "tss": _r(sum((f["carga"] or {}).get("tss", 0) for f in filas if f.get("carga"))) or None,
            "ctl_al_acabar": next(((f["carga"] or {}).get("ctl") for f in reversed(filas) if f.get("carga")), None),
        }))

    # -- after today: only what is planned -----------------------------------------------------------------------------
    planeado = []
    for d_futuro in sorted({*comidas, *tomas_de}):
        if d_futuro > hoy.isoformat():
            futuro: dict[str, Any] = {"fecha": d_futuro, "comidas": comidas.get(d_futuro, [])}
            # unit 109 (C-410): the gels of a race to come
            suplementos = _suplementacion(tomas_de.get(d_futuro, []), None)
            if suplementos:
                futuro["suplementacion"] = suplementos
            planeado.append(futuro)

    v = ajustes.vigentes(hoy)
    pulso = historial.vigente(hoy)
    datos_json = {
        "hoy": hoy.isoformat(), "dia_semana": _DIAS[hoy.weekday()],
        "calendario": [{"fecha": d.isoformat(), "dia": _DIAS[d.weekday()]}
                       for d in (hoy - timedelta(days=hoy.weekday() - i) for i in range(14))],
        "ajustes": _limpio({"objetivo_pct": v.objetivo_pct, "motivo_objetivo": v.motivo_objetivo,
                            "basal_kcal": v.basal_kcal, "pasos_por_defecto": v.pasos_por_defecto,
                            "limite_saludable_pct": v.limite_saludable_pct, "edad": v.edad, "altura_cm": v.altura_cm,
                            "objetivo_peso": ({"kg": v.objetivo_peso.kg, "fecha": v.objetivo_peso.fecha.isoformat()}
                                              if v.objetivo_peso else None)}),
        "carreras": [{"nombre": c.nombre, "fecha": c.fecha.isoformat(), "dias_mantenimiento": c.dias_mantenimiento}
                     for c in ajustes.carreras()],
        "pulso": _limpio({"fc_max": pulso.fc_max if pulso else None, "fc_reposo": pulso.fc_reposo if pulso else None,
                          "zonas": [list(z) for z in pulso.zonas] if pulso and pulso.zonas else None,
                          "texto": texto_pulso(pulso)["texto"]}),
        "dias": dias,
        "semanas": resumen_semanas,
        "planeado_despues_de_hoy": planeado,
        "google_calendar": {"conectado": calendario_conectado,
                            "lleva": CALENDARIO_CONECTADO if calendario_conectado else CALENDARIO_SIN_CONECTAR},
    }
    macros = vista_macros(registro=registro, pesos=pesos, store=store, planes=planes, hoy=hoy)  # unit 099 (R-279)
    macros["hoy"] = {**macros["hoy"], "texto": texto_macros(macros["hoy"])}  # a copy: `vista` is built per call
    datos_json["macros"] = macros
    sin_macros = _sin_macros(registro, hoy)  # unit 101 (R-285)
    if sin_macros:
        datos_json["alimentos_sin_macros"] = sin_macros
    previstos = _entrenos_previstos(objetivos, hoy)  # unit 071: the same SQLite
    if previstos:
        datos_json["entrenos_previstos"] = previstos
    no_de_ajustes = _objetivos_no_de_ajustes(objetivos, ajustes, hoy)  # unit 111 (R-336)
    if no_de_ajustes:
        datos_json["objetivos_no_de_ajustes"] = no_de_ajustes
    variantes = _variantes_de_recetas(registro)  # unit 112 (R-344): read only, the same SQLite
    if variantes:
        datos_json["variantes_de_recetas"] = variantes
    productos = leer_productos(registro.db_path)  # unit 109 (R-322): read only, the same SQLite
    if productos:
        datos_json["productos_suplemento"] = productos
    zapatillas = zapatillas_sin_crear(registro.db_path, hoy)  # unit 102: read only, the same SQLite
    if zapatillas:
        datos_json["zapatillas"] = para_el_contexto(zapatillas)
    sin_zapatilla = pendientes_sin_crear(registro.db_path, hoy)  # unit 103 (S-100): read only too
    if sin_zapatilla:
        datos_json["carreras_sin_zapatilla"] = carreras_para_el_contexto(sin_zapatilla)
    plan, incidencias = plan_en_curso(planes, store, hoy)
    if plan is not None:
        datos_json["plan"] = plan
    if incidencias:
        datos_json["incidencias"] = incidencias

    ve = list(VE_SIEMPRE)
    if any(t["tipo"] == "analitica" for t in transcripciones):
        ve.append("analítica")
    if any(t["tipo"] == "prueba_esfuerzo" for t in transcripciones):
        ve.append("prueba de esfuerzo")
    if transcripciones:
        medico = ("Datos médicos de Andrés, transcritos en su PC de sus PDF (son suyos: úsalos cuando vengan al caso):\n"
                  + json.dumps([{"tipo": t["tipo"], "titulo": t["titulo"], "fecha": t["fecha"], "valores": t["valores"],
                                 "notas": t["notas"]} for t in transcripciones],
                               ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        medico = "Datos médicos de Andrés: no hay ninguno disponible ahora mismo."
    instrucciones = INSTRUCCIONES + "\n" + texto_acciones()
    return Contexto(
        bloques=(instrucciones, medico, json.dumps(datos_json, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        ve=tuple(ve), aviso=None if len(ve) == len(VE_SIEMPRE) + 2 else aviso_medico,
        plan=plan["nombre"] if plan else None, incidencias=len(incidencias))


__all__ = ["DIAS_CON_ALIMENTOS", "DIAS_DIA_A_DIA", "INSTRUCCIONES", "Contexto", "detalle_dias", "montar",
           "plan_en_curso"]
