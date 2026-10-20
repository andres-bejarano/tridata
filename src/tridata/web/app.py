"""Starlette app: the day page and its JSON routes.

    GET  /                        the page (static HTML + JS modules + CSS)
    POST /api/interpretar         text -> interpreted list; saves NOTHING (R-1)
    POST /api/confirmar           reviewed list -> saved food by food; returns today (R-4, R-5)
    POST /api/alimento            add a food by hand (no AI, no interpretation); body with `dia`
    POST /api/alimento/N          change a saved food (name, quantity, unit, kcal; `dia` optional
                                  moves it, with the planned/eaten rule)
    POST /api/alimento/N/borrar   delete a saved food; only with {"confirmado": true} (R-11)
    POST /api/alimento/N/comido   mark a planned food as eaten (today or a past day only)
    GET  /api/dia                 the day table, total, expenditure and margins (?fecha=AAAA-MM-DD)

Every /api/alimento route answers {"alimento": ..., "dia": ...}, where `dia` is the day the food
was in BEFORE the action (the screen being looked at; for an added food, its day).
404 if the food does not exist, 400 malformed data, 422 a business rule.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..ia import ErrorInterpretacion
from ..nutricion import Alimento, ConfigGasto, RegistroComidas, resumen_del_dia

ESTATICOS = Path(__file__).parent / "static"
NO_ESTA = "Ese alimento ya no está apuntado."


def _error(mensaje: str, estado: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": mensaje, **extra}, status_code=estado)


async def _cuerpo(request: Request) -> dict | None:
    try:
        datos = await request.json()
    except (ValueError, json.JSONDecodeError):
        return None
    return datos if isinstance(datos, dict) else None


def crear_app(
    registro: RegistroComidas, interprete: Any, config: ConfigGasto, hoy: Callable[[], date] = date.today,
) -> Starlette:
    """`interprete` is anything with `interpretar(texto, hoy)` (the Claude one, or a test double)."""

    def _dia(fecha: date) -> dict:
        return resumen_del_dia(registro.apuntes_del_dia(fecha), fecha, config)

    async def pagina(request: Request) -> Response:
        return FileResponse(ESTATICOS / "index.html")

    async def dia(request: Request) -> Response:
        texto = request.query_params.get("fecha")
        try:
            fecha = date.fromisoformat(texto) if texto else hoy()
        except ValueError:
            return _error(f"Fecha no válida: {texto!r}", 400)
        return JSONResponse(_dia(fecha))

    async def interpretar(request: Request) -> Response:
        texto = (await _cuerpo(request) or {}).get("texto")
        if not isinstance(texto, str) or not texto.strip():
            return _error("Escribe lo que has comido o vas a comer.", 400, texto=texto or "")
        try:
            lista = await run_in_threadpool(interprete.interpretar, texto.strip(), hoy())
        except ErrorInterpretacion as exc:
            return _error(f"No he podido interpretar lo que has escrito ({exc}). Inténtalo de nuevo.", 502,
                          texto=texto)
        return JSONResponse({"texto": texto, "alimentos": [a.model_dump(mode="json") for a in lista]})

    async def confirmar(request: Request) -> Response:
        cuerpo = await _cuerpo(request) or {}
        crudos = cuerpo.get("alimentos")
        if not isinstance(crudos, list) or not crudos:
            return _error("No hay ningún alimento que guardar.", 400)
        try:
            alimentos = [Alimento.desde_dict(a) for a in crudos]
        except ValueError as exc:
            return _error(str(exc), 400)
        texto = cuerpo.get("texto")
        try:
            guardados = registro.guardar(alimentos, hoy=hoy(), texto=texto if isinstance(texto, str) else "",
                                         sin_cambios=bool(cuerpo.get("sin_cambios")))
        except ValueError as exc:
            return _error(str(exc), 422)
        return JSONResponse({"guardados": guardados, "dia": _dia(hoy())})

    async def anadir(request: Request) -> Response:
        cuerpo = await _cuerpo(request) or {}
        try:
            alimento = Alimento.desde_dict({**cuerpo, "estimado": False, "dudoso": False, "supuestos": []})
        except ValueError as exc:
            return _error(str(exc), 400)
        try:
            anadido = registro.anadir(alimento, hoy=hoy())
        except ValueError as exc:
            return _error(str(exc), 422)
        return JSONResponse({"alimento": anadido, "dia": _dia(alimento.dia)})

    async def cambiar(request: Request) -> Response:
        cuerpo = await _cuerpo(request) or {}
        try:
            Alimento.desde_dict({**cuerpo, "dia": cuerpo.get("dia") or hoy().isoformat()})
        except ValueError as exc:
            return _error(str(exc), 400)
        try:
            origen = registro.alimento(request.path_params["id"])
            cambiado = registro.cambiar(request.path_params["id"], cuerpo, hoy=hoy())
        except KeyError:
            return _error(NO_ESTA, 404)
        except ValueError as exc:
            return _error(str(exc), 422)
        return JSONResponse({"alimento": cambiado, "dia": _dia(date.fromisoformat(origen["dia"]))})

    async def borrar(request: Request) -> Response:
        if (await _cuerpo(request) or {}).get("confirmado") is not True:
            return _error("Hay que confirmar antes de borrar: no se ha borrado nada.", 400)
        try:
            borrado = registro.borrar(request.path_params["id"])
        except KeyError:
            return _error(NO_ESTA, 404)
        return JSONResponse({"alimento": borrado, "dia": _dia(date.fromisoformat(borrado["dia"]))})

    async def comido(request: Request) -> Response:
        try:
            marcado = registro.marcar_comido(request.path_params["id"], hoy=hoy())
        except KeyError:
            return _error(NO_ESTA, 404)
        except ValueError as exc:
            return _error(str(exc), 422)
        return JSONResponse({"alimento": marcado, "dia": _dia(date.fromisoformat(marcado["dia"]))})

    return Starlette(routes=[
        Route("/", pagina),
        Route("/api/dia", dia),
        Route("/api/interpretar", interpretar, methods=["POST"]),
        Route("/api/confirmar", confirmar, methods=["POST"]),
        Route("/api/alimento", anadir, methods=["POST"]),
        Route("/api/alimento/{id:int}", cambiar, methods=["POST"]),
        Route("/api/alimento/{id:int}/borrar", borrar, methods=["POST"]),
        Route("/api/alimento/{id:int}/comido", comido, methods=["POST"]),
        Mount("/static", StaticFiles(directory=ESTATICOS), name="static"),
    ])
