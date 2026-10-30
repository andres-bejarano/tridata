"""The JSON routes of Fitness › Plan (unit 040, REC-22 of `llevar-plan`), mounted by `crear_app`.

    GET  /api/plan                    {hoy, plan, planes}: the plan of the current objective (or the one of ?id=,
                                      404 if it is not there) as `plan.semanas.vista` paints it, or None without
                                      plans; `planes` every plan with `en_curso`
    GET  /api/plan/semana/N           one week of that plan (?id= too) with its seven days (404 without it)
    GET  /api/plan/actividad/ID       the detail of a done session: the one of Fitness (`fitness.detalle_actividad`)
                                      plus the cadence and the climb of the activity and of each lap (R4)
    POST /api/plan/importar           {texto, fichero}: the proposal of the .md (`plan.importar.proponer`) with a
                                      `token`; NOTHING is saved. 400 empty, 429 at the spending cap of the month,
                                      502 if the AI failed (it can be tried again)
    POST /api/plan/confirmar          {token, elecciones: {"4": "plan" | "garmin"}}: saves that proposal (once; 404
                                      if it is not there any more) and answers like GET /api/plan with it
    GET  /api/plan/N.md               the plan as a document (`plan.markdown.escribir`), to download
    POST /api/plan/sesion/ID/garmin   unit 042: {tarjeta} «Voy a mandar a tu Garmin» for that session (it is confirmed
                                      or discarded with /api/asistente/*, like every card: nothing goes to Garmin
                                      here, G-55); 422 with why if it cannot be sent (a block it cannot read, a day
                                      that is over), 404 without that session
    GET  /api/plan/sesion/ID.fit      unit 042: the session as a FIT workout file, made here (R-159); 422, 404 as above
    GET  /api/plan/incidencias        unit 044: {plan: {id, nombre} | None, incidencias}: the incidents of that plan
                                      (?id= too), the open ones first, with their visits and lesson (R-147)
    POST /api/plan/carrera            unit 059: {id, carrera, nombre?, fecha?, objetivo?, tiempo?}: changes those of
                                      that race (`Planes.editar_carrera`; its strategy stays with it, R3) and answers
                                      like GET /api/plan with that plan; 400 with why (a name of another race, a bad
                                      date), 404 without that plan or race
    POST /api/plan/nuevo              unit 063: {nombre, carrera: {nombre, fecha, objetivo}}: a new plan with no
                                      weeks and that main race (`Planes.nuevo`); answers like GET /api/plan with it;
                                      400 with why (an empty name, a bad date)
    POST /api/plan/ID/renombrar       unit 063: {nombre}: another name (400 empty, 404 without it)
    POST /api/plan/ID/borrar          unit 063: the whole plan; answers like GET /api/plan with the current one (404)
    POST /api/plan/carrera/anadir     unit 063: {id, nombre, fecha, objetivo}: a race at the end of that plan
    POST /api/plan/carrera/quitar     unit 063: {id, carrera}: that race out of the plan
    POST /api/plan/carrera/mover      unit 063: {id, carrera, destino}: that race to plan `destino`, with its target,
                                      result and strategy; answers with the plan of origin
    POST /api/plan/carrera/principal  unit 063: {id, carrera}: the main race of that plan (only one)
                                      The four answer like GET /api/plan with plan `id`; 400 with why (a repeated
                                      name, a bad date, the same plan), 404 without that plan, race or destination
    POST /api/plan/incidencia/ID/cerrar  unit 044: {leccion}: closes it by hand with its lesson (R-148) and answers
                                      like GET /api/plan/incidencias; 400 without a lesson, 404 without it, 409 closed

Unit 042: each day of GET /api/plan/semana/N also brings `reloj`, one per session of `sesiones` and in its order:
{id, estado: "al_dia" | "cambiada" | None, enviada}, whether it is in the watch as it is now. Unit 061: and its
`aviso` from the last pass with Garmin («Ya no está en tu Garmin», or that Garmin has another one that day and which
one it replaces is not known), or None.
Unit 059: each race of GET /api/plan brings its `resultado` ({tiempo, km, origen: "garmin" | "a mano"} or None)
and its `estrategia` (unit 045), both from `plan.semanas.vista`.
Unit 044: each day also brings its `notas` (how it went, R-150), and GET /api/plan its `abiertas`, how many incidents
are open («⚠️ Incidencias (1)»).

The proposals live only in memory until confirmed (the last few). The spending of every call to the AI goes to the
month's cap as «importar plan» (`Conversaciones.apuntar_gasto`). Nothing here logs a text of the plan (R9).
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from collections import OrderedDict
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..conversaciones import TOPE_DOLARES_MES, Conversaciones
from ..fitness import detalle_actividad
from ..ia.plan import ErrorPlan
from ..nutricion.ajustes import Ajustes
from ..plan import ABIERTA, Planes, semanas
from ..plan.acciones import tarjeta_garmin
from ..plan.importar import confirmar, proponer
from ..plan.markdown import escribir
from ..plan.reloj import NoTraducible, fit_local
from ..storage import DataStore

log = logging.getLogger(__name__)

PROPUESTAS_GUARDADAS = 5
NO_ESTA = "Ese plan ya no está."


def _error(mensaje: str, estado: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": mensaje, **extra}, status_code=estado)


async def _cuerpo(request: Request) -> dict | None:
    try:
        datos = await request.json()
    except (ValueError, json.JSONDecodeError):
        return None
    return datos if isinstance(datos, dict) else None


def _nombre_de_fichero(nombre: str) -> str:
    limpio = re.sub(r"[^a-z0-9]+", "-", nombre.lower()).strip("-")
    return f"{limpio or 'plan'}.md"


def rutas(planes: Planes, store: DataStore | None, ajustes: Ajustes, ordenador: Any,
          conversaciones: Conversaciones, hoy: Callable[[], date], ahora: Callable[[], datetime]) -> list[Route]:
    propuestas: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def _plan_pedido(request: Request) -> tuple[dict | None, bool]:
        """(the plan of ?id= or the current one, whether an ?id= was asked that is not there)."""
        pedido = request.query_params.get("id")
        if pedido is not None:
            plan = planes.leer(int(pedido)) if pedido.isdigit() else None
            return plan, plan is None
        ident = planes.en_curso(hoy())
        return (planes.leer(ident) if ident is not None else None), False

    def _vista(plan: dict | None) -> dict[str, Any]:
        dia = hoy()
        en_curso = planes.en_curso(dia)
        vista = semanas.vista(plan, store, dia) if plan is not None else None
        if plan is not None and vista is not None:
            vista["abiertas"] = len([i for i in planes.incidencias(plan["id"]) if i["estado"] == ABIERTA])
        return {"hoy": dia.isoformat(), "plan": vista,
                "planes": [{"id": p["id"], "nombre": p["nombre"], "en_curso": p["id"] == en_curso}
                           for p in planes.lista()]}

    async def ver(request: Request) -> Response:
        plan, falta = await run_in_threadpool(_plan_pedido, request)
        if falta:
            return _error(NO_ESTA, 404)
        return JSONResponse(await run_in_threadpool(_vista, plan))

    def _semana(plan: dict, n: int) -> dict[str, Any] | None:
        vista = semanas.semana(plan, n, store, hoy(), notas=planes.notas_del_dia(plan["id"]))
        if vista is None:
            return None
        reloj = planes.sesiones_reloj(plan["id"])  # the same order as plan["sesiones"]
        for dia in vista["dias"]:
            dia["reloj"] = [{k: s[k] for k in ("id", "estado", "enviada", "aviso")} for s in reloj
                           if s["fecha"] == dia["fecha"]]
        return vista

    async def ver_semana(request: Request) -> Response:
        plan, _ = await run_in_threadpool(_plan_pedido, request)
        vista = await run_in_threadpool(_semana, plan, request.path_params["n"]) if plan is not None else None
        if vista is None:
            return _error("Esa semana no está en el plan.", 404)
        return JSONResponse(vista)

    _CAMPOS = {"nombre": "nombre", "fecha": "fecha", "objetivo": "objetivo", "tiempo": "resultado"}

    async def editar_carrera(request: Request) -> Response:
        cuerpo = await _cuerpo(request)
        if cuerpo is None or not isinstance(cuerpo.get("carrera"), str):
            return _error("Lo enviado no se entiende: no se ha guardado nada.", 400)
        ident = cuerpo.get("id")
        if not isinstance(ident, int) or isinstance(ident, bool) or await run_in_threadpool(planes.leer, ident) is None:
            return _error(NO_ESTA, 404)
        cambios = {campo: cuerpo[clave] for clave, campo in _CAMPOS.items()
                   if clave in cuerpo and (cuerpo[clave] is None or isinstance(cuerpo[clave], str))}
        try:
            await run_in_threadpool(planes.editar_carrera, ident, cuerpo["carrera"], cambios)
        except LookupError:
            return _error("Esa carrera ya no está en el plan.", 404)
        except ValueError as exc:
            return _error(f"{exc}: no se ha guardado nada.", 400)
        return JSONResponse(await run_in_threadpool(lambda: _vista(planes.leer(ident))))

    # -- unit 063: plans by hand ------------------------------------------------------------------------------------

    async def _hacer(accion: Callable[[], Any], despues: Callable[[], dict | None], falta: str) -> Response:
        """Runs `accion` and answers like GET /api/plan with the plan `despues` gives; 400 with why, 404 `falta`."""
        try:
            await run_in_threadpool(accion)
        except LookupError:
            return _error(falta, 404)
        except ValueError as exc:
            return _error(f"{exc}: no se ha guardado nada.", 400)
        return JSONResponse(await run_in_threadpool(lambda: _vista(despues())))

    def _texto(cuerpo: dict, clave: str) -> str:
        valor = cuerpo.get(clave)
        return valor if isinstance(valor, str) else ""

    def _carrera_de(cuerpo: dict) -> dict[str, str]:
        return {c: _texto(cuerpo, c) for c in ("nombre", "fecha", "objetivo")}

    async def nuevo_plan(request: Request) -> Response:
        cuerpo = await _cuerpo(request)
        if cuerpo is None or not isinstance(cuerpo.get("carrera"), dict):
            return _error("Lo enviado no se entiende: no se ha guardado nada.", 400)
        try:
            ident = await run_in_threadpool(planes.nuevo, _texto(cuerpo, "nombre"), _carrera_de(cuerpo["carrera"]),
                                            ahora())
        except ValueError as exc:
            return _error(f"{exc}: no se ha guardado nada.", 400)
        return JSONResponse(await run_in_threadpool(lambda: _vista(planes.leer(ident))))

    async def renombrar(request: Request) -> Response:
        ident = request.path_params["id"]
        cuerpo = await _cuerpo(request) or {}
        return await _hacer(lambda: planes.renombrar(ident, _texto(cuerpo, "nombre")), lambda: planes.leer(ident),
                            NO_ESTA)

    async def borrar(request: Request) -> Response:
        ident = request.path_params["id"]

        def en_curso() -> dict | None:
            actual = planes.en_curso(hoy())
            return planes.leer(actual) if actual is not None else None

        return await _hacer(lambda: planes.borrar(ident), en_curso, NO_ESTA)

    async def _de_carrera(request: Request, accion: Callable[[dict, int], Any], con_carrera: bool = True) -> Response:
        cuerpo = await _cuerpo(request)
        if cuerpo is None or (con_carrera and not isinstance(cuerpo.get("carrera"), str)):
            return _error("Lo enviado no se entiende: no se ha guardado nada.", 400)
        ident = cuerpo.get("id")
        if not isinstance(ident, int) or isinstance(ident, bool) or await run_in_threadpool(planes.leer, ident) is None:
            return _error(NO_ESTA, 404)
        return await _hacer(lambda: accion(cuerpo, ident), lambda: planes.leer(ident),
                            "Esa carrera o ese plan ya no están.")

    async def anadir_carrera(request: Request) -> Response:
        return await _de_carrera(request, lambda c, i: planes.anadir_carrera(i, _carrera_de(c)), con_carrera=False)

    async def quitar_carrera(request: Request) -> Response:
        return await _de_carrera(request, lambda c, i: planes.quitar_carrera(i, c["carrera"]))

    def _mover(cuerpo: dict, ident: int) -> None:
        destino = cuerpo.get("destino")
        if not isinstance(destino, int) or isinstance(destino, bool):
            raise LookupError(destino)
        planes.mover_carrera(ident, cuerpo["carrera"], destino)

    async def mover_carrera(request: Request) -> Response:
        return await _de_carrera(request, _mover)

    async def hacer_principal(request: Request) -> Response:
        return await _de_carrera(request, lambda c, i: planes.hacer_principal(i, c["carrera"]))

    def _incidencias(plan: dict | None) -> dict[str, Any]:
        if plan is None:
            return {"plan": None, "incidencias": []}
        return {"plan": {"id": plan["id"], "nombre": plan["nombre"]}, "incidencias": planes.incidencias(plan["id"])}

    async def ver_incidencias(request: Request) -> Response:
        plan, falta = await run_in_threadpool(_plan_pedido, request)
        if falta:
            return _error(NO_ESTA, 404)
        return JSONResponse(await run_in_threadpool(_incidencias, plan))

    async def cerrar_incidencia(request: Request) -> Response:
        ident = request.path_params["id"]
        incidencia = await run_in_threadpool(planes.incidencia, ident)
        if incidencia is None:
            return _error("Esa incidencia ya no está en tu plan.", 404)
        if incidencia["estado"] != ABIERTA:
            return _error("Esa incidencia ya está cerrada.", 409)
        leccion = (await _cuerpo(request) or {}).get("leccion")
        leccion = " ".join(leccion.split()) if isinstance(leccion, str) else ""
        if not leccion:
            return _error("Escribe la lección que te llevas para cerrarla.", 400)
        await run_in_threadpool(planes.cerrar_incidencia, ident, leccion, hoy())
        return JSONResponse(await run_in_threadpool(lambda: _incidencias(planes.leer(incidencia["plan_id"]))))

    async def tarjeta_reloj(request: Request) -> Response:
        ident = request.path_params["id"]
        if await run_in_threadpool(planes.sesion, ident) is None:
            return _error("Esa sesión ya no está en el plan.", 404)
        resultado = await run_in_threadpool(tarjeta_garmin, planes, ident, hoy())
        if resultado["tipo"] != "propuesta":
            return _error(f"{resultado['texto']}.", 422)
        return JSONResponse({"tarjeta": resultado})

    async def bajar_fit(request: Request) -> Response:
        sesion = await run_in_threadpool(planes.sesion, request.path_params["id"])
        if sesion is None:
            return _error("Esa sesión ya no está en el plan.", 404)
        try:
            fichero = await run_in_threadpool(fit_local, sesion)
        except NoTraducible as exc:
            return _error(f"{exc}.", 422)
        nombre = re.sub(r"[^a-z0-9]+", "-", f"{sesion['fecha']} {sesion['titulo']}".lower()).strip("-")
        return Response(fichero, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{nombre or "sesion"}.fit"'})

    def _detalle(ident: str) -> dict | None:
        if store is None:
            return None
        detalle = detalle_actividad(store, ajustes.historial_fc(), ident)
        if detalle is None:
            return None
        actividad = store.get_activity(ident) or {}
        laps = store.get_laps(ident)
        detalle["actividad"] = {**detalle["actividad"], "cadencia": actividad.get("avg_cadence"),
                                "desnivel_m": actividad.get("elevation_gain_m")}
        detalle["vueltas"] = [{**v, "cadencia": lap.get("avg_cadence"), "desnivel_m": lap.get("elevation_gain_m")}
                              for v, lap in zip(detalle["vueltas"], laps, strict=True)]
        return detalle

    async def ver_actividad(request: Request) -> Response:
        detalle = await run_in_threadpool(_detalle, request.path_params["id"])
        if detalle is None:
            return _error("Esa actividad no está.", 404)
        return JSONResponse(detalle)

    def _importar(texto: str, fichero: str) -> dict[str, Any]:
        nombre = re.sub(r"\.(md|markdown|txt)$", "", fichero, flags=re.I) or None
        propuesta = proponer(texto, ordenador, store, hoy(), nombre=nombre,
                             al_gastar=lambda modelo, uso: conversaciones.apuntar_gasto(ahora(), modelo, uso,
                                                                                        "importar plan"))
        token = secrets.token_urlsafe(16)
        propuestas[token] = propuesta
        while len(propuestas) > PROPUESTAS_GUARDADAS:
            propuestas.popitem(last=False)
        return {"token": token, "fichero": fichero, **propuesta}

    async def importar(request: Request) -> Response:
        cuerpo = await _cuerpo(request) or {}
        texto, fichero = cuerpo.get("texto"), cuerpo.get("fichero")
        if not isinstance(texto, str) or not texto.strip():
            return _error("Elige tu plan (.md): no se ha importado nada.", 400)
        fichero = fichero if isinstance(fichero, str) else ""
        if await run_in_threadpool(conversaciones.tope_alcanzado, ahora()):
            return _error(f"Has llegado al tope de {TOPE_DOLARES_MES:g} $ de este mes: importar el plan vuelve el "
                          "día 1. No se ha importado nada.", 429, tope=True)
        try:
            return JSONResponse(await run_in_threadpool(_importar, texto, fichero))
        except ErrorPlan as exc:
            log.warning("plan import failed (%s)", type(exc).__name__)  # never a text of the plan (R9)
            return _error(f"No he podido leer tu plan ({exc}). No se ha guardado nada. Inténtalo de nuevo.", 502)

    async def confirmar_importacion(request: Request) -> Response:
        cuerpo = await _cuerpo(request)
        if cuerpo is None:
            return _error("Lo enviado no se entiende: no se ha guardado nada.", 400)
        token, elecciones = cuerpo.get("token"), cuerpo.get("elecciones") or {}
        propuesta = propuestas.pop(token, None) if isinstance(token, str) else None
        if propuesta is None:
            return _error("Esa importación ya no está: vuelve a elegir tu plan.", 404)
        if not isinstance(elecciones, dict):
            elecciones = {}
        ident = await run_in_threadpool(confirmar, propuesta, {str(k): str(v) for k, v in elecciones.items()},
                                        planes, ahora())
        return JSONResponse(await run_in_threadpool(lambda: _vista(planes.leer(ident))))

    async def bajar(request: Request) -> Response:
        plan = await run_in_threadpool(planes.leer, request.path_params["id"])
        if plan is None:
            return _error(NO_ESTA, 404)
        texto = await run_in_threadpool(escribir, plan, store, hoy())
        return Response(texto, media_type="text/markdown; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="{_nombre_de_fichero(plan["nombre"])}"'})

    return [
        Route("/api/plan", ver),
        Route("/api/plan/semana/{n:int}", ver_semana),
        Route("/api/plan/actividad/{id}", ver_actividad),
        Route("/api/plan/importar", importar, methods=["POST"]),
        Route("/api/plan/confirmar", confirmar_importacion, methods=["POST"]),
        Route("/api/plan/{id:int}.md", bajar),
        Route("/api/plan/sesion/{id:int}/garmin", tarjeta_reloj, methods=["POST"]),
        Route("/api/plan/sesion/{id:int}.fit", bajar_fit),
        Route("/api/plan/carrera", editar_carrera, methods=["POST"]),
        Route("/api/plan/nuevo", nuevo_plan, methods=["POST"]),
        Route("/api/plan/{id:int}/renombrar", renombrar, methods=["POST"]),
        Route("/api/plan/{id:int}/borrar", borrar, methods=["POST"]),
        Route("/api/plan/carrera/anadir", anadir_carrera, methods=["POST"]),
        Route("/api/plan/carrera/quitar", quitar_carrera, methods=["POST"]),
        Route("/api/plan/carrera/mover", mover_carrera, methods=["POST"]),
        Route("/api/plan/carrera/principal", hacer_principal, methods=["POST"]),
        Route("/api/plan/incidencias", ver_incidencias),
        Route("/api/plan/incidencia/{id:int}/cerrar", cerrar_incidencia, methods=["POST"]),
    ]
