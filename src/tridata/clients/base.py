"""FitnessClient — the contract every brand client must satisfy.

Using typing.Protocol (structural subtyping) so existing classes like
GarminClient don't need to inherit from anything; they just need to
expose these methods with the right signatures.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from ..models import Activity, DailyStats, HRVRecord, SleepRecord


@runtime_checkable
class FitnessClient(Protocol):
    def login(self) -> None: ...
    def get_activities(self, start: date, end: date) -> list[Activity]: ...
    def get_daily_stats(self, day: date) -> DailyStats | None: ...
    def get_sleep(self, day: date) -> SleepRecord | None: ...
    def get_hrv(self, day: date) -> HRVRecord | None: ...
