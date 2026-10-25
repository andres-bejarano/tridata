"""Starlette app: the day page and its JSON routes.

    GET  /                        the page (static HTML + JS modules + CSS)
    POST /api/interpretar         text -> every piece in it (foods, weight, objective of the day,
                                  doubtful phrases); saves NOTHING (R-1, R-31). Unit 016: optional `fecha`
                                  (a list of the queue: «hoy» = the day it was recorded; after today is 400)
    POST /api/dictado             raw audio (?id=<uuid>&fecha=AAAA-MM-DD) -> its text, transcribed on this PC,
                                  and the same interpretation /api/interpretar gives, with «hoy» = the day
                                  of the recording; saves NOTHING, keeps no audio (unit 006: R-1, R-10, R-12)
    GET  /sw.js                   the service worker, at the root so it serves the page offline (unit 006)
    POST /api/confirmar           reviewed pieces (at least one) -> each saved in its place, all or
                                  nothing; a doubtful phrase blocks it; returns today (R-4, R-5, R-32).
                                  Unit 016: optional `dia` of the list, where the objective of the day goes
                                  (after today or not a date: 400, nothing saved)
    POST /api/alimento            add a food by hand (no AI, no interpretation); body with `dia`; unit 019:
                                  or chosen from the saved ones (`guardado`, see below)
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
    POST /api/dia/objetivo        today or a future day: {"fecha"? (today by default), "porcentaje"?, "niveles"?,
                                  "previsto"?: {"actividad_id", "minutos"} | null}; changes `objetivo_dia` of that
                                  day, never the plan (R-16, R-18), and returns it recomputed. Unit 034: a past day
                                  or a date that is not one is 422 and changes nothing. Unit 021: also "pasos"?
                                  (0 to 50,000; null: the default steps of Ajustes)
    GET  /api/actividades-recientes  activities of the last 14 days, to choose the planned training
    POST /api/simular             {"alimentos": [...]} reviewed list -> margins and bar if eaten, and
                                  how much of each food fits; saves NOTHING (R-17, REC-2)
    POST /api/garmin/actualizar   brings today from Garmin with the `tridata sync` sync and returns
                                  today recomputed; 409 if one is running, 502 `motivo` "sesion"/"red",
                                  503 without Garmin credentials. Unit 018: it brings from the day of the last
                                  good sync (at most 45 days), writes down `ok_en` when it ends well, and a 429 is
                                  502 `motivo` "limite"; a failure is not written down (the routine does that)
    GET  /api/hoy                 unit 030 (REC-18): the four cards of Hoy in one answer: `comida` (eaten and what is
                                  left at the marked level, from the same day as /api/dia), `entreno` (today's
                                  activities), `como_llegas` (sleep, resting HR, HRV and its 7-day mean, TSB, each
                                  with its `estado`, `hoy.py`), `garmin_sync` (/api/dia's plus `texto_hora` and the
                                  routine's `motivos`) and `asistente` (unit 032: the beginning of the last answer,
                                  its conversation and title, R-97; null without any). Writes nothing new.
                                  Unit 030 (G-48): «Actualizar Garmin» also takes the routine's lock (`sync.lock`
                                  next to the database): with `tridata sync` running it is the same 409 «en_marcha»,
                                  and it brings at least yesterday and today
    GET  /api/fitness             unit 031 (REC-19): the Fitness tab in one answer (`fitness.py`): `carga` (CTL, ATL, TSB
                                  of today and the 14 days that end today, and `pulso`: where today's HR comes from),
                                  `ultima_actividad` and `recuperacion` (sleep, resting HR, HRV with their 7-day means,
                                  each with its `estado`). Every day with the HR in force that day in Ajustes (G-49)
    GET  /api/actividad/ID        unit 031: one activity with its laps (pace or speed, mean HR and its zone) and its
                                  weather; 404 if it does not exist
    GET  /api/plan*               unit 040 (REC-22): Fitness › Plan, the routes of `web/plan.py` (the plan of the
                                  current objective, its weeks, importing the .md and downloading it)
    GET  /ajustes                 the settings page
    GET  /api/ajustes             settings in force (?fecha=, default today), changes to come, races
    POST /api/ajustes             {"datos": {...}, "desde": "AAAA-MM-DD"} save one or more settings
                                  (`desde` defaults to today); says whether to ask for the limit. Unit 031: also
                                  «Pulso y zonas» (`fc_max`, `fc_reposo`, `zonas_fc`, `fc_origen`), checked together
    POST /api/carrera             add a race {"nombre", "fecha", "dias_mantenimiento"}
    POST /api/carrera/N           change a race (same body)
    POST /api/carrera/N/quitar    remove a race; only with {"confirmado": true}
    GET  /api/recetas             unit 011, the Recetas tab (web/recetas.py): recipes with their versions,
    POST /api/recetas[/N[/version|/borrar]]   fixed foods and pots; new recipe or batch, correct the one
    POST /api/fijos[/N[/borrar]]  in use, choose a batch, delete with {"confirmado": true}; add, change and
    POST /api/ollas[/N[/borrar]]  delete fixed foods and pots
    POST /api/recetas/N/apuntar   unit 013: {"cantidad": n} of that recipe or fixed food, logged today as eaten;
                                  unit 019: optional "unidad" ("receta": a fraction of the recipe)
    POST /api/fijos/N/apuntar     with the kcal worked out on the server with what is saved (R-57)
    POST /api/dia/cerrar          unit 014: «Cerrar el día» {"fecha"?} (today by default, or a past day still
                                  open with something logged; a future day or one with nothing is 422): what
                                  is planned becomes eaten and the food is closed; returns the day
    POST /api/aviso-cierre/N/visto  «Entendido» on the warning of a Garmin correction (marked seen, never
                                  deleted); returns the warnings still pending
    GET  /api/balance/semana      unit 017 (nutricion/balance.py): the week (Monday to Sunday) of ?fecha= (default
                                  today), its rows, sum, what is above the objective and the week of unit 014;
                                  `reparto_posible` (true or why not) only for the week of today
    GET  /api/balance             ?rango=4s|3m|todo: the bars of the balance (by day, or by week past 91 days),
                                  the weights and their 7-day means and the weight goal; another range is 400.
                                  Unit 018: or ?desde=AAAA-MM-DD&hasta=AAAA-MM-DD (both, without `rango`; desde ≤
                                  hasta ≤ today, else 400), the same answer from the first day with data on;
                                  `vacio` if those dates have no data. Every answer says `primer_dia`
    GET  /api/balance/semanas     every week from today's back to the first day with data, newest first
    POST /api/balance/repartir    {"aplicar": false} -> the proposal to spread what is above the objective, saving
                                  NOTHING; {"aplicar": true, "propuesta": {...}} recomputes it: if it changed, 409
                                  with the new one and nothing saved, else the % of each day goes to `objetivo_dia`
                                  marked `repartido`. Unit 018: only what is still pending is proposed
                                  (`ya_repartido` when everything is already spread)
    POST /api/balance/volver-al-plan  {"confirmado": true}: the days that are left lose the % set for them
                                  (also one set by hand), except the maintenance days before a race (unit 018);
                                  without it 400 and nothing changes
Every Balance route sends only totals (no food, no names) and computes each day ONCE per request with the
same `resumen_completo` as /api/dia. Unit 017 also adds `barras` (the 30 days, by day) to /api/peso.

Unit 016: every day the screen gets also says `hoy` (today on the server), so the page sees midnight pass.

Unit 021 (the assistant that acts, REC-12): /api/interpretar and /api/dictado also bring `cambios`, one result per
change Claude understood (`nutricion.asistente.proponer` with «hoy» = the day of the request): a card «Voy a …», a
choice between candidates or a `no` that says why; still nothing is saved (R-1). Confirming, correcting and
discarding a card are the routes of `web/asistente.py` (/api/asistente/*); /api/confirmar does not touch them.

Unit 032 (REC-20): the Asistente's conversations are the routes of `web/conversaciones.py` (/api/conversaciones*):
every conversation sees all the context of this PC (`conversaciones.contexto`), its answer arrives as it is written and
a change it proposes is a card of the 021. `conversador` (`ia.conversacion.ConversadorClaude`, or a test double),
`medicos_dir` (the folder of `TRIDATA_MEDICOS_DIR`) and `transcriptor_medico` are given by `tridata web`.

Unit 018 (R-91): today's day also brings `garmin_sync` = `{ok_en, fallo_en, fallo_motivo, aviso, texto}`
(`sync.aviso_sync` with the clock `ahora`); any other day brings it as null.

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

Unit 019 (R-93, R-57): a food of /api/alimento or /api/confirmar chosen from the saved ones carries
`guardado: {"tipo": "receta"|"fijo", "id": N}` with its `cantidad` and `unidad` ("g", "unidad", or "receta" for a
fraction of a recipe). It goes through `Recetario.para_apuntar` before anything else: its name, quantity, unit,
kcal, origin and provisional are the server's (never the page's kcal), not estimated nor doubtful; its day stays.
Gone is 422 «"…" ya no está guardado», one that cannot be worked out is 422 with why, a malformed one is 400, and
nothing is saved (in /api/confirmar, nothing of the list: every one is resolved before writing). A `guardado` that
is not an object (the name Claude matched, unit 011) is not a choice and is ignored as before.

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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from garminconnect import GarminConnectTooManyRequestsError

from ..garmin_client import GarminAuthError, GarminRateLimitError
from ..ia import ErrorInterpretacion, ErrorTranscripcion
from ..nutricion import (
    DESDE_SIEMPRE, Ajustes, Alimento, Cierres, ObjetivosDia, RegistroComidas, RegistroPeso, gramos_que_caben, kcal_previstas,
    objetivo_valido, peso_valido, Recetario, media_7_dias, simular,
)
from ..nutricion.balance import (
    NADA, SIN_DIAS, dias_que_quedan, lunes_de, reparto_de_la_semana, semana as semana_balance, semanas, serie,
    texto_reparto,
)
from ..nutricion.dia import FaltanAjustes, plan_del_dia, resumen_completo
from ..nutricion.margen import cabe_de_receta
from ..nutricion.asistente import Contexto as ContextoAsistente, Peticion, proponer as proponer_cambios
from ..nutricion.recetas import ErrorDatos, _numero
from ..fitness import detalle_actividad, fitness, texto_pulso
from ..hoy import como_llegas, entreno, texto_sync
from ..storage import DataStore
from ..sync import MOTIVOS, SyncEnMarcha, aviso_sync, cerrojo_sync
from ..conversaciones import Conversaciones
from ..conversaciones.contexto import Contexto as ContextoConversacion, montar as montar_contexto
from ..conversaciones.medicos import Medicos
from ..ia.conversacion import ConversadorClaude
from .asistente import rutas as rutas_asistente
from .conversaciones import rutas as rutas_conversaciones
from .recetas import rutas as rutas_recetas
from .plan import rutas as rutas_plan
from ..ia.plan import OrdenadorPlan
from ..plan import Planes

log = logging.getLogger(__name__)

ESTATICOS = Path(__file__).parent / "static"
NO_ESTA = "Ese alimento ya no está apuntado."
NO_ESTA_CARRERA = "Esa carrera ya no está en tus ajustes."
DIAS_RECIENTES = 14  # the planned training is chosen among the activities of the last 14 days (R-15)
DIAS_GRAFICA_PESO = 30  # the weight chart of the Peso tab (unit 010, R10)
RANGOS_BALANCE = {"4s": 28, "3m": 91, "todo": None}  # unit 017: the 28 or 91 days that end today, or all
FALTA_GARMIN = "Faltan GARMIN_EMAIL / GARMIN_PASSWORD en .env: no puedo traer Garmin."
# unit 016 (R6): a fixed text; the library's exception (it may carry the e-mail) never reaches the screen or the log
SESION_GARMIN = ("La sesión de Garmin ha fallado. Prueba otra vez y, si sigue, revisa GARMIN_EMAIL y "
                 "GARMIN_PASSWORD en .env.")
LIMITE_GARMIN = "Garmin está limitando las peticiones: espera unos minutos y vuelve a probar."  # unit 018 (R3)
EN_MARCHA = "Ya estoy actualizando Garmin: espera a que acabe."
# unit 030 (R-101): the button of Hoy says why with the words of the routine's warning (`sync.MOTIVOS`)
MOTIVOS_BOTON = {"sesion": MOTIVOS["sesion"], "limite": MOTIVOS["limite"], "red": MOTIVOS["conexion"]}
AL_REVES = "La fecha de inicio tiene que ser anterior o igual a la de fin"
AUDIO_MAX_BYTES = 10 * 1024 * 1024  # a 2-minute recording is far below this (R4)
TIPOS_AUDIO = frozenset({"audio/mp4", "audio/x-m4a", "audio/aac", "audio/webm", "audio/ogg", "audio/wav", "audio/mpeg"})
DICTADO_NO_DISPONIBLE = "El dictado no está disponible en el PC ahora mismo. Se reintentará más tarde."


def _error(mensaje: str, estado: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": mensaje, **extra}, status_code=estado)


def _dia_de_la_lista(valor: Any, hoy: date) -> date:
    """Unit 016 (R1, R2): the day a list was recorded or written; ValueError if it is not a date or is after today."""
    if not isinstance(valor, str):
        raise ValueError(f"el día de la lista no es una fecha: {valor!r}")
    try:
        dia = date.fromisoformat(valor)
    except ValueError:
        raise ValueError(f"el día de la lista no es una fecha: {valor!r}") from None
    if dia > hoy:
        raise ValueError("la lista es de un día que aún no ha llegado")
    return dia


def _fecha_ajustable(valor: Any, hoy: date) -> date | None:
    """Unit 034 (R4): the day whose objective may change, today or one that has not come yet; None otherwise."""
    if not isinstance(valor, str):
        return None
    try:
        fecha = date.fromisoformat(valor)
    except ValueError:
        return None
    return fecha if fecha >= hoy else None


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


class NoSeGuarda(Exception):
    """Unit 019: a chosen saved thing that cannot be logged; `estado` 400 (malformed) or 422, and why."""

    def __init__(self, mensaje: str, estado: int) -> None:
        super().__init__(mensaje)
        self.estado = estado


def _con_lo_guardado(recetario: Recetario, crudo: Any) -> Any:
    """Unit 019 (R-93, R-57): a food chosen from the saved ones with the server's kcal, origin and name.

    Anything without `guardado` as an object goes back as it came. Raises `NoSeGuarda`.
    """
    if not isinstance(crudo, dict) or not isinstance(crudo.get("guardado"), dict):
        return crudo
    elegido = crudo["guardado"]
    tipo, ident = elegido.get("tipo"), elegido.get("id")
    nombre = crudo.get("nombre") if isinstance(crudo.get("nombre"), str) and crudo["nombre"].strip() else "lo elegido"
    try:
        if tipo not in ("receta", "fijo") or isinstance(ident, bool) or not isinstance(ident, int):
            raise ErrorDatos(f"«{nombre}» no es una receta ni un ingrediente guardado")
        unidad = crudo.get("unidad")
        if unidad is not None and not isinstance(unidad, str):
            raise ErrorDatos(f"{nombre}: unidad no válida")
        datos = recetario.para_apuntar(tipo, ident, _numero(crudo, "cantidad", f"{nombre}: cantidad"), unidad or None)
    except KeyError:
        raise NoSeGuarda(f"«{nombre}» ya no está guardado: no se ha guardado nada.", 422) from None
    except ErrorDatos as exc:
        raise NoSeGuarda(f"{exc}: no se ha guardado nada.", 400) from None
    except ValueError as exc:
        raise NoSeGuarda(f"«{nombre}»: {exc}; no se ha guardado nada.", 422) from None
    return {**crudo, **datos, "estimado": False, "dudoso": False, "supuestos": [], "proteina_g": None,
            "hidratos_g": None, "grasa_g": None}


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
    ahora: Callable[[], datetime] = datetime.now, conversador: Any = None, medicos_dir: Path | None = None,
    transcriptor_medico: Any = None, ordenador_plan: Any = None,
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
    `ahora` is the clock of the Garmin warning and of the sync record (unit 018).
    Unit 032: `conversador` answers the Asistente's conversations (by default the Claude one, created lazily: nothing
    is called until a message is sent); `medicos_dir` is the folder of the medical PDFs (None: the context goes
    without them and says so) and `transcriptor_medico` reads each one once (by default the Claude one).
    Unit 040: `ordenador_plan` orders each section of an imported plan (by default the Claude one, created lazily);
    the plans live in the same SQLite (`plan.Planes`) and their routes are `web/plan.py`.
    """
    recetario = recetario or Recetario(registro.db_path)
    pesos = pesos or RegistroPeso(registro.db_path)
    objetivos = objetivos or ObjetivosDia(registro.db_path)
    cierres = cierres or Cierres(registro.db_path, ajustes=ajustes, pesos=pesos, objetivos=objetivos, store=store,
                                 registro=registro)
    cerrojo_garmin = threading.Lock()
    carpeta_datos = Path(registro.db_path).parent  # unit 030: where `tridata sync` keeps its lock (G-48)

    def _recientes() -> list[dict]:
        if store is None:
            return []
        dia_hoy = hoy()  # today's own activities already count as done: they are not offered
        return store.activities_between(dia_hoy - timedelta(days=DIAS_RECIENTES), dia_hoy - timedelta(days=1))

    # unit 021: what the assistant's actions read and change, the same repositories as the screens by hand
    asistente = ContextoAsistente(registro=registro, objetivos=objetivos, ajustes=ajustes, pesos=pesos,
                                  recetario=recetario, hoy=hoy, store=store, cierres=cierres, recientes=_recientes)

    # unit 032: the conversations, what they cost and the medical documents, in the same SQLite
    conversaciones = Conversaciones(registro.db_path)
    medicos = Medicos(registro.db_path, medicos_dir, transcriptor_medico,
                      al_gastar=lambda modelo, uso: conversaciones.apuntar_gasto(ahora(), modelo, uso, "transcripcion"))

    def _contexto_conversacion() -> ContextoConversacion:
        estado = medicos.al_dia()
        return montar_contexto(registro=registro, pesos=pesos, ajustes=ajustes, store=store,
                               transcripciones=medicos.transcripciones(), aviso_medico=estado["aviso"], hoy=hoy())

    def _dia(fecha: date) -> dict:
        """The day as the screen shows it: `nutricion.dia.resumen_completo` (unit 015), the same nutricion.md uses."""
        dia_hoy = hoy()
        resumen = resumen_completo(fecha, registro=registro, pesos=pesos, ajustes=ajustes, objetivos=objetivos,
                                   store=store, cierres=cierres, hoy=dia_hoy)
        # unit 018 (R-91): only today says whether Garmin is behind
        garmin_sync = (aviso_sync(store.estado_sync() if store is not None else None, ahora())
                       if fecha == dia_hoy else None)
        return {**resumen, "hoy": dia_hoy.isoformat(), "garmin_sync": garmin_sync}  # unit 016 (R3): midnight

    def _al_dia() -> None:
        """Today's screen first brings the days up to date (unit 014); a failure there never hides the day."""
        try:
            cierres.al_dia(hoy())
        except Exception:  # noqa: BLE001 - logged, and the day is shown as it is
            log.exception("could not bring the closed days up to date")

    # -- unit 017: Balance ------------------------------------------------------------------------

    def _resumenes() -> Callable[[date], dict | None]:
        """`fecha -> resumen_completo` (None without settings that day), each day computed ONCE per request."""
        hechos: dict[date, dict | None] = {}
        de_la_semana: dict = {}  # the week of unit 014 of each Monday, once per request (Cierres.bloque_del_dia)
        dia_hoy = hoy()

        def resumen(fecha: date) -> dict | None:
            if fecha not in hechos:
                try:
                    hechos[fecha] = resumen_completo(fecha, registro=registro, pesos=pesos, ajustes=ajustes,
                                                     objetivos=objetivos, store=store, cierres=cierres, hoy=dia_hoy,
                                                     semanas=de_la_semana)
                except FaltanAjustes:
                    hechos[fecha] = None
            return hechos[fecha]

        return resumen

    def _primer_dia() -> date | None:
        """The first day with something eaten or a weight (where «Todo» starts)."""
        candidatos = [d for d in (registro.primer_dia(),) if d is not None]
        primer_peso = pesos.entre(DESDE_SIEMPRE, hoy())
        if primer_peso:
            candidatos.append(date.fromisoformat(primer_peso[0]["dia"]))
        return min(candidatos) if candidatos else None

    def _propuesta(resumen: Callable[[date], dict | None], s: dict) -> dict:
        dia_hoy = hoy()
        propuesta = reparto_de_la_semana(s, dias_que_quedan(dia_hoy, s, ajustes),
                                         ajustes.vigentes(dia_hoy).limite_saludable_pct)
        return {**propuesta, "desde": s["desde"], "texto": texto_reparto(propuesta)}

    def _vista_semana(fecha: date, resumen: Callable[[date], dict | None] | None = None) -> dict:
        resumen = resumen or _resumenes()
        dia_hoy = hoy()
        s = semana_balance(fecha, resumen=resumen)
        es_actual = s["desde"] == lunes_de(dia_hoy).isoformat()
        primer = _primer_dia()
        vista = {**s, "hoy": dia_hoy.isoformat(), "es_actual": es_actual, "vacio": primer is None,
                 "primer_dia": primer.isoformat() if primer else None, "reparto_posible": None,
                 "volver_al_plan": False}
        if es_actual:
            propuesta = _propuesta(resumen, s)
            # unit 018: with everything already spread the button stays, and says so when pressed
            vista["reparto_posible"] = True if propuesta["motivo"] in (None, "ya_repartido") else (
                NADA if propuesta["motivo"] == "nada_que_repartir" else SIN_DIAS)
            quedan = {q["dia"] for q in dias_que_quedan(dia_hoy, s, ajustes) if not q["mantenimiento"]}
            vista["volver_al_plan"] = any(f["dia"] in quedan and f.get("objetivo_origen") == "dia" for f in s["filas"])
        return vista

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
        try:  # unit 028 (R8): SQLite may wait for the lock; never inside the app's loop
            await run_in_threadpool(cierres.cerrar_comida, fecha, "boton")
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
        barras = (await run_in_threadpool(serie, desde, hasta, resumen=_resumenes(), pesos=[], hoy=hoy()))["barras"]
        return JSONResponse({
            "hasta": hasta.isoformat(), "dias": DIAS_GRAFICA_PESO,
            "pesos": [{"dia": p["dia"], "kg": p["kg"], "nota": p["nota"]} for p in todos if p["dia"] >= desde.isoformat()],
            "medias": medias,
            "objetivo": {"kg": meta.kg, "fecha": meta.fecha.isoformat()} if meta is not None else None,
            "barras": barras,  # unit 017 (R12): the balance of each of those days, for the chart
        })

    async def balance_semana(request: Request) -> Response:
        texto = request.query_params.get("fecha")
        try:
            fecha = date.fromisoformat(texto) if texto else hoy()
        except ValueError:
            return _error(f"Fecha no válida: {texto!r}", 400)
        return JSONResponse(await run_in_threadpool(_vista_semana, fecha))

    def _serie(rango: str | None, fechas: tuple[date, date] | None = None) -> dict:
        """The chart of a range that ends today (`rango`) or between two dates (`fechas`, unit 018)."""
        dia_hoy = hoy()
        primer = _primer_dia()
        objetivo = ajustes.vigentes(dia_hoy).objetivo_peso
        meta = {"kg": objetivo.kg, "fecha": objetivo.fecha.isoformat()} if objetivo else None
        hasta = fechas[1] if fechas else dia_hoy
        vacio: dict[str, Any] = {
            "rango": rango, "vacio": True, "desde": fechas[0].isoformat() if fechas else None,
            "hasta": hasta.isoformat(), "agrupado": "dia", "barras": [], "pesos": [], "medias": [],
            "objetivo": meta, "primer_dia": primer.isoformat() if primer else None}
        if primer is None or hasta < primer:
            return vacio
        if fechas:
            desde = max(primer, fechas[0])
        else:
            dias = RANGOS_BALANCE[rango or "todo"]
            desde = primer if dias is None else max(primer, dia_hoy - timedelta(days=dias - 1))
        datos = serie(desde, hasta, resumen=_resumenes(), pesos=pesos.entre(desde - timedelta(days=6), hasta),
                      hoy=dia_hoy)
        if fechas and not datos["barras"] and not datos["pesos"]:
            return {**vacio, "desde": desde.isoformat()}
        return {"rango": rango, "vacio": False, **datos, "objetivo": meta, "primer_dia": primer.isoformat()}

    def _fechas_del_grafico(desde_texto: str | None, hasta_texto: str | None) -> tuple[date, date]:
        """Unit 018 (R8): both dates, well written, desde ≤ hasta ≤ today; ValueError says what is wrong."""
        if not desde_texto or not hasta_texto:
            raise ValueError("Faltan las dos fechas: desde y hasta.")
        try:
            desde, hasta = date.fromisoformat(desde_texto), date.fromisoformat(hasta_texto)
        except ValueError:
            raise ValueError(f"Fecha no válida: {desde_texto!r} o {hasta_texto!r}.") from None
        if desde > hasta:
            raise ValueError(f"{AL_REVES}.")
        if hasta > hoy():
            raise ValueError("La fecha de fin no puede ser después de hoy.")
        return desde, hasta

    async def balance_serie(request: Request) -> Response:
        rango = request.query_params.get("rango")
        desde, hasta = request.query_params.get("desde"), request.query_params.get("hasta")
        if desde is not None or hasta is not None:
            if rango is not None:
                return _error("O un rango o dos fechas, no las dos cosas.", 400)
            try:
                fechas = _fechas_del_grafico(desde, hasta)
            except ValueError as exc:
                return _error(str(exc), 400)
            return JSONResponse(await run_in_threadpool(_serie, None, fechas))
        if rango not in RANGOS_BALANCE:
            return _error(f"Rango no válido: {rango!r} (4s, 3m o todo).", 400)
        return JSONResponse(await run_in_threadpool(_serie, rango))

    def _semanas() -> dict:
        dia_hoy = hoy()
        primer = _primer_dia()
        if primer is None:
            return {"hoy": dia_hoy.isoformat(), "semanas": []}
        return {"hoy": dia_hoy.isoformat(),
                "semanas": semanas(primer, dia_hoy, resumen=_resumenes(), pesos=pesos.entre(primer, dia_hoy))}

    async def balance_semanas(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(_semanas))

    async def balance_repartir(request: Request) -> Response:
        """Propose (saves nothing) or apply the proposal Andrés looked at, only if it is still the same (R-20)."""
        cuerpo = await _cuerpo(request) or {}
        dia_hoy = hoy()
        resumen = _resumenes()
        propuesta = _propuesta(resumen, semana_balance(dia_hoy, resumen=resumen))
        if cuerpo.get("aplicar") is not True:
            return JSONResponse({"propuesta": propuesta})
        enviada = cuerpo.get("propuesta")
        if not isinstance(enviada, dict):
            return _error("Falta la propuesta que has mirado: no se ha cambiado nada.", 400)
        if enviada != propuesta:
            return _error("La semana ha cambiado desde la propuesta: mírala otra vez. No se ha cambiado nada.", 409,
                          propuesta=propuesta)
        if not propuesta["dias"]:
            return _error(f"No se ha cambiado nada: {propuesta['texto']}.", 422, propuesta=propuesta)
        for x in propuesta["dias"]:  # unit 018: marked as coming from the spread (R11)
            objetivos.cambiar(date.fromisoformat(x["dia"]), porcentaje=x["nuevo_pct"], repartido=True)
        return JSONResponse({"semana": _vista_semana(dia_hoy), "dia": _dia(dia_hoy)})

    async def balance_volver(request: Request) -> Response:
        if (await _cuerpo(request) or {}).get("confirmado") is not True:
            return _error("Hay que confirmar antes de volver al plan: no se ha cambiado nada.", 400)
        dia_hoy = hoy()
        s = semana_balance(dia_hoy, resumen=_resumenes())
        for q in dias_que_quedan(dia_hoy, s, ajustes):
            if q["mantenimiento"]:  # unit 018 (R10): the days before a race stay as they are
                continue
            dia_q = date.fromisoformat(q["dia"])
            actual = objetivos.del_dia(dia_q)
            if actual is not None and actual.get("porcentaje") is not None:
                objetivos.cambiar(dia_q, porcentaje=None)
        return JSONResponse({"semana": _vista_semana(dia_hoy), "dia": _dia(dia_hoy)})

    async def _interpretacion(texto: str, dia: date) -> dict:
        """What /api/interpretar answers for `texto` with «hoy» = `dia`. Raises ErrorInterpretacion.

        Unit 021: plus `cambios`, the cards of what Claude understood as changes, proposed with the day `dia`."""
        resultado = await run_in_threadpool(interprete.interpretar, texto.strip(), dia)
        piezas = _piezas(resultado, recetario.resolver)
        cambios = getattr(resultado, "cambios", None) or []
        tarjetas = await run_in_threadpool(proponer_cambios, list(cambios), asistente, Peticion(texto=texto, fecha=dia))
        return {"texto": texto, **piezas, "cambios": tarjetas}

    async def interpretar(request: Request) -> Response:
        cuerpo = await _cuerpo(request) or {}
        texto = cuerpo.get("texto")
        if not isinstance(texto, str) or not texto.strip():
            return _error("Escribe lo que has comido o vas a comer.", 400, texto=texto or "")
        dia = hoy()
        if cuerpo.get("fecha") is not None:  # unit 016 (R2): a list of the queue, with the day it was recorded
            try:
                dia = _dia_de_la_lista(cuerpo["fecha"], dia)
            except ValueError as exc:
                return _error(f"No he interpretado nada: {exc}.", 400, texto=texto)
        try:
            return JSONResponse(await _interpretacion(texto, dia))
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
        dia_lista = dia_hoy  # unit 016 (R1): the objective of the day goes to the day of the list
        if cuerpo.get("dia") is not None:
            try:
                dia_lista = _dia_de_la_lista(cuerpo["dia"], dia_hoy)
            except ValueError as exc:
                return _error(f"No se ha guardado nada: {exc}.", 400)
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
        try:  # unit 019: every chosen one is worked out before anything is written
            crudos = [_con_lo_guardado(recetario, a) for a in crudos]
        except NoSeGuarda as exc:
            return _error(str(exc), exc.estado)
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
        objetivo_guardado = objetivos.guardar(dia_lista, objetivo) if objetivo is not None else None
        return JSONResponse({"guardados": guardados, "peso": peso_guardado, "objetivo_dia": objetivo_guardado,
                             "dia": _dia(dia_hoy)})

    async def anadir(request: Request) -> Response:
        try:  # unit 019: chosen from the saved ones, the kcal are the server's
            cuerpo = _con_lo_guardado(recetario, await _cuerpo(request) or {})
        except NoSeGuarda as exc:
            return _error(str(exc), exc.estado)
        # unit 028 (R6): estimated kcal without a quantity do not count here either (R-8); it looks at the
        # `estimado` that came, before it is set to False
        cuerpo = _sin_estimacion_suelta(cuerpo)
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

    async def seccion(request: Request) -> Response:
        """Unit 035 (R-123, G-52): only the section of a food changes; the day goes back recomputed."""
        cuerpo = await _cuerpo(request)
        if cuerpo is None or "seccion" not in cuerpo:
            return _error("No se ha cambiado nada: falta la sección a la que va el alimento.", 422)
        try:
            movido = registro.mover_seccion(request.path_params["id"], cuerpo["seccion"])
        except KeyError:
            return _error(f"No se ha cambiado nada: {NO_ESTA[0].lower()}{NO_ESTA[1:]}", 404)
        except ValueError as exc:
            return _error(f"No se ha cambiado nada: {exc}.", 422)
        return JSONResponse({"alimento": movido, "dia": _dia(date.fromisoformat(movido["dia"]))})

    async def cambiar_objetivo(request: Request) -> Response:
        """The %, levels and planned training of today or a future day (unit 034); checked first, so a bad value
        changes nothing (R6)."""
        cuerpo = await _cuerpo(request)
        if not cuerpo:
            return _error("No hay nada que cambiar en el objetivo de hoy.", 400)
        campos = dict(cuerpo)
        dia_hoy = hoy()
        fecha = _fecha_ajustable(campos.pop("fecha"), dia_hoy) if "fecha" in campos else dia_hoy
        if fecha is None:  # unit 034 (R4): a past day or a date that is not one
            return _error("No se ha cambiado nada: solo se puede ajustar hoy o un día que aún no ha llegado.", 422)
        if not campos:
            return _error("No hay nada que cambiar en el objetivo del día.", 400)
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
            guardado = objetivos.cambiar(fecha, **campos)
        except ValueError as exc:
            return _error(f"No se ha cambiado nada: {exc}.", 400)
        return JSONResponse({"objetivo_dia": guardado, "dia": _dia(fecha)})

    async def actividades_recientes(request: Request) -> Response:
        recientes = sorted(_recientes(), key=lambda a: (a.get("activity_date") or "", a.get("activity_id") or ""),
                           reverse=True)
        return JSONResponse({"dias": DIAS_RECIENTES, "actividades": [_actividad_corta(a) for a in recientes]})

    def _kcal_100g_de_receta(crudo: dict, nombre: str) -> float | None:
        """The kcal per 100 g of the saved recipe of a card: the one chosen (`guardado: {tipo, id}`) or, dictated,
        the one with that name; None if it is not a saved recipe or it is not weighed."""
        elegido = crudo.get("guardado")
        ident = elegido.get("id") if isinstance(elegido, dict) and elegido.get("tipo") == "receta" else None
        if isinstance(ident, bool) or not isinstance(ident, int):
            objetivo = recetario._objetivo(nombre)
            if objetivo is None or objetivo[0] != "receta":
                return None
            ident = objetivo[1]["id"]
        try:
            return recetario.para_apuntar("receta", ident, 100, "g")["kcal"]
        except (KeyError, ValueError):
            return None

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
        for i, a in enumerate(alimentos):
            if a.cantidad is None or a.kcal is None:
                caben.append({"nombre": a.nombre, "cantidad": None, "unidad": a.unidad,
                              "texto": "sin cantidad o kcal: no se puede calcular"})
                continue
            if a.unidad == "receta":  # bug 024: a fraction of a recipe, said in grams and kcal (or kcal)
                cabe = cabe_de_receta(margen, a.nombre, _kcal_100g_de_receta(crudos[i], a.nombre))
                caben.append({"nombre": a.nombre, **cabe})
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
            return _error(EN_MARCHA, 409, motivo="en_marcha")
        try:
            with cerrojo_sync(carpeta_datos):  # unit 030 (G-48): the routine's `tridata sync` neither
                await run_in_threadpool(actualizar_garmin, hoy())
            if store is not None:  # unit 018 (R-90): a good one is written down; a failure, never here
                store.marcar_sync_ok(ahora())
        except SyncEnMarcha:  # unit 030 (R-103): the routine of the morning is syncing
            return _error(EN_MARCHA, 409, motivo="en_marcha")
        except (GarminRateLimitError, GarminConnectTooManyRequestsError):  # unit 018 (R3): a 429, said as such
            return _error(LIMITE_GARMIN, 502, motivo="limite")
        except GarminAuthError:  # its text is not shown nor logged (R6 of unit 016)
            return _error(SESION_GARMIN, 502, motivo="sesion")
        except Exception:  # network, Garmin down, anything else: said, never hidden
            return _error("No se ha podido conectar con Garmin. Inténtalo más tarde.", 502, motivo="red")
        finally:
            cerrojo_garmin.release()
        return JSONResponse({"dia": _dia(hoy())})

    def _hoy() -> dict:
        """Unit 030: the four cards. Comida is today's `_dia` (the same figures as Nutrición › Día)."""
        dia_hoy = hoy()
        resumen = _dia(dia_hoy)
        marcado = next((m for m in resumen["margenes"] if m.get("objetivo")), None) or next(
            (m for m in resumen["margenes"] if m["nivel"] == resumen["objetivo"]["porcentaje"]), resumen["margenes"][0])
        comida = {"total_kcal": resumen["total_kcal"], "nivel": marcado["nivel"], "queda_kcal": marcado["kcal"],
                  "objetivo_kcal": resumen["barra"]["objetivo_kcal"], "pct": resumen["barra"]["pct"],
                  "planeado_kcal": resumen["planeado_kcal"]}
        garmin_sync = {**resumen["garmin_sync"], "texto_hora": texto_sync(resumen["garmin_sync"]["ok_en"], ahora()),
                       "motivos": MOTIVOS_BOTON}
        sin_garmin = {"sueno": {"estado": "no_ha_llegado", "segundos": None, "puntuacion": None},
                      "pulso_reposo": {"estado": "no_ha_llegado", "valor": None},
                      "hrv": {"estado": "no_ha_llegado", "valor": None, "media_7d": None},
                      "frescura": {"estado": "no_ha_llegado", "valor": None}}
        return {"fecha": dia_hoy.isoformat(), "comida": comida,
                "entreno": entreno(store, dia_hoy) if store is not None else [],
                # unit 031 (R8): the TSB with the HR of each day in Ajustes, the same as Fitness
                "como_llegas": (como_llegas(store, dia_hoy, limites=ajustes.historial_fc().limites)
                                if store is not None else sin_garmin),
                "garmin_sync": garmin_sync, "asistente": conversaciones.ultima_respuesta()}  # unit 032 (R-97)

    async def ver_hoy(request: Request) -> Response:
        await run_in_threadpool(_al_dia)
        return JSONResponse(await run_in_threadpool(_hoy))

    def _fitness() -> dict:
        """Unit 031: the three blocks of Fitness; without Garmin data, an empty store's answer (nothing invented)."""
        historial = ajustes.historial_fc()
        if store is None:
            falta = {"estado": "no_ha_llegado", "valor": None, "media_7d": None}
            return {"fecha": hoy().isoformat(), "ultima_actividad": None,
                    "carga": {"hoy": None, "serie": [], "pulso": texto_pulso(historial.vigente(hoy()))},
                    "recuperacion": {"sueno": {"estado": "no_ha_llegado", "segundos": None, "puntuacion": None,
                                               "media_7d": None}, "pulso_reposo": falta, "hrv": dict(falta)}}
        return fitness(store, historial, hoy())

    async def ver_fitness(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(_fitness))

    async def ver_actividad(request: Request) -> Response:
        if store is None:
            return _error("Esa actividad no está.", 404)
        detalle = await run_in_threadpool(detalle_actividad, store, ajustes.historial_fc(), request.path_params["id"])
        if detalle is None:
            return _error("Esa actividad no está.", 404)
        return JSONResponse(detalle)

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
        Route("/api/balance", balance_serie),
        Route("/api/balance/semana", balance_semana),
        Route("/api/balance/semanas", balance_semanas),
        Route("/api/balance/repartir", balance_repartir, methods=["POST"]),
        Route("/api/balance/volver-al-plan", balance_volver, methods=["POST"]),
        Route("/api/actividades-recientes", actividades_recientes),
        Route("/api/simular", simular_lista, methods=["POST"]),
        Route("/api/garmin/actualizar", actualizar, methods=["POST"]),
        Route("/api/hoy", ver_hoy),
        Route("/api/fitness", ver_fitness),
        Route("/api/actividad/{id}", ver_actividad),
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
        Route("/api/alimento/{id:int}/seccion", seccion, methods=["POST"]),
        *rutas_recetas(recetario, hoy, registro),
        *rutas_asistente(asistente, _dia),
        *rutas_conversaciones(conversaciones, conversador if conversador is not None else ConversadorClaude(), medicos,
                              _contexto_conversacion, asistente, hoy, ahora, transcriptor),
        *rutas_plan(Planes(registro.db_path), store, ajustes,
                    ordenador_plan if ordenador_plan is not None else OrdenadorPlan(), conversaciones, hoy, ahora),
        Mount("/static", StaticFiles(directory=ESTATICOS), name="static"),
    ])
