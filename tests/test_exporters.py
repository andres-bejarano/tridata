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


def test_markdown_exporter_hydration_partial_none_does_not_raise(tmp_path):
    # Regression: value_ml present but goal_ml/sweat_loss_ml None must not TypeError.
    data = {**SAMPLE_DATA, "hydration": [
        {"hydration_date": "2026-08-04", "value_ml": 500.0, "goal_ml": None, "sweat_loss_ml": None},
        {"hydration_date": "2026-08-05", "value_ml": None,  "goal_ml": None, "sweat_loss_ml": None},
    ]}
    exporter = MarkdownExporter()
    out = exporter.export(data, tmp_path / "out.md")
    content = out.read_text()
    assert content.count("no data") == 2
