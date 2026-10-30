"""Regression tests for GarminClient — no real network calls."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tridata.garmin_client import GarminAuthError, GarminClient
from tridata.models import Activity, DailyStats, HRVRecord, SleepRecord

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict | list:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture()
def mock_api():
    return MagicMock()


@pytest.fixture()
def client(tmp_path, mock_api):
    """GarminClient with the underlying Garmin object replaced by mock_api."""
    with patch("tridata.garmin_client.Garmin", return_value=mock_api):
        c = GarminClient("user@example.com", "secret", token_dir=tmp_path)
        c.login()
    return c, mock_api


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_success(self, tmp_path):
        mock = MagicMock()
        with patch("tridata.garmin_client.Garmin", return_value=mock):
            c = GarminClient("user@example.com", "secret", token_dir=tmp_path)
            c.login()
        mock.login.assert_called_once_with(str(tmp_path))

    def test_login_restricts_token_store_permissions(self, tmp_path):
        token_dir = tmp_path / "tokens"
        mock = MagicMock()
        # Simulate garminconnect writing the token file world-readable.
        mock.login.side_effect = lambda d: (Path(d) / "garmin_tokens.json").write_text("{}")
        with patch("tridata.garmin_client.Garmin", return_value=mock):
            GarminClient("user@example.com", "secret", token_dir=token_dir).login()
        assert token_dir.stat().st_mode & 0o777 == 0o700
        assert (token_dir / "garmin_tokens.json").stat().st_mode & 0o777 == 0o600

    def test_login_failure_raises_auth_error(self, tmp_path):
        mock = MagicMock()
        mock.login.side_effect = RuntimeError("401 Unauthorized")
        with patch("tridata.garmin_client.Garmin", return_value=mock):
            c = GarminClient("user@example.com", "secret", token_dir=tmp_path)
            with pytest.raises(GarminAuthError):
                c.login()

    def test_api_before_login_raises(self, tmp_path):
        c = GarminClient("user@example.com", "secret", token_dir=tmp_path)
        with pytest.raises(GarminAuthError):
            _ = c.api


# ---------------------------------------------------------------------------
# get_activities
# ---------------------------------------------------------------------------

class TestGetActivities:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_activities_by_date.return_value = _load("activities.json")

        result = c.get_activities(date(2026, 8, 4), date(2026, 8, 4))

        assert len(result) == 2
        first = result[0]
        assert isinstance(first, Activity)
        assert first.activity_id == "9100000001"
        assert first.activity_date == date(2026, 8, 4)
        assert first.activity_type == "running"
        assert first.name == "Morning Run"
        assert first.duration_seconds == 3240.0
        assert first.distance_meters == 9000.0
        assert first.avg_hr == 145.0
        assert first.max_hr == 172.0
        assert first.calories == 520.0
        assert first.training_effect_aerobic == 3.2
        assert first.training_effect_anaerobic == 1.1

    def test_returns_empty_list_when_api_returns_empty(self, client):
        c, api = client
        api.get_activities_by_date.return_value = []
        assert c.get_activities(date(2026, 8, 4), date(2026, 8, 4)) == []

    def test_optional_fields_default_to_none(self, client):
        c, api = client
        api.get_activities_by_date.return_value = _load("activities_minimal.json")
        result = c.get_activities(date(2026, 8, 5), date(2026, 8, 5))
        act = result[0]
        assert act.distance_meters is None
        assert act.avg_hr is None
        assert act.calories is None

    def test_moving_time_from_movingduration(self, client):  # unit 040 (R2): the time in movement, as Strava
        c, api = client
        raw = {"activityId": 1, "startTimeLocal": "2026-10-14 07:00:00", "activityType": {"typeKey": "running"},
               "activityName": "Series de prueba", "duration": 3900.0, "movingDuration": 3720.0, "distance": 12000.0}
        api.get_activities_by_date.return_value = [raw, {**raw, "activityId": 2, "movingDuration": None}]
        con, sin = c.get_activities(date(2026, 10, 14), date(2026, 10, 14))
        assert con.moving_duration_seconds == 3720.0 and con.to_dict()["moving_duration_seconds"] == 3720.0
        assert sin.moving_duration_seconds is None  # optional: the old activities still count


# ---------------------------------------------------------------------------
# get_daily_stats
# ---------------------------------------------------------------------------

class TestGetDailyStats:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_stats.return_value = _load("stats.json")

        result = c.get_daily_stats(date(2026, 8, 4))

        assert isinstance(result, DailyStats)
        assert result.stat_date == date(2026, 8, 4)
        assert result.steps == 12500
        assert result.resting_hr == 52
        assert result.stress_avg == 24
        assert result.body_battery_max == 85
        assert result.body_battery_min == 15
        assert result.total_calories == 2300

    def test_returns_none_when_api_returns_empty_dict(self, client):
        c, api = client
        api.get_stats.return_value = {}
        assert c.get_daily_stats(date(2026, 8, 4)) is None

    def test_returns_none_when_api_returns_none(self, client):
        c, api = client
        api.get_stats.return_value = None
        assert c.get_daily_stats(date(2026, 8, 4)) is None


# ---------------------------------------------------------------------------
# get_sleep
# ---------------------------------------------------------------------------

class TestGetSleep:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_sleep_data.return_value = _load("sleep.json")

        result = c.get_sleep(date(2026, 8, 4))

        assert isinstance(result, SleepRecord)
        assert result.sleep_date == date(2026, 8, 4)
        assert result.total_sleep_seconds == 25200
        assert result.deep_sleep_seconds == 5400
        assert result.light_sleep_seconds == 10800
        assert result.rem_sleep_seconds == 7200
        assert result.awake_seconds == 1800
        assert result.sleep_score == 78

    def test_sleep_score_not_read_from_raw_root(self, client):
        # Regression: sleepScores must be read from dailySleepDTO, not from raw.
        # If it were read from raw root, this would return score=99 instead of None.
        c, api = client
        api.get_sleep_data.return_value = {
            "dailySleepDTO": {"sleepTimeSeconds": 25200},
            "sleepScores": {"overall": {"value": 99}},
        }
        result = c.get_sleep(date(2026, 8, 4))
        assert result.sleep_score is None

    def test_returns_none_when_no_daily_sleep_dto(self, client):
        c, api = client
        api.get_sleep_data.return_value = {"someOtherKey": {}}
        assert c.get_sleep(date(2026, 8, 4)) is None

    def test_returns_none_when_api_returns_none(self, client):
        c, api = client
        api.get_sleep_data.return_value = None
        assert c.get_sleep(date(2026, 8, 4)) is None


# ---------------------------------------------------------------------------
# get_hrv
# ---------------------------------------------------------------------------

class TestGetHRV:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_hrv_data.return_value = _load("hrv.json")

        result = c.get_hrv(date(2026, 8, 4))

        assert isinstance(result, HRVRecord)
        assert result.hrv_date == date(2026, 8, 4)
        assert result.last_night_avg == 46.0
        assert result.last_night_high == 62.0
        assert result.last_night_low == 32.0
        assert result.status == "BALANCED"

    def test_returns_none_when_no_hrv_summary(self, client):
        c, api = client
        api.get_hrv_data.return_value = {"someOtherKey": {}}
        assert c.get_hrv(date(2026, 8, 4)) is None

    def test_returns_none_when_api_returns_none(self, client):
        c, api = client
        api.get_hrv_data.return_value = None
        assert c.get_hrv(date(2026, 8, 4)) is None


# ---------------------------------------------------------------------------
# unit 042 (R2, R3, R4 · C-200..C-202): the workouts to the watch
# ---------------------------------------------------------------------------

class TestEntrenos:
    ENTRENO = {"workoutName": "Series de prueba", "sportType": {"sportTypeId": 1, "sportTypeKey": "running"}}

    def test_subir_programar_quitar_borrar_y_bajar(self, client):  # R2, R4
        c, api = client
        api.upload_workout.return_value = {"workoutId": 123, "workoutName": "Series de prueba"}
        api.schedule_workout.return_value = {"workoutScheduleId": 456, "calendarDate": "2026-10-28"}
        api.download_workout.return_value = b".FIT de prueba"
        assert c.subir_entreno(self.ENTRENO) == 123
        api.upload_workout.assert_called_once_with(self.ENTRENO)
        assert c.programar(123, date(2026, 10, 28)) == 456
        api.schedule_workout.assert_called_once_with(123, "2026-10-28")
        c.quitar_programado(456)
        api.unschedule_workout.assert_called_once_with(456)
        c.borrar_entreno(123)
        api.delete_workout.assert_called_once_with(123)
        assert c.bajar_fit(123) == b".FIT de prueba"
        api.download_workout.assert_called_once_with(123)

    def test_el_programado_que_sale_en_dos_meses_se_da_una_vez(self, client):  # bug 064
        c, api = client
        item = {"itemType": "workout", "id": 701, "workoutId": 801, "date": "2026-11-01", "title": "Tirada",
                "sportTypeKey": "running"}
        otro = {"itemType": "workout", "id": 702, "workoutId": 802, "date": "2026-10-30", "title": "Rodaje",
                "sportTypeKey": "running"}
        # the view of each month brings the days of the neighbour one: both scheduled come in both months
        api.get_scheduled_workouts.side_effect = lambda y, m: {"calendarItems": [dict(otro), dict(item)]}
        salida = c.entrenos_programados(date(2026, 10, 27), date(2026, 11, 5))
        assert [e["programado"] for e in salida] == [702, 701]

    def test_un_429_es_el_error_de_siempre(self, client):  # R3
        from garminconnect import GarminConnectTooManyRequestsError

        from tridata.garmin_client import GarminRateLimitError
        c, api = client
        api.upload_workout.side_effect = GarminConnectTooManyRequestsError("429 Too Many Requests")
        with pytest.raises(GarminRateLimitError):
            c.subir_entreno(self.ENTRENO)

    def test_un_rechazo_o_sin_conexion_es_un_error_de_entreno(self, client):  # R3
        from garminconnect import GarminConnectConnectionError

        from tridata.garmin_client import GarminWorkoutError
        c, api = client
        api.upload_workout.side_effect = GarminConnectConnectionError("400 Bad Request")
        with pytest.raises(GarminWorkoutError):
            c.subir_entreno(self.ENTRENO)
        api.schedule_workout.side_effect = None
        api.schedule_workout.return_value = {}  # no id: it was not scheduled
        with pytest.raises(GarminWorkoutError):
            c.programar(123, date(2026, 10, 28))

    def test_quitar_lo_que_ya_no_esta_no_falla(self, client):  # R4: removed by hand in Garmin Connect
        from garminconnect import GarminConnectConnectionError

        from tridata.garmin_client import GarminWorkoutError
        c, api = client
        api.unschedule_workout.side_effect = GarminConnectConnectionError("404 Not Found")
        api.delete_workout.side_effect = GarminConnectConnectionError("404 Not Found")
        c.quitar_programado(456)
        c.borrar_entreno(123)
        api.delete_workout.side_effect = GarminConnectConnectionError("500 Server Error")
        with pytest.raises(GarminWorkoutError):
            c.borrar_entreno(123)


# ---------------------------------------------------------------------------
# unit 045 (REC-25): the saved routes (courses) and the GPX of an activity
# ---------------------------------------------------------------------------

class TestRutas:
    CURSOS = {"coursesForUser": [
        {"courseId": 11, "courseName": "Ruta de prueba", "distanceInMeters": 21097.5, "elevationGainInMeters": 185.2,
         "elevationLossInMeters": 190.4, "activityType": {"typeKey": "running"}},
        {"courseId": 12, "courseName": "Otra de prueba", "distanceInMeters": 10000.0, "elevationGainInMeters": None,
         "elevationLossInMeters": None},
    ]}

    def test_la_lista_de_rutas_guardadas(self, client):  # R1 · C-193
        c, api = client
        api.connectapi.return_value = self.CURSOS
        assert c.rutas_guardadas() == [
            {"id": 11, "nombre": "Ruta de prueba", "distancia_m": 21097.5, "sube_m": 185.2, "baja_m": 190.4},
            {"id": 12, "nombre": "Otra de prueba", "distancia_m": 10000.0, "sube_m": None, "baja_m": None}]
        api.connectapi.assert_called_once_with("/web-gateway/course/owner/")

    def test_los_puntos_de_una_ruta(self, client):  # R2
        c, api = client
        api.connectapi.return_value = {"courseId": 11, "geoPoints": [
            {"distance": 0.0, "elevation": 12.0, "latitude": 1.0, "longitude": 2.0, "timestamp": None},
            {"distance": 50.0, "elevation": None, "latitude": 1.0004, "longitude": 2.0, "timestamp": None},
        ]}
        assert c.ruta(11) == [{"distancia_m": 0.0, "altitud_m": 12.0, "lat": 1.0, "lon": 2.0},
                              {"distancia_m": 50.0, "altitud_m": None, "lat": 1.0004, "lon": 2.0}]
        api.connectapi.assert_called_once_with("/course-service/course/11")

    def test_el_gpx_de_una_actividad(self, client):  # R1
        from garminconnect import Garmin
        c, api = client
        api.download_activity.return_value = b"<gpx/>"
        assert c.gpx_actividad("123") == b"<gpx/>"
        api.download_activity.assert_called_once_with("123", dl_fmt=Garmin.ActivityDownloadFormat.GPX)

    def test_si_garmin_no_responde_es_un_error_de_ruta(self, client):  # R3
        from garminconnect import GarminConnectConnectionError, GarminConnectTooManyRequestsError

        from tridata.garmin_client import GarminRateLimitError, GarminRutaError
        c, api = client
        api.connectapi.side_effect = GarminConnectConnectionError("500 Server Error")
        with pytest.raises(GarminRutaError):
            c.rutas_guardadas()
        api.connectapi.side_effect = None
        api.connectapi.return_value = {"courseId": 11}  # no points: nothing to draw
        with pytest.raises(GarminRutaError):
            c.ruta(11)
        api.download_activity.side_effect = GarminConnectTooManyRequestsError("429 Too Many Requests")
        with pytest.raises(GarminRateLimitError):
            c.gpx_actividad("123")
