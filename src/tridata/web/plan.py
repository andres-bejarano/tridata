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
from ..plan import Planes, semanas
from ..plan.importar import confirmar, proponer
from ..plan.markdown import escribir
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
        return {"hoy": dia.isoformat(), "plan": semanas.vista(plan, store, dia) if plan is not None else None,
                "planes": [{"id": p["id"], "nombre": p["nombre"], "en_curso": p["id"] == en_curso}
                           for p in planes.lista()]}

    async def ver(request: Request) -> Response:
        plan, falta = await run_in_threadpool(_plan_pedido, request)
        if falta:
            return _error(NO_ESTA, 404)
        return JSONResponse(await run_in_threadpool(_vista, plan))

    async def ver_semana(request: Request) -> Response:
        plan, _ = await run_in_threadpool(_plan_pedido, request)
        vista = (await run_in_threadpool(semanas.semana, plan, request.path_params["n"], store, hoy())
                 if plan is not None else None)
        if vista is None:
            return _error("Esa semana no está en el plan.", 404)
        return JSONResponse(vista)

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
    ]
