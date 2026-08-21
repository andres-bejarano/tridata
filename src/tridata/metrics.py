# Performance Management Chart (PMC) metrics: CTL, ATL, TSB
# Based on Banister impulse-response model (Banister et al. 1975)
#
# "TSS" here is Banister TRIMP computed from heart rate — not the
# normalised power-based TSS used by TrainingPeaks. Both plug into the
# same EMA model and produce equivalent CTL/ATL/TSB dynamics; the
# difference is the unit (TRIMP-points vs TSS-points). Within a
# single-source system the numbers are internally consistent.
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Physiological constants — from sports-medicine lab results (2025-11-14,
# Bruce protocol treadmill test):
#   HR_MAX: peak HR reached during the test (do NOT substitute the
#           age-predicted 220-age formula — that one is 188 bpm and would
#           skew every zone calculation).
#   HR_REST: resting ECG value from the same appointment.
# The five training zones (121-127, 134-140, 146-153, 159-165, 171-178 bpm)
# derived from these two values via Karvonen match the zones recommended
# by the examining physician.
# ---------------------------------------------------------------------------
HR_MAX = 178
HR_REST = 52
_HR_RESERVE = HR_MAX - HR_REST  # 126 bpm

# Banister male coefficients
_TRIMP_K = 0.64
_TRIMP_Y = 1.92


def _trimp_segment(duration_seconds: float, avg_hr: float) -> float:
    """TRIMP for a single segment (lap or whole activity)."""
    duration_min = duration_seconds / 60.0
    hr_ratio = (avg_hr - HR_REST) / _HR_RESERVE
    hr_ratio = max(0.0, min(1.0, hr_ratio))  # clamp — avoid nonsense for HR < rest
    return duration_min * hr_ratio * _TRIMP_K * math.exp(_TRIMP_Y * hr_ratio)


def trimp_for_activity(activity: dict[str, Any], laps: list[dict[str, Any]]) -> float:
    """Return the Banister TRIMP for one activity.

    Strategy (in order):
    1. If the activity has laps and every lap has a non-None avg_hr, sum
       TRIMP over laps (finer resolution).
    2. Otherwise fall back to the activity-level avg_hr and total duration.
    3. If no avg_hr is available at either level, return 0.0 and log a warning.

    NOTE: The warm-up period of ~2-6 weeks at the start of the data history
    means CTL/ATL will underestimate true fitness until the EMAs converge.
    This is expected behaviour, not a bug.
    """
    # Attempt lap-level calculation
    if laps:
        lap_hrs = [lap.get("avg_hr") for lap in laps]
        if all(hr is not None for hr in lap_hrs):
            return sum(
                _trimp_segment(lap["duration_seconds"], lap["avg_hr"])
                for lap in laps
                if lap.get("duration_seconds")
            )

    # Fall back to activity-level
    avg_hr = activity.get("avg_hr")
    duration = activity.get("duration_seconds")
    if avg_hr is None or not duration:
        logger.warning(
            "Activity %s has no avg_hr — contributing 0 TRIMP",
            activity.get("activity_id", "?"),
        )
        return 0.0

    return _trimp_segment(duration, avg_hr)


def daily_tss(activities_with_laps: list[dict[str, Any]]) -> dict[date, float]:
    """Aggregate TRIMP per calendar date.

    Multiple activities on the same day are summed. Days with no activity
    are NOT included here — `compute_pmc` fills the gaps with 0.
    """
    tss: dict[date, float] = {}
    for a in activities_with_laps:
        try:
            d = date.fromisoformat(a["activity_date"])
        except (KeyError, ValueError):
            logger.warning("Skipping activity with invalid date: %s", a.get("activity_id"))
            continue
        laps = a.get("laps", [])
        tss[d] = tss.get(d, 0.0) + trimp_for_activity(a, laps)
    return tss


def compute_pmc(
    tss_by_date: dict[date, float],
) -> list[dict[str, Any]]:
    """Compute the full CTL / ATL / TSB series.

    Returns one entry per calendar day from the first to the last date in
    `tss_by_date`, with 0 TSS on days without recorded activity.

    CTL (chronic training load, ~fitness):
        CTL[d] = CTL[d-1] + (TSS[d] - CTL[d-1]) / 42

    ATL (acute training load, ~fatigue):
        ATL[d] = ATL[d-1] + (TSS[d] - ATL[d-1]) / 7

    TSB (training stress balance, ~form):
        TSB[d] = CTL[d-1] - ATL[d-1]    (yesterday's fitness minus yesterday's
                                          fatigue — standard convention: today's
                                          form reflects what you built before
                                          today's session)

    Both CTL and ATL start at 0.  The first ~2-6 weeks of data will
    underestimate true fitness/fatigue while the EMAs warm up.
    """
    if not tss_by_date:
        return []

    start = min(tss_by_date)
    end = max(tss_by_date)

    ctl = atl = 0.0
    series: list[dict[str, Any]] = []

    current = start
    while current <= end:
        tss = tss_by_date.get(current, 0.0)
        tsb = ctl - atl                          # form before today's load
        ctl = ctl + (tss - ctl) / 42.0
        atl = atl + (tss - atl) / 7.0
        series.append({
            "date": current,
            "tss": tss,
            "ctl": ctl,
            "atl": atl,
            "tsb": tsb,
        })
        current += timedelta(days=1)

    return series
