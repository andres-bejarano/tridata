"""Starlette app: the day page and its JSON routes.

    GET  /                        the page (static HTML + JS modules + CSS)
    POST /api/interpretar         text -> interpreted list; saves NOTHING (R-1)
    POST /api/confirmar           reviewed list -> saved food by food; returns today (R-4, R-5)
    POST /api/alimento            add a food by hand (no AI, no interpretation); body with `dia`
    POST /api/alimento/N          change a saved food (name, quantity, unit, kcal; `dia` optional
                                  moves it, with the planned/eaten rule)
    POST /api/alimento/N/borrar   delete a saved food; only with {"confirmado": true} (R-11)
    POST /api/alimento/N/comido   mark a planned food as eaten (today or a past day only)
    GET  /api/dia                 the day table, total, expenditure, margins and plan (?fecha=AAAA-MM-DD),
                                  all with the settings in force THAT day
    GET  /ajustes                 the settings page
    GET  /api/ajustes             settings in force (?fecha=, default today), changes to come, races
    POST /api/ajustes             {"datos": {...}, "desde": "AAAA-MM-DD"} save one or more settings
                                  (`desde` defaults to today); says whether to ask for the limit
    POST /api/carrera             add a race {"nombre", "fecha", "dias_mantenimiento"}
    POST /api/carrera/N           change a race (same body)
    POST /api/carrera/N/quitar    remove a race; only with {"confirmado": true}

Every /api/alimento route answers {"alimento": ..., "dia": ...}, where `dia` is the day the food
was in BEFORE the action (the screen being looked at; for an added food, its day).
404 if the food does not exist, 400 malformed data, 422 a business rule. A settings value that
is not valid is a 400 and nothing is saved. A day with no basal or kcal per step is a 409.
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
from ..nutricion import Ajustes, Alimento, RegistroComidas, resumen_del_dia

ESTATICOS = Path(__file__).parent / "static"
NO_ESTA = "Ese alimento ya no está apuntado."
NO_ESTA_CARRERA = "Esa carrera ya no está en tus ajustes."


class FaltanAjustes(Exception):
    """A day cannot be computed: the settings of that day lack basal or kcal per step."""


def _error(mensaje: str, estado: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": mensaje, **extra}, status_code=estado)


async def _cuerpo(request: Request) -> dict | None:
    try:
        datos = await request.json()
    except (ValueError, json.JSONDecodeError):
        return None
    return datos if isinstance(datos, dict) else None


def _plan(vigentes: Any) -> dict:
    return {"objetivo_pct": vigentes.objetivo_pct, "motivo": vigentes.motivo_objetivo,
            "limite_saludable_pct": vigentes.limite_saludable_pct}


def crear_app(
    registro: RegistroComidas, interprete: Any, ajustes: Ajustes, hoy: Callable[[], date] = date.today,
) -> Starlette:
    """`interprete` is anything with `interpretar(texto, hoy)` (the Claude one, or a test double).

    Every day is computed with `ajustes.vigentes(that day)`: no expenditure config is fixed at start.
    """

    def _dia(fecha: date) -> dict:
        vigentes = ajustes.vigentes(fecha)
        try:
            config = vigentes.config_gasto()
        except ValueError as exc:
            raise FaltanAjustes(str(exc)) from None
        return {**resumen_del_dia(registro.apuntes_del_dia(fecha), fecha, config), "plan": _plan(vigentes)}

    def _vista_ajustes(fecha: date) -> dict:
        vigentes = ajustes.vigentes(fecha)
        return {
            "fecha": fecha.isoformat(),
            "vigentes": ajustes.valores_con_fecha(fecha),
            "futuros": ajustes.cambios_futuros(fecha),
            "carreras": [c.como_dict() for c in vigentes.carreras],
            "plan": _plan(vigentes),
        }

    async def faltan_ajustes(request: Request, exc: Exception) -> Response:
        return _error(f"{exc} Ponlos en Ajustes.", 409)

    async def pagina(request: Request) -> Response:
        return FileResponse(ESTATICOS / "index.html")

    async def pagina_ajustes(request: Request) -> Response:
        return FileResponse(ESTATICOS / "ajustes.html")

    async def ver_ajustes(request: Request) -> Response:
        texto = request.query_params.get("fecha")
        try:
            fecha = date.fromisoformat(texto) if texto else hoy()
        except ValueError:
            return _error(f"Fecha no válida: {texto!r}", 400)
        return JSONResponse(_vista_ajustes(fecha))

    async def guardar_ajustes(request: Request) -> Response:
        cuerpo = await _cuerpo(request) or {}
        datos = cuerpo.get("datos")
        if not isinstance(datos, dict) or not datos:
            return _error("No hay ningún ajuste que guardar.", 400)
        desde = cuerpo.get("desde") or hoy()
        try:
            guardado = ajustes.cambiar(datos, desde=desde)
        except ValueError as exc:
            return _error(f"No se ha guardado nada: {exc}.", 400)
        return JSONResponse({**guardado, "ajustes": _vista_ajustes(hoy()), "dia": _dia(hoy())})

    async def anadir_carrera(request: Request) -> Response:
        cuerpo = await _cuerpo(request) or {}
        try:
            carrera = ajustes.anadir_carrera(cuerpo.get("nombre"), cuerpo.get("fecha"), cuerpo.get("dias_mantenimiento"))
        except ValueError as exc:
            return _error(f"No se ha guardado la carrera: {exc}.", 400)
        return JSONResponse({"carrera": carrera, "ajustes": _vista_ajustes(hoy())})

    async def cambiar_carrera(request: Request) -> Response:
        cuerpo = await _cuerpo(request) or {}
        try:
            carrera = ajustes.cambiar_carrera(request.path_params["id"], cuerpo.get("nombre"), cuerpo.get("fecha"),
                                              cuerpo.get("dias_mantenimiento"))
        except KeyError:
            return _error(NO_ESTA_CARRERA, 404)
        except ValueError as exc:
            return _error(f"No se ha cambiado la carrera: {exc}.", 400)
        return JSONResponse({"carrera": carrera, "ajustes": _vista_ajustes(hoy())})

    async def quitar_carrera(request: Request) -> Response:
        if (await _cuerpo(request) or {}).get("confirmado") is not True:
            return _error("Hay que confirmar antes de quitar la carrera: no se ha quitado nada.", 400)
        try:
            carrera = ajustes.quitar_carrera(request.path_params["id"])
        except KeyError:
            return _error(NO_ESTA_CARRERA, 404)
        return JSONResponse({"carrera": carrera, "ajustes": _vista_ajustes(hoy())})

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

    return Starlette(exception_handlers={FaltanAjustes: faltan_ajustes}, routes=[
        Route("/", pagina),
        Route("/ajustes", pagina_ajustes),
        Route("/api/dia", dia),
        Route("/api/ajustes", ver_ajustes),
        Route("/api/ajustes", guardar_ajustes, methods=["POST"]),
        Route("/api/carrera", anadir_carrera, methods=["POST"]),
        Route("/api/carrera/{id:int}", cambiar_carrera, methods=["POST"]),
        Route("/api/carrera/{id:int}/quitar", quitar_carrera, methods=["POST"]),
        Route("/api/interpretar", interpretar, methods=["POST"]),
        Route("/api/confirmar", confirmar, methods=["POST"]),
        Route("/api/alimento", anadir, methods=["POST"]),
        Route("/api/alimento/{id:int}", cambiar, methods=["POST"]),
        Route("/api/alimento/{id:int}/borrar", borrar, methods=["POST"]),
        Route("/api/alimento/{id:int}/comido", comido, methods=["POST"]),
        Mount("/static", StaticFiles(directory=ESTATICOS), name="static"),
    ])
