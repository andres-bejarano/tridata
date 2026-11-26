"""Orchestrates GarminClient + DataStore for incremental syncing.

This is the piece a daily cron job / scheduled task calls: it figures
out what's missing since the last run and only fetches that.

Unit 061 («Garmin manda»): at the end of each sync (the routine of 08:00 and «Actualizar Garmin», which run this same
`sync`), the workouts scheduled in Garmin from 3 days ago to the end of the plan in course reach the plan
(`plan_desde_garmin`). In its own `try` (R6): a 429 or a failure there leaves the plan as it was, the rest of the sync
ends the same and the next one tries again. Only counts are logged, never a text of the plan.

Unit 113 (R-180, R-185): the detail of a workout already paired is read at most once a day (`ya_leido`); the new ones
and the ones moved in Garmin always. A 429 on a detail waits `ESPERA_429` seconds and tries once more; a second one
goes up as before (the plan as it was, the next sync tries again).

Unit 096 (R-259): after saving the laps (`sync_laps`) or the detail (`sync_detalle`) of a session, `umbral.revisar_sesion`
looks in it for an effort that proposes a new threshold. In its own `try`: a failure there never stops the sync.

Unit 104 (R1, R2): `carpeta_datos` is THE folder of Andrés's personal papers of sport (`TRIDATA_DOCS` of `.env`, only
its path; the repository is public) and `dentro_de` the one frontier every file the team asks for goes through.
"""
from __future__ import annotations

import fcntl
import logging
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .calendario import DESDE_HECHOS
from .garmin_client import GarminAuthError, GarminClient, GarminRateLimitError, _es_429
from .plan import Planes
from .plan.desde_garmin import cambios, programado, ya_leido
from .storage import DataStore

logger = logging.getLogger(__name__)

# -- unit 104 (R1, R2): the one folder of the personal papers of sport --
VARIABLE_DATOS = "TRIDATA_DOCS"
VARIABLE_VIEJA = "TRIDATA_MEDICOS_DIR"  # replaced by TRIDATA_DOCS (its health/); only read to say so


def carpeta_datos(entorno: Mapping[str, str] | None = None) -> Path | None:
    """The folder of `TRIDATA_DOCS` (with `~` expanded), or None without it. Read when called, never at import."""
    valor = (os.environ if entorno is None else entorno).get(VARIABLE_DATOS, "").strip()
    return Path(valor).expanduser() if valor else None


def dentro_de(ruta: Path | str, raiz: Path) -> Path | None:
    """`ruta` (relative ones from `raiz`) resolved with its links and «..», if it falls inside `raiz` (resolved too);
    None if it falls outside or cannot be resolved. Whatever is read is this resolved path, never the one given."""
    try:
        dada = Path(ruta).expanduser()
        final = (dada if dada.is_absolute() else raiz / dada).resolve()
        base = Path(raiz).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return final if final.is_relative_to(base) else None


RUNNING_TYPES = (
    "running", "trail_running", "treadmill_running", "indoor_running",
)
CYCLING_TYPES = (
    "cycling", "road_biking", "mountain_biking", "indoor_cycling",
    "virtual_ride", "gravel_cycling",
)
SWIMMING_TYPES = (
    "lap_swimming",
    "open_water_swimming",
)
GYM_TYPES = ("strength_training",)
ALL_SPORT_TYPES = RUNNING_TYPES + CYCLING_TYPES + SWIMMING_TYPES + GYM_TYPES
# Unit 094 (R-246): the sessions whose detail second by second is asked: run, ride, open water and walks; never the
# pool nor strength. Up to 20 per pass (S-86, G-63); the copy before the refill looks at sessions older than 2 days.
DETALLE_TYPES = RUNNING_TYPES + CYCLING_TYPES + ("open_water_swimming", "hiking", "walking")
DETALLE_POR_PASADA = 20
DIAS_NUEVA = 2

