"""The JSON routes of the Recetas tab (unit 011), mounted by `crear_app`.

    GET  /api/recetas                 recipes (state, kcal/100 g, estimated ingredients, versions), fixed
                                      foods and pots
    POST /api/recetas                 a new recipe; with "otra_tanda": true, a new batch of the recipe with
                                      that name. 409 with {"otra_tanda": true, "receta_id"} if the name is
                                      already a recipe and it was not asked as a batch (R-51)
    POST /api/recetas/N               correct the version in use (ingredients, weight, pot, approximate
                                      kcal/100 g; name, alias, note)
    POST /api/recetas/N/version       {"numero": n}: use that batch from now on
    POST /api/recetas/N/borrar        delete it; only with {"confirmado": true} (R-53)
    POST /api/fijos, /api/fijos/N, /api/fijos/N/borrar   add, change, delete a fixed food
    POST /api/ollas, /api/ollas/N, /api/ollas/N/borrar   add, change, delete a pot

Every route answers the whole listing plus the thing touched ("receta", "fijo" or "olla").
400 malformed data, 422 a business rule (its text says why), 404 it does not exist. The kcal of
an ingredient that is a fixed food are always recomputed here, never taken from the page.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..nutricion.recetas import ErrorDatos, Recetario, YaEsReceta

NO_ESTA = {"receta": "Esa receta ya no está guardada.", "fijo": "Ese alimento fijo ya no está guardado.",
           "olla": "Esa tara ya no está guardada."}


def _error(mensaje: str, estado: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": mensaje, **extra}, status_code=estado)


async def _cuerpo(request: Request) -> dict | None:
    try:
        datos = await request.json()
    except (ValueError, json.JSONDecodeError):
        return None
    return datos if isinstance(datos, dict) else None


def rutas(recetario: Recetario, hoy: Callable[[], date]) -> list[Route]:
    def respuesta(que: str, cosa: dict | None) -> JSONResponse:
        return JSONResponse({que: cosa, **recetario.listado()})

    def ejecutar(que: str, hacer: Callable[[dict], dict]) -> Callable[[Request], Any]:
        async def ruta(request: Request) -> Response:
            cuerpo = await _cuerpo(request)
            if cuerpo is None:
                return _error("Lo enviado no se entiende: no se ha guardado nada.", 400)
            try:
                return respuesta(que, hacer({**cuerpo, "_id": request.path_params.get("id")}))
            except KeyError:
                return _error(NO_ESTA[que], 404)
            except ErrorDatos as exc:
                return _error(f"No se ha guardado nada: {exc}.", 400)
            except YaEsReceta as exc:
                return _error(str(exc), 409, otra_tanda=True, receta_id=exc.receta_id)
            except ValueError as exc:
                return _error(f"No se ha guardado nada: {exc}.", 422)
        return ruta

    def borrar(que: str, hacer: Callable[[int, bool], dict]) -> Callable[[Request], Any]:
        async def ruta(request: Request) -> Response:
            if (await _cuerpo(request) or {}).get("confirmado") is not True:
                return _error("Hay que confirmar antes de borrar: no se ha borrado nada.", 400)
            try:
                return respuesta(que, hacer(request.path_params["id"], True))
            except KeyError:
                return _error(NO_ESTA[que], 404)
        return ruta

    def nueva_receta(datos: dict) -> dict:
        if datos.get("otra_tanda") is True:
            try:
                return recetario.crear_receta(datos, hoy())
            except YaEsReceta as exc:
                return recetario.otra_tanda(exc.receta_id, datos, hoy())
        return recetario.crear_receta(datos, hoy())

    def usar_version(datos: dict) -> dict:
        numero = datos.get("numero")
        if isinstance(numero, bool) or not isinstance(numero, int):
            raise ErrorDatos("di el número de la tanda")
        return recetario.usar_version(datos["_id"], numero)

    async def listado(request: Request) -> Response:
        return JSONResponse(recetario.listado())

    return [
        Route("/api/recetas", listado),
        Route("/api/recetas", ejecutar("receta", nueva_receta), methods=["POST"]),
        Route("/api/recetas/{id:int}", ejecutar("receta", lambda d: recetario.corregir(d["_id"], d)), methods=["POST"]),
        Route("/api/recetas/{id:int}/version", ejecutar("receta", usar_version), methods=["POST"]),
        Route("/api/recetas/{id:int}/borrar", borrar("receta", recetario.borrar_receta), methods=["POST"]),
        Route("/api/fijos", ejecutar("fijo", recetario.crear_fijo), methods=["POST"]),
        Route("/api/fijos/{id:int}", ejecutar("fijo", lambda d: recetario.cambiar_fijo(d["_id"], d)), methods=["POST"]),
        Route("/api/fijos/{id:int}/borrar", borrar("fijo", recetario.borrar_fijo), methods=["POST"]),
        Route("/api/ollas", ejecutar("olla", recetario.crear_olla), methods=["POST"]),
        Route("/api/ollas/{id:int}", ejecutar("olla", lambda d: recetario.cambiar_olla(d["_id"], d)), methods=["POST"]),
        Route("/api/ollas/{id:int}/borrar", borrar("olla", recetario.borrar_olla), methods=["POST"]),
    ]
