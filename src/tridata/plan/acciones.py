"""The cards that change the training plan (unit 041, R5 · R-140, C-182, G-55): a family of the catalogue of the 021
(`nutricion.asistente.catalogo`), proposed by any role of the team with `proponer_cambio`.

`plan_cambiar_sesion`, `plan_anadir_sesion` and `plan_quitar_sesion` (a planned session of one day, with its blocks),
`plan_objetivo_carrera` (the target of a race of the plan) and `plan_nota` (a note of the plan), always on the plan of
the current objective (`Planes.en_curso`). A card says «Voy a cambiar en el plan: mié 28/10 · Series 3×1000» with the
before and the after; nothing is written until Andrés confirms it. Confirming redoes it with what is in the plan NOW:
if the session was changed by hand in between, the card is redone and nothing is applied (G-41).

The blocks travel flat in `texto` (bug 036): «título | bloque | bloque…». A day that is over is not changed (it is
history: what was done comes from Garmin). Nothing here logs a text of the plan.

Unit 042 (REC-26, R-157..R-161): `mandar_a_garmin`, «Voy a mandar a tu Garmin: Series 4×12' (mié 28/10)» with its
blocks as the watch will follow them (`plan.reloj.lineas`, with the margin of pace) and its day, from a conversation
or from the session in Fitness › Plan (`tarjeta_garmin`). Proposing never talks to Garmin (G-55); confirming uploads
it and schedules it on its day, replacing the one sent before (R-160), and the plan marks it «en tu Garmin». If
Garmin fails, nothing is marked and the card says so and where to download the file (R-159).

Unit 044 (REC-24, R-145..R-148, R-150): the incidents. `abrir_incidencia` «Voy a abrir una incidencia: tobillo
derecho, lateral, desde el 24/9» (zone, side, since when, what happened), `anadir_visita` «Voy a añadir a la
incidencia: visita al fisio del 30/9 · alta para correr», `cerrar_incidencia` with its lesson and `nota_del_dia` «Voy
a apuntar en lo hecho del mié 21/10: …». The visit and the closing go to an OPEN incident (of any plan): named by its
zone, or the only one open. Confirming redoes it (G-41): an incident already open with that name, a visit added or an
incident closed in between, a note added that day, and the card is redone.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from ..nutricion.asistente import Accion, Contexto, NoValida, Peticion, TarjetaMal, campo, fecha_de, no, tarjeta
from ..nutricion.recetas import normalizar
from . import ABIERTA, texto_sesion, titulo_incidencia
from .reloj import GarminNoLlega, NoTraducible, lineas, mandar

SIN_PLAN = "No tienes ningún plan de entrenamiento: impórtalo en Fitness › Plan"
YA_PASO = "Ese día ya pasó: lo hecho lo trae Garmin"
NO_ESTA = "Esa sesión ya no está en el plan"
_DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
SEPARADOR = "|"


def dia_corto(dia: date) -> str:
    """«mié 28/10», as the cards of the plan say the day."""
    return f"{_DIAS[dia.weekday()]} {dia.day}/{dia.month}"


def _plan(ctx: Contexto, peticion: Peticion) -> dict | None:
    if ctx.planes is None:
        return None
    ident = ctx.planes.en_curso(peticion.fecha)
    return ctx.planes.leer(ident) if ident is not None else None


def _partes(texto: Any) -> tuple[str, list[str]] | None:
    """«Series 3×1000 | 20' a 5:30 | 3×1000 a 4:05» -> (title, blocks); None without a title."""
    if not isinstance(texto, str):
        return None
    trozos = [" ".join(t.split()) for t in texto.split(SEPARADOR)]
    titulo, bloques = trozos[0], [t for t in trozos[1:] if t]
    return (titulo, bloques) if titulo else None


_sesion_texto = texto_sesion