# -- unit 018: the last good sync, the warning of today and what «Actualizar Garmin» brings (R-90, R-91) --
HORAS_AVISO = 26  # one day plus the margin of the routine: older than this, today's screen warns
REINTENTO_HASTA = 14  # the routine tries again every hour until 14:00 (the timer says 08..14)
DIAS_ATRAS_PLAN = 3  # unit 061: the scheduled workouts of Garmin are read from 3 days ago to the end of the plan
ESPERA_429 = 30  # unit 113 (R-185): seconds waited after a 429 on the detail of a workout, before trying once more
MAX_DIAS_ATRAS = 45  # «Actualizar Garmin» brings at most these days, the same window as `tridata sync`
MOTIVOS = {"sesion": "Garmin no dejó iniciar sesión", "limite": "Garmin estaba limitando las peticiones",
           "conexion": "se cortó la conexión"}
_DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre")


def _cuando(momento: datetime, ahora: datetime) -> str:
    """«hoy», «el martes 20» within the 6 days before, «el 12 de octubre» further back; with its time."""
    atras = (ahora.date() - momento.date()).days
    if atras == 0:
        dia = "hoy"
    elif 0 < atras <= 6:
        dia = f"el {_DIAS_SEMANA[momento.weekday()]} {momento.day}"
    else:
        dia = f"el {momento.day} de {_MESES[momento.month - 1]}"
    return f"{dia} a las {momento:%H:%M}"


def aviso_sync(estado: Mapping[str, str | None] | None, ahora: datetime) -> dict:
    """Pure (R-91): whether today's screen warns that Garmin is not synced, and what it says.

    It warns when the last good sync is older than `HORAS_AVISO`, or when the last attempt failed after it.
    Without any record there is no warning. The second sentence says when the routine tries again.
    """
    estado = dict(estado or {})
    ok_en, fallo_en, motivo = estado.get("ok_en"), estado.get("fallo_en"), estado.get("fallo_motivo")
    bloque: dict[str, Any] = {"ok_en": ok_en, "fallo_en": fallo_en, "fallo_motivo": motivo, "aviso": False,
                              "texto": None}
    ok = datetime.fromisoformat(ok_en) if ok_en else None
    fallo = datetime.fromisoformat(fallo_en) if fallo_en else None
    if ok is None and fallo is None:
        return bloque
    viejo = ok is not None and ahora - ok > timedelta(hours=HORAS_AVISO)
    fallido = fallo is not None and (ok is None or fallo > ok)
    if not (viejo or fallido):
        return bloque
    reintento = (f"Se vuelve a intentar sola cada hora hasta las {REINTENTO_HASTA}:00." if ahora.hour < REINTENTO_HASTA
                 else "Se vuelve a intentar mañana a las 08:00.")
    if ok is not None:
        texto = f"Garmin no se ha sincronizado desde {_cuando(ok, ahora)}. {reintento}"
    elif fallo is not None:
        porque = MOTIVOS.get(motivo or "", "falló")
        texto = f"Garmin no se ha sincronizado: el último intento, {_cuando(fallo, ahora)}, {porque}. {reintento}"
    return {**bloque, "aviso": True, "texto": texto}


def desde_para_actualizar(estado: Mapping[str, str | None] | None, hoy: date) -> date:
    """Pure (R-90): «Actualizar Garmin» asks from the day of the last good sync to today, at most
    `MAX_DIAS_ATRAS` days; without a record, only today (as before unit 018)."""
    ok_en = (estado or {}).get("ok_en")
    if not ok_en:
        return hoy
    desde = datetime.fromisoformat(ok_en).date()
    return min(hoy, max(desde, hoy - timedelta(days=MAX_DIAS_ATRAS - 1)))


# -- unit 030 (G-48): one sync with Garmin at a time, between the app's button and `tridata sync` --
NOMBRE_CERROJO = "sync.lock"


class SyncEnMarcha(RuntimeError):
    """Another sync with Garmin (the button of the app or the daily routine) holds the lock."""


