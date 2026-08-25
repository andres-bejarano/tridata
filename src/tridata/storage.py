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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .models import (
    Activity, ActivityLap, BodyBatteryDay, DailyStats, FloorsRecord, HRVRecord,
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
CREATE TABLE IF NOT EXISTS activity_laps (
    activity_id TEXT NOT NULL,
    lap_index INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (activity_id, lap_index)
);
CREATE TABLE IF NOT EXISTS activity_lap_sync_state (
    activity_id TEXT PRIMARY KEY,
    lap_count INTEGER NOT NULL,
    synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity_weather (
    activity_id TEXT PRIMARY KEY,
    temp_c REAL,
    apparent_temp_c REAL,
    humidity_pct REAL,
    wind_speed_kmh REAL,
    wind_direction_deg REAL,
    condition TEXT,
    station_name TEXT,
    synced_at TEXT NOT NULL
);
"""

# Populate sync-state from any laps that pre-date this table (one-time migration).
_MIGRATE_LAP_SYNC_STATE = """
INSERT OR IGNORE INTO activity_lap_sync_state (activity_id, lap_count, synced_at)
SELECT activity_id, COUNT(*), '2000-01-01T00:00:00'
FROM activity_laps
GROUP BY activity_id;
"""


class DataStore:
    """Repository over a local SQLite file."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.executescript(_MIGRATE_LAP_SYNC_STATE)

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

    def save_activity_weather(self, activity_id: str, weather: dict | None) -> None:
        """Persist weather snapshot for one activity (even if weather is None/unavailable)."""
        synced_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO activity_weather "
                "(activity_id, temp_c, apparent_temp_c, humidity_pct, wind_speed_kmh, "
                "wind_direction_deg, condition, station_name, synced_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    activity_id,
                    weather.get("temp_c") if weather else None,
                    weather.get("apparent_temp_c") if weather else None,
                    weather.get("humidity_pct") if weather else None,
                    weather.get("wind_speed_kmh") if weather else None,
                    weather.get("wind_direction_deg") if weather else None,
                    weather.get("condition") if weather else None,
                    weather.get("station_name") if weather else None,
                    synced_at,
                ),
            )

    def save_activity_laps(self, laps: list[ActivityLap]) -> None:
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO activity_laps (activity_id, lap_index, payload) "
                "VALUES (?, ?, ?)",
                [(lap.activity_id, lap.lap_index, json.dumps(lap.to_dict())) for lap in laps],
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

    def mark_laps_synced(self, activity_id: str, lap_count: int) -> None:
        """Record that lap-sync was attempted for this activity (even if 0 laps)."""
        synced_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO activity_lap_sync_state "
                "(activity_id, lap_count, synced_at) VALUES (?, ?, ?)",
                (activity_id, lap_count, synced_at),
            )

    def activity_ids_lap_synced(self) -> set[str]:
        """Return IDs of activities whose lap-sync has already been attempted."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT activity_id FROM activity_lap_sync_state"
            ).fetchall()
        return {r[0] for r in rows}

    def activity_ids_with_laps(self) -> set[str]:
        """Return the set of activity_ids that already have laps stored."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT activity_id FROM activity_laps"
            ).fetchall()
        return {r[0] for r in rows}

    def activity_ids_weather_synced(self) -> set[str]:
        """Return IDs of activities whose weather-sync has already been attempted."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT activity_id FROM activity_weather"
            ).fetchall()
        return {r[0] for r in rows}

    def get_weather(self, activity_id: str) -> dict | None:
        """Return stored weather for one activity, or None if unavailable or not synced."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT temp_c, apparent_temp_c, humidity_pct, wind_speed_kmh, "
                "wind_direction_deg, condition, station_name "
                "FROM activity_weather WHERE activity_id = ?",
                (activity_id,),
            ).fetchone()
        if row is None:
            return None
        temp_c, apparent_temp_c, humidity_pct, wind_speed_kmh, wind_direction_deg, condition, station_name = row
        # All-null means the API had no weather for this activity.
        if all(v is None for v in (temp_c, humidity_pct, condition)):
            return None
        return {
            "temp_c": temp_c,
            "apparent_temp_c": apparent_temp_c,
            "humidity_pct": humidity_pct,
            "wind_speed_kmh": wind_speed_kmh,
            "wind_direction_deg": wind_direction_deg,
            "condition": condition,
            "station_name": station_name,
        }

    def get_activity_ids_by_type(self, types: tuple[str, ...]) -> list[str]:
        """Return activity IDs (newest first) whose type is one of `types`."""
        placeholders = ",".join("?" * len(types))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT activity_id FROM activities "  # noqa: S608
                f"WHERE json_extract(payload,'$.activity_type') IN ({placeholders}) "
                f"ORDER BY activity_date DESC",
                types,
            ).fetchall()
        return [r[0] for r in rows]

    def get_laps(self, activity_id: str) -> list[dict]:
        """Return stored laps for an activity, ordered by lap_index."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM activity_laps "
                "WHERE activity_id = ? ORDER BY lap_index",
                (activity_id,),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get_activity(self, activity_id: str) -> dict | None:
        """Return the stored payload for one activity, or None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM activities WHERE activity_id = ?",
                (activity_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def get_activities_with_laps(self, types: tuple[str, ...]) -> list[dict]:
        """Return activities of the given types (newest first), each enriched
        with a 'laps' key (empty list if none) and a 'weather' key (None if not synced)."""
        result = []
        for activity_id in self.get_activity_ids_by_type(types):
            activity = self.get_activity(activity_id)
            if activity is None:
                continue
            activity["laps"] = self.get_laps(activity_id)
            activity["weather"] = self.get_weather(activity_id)
            result.append(activity)
        return result

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
