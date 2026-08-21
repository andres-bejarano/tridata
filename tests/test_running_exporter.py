"""Tests for RunningMarkdownExporter and related storage/sync helpers.

All fixtures use invented data — no real health values.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from tridata.exporters import MarkdownExporter, RunningMarkdownExporter
from tridata.models import Activity, ActivityLap
from tridata.storage import DataStore
from tridata.sync import CYCLING_TYPES, RUNNING_TYPES, SyncService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_RUN_WITH_LAPS = {
    "activity_id": "run001",
    "activity_date": "2026-03-10",
    "name": "Morning run",
    "activity_type": "running",
    "duration_seconds": 2400,
    "distance_meters": 7000.0,
    "avg_hr": 148.0,
    "avg_pace_seconds_per_km": 1000.0 / 3.0,   # 5:33/km
    "avg_cadence": 168.0,
    "avg_stride_length_cm": 98.0,
    "elevation_gain_m": 25.0,
    "elevation_loss_m": 22.0,
    "laps": [
        {
            "activity_id": "run001", "lap_index": 1,
            "distance_meters": 1000.0, "duration_seconds": 335.0,
            "avg_pace_seconds_per_km": 335.0,
            "avg_hr": 145.0, "max_hr": 155.0,
            "avg_cadence": 167.0, "avg_stride_length_cm": 97.0,
            "elevation_gain_m": 5.0, "elevation_loss_m": 2.0,
            "intensity_type": "INTERVAL",
        },
        {
            "activity_id": "run001", "lap_index": 2,
            "distance_meters": 1000.0, "duration_seconds": 330.0,
            "avg_pace_seconds_per_km": 330.0,
            "avg_hr": 150.0, "max_hr": 158.0,
            "avg_cadence": 169.0, "avg_stride_length_cm": 99.0,
            "elevation_gain_m": 3.0, "elevation_loss_m": 4.0,
            "intensity_type": "INTERVAL",
        },
    ],
}

_RUN_NO_LAPS = {
    "activity_id": "run002",
    "activity_date": "2026-03-08",
    "name": "Easy jog",
    "activity_type": "running",
    "duration_seconds": 1800,
    "distance_meters": 5000.0,
    "avg_hr": 135.0,
    "avg_pace_seconds_per_km": 360.0,   # 6:00/km
    "avg_cadence": None,
    "avg_stride_length_cm": None,
    "elevation_gain_m": None,
    "elevation_loss_m": None,
    "laps": [],
}


# ---------------------------------------------------------------------------
# _fmt_lap_line shared formatter
# ---------------------------------------------------------------------------

def test_fmt_lap_line_full():
    lap = _RUN_WITH_LAPS["laps"][0]
    line = MarkdownExporter._fmt_lap_line(lap)
    assert "Lap  1" in line
    assert "1.00 km" in line
    assert "/km" in line
    assert "HR 145" in line
    assert "max 155" in line
    assert "167 spm" in line
    assert "stride 97 cm" in line
    assert "+5m" in line
    assert "[INTERVAL]" in line


def test_fmt_lap_line_missing_optional_fields():
    lap = {"lap_index": 3, "distance_meters": 500.0, "duration_seconds": 180.0}
    line = MarkdownExporter._fmt_lap_line(lap)
    assert "Lap  3" in line
    assert "0.50 km" in line
    assert "spm" not in line
    assert "stride" not in line


# ---------------------------------------------------------------------------
# RunningMarkdownExporter
# ---------------------------------------------------------------------------

class TestRunningMarkdownExporter:
    def _render(self, activities: list[dict]) -> str:
        return RunningMarkdownExporter().render(activities)

    def test_title(self):
        content = self._render([])
        assert content.startswith("# Running activities export")

    def test_activity_summary_line_present(self):
        content = self._render([_RUN_WITH_LAPS])
        assert "Morning run" in content
        assert "7.00 km" in content
        assert "5:33/km" in content
        assert "168 spm" in content
        assert "stride 98 cm" in content
        assert "+25m" in content

    def test_laps_block_rendered_when_present(self):
        content = self._render([_RUN_WITH_LAPS])
        assert "Lap  1" in content
        assert "Lap  2" in content
        # laps are indented under the activity
        assert "  - Lap  1" in content

    def test_no_laps_block_when_laps_empty(self):
        content = self._render([_RUN_NO_LAPS])
        assert "Easy jog" in content
        assert "Lap" not in content

    def test_both_activities_present(self):
        content = self._render([_RUN_WITH_LAPS, _RUN_NO_LAPS])
        assert "Morning run" in content
        assert "Easy jog" in content

    def test_export_writes_file(self, tmp_path):
        out = RunningMarkdownExporter().export([_RUN_WITH_LAPS], tmp_path / "running.md")
        assert out.exists()
        content = out.read_text()
        assert "Morning run" in content

    def test_empty_list_renders_without_error(self):
        content = self._render([])
        assert "# Running activities export" in content


# ---------------------------------------------------------------------------
# Storage: get_activity + get_activities_with_laps
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    return DataStore(tmp_path / "test.sqlite3")


def _seed(store: DataStore) -> None:
    store.save_activities([
        Activity("r1", date(2026, 3, 10), "running",     "Run A", 2400.0, distance_meters=7000.0),
        Activity("r2", date(2026, 3, 8),  "running",     "Run B", 1800.0, distance_meters=5000.0),
        Activity("c1", date(2026, 3, 9),  "road_biking", "Ride",  3600.0, distance_meters=40000.0),
        Activity("s1", date(2026, 3, 7),  "lap_swimming","Swim",  2700.0, distance_meters=2000.0),
    ])
    store.save_activity_laps([
        ActivityLap("r1", 1, distance_meters=1000.0, duration_seconds=335.0),
        ActivityLap("r1", 2, distance_meters=1000.0, duration_seconds=330.0),
    ])


class TestStorageGetActivitiesWithLaps:
    def test_get_activity_returns_payload(self, store):
        _seed(store)
        a = store.get_activity("r1")
        assert a is not None
        assert a["name"] == "Run A"

    def test_get_activity_unknown_returns_none(self, store):
        assert store.get_activity("nonexistent") is None

    def test_get_activities_with_laps_only_running(self, store):
        _seed(store)
        result = store.get_activities_with_laps(RUNNING_TYPES)
        names = [a["name"] for a in result]
        assert "Run A" in names
        assert "Run B" in names
        assert "Ride" not in names
        assert "Swim" not in names

    def test_laps_attached_to_correct_activity(self, store):
        _seed(store)
        result = store.get_activities_with_laps(RUNNING_TYPES)
        by_id = {a["activity_id"]: a for a in result}
        assert len(by_id["r1"]["laps"]) == 2
        assert by_id["r2"]["laps"] == []

    def test_ordering_newest_first(self, store):
        _seed(store)
        result = store.get_activities_with_laps(RUNNING_TYPES)
        dates = [a["activity_date"] for a in result]
        assert dates == sorted(dates, reverse=True)

    def test_cycling_types_excluded_from_running(self, store):
        _seed(store)
        running = store.get_activities_with_laps(RUNNING_TYPES)
        cycling = store.get_activities_with_laps(CYCLING_TYPES)
        run_names = {a["name"] for a in running}
        cyc_names = {a["name"] for a in cycling}
        assert "Run A" not in cyc_names
        assert "Ride" not in run_names


# ---------------------------------------------------------------------------
# sync.py: RUNNING_TYPES + CYCLING_TYPES constants
# ---------------------------------------------------------------------------

def test_running_and_cycling_types_are_disjoint():
    assert set(RUNNING_TYPES).isdisjoint(set(CYCLING_TYPES))


def test_lap_types_is_union():
    from tridata.sync import SyncService
    assert set(SyncService._LAP_TYPES) == set(RUNNING_TYPES) | set(CYCLING_TYPES)
