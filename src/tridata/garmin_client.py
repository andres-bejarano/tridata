"""Thin wrapper around the `garminconnect` package.

Encapsulates authentication (including garth's token caching, so you
don't re-login on every run) and exposes a small, typed surface area
that the rest of the app depends on instead of talking to
`garminconnect` directly everywhere.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from garminconnect import Garmin

from .models import Activity, DailyStats, HRVRecord, SleepRecord

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_DIR = Path.home() / ".tridata" / "tokens"


class GarminAuthError(RuntimeError):
    """Raised when login to Garmin Connect fails."""


class GarminClient:
    """Authenticated session against Garmin Connect.

    Usage:
        client = GarminClient(email, password)
        client.login()
        activities = client.get_activities(start=date(2026, 2, 1), end=date.today())
    """

    def __init__(
        self,
        email: str,
        password: str,
        token_dir: Path | str = DEFAULT_TOKEN_DIR,
    ) -> None:
        self._email = email
        self._password = password
        self._token_dir = Path(token_dir)
        self._api: Garmin | None = None

    def login(self) -> None:
        """Authenticate, reusing a cached token if we have one.

        garth (the auth layer garminconnect uses) persists OAuth tokens to
        disk, so after the first successful login you generally won't be
        prompted again until the token expires.
        """
        self._token_dir.mkdir(parents=True, exist_ok=True)
        api = Garmin(email=self._email, password=self._password)
        try:
            api.login(str(self._token_dir))
        except Exception as exc:  # garminconnect raises several exception types
            raise GarminAuthError(f"Could not authenticate with Garmin Connect: {exc}") from exc
        self._api = api
        logger.info("Logged in to Garmin Connect as %s", self._email)

    @property
    def api(self) -> Garmin:
        if self._api is None:
            raise GarminAuthError("Call login() before using the client.")
        return self._api

    # -- Fetchers ----------------------------------------------------------

    def get_activities(self, start: date, end: date) -> list[Activity]:
        """Return activities between start and end (inclusive)."""
        raw_activities = self.api.get_activities_by_date(
            start.isoformat(), end.isoformat()
        )
        activities: list[Activity] = []
        for raw in raw_activities:
            activities.append(
                Activity(
                    activity_id=str(raw.get("activityId")),
                    activity_date=date.fromisoformat(raw["startTimeLocal"][:10]),
                    activity_type=raw.get("activityType", {}).get("typeKey", "unknown"),
                    name=raw.get("activityName", ""),
                    duration_seconds=raw.get("duration", 0.0),
                    distance_meters=raw.get("distance"),
                    avg_hr=raw.get("averageHR"),
                    max_hr=raw.get("maxHR"),
                    calories=raw.get("calories"),
                    training_effect_aerobic=raw.get("aerobicTrainingEffect"),
                    training_effect_anaerobic=raw.get("anaerobicTrainingEffect"),
                    raw=raw,
                )
            )
        return activities

    def get_daily_stats(self, day: date) -> DailyStats | None:
        raw = self.api.get_stats(day.isoformat())
        if not raw:
            return None
        return DailyStats(
            stat_date=day,
            steps=raw.get("totalSteps"),
            resting_hr=raw.get("restingHeartRate"),
            stress_avg=raw.get("averageStressLevel"),
            body_battery_max=raw.get("bodyBatteryHighestValue"),
            body_battery_min=raw.get("bodyBatteryLowestValue"),
            total_calories=raw.get("totalKilocalories"),
            raw=raw,
        )

    def get_sleep(self, day: date) -> SleepRecord | None:
        raw = self.api.get_sleep_data(day.isoformat())
        if not raw or "dailySleepDTO" not in raw:
            return None
        dto = raw["dailySleepDTO"] or {}
        return SleepRecord(
            sleep_date=day,
            total_sleep_seconds=dto.get("sleepTimeSeconds"),
            deep_sleep_seconds=dto.get("deepSleepSeconds"),
            light_sleep_seconds=dto.get("lightSleepSeconds"),
            rem_sleep_seconds=dto.get("remSleepSeconds"),
            awake_seconds=dto.get("awakeSleepSeconds"),
            sleep_score=(raw.get("sleepScores") or {}).get("overall", {}).get("value"),
            raw=raw,
        )

    def get_hrv(self, day: date) -> HRVRecord | None:
        raw = self.api.get_hrv_data(day.isoformat())
        if not raw or "hrvSummary" not in raw:
            return None
        summary = raw["hrvSummary"] or {}
        return HRVRecord(
            hrv_date=day,
            last_night_avg=summary.get("lastNightAvg"),
            last_night_high=summary.get("lastNight5MinHigh"),
            last_night_low=summary.get("lastNightLow"),
            status=summary.get("status"),
            raw=raw,
        )

    def iter_days(self, start: date, end: date):
        """Yield each date from start to end inclusive."""
        current = start
        while current <= end:
            yield current
            current += timedelta(days=1)
