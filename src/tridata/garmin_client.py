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
from typing import Any

from garminconnect import Garmin, GarminConnectTooManyRequestsError

from .models import (
    Activity, ActivityLap, BodyBatteryDay, DailyStats, FloorsRecord, HRVRecord,
    HydrationRecord, IntensityMinutes, PersonalRecord, RacePrediction,
    RespirationRecord, SleepRecord, SpO2Record, TrainingReadiness,
    TrainingStatus, VO2MaxRecord, pace_seconds_per_km,
)

logger = logging.getLogger(__name__)


def _pool_length_m(raw: dict) -> float | None:
    """Convert Garmin's poolLength + unitOfPoolLength['factor'] to metres."""
    length = raw.get("poolLength")
    unit = raw.get("unitOfPoolLength") or {}
    factor = unit.get("factor")
    if length is None or not factor:
        return None
    return length / factor


DEFAULT_TOKEN_DIR = Path.home() / ".tridata" / "tokens"


class GarminAuthError(RuntimeError):
    """Raised when login to Garmin Connect fails."""


class GarminRateLimitError(RuntimeError):
    """Raised when Garmin Connect answers 429 (too many requests) to the login (unit 018, G-45)."""


class GarminWorkoutError(RuntimeError):
    """Garmin Connect refused a workout, or did not answer (unit 042, R-159): the card offers the file instead."""


class GarminRutaError(RuntimeError):
    """Garmin Connect did not give a route or the GPX of an activity (unit 045, R-153): the app offers the GPX."""


