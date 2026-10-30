"""Regression tests for SyncService — no real network, no real Garmin credentials."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from tridata.calendario import DESDE_HECHOS
from tridata.garmin_client import GarminClient
from tridata.models import Activity, DailyStats, HRVRecord, SleepRecord
from tridata.storage import DataStore
from tridata.sync import SyncService


# ---------------------------------------------------------------------------
# Stub — same public interface as GarminClient, records every call made to it
# ---------------------------------------------------------------------------

class _StubClient:
    def __init__(
        self,
        activities: list[Activity] | None = None,
        daily_stats: dict[date, DailyStats] | None = None,
        sleep: dict[date, SleepRecord] | None = None,
        hrv: dict[date, HRVRecord] | None = None,
    ) -> None:
        self._activities = activities or []
        self._daily_stats: dict[date, DailyStats] = daily_stats or {}
        self._sleep: dict[date, SleepRecord] = sleep or {}
        self._hrv: dict[date, HRVRecord] = hrv or {}

        self.login_count = 0
        self.get_activities_calls: list[tuple[date, date]] = []
        self.get_daily_stats_calls: list[date] = []
        self.get_sleep_calls: list[date] = []
        self.get_hrv_calls: list[date] = []

    def login(self) -> None:
        self.login_count += 1

    def get_activities(self, start: date, end: date) -> list[Activity]:
        self.get_activities_calls.append((start, end))
        return self._activities

    def get_daily_stats(self, day: date) -> DailyStats | None:
        self.get_daily_stats_calls.append(day)
        return self._daily_stats.get(day)

    def get_sleep(self, day: date) -> SleepRecord | None:
        self.get_sleep_calls.append(day)
        return self._sleep.get(day)

    def get_hrv(self, day: date) -> HRVRecord | None:
        self.get_hrv_calls.append(day)
        return self._hrv.get(day)


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

DAY1 = date(2026, 8, 4)
DAY2 = date(2026, 8, 5)
RANGE = (DAY1, DAY2)


def _activity(day: date, activity_id: str) -> Activity:
    return Activity(
        activity_id=activity_id,
        activity_date=day,
        activity_type="running",
        name="Morning Run",
        duration_seconds=3600.0,
        distance_meters=10000.0,
        avg_hr=145.0,
        start_time_local=f"{day.isoformat()} 07:00:00",  # unit 054: the client always brings it now
    )


def _stats(day: date) -> DailyStats:
    return DailyStats(stat_date=day, steps=10000, resting_hr=52)


def _sleep(day: date) -> SleepRecord:
    return SleepRecord(sleep_date=day, total_sleep_seconds=25200, sleep_score=78)


def _hrv(day: date) -> HRVRecord:
    return HRVRecord(hrv_date=day, last_night_avg=46.0, status="BALANCED")


@pytest.fixture()
def store(tmp_path) -> DataStore:
    return DataStore(tmp_path / "test.sqlite3")


@pytest.fixture()
def full_client() -> _StubClient:
    return _StubClient(
        activities=[_activity(DAY1, "act-001"), _activity(DAY2, "act-002")],
        daily_stats={DAY1: _stats(DAY1), DAY2: _stats(DAY2)},
        sleep={DAY1: _sleep(DAY1), DAY2: _sleep(DAY2)},
        hrv={DAY1: _hrv(DAY1), DAY2: _hrv(DAY2)},
    )


# ---------------------------------------------------------------------------
# (a) First sync on an empty store: everything must be fetched and persisted
# ---------------------------------------------------------------------------

class TestFirstSync:
    def test_login_called_once(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert full_client.login_count == 1

    def test_activities_fetched_for_full_range(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert full_client.get_activities_calls == [(DAY1, DAY2)]

    def test_stats_fetched_for_every_missing_day(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert set(full_client.get_daily_stats_calls) == {DAY1, DAY2}

    def test_sleep_fetched_for_every_missing_day(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert set(full_client.get_sleep_calls) == {DAY1, DAY2}

    def test_hrv_fetched_for_every_missing_day(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert set(full_client.get_hrv_calls) == {DAY1, DAY2}

    def test_activities_persisted(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert len(store.export_all()["activities"]) == 2

    def test_stats_persisted(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert len(store.export_all()["daily_stats"]) == 2

    def test_sleep_persisted(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert len(store.export_all()["sleep"]) == 2

    def test_hrv_persisted(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert len(store.export_all()["hrv"]) == 2


# ---------------------------------------------------------------------------
# (b) Second sync over the same range must be a no-op
# ---------------------------------------------------------------------------

class TestIdempotency:
    def _sync_twice(self, store: DataStore, client: _StubClient) -> None:
        svc = SyncService(client, store)
        svc.sync(*RANGE)
        client.get_activities_calls.clear()
        client.get_daily_stats_calls.clear()
        client.get_sleep_calls.clear()
        client.get_hrv_calls.clear()
        svc.sync(*RANGE)

    def test_no_activities_fetched_on_second_sync(self, store, full_client):
        self._sync_twice(store, full_client)
        assert full_client.get_activities_calls == []

    def test_no_stats_fetched_on_second_sync(self, store, full_client):
        self._sync_twice(store, full_client)
        assert full_client.get_daily_stats_calls == []

    def test_no_sleep_fetched_on_second_sync(self, store, full_client):
        self._sync_twice(store, full_client)
        assert full_client.get_sleep_calls == []

    def test_no_hrv_fetched_on_second_sync(self, store, full_client):
        self._sync_twice(store, full_client)
        assert full_client.get_hrv_calls == []

    def test_row_count_unchanged_after_second_sync(self, store, full_client):
        svc = SyncService(full_client, store)
        svc.sync(*RANGE)
        snapshot = store.export_all()
        svc.sync(*RANGE)
        assert store.export_all() == snapshot


# ---------------------------------------------------------------------------
# (c) Field values survive the store round-trip
# ---------------------------------------------------------------------------

class TestDataIntegrity:
    def test_activity_ids_round_trip(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        ids = {a["activity_id"] for a in store.export_all()["activities"]}
        assert ids == {"act-001", "act-002"}

    def test_stats_fields_round_trip(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        rows = {r["stat_date"]: r for r in store.export_all()["daily_stats"]}
        assert rows["2026-08-04"]["steps"] == 10000
        assert rows["2026-08-04"]["resting_hr"] == 52

    def test_sleep_fields_round_trip(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        rows = {r["sleep_date"]: r for r in store.export_all()["sleep"]}
        assert rows["2026-08-04"]["total_sleep_seconds"] == 25200
        assert rows["2026-08-04"]["sleep_score"] == 78

    def test_hrv_fields_round_trip(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        rows = {r["hrv_date"]: r for r in store.export_all()["hrv"]}
        assert rows["2026-08-04"]["last_night_avg"] == 46.0
        assert rows["2026-08-04"]["status"] == "BALANCED"

    def test_empty_client_does_not_crash(self, store):
        SyncService(_StubClient(), store).sync(*RANGE)
        exported = store.export_all()
        assert exported["activities"] == []
        assert exported["daily_stats"] == []
        assert exported["sleep"] == []
        assert exported["hrv"] == []


# ---------------------------------------------------------------------------
# (d) `revalidate_days` override — the mechanism behind the weekly wide sweep
# ---------------------------------------------------------------------------

class TestRevalidateDaysOverride:
    def test_default_window_refetches_recent_present_day(self, store):
        """A day inside the default RECENT_REVALIDATE_DAYS window keeps getting
        re-fetched even once it's already stored — this is the existing
        short-window behavior the weekly sweep builds on."""
        today = date.today()
        client = _StubClient(daily_stats={today: _stats(today)})
        svc = SyncService(client, store)
        svc.sync(since=today, until=today)
        client.get_daily_stats_calls.clear()

        svc.sync(since=today, until=today)
        assert client.get_daily_stats_calls == [today]

    def test_revalidate_days_zero_skips_already_present_recent_day(self, store):
        """Passing revalidate_days=0 disables revalidation entirely, even for
        a day that the default window would normally re-fetch."""
        today = date.today()
        client = _StubClient(daily_stats={today: _stats(today)})
        svc = SyncService(client, store)
        svc.sync(since=today, until=today)
        client.get_daily_stats_calls.clear()

        svc.sync(since=today, until=today, revalidate_days=0)
        assert client.get_daily_stats_calls == []

    def test_wide_revalidate_days_catches_day_outside_default_window(self, store):
        """A day that went stale 10 days ago sits outside the default 3-day
        window forever — but a weekly sweep with a wide revalidate_days
        override (e.g. 45) reaches back far enough to catch it, exactly like
        the Aug 20 / Aug 24 case that motivated this."""
        stale_day = date.today() - timedelta(days=10)
        client = _StubClient(daily_stats={stale_day: _stats(stale_day)})
        svc = SyncService(client, store)
        svc.sync(since=stale_day, until=date.today(), revalidate_days=15)
        client.get_daily_stats_calls.clear()

        svc.sync(since=stale_day, until=date.today())
        assert stale_day not in client.get_daily_stats_calls

        svc.sync(since=stale_day, until=date.today(), revalidate_days=15)
        assert stale_day in client.get_daily_stats_calls


# ---------------------------------------------------------------------------
# Unit 054 (R4): the hour of the activities saved before it, asked once
# ---------------------------------------------------------------------------

HOY_054 = date(2026, 10, 29)


def _con_hora(activity_id: str, day: date, hora: str | None) -> Activity:
    return Activity(activity_id=activity_id, activity_date=day, activity_type="running", name="Invented Run",
                    duration_seconds=2400.0, distance_meters=8000.0,
                    start_time_local=f"{day.isoformat()} {hora}" if hora else None)


def test_garmin_client_keeps_the_start_time_local() -> None:  # R4
    class _Api:
        def get_activities_by_date(self, start: str, end: str) -> list[dict]:
            return [{"activityId": 42, "startTimeLocal": "2026-10-27 18:30:00", "activityType": {"typeKey": "running"},
                     "activityName": "Invented", "duration": 3120.0, "distance": 10200.0}]

    client = GarminClient("invented@example.com", "invented")
    client._api = _Api()
    (activity,) = client.get_activities(date(2026, 10, 27), date(2026, 10, 27))
    assert activity.start_time_local == "2026-10-27 18:30:00"
    assert activity.to_dict()["start_time_local"] == "2026-10-27 18:30:00"


def test_rellenar_horas_asks_garmin_once_for_every_missing_hour(tmp_path) -> None:  # R4
    store = DataStore(tmp_path / "t.db")
    store.save_activities([_con_hora("a", date(2026, 8, 3), None), _con_hora("b", date(2026, 10, 2), None),
                           _con_hora("c", date(2026, 10, 3), "07:00:00"),
                           _con_hora("julio", date(2026, 7, 30), None)])
    client = _StubClient(activities=[_con_hora("a", date(2026, 8, 3), "06:10:00"),
                                     _con_hora("b", date(2026, 10, 2), "19:45:00"),
                                     _con_hora("c", date(2026, 10, 3), "07:00:00")])
    SyncService(client, store).rellenar_horas(HOY_054)
    assert client.get_activities_calls == [(DESDE_HECHOS, HOY_054)]
    horas = {a["activity_id"]: a.get("start_time_local") for a in store.activities_between(date(2026, 7, 1), HOY_054)}
    assert horas == {"a": "2026-08-03 06:10:00", "b": "2026-10-02 19:45:00", "c": "2026-10-03 07:00:00",
                     "julio": None}  # before the 1st of August nothing is asked


def test_rellenar_horas_does_not_call_garmin_when_none_is_missing(tmp_path) -> None:  # R4
    store = DataStore(tmp_path / "t.db")
    store.save_activities([_con_hora("c", date(2026, 10, 3), "07:00:00"), _con_hora("julio", date(2026, 7, 30), None)])
    client = _StubClient()
    SyncService(client, store).rellenar_horas(HOY_054)
    assert client.get_activities_calls == []


def test_the_daily_sync_fills_the_missing_hours(tmp_path) -> None:  # R4: the routine of 08:00 brings them
    store = DataStore(tmp_path / "t.db")
    store.save_activities([_con_hora("a", date(2026, 8, 3), None)])

    class _PorFechas(_StubClient):  # like Garmin: only the activities of the range asked
        def get_activities(self, start: date, end: date) -> list[Activity]:
            return [a for a in super().get_activities(start, end) if start <= a.activity_date <= end]

    client = _PorFechas(activities=[_con_hora("a", date(2026, 8, 3), "06:10:00")])
    SyncService(client, store).sync(since=HOY_054, until=HOY_054, revalidate_days=0)
    assert (DESDE_HECHOS, HOY_054) in client.get_activities_calls
    assert store.get_activity("a")["start_time_local"] == "2026-08-03 06:10:00"


# ---------------------------------------------------------------------------
# Unit 061: the workouts scheduled in Garmin reach the plan («Garmin manda»).
# Garmin is always a double; every name, date and id is invented.
# ---------------------------------------------------------------------------

from tridata.garmin_client import GarminRateLimitError  # noqa: E402
from tridata.plan import Planes  # noqa: E402

HOY_061 = date(2026, 10, 30)
PLAN_061 = {"nombre": "Road to Inventada 26", "apartados": [{"titulo": "Semanas", "parte": "previsto"}],
            "carreras": [{"apartado": 0, "nombre": "Media inventada", "fecha": "2026-11-22", "objetivo": "1:30",
                          "estado": "pendiente", "principal": True}],
            "semanas": [], "nutricion": [], "incidencias": [], "notas": [],
            "sesiones": [{"apartado": 0, "fecha": "2026-11-01", "titulo": "LR 1h30", "bloques": ["1h30 a 5:20"],
                          "clave": True}]}


def _leido(nombre: str, segundos: int, ritmo_s_km: int, actualizado: str) -> dict:
    """A workout of one running step, as `get_workout_by_id` gives it (invented)."""
    corre = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
    return {"workoutId": 0, "workoutName": nombre, "description": None, "updatedDate": actualizado,
            "sportType": corre, "estimatedDurationInSecs": segundos,
            "workoutSegments": [{"segmentOrder": 1, "sportType": corre, "workoutSteps": [
                {"type": "ExecutableStepDTO", "stepId": 1, "stepOrder": 1,
                 "stepType": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
                 "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2,
                                  "displayable": True},
                 "endConditionValue": float(segundos),
                 "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6},
                 "targetValueOne": 1000 / (ritmo_s_km + 5), "targetValueTwo": 1000 / (ritmo_s_km - 5)}]}]}


class _ConCalendario(_StubClient):
    """`_StubClient` plus the calendar of Garmin Connect: `entrenos_programados` and `entreno`."""

    def __init__(self, programados: list[dict], detalles: dict[int, dict], *, falla: Exception | None = None,
                 falla_detalle: Exception | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.programados, self.detalles = programados, detalles
        self.falla, self.falla_detalle = falla, falla_detalle
        self.calendario_calls: list[tuple[date, date]] = []

    def entrenos_programados(self, desde: date, hasta: date) -> list[dict]:
        self.calendario_calls.append((desde, hasta))
        if self.falla is not None:
            raise self.falla
        return [p for p in self.programados if desde.isoformat() <= p["fecha"] <= hasta.isoformat()]

    def entreno(self, workout_id: int) -> dict:
        if self.falla_detalle is not None:
            raise self.falla_detalle
        return self.detalles[workout_id]


def _cliente_061(**kwargs) -> _ConCalendario:
    return _ConCalendario(
        [{"programado": 611, "entreno": 511, "fecha": "2026-11-01", "titulo": "Long run inventado",
          "deporte": "running"},
         {"programado": 612, "entreno": 512, "fecha": "2026-11-03", "titulo": "Rodaje inventado",
          "deporte": "running"}],
        {511: _leido("Long run inventado", 6300, 320, "2026-10-29T21:00:00.0"),
         512: _leido("Rodaje inventado", 2700, 335, "2026-10-28T10:00:00.0")},
        activities=[_activity(HOY_061, "act-061")], daily_stats={HOY_061: _stats(HOY_061)}, **kwargs)


def _sesiones(store: DataStore) -> list[tuple]:
    planes = Planes(store.db_path)
    plan = planes.leer(planes.en_curso(HOY_061))
    return [(s["fecha"], s["titulo"], s["bloques"]) for s in plan["sesiones"]]


def test_el_store_dice_su_ruta(tmp_path) -> None:  # 061: `sync.py` opens the plans next to the activities
    assert DataStore(tmp_path / "t.db").db_path == tmp_path / "t.db"


def test_la_sincronizacion_trae_al_plan_lo_de_garmin(tmp_path) -> None:  # R1, R2, R3: the routine of 08:00
    store = DataStore(tmp_path / "t.db")
    Planes(store.db_path).crear(PLAN_061, datetime(2026, 10, 20, 9))
    client = _cliente_061()
    SyncService(client, store).sync(since=HOY_061, until=HOY_061, revalidate_days=0)
    # from 3 days ago to the end of the plan in course
    assert client.calendario_calls == [(date(2026, 10, 27), date(2026, 11, 22))]
    assert _sesiones(store) == [("2026-11-01", "Long run inventado", ["1h45 a 5:20"]),
                                ("2026-11-03", "Rodaje inventado", ["45' a 5:35"])]
    assert store.get_activity("act-061") is not None


def test_sin_plan_en_curso_no_se_pregunta_el_calendario(tmp_path) -> None:
    store = DataStore(tmp_path / "t.db")
    client = _cliente_061()
    SyncService(client, store).sync(since=HOY_061, until=HOY_061, revalidate_days=0)
    assert client.calendario_calls == []


@pytest.mark.parametrize("donde", ["calendario", "detalle"])
@pytest.mark.parametrize("error", [GarminRateLimitError("Garmin Connect is limiting the requests (429)"),
                                   RuntimeError("no answer")], ids=["429", "fallo"])
def test_si_garmin_frena_o_falla_lo_demas_termina_y_el_plan_no_cambia(tmp_path, donde, error) -> None:  # R6
    store = DataStore(tmp_path / "t.db")
    Planes(store.db_path).crear(PLAN_061, datetime(2026, 10, 20, 9))
    client = _cliente_061(**({"falla": error} if donde == "calendario" else {"falla_detalle": error}))
    SyncService(client, store).sync(since=HOY_061, until=HOY_061, revalidate_days=0)  # does not raise
    assert store.get_activity("act-061") is not None
    assert HOY_061 in client.get_daily_stats_calls
    assert _sesiones(store) == [("2026-11-01", "LR 1h30", ["1h30 a 5:20"])]
    # the next one tries again
    client.falla = client.falla_detalle = None
    SyncService(client, store).sync(since=HOY_061, until=HOY_061, revalidate_days=0)
    assert _sesiones(store)[0][1] == "Long run inventado"


def test_actualizar_garmin_tambien_trae_el_plan(tmp_path, monkeypatch) -> None:  # R1: the button of the app
    from tridata import cli

    store = DataStore(tmp_path / "t.db")
    Planes(store.db_path).crear(PLAN_061, datetime(2026, 10, 20, 9))
    client = _cliente_061()
    monkeypatch.setattr(cli, "GarminClient", lambda email, password: client)
    actualizar = cli._actualizador_garmin(store, {"GARMIN_EMAIL": "inventado@example.com",
                                                  "GARMIN_PASSWORD": "inventada"})
    assert actualizar is not None
    actualizar(HOY_061)
    assert _sesiones(store)[0][1] == "Long run inventado"


# -- GarminClient: the calendar of Garmin Connect -------------------------------------------------------------------

def _item(ident: int, workout: int | None, dia: str, tipo: str = "workout") -> dict:
    return {"id": ident, "itemType": tipo, "workoutId": workout, "date": dia, "title": f"Inventado {ident}",
            "sportTypeKey": "running", "duration": None}


class _ApiCalendario:
    def __init__(self, error: Exception | None = None) -> None:
        self.meses: list[tuple[int, int]] = []
        self.error = error

    def get_scheduled_workouts(self, year: int, month: int) -> dict:
        self.meses.append((year, month))
        if self.error is not None:
            raise self.error
        return {"year": year, "month": month - 1, "calendarItems": [
            _item(month * 10 + 1, month * 100 + 1, f"{year}-{month:02d}-02"),
            _item(month * 10 + 2, None, f"{year}-{month:02d}-03", tipo="activity"),
            _item(month * 10 + 3, month * 100 + 3, f"{year}-{month:02d}-28")]}

    def get_workout_by_id(self, workout_id: int) -> dict:
        if self.error is not None:
            raise self.error
        return {"workoutId": workout_id, "workoutName": "Inventado", "updatedDate": "2026-10-29T10:00:00.0"}


def test_entrenos_programados_pide_cada_mes_una_vez_y_solo_trae_entrenos() -> None:  # R1 · P1
    client = GarminClient("inventado@example.com", "inventada")
    api = _ApiCalendario()
    client._api = api
    entrenos = client.entrenos_programados(date(2026, 10, 27), date(2026, 12, 2))
    assert api.meses == [(2026, 10), (2026, 11), (2026, 12)]
    assert entrenos == [
        {"programado": 103, "entreno": 1003, "fecha": "2026-10-28", "titulo": "Inventado 103", "deporte": "running"},
        {"programado": 111, "entreno": 1101, "fecha": "2026-11-02", "titulo": "Inventado 111", "deporte": "running"},
        {"programado": 113, "entreno": 1103, "fecha": "2026-11-28", "titulo": "Inventado 113", "deporte": "running"},
        {"programado": 121, "entreno": 1201, "fecha": "2026-12-02", "titulo": "Inventado 121", "deporte": "running"}]
    assert client.entreno(1003)["updatedDate"] == "2026-10-29T10:00:00.0"


def test_entrenos_programados_con_429_es_el_error_de_limite() -> None:  # R6
    client = GarminClient("inventado@example.com", "inventada")
    client._api = _ApiCalendario(RuntimeError("429 Client Error: Too Many Requests"))
    with pytest.raises(GarminRateLimitError):
        client.entrenos_programados(date(2026, 10, 27), date(2026, 11, 2))
    with pytest.raises(GarminRateLimitError):
        client.entreno(1003)
