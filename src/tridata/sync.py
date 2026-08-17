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

        logger.info("Sync complete: %s to %s", since, until)