@contextmanager
def cerrojo_sync(carpeta: Path) -> Iterator[None]:
    """Holds `carpeta/sync.lock` while the block runs, or raises `SyncEnMarcha` at once if another holds it.

    `fcntl.flock` without waiting: the kernel releases it when the process dies, so a crash never leaves it taken
    (a row «running» in SQLite would). It works between processes and between two opens in the same one.
    """
    carpeta.mkdir(parents=True, exist_ok=True)
    with open(carpeta / NOMBRE_CERROJO, "a", encoding="utf-8") as fichero:
        try:
            fcntl.flock(fichero, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SyncEnMarcha("another sync with Garmin is running") from exc
        try:
            yield
        finally:
            fcntl.flock(fichero, fcntl.LOCK_UN)


class SyncService:
    #: Re-fetch this many most-recent days on every run even when they already have
    #: a row. Garmin sometimes finalizes a day's totals (steps, calories) *after* its
    #: own calendar date — e.g. an activity uploaded late from a separate device/bike
    #: computer — and `missing_dates()` only catches dates with NO row at all, so a
    #: value cached before that upload lands would otherwise stay wrong forever.
    #: (2026-08-18 through 2026-08-27 is a real example of exactly that happening.)
    #:
    #: This short window is cheap enough to run on every daily sync, but it only
    #: catches staleness discovered within RECENT_REVALIDATE_DAYS of it happening.
    #: A day that slips past this window unnoticed (e.g. 2026-08-20 / 2026-08-24,
    #: which stayed wrong for weeks) needs the separate, infrequent wide sweep —
    #: see `sync()`'s `revalidate_days` parameter — daily_update.sh triggers it
    #: automatically every ~7 days (see WIDE_INTERVAL_DAYS there), no separate
    #: script/timer needed.
    RECENT_REVALIDATE_DAYS = 3

    def __init__(self, client: GarminClient, store: DataStore) -> None:
        self._client = client
        self._store = store

    def _days_to_check(
        self, table: str, col: str, since: date, until: date, revalidate_days: int | None = None,
    ) -> list[date]:
        """Missing dates in [since, until], plus the most recent
        `revalidate_days` real-world days even if already present.

        `revalidate_days` defaults to `RECENT_REVALIDATE_DAYS` (the short window
        used by the normal daily run). Pass a larger value — e.g. from a weekly
        wide-sweep job — to also catch days that fell stale outside that short
        window before anything ever re-checked them (see `sync()`'s
        `revalidate_days` parameter). Pass 0 to disable revalidation entirely
        and only fetch genuinely missing days.

        Anchored to *today*, not to `until`: a backfill/resync of old history
        (e.g. `until` deep in the past) shouldn't force-refresh the tail end of
        that old range every time — only calendar days close to right now are
        at risk of having been cached before Garmin finished processing them.
        """
        if revalidate_days is None:
            revalidate_days = self.RECENT_REVALIDATE_DAYS

        missing = set(self._store.missing_dates(table, col, since, until))

        recent: set[date] = set()
        if revalidate_days > 0:
            revalidate_start = date.today() - timedelta(days=revalidate_days - 1)
            recent_start = max(since, revalidate_start)
            recent_end = min(until, date.today())
            day = recent_start
            while day <= recent_end:
                recent.add(day)
                day += timedelta(days=1)

        return sorted(missing | recent)

    def sync(self, since: date, until: date | None = None, revalidate_days: int | None = None) -> None:
        """Fetch and persist everything missing between `since` and `until`
        (plus a recent-day revalidation window — see `_days_to_check`).

        On a fully-synced store this is a no-op aside from that revalidation;
        on first run with `since` set to e.g. 2026-02-01, it backfills your
        whole history.

        `revalidate_days` overrides `RECENT_REVALIDATE_DAYS` for this call only —
        the normal daily cron run leaves it as `None` (short window, cheap), while
        an occasional wide sweep can pass e.g. 45 to revalidate a much larger
        window and catch days that went stale outside the short window. See
        daily_update.sh, which triggers this automatically every ~7 days.
        """
        until = until or date.today()

        self._client.login()

        activity_days = self._days_to_check("activities", "activity_date", since, until, revalidate_days)
        if activity_days:
            start, end = min(activity_days), max(activity_days)
            activities = self._client.get_activities(start, end)
            self._store.save_activities(activities)
            logger.info("Synced %d activities (%s to %s)", len(activities), start, end)
        self.rellenar_horas(until)

        for day in self._days_to_check("daily_stats", "stat_date", since, until, revalidate_days):
            stats = self._client.get_daily_stats(day)
            if stats:
                self._store.save_daily_stats(stats)

        for day in self._days_to_check("sleep", "sleep_date", since, until, revalidate_days):
            sleep = self._client.get_sleep(day)
            if sleep:
                self._store.save_sleep(sleep)

        for day in self._days_to_check("hrv", "hrv_date", since, until, revalidate_days):
            hrv = self._client.get_hrv(day)
            if hrv:
                self._store.save_hrv(hrv)

        # -- Extended metrics (skipped when client does not implement them) ----
        _daily_extended = [
            ("vo2max",             "vo2max_date",     "get_vo2max",             "save_vo2max"),
            ("training_readiness", "readiness_date",  "get_training_readiness", "save_training_readiness"),
            ("training_status",    "status_date",     "get_training_status",    "save_training_status"),
            ("body_battery",       "bb_date",         "get_body_battery",       "save_body_battery"),
            ("spo2",               "spo2_date",       "get_spo2",               "save_spo2"),
            ("respiration",        "respiration_date","get_respiration",         "save_respiration"),
            ("floors",             "floors_date",     "get_floors",             "save_floors"),
            ("intensity_minutes",  "intensity_date",  "get_intensity_minutes",  "save_intensity_minutes"),
            ("hydration",          "hydration_date",  "get_hydration",          "save_hydration"),
        ]
        for table, col, getter_name, saver_name in _daily_extended:
            getter = getattr(self._client, getter_name, None)
            if not callable(getter):
                continue
            saver = getattr(self._store, saver_name)
            for day in self._days_to_check(table, col, since, until, revalidate_days):
                record = getter(day)
                if record:
                    saver(record)

        if callable(getattr(self._client, "get_personal_records", None)):
            records = self._client.get_personal_records()
            if records:
                self._store.save_personal_records(records)

        if callable(getattr(self._client, "get_race_predictions", None)):
            if self._days_to_check("race_predictions", "prediction_date", since, until, revalidate_days):
                prediction = self._client.get_race_predictions()
                if prediction:
                    self._store.save_race_predictions(prediction)

        if callable(getattr(self._client, "entrenos_programados", None)):
            try:
                self.plan_desde_garmin(until)
            except Exception as exc:  # noqa: BLE001 - R6: a 429 or a failure never stops the sync; tried next time
                logger.warning("The plan was not updated from Garmin (%s); the next sync tries again",
                               type(exc).__name__)

        logger.info("Sync complete: %s to %s", since, until)

    def plan_desde_garmin(self, hoy: date) -> dict[str, int] | None:
        """Unit 061 (R1-R5): what is scheduled in Garmin from `DIAS_ATRAS_PLAN` days before `hoy` to the end of the
        plan in course wins over its sessions. Everything is read from Garmin before anything is written, so a
        failure halfway leaves the plan as it was (R6). `{cambiadas, anadidas, avisos}`, or None without a plan."""
        planes = Planes(self._store.db_path)
        plan_id = planes.en_curso(hoy)
        fin = planes.fin(plan_id) if plan_id is not None else None
        desde = hoy - timedelta(days=DIAS_ATRAS_PLAN)
        if plan_id is None or fin is None or fin < desde:
            return None
        sesiones = planes.sesiones_garmin(plan_id, desde, fin, fuera=True)
        dia = hoy.isoformat()
        programados = [programado(item) if ya_leido(sesiones, item, dia)
                       else programado(item, self._detalle(item["entreno"]), dia)
                       for item in self._client.entrenos_programados(desde, fin)]
        resumen = planes.aplicar_garmin(plan_id, cambios(sesiones, programados))
        logger.info("Plan from Garmin: %d workouts (%d details read), %d sessions changed, %d added, %d warnings",
                    len(programados), sum(1 for p in programados if p.leido), resumen["cambiadas"],
                    resumen["anadidas"], resumen["avisos"])
        return resumen

    def _detalle(self, workout_id: int) -> dict[str, Any]:
        """Unit 113 (R-185): the detail of a workout; after a 429, waits and tries once more (a second one goes up)."""
        try:
            return self._client.entreno(workout_id)
        except GarminRateLimitError:
            logger.info("Garmin limited the requests reading a workout; trying again in %d s", ESPERA_429)
            time.sleep(ESPERA_429)
            return self._client.entreno(workout_id)

    def rellenar_horas(self, hasta: date) -> int:
        """Unit 054 (R4): the activities since `DESDE_HECHOS` saved without their hour (before the 054) get it, with
        ONE call to Garmin for the whole range; none if no hour is missing. Returns how many got it."""
        sin_hora = {a["activity_id"] for a in self._store.activities_between(DESDE_HECHOS, hasta)
                    if not a.get("start_time_local")}
        if not sin_hora:
            return 0
        traidas = [a for a in self._client.get_activities(DESDE_HECHOS, hasta) if a.start_time_local]
        self._store.save_activities(traidas)
        rellenas = len(sin_hora & {a.activity_id for a in traidas})
        logger.info("Start time filled for %d of %d activities", rellenas, len(sin_hora))
        return rellenas

    _LAP_TYPES = RUNNING_TYPES + CYCLING_TYPES + SWIMMING_TYPES
    _WEATHER_TYPES = RUNNING_TYPES + CYCLING_TYPES

    def sync_laps(self, limit: int = 20) -> int:
        """Fetch per-lap splits for up to `limit` activities that have none yet.

        Processes the most recent eligible activities first. Returns the number
        of activities attempted.
        """
        self._client.login()

        candidate_ids = self._store.get_activity_ids_by_type(self._LAP_TYPES)
        already_done = self._store.activity_ids_lap_synced()
        pending = [aid for aid in candidate_ids if aid not in already_done][:limit]

        total = len(pending)
        for i, activity_id in enumerate(pending, start=1):
            print(f"parciales: {i}/{total} actividades procesadas", flush=True)
            try:
                laps = self._client.get_activity_laps(activity_id)
            except Exception as exc:
                # Bug 079: a 429 or a session failure stops the run as before (the routine waits and
                # retries); any other failure is one line and the activity stays pending (G-4).
                if isinstance(exc, (GarminAuthError, GarminRateLimitError)) or _es_429(exc):
                    raise
                logger.warning("Laps not fetched for activity %s (%s)", activity_id, type(exc).__name__)
                continue
            if laps:
                self._store.save_activity_laps(laps)
            self._store.mark_laps_synced(activity_id, len(laps))
            logger.info("Laps synced for activity %s (%d laps)", activity_id, len(laps))
            self._buscar_umbral(activity_id)

        logger.info("Lap sync complete: %d/%d activities processed", total, len(candidate_ids))
        return total

    def rellenar_cadencia_bici(self) -> dict[str, Any]:
        """Unit 093 (R-245, the one exception to G-4): asks Garmin again for the laps of the rides already brought
        whose laps have no `avg_bike_cadence` key and adds ONLY that field (`DataStore.poner_cadencia_bici`): distance,
        time, HR and the rest stay as they were. Run by hand (`tridata sync --laps-cadencia-bici`), never by the
        routine. Like `sync_laps` (bug 079): a 429 or a session failure stops it; any other failure is one line and
        that ride stays pending, so a second run asks only for those. Returns `{salidas, vueltas_con_cadencia,
        faltan}`: rides asked, laps that got a cadence, and the IDs still missing."""
        self._client.login()
        pendientes = self._store.ids_sin_cadencia_bici(CYCLING_TYPES)
        faltan: list[str] = []
        con_cadencia = 0
        for i, activity_id in enumerate(pendientes, start=1):
            print(f"cadencia de bici: {i}/{len(pendientes)} salidas", flush=True)
            try:
                laps = self._client.get_activity_laps(activity_id)
            except Exception as exc:
                if isinstance(exc, (GarminAuthError, GarminRateLimitError)) or _es_429(exc):
                    raise
                logger.warning("Bike cadence not fetched for activity %s (%s)", activity_id, type(exc).__name__)
                faltan.append(activity_id)
                continue
            cadencias = {lap.lap_index: lap.avg_bike_cadence for lap in laps}
            self._store.poner_cadencia_bici(activity_id, cadencias)
            con_cadencia += sum(1 for c in cadencias.values() if c is not None)
        logger.info("Bike cadence refill: %d rides, %d laps with cadence, %d missing",
                    len(pendientes), con_cadencia, len(faltan))
        return {"salidas": len(pendientes), "vueltas_con_cadencia": con_cadencia, "faltan": faltan}

    def sync_detalle(self, limit: int = DETALLE_POR_PASADA) -> int:
        """Unit 094 (R-246..R-249, G-63): the detail second by second of up to `limit` sessions of `DETALLE_TYPES`
        never asked, newest first — the new ones enter first and the history fills itself, pass after pass. Like
        `sync_laps` (bug 079): a 429 or a session failure stops the pass (what was saved stays; the next one goes on
        where it was); any other failure is one line and that session stays pending. No detail, or none with HR:
        «intentado», never asked again. Before the FIRST pass that asks for a session older than `DIAS_NUEVA` days,
        a verified copy of the database (`_copia_antes_del_relleno`). Only `activity_detail` is written. Returns the
        number of sessions asked."""
        self._client.login()
        pendientes = self._store.ids_sin_detalle(DETALLE_TYPES)[:limit]
        actividades = {aid: self._store.get_activity(aid) or {} for aid in pendientes}
        self._copia_antes_del_relleno([a.get("activity_date") for a in actividades.values()])

        total = len(pendientes)
        for i, activity_id in enumerate(pendientes, start=1):
            print(f"detalle: {i}/{total} sesiones procesadas", flush=True)
            try:
                detalle = self._client.get_activity_detail(activity_id,
                                                           actividades[activity_id].get("duration_seconds") or 0)
            except Exception as exc:
                if isinstance(exc, (GarminAuthError, GarminRateLimitError)) or _es_429(exc):
                    raise
                logger.warning("Detail not fetched for activity %s (%s)", activity_id, type(exc).__name__)
                continue
            if detalle is None:
                self._store.marcar_detalle_intentado(activity_id)
                logger.info("No detail with HR for activity %s: tried, not asked again", activity_id)
            else:
                self._store.guardar_detalle(activity_id, detalle)
                logger.info("Detail synced for activity %s (%d points)", activity_id, len(detalle["t"]))
                self._buscar_umbral(activity_id)
        logger.info("Detail sync complete: %d activities processed", total)
        return total

    def _buscar_umbral(self, activity_id: str) -> None:
        """Unit 096 (R-259): the effort of a session just brought may propose a new threshold. Never stops the sync."""
        from . import umbral  # here: `umbral` reads `fitness`, which reads this module

        try:
            if umbral.revisar_sesion(self._store, activity_id, date.today()) is not None:
                logger.info("A new threshold was proposed from activity %s", activity_id)
        except Exception as exc:  # noqa: BLE001 - R-259: looking for an effort never breaks the sync
            logger.warning("Threshold not looked for in activity %s (%s)", activity_id, type(exc).__name__)

    def _copia_antes_del_relleno(self, fechas: list[str | None]) -> None:
        """R-249: once, before the first pass that asks for an old session, a verified copy of the database in
        `<carpeta de la base>/backups/<sello>-pulso/data.sqlite3` (the folder of `tridata backup`, with
        `DataStore.backup`, which checks it), and its path written down so it is not repeated. A failure raises:
        without a copy nothing old is asked."""
        if self._store.copia_detalle() is not None:
            return
        frontera = (date.today() - timedelta(days=DIAS_NUEVA)).isoformat()
        if not any(f and f < frontera for f in fechas):
            return
        carpeta = self._store.db_path.parent / "backups"
        carpeta.mkdir(parents=True, exist_ok=True)
        carpeta.chmod(0o700)
        sello = datetime.now().strftime("%Y-%m-%dT%H%M%S")
        for n in range(1000):
            destino = carpeta / (f"{sello}-pulso" if n == 0 else f"{sello}-pulso-{n:03d}")
            try:
                destino.mkdir()
                break
            except FileExistsError:
                continue
        destino.chmod(0o700)
        copia = destino / "data.sqlite3"
        self._store.backup(copia)
        self._store.marcar_copia_detalle(copia)
        logger.info("Verified copy of the database before the detail refill: %s", copia)

    def sync_weather(self, limit: int = 20) -> int:
        """Fetch weather snapshots for up to `limit` running/cycling activities that have none yet.

        Processes the most recent eligible activities first. Returns the number
        of activities attempted.
        """
        self._client.login()

        candidate_ids = self._store.get_activity_ids_by_type(self._WEATHER_TYPES)
        already_done = self._store.activity_ids_weather_synced()
        pending = [aid for aid in candidate_ids if aid not in already_done][:limit]

        total = len(pending)
        for i, activity_id in enumerate(pending, start=1):
            print(f"clima: {i}/{total} actividades procesadas", flush=True)
            weather = self._client.get_activity_weather(activity_id)
            self._store.save_activity_weather(activity_id, weather)
            logger.info("Weather synced for activity %s", activity_id)

        logger.info("Weather sync complete: %d/%d activities processed", total, len(candidate_ids))
        return total
