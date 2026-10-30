"""The context every conversation of the Asistente sees (unit 032, R3, R5 · R-114, G-46, S-36, S-39): ALL of it,
always, the same in every conversation, built on this PC.

The fixed part of each request (`Contexto.bloques`, the `system` of `ia/conversacion.py`), in this order (P3):

1. the fixed instructions and the catalogue of the actions with a card (`nutricion.asistente.texto_acciones`, G-37);
2. the medical transcription (`conversaciones/medicos.py`): the blood test and the stress test, or why they are not;
3. the data, in JSON with sorted keys: the last 8 weeks day by day (foods, weight, steps, resting HR, sleep, HRV,
   trainings and the load), the earlier weeks summed up by week (never the names of the foods), the settings in
   force today, the races and the HR of Ajustes with its origin (031, G-49).

Unit 041 (REC-23, R2 · G-46): the data also carry the plan of the current objective (`plan.Planes.en_curso`: its
races, its weeks with what was planned and what was done — `plan/semanas.py` —, its sessions with their blocks and
the race nutrition) and its incidents. The fixed part is the same for the four roles of the team; what each role is
goes after the cache mark (`conversaciones/papeles.py`). The trainings carry their id, so a role can open their laps
(`abrir_actividad`): the laps never go here (S-54).

Unit 044 (REC-24, R-149 · G-46): "incidencias" carries the open incidents of every plan, always, and the closed ones of
the plan in course, each one with its state, its visits to the physio or the doctor and its lesson (the ones of the
.md, as before: date and text); and the plan carries the notes of each day (`notas_del_dia`).

Unit 048: each session of the plan carries its id (`Planes.sesiones_reloj`), so a role can name one of two with the
same title the same day in the cards that change or remove it.

Unit 059 (R4): each race of the plan carries its result (by hand or from Garmin, `plan/semanas.py`) and the strategy
by stretches it has saved, so the team knows it without Andrés pasting it.

Unit 045 (REC-25, R-154): a route attached to a conversation is NOT here: its profile goes in the message
(`web/conversaciones.py`, `plan.rutas.perfil_para_modelo`), so the fixed part, and its cache, stay the same; only the
line says «ruta adjunta: …».

None of it carries the hour: with the same data it is the same text byte to byte, so every conversation shares the
cache (R10). It changes only when a datum changes (a food, a weight, a sync) or the day does. The line «Ve tus …,
hasta hoy 9:42» (R-115) goes apart (`linea`). Read only: nothing here writes.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any

from ..calendario import DESDE_HECHOS
from ..fitness import texto_pulso
from ..hoy import _deporte
from ..metrics import compute_pmc, daily_tss, pmc_hasta
from ..nutricion.ajustes import DESDE_SIEMPRE, Ajustes
from ..nutricion.asistente import texto_acciones
from ..nutricion.peso import RegistroPeso
from ..nutricion.registro import RegistroComidas
from ..ia.modelos import nombre_de
from ..plan import ABIERTA, Planes
from ..plan import semanas as semanas_del_plan
from ..storage import DataStore
from ..sync import ALL_SPORT_TYPES, CYCLING_TYPES, RUNNING_TYPES, SWIMMING_TYPES

DIAS_DIA_A_DIA = 56  # the last 8 weeks day by day; the earlier ones by week (S-39)
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
con un reloj Garmin: el entrenador, el fisio, el nutricionista y el asistente de siempre. Quién eres en esta \
conversación y cómo miras lo dice tu papel, al final. Andrés habla con uno y, si quiere, pide opinión a otro.

Ves siempre todo su contexto, que va aquí debajo: sus datos médicos (analítica y prueba de esfuerzo, transcritos de \
sus PDF) y sus datos en JSON: "hoy" es la fecha de hoy y "dia_semana", qué día de la semana es; "calendario", las \
fechas de esta semana y de la siguiente con su día; cada fecha lleva su día de la semana al lado ("dia", "desde_dia", \
"hasta_dia"): léelo de ahí y no lo calcules nunca; "dias", las últimas 8 semanas día a día (comidas con sus \
kcal, peso, pasos, pulso en reposo, sueño, HRV, entrenos con su id y carga); "semanas", lo anterior resumido por \
semanas; "ajustes", sus objetivos de hoy; "carreras", sus carreras; "pulso", su frecuencia cardiaca máxima, de reposo \
y sus zonas, y de dónde salen; "plan", su plan de entrenamiento en curso (sus carreras con su objetivo, su resultado —"a mano" si lo escribió él, \
"garmin" si sale de su actividad de ese día— y la estrategia por tramos que guardó, tal cual; sus semanas \
con lo previsto en segundos y lo hecho según Garmin, sus sesiones por día con su id y sus bloques y la nutrición en carrera); \
"incidencias", sus molestias y lesiones apuntadas en el plan: las abiertas (estado abierta) siguen ahí, tenlas en \
cuenta antes de proponerle cualquier sesión; las cerradas de su plan en curso, con su lección; cada una con sus \
visitas al fisio o al médico (fecha, quién y qué le dijo); las que no dicen estado vienen de su .md; \
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
o una nota de cómo fue un entreno), usa la \
herramienta proponer_cambio con una acción de la lista de abajo; un cambio por llamada. La app le enseña una tarjeta \
«Voy a …» que él confirma o descarta: nunca digas que ya está hecho. Lo que no esté en la lista, dile dónde se hace.
- Título: en el primer mensaje de una conversación, llama también a poner_titulo con un título corto del tema.
"""