def _es_429(exc: BaseException | None) -> bool:
    """Whether `exc`, or what caused it, is Garmin limiting the requests (a 429)."""
    vistos = 0
    while exc is not None and vistos < 10:
        if isinstance(exc, GarminConnectTooManyRequestsError) or "429" in str(exc):
            return True
        exc = exc.__cause__ or exc.__context__
        vistos += 1
    return False


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
            if _es_429(exc):  # unit 018: said apart, the routine waits longer (G-45)
                raise GarminRateLimitError("Garmin Connect is limiting the requests (429)") from exc
            raise GarminAuthError(f"Could not authenticate with Garmin Connect: {exc}") from exc
        self._restrict_token_store()
        self._api = api
        logger.info("Logged in to Garmin Connect")  # no email: sync.log is a plain file

    def _restrict_token_store(self) -> None:
        """Make the cached tokens readable by this user only.

        garminconnect <= 0.3.4 writes them world-readable (CVE-2026-54447),
        and they include a refresh token: anyone else on the machine could
        use it to access the Garmin account.
        """
        self._token_dir.chmod(0o700)
        for path in self._token_dir.iterdir():
            if path.is_file():
                path.chmod(0o600)

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
                    avg_pace_seconds_per_km=pace_seconds_per_km(raw.get("averageSpeed")),
                    avg_cadence=raw.get("averageRunningCadenceInStepsPerMinute"),
                    avg_stride_length_cm=raw.get("avgStrideLength"),
                    avg_vertical_oscillation_cm=raw.get("avgVerticalOscillation"),
                    avg_ground_contact_time_ms=raw.get("avgGroundContactTime"),
                    avg_power=raw.get("avgPower"),
                    elevation_gain_m=raw.get("elevationGain"),
                    elevation_loss_m=raw.get("elevationLoss"),
                    avg_swim_cadence=raw.get("averageSwimCadenceInStrokesPerMinute"),
                    avg_swolf=raw.get("averageSwolf"),
                    avg_strokes=raw.get("avgStrokes"),
                    total_strokes=raw.get("strokes"),
                    pool_length_m=_pool_length_m(raw),
                    avg_water_temp_c=raw.get("averageTemperature"),
                    moving_duration_seconds=raw.get("movingDuration"),
                    raw=raw,
                )
            )
        return activities

    def get_activity_laps(self, activity_id: str) -> list[ActivityLap]:
        """Return per-lap splits for one activity. Empty list if none available."""
        raw = self.api.get_activity_splits(activity_id)
        lap_dtos = raw.get("lapDTOs") or []
        return [
            ActivityLap(
                activity_id=activity_id,
                lap_index=dto.get("lapIndex", idx),
                distance_meters=dto.get("distance"),
                duration_seconds=dto.get("duration"),
                avg_pace_seconds_per_km=pace_seconds_per_km(dto.get("averageSpeed")),
                avg_hr=dto.get("averageHR"),
                max_hr=dto.get("maxHR"),
                avg_cadence=dto.get("averageRunCadence"),
                avg_stride_length_cm=dto.get("strideLength"),
                elevation_gain_m=dto.get("elevationGain"),
                elevation_loss_m=dto.get("elevationLoss"),
                intensity_type=dto.get("intensityType"),
                avg_swolf=dto.get("averageSWOLF"),
                avg_swim_cadence=dto.get("averageSwimCadence"),
                total_strokes=dto.get("totalNumberOfStrokes"),
                swim_stroke=dto.get("swimStroke"),
            )
            for idx, dto in enumerate(lap_dtos, start=1)
        ]

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
        first_device: dict[str, Any] = next(iter(status_map.values()), {}) if status_map else {}
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

    def get_activity_weather(self, activity_id: str) -> dict | None:
        """Return a cleaned weather snapshot for one activity, or None if unavailable.

        Temperature is converted from Fahrenheit (Garmin's API default) to Celsius.
        Wind speed is converted from mph (US customary, consistent with the Fahrenheit unit
        system) to km/h.
        """
        try:
            raw = self.api.get_activity_weather(activity_id)
        except Exception:
            return None
        if not raw:
            return None

        def _f_to_c(f: float | None) -> float | None:
            if f is None:
                return None
            return round((f - 32) * 5 / 9, 1)

        def _mph_to_kmh(mph: float | None) -> float | None:
            if mph is None:
                return None
            return round(mph * 1.60934, 1)

        return {
            "temp_c": _f_to_c(raw.get("temp")),
            "apparent_temp_c": _f_to_c(raw.get("apparentTemp")),
            "humidity_pct": raw.get("relativeHumidity"),
            "wind_speed_kmh": _mph_to_kmh(raw.get("windSpeed")),
            "wind_direction_deg": raw.get("windDirection"),
            "condition": (raw.get("weatherTypeDTO") or {}).get("desc"),
            "station_name": (raw.get("weatherStationDTO") or {}).get("name"),
        }

    # -- Workouts to the watch (unit 042, REC-26) ----------------------------

    def _workout_call(self, what: str, call: Any, *args: Any, missing_ok: bool = False,
                      error: type[RuntimeError] = GarminWorkoutError) -> Any:
        """One call of the workout service: a 429 is `GarminRateLimitError`, anything else `error`;
        with `missing_ok`, a 404 (removed by hand in Garmin Connect) is nothing."""
        api = self.api  # GarminAuthError without login
        try:
            return call(api, *args)
        except Exception as exc:  # garminconnect raises several exception types
            if _es_429(exc):
                raise GarminRateLimitError("Garmin Connect is limiting the requests (429)") from exc
            if missing_ok and "404" in str(exc):
                logger.info("Garmin Connect: %s, it was not there any more", what)
                return None
            raise error(f"Garmin Connect could not {what} ({type(exc).__name__})") from exc

    def subir_entreno(self, entreno: dict[str, Any]) -> int:
        """Upload a workout (the JSON of `plan.reloj.a_garmin`) to the library; its `workoutId`."""
        raw = self._workout_call("upload the workout", lambda api, e: api.upload_workout(e), entreno)
        ident = raw.get("workoutId") if isinstance(raw, dict) else None
        if not ident:
            raise GarminWorkoutError("Garmin Connect did not give the workout an id")
        return int(ident)

    def programar(self, workout_id: int, dia: date) -> int:
        """Schedule that workout on `dia` in the calendar of Garmin Connect; its `workoutScheduleId`."""
        raw = self._workout_call("schedule the workout", lambda api, w, d: api.schedule_workout(w, d),
                                 workout_id, dia.isoformat())
        ident = raw.get("workoutScheduleId") if isinstance(raw, dict) else None
        if not ident:
            raise GarminWorkoutError("Garmin Connect did not schedule the workout")
        return int(ident)

    def quitar_programado(self, schedule_id: int) -> None:
        """Remove a scheduled workout from the calendar (the template stays in the library)."""
        self._workout_call("unschedule the workout", lambda api, s: api.unschedule_workout(s), schedule_id,
                           missing_ok=True)

    def borrar_entreno(self, workout_id: int) -> None:
        """Delete a workout template from the library."""
        self._workout_call("delete the workout", lambda api, w: api.delete_workout(w), workout_id, missing_ok=True)

    def bajar_fit(self, workout_id: int) -> bytes:
        """The FIT file of a workout of the library, as Garmin Connect gives it."""
        return bytes(self._workout_call("download the workout", lambda api, w: api.download_workout(w), workout_id))

    # -- Routes (unit 045, REC-25) ----------------------------------------------------------------------------------
    # The library has no call for the saved routes (courses): the endpoints of the web, through `connectapi`
    # (tried on 2026-10-25). The unofficial way: a 429 is `GarminRateLimitError`, anything else `GarminRutaError`.

    def rutas_guardadas(self) -> list[dict[str, Any]]:
        """The routes saved in Garmin Connect: `[{id, nombre, distancia_m, sube_m, baja_m}]`."""
        raw = self._workout_call("list the routes", lambda api: api.connectapi("/web-gateway/course/owner/"),
                                 error=GarminRutaError)
        if isinstance(raw, dict):
            raw = raw.get("coursesForUser", next((v for v in raw.values() if isinstance(v, list)), []))
        if not isinstance(raw, list):
            raise GarminRutaError("Garmin Connect did not give the list of routes")
        return [{"id": int(c["courseId"]), "nombre": str(c.get("courseName") or "Ruta"),
                 "distancia_m": c.get("distanceInMeters"), "sube_m": c.get("elevationGainInMeters"),
                 "baja_m": c.get("elevationLossInMeters")}
                for c in raw if isinstance(c, dict) and c.get("courseId") is not None]

    def ruta(self, course_id: int) -> list[dict[str, Any]]:
        """The points of a saved route: `[{distancia_m, altitud_m, lat, lon}]` (the altitude None where it has none).
        Latitude and longitude stay on this PC (G-57): `plan.rutas` keeps only the profile."""
        raw = self._workout_call("get the route", lambda api, i: api.connectapi(f"/course-service/course/{i}"),
                                 int(course_id), error=GarminRutaError)
        puntos = raw.get("geoPoints") if isinstance(raw, dict) else None
        if not puntos:
            raise GarminRutaError("Garmin Connect gave the route without its points")
        return [{"distancia_m": p.get("distance"), "altitud_m": p.get("elevation"), "lat": p.get("latitude"),
                 "lon": p.get("longitude")} for p in puntos if isinstance(p, dict)]

    def gpx_actividad(self, activity_id: str) -> bytes:
        """The GPX of an activity already done, as Garmin Connect gives it."""
        return bytes(self._workout_call(
            "download the GPX", lambda api, i: api.download_activity(i, dl_fmt=Garmin.ActivityDownloadFormat.GPX),
            activity_id, error=GarminRutaError))

    def iter_days(self, start: date, end: date):
        """Yield each date from start to end inclusive."""
        current = start
        while current <= end:
            yield current
            current += timedelta(days=1)
