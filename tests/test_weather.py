"""Tests for the activity weather feature.

All invented data — no real health values, no real credentials.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from tridata.exporters import CyclingMarkdownExporter, RunningMarkdownExporter
from tridata.garmin_client import GarminClient
from tridata.models import Activity
from tridata.storage import DataStore
from tridata.sync import SyncService


# ---------------------------------------------------------------------------
# Unit helpers: F→C and mph→km/h conversions
# ---------------------------------------------------------------------------

def _make_client(tmp_path, mock_api):
    with patch("tridata.garmin_client.Garmin", return_value=mock_api):
        c = GarminClient("user@example.com", "secret", token_dir=tmp_path)
        c.login()
    return c


class TestFahrenheitToCelsius:
    """Verify the F→C conversion embedded in get_activity_weather."""

    def _convert(self, f_value, tmp_path):
        mock_api = MagicMock()
        mock_api.get_activity_weather.return_value = {
            "temp": f_value,
            "apparentTemp": None,
            "relativeHumidity": None,
            "windSpeed": None,
            "windDirection": None,
            "weatherTypeDTO": {"desc": None},
            "weatherStationDTO": {"name": None},
        }
        c = _make_client(tmp_path, mock_api)
        return c.get_activity_weather("any")["temp_c"]

    def test_freezing_point(self, tmp_path):
        assert self._convert(32, tmp_path) == 0.0

    def test_boiling_point(self, tmp_path):
        assert self._convert(212, tmp_path) == 100.0

    def test_typical_run_temperature(self, tmp_path):
        # 68°F = 20.0°C
        assert self._convert(68, tmp_path) == 20.0

    def test_rounding_to_one_decimal(self, tmp_path):
        # 77°F = 25.0°C (exact), 73°F = 22.777...→ 22.8°C
        assert self._convert(73, tmp_path) == 22.8


class TestMphToKmh:
    """Verify the mph→km/h conversion embedded in get_activity_weather."""

    def _convert(self, mph_value, tmp_path):
        mock_api = MagicMock()
        mock_api.get_activity_weather.return_value = {
            "temp": None,
            "apparentTemp": None,
            "relativeHumidity": None,
            "windSpeed": mph_value,
            "windDirection": None,
            "weatherTypeDTO": {"desc": None},
            "weatherStationDTO": {"name": None},
        }
        c = _make_client(tmp_path, mock_api)
        return c.get_activity_weather("any")["wind_speed_kmh"]

    def test_zero_mph(self, tmp_path):
        assert self._convert(0, tmp_path) == 0.0

    def test_typical_light_wind(self, tmp_path):
        # 10 mph → 16.1 km/h (10 * 1.60934 = 16.0934 → 16.1)
        assert self._convert(10, tmp_path) == 16.1

    def test_none_remains_none(self, tmp_path):
        assert self._convert(None, tmp_path) is None


# ---------------------------------------------------------------------------
# GarminClient.get_activity_weather
# ---------------------------------------------------------------------------

_FULL_WEATHER_RAW = {
    "temp": 68,
    "apparentTemp": 65,
    "relativeHumidity": 75,
    "windSpeed": 10,
    "windDirection": 180,
    "weatherTypeDTO": {"desc": "Partly Cloudy"},
    "weatherStationDTO": {"name": "Test Station"},
}


class TestGetActivityWeather:
    def test_full_response_maps_all_fields(self, tmp_path):
        mock_api = MagicMock()
        mock_api.get_activity_weather.return_value = _FULL_WEATHER_RAW
        c = _make_client(tmp_path, mock_api)

        result = c.get_activity_weather("act-001")

        assert result is not None
        assert result["temp_c"] == 20.0
        assert result["apparent_temp_c"] == 18.3
        assert result["humidity_pct"] == 75
        assert result["wind_speed_kmh"] == 16.1
        assert result["wind_direction_deg"] == 180
        assert result["condition"] == "Partly Cloudy"
        assert result["station_name"] == "Test Station"

    def test_null_wind_gust_does_not_break(self, tmp_path):
        raw = {**_FULL_WEATHER_RAW, "windGust": None}
        mock_api = MagicMock()
        mock_api.get_activity_weather.return_value = raw
        c = _make_client(tmp_path, mock_api)
        result = c.get_activity_weather("act-001")
        assert result is not None

    def test_api_returns_none_yields_none(self, tmp_path):
        mock_api = MagicMock()
        mock_api.get_activity_weather.return_value = None
        c = _make_client(tmp_path, mock_api)
        assert c.get_activity_weather("act-001") is None

    def test_api_returns_empty_dict_yields_none(self, tmp_path):
        mock_api = MagicMock()
        mock_api.get_activity_weather.return_value = {}
        c = _make_client(tmp_path, mock_api)
        assert c.get_activity_weather("act-001") is None

    def test_api_raises_exception_yields_none(self, tmp_path):
        mock_api = MagicMock()
        mock_api.get_activity_weather.side_effect = RuntimeError("network error")
        c = _make_client(tmp_path, mock_api)
        assert c.get_activity_weather("act-001") is None

    def test_missing_weather_type_dto_yields_none_condition(self, tmp_path):
        raw = {**_FULL_WEATHER_RAW, "weatherTypeDTO": None}
        mock_api = MagicMock()
        mock_api.get_activity_weather.return_value = raw
        c = _make_client(tmp_path, mock_api)
        result = c.get_activity_weather("act-001")
        assert result["condition"] is None

    def test_missing_station_dto_yields_none_name(self, tmp_path):
        raw = {**_FULL_WEATHER_RAW, "weatherStationDTO": None}
        mock_api = MagicMock()
        mock_api.get_activity_weather.return_value = raw
        c = _make_client(tmp_path, mock_api)
        result = c.get_activity_weather("act-001")
        assert result["station_name"] is None


# ---------------------------------------------------------------------------
# DataStore: save_activity_weather / get_weather / activity_ids_weather_synced
# ---------------------------------------------------------------------------

_WEATHER = {
    "temp_c": 20.0,
    "apparent_temp_c": 18.3,
    "humidity_pct": 75.0,
    "wind_speed_kmh": 16.1,
    "wind_direction_deg": 180.0,
    "condition": "Partly Cloudy",
    "station_name": "Test Station",
}


@pytest.fixture()
def store(tmp_path) -> DataStore:
    return DataStore(tmp_path / "test.sqlite3")


class TestDataStoreWeather:
    def test_weather_round_trip(self, store):
        store.save_activity_weather("act-001", _WEATHER)
        result = store.get_weather("act-001")
        assert result is not None
        assert result["temp_c"] == 20.0
        assert result["humidity_pct"] == 75.0
        assert result["condition"] == "Partly Cloudy"
        assert result["station_name"] == "Test Station"

    def test_weather_round_trip_all_fields(self, store):
        store.save_activity_weather("act-001", _WEATHER)
        result = store.get_weather("act-001")
        assert result["apparent_temp_c"] == 18.3
        assert result["wind_speed_kmh"] == 16.1
        assert result["wind_direction_deg"] == 180.0

    def test_none_weather_stored_returns_none(self, store):
        store.save_activity_weather("act-002", None)
        assert store.get_weather("act-002") is None

    def test_unknown_activity_returns_none(self, store):
        assert store.get_weather("nonexistent") is None

    def test_activity_ids_weather_synced_includes_both_with_and_without_data(self, store):
        store.save_activity_weather("act-001", _WEATHER)
        store.save_activity_weather("act-002", None)
        synced = store.activity_ids_weather_synced()
        assert "act-001" in synced
        assert "act-002" in synced

    def test_activity_ids_weather_synced_empty_initially(self, store):
        assert store.activity_ids_weather_synced() == set()

    def test_overwrite_weather_replaces_previous(self, store):
        store.save_activity_weather("act-001", _WEATHER)
        updated = {**_WEATHER, "temp_c": 25.0, "condition": "Clear"}
        store.save_activity_weather("act-001", updated)
        result = store.get_weather("act-001")
        assert result["temp_c"] == 25.0
        assert result["condition"] == "Clear"


class TestGetActivitiesWithLapsWeather:
    def _seed(self, store: DataStore) -> None:
        store.save_activities([
            Activity("r1", date(2026, 3, 10), "running", "Run A", 2400.0),
            Activity("r2", date(2026, 3, 8),  "running", "Run B", 1800.0),
        ])

    def test_weather_attached_when_synced(self, store):
        self._seed(store)
        store.save_activity_weather("r1", _WEATHER)
        from tridata.sync import RUNNING_TYPES
        result = store.get_activities_with_laps(RUNNING_TYPES)
        by_id = {a["activity_id"]: a for a in result}
        assert by_id["r1"]["weather"] is not None
        assert by_id["r1"]["weather"]["temp_c"] == 20.0

    def test_weather_is_none_when_not_synced(self, store):
        self._seed(store)
        from tridata.sync import RUNNING_TYPES
        result = store.get_activities_with_laps(RUNNING_TYPES)
        by_id = {a["activity_id"]: a for a in result}
        assert by_id["r1"]["weather"] is None

    def test_weather_is_none_when_api_had_no_data(self, store):
        self._seed(store)
        store.save_activity_weather("r1", None)
        from tridata.sync import RUNNING_TYPES
        result = store.get_activities_with_laps(RUNNING_TYPES)
        by_id = {a["activity_id"]: a for a in result}
        assert by_id["r1"]["weather"] is None


# ---------------------------------------------------------------------------
# SyncService.sync_weather
# ---------------------------------------------------------------------------

class _StubWeatherClient:
    def __init__(self, weather_map: dict[str, dict | None]) -> None:
        self._weather_map = weather_map
        self.calls: list[str] = []
        self.login_count = 0

    def login(self) -> None:
        self.login_count += 1

    def get_activity_weather(self, activity_id: str) -> dict | None:
        self.calls.append(activity_id)
        return self._weather_map.get(activity_id)


def _seed_running_activities(store: DataStore, ids: list[tuple[str, date]]) -> None:
    store.save_activities([
        Activity(aid, day, "running", f"Run {aid}", 3600.0, distance_meters=10000.0)
        for aid, day in ids
    ])


class TestSyncWeather:
    def test_login_called(self, store):
        _seed_running_activities(store, [("r1", date(2026, 3, 10))])
        client = _StubWeatherClient({"r1": _WEATHER})
        SyncService(client, store).sync_weather()
        assert client.login_count == 1

    def test_weather_fetched_and_saved(self, store):
        _seed_running_activities(store, [("r1", date(2026, 3, 10))])
        client = _StubWeatherClient({"r1": _WEATHER})
        SyncService(client, store).sync_weather()
        assert store.get_weather("r1") is not None

    def test_already_synced_not_reprocessed(self, store):
        _seed_running_activities(store, [("r1", date(2026, 3, 10))])
        client = _StubWeatherClient({"r1": _WEATHER})
        svc = SyncService(client, store)
        svc.sync_weather()
        client.calls.clear()
        svc.sync_weather()
        assert client.calls == []

    def test_limit_respected(self, store):
        ids = [(f"r{i}", date(2026, 3, i)) for i in range(1, 11)]  # 10 activities
        _seed_running_activities(store, ids)
        weather_map = {aid: _WEATHER for aid, _ in ids}
        client = _StubWeatherClient(weather_map)
        SyncService(client, store).sync_weather(limit=3)
        assert len(client.calls) == 3

    def test_none_weather_still_marks_synced(self, store):
        _seed_running_activities(store, [("r1", date(2026, 3, 10))])
        client = _StubWeatherClient({"r1": None})
        SyncService(client, store).sync_weather()
        synced = store.activity_ids_weather_synced()
        assert "r1" in synced

    def test_returns_count_processed(self, store):
        ids = [(f"r{i}", date(2026, 3, i)) for i in range(1, 6)]
        _seed_running_activities(store, ids)
        weather_map = {aid: _WEATHER for aid, _ in ids}
        client = _StubWeatherClient(weather_map)
        n = SyncService(client, store).sync_weather(limit=3)
        assert n == 3

    def test_cycling_activities_included(self, store):
        store.save_activities([
            Activity("c1", date(2026, 3, 10), "cycling", "Ride", 7200.0, distance_meters=50000.0)
        ])
        client = _StubWeatherClient({"c1": _WEATHER})
        SyncService(client, store).sync_weather()
        assert "c1" in client.calls

    def test_swimming_activities_excluded(self, store):
        store.save_activities([
            Activity("s1", date(2026, 3, 10), "lap_swimming", "Swim", 2700.0)
        ])
        client = _StubWeatherClient({"s1": _WEATHER})
        SyncService(client, store).sync_weather()
        assert "s1" not in client.calls


# ---------------------------------------------------------------------------
# Exporter rendering: weather columns in running and cycling
# ---------------------------------------------------------------------------

_RUN_WITH_WEATHER = {
    "activity_id": "run001",
    "activity_date": "2026-03-10",
    "name": "Morning Run",
    "activity_type": "running",
    "duration_seconds": 3600,
    "distance_meters": 10000.0,
    "avg_hr": 148.0,
    "avg_pace_seconds_per_km": 360.0,
    "avg_cadence": None,
    "avg_stride_length_cm": None,
    "elevation_gain_m": None,
    "elevation_loss_m": None,
    "laps": [],
    "weather": {
        "temp_c": 20.0,
        "apparent_temp_c": 18.3,
        "humidity_pct": 75.0,
        "wind_speed_kmh": 16.1,
        "wind_direction_deg": 180.0,
        "condition": "Partly Cloudy",
        "station_name": "Test Station",
    },
}

_RUN_WITHOUT_WEATHER = {
    **_RUN_WITH_WEATHER,
    "activity_id": "run002",
    "name": "Easy Jog",
    "weather": None,
}

_RIDE_WITH_WEATHER = {
    "activity_id": "cyc001",
    "activity_date": "2026-03-12",
    "name": "Morning Ride",
    "activity_type": "road_biking",
    "duration_seconds": 7200,
    "distance_meters": 50000.0,
    "avg_hr": 145.0,
    "avg_pace_seconds_per_km": 1000.0 / 7.0,
    "avg_cadence": None,
    "avg_stride_length_cm": None,
    "avg_power": None,
    "elevation_gain_m": 800.0,
    "elevation_loss_m": 790.0,
    "laps": [],
    "weather": {
        "temp_c": 18.0,
        "apparent_temp_c": 17.0,
        "humidity_pct": 60.0,
        "wind_speed_kmh": 8.0,
        "wind_direction_deg": 90.0,
        "condition": "Clear",
        "station_name": "Another Station",
    },
}

_RIDE_WITHOUT_WEATHER = {
    **_RIDE_WITH_WEATHER,
    "activity_id": "cyc002",
    "name": "Club Ride",
    "weather": None,
}


class TestRunningExporterWeather:
    def test_weather_fields_appear_in_line(self):
        content = RunningMarkdownExporter().render([_RUN_WITH_WEATHER])
        assert "20.0°C" in content
        assert "sens. 18.3°C" in content
        assert "75% hum" in content
        assert "16.1 km/h viento" in content
        assert "Partly Cloudy" in content

    def test_no_weather_shows_dash(self):
        content = RunningMarkdownExporter().render([_RUN_WITHOUT_WEATHER])
        assert "Easy Jog" in content
        assert "| -" in content

    def test_both_activities_rendered(self):
        content = RunningMarkdownExporter().render([_RUN_WITH_WEATHER, _RUN_WITHOUT_WEATHER])
        assert "20.0°C" in content
        assert "| -" in content

    def test_weather_separator_present(self):
        content = RunningMarkdownExporter().render([_RUN_WITH_WEATHER])
        assert "|" in content


class TestCyclingExporterWeather:
    def test_weather_fields_appear_in_line(self):
        content = CyclingMarkdownExporter().render([_RIDE_WITH_WEATHER])
        assert "18.0°C" in content
        assert "sens. 17.0°C" in content
        assert "60% hum" in content
        assert "8.0 km/h viento" in content
        assert "Clear" in content

    def test_no_weather_shows_dash(self):
        content = CyclingMarkdownExporter().render([_RIDE_WITHOUT_WEATHER])
        assert "Club Ride" in content
        assert "| -" in content

    def test_both_activities_rendered(self):
        content = CyclingMarkdownExporter().render([_RIDE_WITH_WEATHER, _RIDE_WITHOUT_WEATHER])
        assert "18.0°C" in content
        assert "| -" in content


class TestFmtWeatherStatic:
    def test_full_weather_all_fields(self):
        result = MarkdownExporter._fmt_weather(_RUN_WITH_WEATHER["weather"])
        assert "20.0°C" in result
        assert "sens. 18.3°C" in result
        assert "75% hum" in result
        assert "16.1 km/h viento" in result
        assert "Partly Cloudy" in result

    def test_none_yields_dash(self):
        assert MarkdownExporter._fmt_weather(None) == "-"

    def test_empty_dict_yields_dash(self):
        assert MarkdownExporter._fmt_weather({}) == "-"

    def test_partial_weather_only_available_fields(self):
        partial = {"temp_c": 15.0, "apparent_temp_c": None, "humidity_pct": None,
                   "wind_speed_kmh": None, "condition": None}
        result = MarkdownExporter._fmt_weather(partial)
        assert "15.0°C" in result
        assert "sens." not in result
        assert "hum" not in result


# Confirm _fmt_weather is accessible (it's a static method on MarkdownExporter)
from tridata.exporters import MarkdownExporter  # noqa: E402
