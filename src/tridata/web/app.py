"""Starlette app: the day page and its JSON routes.

    GET  /                        the page (static HTML + JS modules + CSS)
    GET  /sw.js                   the service worker, at the root so it serves the page offline (unit 006)
    POST /api/alimento            add a food by hand (no AI, no interpretation); body with `dia`; unit 019:
                                  or chosen from the saved ones (`guardado`, see below)
    POST /api/alimento/N          change a saved food (name, quantity, unit, kcal; `dia` optional
                                  moves it, with the planned/eaten rule); unit 074: or into one chosen
                                  from the saved ones (`guardado`, see below), same row and section
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
    POST /api/peso/AAAA-MM-DD     unit 080 (R-223): {"kg"} changes the weight of that day, only if it HAS one (404
                                  otherwise: no weight is written down here, S-74); its note stays. A value that is
                                  not a weight is 422 and nothing changes. Answers {"peso", "resumen"}: the weight
                                  block of that day (7-day mean, basal, projection) recomputed
    GET  /api/coach               unit 080 (R-224): «Lo que ha hecho el coach», the last 20 changes made from the PC
                                  (the conversation «Desde el PC»), newest first; read only
    POST /api/dia/objetivo        today or a future day: {"fecha"? (today by default), "porcentaje"?, "niveles"?,
                                  "previsto"?: {"actividad_id", "minutos"} | null}; changes `objetivo_dia` of that
                                  day, never the plan (R-16, R-18), and returns it recomputed. Unit 034: a past day
                                  or a date that is not one is 422 and changes nothing. Unit 021: also "pasos"?
                                  (0 to 50,000; null: the default steps of Ajustes). Unit 047: several planned
                                  trainings a day (at most 10), one of these at a time: "previsto_anadir":
                                  {"actividad_id", "minutos"} adds one, "previsto_cambiar": {"posicion", "minutos",
                                  "actividad_id"?} changes that one, "previsto_quitar": posicion removes it (positions
                                  from 0 in `previstos`); "previsto" (object: only that one; null: none) as before
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
                                  routine's `motivos`). Unit 080: without the card of the Asistente. Writes nothing new.
                                  Unit 030 (G-48): «Actualizar Garmin» also takes the routine's lock (`sync.lock`
                                  next to the database): with `tridata sync` running it is the same 409 «en_marcha»,
                                  and it brings at least yesterday and today
    GET  /api/fitness             unit 031 (REC-19): the Fitness tab in one answer (`fitness.py`): `carga` (CTL, ATL, TSB
                                  of today and the 14 days that end today, and `pulso`: where today's HR comes from),
                                  `ultima_actividad` and `recuperacion` (sleep, resting HR, HRV with their 7-day means,
                                  each with its `estado`). Every day with the HR in force that day in Ajustes (G-49)
    GET  /api/actividad/ID        unit 031: one activity with its laps (pace or speed, mean HR and its zone) and its
                                  weather; 404 if it does not exist
    POST /api/toma                unit 082 (R-227): a dose by hand {"dia", "que" (cafe|b12), "forma"?, "hora"?, "mg"?,
                                  "solo"?, "gramos"?, "fraccion"?, "cucharadas"?, "dosis"?}; today without an hour
                                  takes the hour it is now (R-231), another day without one is 422. Its mg are by
                                  hand (not estimated). Answers {"toma", "dia"}; no kcal change (G-60)
    POST /api/toma/N              unit 082: changes its hour, form, details, mg or «solo»; 404 / 422, nothing changes
    DELETE /api/toma/N            unit 082: deletes it, only with {"confirmado": true} (400 otherwise, nothing deleted)
    POST /api/dia/AAAA-MM-DD/hora unit 082 (R-229): {"seccion", "hora" (HH:MM, or null: the typical one again)}: the
                                  hour of that section that day; kcal and margin untouched. Every day (/api/dia and
                                  the answers above) carries `suplementacion` (the doses by hour with their share of
                                  the milk, `cafeina_mg`, `ultima`, `leche`) and `horas` (each section's hour, S-79)
    GET  /api/plan*               unit 040 (REC-22): Fitness › Plan, the routes of `web/plan.py` (the plan of the
                                  current objective, its weeks and downloading the .md; unit 080: importing it is
                                  the coach's)
    GET  /api/calendario*         unit 043 (REC-27): Ajustes › Google Calendar, the routes of `web/calendario.py`
                                  (connect once; every planned session of the plan in course, an all-day event of
                                  «TriData»); the pass also runs at start and every 10 minutes, in its own thread,
                                  and (unit 062) right after a good answer of what changes the plan or «Actualizar
                                  Garmin»; POST /api/calendario/desconectar withdraws the permission
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

Unit 080 (P-20261105-cf32bea5, S-5): the app calls no AI. Gone are the box to write or dictate (/api/interpretar,
/api/dictado, /api/confirmar), the cards of the assistant (/api/asistente/*), the Asistente's conversations
(/api/conversaciones*, /api/equipo, /api/rutas) and importing a plan (/api/plan/importar, /api/plan/confirmar): all
that is asked to the coach (Claude Code on the PC, `mcp_server.py`), which uses the same mechanisms underneath. The
conversations, cards and transcriptions already saved stay in the base (R10). The card of the watch («Mandar al
reloj») is confirmed or discarded with the routes of `web/plan.py`.

Unit 018 (R-91): today's day also brings `garmin_sync` = `{ok_en, fallo_en, fallo_motivo, aviso, texto}`
(`sync.aviso_sync` with the clock `ahora`); any other day brings it as null.

Unit 014: today's GET /api/dia first brings the days up to date (`Cierres.al_dia`: closes the food of the past
days, closes with Garmin those it can, looks at the corrections and recalibrates the kcal per step). Every
/api/dia brings `cierre` (state, balance, trail and week, `Cierres.bloque_del_dia`), `avisos_cierre` and, in
`gasto`, `origen` ("garmin" | "estimado"), `estimado_kcal`, `real_kcal` and `calibracion`: a day closed with
Garmin computes its margins, objective and bar with the real expenditure (`gasto.kcal`).

Unit 013 (R-8): a food without a quantity but with kcal counts («kcal a mano»); only one with neither is
pending. A food sent with estimated kcal and no quantity loses those kcal, so an estimate never passes for
hand-written kcal.

Unit 011: a food that comes from a saved recipe or fixed food keeps `origen` («de tu receta …») and `provisional`
in its row, and /api/dia shows them.

Unit 019 (R-93, R-57): a food of /api/alimento or /api/alimento/N (unit 074) chosen from the saved ones carries
`guardado: {"tipo": "receta"|"fijo", "id": N}` with its `cantidad` and `unidad` ("g", "unidad", or "receta" for a
fraction of a recipe). It goes through `Recetario.para_apuntar` before anything else: its name,
quantity, unit, kcal, origin and provisional are the server's (never the page's kcal), not estimated nor doubtful;
its day stays (and, in a change, its row and section).
Gone is 422 «"…" ya no está guardado», one that cannot be worked out is 422 with why, a malformed one is 400, and
nothing is saved. A `guardado` that is not an object is not a choice and is ignored as before.

Every /api/alimento route answers {"alimento": ..., "dia": ...}, where `dia` is the day the food
was in BEFORE the action (the screen being looked at; for an added food, its day).
404 if the food does not exist, 400 malformed data, 422 a business rule. A settings value that
is not valid is a 400 and nothing is saved. A day with no basal or kcal per step is a 409.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from garminconnect import GarminConnectTooManyRequestsError

from ..garmin_client import GarminAuthError, GarminRateLimitError
from ..nutricion import (
    DESDE_SIEMPRE, Ajustes, Alimento, Cierres, ObjetivosDia, RegistroComidas, RegistroPeso, gramos_que_caben, kcal_previstas,
    Recetario, media_7_dias, simular,
)
from ..nutricion.balance import (
    NADA, SIN_DIAS, dias_que_quedan, lunes_de, reparto_de_la_semana, semana as semana_balance, semanas, serie,
    texto_reparto,
)
from ..nutricion.dia import FaltanAjustes, plan_del_dia, resumen_completo
from ..nutricion.margen import cabe_de_receta
from ..nutricion.objetivo_dia import PREVISTOS_MAX
from ..nutricion.asistente import Contexto as ContextoAsistente
from ..nutricion.recetas import ErrorDatos, _numero
from ..nutricion.suplementacion import Suplementacion
from ..fitness import detalle_actividad, fitness, texto_pulso
from ..hoy import como_llegas, entreno, texto_sync
from ..storage import DataStore
from ..sync import MOTIVOS, SyncEnMarcha, aviso_sync, cerrojo_sync
from ..conversaciones import Conversaciones
from .conversaciones import rutas_coach
from .recetas import rutas as rutas_recetas
from .plan import rutas as rutas_plan
from ..plan import Planes
from ..calendario import Calendario
from .calendario import Despertar, Sincronizacion, rutas as rutas_calendario
from .origen import Origen

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
SIN_PESO = "Ese día no tiene peso apuntado: no se ha cambiado nada. Un peso nuevo se le dice al coach."  # unit 080


def _error(mensaje: str, estado: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": mensaje, **extra}, status_code=estado)


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
    transcriptor_medico: Any = None, ordenador_plan: Any = None, calendario: Calendario | None = None,
) -> Starlette:
    """Unit 080 (R6): the app calls no AI. `interprete`, `transcriptor`, `conversador`, `medicos_dir`,
    `transcriptor_medico` and `ordenador_plan` are still accepted, so the callers of before keep working, and are
    IGNORED: nothing is interpreted, transcribed or loaded, and no AI client is created.

    Every day is computed with `ajustes.vigentes(that day)`: no expenditure config is fixed at start.
    Weights and objectives of the day live in the same SQLite as `registro` unless given.
    `store` is the Garmin data (`DataStore`); without it every day has no Garmin data.
    `actualizar_garmin(dia)` brings that day from Garmin (the «Actualizar Garmin» button); without
    it the button says the Garmin credentials are missing.
    `recetario` holds the recipes, fixed foods and pots (unit 011); by default, in the same SQLite.
    `cierres` is the close of the day (unit 014); by default, in the same SQLite with these settings and `store`.
    `ahora` is the clock of the Garmin warning and of the sync record (unit 018).
    Unit 040: the plans live in the same SQLite (`plan.Planes`) and their routes are `web/plan.py`.
    Unit 043: `calendario` is Google Calendar (by default the token next to the database, `google-token.json`, and the
    client's JSON where TRIDATA_GOOGLE_CLIENTE says); without a token nothing talks to Google.
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

    # unit 021: what the cards read and change, the same repositories as the screens by hand; unit 080: only the card
    # of the watch is confirmed in the app now (`web/plan.py`); the coach makes the rest from the PC
    planes = Planes(registro.db_path)
    # unit 043: the sessions of the plan in Google Calendar, a pass at start and every 10 minutes (`ciclo`)
    if calendario is None:
        cliente = os.environ.get("TRIDATA_GOOGLE_CLIENTE", "").strip()
        calendario = Calendario(registro.db_path, carpeta_datos / "google-token.json",
                                str(Path(cliente).expanduser()) if cliente else None)
    sincronizacion = Sincronizacion(calendario, planes, hoy, ahora, store)
    asistente = ContextoAsistente(registro=registro, objetivos=objetivos, ajustes=ajustes, pesos=pesos,
                                  recetario=recetario, hoy=hoy, store=store, cierres=cierres, recientes=_recientes,
                                  planes=planes)

    # unit 080 (R-224): the conversation «Desde el PC», only read (what the coach has done)
    conversaciones = Conversaciones(registro.db_path)
    # unit 082 (REC-34): the doses of the day, the milk of the coffees and the hours of the sections, apart from the foods
    suplementacion = Suplementacion(registro.db_path)

    def _dia(fecha: date) -> dict:
        """The day as the screen shows it: `nutricion.dia.resumen_completo` (unit 015), the same nutricion.md uses."""
        dia_hoy = hoy()
        resumen = resumen_completo(fecha, registro=registro, pesos=pesos, ajustes=ajustes, objetivos=objetivos,
                                   store=store, cierres=cierres, hoy=dia_hoy)
        # unit 018 (R-91): only today says whether Garmin is behind
        garmin_sync = (aviso_sync(store.estado_sync() if store is not None else None, ahora())
                       if fecha == dia_hoy else None)
        return {**resumen, "hoy": dia_hoy.isoformat(), "garmin_sync": garmin_sync,  # unit 016 (R3): midnight
                "suplementacion": suplementacion.resumen(fecha), "horas": suplementacion.horas_del_dia(fecha)}

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

    async def cambiar_peso(request: Request) -> Response:
        """Unit 080 (R-223, C-276, C-277): another value for a weight already written down; never a new one (S-74)."""
        try:
            fecha = date.fromisoformat(request.path_params["dia"])
        except ValueError:
            return _error(f"Fecha no válida: {request.path_params['dia']!r}", 400)
        cuerpo = await _cuerpo(request)
        if cuerpo is None or "kg" not in cuerpo:
            return _error("No se ha cambiado nada: falta el peso (kg).", 400)
        try:
            cambiado = pesos.cambiar(fecha, cuerpo["kg"])
        except KeyError:
            return _error(SIN_PESO, 404)
        except ValueError as exc:
            return _error(f"No se ha cambiado nada: {exc}.", 422)
        resumen = await run_in_threadpool(_dia, fecha)
        return JSONResponse({"peso": cambiado, "resumen": resumen["peso"]})

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

    async def service_worker(request: Request) -> Response:
        return FileResponse(ESTATICOS / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

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
        # unit 074 (R-213): a saved recipe or ingredient chosen on the change card, worked out as in the add
        de_lo_guardado = isinstance(cuerpo, dict) and isinstance(cuerpo.get("guardado"), dict)
        try:
            cuerpo = _con_lo_guardado(recetario, cuerpo)
        except NoSeGuarda as exc:
            return _error(str(exc), exc.estado)
        try:
            Alimento.desde_dict({**cuerpo, "dia": cuerpo.get("dia") or hoy().isoformat()})
        except ValueError as exc:
            return _error(str(exc), 400)
        try:
            origen = registro.alimento(request.path_params["id"])
            cambiado = registro.cambiar(request.path_params["id"], cuerpo, hoy=hoy(), de_lo_guardado=de_lo_guardado)
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

    # -- unit 082: the block «Suplementación» and the hour of each section ---------------------------------------------

    async def anadir_toma(request: Request) -> Response:
        cuerpo = await _cuerpo(request) or {}
        try:
            fecha = date.fromisoformat(str(cuerpo.get("dia")))
        except ValueError:
            return _error(f"No se ha apuntado nada: día {cuerpo.get('dia')!r} no válido.", 400)
        datos = {k: v for k, v in cuerpo.items() if k != "dia"}
        if datos.get("mg") not in (None, ""):
            datos["mg_estimado"] = False  # by hand
        try:
            toma = suplementacion.apuntar(fecha, datos, hoy=hoy(), ahora=ahora())
        except ValueError as exc:
            return _error(f"No se ha apuntado nada: {exc}.", 422)
        return JSONResponse({"toma": toma, "dia": _dia(fecha)})

    async def cambiar_toma(request: Request) -> Response:
        cuerpo = {k: v for k, v in (await _cuerpo(request) or {}).items() if k not in ("dia", "que", "mg_estimado")}
        try:
            toma = suplementacion.cambiar(request.path_params["id"], cuerpo)
        except KeyError:
            return _error("Esa toma ya no está.", 404)
        except ValueError as exc:
            return _error(f"No se ha cambiado nada: {exc}.", 422)
        return JSONResponse({"toma": toma, "dia": _dia(date.fromisoformat(toma["dia"]))})

    async def borrar_toma(request: Request) -> Response:
        if (await _cuerpo(request) or {}).get("confirmado") is not True:
            return _error("Hay que confirmar antes de borrar: no se ha borrado nada.", 400)
        try:
            toma = suplementacion.borrar(request.path_params["id"])
        except KeyError:
            return _error("Esa toma ya no está.", 404)
        return JSONResponse({"toma": toma, "dia": _dia(date.fromisoformat(toma["dia"]))})

    async def hora_seccion(request: Request) -> Response:
        texto = request.path_params["dia"]
        try:
            fecha = date.fromisoformat(texto)
        except ValueError:
            return _error(f"Fecha no válida: {texto!r}", 400)
        cuerpo = await _cuerpo(request) or {}
        try:
            suplementacion.poner_hora_seccion(fecha, cuerpo.get("seccion"), cuerpo.get("hora"))
        except ValueError as exc:
            return _error(f"No se ha cambiado nada: {exc}.", 422)
        return JSONResponse({"dia": _dia(fecha)})

    def _previsto_elegido(actividad_id: Any, minutos: Any) -> dict:
        """A planned training like one of the last 14 days, its kcal scaled to `minutos`. `ValueError` otherwise."""
        actividad = next((a for a in _recientes() if a.get("activity_id") == actividad_id), None)
        if actividad is None:
            raise ValueError(f"solo puedes elegir un entreno de los últimos {DIAS_RECIENTES} días")
        kcal = kcal_previstas(actividad, minutos)
        return {**_actividad_corta(actividad), "minutos": minutos, "kcal": kcal}

    def _posicion(valor: Any, lista: list) -> int:
        if isinstance(valor, bool) or not isinstance(valor, int) or not 0 <= valor < len(lista):
            raise ValueError(f"no hay entreno previsto en la posición {valor!r}")
        return valor

    def _previstos_por_posicion(fecha: date, campos: dict) -> None:
        """Unit 047: `previsto_anadir`, `previsto_cambiar` or `previsto_quitar` -> the whole list in `previstos`.

        One at a time (and not with the old `previsto`); `ValueError` says what is wrong and changes nothing."""
        if "previstos" in campos:  # their kcal are always computed here, never taken from the body
            raise ValueError("los entrenos previstos se añaden, cambian o quitan de uno en uno")
        claves = [c for c in ("previsto", "previsto_anadir", "previsto_cambiar", "previsto_quitar") if c in campos]
        if len(claves) > 1:
            raise ValueError("los entrenos previstos se cambian de uno en uno")
        if not claves or claves[0] == "previsto":
            return
        clave = claves[0]
        valor = campos.pop(clave)
        lista = list((objetivos.del_dia(fecha) or {}).get("previstos") or [])
        if clave == "previsto_quitar":
            lista.pop(_posicion(valor, lista))
        elif not isinstance(valor, dict):
            raise ValueError("el entreno previsto tiene que ser un objeto")
        elif clave == "previsto_anadir":
            if len(lista) >= PREVISTOS_MAX:
                raise ValueError(f"como mucho {PREVISTOS_MAX} entrenos previstos en un día")
            lista.append(_previsto_elegido(valor.get("actividad_id"), valor.get("minutos")))
        else:
            i = _posicion(valor.get("posicion"), lista)
            actual = lista[i]
            otra = valor.get("actividad_id")
            if otra is not None and otra != actual.get("actividad_id"):
                lista[i] = _previsto_elegido(otra, valor.get("minutos"))
            elif any(a.get("activity_id") == actual.get("actividad_id") for a in _recientes()):
                lista[i] = _previsto_elegido(actual.get("actividad_id"), valor.get("minutos"))
            else:  # its activity is older than 14 days by now: scaled from what was saved
                guardada = {"duration_seconds": float(actual["minutos"]) * 60, "calories": actual["kcal"]}
                lista[i] = {**actual, "minutos": valor.get("minutos"),
                            "kcal": kcal_previstas(guardada, valor.get("minutos"))}
        campos["previstos"] = lista

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
        try:
            if isinstance(campos.get("previsto"), dict):
                campos["previsto"] = _previsto_elegido(campos["previsto"].get("actividad_id"),
                                                       campos["previsto"].get("minutos"))
            _previstos_por_posicion(fecha, campos)
        except ValueError as exc:
            return _error(f"No se ha cambiado nada: {exc}.", 400)
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
                "garmin_sync": garmin_sync}  # unit 080 (S-27): no card of the Asistente

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

    @asynccontextmanager
    async def ciclo(app: Starlette) -> AsyncIterator[None]:
        parar_calendario = threading.Event()  # unit 043: Google never makes the app wait (R4)
        threading.Thread(target=sincronizacion.en_segundo_plano, args=(parar_calendario,), name="google-calendar",
                         daemon=True).start()
        yield
        parar_calendario.set()
        sincronizacion.pronto()  # unit 062: wakes the thread so it sees `parar`

    # bug 078: `Origen` goes the first, so that what does not come from the app itself reaches nothing (`web/origen.py`)
    # unit 062 (R1): what changes the plan wakes the calendar's thread (`web/calendario.py::Despertar`)
    return Starlette(exception_handlers={FaltanAjustes: faltan_ajustes}, lifespan=ciclo,
                     middleware=[Middleware(Origen), Middleware(Despertar, sinc=sincronizacion)], routes=[
        Route("/", pagina),
        Route("/sw.js", service_worker),
        Route("/ajustes", pagina_ajustes),
        Route("/api/dia", dia),
        Route("/api/dia/objetivo", cambiar_objetivo, methods=["POST"]),
        Route("/api/dia/cerrar", cerrar_dia, methods=["POST"]),
        Route("/api/aviso-cierre/{id:int}/visto", aviso_visto, methods=["POST"]),
        Route("/api/peso", peso),
        Route("/api/peso/{dia}", cambiar_peso, methods=["POST"]),
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
        Route("/api/alimento", anadir, methods=["POST"]),
        Route("/api/alimento/{id:int}", cambiar, methods=["POST"]),
        Route("/api/alimento/{id:int}/borrar", borrar, methods=["POST"]),
        Route("/api/alimento/{id:int}/comido", comido, methods=["POST"]),
        Route("/api/alimento/{id:int}/seccion", seccion, methods=["POST"]),
        Route("/api/toma", anadir_toma, methods=["POST"]),
        Route("/api/toma/{id:int}", cambiar_toma, methods=["POST"]),
        Route("/api/toma/{id:int}", borrar_toma, methods=["DELETE"]),
        Route("/api/dia/{dia}/hora", hora_seccion, methods=["POST"]),
        *rutas_recetas(recetario, hoy, registro),
        *rutas_coach(conversaciones),
        *rutas_plan(planes, store, ajustes, asistente, hoy, ahora),
        *rutas_calendario(sincronizacion),
        Mount("/static", StaticFiles(directory=ESTATICOS), name="static"),
    ])
