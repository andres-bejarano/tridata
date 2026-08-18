"""Suunto cloud client — NOT YET IMPLEMENTED.

Suunto's API (https://apizone.suunto.com/) is restricted to registered
partner companies and organisations; personal projects are not eligible.
Once a partner API key is obtained, implement this class against the
Suunto Application Interface (REST + OAuth 2.0).

Steps to unlock:
  1. Register at https://apizone.suunto.com/
  2. Receive client_id / client_secret from Suunto.
  3. Implement OAuth 2.0 PKCE flow to obtain user access tokens.
  4. Replace NotImplementedError calls below with real API requests.
"""
from __future__ import annotations

from datetime import date

from ..models import Activity, DailyStats, HRVRecord, SleepRecord


class SuuntoClient:
    """Skeleton — raises NotImplementedError until partner credentials are available."""

    def login(self) -> None:
        raise NotImplementedError(
            "SuuntoClient is not yet implemented. "
            "Suunto requires a partner API key from https://apizone.suunto.com/ — "
            "access is limited to companies/organisations."
        )

    def get_activities(self, start: date, end: date) -> list[Activity]:
        raise NotImplementedError("SuuntoClient.get_activities — see suunto.py for details.")

    def get_daily_stats(self, day: date) -> DailyStats | None:
        raise NotImplementedError("SuuntoClient.get_daily_stats — see suunto.py for details.")

    def get_sleep(self, day: date) -> SleepRecord | None:
        raise NotImplementedError("SuuntoClient.get_sleep — see suunto.py for details.")

    def get_hrv(self, day: date) -> HRVRecord | None:
        raise NotImplementedError("SuuntoClient.get_hrv — see suunto.py for details.")
