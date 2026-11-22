"""Fitness › Zapatillas and Ajustes › Strava (unit 102, REC-46 of `ver-fitness`), mounted by `crear_app`.

    GET  /api/zapatillas        {zapatillas, lectura, aviso_lectura, strava}: each shoe in use with its km, cap, what
                                is left (or gone over), its mark and where the km come from with its date (R2); why
                                the last read failed, if it did (R5, C-368)
    POST /api/zapatillas/{id}   {"km": n} (said by Andrés, R6) or {"tope": n} (a whole number from 50 to 3000, R3),
                                one of them; the list again. 400 with why and nothing changes; 404 if it is not in use
    GET  /api/strava            {conectado, configurado, texto, ultima, error, falta}: «sin conectar» or «Conectado ·
                                última lectura …», why the last read failed and, without TRIDATA_STRAVA_CLIENTE, what
                                is missing (R7)
    POST /api/strava/conectar   Strava's permission in the browser of this PC (`Strava.conectar`), then the first read;
                                409 without TRIDATA_STRAVA_CLIENTE or while it is connecting, 502 if Strava said no

Strava is read only here and in the daily routine (`tridata strava`): never with «Actualizar Garmin», never in a
thread of the app. Nothing here answers or logs a token, the client's secret, the `code` or the path of the JSON.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..strava import FALTA_CLIENTE, SIN_CONECTAR, ErrorStrava, Strava
from ..zapatillas import ErrorZapatilla, Zapatillas, leer_de_strava

log = logging.getLogger(__name__)

CONECTANDO = "Ya estoy conectando Strava: termina en el navegador del PC."
NO_ESTA = "Esa zapatilla no está entre las que tienes en activo."
UNA_COSA = "Di los km o el tope de la zapatilla, una cosa cada vez."


def _cuando(momento: str, hoy: date) -> str:
    hora = datetime.fromisoformat(momento)
    dia = "hoy" if hora.date() == hoy else f"el {hora.day}/{hora.month}"
    return f"{dia} a las {hora.hour}:{hora.minute:02d}"


def estado_strava(strava: Strava, repo: Zapatillas, hoy: date) -> dict[str, Any]:
    """What Ajustes › Strava shows (R7)."""
    conectado = strava.conectado
    lectura = repo.estado_lectura()
    if conectado:
        texto = ("Conectado · última lectura " + _cuando(lectura["ultima"], hoy) if lectura["ultima"]
                 else "Conectado · aún sin leer")
    else:
        texto = SIN_CONECTAR
    return {"conectado": conectado, "configurado": strava.configurado, "texto": texto,
            "ultima": lectura["ultima"] if conectado else None, "error": lectura["motivo"] if conectado else None,
            "falta": None if strava.configurado else FALTA_CLIENTE}


def rutas(repo: Zapatillas, strava: Strava, hoy: Callable[[], date], ahora: Callable[[], datetime]) -> list[Route]:
    conectando = threading.Lock()

    def _vista() -> dict[str, Any]:
        dia = hoy()
        return {**repo.vista(dia), "strava": estado_strava(strava, repo, dia)}

    async def ver(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(_vista))

    async def cambiar(request: Request) -> Response:
        ident = request.path_params["id"]
        try:
            cuerpo = await request.json()
        except ValueError:
            cuerpo = None
        if not isinstance(cuerpo, dict) or len(cuerpo) != 1 or not ({"km", "tope"} & set(cuerpo)):
            return JSONResponse({"error": UNA_COSA}, status_code=400)
        try:
            if "tope" in cuerpo:
                await run_in_threadpool(repo.cambiar_tope, ident, cuerpo["tope"])
            else:
                await run_in_threadpool(repo.poner_km, ident, cuerpo["km"], ahora())
        except ErrorZapatilla as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except KeyError:
            return JSONResponse({"error": NO_ESTA}, status_code=404)
        return JSONResponse(await run_in_threadpool(_vista))

    async def ver_strava(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(estado_strava, strava, repo, hoy()))

    async def conectar(request: Request) -> Response:
        if not strava.configurado:
            return JSONResponse({**estado_strava(strava, repo, hoy()), "error": FALTA_CLIENTE}, status_code=409)
        if not conectando.acquire(blocking=False):
            return JSONResponse({"error": CONECTANDO}, status_code=409)
        try:
            await run_in_threadpool(strava.conectar)
        except ErrorStrava as exc:  # its text is a fixed reason, never a token
            log.warning("Strava not connected: %s", exc)
            return JSONResponse({"error": f"No se pudo conectar Strava: {exc}."}, status_code=502)
        finally:
            conectando.release()
        try:
            await run_in_threadpool(leer_de_strava, strava, repo, ahora())
        except ErrorStrava as exc:  # written down: Ajustes and Fitness say it
            log.warning("Strava connected but not read: %s", exc)
        dia = hoy()
        return JSONResponse({**estado_strava(strava, repo, dia), "zapatillas": repo.vista(dia)})

    return [
        Route("/api/zapatillas", ver),
        Route("/api/zapatillas/{id}", cambiar, methods=["POST"]),
        Route("/api/strava", ver_strava),
        Route("/api/strava/conectar", conectar, methods=["POST"]),
    ]


__all__ = ["CONECTANDO", "estado_strava", "rutas"]
