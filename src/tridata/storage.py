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

from .models import (
    Activity, BodyBatteryDay, DailyStats, FloorsRecord, HRVRecord,
    HydrationRecord, IntensityMinutes, PersonalRecord, RacePrediction,
    RespirationRecord, SleepRecord, SpO2Record, TrainingReadiness,
    TrainingStatus, VO2MaxRecord,
)

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
CREATE TABLE IF NOT EXISTS vo2max (
    vo2max_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS training_readiness (
    readiness_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS training_status (
    status_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS body_battery (
    bb_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spo2 (
    spo2_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS respiration (
    respiration_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS floors (
    floors_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intensity_minutes (
    intensity_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hydration (
    hydration_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS personal_records (
    record_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS race_predictions (
    prediction_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
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

    def save_vo2max(self, record: VO2MaxRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO vo2max (vo2max_date, payload) VALUES (?, ?)",
                (record.vo2max_date.isoformat(), json.dumps(record.to_dict())),
            )

    def save_training_readiness(self, record: TrainingReadiness) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO training_readiness (readiness_date, payload) VALUES (?, ?)",
                (record.readiness_date.isoformat(), json.dumps(record.to_dict())),
            )

    def save_training_status(self, record: TrainingStatus) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO training_status (status_date, payload) VALUES (?, ?)",
                (record.status_date.isoformat(), json.dumps(record.to_dict())),
            )

    def save_body_battery(self, record: BodyBatteryDay) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO body_battery (bb_date, payload) VALUES (?, ?)",
                (record.bb_date.isoformat(), json.dumps(record.to_dict())),
            )

    def save_spo2(self, record: SpO2Record) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO spo2 (spo2_date, payload) VALUES (?, ?)",
                (record.spo2_date.isoformat(), json.dumps(record.to_dict())),
            )

    def save_respiration(self, record: RespirationRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO respiration (respiration_date, payload) VALUES (?, ?)",
                (record.respiration_date.isoformat(), json.dumps(record.to_dict())),
            )

    def save_floors(self, record: FloorsRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO floors (floors_date, payload) VALUES (?, ?)",
                (record.floors_date.isoformat(), json.dumps(record.to_dict())),
            )

    def save_intensity_minutes(self, record: IntensityMinutes) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO intensity_minutes (intensity_date, payload) VALUES (?, ?)",
                (record.intensity_date.isoformat(), json.dumps(record.to_dict())),
            )

    def save_hydration(self, record: HydrationRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO hydration (hydration_date, payload) VALUES (?, ?)",
                (record.hydration_date.isoformat(), json.dumps(record.to_dict())),
            )

    def save_personal_records(self, records: list[PersonalRecord]) -> None:
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO personal_records (record_id, payload) VALUES (?, ?)",
                [(r.record_id, json.dumps(r.to_dict())) for r in records],
            )

    def save_race_predictions(self, record: RacePrediction) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO race_predictions (prediction_date, payload) VALUES (?, ?)",
                (record.prediction_date.isoformat(), json.dumps(record.to_dict())),
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
        def _rows(conn, sql):
            return [json.loads(r[0]) for r in conn.execute(sql).fetchall()]

        with self._connect() as conn:
            return {
                "activities":          _rows(conn, "SELECT payload FROM activities ORDER BY activity_date"),
                "daily_stats":         _rows(conn, "SELECT payload FROM daily_stats ORDER BY stat_date"),
                "sleep":               _rows(conn, "SELECT payload FROM sleep ORDER BY sleep_date"),
                "hrv":                 _rows(conn, "SELECT payload FROM hrv ORDER BY hrv_date"),
                "vo2max":              _rows(conn, "SELECT payload FROM vo2max ORDER BY vo2max_date"),
                "training_readiness":  _rows(conn, "SELECT payload FROM training_readiness ORDER BY readiness_date"),
                "training_status":     _rows(conn, "SELECT payload FROM training_status ORDER BY status_date"),
                "body_battery":        _rows(conn, "SELECT payload FROM body_battery ORDER BY bb_date"),
                "spo2":                _rows(conn, "SELECT payload FROM spo2 ORDER BY spo2_date"),
                "respiration":         _rows(conn, "SELECT payload FROM respiration ORDER BY respiration_date"),
                "floors":              _rows(conn, "SELECT payload FROM floors ORDER BY floors_date"),
                "intensity_minutes":   _rows(conn, "SELECT payload FROM intensity_minutes ORDER BY intensity_date"),
                "hydration":           _rows(conn, "SELECT payload FROM hydration ORDER BY hydration_date"),
                "personal_records":    _rows(conn, "SELECT payload FROM personal_records ORDER BY record_id"),
                "race_predictions":    _rows(conn, "SELECT payload FROM race_predictions ORDER BY prediction_date"),
            }