@dataclass(frozen=True)
class Contexto:
    """The fixed part (`bloques`), what it sees (`ve`, for the line of R5) and why the medical part is missing."""

    bloques: tuple[str, ...]
    ve: tuple[str, ...]
    aviso: str | None = None
    plan: str | None = None  # unit 041: the name of the plan in course, for the line
    incidencias: int = 0


def _y(partes: list[str] | tuple[str, ...], ultima: str = "y") -> str:
    partes = list(partes)
    return partes[0] if len(partes) == 1 else f"{', '.join(partes[:-1])} {ultima} {partes[-1]}"


def linea(ctx: Contexto, ahora: datetime, modelo: str | None = None, ruta: str | None = None) -> str:
    """«Ve tus comidas, peso, entrenos, sueño, pulso, HRV, analítica y prueba de esfuerzo, hasta hoy 9:42» (R-115);
    unit 041: with «tu plan …, incidencias (1)» after the HRV when there is a plan, and « · Sonnet», the model of the
    role, at the end; unit 045: « · ruta adjunta: Behobia» before the model when the conversation has a route."""
    ve = list(ctx.ve)
    if ctx.plan:
        extra = [f"tu plan {ctx.plan}"] + ([f"incidencias ({ctx.incidencias})"] if ctx.incidencias else [])
        ve[len(VE_SIEMPRE):len(VE_SIEMPRE)] = extra
    texto = f"Ve tus {_y(ve)}, hasta hoy {ahora.hour}:{ahora.minute:02d}"
    faltan = [m for m in ("analítica", "prueba de esfuerzo") if m not in ctx.ve]
    if faltan:
        texto += f" (sin {_y(faltan, 'ni')}" + (f": {ctx.aviso[0].lower()}{ctx.aviso[1:]}" if ctx.aviso else "") + ")"
    return texto + (f" · ruta adjunta: {ruta}" if ruta else "") + (f" · {nombre_de(modelo)}" if modelo else "")


def _limpio(d: dict[str, Any]) -> dict[str, Any]:
    """Without the empty keys: what is not there is not sent (fewer tokens, nothing invented)."""
    return {k: v for k, v in d.items() if v not in (None, [], {})}


def _r(valor: float | None, decimales: int = 1) -> float | int | None:
    if valor is None:
        return None
    r = round(float(valor), decimales)
    return int(r) if decimales == 0 or r.is_integer() else r


