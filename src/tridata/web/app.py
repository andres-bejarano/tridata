"""Starlette app: the day page and its JSON routes.

    GET  /                        the page (static HTML + JS modules + CSS)
    POST /api/interpretar         text -> every piece in it (foods, weight, objective of the day,
                                  doubtful phrases); saves NOTHING (R-1, R-31)
    POST /api/dictado             raw audio (?id=<uuid>&fecha=AAAA-MM-DD) -> its text, transcribed on this PC,
                                  and the same interpretation /api/interpretar gives, with «hoy» = the day
                                  of the recording; saves NOTHING, keeps no audio (unit 006: R-1, R-10, R-12)
    GET  /sw.js                   the service worker, at the root so it serves the page offline (unit 006)
    POST /api/confirmar           reviewed pieces (at least one) -> each saved in its place, all or
                                  nothing; a doubtful phrase blocks it; returns today (R-4, R-5, R-32)
    POST /api/alimento            add a food by hand (no AI, no interpretation); body with `dia`
    POST /api/alimento/N          change a saved food (name, quantity, unit, kcal; `dia` optional
                                  moves it, with the planned/eaten rule)
    POST /api/alimento/N/borrar   delete a saved food; only with {"confirmado": true} (R-11)
    POST /api/alimento/N/comido   mark a planned food as eaten (today or a past day only)
    GET  /api/dia                 the day table, total, expenditure, margins and plan (?fecha=AAAA-MM-DD),
                                  all with the settings in force THAT day, plus the weight block (weight,
                                  7-day mean, basal and projection) and the objective of the day.
                                  Unit 008: the expenditure with Garmin of that day and the planned
                                  training, with its breakdown and warnings; the objective (% of the
                                  day or of the plan, days of delay) and the bar with the healthy limit.
                                  Unit 010: `planeado_kcal` (planned foods with quantity and kcal) and
                                  `con_planeado`, the margins and bar if they were eaten (`simular`)
    GET  /api/peso                the weights of the 30 days up to ?hasta=AAAA-MM-DD (default today), the
                                  7-day mean of each of those days that has one and the weight goal
                                  (unit 010); writes nothing
    POST /api/dia/objetivo        today only: {"porcentaje"?, "niveles"?, "previsto"?: {"actividad_id",
                                  "minutos"} | null}; changes `objetivo_dia`, never the plan (R-16, R-18)
    GET  /api/actividades-recientes  activities of the last 14 days, to choose the planned training
    POST /api/simular             {"alimentos": [...]} reviewed list -> margins and bar if eaten, and
                                  how much of each food fits; saves NOTHING (R-17, REC-2)
    POST /api/garmin/actualizar   brings today from Garmin with the `tridata sync` sync and returns
                                  today recomputed; 409 if one is running, 502 `motivo` "sesion"/"red",
                                  503 without Garmin credentials
    GET  /ajustes                 the settings page
    GET  /api/ajustes             settings in force (?fecha=, default today), changes to come, races
    POST /api/ajustes             {"datos": {...}, "desde": "AAAA-MM-DD"} save one or more settings
                                  (`desde` defaults to today); says whether to ask for the limit
    POST /api/carrera             add a race {"nombre", "fecha", "dias_mantenimiento"}
    POST /api/carrera/N           change a race (same body)
    POST /api/carrera/N/quitar    remove a race; only with {"confirmado": true}
    GET  /api/recetas             unit 011, the Recetas tab (web/recetas.py): recipes with their versions,
    POST /api/recetas[/N[/version|/borrar]]   fixed foods and pots; new recipe or batch, correct the one
    POST /api/fijos[/N[/borrar]]  in use, choose a batch, delete with {"confirmado": true}; add, change and
    POST /api/ollas[/N[/borrar]]  delete fixed foods and pots
    POST /api/recetas/N/apuntar   unit 013: {"cantidad": n} of that recipe or fixed food, logged today as eaten
    POST /api/fijos/N/apuntar     with the kcal worked out on the server with what is saved (R-57)
    POST /api/dia/cerrar          unit 014: «Cerrar el día» {"fecha"?} (today by default, or a past day still
                                  open with something logged; a future day or one with nothing is 422): what
                                  is planned becomes eaten and the food is closed; returns the day
    POST /api/aviso-cierre/N/visto  «Entendido» on the warning of a Garmin correction (marked seen, never
                                  deleted); returns the warnings still pending

Unit 014: today's GET /api/dia first brings the days up to date (`Cierres.al_dia`: closes the food of the past
days, closes with Garmin those it can, looks at the corrections and recalibrates the kcal per step). Every
/api/dia brings `cierre` (state, balance, trail and week, `Cierres.bloque_del_dia`), `avisos_cierre` and, in
`gasto`, `origen` ("garmin" | "estimado"), `estimado_kcal`, `real_kcal` and `calibracion`: a day closed with
Garmin computes its margins, objective and bar with the real expenditure (`gasto.kcal`).

Unit 013 (R-8): a food without a quantity but with kcal counts («kcal a mano»); only one with neither is
pending. An interpreted food without a quantity whose kcal are estimated goes out with `kcal: null`, so an
estimate never passes for hand-written kcal.

Unit 011: every food /api/interpretar and /api/dictado give back has gone through the saved recipes
and fixed foods (`Recetario.resolver`): `origen` («de tu receta …»), `provisional`, `propuesta` (what
was said and the saved one proposed, when it is only alike) and `kcal_estimada` (Claude's, to go
back to it). /api/confirmar keeps `origen` and `provisional` in the row, and /api/dia shows them.

Every /api/alimento route answers {"alimento": ..., "dia": ...}, where `dia` is the day the food
was in BEFORE the action (the screen being looked at; for an added food, its day).
404 if the food does not exist, 400 malformed data, 422 a business rule. A settings value that
is not valid is a 400 and nothing is saved. A day with no basal or kcal per step is a 409.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..garmin_client import GarminAuthError
from ..ia import ErrorInterpretacion, ErrorTranscripcion
from ..nutricion import (
    Ajustes, Alimento, Cierres, ObjetivosDia, RegistroComidas, RegistroPeso, gramos_que_caben, kcal_previstas,
    objetivo_valido, peso_valido, Recetario, media_7_dias, simular,
)
from ..nutricion.dia import FaltanAjustes, plan_del_dia, resumen_completo
from ..storage import DataStore
from .recetas import rutas as rutas_recetas

log = logging.getLogger(__name__)

ESTATICOS = Path(__file__).parent / "static"
NO_ESTA = "Ese alimento ya no está apuntado."
NO_ESTA_CARRERA = "Esa carrera ya no está en tus ajustes."
DIAS_RECIENTES = 14  # the planned training is chosen among the activities of the last 14 days (R-15)
DIAS_GRAFICA_PESO = 30  # the weight chart of the Peso tab (unit 010, R10)
FALTA_GARMIN = "Faltan GARMIN_EMAIL / GARMIN_PASSWORD en .env: no puedo traer Garmin."
AUDIO_MAX_BYTES = 10 * 1024 * 1024  # a 2-minute recording is far below this (R4)
TIPOS_AUDIO = frozenset({"audio/mp4", "audio/x-m4a", "audio/aac", "audio/webm", "audio/ogg", "audio/wav", "audio/mpeg"})
DICTADO_NO_DISPONIBLE = "El dictado no está disponible en el PC ahora mismo. Se reintentará más tarde."


def _error(mensaje: str, estado: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": mensaje, **extra}, status_code=estado)


async def _cuerpo(request: Request) -> dict | None:
    try:
        datos = await request.json()
    except (ValueError, json.JSONDecodeError):
        return None
    return datos if isinstance(datos, dict) else None


_NUEVAS = ("guardado", "coincidencia", "origen", "provisional", "propuesta", "kcal_estimada")


def _sin_lo_vacio(alimento: dict) -> dict:
    """A food nothing saved touched goes out exactly as before unit 011: without its new, empty keys."""
    if alimento.get("origen") or alimento.get("propuesta") or alimento.get("provisional"):
        return alimento
    return {k: v for k, v in alimento.items() if k not in _NUEVAS or v not in (None, False)}


def _sin_estimacion_suelta(alimento: dict) -> dict:
    """R-8 (unit 013): estimated kcal without a quantity are not kcal by hand: the food stays pending."""
    if alimento.get("cantidad") is None and alimento.get("estimado") and alimento.get("kcal") is not None:
        return {**alimento, "kcal": None}
    return alimento


def _piezas(resultado: Any, resolver: Callable[[dict], dict] = lambda a: a) -> dict:
    """The interpretation as JSON: every piece, empty when absent. A bare list is foods only.

    Each food goes through `resolver` (the saved recipes and fixed foods, unit 011).
    """
    if isinstance(resultado, list):
        alimentos, peso, objetivo, dudosas = resultado, None, None, []
    else:
        alimentos, peso, objetivo, dudosas = (resultado.alimentos, resultado.peso, resultado.objetivo_dia,
                                              resultado.dudosas)
    return {
        "alimentos": [_sin_estimacion_suelta(_sin_lo_vacio(resolver(a.model_dump(mode="json")))) for a in alimentos],
        "peso": peso.model_dump(mode="json") if peso is not None else None,
        "objetivo_dia": objetivo.model_dump(mode="json") if objetivo is not None else None,
        "dudosas": list(dudosas),
    }


def _actividad_corta(a: dict) -> dict:
    return {"actividad_id": a.get("activity_id"), "fecha": a.get("activity_date"),
            "nombre": a.get("name") or a.get("activity_type"), "tipo": a.get("activity_type"),
            "minutos": round(float(a.get("duration_seconds") or 0) / 60, 1), "kcal": a.get("calories")}


def crear_app(
    registro: RegistroComidas, interprete: Any, ajustes: Ajustes, hoy: Callable[[], date] = date.today, *,
    pesos: RegistroPeso | None = None, objetivos: ObjetivosDia | None = None,
    store: DataStore | None = None, actualizar_garmin: Callable[[date], None] | None = None,
    transcriptor: Any = None, recetario: Recetario | None = None, cierres: Cierres | None = None,
) -> Starlette:
    """`interprete` is anything with `interpretar(texto, hoy)` (the Claude one, or a test double).

    Every day is computed with `ajustes.vigentes(that day)`: no expenditure config is fixed at start.
    Weights and objectives of the day live in the same SQLite as `registro` unless given.
    `store` is the Garmin data (`DataStore`); without it every day has no Garmin data.
    `actualizar_garmin(dia)` brings that day from Garmin (the «Actualizar Garmin» button); without
    it the button says the Garmin credentials are missing.
    `transcriptor` is anything with `transcribir(audio) -> str` (Whisper, or a test double); with a
    `cargar()` too, it is loaded in a background thread when the server starts, never blocking it.
    Without it, /api/dictado says «try again later» (503) and writing works as always.
    `recetario` holds the recipes, fixed foods and pots (unit 011); by default, in the same SQLite.
    `cierres` is the close of the day (unit 014); by default, in the same SQLite with these settings and `store`.
    """
    recetario = recetario or Recetario(registro.db_path)
    pesos = pesos or RegistroPeso(registro.db_path)
    objetivos = objetivos or ObjetivosDia(registro.db_path)
    cierres = cierres or Cierres(registro.db_path, ajustes=ajustes, pesos=pesos, objetivos=objetivos, store=store,
                                 registro=registro)
    cerrojo_garmin = threading.Lock()

    def _recientes() -> list[dict]:
        if store is None:
            return []
        dia_hoy = hoy()  # today's own activities already count as done: they are not offered
        return store.activities_between(dia_hoy - timedelta(days=DIAS_RECIENTES), dia_hoy - timedelta(days=1))

    def _dia(fecha: date) -> dict:
        """The day as the screen shows it: `nutricion.dia.resumen_completo` (unit 015), the same nutricion.md uses."""
        return resumen_completo(fecha, registro=registro, pesos=pesos, ajustes=ajustes, objetivos=objetivos,
                                store=store, cierres=cierres, hoy=hoy())

    def _al_dia() -> None:
        """Today's screen first brings the days up to date (unit 014); a failure there never hides the day."""
        try:
            cierres.al_dia(hoy())
        except Exception:  # noqa: BLE001 - logged, and the day is shown as it is
            log.exception("could not bring the closed days up to date")

    def _vista_ajustes(fecha: date) -> dict:
        vigentes = ajustes.vigentes(fecha)
        return {
            "fecha": fecha.isoformat(),
            "vigentes": ajustes.valores_con_fecha(fecha),
            "futuros": ajustes.cambios_futuros(fecha),
            "carreras": [c.como_dict() for c in vigentes.carreras],
            "plan": plan_del_dia(vigentes),
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
        if fecha == hoy():
            await run_in_threadpool(_al_dia)
        return JSONResponse(_dia(fecha))

    async def cerrar_dia(request: Request) -> Response:
        """«Cerrar el día» (R-24): today or a past day still open; never a future one. Nothing else changes."""
        texto = (await _cuerpo(request) or {}).get("fecha")
        dia_hoy = hoy()
        try:
            fecha = date.fromisoformat(texto) if texto else dia_hoy
        except (TypeError, ValueError):
            return _error(f"Fecha no válida: {texto!r}", 400)
        if fecha > dia_hoy:
            return _error("Ese día aún no ha llegado: no se puede cerrar.", 422)
        try:
            cierres.cerrar_comida(fecha, "boton")
        except ValueError as exc:
            return _error(f"No se ha cerrado: {exc}.", 422)
        if fecha < dia_hoy:  # a past day may already have its whole day in Garmin
            await run_in_threadpool(_al_dia)
        return JSONResponse({"dia": _dia(fecha)})

    async def aviso_visto(request: Request) -> Response:
        try:
            cierres.marcar_visto(request.path_params["id"])
        except KeyError:
            return _error("Ese aviso ya no está.", 404)
        return JSONResponse({"avisos_cierre": cierres.avisos_pendientes()})

    async def peso(request: Request) -> Response:
        texto = request.query_params.get("hasta")
        try:
            hasta = date.fromisoformat(texto) if texto else hoy()
        except ValueError:
            return _error(f"Fecha no válida: {texto!r}", 400)
        desde = hasta - timedelta(days=DIAS_GRAFICA_PESO - 1)
        # the 6 days before the window too, so its first days have their 7-day mean
        todos = pesos.entre(desde - timedelta(days=6), hasta)
        medias = []
        for i in range(DIAS_GRAFICA_PESO):
            dia_i = desde + timedelta(days=i)
            media = media_7_dias(todos, dia_i)
            if media is not None:
                medias.append({"dia": dia_i.isoformat(), **media.como_dict()})
        meta = ajustes.vigentes(hasta).objetivo_peso
        return JSONResponse({
            "hasta": hasta.isoformat(), "dias": DIAS_GRAFICA_PESO,
            "pesos": [{"dia": p["dia"], "kg": p["kg"], "nota": p["nota"]} for p in todos if p["dia"] >= desde.isoformat()],
            "medias": medias,
            "objetivo": {"kg": meta.kg, "fecha": meta.fecha.isoformat()} if meta is not None else None,
        })

    async def _interpretacion(texto: str, dia: date) -> dict:
        """What /api/interpretar answers for `texto` with «hoy» = `dia`. Raises ErrorInterpretacion."""
        resultado = await run_in_threadpool(interprete.interpretar, texto.strip(), dia)
        return {"texto": texto, **_piezas(resultado, recetario.resolver)}

    async def interpretar(request: Request) -> Response:
        texto = (await _cuerpo(request) or {}).get("texto")
        if not isinstance(texto, str) or not texto.strip():
            return _error("Escribe lo que has comido o vas a comer.", 400, texto=texto or "")
        try:
            return JSONResponse(await _interpretacion(texto, hoy()))
        except ErrorInterpretacion as exc:
            return _error(f"No he podido interpretar lo que has escrito ({exc}). Inténtalo de nuevo.", 502,
                          texto=texto)

    async def dictado(request: Request) -> Response:
        """A recording -> its text and its interpretation. Nothing is saved and the audio stays in memory.

        Every error says `reintentar`: the phone keeps the recording and tries again only when True.
        """
        id_grabacion = request.query_params.get("id")
        texto_fecha = request.query_params.get("fecha")
        try:
            fecha = date.fromisoformat(texto_fecha) if texto_fecha else hoy()
        except ValueError:
            return _error(f"Fecha de la grabación no válida: {texto_fecha!r}", 400, reintentar=False)
        tipo = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if tipo not in TIPOS_AUDIO:
            return _error("Eso no es una grabación de audio.", 415, reintentar=False)
        largo = request.headers.get("content-length", "")
        if largo.isdigit() and int(largo) > AUDIO_MAX_BYTES:
            return _error("La grabación pasa de 10 MB.", 413, reintentar=False)
        trozos = bytearray()
        async for trozo in request.stream():
            trozos.extend(trozo)
            if len(trozos) > AUDIO_MAX_BYTES:
                return _error("La grabación pasa de 10 MB.", 413, reintentar=False)
        audio = bytes(trozos)
        if not audio:
            return _error("La grabación está vacía.", 422, reintentar=False)
        if transcriptor is None:
            return _error(DICTADO_NO_DISPONIBLE, 503, reintentar=True)
        try:
            texto = await run_in_threadpool(transcriptor.transcribir, audio)
        except ErrorTranscripcion as exc:
            if exc.motivo == "vacio":
                return _error("No he oído nada en la grabación.", 422, reintentar=False)
            if exc.motivo == "audio_no_valido":
                return _error("No se ha podido transcribir la grabación.", 422, reintentar=False)
            return _error(DICTADO_NO_DISPONIBLE, 503, reintentar=True)
        try:
            interpretacion = await _interpretacion(texto, fecha)
        except ErrorInterpretacion as exc:
            return _error(f"He entendido lo que has dicho, pero no he podido interpretarlo ({exc}). "
                          "Revísalo y pulsa «Interpretar».", 502, reintentar=False, texto=texto)
        return JSONResponse({"id": id_grabacion, "fecha": fecha.isoformat(), "texto": texto,
                             "interpretacion": interpretacion})

    async def service_worker(request: Request) -> Response:
        return FileResponse(ESTATICOS / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    async def confirmar(request: Request) -> Response:
        """Validate EVERY piece first; only if all are valid is anything written (R11)."""
        cuerpo = await _cuerpo(request) or {}
        dia_hoy = hoy()
        crudos = cuerpo.get("alimentos")
        if crudos is None:
            crudos = []
        if not isinstance(crudos, list):
            return _error("Los alimentos tienen que ser una lista.", 400)
        dudosas = cuerpo.get("dudosas") or []
        if not isinstance(dudosas, list):
            return _error("Las frases dudosas tienen que ser una lista.", 400)
        if dudosas:
            frases = ", ".join(f"«{d}»" for d in dudosas)
            return _error(f"No sé dónde poner {frases}: quítalo antes de confirmar. No se ha guardado nada.", 422)
        try:
            alimentos = [Alimento.desde_dict(a) for a in crudos]
        except ValueError as exc:
            return _error(f"No se ha guardado nada: {exc}.", 400)
        peso = None
        if cuerpo.get("peso") is not None:
            try:
                peso = peso_valido(cuerpo["peso"], dia_hoy)
            except ValueError as exc:
                return _error(f"No se ha guardado nada: {exc}.", 422)
        objetivo = None
        if cuerpo.get("objetivo_dia") is not None:
            try:
                objetivo = objetivo_valido(cuerpo["objetivo_dia"])
            except ValueError as exc:
                return _error(f"No se ha guardado nada: {exc}.", 400)
        if not alimentos and peso is None and objetivo is None:
            return _error("No hay nada que guardar.", 400)
        try:
            registro.validar(alimentos)
        except ValueError as exc:
            return _error(f"No se ha guardado nada: {exc}.", 422)

        texto = cuerpo.get("texto")
        guardados = registro.guardar(alimentos, hoy=dia_hoy, texto=texto if isinstance(texto, str) else "",
                                     sin_cambios=bool(cuerpo.get("sin_cambios")))
        peso_guardado = pesos.apuntar(*peso, hoy=dia_hoy) if peso is not None else None
        objetivo_guardado = objetivos.guardar(dia_hoy, objetivo) if objetivo is not None else None
        return JSONResponse({"guardados": guardados, "peso": peso_guardado, "objetivo_dia": objetivo_guardado,
                             "dia": _dia(dia_hoy)})

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

    async def cambiar_objetivo(request: Request) -> Response:
        """Today's %, levels and planned training; checked first, so a bad value changes nothing (R6)."""
        cuerpo = await _cuerpo(request)
        if not cuerpo:
            return _error("No hay nada que cambiar en el objetivo de hoy.", 400)
        campos = dict(cuerpo)
        if isinstance(campos.get("previsto"), dict):
            elegido = campos["previsto"]
            actividad = next((a for a in _recientes() if a.get("activity_id") == elegido.get("actividad_id")), None)
            if actividad is None:
                return _error(f"No se ha cambiado nada: solo puedes elegir un entreno de los últimos "
                              f"{DIAS_RECIENTES} días.", 400)
            try:
                kcal = kcal_previstas(actividad, elegido.get("minutos"))
            except ValueError as exc:
                return _error(f"No se ha cambiado nada: {exc}.", 400)
            campos["previsto"] = {**_actividad_corta(actividad), "minutos": elegido["minutos"], "kcal": kcal}
        try:
            guardado = objetivos.cambiar(hoy(), **campos)
        except ValueError as exc:
            return _error(f"No se ha cambiado nada: {exc}.", 400)
        return JSONResponse({"objetivo_dia": guardado, "dia": _dia(hoy())})

    async def actividades_recientes(request: Request) -> Response:
        recientes = sorted(_recientes(), key=lambda a: (a.get("activity_date") or "", a.get("activity_id") or ""),
                           reverse=True)
        return JSONResponse({"dias": DIAS_RECIENTES, "actividades": [_actividad_corta(a) for a in recientes]})

    async def simular_lista(request: Request) -> Response:
        """The reviewed list, not yet confirmed: how the day would be if eaten. Writes nothing (REC-2)."""
        crudos = (await _cuerpo(request) or {}).get("alimentos")
        if not isinstance(crudos, list):
            return _error("Los alimentos tienen que ser una lista.", 400)
        try:
            alimentos = [Alimento.desde_dict(a) for a in crudos]
        except ValueError as exc:
            return _error(f"No se puede simular: {exc}.", 400)
        dia_hoy = hoy()
        resumen = _dia(dia_hoy)
        cuenta = [a for a in alimentos if a.dia == dia_hoy and not a.pendiente_cantidad and a.kcal is not None]
        kcal_lista = round(sum(a.kcal or 0 for a in cuenta), 1)
        margen = resumen["barra"]["objetivo_kcal"] - resumen["total_kcal"]
        caben = []
        for a in alimentos:
            if a.cantidad is None or a.kcal is None:
                caben.append({"nombre": a.nombre, "cantidad": None, "unidad": a.unidad,
                              "texto": "sin cantidad o kcal: no se puede calcular"})
                continue
            cabe = gramos_que_caben(margen, a.kcal, a.cantidad)
            unidad = a.unidad or "g"
            texto = cabe["texto"] if not cabe["cantidad"] else f"te caben {cabe['cantidad']} {unidad} de {a.nombre}"
            caben.append({"nombre": a.nombre, "cantidad": cabe["cantidad"], "unidad": unidad, "texto": texto})
        return JSONResponse({"kcal_lista": kcal_lista, **simular(resumen, kcal_lista), "caben": caben,
                             "antes": {"margenes": resumen["margenes"], "barra": resumen["barra"]}})

    async def actualizar(request: Request) -> Response:
        """«Actualizar Garmin»: one at a time; on any failure the saved data stay as they were (R13)."""
        if actualizar_garmin is None:
            return _error(FALTA_GARMIN, 503, motivo="credenciales")
        if not cerrojo_garmin.acquire(blocking=False):
            return _error("Ya estoy actualizando Garmin: espera a que acabe.", 409, motivo="en_marcha")
        try:
            await run_in_threadpool(actualizar_garmin, hoy())
        except GarminAuthError as exc:
            return _error(f"La sesión de Garmin ha fallado: {exc}", 502, motivo="sesion")
        except Exception:  # network, Garmin down, anything else: said, never hidden
            return _error("No se ha podido conectar con Garmin. Inténtalo más tarde.", 502, motivo="red")
        finally:
            cerrojo_garmin.release()
        return JSONResponse({"dia": _dia(hoy())})

    def _cargar_transcriptor() -> None:
        try:
            transcriptor.cargar()
        except ErrorTranscripcion:
            log.warning("dictation disabled until the next request: Whisper could not be loaded")

    @asynccontextmanager
    async def ciclo(app: Starlette) -> AsyncIterator[None]:
        if transcriptor is not None and callable(getattr(transcriptor, "cargar", None)):
            # once, in the background: the server answers (and writing works) while the model loads
            threading.Thread(target=_cargar_transcriptor, name="carga-whisper", daemon=True).start()
        yield

    return Starlette(exception_handlers={FaltanAjustes: faltan_ajustes}, lifespan=ciclo, routes=[
        Route("/", pagina),
        Route("/sw.js", service_worker),
        Route("/ajustes", pagina_ajustes),
        Route("/api/dia", dia),
        Route("/api/dia/objetivo", cambiar_objetivo, methods=["POST"]),
        Route("/api/dia/cerrar", cerrar_dia, methods=["POST"]),
        Route("/api/aviso-cierre/{id:int}/visto", aviso_visto, methods=["POST"]),
        Route("/api/peso", peso),
        Route("/api/actividades-recientes", actividades_recientes),
        Route("/api/simular", simular_lista, methods=["POST"]),
        Route("/api/garmin/actualizar", actualizar, methods=["POST"]),
        Route("/api/ajustes", ver_ajustes),
        Route("/api/ajustes", guardar_ajustes, methods=["POST"]),
        Route("/api/carrera", anadir_carrera, methods=["POST"]),
        Route("/api/carrera/{id:int}", cambiar_carrera, methods=["POST"]),
        Route("/api/carrera/{id:int}/quitar", quitar_carrera, methods=["POST"]),
        Route("/api/interpretar", interpretar, methods=["POST"]),
        Route("/api/dictado", dictado, methods=["POST"]),
        Route("/api/confirmar", confirmar, methods=["POST"]),
        Route("/api/alimento", anadir, methods=["POST"]),
        Route("/api/alimento/{id:int}", cambiar, methods=["POST"]),
        Route("/api/alimento/{id:int}/borrar", borrar, methods=["POST"]),
        Route("/api/alimento/{id:int}/comido", comido, methods=["POST"]),
        *rutas_recetas(recetario, hoy, registro),
        Mount("/static", StaticFiles(directory=ESTATICOS), name="static"),
    ])
