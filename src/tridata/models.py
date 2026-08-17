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
