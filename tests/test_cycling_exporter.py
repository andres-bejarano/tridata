"""Tests for CyclingMarkdownExporter.

All fixtures use invented data — no real health values.
"""
from __future__ import annotations

from tridata.exporters import CyclingMarkdownExporter


# ---------------------------------------------------------------------------
# Fixtures (invented data)
# ---------------------------------------------------------------------------

# averageSpeed = 7.0 m/s → pace = 1000/7.0 ≈ 142.86 s/km → 3600/142.86 = 25.2 km/h
_PACE_FAST = 1000.0 / 7.0

# averageSpeed = 5.0 m/s → pace = 200 s/km → 3600/200 = 18.0 km/h
_PACE_MEDIUM = 1000.0 / 5.0

_RIDE_NO_SENSORS = {
    "activity_id": "cyc001",
    "activity_date": "2026-03-15",
    "name": "Morning ride",
    "activity_type": "road_biking",
    "duration_seconds": 7200,
    "distance_meters": 50000.0,
    "avg_hr": 145.0,
    "avg_pace_seconds_per_km": _PACE_FAST,
    "avg_cadence": None,           # no pedalling sensor
    "avg_stride_length_cm": None,
    "avg_power": None,
    "elevation_gain_m": 800.0,
    "elevation_loss_m": 790.0,
    "laps": [
        {
            "activity_id": "cyc001", "lap_index": 1,
            "distance_meters": 10000.0, "duration_seconds": 1440.0,
            "avg_pace_seconds_per_km": _PACE_FAST,
            "avg_hr": 140.0, "max_hr": 160.0,
            "avg_cadence": None,
            "avg_stride_length_cm": None,
            "elevation_gain_m": 150.0, "elevation_loss_m": 140.0,
            "intensity_type": "ACTIVE",
        },
    ],
}

_RIDE_WITH_SENSORS = {
    "activity_id": "cyc002",
    "activity_date": "2026-03-12",
    "name": "Club ride",
    "activity_type": "cycling",
    "duration_seconds": 5400,
    "distance_meters": 40000.0,
    "avg_hr": 150.0,
    "avg_pace_seconds_per_km": _PACE_MEDIUM,
    "avg_cadence": 88.0,           # fictional cadence sensor
    "avg_stride_length_cm": None,
    "avg_power": 210.0,            # fictional power meter
    "elevation_gain_m": 400.0,
    "elevation_loss_m": 395.0,
    "laps": [
        {
            "activity_id": "cyc002", "lap_index": 1,
            "distance_meters": 10000.0, "duration_seconds": 1350.0,
            "avg_pace_seconds_per_km": _PACE_MEDIUM,
            "avg_hr": 148.0, "max_hr": 165.0,
            "avg_cadence": 90.0,
            "avg_stride_length_cm": None,
            "elevation_gain_m": 100.0, "elevation_loss_m": 98.0,
            "intensity_type": "ACTIVE",
            "avg_power": 215.0,
        },
    ],
}


# ---------------------------------------------------------------------------
# _fmt_speed_kmh
# ---------------------------------------------------------------------------

def test_fmt_speed_kmh_typical():
    # 200 s/km → 3600/200 = 18.0 km/h
    assert CyclingMarkdownExporter._fmt_speed_kmh(200.0) == "18.0 km/h"


def test_fmt_speed_kmh_one_decimal():
    # 142.857 s/km → 3600/142.857 ≈ 25.2 km/h
    result = CyclingMarkdownExporter._fmt_speed_kmh(1000.0 / 7.0)
    assert result == "25.2 km/h"


def test_fmt_speed_kmh_none_returns_dash():
    assert CyclingMarkdownExporter._fmt_speed_kmh(None) == "—"


def test_fmt_speed_kmh_zero_returns_dash():
    assert CyclingMarkdownExporter._fmt_speed_kmh(0.0) == "—"


# ---------------------------------------------------------------------------
# CyclingMarkdownExporter — activity without sensors
# ---------------------------------------------------------------------------

class TestCyclingNoSensors:
    def _render(self) -> str:
        return CyclingMarkdownExporter().render([_RIDE_NO_SENSORS])

    def test_title(self):
        assert "# Cycling activities export" in self._render()

    def test_speed_in_kmh_not_pace_in_activity_line(self):
        # The activity summary line must use km/h, not m:ss/km.
        # (/km CAN appear in lap lines via the shared _fmt_lap_line formatter.)
        lines = self._render().splitlines()
        activity_lines = [l for l in lines if l.startswith("- **")]
        assert any("km/h" in l for l in activity_lines)
        assert all("/km" not in l for l in activity_lines)

    def test_no_cadence_or_stride_in_activity_line(self):
        content = self._render()
        assert "spm" not in content
        assert "stride" not in content

    def test_distance_present(self):
        assert "50.00 km" in self._render()

    def test_avg_hr_present(self):
        assert "avg HR 145" in self._render()

    def test_elevation_gain_and_loss_present(self):
        content = self._render()
        assert "+800m" in content
        assert "-790m" in content

    def test_lap_block_rendered(self):
        content = self._render()
        assert "Lap  1" in content
        assert "  - Lap  1" in content

    def test_intensity_type_active_in_lap(self):
        assert "[ACTIVE]" in self._render()


# ---------------------------------------------------------------------------
# CyclingMarkdownExporter — activity WITH sensors (fictional)
# ---------------------------------------------------------------------------

class TestCyclingWithSensors:
    def _render(self) -> str:
        return CyclingMarkdownExporter().render([_RIDE_WITH_SENSORS])

    def test_speed_in_kmh(self):
        assert "18.0 km/h" in self._render()

    def test_cadence_appears_in_lap_when_present(self):
        # _fmt_lap_line shows cadence if avg_cadence is not None
        assert "90 spm" in self._render()

    def test_no_cadence_in_activity_summary_line(self):
        # The activity-level summary line should never show "spm"
        # (cadence is only in the lap breakdown)
        lines = self._render().splitlines()
        activity_lines = [l for l in lines if l.startswith("- **")]
        assert all("spm" not in l for l in activity_lines)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_no_laps_renders_cleanly():
    ride = {**_RIDE_NO_SENSORS, "laps": []}
    content = CyclingMarkdownExporter().render([ride])
    assert "Morning ride" in content
    assert "Lap" not in content


def test_empty_list_renders_without_error():
    content = CyclingMarkdownExporter().render([])
    assert "# Cycling activities export" in content


def test_export_writes_file(tmp_path):
    out = CyclingMarkdownExporter().export([_RIDE_NO_SENSORS], tmp_path / "cycling.md")
    assert out.exists()
    assert "Morning ride" in out.read_text()


def test_cli_default_out_cycling():
    from pathlib import Path
    from tridata.cli import _build_parser
    args = _build_parser().parse_args(["export-cycling"])
    assert args.out == Path("exports/cycling.md")
