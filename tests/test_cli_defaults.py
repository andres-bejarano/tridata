"""Tests for CLI argument defaults — specifically that export commands write
to the exports/ subfolder by default."""
from pathlib import Path

from tridata.cli import _build_parser


def test_export_default_out_is_in_exports_folder():
    args = _build_parser().parse_args(["export"])
    assert args.out == Path("exports/garmin_export.md")


def test_export_json_default_out_is_in_exports_folder():
    args = _build_parser().parse_args(["export", "--format", "json"])
    assert args.out == Path("exports/garmin_export.md")


def test_export_metrics_default_out_is_in_exports_folder():
    args = _build_parser().parse_args(["export-metrics"])
    assert args.out == Path("exports/metrics.md")


def test_export_running_default_out_is_in_exports_folder():
    args = _build_parser().parse_args(["export-running"])
    assert args.out == Path("exports/running.md")


def test_exporter_creates_exports_subfolder(tmp_path):
    from tridata.exporters import MarkdownExporter
    out = tmp_path / "exports" / "garmin_export.md"
    # Parent does not exist yet — Exporter.export() must create it.
    assert not out.parent.exists()
    MarkdownExporter().export({"activities": [], "daily_stats": [], "sleep": [], "hrv": []}, out)
    assert out.exists()
    assert out.parent.name == "exports"
