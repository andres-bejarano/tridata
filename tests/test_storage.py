from datetime import date

from tridata.models import Activity, DailyStats
from tridata.storage import DataStore


def test_missing_dates_full_range_when_empty(tmp_path):
    store = DataStore(tmp_path / "test.sqlite3")
    missing = store.missing_dates(
        "daily_stats", "stat_date", date(2026, 2, 1), date(2026, 2, 3)
    )
    assert missing == [date(2026, 2, 1), date(2026, 2, 2), date(2026, 2, 3)]


def test_save_and_dedupe_daily_stats(tmp_path):
    store = DataStore(tmp_path / "test.sqlite3")
    stats = DailyStats(stat_date=date(2026, 2, 1), steps=10000, resting_hr=52)
    store.save_daily_stats(stats)

    missing = store.missing_dates(
        "daily_stats", "stat_date", date(2026, 2, 1), date(2026, 2, 1)
    )
    assert missing == []

    exported = store.export_all()
    assert len(exported["daily_stats"]) == 1
    assert exported["daily_stats"][0]["steps"] == 10000


def test_save_activities_upsert(tmp_path):
    store = DataStore(tmp_path / "test.sqlite3")
    activity = Activity(
        activity_id="123",
        activity_date=date(2026, 2, 5),
        activity_type="running",
        name="Morning run",
        duration_seconds=1800,
        distance_meters=5000,
    )
    store.save_activities([activity])
    store.save_activities([activity])  # re-saving same id should not duplicate

    exported = store.export_all()
    assert len(exported["activities"]) == 1
