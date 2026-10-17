import asyncio
from datetime import date, timedelta

import pytest

from tridata.mcp_server import (
    build_server, get_activity, get_daily_metrics, get_records, get_training_load,
    get_weekly_summary, list_activities,
)
from tridata.models import Activity, ActivityLap, DailyStats, HRVRecord, PersonalRecord
from tridata.storage import DataStore

TODAY = date.today()


@pytest.fixture
def store(tmp_path):
    store = DataStore(tmp_path / "test.sqlite3")
    store.save_activities([
        Activity(
            activity_id="run1", activity_date=TODAY - timedelta(days=1),
            activity_type="running", name="Easy run", duration_seconds=3000,
            distance_meters=10000, avg_hr=140, avg_pace_seconds_per_km=300,
        ),
        Activity(
            activity_id="ride1", activity_date=TODAY - timedelta(days=2),
            activity_type="cycling", name="Long ride", duration_seconds=7200,
            distance_meters=60000, avg_hr=130, avg_pace_seconds_per_km=120,
        ),
        Activity(
            activity_id="old1", activity_date=TODAY - timedelta(days=60),
            activity_type="running", name="Old run", duration_seconds=1800,
            distance_meters=5000, avg_hr=150,
        ),
    ])
    store.save_activity_laps([
        ActivityLap(activity_id="ride1", lap_index=1, duration_seconds=3600,
                    distance_meters=30000, avg_hr=128, avg_pace_seconds_per_km=120),
    ])
    store.save_daily_stats(DailyStats(stat_date=TODAY - timedelta(days=1), steps=12000, resting_hr=50))
    store.save_daily_stats(DailyStats(stat_date=TODAY - timedelta(days=30), steps=5000, resting_hr=55))
    store.save_hrv(HRVRecord(hrv_date=TODAY - timedelta(days=1), last_night_avg=60, status="BALANCED"))
    store.save_personal_records([
        PersonalRecord(record_id="pr1", activity_type="running", value=1234.0, pr_date=date(2026, 3, 1)),
    ])
    return store


def test_list_activities_defaults_to_recent_window(store):
    out = list_activities(store)
    assert "Easy run" in out and "Long ride" in out
    assert "Old run" not in out
    assert "id run1" in out


def test_list_activities_filters_by_sport(store):
    out = list_activities(store, sport="cycling")
    assert "Long ride" in out
    assert "Easy run" not in out
    assert "km/h" in out  # cycling formatter, not running pace


def test_list_activities_rejects_unknown_sport(store):
    with pytest.raises(ValueError):
        list_activities(store, sport="rowing")


def test_list_activities_rejects_inverted_range(store):
    with pytest.raises(ValueError):
        list_activities(store, start_date="2026-05-10", end_date="2026-05-01")


def test_get_activity_includes_laps_with_sport_formatter(store):
    out = get_activity(store, "ride1")
    assert "## Laps (1)" in out
    assert "30.0 km/h" in out


def test_get_activity_unknown_id(store):
    with pytest.raises(ValueError):
        get_activity(store, "nope")


def test_get_daily_metrics_slices_by_date(store):
    out = get_daily_metrics(store)
    assert "steps: 12000" in out
    assert "steps: 5000" not in out
    assert "BALANCED" in out
    assert "Personal records" not in out


def test_get_training_load_extends_to_today(store):
    out = get_training_load(store, days=3)
    assert f"Today {TODAY}" in out
    assert out.count("\n| 20") == 3  # three table rows


def test_get_weekly_summary_counts_recent_sessions(store):
    out = get_weekly_summary(store, weeks=2)
    assert "| cycling | 1 | 2.0 | 60.0 |" in out
    assert "Old run" not in out and out.count("| running |") == 1


def test_get_records(store):
    out = get_records(store)
    assert "Personal records (1)" in out


def test_server_registers_read_only_tools(store):
    server = build_server(store)
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "list_activities", "get_activity", "get_daily_metrics",
        "get_training_load", "get_weekly_summary", "get_records",
    }
    assert all(t.annotations.read_only_hint for t in tools)
