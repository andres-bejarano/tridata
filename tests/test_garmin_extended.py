"""Regression tests for the extended GarminClient metrics."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tridata.garmin_client import GarminClient
from tridata.models import (
    BodyBatteryDay, FloorsRecord, HydrationRecord, IntensityMinutes,
    PersonalRecord, RacePrediction, RespirationRecord, SpO2Record,
    TrainingReadiness, TrainingStatus, VO2MaxRecord,
)

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2026, 8, 16)


def _load(name: str) -> dict | list:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture()
def mock_api():
    return MagicMock()


@pytest.fixture()
def client(tmp_path, mock_api):
    with patch("tridata.garmin_client.Garmin", return_value=mock_api):
        c = GarminClient("user@example.com", "secret", token_dir=tmp_path)
        c.login()
    return c, mock_api


# ---------------------------------------------------------------------------
# VO2max
# ---------------------------------------------------------------------------

class TestGetVO2Max:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_max_metrics.return_value = _load("vo2max.json")
        result = c.get_vo2max(DAY)
        assert isinstance(result, VO2MaxRecord)
        assert result.vo2max_date == DAY
        assert result.vo2max_running == 58.3
        assert result.vo2max_cycling is None  # cycling key is null in fixture

    def test_returns_none_when_api_returns_empty(self, client):
        c, api = client
        api.get_max_metrics.return_value = []
        assert c.get_vo2max(DAY) is None

    def test_returns_none_when_api_returns_none(self, client):
        c, api = client
        api.get_max_metrics.return_value = None
        assert c.get_vo2max(DAY) is None


# ---------------------------------------------------------------------------
# Training readiness
# ---------------------------------------------------------------------------

class TestGetTrainingReadiness:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_training_readiness.return_value = _load("training_readiness.json")
        result = c.get_training_readiness(DAY)
        assert isinstance(result, TrainingReadiness)
        assert result.readiness_date == DAY
        assert result.score == 6
        assert result.level == "POOR"
        assert result.feedback_short == "LET_YOUR_BODY_RECOVER"
        assert result.sleep_score == 62
        assert result.recovery_time_minutes == 49   # 2979 // 60
        assert result.hrv_weekly_avg == 44

    def test_returns_none_when_api_returns_empty(self, client):
        c, api = client
        api.get_training_readiness.return_value = []
        assert c.get_training_readiness(DAY) is None

    def test_returns_none_when_api_returns_none(self, client):
        c, api = client
        api.get_training_readiness.return_value = None
        assert c.get_training_readiness(DAY) is None


# ---------------------------------------------------------------------------
# Training status
# ---------------------------------------------------------------------------

class TestGetTrainingStatus:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_training_status.return_value = _load("training_status.json")
        result = c.get_training_status(DAY)
        assert isinstance(result, TrainingStatus)
        assert result.status_date == DAY
        assert result.training_status == 4

    def test_returns_none_when_api_returns_none(self, client):
        c, api = client
        api.get_training_status.return_value = None
        assert c.get_training_status(DAY) is None

    def test_returns_none_training_status_when_no_device_data(self, client):
        c, api = client
        api.get_training_status.return_value = {"userId": 1, "mostRecentTrainingStatus": {}}
        result = c.get_training_status(DAY)
        assert result.training_status is None


# ---------------------------------------------------------------------------
# Body battery
# ---------------------------------------------------------------------------

class TestGetBodyBattery:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_body_battery.return_value = _load("body_battery.json")
        result = c.get_body_battery(DAY)
        assert isinstance(result, BodyBatteryDay)
        assert result.bb_date == DAY
        assert result.charged == 44
        assert result.drained == 50
        assert isinstance(result.values, list)
        assert len(result.values) == 5
        assert result.values[1] == [1786855680000, 57]

    def test_returns_none_when_api_returns_empty(self, client):
        c, api = client
        api.get_body_battery.return_value = []
        assert c.get_body_battery(DAY) is None

    def test_returns_none_when_api_returns_none(self, client):
        c, api = client
        api.get_body_battery.return_value = None
        assert c.get_body_battery(DAY) is None


# ---------------------------------------------------------------------------
# SpO2 (no hardware — all fields None)
# ---------------------------------------------------------------------------

class TestGetSpO2:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_spo2_data.return_value = _load("spo2.json")
        result = c.get_spo2(DAY)
        assert isinstance(result, SpO2Record)
        assert result.spo2_date == DAY
        assert result.avg_spo2 is None
        assert result.lowest_spo2 is None
        assert result.avg_sleep_spo2 is None

    def test_returns_none_when_api_returns_none(self, client):
        c, api = client
        api.get_spo2_data.return_value = None
        assert c.get_spo2(DAY) is None


# ---------------------------------------------------------------------------
# Respiration
# ---------------------------------------------------------------------------

class TestGetRespiration:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_respiration_data.return_value = _load("respiration.json")
        result = c.get_respiration(DAY)
        assert isinstance(result, RespirationRecord)
        assert result.respiration_date == DAY
        assert result.lowest_value == 7.0
        assert result.highest_value == 22.0
        assert result.avg_waking == 15.0
        assert result.avg_sleep == 14.0

    def test_returns_none_when_api_returns_none(self, client):
        c, api = client
        api.get_respiration_data.return_value = None
        assert c.get_respiration(DAY) is None


# ---------------------------------------------------------------------------
# Floors
# ---------------------------------------------------------------------------

class TestGetFloors:
    def test_sums_intraday_buckets(self, client):
        c, api = client
        api.get_floors.return_value = _load("floors.json")
        result = c.get_floors(DAY)
        assert isinstance(result, FloorsRecord)
        assert result.floors_date == DAY
        assert result.floors_ascended == 10   # 3+5+2
        assert result.floors_descended == 6   # 0+2+4

    def test_returns_none_when_no_floor_array(self, client):
        c, api = client
        api.get_floors.return_value = {"startTimestampLocal": "2026-08-16T00:00:00.0"}
        assert c.get_floors(DAY) is None

    def test_returns_none_when_api_returns_none(self, client):
        c, api = client
        api.get_floors.return_value = None
        assert c.get_floors(DAY) is None


# ---------------------------------------------------------------------------
# Intensity minutes
# ---------------------------------------------------------------------------

class TestGetIntensityMinutes:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_intensity_minutes_data.return_value = _load("intensity_minutes.json")
        result = c.get_intensity_minutes(DAY)
        assert isinstance(result, IntensityMinutes)
        assert result.intensity_date == DAY
        assert result.weekly_moderate == 157
        assert result.weekly_vigorous == 278
        assert result.weekly_total == 713
        assert result.week_goal == 4200

    def test_returns_none_when_api_returns_none(self, client):
        c, api = client
        api.get_intensity_minutes_data.return_value = None
        assert c.get_intensity_minutes(DAY) is None


# ---------------------------------------------------------------------------
# Hydration
# ---------------------------------------------------------------------------

class TestGetHydration:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_hydration_data.return_value = _load("hydration.json")
        result = c.get_hydration(DAY)
        assert isinstance(result, HydrationRecord)
        assert result.hydration_date == DAY
        assert result.value_ml == 1200.0
        assert result.goal_ml == 2000.0
        assert result.sweat_loss_ml == 850.0

    def test_returns_none_when_api_returns_none(self, client):
        c, api = client
        api.get_hydration_data.return_value = None
        assert c.get_hydration(DAY) is None


# ---------------------------------------------------------------------------
# Personal records
# ---------------------------------------------------------------------------

class TestGetPersonalRecords:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_personal_record.return_value = _load("personal_records.json")
        result = c.get_personal_records()
        assert len(result) == 2
        assert all(isinstance(r, PersonalRecord) for r in result)
        first = result[0]
        assert first.record_id == "2000000001"
        assert first.type_id == 1
        assert first.activity_type == "running"
        assert first.value == 207.38
        assert first.pr_date == date(2026, 7, 31)

    def test_returns_empty_list_when_api_returns_none(self, client):
        c, api = client
        api.get_personal_record.return_value = None
        assert c.get_personal_records() == []

    def test_returns_empty_list_when_api_returns_empty(self, client):
        c, api = client
        api.get_personal_record.return_value = []
        assert c.get_personal_records() == []


# ---------------------------------------------------------------------------
# Race predictions
# ---------------------------------------------------------------------------

class TestGetRacePredictions:
    def test_maps_fields_from_fixture(self, client):
        c, api = client
        api.get_race_predictions.return_value = _load("race_predictions.json")
        result = c.get_race_predictions()
        assert isinstance(result, RacePrediction)
        assert result.prediction_date == date(2026, 8, 18)
        assert result.time_5k_seconds == 1024
        assert result.time_10k_seconds == 2225
        assert result.time_half_marathon_seconds == 5053
        assert result.time_marathon_seconds == 11293

    def test_returns_none_when_api_returns_none(self, client):
        c, api = client
        api.get_race_predictions.return_value = None
        assert c.get_race_predictions() is None
