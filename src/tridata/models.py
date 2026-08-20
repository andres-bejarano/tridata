"""Domain models for the data we pull out of Garmin Connect.

Keeping these as plain dataclasses (rather than passing raw Garmin JSON
around) gives the rest of the codebase a stable, typed contract to work
against, independent of whatever shape garminconnect/garth happen to
return this week.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any


@dataclass
class Activity:
    """A single logged activity (run, ride, swim, strength session...)."""

    activity_id: str
    activity_date: date
    activity_type: str
    name: str
    duration_seconds: float
    distance_meters: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    calories: float | None = None
    training_effect_aerobic: float | None = None
    training_effect_anaerobic: float | None = None
    avg_pace_seconds_per_km: float | None = None
    avg_cadence: float | None = None
    avg_stride_length_cm: float | None = None
    avg_vertical_oscillation_cm: float | None = None
    avg_ground_contact_time_ms: float | None = None
    avg_power: float | None = None
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["activity_date"] = self.activity_date.isoformat()
        data.pop("raw", None)
        return data


@dataclass
class DailyStats:
    """Daily summary: steps, resting HR, stress, body battery, etc."""

    stat_date: date
    steps: int | None = None
    resting_hr: int | None = None
    stress_avg: int | None = None
    body_battery_max: int | None = None
    body_battery_min: int | None = None
    total_calories: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stat_date"] = self.stat_date.isoformat()
        data.pop("raw", None)
        return data


@dataclass
class SleepRecord:
    """One night's sleep summary."""

    sleep_date: date
    total_sleep_seconds: float | None = None
    deep_sleep_seconds: float | None = None
    light_sleep_seconds: float | None = None
    rem_sleep_seconds: float | None = None
    awake_seconds: float | None = None
    sleep_score: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sleep_date"] = self.sleep_date.isoformat()
        data.pop("raw", None)
        return data


@dataclass
class HRVRecord:
    """Overnight heart-rate-variability summary."""

    hrv_date: date
    last_night_avg: float | None = None
    last_night_high: float | None = None
    last_night_low: float | None = None
    status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["hrv_date"] = self.hrv_date.isoformat()
        data.pop("raw", None)
        return data


@dataclass
class VO2MaxRecord:
    """Daily VO2max estimate (running and, if available, cycling)."""

    vo2max_date: date
    vo2max_running: float | None = None
    vo2max_cycling: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["vo2max_date"] = self.vo2max_date.isoformat()
        data.pop("raw", None)
        return data


@dataclass
class TrainingReadiness:
    """Daily training-readiness assessment."""

    readiness_date: date
    score: int | None = None
    level: str | None = None
    feedback_short: str | None = None
    sleep_score: int | None = None
    recovery_time_minutes: int | None = None
    hrv_weekly_avg: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["readiness_date"] = self.readiness_date.isoformat()
        data.pop("raw", None)
        return data


@dataclass
class TrainingStatus:
    """Daily training-status code from Garmin's load model."""

    status_date: date
    training_status: int | None = None  # numeric code: 4=Maintaining, 5=Improving, etc.
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status_date"] = self.status_date.isoformat()
        data.pop("raw", None)
        return data


@dataclass
class BodyBatteryDay:
    """Daily body-battery summary with intraday curve."""

    bb_date: date
    charged: int | None = None
    drained: int | None = None
    values: list | None = None  # [[timestamp_ms, level], ...]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bb_date"] = self.bb_date.isoformat()
        data.pop("raw", None)
        return data


@dataclass
class SpO2Record:
    """Daily SpO2 (blood-oxygen) summary. Fields are None when hardware is absent."""

    spo2_date: date
    avg_spo2: float | None = None
    lowest_spo2: float | None = None
    avg_sleep_spo2: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["spo2_date"] = self.spo2_date.isoformat()
        data.pop("raw", None)
        return data


@dataclass
class RespirationRecord:
    """Daily respiration-rate summary (breaths per minute)."""

    respiration_date: date
    lowest_value: float | None = None
    highest_value: float | None = None
    avg_waking: float | None = None
    avg_sleep: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["respiration_date"] = self.respiration_date.isoformat()
        data.pop("raw", None)
        return data


@dataclass
class FloorsRecord:
    """Daily floors ascended and descended."""

    floors_date: date
    floors_ascended: int | None = None
    floors_descended: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["floors_date"] = self.floors_date.isoformat()
        data.pop("raw", None)
        return data


@dataclass
class IntensityMinutes:
    """Weekly intensity-minutes snapshot stored per day."""

    intensity_date: date
    weekly_moderate: int | None = None
    weekly_vigorous: int | None = None
    weekly_total: int | None = None
    week_goal: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["intensity_date"] = self.intensity_date.isoformat()
        data.pop("raw", None)
        return data


@dataclass
class HydrationRecord:
    """Daily hydration log (ml)."""

    hydration_date: date
    value_ml: float | None = None
    goal_ml: float | None = None
    sweat_loss_ml: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["hydration_date"] = self.hydration_date.isoformat()
        data.pop("raw", None)
        return data


@dataclass
class PersonalRecord:
    """A single personal-best record."""

    record_id: str
    type_id: int | None = None
    activity_type: str | None = None
    value: float | None = None
    pr_date: date | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pr_date"] = self.pr_date.isoformat() if self.pr_date else None
        data.pop("raw", None)
        return data


@dataclass
class RacePrediction:
    """Garmin's predicted race times (seconds) as of a given date."""

    prediction_date: date
    time_5k_seconds: int | None = None
    time_10k_seconds: int | None = None
    time_half_marathon_seconds: int | None = None
    time_marathon_seconds: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["prediction_date"] = self.prediction_date.isoformat()
        data.pop("raw", None)
        return data
