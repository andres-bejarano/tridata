"""Ajustes › Google Calendar (unit 043, REC-27 of `mandar-sesion-reloj`), mounted by `crear_app`.

    GET  /api/calendario              {conectado, configurado, calendario, texto, ultima, error}: «sin conectar» or
                                      «Conectado · calendario TriData», the last good update and the last failure
    POST /api/calendario/conectar     Google's permission in the browser of this PC (`Calendario.conectar`), then a
                                      first pass; 409 without TRIDATA_GOOGLE_CLIENTE or while it is connecting, 502
                                      if Google said no
    POST /api/calendario/sincronizar  «Sincronizar ahora»: one pass, and the state (a failure is in `error`)

The pass also runs when the app starts and every 10 minutes (`en_segundo_plano`, in its own thread): a failure is
said in Ajustes and tried again on the next one, and nothing of the plan waits for Google (R4). Without a token
nothing talks to Google (R6). Nothing here logs or answers the token or the path of the client's JSON (R5).
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

from ..calendario import FALTA_CLIENTE, Calendario, ErrorCalendario
from ..calendario.sincronizar import sincronizar
from ..plan import Planes

log = logging.getLogger(__name__)

CADA_SEGUNDOS = 600
CONECTANDO = "Ya estoy conectando Google Calendar: termina en el navegador del PC."


class Sincronizacion:
    """One pass at a time (the periodic one, «Sincronizar ahora» and the one after connecting)."""

    def __init__(self, calendario: Calendario, planes: Planes, hoy: Callable[[], date],
                 ahora: Callable[[], datetime]) -> None:
        self.calendario, self._planes, self._hoy, self._ahora = calendario, planes, hoy, ahora
        self._cerrojo = threading.Lock()
        self.conectando = threading.Lock()

    def vuelta(self) -> dict[str, Any]:
        """One pass if connected, written down as good or as failed; the state. Never raises."""
        if self.calendario.conectado:
            with self._cerrojo:
                try:
                    resumen = sincronizar(self._planes, self.calendario, self._hoy())
                except ErrorCalendario as exc:  # its text is a fixed reason, never the library's (R5)
                    log.warning("Google Calendar not updated: %s", exc)
                    self.calendario.apuntar_fallo(f"No se pudo actualizar el calendario: {exc}. Se reintentará en "
                                                  "la próxima vuelta.")
                else:
                    log.info("Google Calendar updated: %(creados)d created, %(cambiados)d changed, "
                             "%(borrados)d deleted", resumen)
                    self.calendario.apuntar_bien(self._ahora())
        return self.calendario.estado()

    def en_segundo_plano(self, parar: threading.Event, cada: float = CADA_SEGUNDOS) -> None:
        """At start and then every `cada` seconds, until `parar`."""
        while not parar.is_set():
            self.vuelta()
            parar.wait(cada)


def rutas(sinc: Sincronizacion) -> list[Route]:
    calendario = sinc.calendario

    async def estado(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(calendario.estado))

    async def conectar(request: Request) -> Response:
        if not calendario.configurado:
            return JSONResponse({**calendario.estado(), "error": FALTA_CLIENTE}, status_code=409)
        if not sinc.conectando.acquire(blocking=False):
            return JSONResponse({"error": CONECTANDO}, status_code=409)
        try:
            await run_in_threadpool(calendario.conectar)
        except ErrorCalendario as exc:
            log.warning("Google Calendar not connected: %s", exc)
            return JSONResponse({"error": f"No se pudo conectar Google Calendar: {exc}."}, status_code=502)
        finally:
            sinc.conectando.release()
        return JSONResponse(await run_in_threadpool(sinc.vuelta))

    async def sincronizar_ahora(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(sinc.vuelta))

    return [
        Route("/api/calendario", estado),
        Route("/api/calendario/conectar", conectar, methods=["POST"]),
        Route("/api/calendario/sincronizar", sincronizar_ahora, methods=["POST"]),
    ]


__all__ = ["CADA_SEGUNDOS", "Sincronizacion", "rutas"]
