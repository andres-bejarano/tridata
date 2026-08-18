"""COROS cloud client — NOT YET IMPLEMENTED.

COROS provides a developer API but requires submitting an application and
receiving partner approval before any API key is issued.

Steps to unlock:
  1. Submit the API application at:
     https://support.coros.com/hc/en-us/articles/17085887816340
  2. Receive client_id / client_secret from COROS after approval.
  3. Implement OAuth 2.0 flow to obtain user access tokens.
  4. Replace NotImplementedError calls below with real API requests.
"""
from __future__ import annotations

from datetime import date

from ..models import Activity, DailyStats, HRVRecord, SleepRecord


class CorosClient:
    """Skeleton — raises NotImplementedError until partner credentials are available."""

    def login(self) -> None:
        raise NotImplementedError(
            "CorosClient is not yet implemented. "
            "COROS requires a developer API key obtained through their partner programme: "
            "https://support.coros.com/hc/en-us/articles/17085887816340"
        )

    def get_activities(self, start: date, end: date) -> list[Activity]:
        raise NotImplementedError("CorosClient.get_activities — see coros.py for details.")

    def get_daily_stats(self, day: date) -> DailyStats | None:
        raise NotImplementedError("CorosClient.get_daily_stats — see coros.py for details.")

    def get_sleep(self, day: date) -> SleepRecord | None:
        raise NotImplementedError("CorosClient.get_sleep — see coros.py for details.")

    def get_hrv(self, day: date) -> HRVRecord | None:
        raise NotImplementedError("CorosClient.get_hrv — see coros.py for details.")
