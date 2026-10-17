"""Regression tests for SyncService — no real network, no real Garmin credentials."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from tridata.models import Activity, DailyStats, HRVRecord, SleepRecord
from tridata.storage import DataStore
from tridata.sync import SyncService


# ---------------------------------------------------------------------------
# Stub — same public interface as GarminClient, records every call made to it
# ---------------------------------------------------------------------------

class _StubClient:
    def __init__(
        self,
        activities: list[Activity] | None = None,
        daily_stats: dict[date, DailyStats] | None = None,
        sleep: dict[date, SleepRecord] | None = None,
        hrv: dict[date, HRVRecord] | None = None,
    ) -> None:
        self._activities = activities or []
        self._daily_stats: dict[date, DailyStats] = daily_stats or {}
        self._sleep: dict[date, SleepRecord] = sleep or {}
        self._hrv: dict[date, HRVRecord] = hrv or {}

        self.login_count = 0
        self.get_activities_calls: list[tuple[date, date]] = []
        self.get_daily_stats_calls: list[date] = []
        self.get_sleep_calls: list[date] = []
        self.get_hrv_calls: list[date] = []

    def login(self) -> None:
        self.login_count += 1

    def get_activities(self, start: date, end: date) -> list[Activity]:
        self.get_activities_calls.append((start, end))
        return self._activities

    def get_daily_stats(self, day: date) -> DailyStats | None:
        self.get_daily_stats_calls.append(day)
        return self._daily_stats.get(day)

    def get_sleep(self, day: date) -> SleepRecord | None:
        self.get_sleep_calls.append(day)
        return self._sleep.get(day)

    def get_hrv(self, day: date) -> HRVRecord | None:
        self.get_hrv_calls.append(day)
        return self._hrv.get(day)


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

DAY1 = date(2026, 8, 4)
DAY2 = date(2026, 8, 5)
RANGE = (DAY1, DAY2)


def _activity(day: date, activity_id: str) -> Activity:
    return Activity(
        activity_id=activity_id,
        activity_date=day,
        activity_type="running",
        name="Morning Run",
        duration_seconds=3600.0,
        distance_meters=10000.0,
        avg_hr=145.0,
    )


def _stats(day: date) -> DailyStats:
    return DailyStats(stat_date=day, steps=10000, resting_hr=52)


def _sleep(day: date) -> SleepRecord:
    return SleepRecord(sleep_date=day, total_sleep_seconds=25200, sleep_score=78)


def _hrv(day: date) -> HRVRecord:
    return HRVRecord(hrv_date=day, last_night_avg=46.0, status="BALANCED")


@pytest.fixture()
def store(tmp_path) -> DataStore:
    return DataStore(tmp_path / "test.sqlite3")


@pytest.fixture()
def full_client() -> _StubClient:
    return _StubClient(
        activities=[_activity(DAY1, "act-001"), _activity(DAY2, "act-002")],
        daily_stats={DAY1: _stats(DAY1), DAY2: _stats(DAY2)},
        sleep={DAY1: _sleep(DAY1), DAY2: _sleep(DAY2)},
        hrv={DAY1: _hrv(DAY1), DAY2: _hrv(DAY2)},
    )


# ---------------------------------------------------------------------------
# (a) First sync on an empty store: everything must be fetched and persisted
# ---------------------------------------------------------------------------

class TestFirstSync:
    def test_login_called_once(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert full_client.login_count == 1

    def test_activities_fetched_for_full_range(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert full_client.get_activities_calls == [(DAY1, DAY2)]

    def test_stats_fetched_for_every_missing_day(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert set(full_client.get_daily_stats_calls) == {DAY1, DAY2}

    def test_sleep_fetched_for_every_missing_day(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert set(full_client.get_sleep_calls) == {DAY1, DAY2}

    def test_hrv_fetched_for_every_missing_day(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert set(full_client.get_hrv_calls) == {DAY1, DAY2}

    def test_activities_persisted(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert len(store.export_all()["activities"]) == 2

    def test_stats_persisted(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert len(store.export_all()["daily_stats"]) == 2

    def test_sleep_persisted(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert len(store.export_all()["sleep"]) == 2

    def test_hrv_persisted(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        assert len(store.export_all()["hrv"]) == 2


# ---------------------------------------------------------------------------
# (b) Second sync over the same range must be a no-op
# ---------------------------------------------------------------------------

class TestIdempotency:
    def _sync_twice(self, store: DataStore, client: _StubClient) -> None:
        svc = SyncService(client, store)
        svc.sync(*RANGE)
        client.get_activities_calls.clear()
        client.get_daily_stats_calls.clear()
        client.get_sleep_calls.clear()
        client.get_hrv_calls.clear()
        svc.sync(*RANGE)

    def test_no_activities_fetched_on_second_sync(self, store, full_client):
        self._sync_twice(store, full_client)
        assert full_client.get_activities_calls == []

    def test_no_stats_fetched_on_second_sync(self, store, full_client):
        self._sync_twice(store, full_client)
        assert full_client.get_daily_stats_calls == []

    def test_no_sleep_fetched_on_second_sync(self, store, full_client):
        self._sync_twice(store, full_client)
        assert full_client.get_sleep_calls == []

    def test_no_hrv_fetched_on_second_sync(self, store, full_client):
        self._sync_twice(store, full_client)
        assert full_client.get_hrv_calls == []

    def test_row_count_unchanged_after_second_sync(self, store, full_client):
        svc = SyncService(full_client, store)
        svc.sync(*RANGE)
        snapshot = store.export_all()
        svc.sync(*RANGE)
        assert store.export_all() == snapshot


# ---------------------------------------------------------------------------
# (c) Field values survive the store round-trip
# ---------------------------------------------------------------------------

class TestDataIntegrity:
    def test_activity_ids_round_trip(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        ids = {a["activity_id"] for a in store.export_all()["activities"]}
        assert ids == {"act-001", "act-002"}

    def test_stats_fields_round_trip(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        rows = {r["stat_date"]: r for r in store.export_all()["daily_stats"]}
        assert rows["2026-08-04"]["steps"] == 10000
        assert rows["2026-08-04"]["resting_hr"] == 52

    def test_sleep_fields_round_trip(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        rows = {r["sleep_date"]: r for r in store.export_all()["sleep"]}
        assert rows["2026-08-04"]["total_sleep_seconds"] == 25200
        assert rows["2026-08-04"]["sleep_score"] == 78

    def test_hrv_fields_round_trip(self, store, full_client):
        SyncService(full_client, store).sync(*RANGE)
        rows = {r["hrv_date"]: r for r in store.export_all()["hrv"]}
        assert rows["2026-08-04"]["last_night_avg"] == 46.0
        assert rows["2026-08-04"]["status"] == "BALANCED"

    def test_empty_client_does_not_crash(self, store):
        SyncService(_StubClient(), store).sync(*RANGE)
        exported = store.export_all()
        assert exported["activities"] == []
        assert exported["daily_stats"] == []
        assert exported["sleep"] == []
        assert exported["hrv"] == []


# ---------------------------------------------------------------------------
# (d) `revalidate_days` override — the mechanism behind the weekly wide sweep
# ---------------------------------------------------------------------------

class TestRevalidateDaysOverride:
    def test_default_window_refetches_recent_present_day(self, store):
        """A day inside the default RECENT_REVALIDATE_DAYS window keeps getting
        re-fetched even once it's already stored — this is the existing
        short-window behavior the weekly sweep builds on."""
        today = date.today()
        client = _StubClient(daily_stats={today: _stats(today)})
        svc = SyncService(client, store)
        svc.sync(since=today, until=today)
        client.get_daily_stats_calls.clear()

        svc.sync(since=today, until=today)
        assert client.get_daily_stats_calls == [today]

    def test_revalidate_days_zero_skips_already_present_recent_day(self, store):
        """Passing revalidate_days=0 disables revalidation entirely, even for
        a day that the default window would normally re-fetch."""
        today = date.today()
        client = _StubClient(daily_stats={today: _stats(today)})
        svc = SyncService(client, store)
        svc.sync(since=today, until=today)
        client.get_daily_stats_calls.clear()

        svc.sync(since=today, until=today, revalidate_days=0)
        assert client.get_daily_stats_calls == []

    def test_wide_revalidate_days_catches_day_outside_default_window(self, store):
        """A day that went stale 10 days ago sits outside the default 3-day
        window forever — but a weekly sweep with a wide revalidate_days
        override (e.g. 45) reaches back far enough to catch it, exactly like
        the Aug 20 / Aug 24 case that motivated this."""
        stale_day = date.today() - timedelta(days=10)
        client = _StubClient(daily_stats={stale_day: _stats(stale_day)})
        svc = SyncService(client, store)
        svc.sync(since=stale_day, until=date.today(), revalidate_days=15)
        client.get_daily_stats_calls.clear()

        svc.sync(since=stale_day, until=date.today())
        assert stale_day not in client.get_daily_stats_calls

        svc.sync(since=stale_day, until=date.today(), revalidate_days=15)
        assert stale_day in client.get_daily_stats_calls