def _sesiones_del_dia(plan: dict, dia: date, objeto: Any) -> list[dict]:
    """The sessions of that day; with `objeto`, the ones whose title has it (without case or accents)."""
    del_dia = [s for s in plan["sesiones"] if s["fecha"] == dia.isoformat()]
    buscado = normalizar(objeto) if isinstance(objeto, str) else ""
    if not buscado:
        return del_dia
    return [s for s in del_dia if buscado in normalizar(s["titulo"])] or del_dia


def _la_sesion(cambio: Any, plan: dict, dia: date) -> dict | str:
    """The one session of that day, or why not."""
    sesiones = _sesiones_del_dia(plan, dia, cambio.objeto)
    if not sesiones:
        return f"El {dia_corto(dia)} no hay ninguna sesión en tu plan"
    if len(sesiones) > 1:
        return f"El {dia_corto(dia)} hay {len(sesiones)} sesiones en tu plan: dime cuál por su nombre"
    return sesiones[0]


# -- plan_cambiar_sesion, plan_anadir_sesion, plan_quitar_sesion ------------------------------------------------

def _proponer_sesion(que: str) -> Any:
    def proponer(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
        plan = _plan(ctx, peticion)
        if plan is None:
            return no(SIN_PLAN)
        if cambio.dia is None:
            return no("No sé qué día del plan es")
        nueva = _partes(cambio.texto) if que != "quitar" else None
        if que != "quitar" and nueva is None:
            return no("No sé qué sesión quieres poner")
        parametros: dict[str, Any] = {"plan_id": plan["id"], "fecha": cambio.dia.isoformat()}
        if nueva is not None:
            parametros.update(nuevo_titulo=nueva[0], bloques=nueva[1])
        if que != "anadir":
            sesion = _la_sesion(cambio, plan, cambio.dia)
            if isinstance(sesion, str):
                return no(sesion)
            parametros["titulo"] = sesion["titulo"]
        return _rehacer_sesion(que)(parametros, {}, ctx, peticion)
    return proponer


def _rehacer_sesion(que: str) -> Any:
    accion = {"cambiar": "plan_cambiar_sesion", "anadir": "plan_anadir_sesion", "quitar": "plan_quitar_sesion"}[que]

    def rehacer(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
        plan = ctx.planes.leer(parametros["plan_id"]) if ctx.planes is not None else None
        if plan is None:
            return no(SIN_PLAN)
        dia = fecha_de(parametros["fecha"])
        if dia < peticion.fecha:
            return no(YA_PASO)
        antes = despues = None
        if que != "anadir":
            sesion = next((s for s in plan["sesiones"] if s["fecha"] == dia.isoformat()
                           and s["titulo"] == parametros["titulo"]), None)
            if sesion is None:
                return no(NO_ESTA)
            antes = _sesion_texto(sesion["titulo"], sesion["bloques"])
        titulo = parametros.get("nuevo_titulo") or parametros.get("titulo") or ""
        bloques = list(parametros.get("bloques") or [])
        if que != "quitar":
            despues = _sesion_texto(titulo, bloques)
        texto = {"cambiar": f"Voy a cambiar en el plan: {dia_corto(dia)} · {titulo}",
                 "anadir": f"Voy a añadir en el plan: {dia_corto(dia)} · {titulo}",
                 "quitar": f"Voy a quitar del plan: {dia_corto(dia)} · {titulo}"}[que]
        return tarjeta(accion=accion, parametros=parametros, peticion=peticion, texto=texto, antes=antes,
                       despues=despues, dia=dia, donde=f"en tu plan {plan['nombre']}, en Fitness › Plan",
                       lineas=bloques if que != "quitar" else [])
    return rehacer


def _aplicar_sesion(que: str) -> Any:
    def aplicar(parametros: dict, ctx: Contexto) -> str:
        if ctx.planes is None:
            raise TarjetaMal("no hay planes")
        plan_id, fecha = parametros["plan_id"], parametros["fecha"]
        dia = dia_corto(fecha_de(fecha))
        with ctx.planes._connect() as conn:  # the same SQLite and connection of `Planes`
            if que == "anadir":
                apartado, orden = _sitio(conn, "plan_sesion", plan_id, fecha)
                conn.execute("INSERT INTO plan_sesion (plan_id, orden, apartado, fecha, titulo, bloques, clave) "
                             "VALUES (?, ?, ?, ?, ?, ?, 0)",
                             (plan_id, orden, apartado, fecha, parametros["nuevo_titulo"],
                              json.dumps(parametros["bloques"], ensure_ascii=False)))
                return f"el {dia} tienes en el plan «{parametros['nuevo_titulo']}»"
            fila = conn.execute("SELECT rowid FROM plan_sesion WHERE plan_id = ? AND fecha = ? AND titulo = ? "
                                "ORDER BY orden LIMIT 1", (plan_id, fecha, parametros["titulo"])).fetchone()
            if fila is None:
                raise TarjetaMal(NO_ESTA.lower())
            if que == "quitar":
                conn.execute("DELETE FROM plan_sesion WHERE rowid = ?", (fila[0],))
                return f"el {dia} ya no tienes «{parametros['titulo']}» en el plan"
            conn.execute("UPDATE plan_sesion SET titulo = ?, bloques = ? WHERE rowid = ?",
                         (parametros["nuevo_titulo"], json.dumps(parametros["bloques"], ensure_ascii=False), fila[0]))
            return f"el {dia} tienes en el plan «{parametros['nuevo_titulo']}»"
    return aplicar


def _sitio(conn: Any, tabla: str, plan_id: int, fecha: str | None = None) -> tuple[int, int]:
    """(section, order) of a new row: the section of the session closest before it (else the last section of that
    table, else the last one of the plan) and the next order."""
    apartado = None
    if fecha is not None:
        fila = conn.execute(f"SELECT apartado FROM {tabla} WHERE plan_id = ? AND fecha <= ? "  # noqa: S608
                            "ORDER BY fecha DESC, orden DESC LIMIT 1", (plan_id, fecha)).fetchone()
        apartado = fila[0] if fila else None
    if apartado is None:
        fila = conn.execute(f"SELECT MAX(apartado) FROM {tabla} WHERE plan_id = ?", (plan_id,)).fetchone()  # noqa: S608
        apartado = fila[0]
    if apartado is None:
        (apartados,) = conn.execute("SELECT apartados FROM plan WHERE id = ?", (plan_id,)).fetchone()
        apartado = max(len(json.loads(apartados)) - 1, 0)
    (orden,) = conn.execute(f"SELECT COALESCE(MAX(orden), -1) + 1 FROM {tabla} WHERE plan_id = ?",  # noqa: S608
                            (plan_id,)).fetchone()
    return int(apartado), int(orden)


# -- plan_objetivo_carrera ------------------------------------------------------------------------------------------

def _proponer_objetivo(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    plan = _plan(ctx, peticion)
    if plan is None:
        return no(SIN_PLAN)
    buscado = normalizar(cambio.objeto) if isinstance(cambio.objeto, str) else ""
    carreras = [c for c in plan["carreras"] if buscado and buscado in normalizar(c["nombre"])]
    if len(carreras) != 1:
        return no("Esa carrera no está en tu plan" if not carreras else "Hay varias carreras con ese nombre en tu plan")
    if not isinstance(cambio.texto, str) or not cambio.texto.strip():
        return no("No sé qué objetivo quieres para esa carrera")
    return _rehacer_objetivo({"plan_id": plan["id"], "carrera": carreras[0]["nombre"],
                              "objetivo": " ".join(cambio.texto.split())}, {}, ctx, peticion)


def _rehacer_objetivo(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    plan = ctx.planes.leer(parametros["plan_id"]) if ctx.planes is not None else None
    carrera = next((c for c in plan["carreras"] if c["nombre"] == parametros["carrera"]), None) if plan else None
    if plan is None or carrera is None:
        return no("Esa carrera ya no está en tu plan")
    if carrera["objetivo"] == parametros["objetivo"]:
        return no(f"El objetivo de {carrera['nombre']} ya es {carrera['objetivo']}")
    return tarjeta(accion="plan_objetivo_carrera", parametros=parametros, peticion=peticion,
                   texto=(f"Voy a cambiar en el plan el objetivo de {carrera['nombre']}: {carrera['objetivo']} → "
                          f"{parametros['objetivo']}"),
                   antes=carrera["objetivo"], despues=parametros["objetivo"],
                   donde=f"en tu plan {plan['nombre']}, en Fitness › Plan")


def _aplicar_objetivo(parametros: dict, ctx: Contexto) -> str:
    if ctx.planes is None:
        raise TarjetaMal("no hay planes")
    with ctx.planes._connect() as conn:
        conn.execute("UPDATE plan_carrera SET objetivo = ? WHERE plan_id = ? AND nombre = ?",
                     (parametros["objetivo"], parametros["plan_id"], parametros["carrera"]))
    return f"el objetivo de {parametros['carrera']} es {parametros['objetivo']}"


# -- plan_nota ----------------------------------------------------------------------------------------------------

def _proponer_nota(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    plan = _plan(ctx, peticion)
    if plan is None:
        return no(SIN_PLAN)
    if not isinstance(cambio.texto, str) or not cambio.texto.strip():
        return no("No sé qué nota quieres añadir")
    return _rehacer_nota({"plan_id": plan["id"], "texto": cambio.texto.strip()}, {}, ctx, peticion)


def _rehacer_nota(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    plan = ctx.planes.leer(parametros["plan_id"]) if ctx.planes is not None else None
    if plan is None:
        return no(SIN_PLAN)
    return tarjeta(accion="plan_nota", parametros=parametros, peticion=peticion,
                   texto=f"Voy a añadir en el plan una nota: {parametros['texto']}", despues=parametros["texto"],
                   donde=f"en tu plan {plan['nombre']}, en Fitness › Plan")


def _aplicar_nota(parametros: dict, ctx: Contexto) -> str:
    if ctx.planes is None:
        raise TarjetaMal("no hay planes")
    with ctx.planes._connect() as conn:
        apartado, orden = _sitio(conn, "plan_nota", parametros["plan_id"])
        conn.execute("INSERT INTO plan_nota (plan_id, orden, apartado, tipo, texto) VALUES (?, ?, ?, 'nota', ?)",
                     (parametros["plan_id"], orden, apartado, parametros["texto"]))
    return "la nota está en tu plan"


# -- mandar_a_garmin (unit 042) --------------------------------------------------------------------------------

def _proponer_garmin(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    plan = _plan(ctx, peticion)
    if plan is None:
        return no(SIN_PLAN)
    if cambio.dia is None:
        return no("No sé qué día del plan es")
    if cambio.dia < peticion.fecha:
        return no(YA_PASO)
    sesion = _la_sesion(cambio, plan, cambio.dia)
    if isinstance(sesion, str):
        return no(sesion)
    del_dia = [s for s in ctx.planes.sesiones_reloj(plan["id"])
               if s["fecha"] == cambio.dia.isoformat() and s["titulo"] == sesion["titulo"]]
    if not del_dia:
        return no(NO_ESTA)
    return _rehacer_garmin({"sesion": del_dia[0]["id"]}, {}, ctx, peticion)


def _rehacer_garmin(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    sesion = ctx.planes.sesion(int(parametros["sesion"])) if ctx.planes is not None else None
    if sesion is None:
        return no(NO_ESTA)
    dia = fecha_de(sesion["fecha"])
    if dia < peticion.fecha:
        return no(YA_PASO)
    try:
        bloques = lineas(sesion)
    except NoTraducible as exc:
        return no(f"No puedo mandarla a tu Garmin: {exc}")
    avisos = []
    if sesion["garmin_workout_id"] is not None:
        avisos.append("ya la mandaste a tu Garmin: esta la sustituye, allí queda una sola")
    return tarjeta(accion="mandar_a_garmin", parametros={"sesion": sesion["id"]}, peticion=peticion,
                   texto=f"Voy a mandar a tu Garmin: {sesion['titulo']} ({dia_corto(dia)})",
                   despues=texto_sesion(sesion["titulo"], sesion["bloques"]), dia=dia,
                   donde=f"a tu Garmin Connect, programada el {dia_corto(dia)}: el reloj la coge al sincronizar",
                   avisos=avisos, lineas=bloques)


def _aplicar_garmin(parametros: dict, ctx: Contexto) -> str:
    if ctx.planes is None:
        raise TarjetaMal("no hay planes")
    planes = ctx.planes
    sesion = planes.sesion(int(parametros["sesion"]))
    if sesion is None:
        raise TarjetaMal(NO_ESTA.lower())
    dia = dia_corto(fecha_de(sesion["fecha"]))
    try:
        workout_id, schedule_id = mandar(sesion, planes.garmin,
                                         al_quitar=lambda: planes.marcar_garmin(sesion["id"], None, None))
    except (GarminNoLlega, NoTraducible) as exc:
        raise NoValida(f"No ha llegado a tu Garmin: {exc}. Puedes bajarla como fichero e importarla a mano: "
                       f"Fitness › Plan, la sesión del {dia}, «Bajar fichero».") from None
    planes.marcar_garmin(sesion["id"], workout_id, schedule_id, datetime.now(),
                         texto_sesion(sesion["titulo"], sesion["bloques"]))
    return f"la sesión del {dia} está en tu Garmin: el reloj la coge al sincronizar"


def tarjeta_garmin(planes: Any, ident: int, hoy: date) -> dict:
    """The card of `mandar_a_garmin` for one session of Fitness › Plan (or a `no` saying why there is none)."""
    contexto = _SoloPlanes(planes)
    return _rehacer_garmin({"sesion": ident}, {}, contexto,  # type: ignore[arg-type]
                           Peticion(texto="Mandar a mi Garmin, desde Fitness › Plan", fecha=hoy))


class _SoloPlanes:
    """What `_rehacer_garmin` reads of a `Contexto`: only the plans."""

    def __init__(self, planes: Any) -> None:
        self.planes = planes


# -- unit 044: the incidents and the notes of a day ------------------------------------------------------------------

AUN_NO = "Ese día aún no ha llegado"
NO_ESTA_INCIDENCIA = "Esa incidencia ya no está en tu plan"
YA_CERRADA = "Esa incidencia ya está cerrada"
EN_INCIDENCIAS = "en Fitness › Plan › Incidencias"


def _texto(valor: Any) -> str | None:
    """The text said, with its spaces tidied; None if nothing."""
    return (" ".join(valor.split()) or None) if isinstance(valor, str) else None


def _dm(iso: str | date) -> str:
    dia = fecha_de(iso)
    return f"{dia.day}/{dia.month}"


def _el_dia(cambio: Any, peticion: Peticion) -> date:
    """The day it speaks of; none said, the day it was said."""
    return cambio.dia or cambio.fecha or peticion.fecha


def _la_abierta(ctx: Contexto, objeto: Any) -> dict | str:
    """The open incident named by its zone (or the only one open), or why not."""
    abiertas = ctx.planes.abiertas() if ctx.planes is not None else []
    if not abiertas:
        return "No tienes ninguna incidencia abierta: cuéntame la molestia y te propongo abrirla"
    nombre = _texto(objeto)
    if nombre:
        buscado = normalizar(nombre)
        abiertas = [i for i in abiertas if buscado in normalizar(i["titulo"])
                    or (i["zona"] and normalizar(i["zona"]) in buscado)]
        if not abiertas:
            return f"No tienes abierta ninguna incidencia de {nombre}"
    if len(abiertas) > 1:
        return f"Tienes {len(abiertas)} incidencias abiertas: dime de cuál hablas"
    return abiertas[0]


def _abierta_por_id(ctx: Contexto, ident: Any) -> dict | str:
    incidencia = ctx.planes.incidencia(int(ident)) if ctx.planes is not None else None
    if incidencia is None:
        return NO_ESTA_INCIDENCIA
    return incidencia if incidencia["estado"] == ABIERTA else YA_CERRADA


def _visita(v: dict) -> str:
    return f"{_dm(v['fecha'])} · {v['quien'][:1].upper()}{v['quien'][1:]}: {v['que_dijo']}"


# abrir_incidencia

def _proponer_abrir(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    plan = _plan(ctx, peticion)
    if plan is None:
        return no(SIN_PLAN)
    zona, que_paso = _texto(cambio.objeto), _texto(cambio.texto)
    if not zona:
        return no("No sé dónde lo notas: dime la zona (el tobillo, el gemelo…)")
    if not que_paso:
        return no("No sé qué pasó: cuéntame cuándo lo notas y cómo")
    desde = cambio.desde or cambio.dia or cambio.fecha or peticion.fecha
    return _rehacer_abrir({"plan_id": plan["id"], "zona": zona, "lado": _texto(cambio.campo),
                           "desde": desde.isoformat(), "que_paso": que_paso}, {}, ctx, peticion)


def _rehacer_abrir(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    corregidos = {k: _texto(v) for k, v in valores.items() if k in ("desde", "que_paso") and _texto(v)}
    parametros = {**parametros, **corregidos}
    plan = ctx.planes.leer(parametros["plan_id"]) if ctx.planes is not None else None
    if plan is None:
        return no(SIN_PLAN)
    desde = fecha_de(parametros["desde"])
    if desde > peticion.fecha:
        return no(f"{AUN_NO}: dime desde qué día lo notas")
    titulo = titulo_incidencia(parametros["zona"], parametros["lado"])
    if any(normalizar(i["titulo"]) == normalizar(titulo) for i in ctx.planes.abiertas()):
        return no(f"Ya tienes abierta una incidencia de {titulo}: cuéntame lo nuevo y lo añado")
    que_paso = parametros["que_paso"]
    return tarjeta(accion="abrir_incidencia", parametros=parametros, peticion=peticion,
                   texto=f"Voy a abrir una incidencia: {titulo}, desde el {_dm(desde)}",
                   despues=f"{titulo} · desde el {_dm(desde)} · {que_paso}", lineas=[f"Qué pasó: {que_paso}"],
                   donde=(f"en tu plan {plan['nombre']}, {EN_INCIDENCIAS}: mientras siga abierta, todo el equipo la "
                          "tiene en cuenta"),
                   campos=[campo("desde", "Desde", "fecha", desde.isoformat()),
                           campo("que_paso", "Qué pasó", "texto", que_paso)])


def _aplicar_abrir(parametros: dict, ctx: Contexto) -> str:
    if ctx.planes is None:
        raise TarjetaMal("no hay planes")
    ctx.planes.abrir_incidencia(parametros["plan_id"], parametros["zona"], parametros["lado"],
                                fecha_de(parametros["desde"]), parametros["que_paso"], datetime.now())
    titulo = titulo_incidencia(parametros["zona"], parametros["lado"])
    return f"la incidencia «{titulo}» está abierta en tu plan: todo el equipo la tiene en cuenta"


# anadir_visita

def _proponer_visita(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    incidencia = _la_abierta(ctx, cambio.objeto)
    if isinstance(incidencia, str):
        return no(incidencia)
    quien, que_dijo = _texto(cambio.campo), _texto(cambio.texto)
    if not quien:
        return no("No sé quién te vio: ¿el fisio o el médico?")
    if not que_dijo:
        return no("No sé qué te dijo: cuéntamelo y lo añado")
    return _rehacer_visita({"incidencia": incidencia["id"], "fecha": _el_dia(cambio, peticion).isoformat(),
                            "quien": quien.lower(), "que_dijo": que_dijo}, {}, ctx, peticion)


def _rehacer_visita(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    incidencia = _abierta_por_id(ctx, parametros["incidencia"])
    if isinstance(incidencia, str):
        return no(incidencia)
    if fecha_de(parametros["fecha"]) > peticion.fecha:
        return no(f"{AUN_NO}: dime el día de la visita")
    nueva = {k: parametros[k] for k in ("fecha", "quien", "que_dijo")}
    visitas = sorted([*incidencia["visitas"], nueva], key=lambda v: v["fecha"])
    return tarjeta(accion="anadir_visita", parametros=parametros, peticion=peticion,
                   texto=(f"Voy a añadir a la incidencia: visita al {nueva['quien']} del {_dm(nueva['fecha'])} · "
                          f"{nueva['que_dijo']}"),
                   antes="; ".join(_visita(v) for v in incidencia["visitas"]) or "sin visitas",
                   despues="; ".join(_visita(v) for v in visitas),
                   donde=f"en la incidencia «{incidencia['titulo']}» de tu plan, {EN_INCIDENCIAS}")


def _aplicar_visita(parametros: dict, ctx: Contexto) -> str:
    if ctx.planes is None:
        raise TarjetaMal("no hay planes")
    ctx.planes.anadir_visita(int(parametros["incidencia"]), fecha_de(parametros["fecha"]), parametros["quien"],
                             parametros["que_dijo"], datetime.now())
    return f"la visita al {parametros['quien']} del {_dm(parametros['fecha'])} está en la incidencia"


# cerrar_incidencia

def _proponer_cerrar(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    incidencia = _la_abierta(ctx, cambio.objeto)
    if isinstance(incidencia, str):
        return no(incidencia)
    leccion = _texto(cambio.texto)
    if not leccion:
        return no("No sé qué lección te llevas: dímela y la cierro con ella")
    return _rehacer_cerrar({"incidencia": incidencia["id"], "leccion": leccion}, {}, ctx, peticion)


def _rehacer_cerrar(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    incidencia = _abierta_por_id(ctx, parametros["incidencia"])
    if isinstance(incidencia, str):
        return no(incidencia)
    return tarjeta(accion="cerrar_incidencia", parametros=parametros, peticion=peticion,
                   texto=f"Voy a cerrar la incidencia: {incidencia['titulo']} · lección: {parametros['leccion']}",
                   antes=f"abierta desde el {_dm(incidencia['desde'])}",
                   despues=f"cerrada · lección: {parametros['leccion']}",
                   donde=f"en tu plan, {EN_INCIDENCIAS}: el equipo deja de tratarla como abierta")


def _aplicar_cerrar(parametros: dict, ctx: Contexto) -> str:
    if ctx.planes is None:
        raise TarjetaMal("no hay planes")
    ctx.planes.cerrar_incidencia(int(parametros["incidencia"]), parametros["leccion"], ctx.hoy())
    return "la incidencia está cerrada con su lección"


# nota_del_dia

def _proponer_nota_dia(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    plan = _plan(ctx, peticion)
    if plan is None:
        return no(SIN_PLAN)
    texto = _texto(cambio.texto)
    if not texto:
        return no("No sé qué nota quieres apuntar")
    return _rehacer_nota_dia({"plan_id": plan["id"], "fecha": _el_dia(cambio, peticion).isoformat(), "texto": texto},
                             {}, ctx, peticion)


def _rehacer_nota_dia(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    plan = ctx.planes.leer(parametros["plan_id"]) if ctx.planes is not None else None
    if plan is None:
        return no(SIN_PLAN)
    dia = fecha_de(parametros["fecha"])
    if dia > peticion.fecha:
        return no(f"{AUN_NO}: lo hecho se apunta el día que lo haces")
    ya = ctx.planes.notas_del_dia(plan["id"]).get(dia.isoformat(), [])
    return tarjeta(accion="nota_del_dia", parametros=parametros, peticion=peticion,
                   texto=f"Voy a apuntar en lo hecho del {dia_corto(dia)}: {parametros['texto']}",
                   antes=" · ".join(ya) or None, despues=" · ".join([*ya, parametros["texto"]]), dia=dia,
                   donde=f"en tu plan {plan['nombre']}, en Fitness › Plan, en la semana de ese día")


def _aplicar_nota_dia(parametros: dict, ctx: Contexto) -> str:
    if ctx.planes is None:
        raise TarjetaMal("no hay planes")
    dia = fecha_de(parametros["fecha"])
    ctx.planes.nota_del_dia(parametros["plan_id"], dia, parametros["texto"], datetime.now())
    return f"la nota está en lo hecho del {dia_corto(dia)}"


# no figure in a line for Claude (G-38): the example goes in words
_SESION = ("texto: «título | bloque | bloque…», cada bloque en números redondos con su tiempo o distancia, su ritmo o "
           "esfuerzo y su recuperación (p. ej. «Series | calentamiento suave | series a ritmo de diez mil con "
           "recuperación al trote | vuelta a la calma»)")

ACCIONES = [
    Accion("plan_cambiar_sesion", "cambiar una sesión prevista del plan en curso, de hoy o de un día que aún no ha "
           f"llegado. dia: el día de la sesión; objeto: su título si ese día hay varias, o null; {_SESION}.",
           _proponer_sesion("cambiar"), _rehacer_sesion("cambiar"), _aplicar_sesion("cambiar")),
    Accion("plan_anadir_sesion", "añadir una sesión al plan en curso, hoy o un día que aún no ha llegado. dia: el día; "
           f"{_SESION}.", _proponer_sesion("anadir"), _rehacer_sesion("anadir"), _aplicar_sesion("anadir")),
    Accion("plan_quitar_sesion", "quitar una sesión prevista del plan en curso. dia: el día de la sesión; objeto: su "
           "título si ese día hay varias, o null.",
           _proponer_sesion("quitar"), _rehacer_sesion("quitar"), _aplicar_sesion("quitar")),
    Accion("plan_objetivo_carrera", "cambiar el objetivo de una carrera del plan en curso (p. ej. un tiempo). objeto: "
           "el nombre de la carrera; texto: el objetivo nuevo.",
           _proponer_objetivo, _rehacer_objetivo, _aplicar_objetivo),
    Accion("plan_nota", "añadir una nota al plan en curso. texto: la nota.", _proponer_nota, _rehacer_nota,
           _aplicar_nota),
    Accion("mandar_a_garmin", "mandar a su reloj Garmin una sesión de carrera a pie del plan en curso, de hoy o de un "
           "día que aún no ha llegado, para que el reloj le guíe bloque a bloque (si la cambias, primero la cambias "
           "en el plan). dia: el día de la sesión; objeto: su título si ese día hay varias, o null.",
           _proponer_garmin, _rehacer_garmin, _aplicar_garmin),
    Accion("abrir_incidencia", "abrir una incidencia en el plan en curso cuando Andrés cuenta una molestia, un dolor o "
           "un problema físico (sobrecarga, rampas…); pregúntale antes lo que falte. objeto: la zona (p. ej. tobillo); "
           "campo: el lado y la parte (p. ej. derecho, lateral), o null; desde: desde qué día lo nota, o null si es de "
           "hoy; texto: qué pasó, en sus palabras.", _proponer_abrir, _rehacer_abrir, _aplicar_abrir),
    Accion("anadir_visita", "añadir a una incidencia abierta lo que le dijo el fisio o el médico en una visita. objeto: "
           "la zona de la incidencia, o null si solo hay una abierta; campo: quién (fisio, médico…); dia: el día de la "
           "visita, o null si es hoy; texto: qué le dijo.", _proponer_visita, _rehacer_visita, _aplicar_visita),
    Accion("cerrar_incidencia", "cerrar una incidencia abierta cuando Andrés dice que ya está bien, con la lección que "
           "se lleva. objeto: la zona de la incidencia, o null si solo hay una abierta; texto: la lección.",
           _proponer_cerrar, _rehacer_cerrar, _aplicar_cerrar),
    Accion("nota_del_dia", "apuntar cómo fue un entreno o un día (sensaciones, pulso alto para el ritmo, piernas "
           "cargadas) como nota de ese día en lo hecho del plan en curso; si es una molestia, abrir_incidencia. dia: "
           "el día, o null si es hoy; texto: la nota, en sus palabras.",
           _proponer_nota_dia, _rehacer_nota_dia, _aplicar_nota_dia),
]

__all__ = ["ACCIONES", "dia_corto", "tarjeta_garmin"]
