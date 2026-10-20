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


# -- unit 008: readers by date for the margin of the day (invented rows) ------------------------

def _act(id_, dia, kcal=300):
    return Activity(activity_id=id_, activity_date=dia, activity_type="lap_swimming", name="Natación",
                    duration_seconds=2700, calories=kcal)


def test_activities_between_solo_las_del_rango(tmp_path):  # R1, R5
    store = DataStore(tmp_path / "test.sqlite3")
    store.save_activities([_act("a", date(2026, 3, 9)), _act("b", date(2026, 3, 10)), _act("c", date(2026, 3, 11)),
                           _act("d", date(2026, 2, 20))])

    assert [a["activity_id"] for a in store.activities_between(date(2026, 3, 10), date(2026, 3, 10))] == ["b"]
    assert [a["activity_id"] for a in store.activities_between(date(2026, 3, 9), date(2026, 3, 11))] == ["a", "b", "c"]
    assert store.activities_between(date(2026, 1, 1), date(2026, 1, 31)) == []
    assert store.activities_between(date(2026, 3, 10), date(2026, 3, 10))[0]["calories"] == 300


def test_daily_stats_on_un_dia_o_none(tmp_path):  # R1, R4
    store = DataStore(tmp_path / "test.sqlite3")
    store.save_daily_stats(DailyStats(stat_date=date(2026, 3, 9), steps=4000))
    store.save_daily_stats(DailyStats(stat_date=date(2026, 3, 10), steps=12000))

    assert store.daily_stats_on(date(2026, 3, 10))["steps"] == 12000
    assert store.daily_stats_on(date(2026, 3, 11)) is None