def _comidas(registro: RegistroComidas) -> dict[str, list[dict[str, Any]]]:
    """Every logged food, by day, in the order it was logged."""
    conn = sqlite3.connect(registro.db_path)
    conn.row_factory = sqlite3.Row
    try:
        filas = conn.execute("SELECT dia, nombre, cantidad, unidad, kcal, estado FROM alimento_apuntado "
                             "ORDER BY dia, id").fetchall()
    finally:
        conn.close()
    por_dia: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in filas:
        por_dia[f["dia"]].append(_limpio({"nombre": f["nombre"], "cantidad": _r(f["cantidad"]), "unidad": f["unidad"],
                                          "kcal": _r(f["kcal"], 0), "estado": f["estado"]}))
    return por_dia


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
    vista = semanas_del_plan.vista(plan, store, hoy)
    datos = {
        "nombre": plan["nombre"],
        "carreras": [_limpio({**c, "resultado": _limpio(c["resultado"]) if c["resultado"] else None})
                     for c in vista["carreras"]],
        "semanas": [{**s, "desde_dia": _dia(s["desde"]), "hasta_dia": _dia(s["hasta"])} for s in vista["semanas"]],
        "sesiones": [{"id": ident, "fecha": x["fecha"], "dia": _dia(x["fecha"]), "titulo": x["titulo"],
                      "bloques": x["bloques"],
                      "clave": x["clave"]} for x, ident in sorted(_con_id(planes, plan), key=lambda x: x[0]["fecha"])],
        "nutricion": [{"producto": n["producto"], "pauta": n["pauta"]} for n in plan["nutricion"]],
        "notas": [n["texto"] for n in plan["notas"] if n["tipo"] == "nota"],
    }
    notas_del_dia = planes.notas_del_dia(plan["id"]) if planes is not None else {}
    datos["notas_del_dia"] = [{"fecha": dia, "texto": texto} for dia, textos in sorted(notas_del_dia.items())
                              for texto in textos]
    return _limpio(datos), _incidencias(planes, plan["id"]) if planes is not None else []


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
    salida = []
    for i in todas:
        if i["importada"]:  # the ones of the .md, as before (unit 041)
            salida.append({"fecha": i["desde"], "texto": i["texto"]})
            continue
        salida.append(_limpio({"fecha": i["desde"], "texto": i["texto"], "zona": i["zona"], "lado": i["lado"],
                               "estado": i["estado"], "visitas": i["visitas"], "leccion": i["leccion"],
                               "cerrada": i["cerrada"]}))
    return salida


def montar(*, registro: RegistroComidas, pesos: RegistroPeso, ajustes: Ajustes, store: DataStore | None,
           transcripciones: list[dict[str, Any]], aviso_medico: str | None, hoy: date,
           planes: Planes | None = None, calendario_conectado: bool = False) -> Contexto:
    """The fixed part of every request of every conversation, with the data up to `hoy` (R3); unit 041: with the
    plan in course and its incidents (`planes`); unit 054: whether Google Calendar is connected and what it carries."""
    desde = hoy - timedelta(days=DIAS_DIA_A_DIA - 1)
    comidas = _comidas(registro)
    peso_de = {p["dia"]: p for p in pesos.entre(DESDE_SIEMPRE, hoy)}
    historial = ajustes.historial_fc()

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
            "comidas": comidas.get(dia, []),
            "kcal_comidas": _r(sum(c.get("kcal") or 0 for c in comido), 0) if comido else None,
            "kcal_planeadas": _r(sum(c.get("kcal") or 0 for c in planeado), 0) if planeado else None,
            "peso": _r(peso_de[dia]["kg"], 2) if dia in peso_de else None,
            "nota_peso": peso_de[dia].get("nota") if dia in peso_de else None,
            "pasos": s.get("steps"), "kcal_garmin": s.get("total_calories"), "pulso_reposo": s.get("resting_hr"),
            "sueno_h": _r(sueno / 3600, 2) if sueno else None, "sueno_puntos": n.get("sleep_score"),
            "hrv": _r(h.get("last_night_avg"), 0), "entrenos": entrenos.get(dia, []), "carga": carga.get(dia),
        }

    dias = []
    for i in range(DIAS_DIA_A_DIA):
        dia = (desde + timedelta(days=i)).isoformat()
        dias.append(_limpio({"fecha": dia, "dia": _dia(dia), **del_dia(dia)}))

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
    planeado = [{"fecha": d, "comidas": cs} for d, cs in sorted(comidas.items()) if d > hoy.isoformat()]

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


__all__ = ["DIAS_DIA_A_DIA", "INSTRUCCIONES", "Contexto", "linea", "montar", "plan_en_curso"]
