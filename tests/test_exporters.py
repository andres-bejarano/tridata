from tridata.exporters import JSONExporter, MarkdownExporter

SAMPLE_DATA = {
    "activities": [
        {
            "activity_date": "2026-02-05",
            "name": "Morning run",
            "activity_type": "running",
            "duration_seconds": 1800,
            "distance_meters": 5000,
            "avg_hr": 150,
        }
    ],
    "daily_stats": [],
    "sleep": [],
    "hrv": [],
}


def test_json_exporter_roundtrip(tmp_path):
    exporter = JSONExporter()
    out = exporter.export(SAMPLE_DATA, tmp_path / "out.json")
    assert out.exists()
    assert "Morning run" in out.read_text()


def test_markdown_exporter_includes_activity_summary(tmp_path):
    exporter = MarkdownExporter()
    out = exporter.export(SAMPLE_DATA, tmp_path / "out.md")
    content = out.read_text()
    assert "Morning run" in content
    assert "5.00 km" in content
    assert "avg HR 150" in content
