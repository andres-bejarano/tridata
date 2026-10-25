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
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from ..nutricion.asistente import Accion, Contexto, NoValida, Peticion, TarjetaMal, fecha_de, no, tarjeta
from ..nutricion.recetas import normalizar
from . import texto_sesion
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
]

__all__ = ["ACCIONES", "dia_corto", "tarjeta_garmin"]
