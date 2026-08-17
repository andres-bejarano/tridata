"""Local persistence layer.

A single SQLite file holds everything we've ever pulled from Garmin.
This is what makes daily automated syncs cheap: each run only needs to
fetch the days that are missing (see `DataStore.missing_dates`) instead
of re-downloading the whole year every time, and it's also what lets you
ask Claude questions over your *entire* history instead of just today.

SQLite was chosen deliberately over a hosted DB: it's zero-config,
ships with Python, and is more than enough for a single person's daily
metrics. Swap it for Postgres later if this ever needs multi-user support.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

from .models import Activity, DailyStats, HRVRecord, SleepRecord

DEFAULT_DB_PATH = Path.home() / ".tridata" / "data.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    activity_id TEXT PRIMARY KEY,
    activity_date TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_stats (
    stat_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sleep (
    sleep_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hrv (
    hrv_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(activity_date);
"""


class DataStore:
    """Repository over a local SQLite file."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- Writes --------------------------------------------------------

    def save_activities(self, activities: list[Activity]) -> None:
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO activities (activity_id, activity_date, payload) "
                "VALUES (?, ?, ?)",
                [
                    (a.activity_id, a.activity_date.isoformat(), json.dumps(a.to_dict()))
                    for a in activities
                ],
            )

    def save_daily_stats(self, stats: DailyStats) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO daily_stats (stat_date, payload) VALUES (?, ?)",
                (stats.stat_date.isoformat(), json.dumps(stats.to_dict())),
            )

    def save_sleep(self, sleep: SleepRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sleep (sleep_date, payload) VALUES (?, ?)",
                (sleep.sleep_date.isoformat(), json.dumps(sleep.to_dict())),
            )

    def save_hrv(self, hrv: HRVRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO hrv (hrv_date, payload) VALUES (?, ?)",
                (hrv.hrv_date.isoformat(), json.dumps(hrv.to_dict())),
            )

    # -- Reads -----------------------------------------------------------

    def last_synced_date(self, table: str, date_column: str) -> date | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT MAX({date_column}) FROM {table}"  # noqa: S608 - fixed internal table names
            ).fetchone()
        if not row or not row[0]:
            return None
        return date.fromisoformat(row[0])

    def missing_dates(self, table: str, date_column: str, start: date, end: date) -> list[date]:
        """Dates in [start, end] not yet present in `table`."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {date_column} FROM {table} WHERE {date_column} BETWEEN ? AND ?",  # noqa: S608
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        existing = {date.fromisoformat(r[0]) for r in rows}
        missing = []
        current = start
        while current <= end:
            if current not in existing:
                missing.append(current)
            current += timedelta(days=1)
        return missing

    def export_all(self) -> dict:
        """Dump everything in the store, keyed by category, oldest-first."""
        with self._connect() as conn:
            activities = conn.execute(
                "SELECT payload FROM activities ORDER BY activity_date"
            ).fetchall()
            stats = conn.execute(
                "SELECT payload FROM daily_stats ORDER BY stat_date"
            ).fetchall()
            sleep = conn.execute("SELECT payload FROM sleep ORDER BY sleep_date").fetchall()
            hrv = conn.execute("SELECT payload FROM hrv ORDER BY hrv_date").fetchall()
        return {
            "activities": [json.loads(r[0]) for r in activities],
            "daily_stats": [json.loads(r[0]) for r in stats],
            "sleep": [json.loads(r[0]) for r in sleep],
            "hrv": [json.loads(r[0]) for r in hrv],
        }
