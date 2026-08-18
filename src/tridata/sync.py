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
