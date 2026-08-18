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

from .models import (
    Activity, BodyBatteryDay, DailyStats, FloorsRecord, HRVRecord,
    HydrationRecord, IntensityMinutes, PersonalRecord, RacePrediction,
    RespirationRecord, SleepRecord, SpO2Record, TrainingReadiness,
    TrainingStatus, VO2MaxRecord,
)

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
            sleep_score=(dto.get("sleepScores") or {}).get("overall", {}).get("value"),
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

    def get_vo2max(self, day: date) -> VO2MaxRecord | None:
        raw_list = self.api.get_max_metrics(day.isoformat())
        if not raw_list:
            return None
        raw = raw_list[0]
        generic = raw.get("generic") or {}
        cycling = raw.get("cycling") or {}
        return VO2MaxRecord(
            vo2max_date=day,
            vo2max_running=generic.get("vo2MaxPreciseValue"),
            vo2max_cycling=cycling.get("vo2MaxPreciseValue") if cycling else None,
            raw=raw,
        )

    def get_training_readiness(self, day: date) -> TrainingReadiness | None:
        raw_list = self.api.get_training_readiness(day.isoformat())
        if not raw_list:
            return None
        # Pick the reading with the latest timestamp — the API may return them in any order.
        raw = max(
            raw_list,
            key=lambda r: r.get("timestampLocal") or r.get("timestamp") or "",
        )
        recovery_secs = raw.get("recoveryTime")
        return TrainingReadiness(
            readiness_date=day,
            score=raw.get("score"),
            level=raw.get("level"),
            feedback_short=raw.get("feedbackShort"),
            sleep_score=raw.get("sleepScore"),
            recovery_time_minutes=int(recovery_secs / 60) if recovery_secs else None,
            hrv_weekly_avg=raw.get("hrvWeeklyAverage"),
            raw=raw,
        )

    def get_training_status(self, day: date) -> TrainingStatus | None:
        raw = self.api.get_training_status(day.isoformat())
        if not raw:
            return None
        status_map = (raw.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}
        first_device = next(iter(status_map.values()), {}) if status_map else {}
        return TrainingStatus(
            status_date=day,
            training_status=first_device.get("trainingStatus"),
            raw=raw,
        )

    def get_body_battery(self, day: date) -> BodyBatteryDay | None:
        raw_list = self.api.get_body_battery(day.isoformat(), day.isoformat())
        if not raw_list:
            return None
        raw = raw_list[0]
        return BodyBatteryDay(
            bb_date=day,
            charged=raw.get("charged"),
            drained=raw.get("drained"),
            values=raw.get("bodyBatteryValuesArray"),
            raw=raw,
        )

    def get_spo2(self, day: date) -> SpO2Record | None:
        raw = self.api.get_spo2_data(day.isoformat())
        if not raw:
            return None
        return SpO2Record(
            spo2_date=day,
            avg_spo2=raw.get("averageSpO2"),
            lowest_spo2=raw.get("lowestSpO2"),
            avg_sleep_spo2=raw.get("avgSleepSpO2"),
            raw=raw,
        )

    def get_respiration(self, day: date) -> RespirationRecord | None:
        raw = self.api.get_respiration_data(day.isoformat())
        if not raw:
            return None
        return RespirationRecord(
            respiration_date=day,
            lowest_value=raw.get("lowestRespirationValue"),
            highest_value=raw.get("highestRespirationValue"),
            avg_waking=raw.get("avgWakingRespirationValue"),
            avg_sleep=raw.get("avgSleepRespirationValue"),
            raw=raw,
        )

    def get_floors(self, day: date) -> FloorsRecord | None:
        raw = self.api.get_floors(day.isoformat())
        if not raw or "floorValuesArray" not in raw:
            return None
        arr = raw.get("floorValuesArray") or []
        # each element: [startGMT, endGMT, floorsAscended, floorsDescended]
        ascended  = sum(row[2] for row in arr if len(row) > 2)
        descended = sum(row[3] for row in arr if len(row) > 3)
        return FloorsRecord(
            floors_date=day,
            floors_ascended=ascended,
            floors_descended=descended,
            raw=raw,
        )

    def get_intensity_minutes(self, day: date) -> IntensityMinutes | None:
        raw = self.api.get_intensity_minutes_data(day.isoformat())
        if not raw:
            return None
        return IntensityMinutes(
            intensity_date=day,
            weekly_moderate=raw.get("weeklyModerate"),
            weekly_vigorous=raw.get("weeklyVigorous"),
            weekly_total=raw.get("weeklyTotal"),
            week_goal=raw.get("weekGoal"),
            raw=raw,
        )

    def get_hydration(self, day: date) -> HydrationRecord | None:
        raw = self.api.get_hydration_data(day.isoformat())
        if not raw:
            return None
        return HydrationRecord(
            hydration_date=day,
            value_ml=raw.get("valueInML"),
            goal_ml=raw.get("goalInML"),
            sweat_loss_ml=raw.get("sweatLossInML"),
            raw=raw,
        )

    def get_personal_records(self) -> list[PersonalRecord]:
        raw_list = self.api.get_personal_record()
        if not raw_list:
            return []
        records = []
        for raw in raw_list:
            pr_date = None
            date_str = raw.get("actStartDateTimeInGMTFormatted")
            if date_str:
                pr_date = date.fromisoformat(date_str[:10])
            records.append(PersonalRecord(
                record_id=str(raw.get("id")),
                type_id=raw.get("typeId"),
                activity_type=raw.get("activityType"),
                value=raw.get("value"),
                pr_date=pr_date,
                raw=raw,
            ))
        return records

    def get_race_predictions(self) -> RacePrediction | None:
        raw = self.api.get_race_predictions()
        if not raw:
            return None
        date_str = raw.get("calendarDate")
        pred_date = date.fromisoformat(date_str) if date_str else date.today()
        return RacePrediction(
            prediction_date=pred_date,
            time_5k_seconds=raw.get("time5K"),
            time_10k_seconds=raw.get("time10K"),
            time_half_marathon_seconds=raw.get("timeHalfMarathon"),
            time_marathon_seconds=raw.get("timeMarathon"),
            raw=raw,
        )

    def iter_days(self, start: date, end: date):
        """Yield each date from start to end inclusive."""
        current = start
        while current <= end:
            yield current
            current += timedelta(days=1)
