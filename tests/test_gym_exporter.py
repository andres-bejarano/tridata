"""Tests for GymMarkdownExporter. All fixtures use invented data."""
from __future__ import annotations

from pathlib import Path

from tridata.exporters import GymMarkdownExporter


_GYM_FULL = {
    "activity_date": "2026-04-10",
    "name": "Morning strength",
    "activity_type": "strength_training",
    "duration_seconds": 3600,
    "avg_hr": 118.0,
    "max_hr": 152.0,
    "calories": 320.0,
    "laps": [],
}

_GYM_SPARSE = {
    "activity_date": "2026-04-08",
    "name": "Quick session",
    "activity_type": "strength_training",
    "duration_seconds": 1200,
    "avg_hr": None,
    "max_hr": None,
    "calories": None,
    "laps": [],
}


class TestGymMarkdownExporter:
    def test_title(self):
        content = GymMarkdownExporter().render([])
        assert content.startswith("# Gym activities export")

    def test_full_activity_line(self):
        content = GymMarkdownExporter().render([_GYM_FULL])
        assert "Morning strength" in content
        assert "1h00m" in content
        assert "avg HR 118" in content
        assert "max 152" in content
        assert "320 kcal" in content

    def test_sparse_activity_no_error(self):
        content = GymMarkdownExporter().render([_GYM_SPARSE])
        assert "Quick session" in content
        assert "20m" in content
        assert "HR" not in content
        assert "kcal" not in content

    def test_no_lap_lines_rendered(self):
        gym_with_laps = {**_GYM_FULL, "laps": [
            {"lap_index": 1, "distance_meters": 0.0, "duration_seconds": 300.0},
        ]}
        content = GymMarkdownExporter().render([gym_with_laps])
        assert "Lap" not in content

    def test_both_activities_present(self):
        content = GymMarkdownExporter().render([_GYM_FULL, _GYM_SPARSE])
        assert "Morning strength" in content
        assert "Quick session" in content

    def test_export_writes_file(self, tmp_path):
        out = GymMarkdownExporter().export([_GYM_FULL], tmp_path / "gym.md")
        assert out.exists()
        assert "Morning strength" in out.read_text()

    def test_empty_list_renders_without_error(self):
        content = GymMarkdownExporter().render([])
        assert "# Gym activities export" in content

    def test_cli_default_out(self):
        from tridata.cli import _build_parser
        args = _build_parser().parse_args(["export-gym"])
        assert args.out == Path("exports/gym.md")

    def test_gym_types_exported_from_sync(self):
        from tridata.sync import GYM_TYPES
        assert "strength_training" in GYM_TYPES
