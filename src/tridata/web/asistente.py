"""The JSON routes of the assistant's cards (unit 021), mounted by `crear_app` (as `web/recetas.py`).

    POST /api/asistente/rehacer     {"tarjeta", "eleccion"?, "valores"?} -> {"tarjeta": the card redone with what is
                                    in the base now, with the chosen candidate (R-69) or the corrected values (R-71);
                                    a card that does not pass keeps its fields and says why in `error`; or a `no`}.
                                    Writes nothing
    POST /api/asistente/confirmar   {"tarjeta"} -> redone and compared (G-41, R-78, S-20): 200 {"hecho", "dia"} with
                                    today's day (the margin at once, R-65) when it does what it showed, applied with
                                    the method of the screen by hand; 409 {"error", "tarjeta"} with the redone card
                                    and nothing applied when what was before changed; 422 {"error"} when it does not
                                    pass the check
    POST /api/asistente/descartar   {"tarjeta"} -> nothing changes (R-70); the card is written down as `descartada`

400 for what is not a card. The cards themselves come in `cambios` of /api/interpretar and /api/dictado.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..nutricion.asistente import CAMBIADO, Cambiado, Contexto, NoValida, TarjetaMal, confirmar, descartar, numero, rehacer


def _error(mensaje: str, estado: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": mensaje, **extra}, status_code=estado)


async def _cuerpo(request: Request) -> dict | None:
    try:
        datos = await request.json()
    except (ValueError, json.JSONDecodeError):
        return None
    return datos if isinstance(datos, dict) else None


def _margen_de_hoy(dia: dict) -> str:
    """«Hoy te quedan 1.865 kcal (mantenimiento)»: the level of the objective, as the card «Comida» of Hoy."""
    margenes = dia.get("margenes") or []
    if not margenes:
        return ""
    marcado = next((m for m in margenes if m.get("objetivo")), None) or next(
        (m for m in margenes if m["nivel"] == dia.get("objetivo", {}).get("porcentaje")), margenes[0])
    nivel = "mantenimiento" if marcado["nivel"] == 0 else f"{'+' if marcado['nivel'] > 0 else '−'}{numero(abs(marcado['nivel']))} %"
    return f" Hoy te quedan {numero(round(marcado['kcal']))} kcal ({nivel})."


def rutas(contexto: Contexto, dia: Callable[[date], dict]) -> list[Route]:
    """`dia(fecha)` is the day as /api/dia answers it (the same `_dia` of `crear_app`)."""

    async def ruta_rehacer(request: Request) -> Response:
        cuerpo = await _cuerpo(request) or {}
        try:
            resultado = await run_in_threadpool(lambda: rehacer(cuerpo.get("tarjeta"), contexto,
                                                                eleccion=cuerpo.get("eleccion"),
                                                                valores=cuerpo.get("valores")))
        except TarjetaMal as exc:
            return _error(f"No se ha cambiado nada: {exc}.", 400)
        return JSONResponse({"tarjeta": resultado})

    async def ruta_confirmar(request: Request) -> Response:
        cuerpo = await _cuerpo(request) or {}
        try:
            hecho = await run_in_threadpool(confirmar, cuerpo.get("tarjeta"), contexto)
        except TarjetaMal as exc:
            return _error(f"No se ha cambiado nada: {exc}.", 400)
        except Cambiado as exc:
            return _error(f"{CAMBIADO}. No se ha cambiado nada.", 409, tarjeta=exc.tarjeta)
        except NoValida as exc:
            return _error(str(exc), 422)
        hoy = await run_in_threadpool(dia, contexto.hoy())
        return JSONResponse({"hecho": f"Hecho: {hecho['hecho']}.{_margen_de_hoy(hoy)}", "dia": hoy,
                             "tarjeta": hecho["tarjeta"]})

    async def ruta_descartar(request: Request) -> Response:
        cuerpo = await _cuerpo(request) or {}
        try:
            await run_in_threadpool(descartar, cuerpo.get("tarjeta"), contexto)
        except TarjetaMal as exc:
            return _error(f"No se ha descartado nada: {exc}.", 400)
        return JSONResponse({"descartada": True})

    return [
        Route("/api/asistente/rehacer", ruta_rehacer, methods=["POST"]),
        Route("/api/asistente/confirmar", ruta_confirmar, methods=["POST"]),
        Route("/api/asistente/descartar", ruta_descartar, methods=["POST"]),
    ]
