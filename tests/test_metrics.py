"""Tests for the PMC metrics module. All HR/TSS values are fictional."""
from __future__ import annotations

import math
from datetime import date

import pytest

from tridata.metrics import (
    HR_MAX, HR_REST, _HR_RESERVE, _trimp_segment,
    compute_pmc, daily_tss, trimp_for_activity,
)


# ---------------------------------------------------------------------------
# _trimp_segment
# ---------------------------------------------------------------------------

def test_trimp_segment_positive():
    # 60 min at HR=140: hr_ratio=(140-52)/126=0.6984
    # trimp = 60 * 0.6984 * 0.64 * exp(1.92*0.6984)
    hr_ratio = (140 - HR_REST) / _HR_RESERVE
    expected = 60 * hr_ratio * 0.64 * math.exp(1.92 * hr_ratio)
    assert _trimp_segment(3600, 140) == pytest.approx(expected, rel=1e-6)


def test_trimp_segment_zero_duration():
    assert _trimp_segment(0, 150) == pytest.approx(0.0)


def test_trimp_segment_hr_below_rest_clamped():
    # HR below rest → hr_ratio clamped to 0 → trimp = 0
    assert _trimp_segment(3600, 40) == pytest.approx(0.0)


def test_trimp_segment_hr_at_max_clamped():
    # HR above max → hr_ratio clamped to 1.0
    hr_ratio = 1.0
    expected = 60 * hr_ratio * 0.64 * math.exp(1.92 * hr_ratio)
    assert _trimp_segment(3600, 200) == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# trimp_for_activity — lap-level path
# ---------------------------------------------------------------------------

_ACTIVITY = {
    "activity_id": "act001",
    "activity_date": "2026-03-10",
    "avg_hr": 145.0,
    "duration_seconds": 3600.0,
}

_LAPS = [
    {"avg_hr": 138.0, "duration_seconds": 1800.0},
    {"avg_hr": 152.0, "duration_seconds": 1800.0},
]


def test_trimp_uses_laps_when_all_have_hr():
    result = trimp_for_activity(_ACTIVITY, _LAPS)
    expected = (
        _trimp_segment(1800, 138) +
        _trimp_segment(1800, 152)
    )
    assert result == pytest.approx(expected, rel=1e-6)


def test_trimp_lap_result_differs_from_aggregate():
    # Using lap HR should give a different (more accurate) result than
    # using the single activity avg_hr over the full duration.
    lap_result = trimp_for_activity(_ACTIVITY, _LAPS)
    fallback_result = trimp_for_activity(_ACTIVITY, [])
    assert lap_result != pytest.approx(fallback_result, rel=0.01)


# ---------------------------------------------------------------------------
# trimp_for_activity — fallback path (no laps / partial None HR)
# ---------------------------------------------------------------------------

def test_trimp_fallback_when_no_laps():
    result = trimp_for_activity(_ACTIVITY, [])
    expected = _trimp_segment(3600, 145)
    assert result == pytest.approx(expected, rel=1e-6)


def test_trimp_fallback_when_one_lap_hr_is_none():
    laps_partial = [
        {"avg_hr": 138.0, "duration_seconds": 1800.0},
        {"avg_hr": None,  "duration_seconds": 1800.0},
    ]
    # Any None in laps → fall back to activity avg_hr
    result = trimp_for_activity(_ACTIVITY, laps_partial)
    expected = _trimp_segment(3600, 145)
    assert result == pytest.approx(expected, rel=1e-6)


def test_trimp_returns_zero_when_no_hr_anywhere():
    activity_no_hr = {**_ACTIVITY, "avg_hr": None}
    result = trimp_for_activity(activity_no_hr, [])
    assert result == 0.0


def test_trimp_returns_zero_when_no_duration():
    activity_no_dur = {**_ACTIVITY, "duration_seconds": None}
    result = trimp_for_activity(activity_no_dur, [])
    assert result == 0.0


