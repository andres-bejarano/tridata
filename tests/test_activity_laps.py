"""Tests for per-lap splits: models, storage, client, and sync idempotency.

All fixtures use invented data — no real health values.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from tridata.garmin_client import GarminClient
from tridata.models import Activity, ActivityLap, pace_seconds_per_km
from tridata.storage import DataStore
from tridata.sync import SyncService


# ---------------------------------------------------------------------------
# pace_seconds_per_km helper
# ---------------------------------------------------------------------------

def test_pace_typical_speed():
    # 3.0 m/s → 1000/3 = 333.33… s/km
    assert pace_seconds_per_km(3.0) == pytest.approx(333.33, rel=1e-3)


def test_pace_from_none_returns_none():
    assert pace_seconds_per_km(None) is None


def test_pace_from_zero_returns_none():
    assert pace_seconds_per_km(0.0) is None


# ---------------------------------------------------------------------------
# Fake lap DTOs (entirely invented)
# ---------------------------------------------------------------------------

_FAKE_SPLITS_RESPONSE = {
    "activityId": 999000111,
    "lapDTOs": [
        {
            "lapIndex": 1,
            "distance": 1000.0,
            "duration": 360.0,
            "averageSpeed": 2.78,
            "averageHR": 142.0,
            "maxHR": 155.0,
            "averageRunCadence": 164.0,
            "strideLength": 101.0,
            "elevationGain": 3.0,
            "elevationLoss": 1.0,
            "intensityType": "INTERVAL",
        },
        {
            "lapIndex": 2,
            "distance": 1000.0,
            "duration": 355.0,
            "averageSpeed": 2.82,
            "averageHR": 146.0,
            "maxHR": 158.0,
            "averageRunCadence": 166.0,
            "strideLength": 103.0,
            "elevationGain": 0.0,
            "elevationLoss": 4.0,
            "intensityType": "INTERVAL",
        },
    ],
    "eventDTOs": [],
}


# ---------------------------------------------------------------------------
# GarminClient.get_activity_laps
# ---------------------------------------------------------------------------

def _client_with_splits(response: dict) -> GarminClient:
    client = GarminClient.__new__(GarminClient)
    mock_api = MagicMock()
    mock_api.get_activity_splits.return_value = response
    client._api = mock_api
    return client


class TestGetActivityLaps:
    def test_maps_lap_dtos_correctly(self):
        client = _client_with_splits(_FAKE_SPLITS_RESPONSE)
        laps = client.get_activity_laps("act-abc")

        assert len(laps) == 2

        lap = laps[0]
        assert lap.activity_id == "act-abc"
        assert lap.lap_index == 1
        assert lap.distance_meters == 1000.0
        assert lap.duration_seconds == pytest.approx(360.0)
        assert lap.avg_hr == 142.0
        assert lap.max_hr == 155.0
        assert lap.avg_cadence == 164.0
        assert lap.avg_stride_length_cm == 101.0
        assert lap.elevation_gain_m == 3.0
        assert lap.elevation_loss_m == 1.0
        assert lap.intensity_type == "INTERVAL"
        assert lap.avg_pace_seconds_per_km == pytest.approx(1000.0 / 2.78, rel=1e-3)

    def test_second_lap_mapped(self):
        client = _client_with_splits(_FAKE_SPLITS_RESPONSE)
        laps = client.get_activity_laps("act-abc")
        lap2 = laps[1]
        assert lap2.lap_index == 2
        assert lap2.avg_pace_seconds_per_km == pytest.approx(1000.0 / 2.82, rel=1e-3)

    def test_returns_empty_list_when_lap_dtos_absent(self):
        client = _client_with_splits({"eventDTOs": []})
        assert client.get_activity_laps("act-xyz") == []

    def test_returns_empty_list_when_lap_dtos_empty(self):
        client = _client_with_splits({"lapDTOs": [], "eventDTOs": []})
        assert client.get_activity_laps("act-xyz") == []

    def test_lap_without_speed_has_none_pace(self):
        response = {
            "lapDTOs": [{"lapIndex": 1, "distance": 500.0, "duration": 200.0}],
            "eventDTOs": [],
        }
        client = _client_with_splits(response)
        laps = client.get_activity_laps("act-nospeed")
        assert laps[0].avg_pace_seconds_per_km is None


# ---------------------------------------------------------------------------
# DataStore: activity_laps table
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    return DataStore(tmp_path / "test.sqlite3")


def _make_laps(activity_id: str, n: int = 2) -> list[ActivityLap]:
    return [
        ActivityLap(
            activity_id=activity_id,
            lap_index=i,
            distance_meters=1000.0,
            duration_seconds=360.0 + i,
            avg_hr=140.0 + i,
        )
        for i in range(1, n + 1)
    ]


class TestDataStoreLaps:
    def test_save_and_retrieve(self, store):
        laps = _make_laps("act1", n=3)
        store.save_activity_laps(laps)
        retrieved = store.get_laps("act1")
        assert len(retrieved) == 3
        assert retrieved[0]["lap_index"] == 1
        assert retrieved[2]["lap_index"] == 3

    def test_get_laps_unknown_activity_returns_empty(self, store):
        assert store.get_laps("nonexistent") == []

    def test_upsert_replaces_existing_lap(self, store):
        store.save_activity_laps([ActivityLap("act1", 1, distance_meters=1000.0)])
        store.save_activity_laps([ActivityLap("act1", 1, distance_meters=999.0)])
        retrieved = store.get_laps("act1")
        assert len(retrieved) == 1
        assert retrieved[0]["distance_meters"] == 999.0

    def test_activity_ids_with_laps_empty(self, store):
        assert store.activity_ids_with_laps() == set()

    def test_activity_ids_with_laps_populated(self, store):
        store.save_activity_laps(_make_laps("act1"))
        store.save_activity_laps(_make_laps("act2"))
        assert store.activity_ids_with_laps() == {"act1", "act2"}

    def test_get_activity_ids_by_type(self, store):
        store.save_activities([
            Activity("r1", date(2026, 1, 10), "running",  "Run A", 1800.0),
            Activity("r2", date(2026, 1, 12), "running",  "Run B", 2000.0),
            Activity("s1", date(2026, 1, 11), "lap_swimming", "Swim", 2700.0),
        ])
        ids = store.get_activity_ids_by_type(("running",))
        # newest first
        assert ids == ["r2", "r1"]
        assert "s1" not in ids

    def test_get_activity_ids_by_type_empty_table(self, store):
        assert store.get_activity_ids_by_type(("running",)) == []


# ---------------------------------------------------------------------------
# SyncService.sync_laps — idempotency
# ---------------------------------------------------------------------------

class TestSyncLapsIdempotency:
    def _seed_activities(self, store: DataStore) -> None:
        store.save_activities([
            Activity("old-act", date(2026, 1, 5),  "running", "Old run", 1500.0),
            Activity("new-act", date(2026, 1, 10), "running", "New run", 2000.0),
        ])

    def test_already_synced_activity_not_refetched(self, store):
        self._seed_activities(store)
        # Mark old-act as already having laps
        store.save_activity_laps([ActivityLap("old-act", 1, distance_meters=1000.0)])

        mock_client = MagicMock()
        mock_client.get_activity_laps.return_value = [
            ActivityLap("new-act", 1, distance_meters=1000.0)
        ]

        SyncService(mock_client, store).sync_laps(limit=20)

        mock_client.get_activity_laps.assert_called_once_with("new-act")

    def test_limit_caps_activities_processed(self, store):
        store.save_activities([
            Activity(f"act{i}", date(2026, 1, i + 1), "running", f"Run {i}", 1800.0)
            for i in range(1, 6)
        ])

        mock_client = MagicMock()
        mock_client.get_activity_laps.return_value = []

        SyncService(mock_client, store).sync_laps(limit=3)

        assert mock_client.get_activity_laps.call_count == 3

    def test_non_running_types_excluded(self, store):
        store.save_activities([
            Activity("swim1", date(2026, 1, 10), "lap_swimming", "Swim", 2700.0),
        ])

        mock_client = MagicMock()
        SyncService(mock_client, store).sync_laps(limit=20)

        mock_client.get_activity_laps.assert_not_called()

    def test_returns_count_of_activities_attempted(self, store):
        store.save_activities([
            Activity("a1", date(2026, 1, 1), "running", "R1", 1800.0),
            Activity("a2", date(2026, 1, 2), "running", "R2", 1800.0),
        ])
        mock_client = MagicMock()
        mock_client.get_activity_laps.return_value = []

        n = SyncService(mock_client, store).sync_laps(limit=5)
        assert n == 2
