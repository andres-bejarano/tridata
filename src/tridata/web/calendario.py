"""Ajustes › Google Calendar (unit 043, REC-27 of `mandar-sesion-reloj`), mounted by `crear_app`.

    GET  /api/calendario              {conectado, configurado, calendario, texto, ultima, error}: «sin conectar» or
                                      «Conectado · calendario TriData», the last good update and the last failure
    POST /api/calendario/conectar     Google's permission in the browser of this PC (`Calendario.conectar`), then a
                                      first pass; 409 without TRIDATA_GOOGLE_CLIENTE or while it is connecting, 502
                                      if Google said no
    POST /api/calendario/sincronizar  «Sincronizar ahora»: one pass, and the state (a failure is in `error`)
    POST /api/calendario/desconectar  unit 062: the permission withdrawn in Google and deleted from this PC, after the
                                      pass in course; the state («sin conectar») and, if Google did not answer,
                                      `aviso`. «TriData» stays in Google and is reused when connecting again

Unit 054: the pass also puts the trainings done in Garmin since 2026-08-01 (`store`), so the new ones arrive on
the pass after each sync with Garmin.

The pass also runs when the app starts and every 10 minutes (`en_segundo_plano`, in its own thread): a failure is
said in Ajustes and tried again on the next one, and nothing of the plan waits for Google (R4). Without a token
nothing talks to Google (R6). Nothing here logs or answers the token or the path of the client's JSON (R5).

Unit 062: `pronto()` wakes that thread at once (after confirming a card, saving in Fitness › Plan or «Actualizar
Garmin»: `Despertar`, a middleware of `crear_app`, with `despierta`), so the calendar changes in seconds; several in
a row give one pass more, not one each. An unexpected failure of a pass is said with a fixed text and only its type
is logged: the thread never dies.
"""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..calendario import FALTA_CLIENTE, Calendario, ErrorCalendario
from ..calendario.sincronizar import sincronizar
from ..plan import Planes
from ..storage import DataStore

log = logging.getLogger(__name__)

CADA_SEGUNDOS = 600
CONECTANDO = "Ya estoy conectando Google Calendar: termina en el navegador del PC."
INESPERADO = "No se pudo actualizar el calendario: error inesperado. Se reintentará en la próxima vuelta."
NO_REVOCADO = "Desconectado en este PC, pero no se pudo retirar el permiso en Google ({}): puedes quitarlo en tu cuenta."
# unit 062 R1: what changes the plan or the activities, so the calendar is updated at once after a good answer: a
# card confirmed, «Actualizar Garmin» and every POST under /api/plan/ (confirming an import, the races, the plans by
# hand of the 063…) but those that change nothing that goes to the calendar: `importar` only proposes, the watch's
# card only is a card, and an incidence never goes to Google (G-59)
DESPIERTAN = frozenset({"/api/asistente/confirmar", "/api/garmin/actualizar"})
NO_DESPIERTAN = re.compile(r"/api/plan/(importar|sesion/\d+/garmin|incidencia/\d+/cerrar)")


def despierta(metodo: str, ruta: str) -> bool:
    """Whether a 2xx of this request changes what goes to Google Calendar (062 R1)."""
    if metodo != "POST":
        return False
    if ruta in DESPIERTAN:
        return True
    return ruta.startswith("/api/plan/") and not NO_DESPIERTAN.fullmatch(ruta)


class Sincronizacion:
    """One pass at a time (the periodic one, «Sincronizar ahora» and the one after connecting)."""

    def __init__(self, calendario: Calendario, planes: Planes, hoy: Callable[[], date],
                 ahora: Callable[[], datetime], store: DataStore | None = None) -> None:
        self.calendario, self._planes, self._hoy, self._ahora = calendario, planes, hoy, ahora
        self._store = store  # unit 054: the trainings done in Garmin go too
        self._cerrojo = threading.Lock()
        self.conectando = threading.Lock()
        self._despertar = threading.Event()  # unit 062: `pronto()`

    def vuelta(self) -> dict[str, Any]:
        """One pass if connected, written down as good or as failed; the state. Never raises."""
        if self.calendario.conectado:
            with self._cerrojo:
                if self.calendario.conectado:  # unit 062: it may have been disconnected while this pass waited
                    self._pasada()
        return self.calendario.estado()

    def _pasada(self) -> None:
        try:
            resumen = sincronizar(self._planes, self.calendario, self._hoy(), self._store)
        except ErrorCalendario as exc:  # its text is a fixed reason, never the library's (R5)
            log.warning("Google Calendar not updated: %s", exc)
            self.calendario.apuntar_fallo(f"No se pudo actualizar el calendario: {exc}. Se reintentará en "
                                          "la próxima vuelta.")
        except Exception as exc:  # noqa: BLE001 - unit 062 R3: anything else is said too, and the next pass runs
            log.warning("Google Calendar not updated: unexpected %s", type(exc).__name__)  # never its text
            self.calendario.apuntar_fallo(INESPERADO)
        else:
            log.info("Google Calendar updated: %(creados)d created, %(cambiados)d changed, "
                     "%(borrados)d deleted", resumen)
            self.calendario.apuntar_bien(self._ahora())

    def pronto(self) -> None:
        """Unit 062 R1: the thread makes a pass now (or right after the one in course), not in 10 minutes."""
        self._despertar.set()

    def desconectar(self) -> dict[str, Any]:
        """Unit 062 R4: after the pass in course, the permission withdrawn and deleted; the state, with `aviso` if
        Google did not answer."""
        with self._cerrojo:
            motivo = self.calendario.desconectar()
        estado = self.calendario.estado()
        if motivo:
            log.warning("Google Calendar disconnected; the permission not withdrawn in Google: %s", motivo)
            estado["aviso"] = NO_REVOCADO.format(motivo)
        return estado

    def en_segundo_plano(self, parar: threading.Event, cada: float = CADA_SEGUNDOS) -> None:
        """At start, then every `cada` seconds or as soon as `pronto()`, until `parar` (set it, then `pronto()`)."""
        while not parar.is_set():
            self._despertar.clear()  # what asks for a pass from here on gets one more after this one
            if parar.is_set():  # `parar.set(); pronto()` may have fallen just before the `clear()`
                break
            try:
                self.vuelta()
            except Exception as exc:  # noqa: BLE001 - unit 062 R3: the thread never dies (e.g. the SQLite busy)
                log.warning("Google Calendar pass failed: %s", type(exc).__name__)
            self._despertar.wait(cada)


class Despertar:
    """Unit 062 R1: after a 2xx of a request that `despierta`, `pronto()`. The answer has gone already: it never waits
    for Google."""

    def __init__(self, app: ASGIApp, sinc: Sincronizacion) -> None:
        self.app, self.sinc = app, sinc

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not despierta(scope["method"], scope["path"]):
            await self.app(scope, receive, send)
            return
        estado = 0

        async def enviar(mensaje: Message) -> None:
            nonlocal estado
            if mensaje["type"] == "http.response.start":
                estado = mensaje["status"]
            await send(mensaje)

        await self.app(scope, receive, enviar)
        if 200 <= estado < 300:
            self.sinc.pronto()


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

    async def desconectar(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(sinc.desconectar))

    return [
        Route("/api/calendario", estado),
        Route("/api/calendario/conectar", conectar, methods=["POST"]),
        Route("/api/calendario/sincronizar", sincronizar_ahora, methods=["POST"]),
        Route("/api/calendario/desconectar", desconectar, methods=["POST"]),
    ]


__all__ = ["CADA_SEGUNDOS", "DESPIERTAN", "Despertar", "Sincronizacion", "despierta", "rutas"]