# ---------------------------------------------------------------------------
# daily_tss
# ---------------------------------------------------------------------------

def test_daily_tss_sums_multiple_activities_same_day():
    acts = [
        {**_ACTIVITY, "activity_id": "a1", "activity_date": "2026-03-10", "laps": []},
        {**_ACTIVITY, "activity_id": "a2", "activity_date": "2026-03-10", "laps": []},
    ]
    result = daily_tss(acts)
    single = trimp_for_activity(_ACTIVITY, [])
    assert result[date(2026, 3, 10)] == pytest.approx(2 * single, rel=1e-6)


def test_daily_tss_different_days():
    acts = [
        {**_ACTIVITY, "activity_id": "a1", "activity_date": "2026-03-10", "laps": []},
        {**_ACTIVITY, "activity_id": "a2", "activity_date": "2026-03-11", "laps": []},
    ]
    result = daily_tss(acts)
    assert len(result) == 2
    assert date(2026, 3, 10) in result
    assert date(2026, 3, 11) in result


def test_daily_tss_empty_input():
    assert daily_tss([]) == {}


# ---------------------------------------------------------------------------
# compute_pmc — manual verification
#
# Using fictional round TSS values for easy hand-calculation:
#   Day 1 TSS=100: CTL=100/42≈2.381, ATL=100/7≈14.286, TSB=0-0=0
#   Day 2 TSS=50:  CTL≈3.515,        ATL≈19.388,        TSB≈2.381-14.286=-11.905
#   Day 3 TSS=0:   CTL≈3.431,        ATL≈16.618,        TSB≈3.515-19.388=-15.873
# ---------------------------------------------------------------------------

@pytest.fixture()
def three_day_tss():
    return {
        date(2026, 1, 1): 100.0,
        date(2026, 1, 2): 50.0,
        date(2026, 1, 3): 0.0,
    }


def test_compute_pmc_length(three_day_tss):
    series = compute_pmc(three_day_tss)
    assert len(series) == 3


def test_compute_pmc_tsb_day1_is_zero(three_day_tss):
    # TSB on day 1 = CTL(0) - ATL(0) = 0
    series = compute_pmc(three_day_tss)
    assert series[0]["tsb"] == pytest.approx(0.0, abs=0.01)


def test_compute_pmc_ctl_day1(three_day_tss):
    series = compute_pmc(three_day_tss)
    assert series[0]["ctl"] == pytest.approx(100.0 / 42, rel=1e-3)


def test_compute_pmc_atl_day1(three_day_tss):
    series = compute_pmc(three_day_tss)
    assert series[0]["atl"] == pytest.approx(100.0 / 7, rel=1e-3)


def test_compute_pmc_tsb_day2(three_day_tss):
    series = compute_pmc(three_day_tss)
    ctl1 = 100.0 / 42
    atl1 = 100.0 / 7
    assert series[1]["tsb"] == pytest.approx(ctl1 - atl1, rel=1e-3)


def test_compute_pmc_ctl_atl_day3_decay(three_day_tss):
    series = compute_pmc(three_day_tss)
    ctl2 = series[1]["ctl"]
    atl2 = series[1]["atl"]
    # Day 3 TSS=0 → pure decay
    assert series[2]["ctl"] == pytest.approx(ctl2 + (0 - ctl2) / 42, rel=1e-3)
    assert series[2]["atl"] == pytest.approx(atl2 + (0 - atl2) / 7, rel=1e-3)


def test_compute_pmc_fills_gap_days():
    # Gap between two active days must be filled with TSS=0
    tss = {
        date(2026, 1, 1): 80.0,
        date(2026, 1, 4): 60.0,  # 2 gap days
    }
    series = compute_pmc(tss)
    assert len(series) == 4
    assert series[1]["tss"] == 0.0
    assert series[2]["tss"] == 0.0


def test_compute_pmc_empty_input():
    assert compute_pmc({}) == []
