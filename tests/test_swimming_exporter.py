"""Tests for SwimmingMarkdownExporter.

All fixtures use invented data — no real health values.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tridata.exporters import SwimmingMarkdownExporter


# ---------------------------------------------------------------------------
# Fixtures (invented data)
# ---------------------------------------------------------------------------

# averageSpeed = 1.0 m/s → pace = 1000/1.0 = 1000 s/km → /100m = 100 s → 1:40/100m
_PACE_1000 = 1000.0 / 1.0

# averageSpeed = 1.25 m/s → pace = 800 s/km → /100m = 80 s → 1:20/100m
_PACE_800 = 1000.0 / 1.25

_POOL_SWIM = {
    "activity_id": "sw001",
    "activity_date": "2026-03-20",
    "name": "Pool session",
    "activity_type": "lap_swimming",
    "duration_seconds": 2700,
    "distance_meters": 2000.0,
    "avg_hr": 138.0,
    "avg_pace_seconds_per_km": _PACE_800,
    "avg_swolf": 38.0,
    "avg_swim_cadence": 28.0,
    "avg_strokes": 12.0,
    "total_strokes": 960.0,
    "pool_length_m": 25.0,
    "avg_water_temp_c": None,   # no temp sensor in pool
    "elevation_gain_m": None,
    "elevation_loss_m": None,
    "laps": [
        {
            "activity_id": "sw001", "lap_index": 1,
            "distance_meters": 150.0, "duration_seconds": 130.0,
            "avg_pace_seconds_per_km": _PACE_800,
            "avg_hr": 135.0, "max_hr": 145.0,
            "avg_swolf": 36.0,
            "avg_swim_cadence": 27.0,
            "total_strokes": 56,
            "swim_stroke": "FREESTYLE",
            "avg_cadence": None, "avg_stride_length_cm": None,
            "elevation_gain_m": None, "elevation_loss_m": None,
            "intensity_type": None,
        },
    ],
}

_OW_SWIM = {
    "activity_id": "sw002",
    "activity_date": "2026-06-15",
    "name": "Open water race",
    "activity_type": "open_water_swimming",
    "duration_seconds": 1800,
    "distance_meters": 1000.0,
    "avg_hr": 145.0,
    "avg_pace_seconds_per_km": _PACE_1000,
    "avg_swolf": 62.0,
    "avg_swim_cadence": 25.0,
    "avg_strokes": None,      # no per-length data in OW
    "total_strokes": 450.0,
    "pool_length_m": None,    # no pool in open water
    "avg_water_temp_c": 22.0,
    "elevation_gain_m": None,
    "elevation_loss_m": None,
    "laps": [
        {
            "activity_id": "sw002", "lap_index": 1,
            "distance_meters": 500.0, "duration_seconds": 900.0,
            "avg_pace_seconds_per_km": _PACE_1000,
            "avg_hr": 143.0, "max_hr": 158.0,
            "avg_swolf": 61.0,
            "avg_swim_cadence": 24.0,
            "total_strokes": 220,
            "swim_stroke": None,   # OW typically no stroke info
            "avg_cadence": None, "avg_stride_length_cm": None,
            "elevation_gain_m": None, "elevation_loss_m": None,
            "intensity_type": "ACTIVE",
        },
    ],
}


# ---------------------------------------------------------------------------
# _fmt_pace_per_100m
# ---------------------------------------------------------------------------

def test_fmt_pace_per_100m_typical():
    # 800 s/km → 80 s/100m → 1:20/100m
    assert SwimmingMarkdownExporter._fmt_pace_per_100m(800.0) == "1:20/100m"


def test_fmt_pace_per_100m_round_seconds():
    # 1000 s/km → 100 s/100m → 1:40/100m
    assert SwimmingMarkdownExporter._fmt_pace_per_100m(1000.0) == "1:40/100m"


def test_fmt_pace_per_100m_none():
    assert SwimmingMarkdownExporter._fmt_pace_per_100m(None) == "—"


def test_fmt_pace_per_100m_zero():
    assert SwimmingMarkdownExporter._fmt_pace_per_100m(0.0) == "—"


# ---------------------------------------------------------------------------
# Pool swimming
# ---------------------------------------------------------------------------

class TestPoolSwimming:
    def _render(self) -> str:
        return SwimmingMarkdownExporter().render([_POOL_SWIM])

    def test_title(self):
        assert "# Swimming activities export" in self._render()

    def test_distance_in_metres_not_km(self):
        content = self._render()
        assert "2000 m" in content
        assert "2.00 km" not in content

    def test_pace_per_100m_in_activity_line(self):
        lines = [l for l in self._render().splitlines() if l.startswith("- **")]
        assert any("1:20/100m" in l for l in lines)

    def test_swolf_in_activity_line(self):
        assert "SWOLF 38" in self._render()

    def test_swim_cadence_in_activity_line(self):
        assert "28 spm" in self._render()

    def test_pool_length_shown(self):
        assert "25m pool" in self._render()

    def test_no_water_temp_when_none(self):
        assert "°C" not in self._render()

    def test_lap_block_rendered(self):
        content = self._render()
        assert "Lap  1" in content
        assert "  - Lap  1" in content

    def test_swim_stroke_in_lap(self):
        assert "[FREESTYLE]" in self._render()

    def test_strokes_in_lap(self):
        assert "56 strokes" in self._render()


# ---------------------------------------------------------------------------
# Open water swimming
# ---------------------------------------------------------------------------

class TestOpenWaterSwimming:
    def _render(self) -> str:
        return SwimmingMarkdownExporter().render([_OW_SWIM])

    def test_water_temp_shown(self):
        assert "22°C" in self._render()

    def test_no_pool_length(self):
        assert "pool" not in self._render()

    def test_swolf_present(self):
        assert "SWOLF 62" in self._render()

    def test_no_stroke_name_in_lap_when_none(self):
        # OW lap has swim_stroke=None → stroke names like FREESTYLE/BACKSTROKE absent.
        # intensity_type may appear as fallback bracket tag — that's fine.
        content = self._render()
        assert "FREESTYLE" not in content
        assert "BACKSTROKE" not in content

    def test_intensity_type_shown_in_lap(self):
        assert "[ACTIVE]" in self._render()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_laps_renders_cleanly():
    a = {**_POOL_SWIM, "laps": []}
    content = SwimmingMarkdownExporter().render([a])
    assert "Pool session" in content
    assert "Lap" not in content


def test_empty_list_renders_without_error():
    content = SwimmingMarkdownExporter().render([])
    assert "# Swimming activities export" in content


def test_export_writes_file(tmp_path):
    out = SwimmingMarkdownExporter().export([_POOL_SWIM], tmp_path / "swimming.md")
    assert out.exists()
    assert "Pool session" in out.read_text()


def test_cli_default_out_swimming():
    from tridata.cli import _build_parser
    args = _build_parser().parse_args(["export-swimming"])
    assert args.out == Path("exports/swimming.md")


def test_swimming_types_in_lap_types():
    from tridata.sync import SWIMMING_TYPES, SyncService
    assert all(t in SyncService._LAP_TYPES for t in SWIMMING_TYPES)
