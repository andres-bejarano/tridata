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


class SyncService:
    def __init__(self, client: GarminClient, store: DataStore) -> None:
        self._client = client
        self._store = store

    def sync(self, since: date, until: date | None = None) -> None:
        """Fetch and persist everything missing between `since` and `until`.

        On a fully-synced store this is a no-op; on first run with
        `since` set to e.g. 2026-02-01, it backfills your whole history.
        """
        until = until or date.today()

        self._client.login()

        missing_activity_days = self._store.missing_dates("activities", "activity_date", since, until)
        if missing_activity_days:
            start, end = min(missing_activity_days), max(missing_activity_days)
            activities = self._client.get_activities(start, end)
            self._store.save_activities(activities)
            logger.info("Synced %d activities (%s to %s)", len(activities), start, end)

        for day in self._store.missing_dates("daily_stats", "stat_date", since, until):
            stats = self._client.get_daily_stats(day)
            if stats:
                self._store.save_daily_stats(stats)

        for day in self._store.missing_dates("sleep", "sleep_date", since, until):
            sleep = self._client.get_sleep(day)
            if sleep:
                self._store.save_sleep(sleep)

        for day in self._store.missing_dates("hrv", "hrv_date", since, until):
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
            for day in self._store.missing_dates(table, col, since, until):
                record = getter(day)
                if record:
                    saver(record)

        if callable(getattr(self._client, "get_personal_records", None)):
            records = self._client.get_personal_records()
            if records:
                self._store.save_personal_records(records)

        if callable(getattr(self._client, "get_race_predictions", None)):
            if self._store.missing_dates("race_predictions", "prediction_date", since, until):
                prediction = self._client.get_race_predictions()
                if prediction:
                    self._store.save_race_predictions(prediction)

        logger.info("Sync complete: %s to %s", since, until)

    _LAP_TYPES = RUNNING_TYPES + CYCLING_TYPES + SWIMMING_TYPES

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
