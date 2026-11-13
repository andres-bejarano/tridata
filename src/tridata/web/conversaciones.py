"""«Lo que ha hecho el coach» in Ajustes (unit 080, R-224, C-278, C-279): the only route of the team the app keeps.

    GET  /api/coach    {cambios: the last 20 changes made from the PC (messages of the conversation «Desde el PC»),
                       newest first, each {texto, hora, papel}}. Read only: any other method 405

Unit 083: the Asistente's routes (its conversations, the team's models, the medical PDF read by the API, the routes
attached, the dictation of the chat) were removed with the paid AI. What the coach does from the PC lives in
`conversaciones/desde_pc.py`; the tables of before stay in the base as they were.
"""
from __future__ import annotations

from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..conversaciones import Conversaciones

# -- unit 080 (R-224): «Lo que ha hecho el coach» ----------------------------------------------------------------------

CAMBIOS_DEL_COACH = 20
DESDE_EL_PC = "Desde el PC"  # the title of the conversation the coach writes in (`conversaciones.desde_pc.TITULO`)


def lo_que_ha_hecho_el_coach(conversaciones: Conversaciones, cuantos: int = CAMBIOS_DEL_COACH) -> list[dict[str, Any]]:
    """The last `cuantos` changes made from the PC (R-196: each one a message of the coach in «Desde el PC»), newest
    first. Only reads."""
    cambios = [{"texto": m["texto"], "hora": m["hora"], "papel": m["papel"], "id": m["id"]}
               for c in conversaciones.lista() if c["titulo"] == DESDE_EL_PC
               for m in conversaciones.mensajes(c["id"]) if m["quien"] == "asistente"]
    cambios.sort(key=lambda m: (m["hora"], m["id"]), reverse=True)
    return [{k: m[k] for k in ("texto", "hora", "papel")} for m in cambios[:cuantos]]


def rutas_coach(conversaciones: Conversaciones) -> list[Route]:
    async def ver(request: Request) -> Response:
        return JSONResponse({"cambios": await run_in_threadpool(lo_que_ha_hecho_el_coach, conversaciones)})

    return [Route("/api/coach", ver)]
