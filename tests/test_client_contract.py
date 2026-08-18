"""Contract tests for FitnessClient implementations.

Every class that claims to implement FitnessClient must pass the same
assertions defined in _ContractBase.  Suunto and COROS are skipped until
their partner credentials are obtained and the clients are implemented.

To activate a skipped suite:
  1. Remove the @pytest.mark.skip decorator from its test class.
  2. Update the `client` fixture to mock (or stub) the underlying API.
  3. Add corresponding fixture JSON files under tests/fixtures/.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tridata.clients.base import FitnessClient
from tridata.clients.coros import CorosClient
from tridata.clients.garmin import GarminClient
from tridata.clients.suunto import SuuntoClient
from tridata.models import Activity, DailyStats, HRVRecord, SleepRecord

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2026, 8, 4)


def _load(name: str) -> dict | list:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Shared assertions — every FitnessClient must satisfy these
# ---------------------------------------------------------------------------

class _ContractBase:
    """Base class; subclasses provide the `client` fixture."""

    def test_satisfies_fitness_client_protocol(self, client):
        assert isinstance(client, FitnessClient)

    def test_get_activities_returns_list_of_activity(self, client):
        result = client.get_activities(DAY, DAY)
        assert isinstance(result, list)
        assert all(isinstance(a, Activity) for a in result)

    def test_get_daily_stats_returns_correct_type(self, client):
        result = client.get_daily_stats(DAY)
        assert result is None or isinstance(result, DailyStats)

    def test_get_sleep_returns_correct_type(self, client):
        result = client.get_sleep(DAY)
        assert result is None or isinstance(result, SleepRecord)

    def test_get_hrv_returns_correct_type(self, client):
        result = client.get_hrv(DAY)
        assert result is None or isinstance(result, HRVRecord)


# ---------------------------------------------------------------------------
# Garmin — fully implemented, runs against mocked garminconnect
# ---------------------------------------------------------------------------

class TestGarminContract(_ContractBase):
    @pytest.fixture()
    def client(self, tmp_path):
        mock_api = MagicMock()
        mock_api.get_activities_by_date.return_value = _load("activities.json")
        mock_api.get_stats.return_value = _load("stats.json")
        mock_api.get_sleep_data.return_value = _load("sleep.json")
        mock_api.get_hrv_data.return_value = _load("hrv.json")
        # patch in the original module so existing tests are unaffected
        with patch("tridata.garmin_client.Garmin", return_value=mock_api):
            c = GarminClient("user@example.com", "secret", token_dir=tmp_path)
            c.login()
        return c


# ---------------------------------------------------------------------------
# Suunto — skeleton only (partner API key required)
# ---------------------------------------------------------------------------

@pytest.mark.skip(
    reason=(
        "SuuntoClient not yet implemented — requires a partner API key from "
        "https://apizone.suunto.com/ (restricted to companies/organisations). "
        "Remove this skip and update the client fixture once credentials are available."
    )
)
class TestSuuntoContract(_ContractBase):
    @pytest.fixture()
    def client(self):
        # TODO: mock the underlying Suunto HTTP calls here once implemented.
        return SuuntoClient()


# ---------------------------------------------------------------------------
# COROS — skeleton only (developer partner approval required)
# ---------------------------------------------------------------------------

@pytest.mark.skip(
    reason=(
        "CorosClient not yet implemented — requires developer API key from the COROS "
        "partner programme: https://support.coros.com/hc/en-us/articles/17085887816340. "
        "Remove this skip and update the client fixture once credentials are available."
    )
)
class TestCorosContract(_ContractBase):
    @pytest.fixture()
    def client(self):
        # TODO: mock the underlying COROS HTTP calls here once implemented.
        return CorosClient()
