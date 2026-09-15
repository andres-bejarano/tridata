"""Orchestrates GarminClient + DataStore for incremental syncing.

This is the piece a daily cron job / scheduled task calls: it figures
out what's missing since the last run and only fetches that.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .garmin_client import GarminClient
from .storage import DataStore

logger = logging.getLogger(__name__)

RUNNING_TYPES = (
    "running", "trail_running", "treadmill_running", "indoor_running",
)
CYCLING_TYPES = (
    "cycling", "road_biking", "mountain_biking", "indoor_cycling",
    "virtual_ride", "gravel_cycling",
)
SWIMMING_TYPES = (
    "lap_swimming",
    "open_water_swimming",
)
GYM_TYPES = ("strength_training",)
ALL_SPORT_TYPES = RUNNING_TYPES + CYCLING_TYPES + SWIMMING_TYPES + GYM_TYPES


class SyncService:
    #: Re-fetch this many most-recent days on every run even when they already have
    #: a row. Garmin sometimes finalizes a day's totals (steps, calories) *after* its
    #: own calendar date — e.g. an activity uploaded late from a separate device/bike
    #: computer — and `missing_dates()` only catches dates with NO row at all, so a
    #: value cached before that upload lands would otherwise stay wrong forever.
    #: (2026-08-18 through 2026-08-27 is a real example of exactly that happening.)
    #:
    #: This short window is cheap enough to run on every daily sync, but it only
    #: catches staleness discovered within RECENT_REVALIDATE_DAYS of it happening.
    #: A day that slips past this window unnoticed (e.g. 2026-08-20 / 2026-08-24,
    #: which stayed wrong for weeks) needs the separate, infrequent wide sweep —
    #: see `sync()`'s `revalidate_days` parameter — daily_update.sh triggers it
    #: automatically every ~7 days (see WIDE_INTERVAL_DAYS there), no separate
    #: script/timer needed.
    RECENT_REVALIDATE_DAYS = 3

    def __init__(self, client: GarminClient, store: DataStore) -> None:
        self._client = client
        self._store = store

    def _days_to_check(
        self, table: str, col: str, since: date, until: date, revalidate_days: int | None = None,
    ) -> list[date]:
        """Missing dates in [since, until], plus the most recent
        `revalidate_days` real-world days even if already present.

        `revalidate_days` defaults to `RECENT_REVALIDATE_DAYS` (the short window
        used by the normal daily run). Pass a larger value — e.g. from a weekly
        wide-sweep job — to also catch days that fell stale outside that short
        window before anything ever re-checked them (see `sync()`'s
        `revalidate_days` parameter). Pass 0 to disable revalidation entirely
        and only fetch genuinely missing days.

        Anchored to *today*, not to `until`: a backfill/resync of old history
        (e.g. `until` deep in the past) shouldn't force-refresh the tail end of
        that old range every time — only calendar days close to right now are
        at risk of having been cached before Garmin finished processing them.
        """
        if revalidate_days is None:
            revalidate_days = self.RECENT_REVALIDATE_DAYS

        missing = set(self._store.missing_dates(table, col, since, until))

        recent: set[date] = set()
        if revalidate_days > 0:
            revalidate_start = date.today() - timedelta(days=revalidate_days - 1)
            recent_start = max(since, revalidate_start)
            recent_end = min(until, date.today())
            day = recent_start
            while day <= recent_end:
                recent.add(day)
                day += timedelta(days=1)

        return sorted(missing | recent)

    def sync(self, since: date, until: date | None = None, revalidate_days: int | None = None) -> None:
        """Fetch and persist everything missing between `since` and `until`
        (plus a recent-day revalidation window — see `_days_to_check`).

        On a fully-synced store this is a no-op aside from that revalidation;
        on first run with `since` set to e.g. 2026-02-01, it backfills your
        whole history.

        `revalidate_days` overrides `RECENT_REVALIDATE_DAYS` for this call only —
        the normal daily cron run leaves it as `None` (short window, cheap), while
        an occasional wide sweep can pass e.g. 45 to revalidate a much larger
        window and catch days that went stale outside the short window. See
        daily_update.sh, which triggers this automatically every ~7 days.
        """
        until = until or date.today()

        self._client.login()

        activity_days = self._days_to_check("activities", "activity_date", since, until, revalidate_days)
        if activity_days:
            start, end = min(activity_days), max(activity_days)
            activities = self._client.get_activities(start, end)
            self._store.save_activities(activities)
            logger.info("Synced %d activities (%s to %s)", len(activities), start, end)

        for day in self._days_to_check("daily_stats", "stat_date", since, until, revalidate_days):
            stats = self._client.get_daily_stats(day)
            if stats:
                self._store.save_daily_stats(stats)

        for day in self._days_to_check("sleep", "sleep_date", since, until, revalidate_days):
            sleep = self._client.get_sleep(day)
            if sleep:
                self._store.save_sleep(sleep)

        for day in self._days_to_check("hrv", "hrv_date", since, until, revalidate_days):
            hrv = self._client.get_hrv(day)
            if hrv:
                self._store.save_hrv(hrv)

        # -- Extended metrics (skipped when client does not implement them) ----
        _daily_extended = [
            ("vo2max",             "vo2max_date",     "get_vo2max",             "save_vo2max"),
            ("training_readiness", "readiness_date",  "get_training_readiness", "save_training_readiness"),
            ("training_status",    "status_date",     "get_training_status",    "save_training_status"),
            ("body_battery",       "bb_date",         "get_body_battery",       "save_body_battery"),
            ("spo2",               "spo2_date",       "get_spo2",               "save_spo2"),
            ("respiration",        "respiration_date","get_respiration",         "save_respiration"),
            ("floors",             "floors_date",     "get_floors",             "save_floors"),
            ("intensity_minutes",  "intensity_date",  "get_intensity_minutes",  "save_intensity_minutes"),
            ("hydration",          "hydration_date",  "get_hydration",          "save_hydration"),
        ]
        for table, col, getter_name, saver_name in _daily_extended:
            getter = getattr(self._client, getter_name, None)
            if not callable(getter):
                continue
            saver = getattr(self._store, saver_name)
            for day in self._days_to_check(table, col, since, until, revalidate_days):
                record = getter(day)
                if record:
                    saver(record)

        if callable(getattr(self._client, "get_personal_records", None)):
            records = self._client.get_personal_records()
            if records:
                self._store.save_personal_records(records)

        if callable(getattr(self._client, "get_race_predictions", None)):
            if self._days_to_check("race_predictions", "prediction_date", since, until, revalidate_days):
                prediction = self._client.get_race_predictions()
                if prediction:
                    self._store.save_race_predictions(prediction)

        logger.info("Sync complete: %s to %s", since, until)

    _LAP_TYPES = RUNNING_TYPES + CYCLING_TYPES + SWIMMING_TYPES
    _WEATHER_TYPES = RUNNING_TYPES + CYCLING_TYPES

    def sync_laps(self, limit: int = 20) -> int:
        """Fetch per-lap splits for up to `limit` activities that have none yet.

        Processes the most recent eligible activities first. Returns the number
        of activities attempted.
        """
        self._client.login()

        candidate_ids = self._store.get_activity_ids_by_type(self._LAP_TYPES)
        already_done = self._store.activity_ids_lap_synced()
        pending = [aid for aid in candidate_ids if aid not in already_done][:limit]

        total = len(pending)
        for i, activity_id in enumerate(pending, start=1):
            print(f"parciales: {i}/{total} actividades procesadas", flush=True)
            laps = self._client.get_activity_laps(activity_id)
            if laps:
                self._store.save_activity_laps(laps)
            self._store.mark_laps_synced(activity_id, len(laps))
            logger.info("Laps synced for activity %s (%d laps)", activity_id, len(laps))

        logger.info("Lap sync complete: %d/%d activities processed", total, len(candidate_ids))
        return total

    def sync_weather(self, limit: int = 20) -> int:
        """Fetch weather snapshots for up to `limit` running/cycling activities that have none yet.

        Processes the most recent eligible activities first. Returns the number
        of activities attempted.
        """
        self._client.login()

        candidate_ids = self._store.get_activity_ids_by_type(self._WEATHER_TYPES)
        already_done = self._store.activity_ids_weather_synced()
        pending = [aid for aid in candidate_ids if aid not in already_done][:limit]

        total = len(pending)
        for i, activity_id in enumerate(pending, start=1):
            print(f"clima: {i}/{total} actividades procesadas", flush=True)
            weather = self._client.get_activity_weather(activity_id)
            self._store.save_activity_weather(activity_id, weather)
            logger.info("Weather synced for activity %s", activity_id)

        logger.info("Weather sync complete: %d/%d activities processed", total, len(candidate_ids))
        return total
