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
