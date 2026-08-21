from tridata.exporters import JSONExporter, MarkdownExporter, MetricsMarkdownExporter

# averageSpeed = 3.0 m/s → pace = 1000/3.0 = 333.33 s/km → 5:33/km
_RUN_WITH_DYNAMICS = {
    "activity_date": "2026-02-05",
    "name": "Morning run",
    "activity_type": "running",
    "duration_seconds": 1800,
    "distance_meters": 5000,
    "avg_hr": 150,
    "avg_pace_seconds_per_km": 1000.0 / 3.0,  # 333.33 s/km → 5:33
    "avg_cadence": 168.0,
    "avg_stride_length_cm": 95.0,
    "avg_vertical_oscillation_cm": 8.5,
    "avg_ground_contact_time_ms": 265.0,
    "avg_power": 280.0,
    "elevation_gain_m": 42.0,
    "elevation_loss_m": 40.0,
}

SAMPLE_DATA = {
    "activities": [_RUN_WITH_DYNAMICS],
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


def test_markdown_activity_includes_pace_cadence_stride_elevation(tmp_path):
    exporter = MarkdownExporter()
    out = exporter.export(SAMPLE_DATA, tmp_path / "out.md")
    content = out.read_text()
    assert "5:33/km" in content
    assert "168 spm" in content
    assert "stride 95 cm" in content
    assert "+42m" in content


def test_markdown_activity_no_speed_does_not_raise(tmp_path):
    # averageSpeed=None (e.g. a treadmill without GPS) must not raise and must
    # produce no pace token in output.
    activity_no_speed = {
        "activity_date": "2026-03-10",
        "name": "Treadmill run",
        "activity_type": "treadmill_running",
        "duration_seconds": 1200,
        "distance_meters": None,
        "avg_hr": None,
        "avg_pace_seconds_per_km": None,
        "avg_cadence": None,
        "avg_stride_length_cm": None,
        "avg_vertical_oscillation_cm": None,
        "avg_ground_contact_time_ms": None,
        "avg_power": None,
        "elevation_gain_m": None,
        "elevation_loss_m": None,
    }
    data = {**SAMPLE_DATA, "activities": [activity_no_speed]}
    exporter = MarkdownExporter()
    out = exporter.export(data, tmp_path / "out.md")
    content = out.read_text()
    assert "Treadmill run" in content
    assert "/km" not in content
    assert "spm" not in content


def test_markdown_activity_swim_no_running_fields(tmp_path):
    # Lap swimming has no cadence/pace/elevation — must render cleanly.
    swim = {
        "activity_date": "2026-04-01",
        "name": "Pool swim",
        "activity_type": "lap_swimming",
        "duration_seconds": 2700,
        "distance_meters": 2000,
        "avg_hr": 130,
    }
    data = {**SAMPLE_DATA, "activities": [swim]}
    exporter = MarkdownExporter()
    out = exporter.export(data, tmp_path / "out.md")
    content = out.read_text()
    assert "Pool swim" in content
    assert "2.00 km" in content
    assert "/km" not in content
    assert "spm" not in content
    assert "+0m" not in content


def test_fmt_pace_rounds_correctly():
    from tridata.exporters import MarkdownExporter
    assert MarkdownExporter._fmt_pace(333.333) == "5:33"
    assert MarkdownExporter._fmt_pace(360.0) == "6:00"
    assert MarkdownExporter._fmt_pace(301.0) == "5:01"
    assert MarkdownExporter._fmt_pace(None) == "—"


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


# ---------------------------------------------------------------------------
# Regression: MarkdownExporter.render() after refactor must include all sections
# ---------------------------------------------------------------------------

_RICH_DATA = {
    "activities": [_RUN_WITH_DYNAMICS],
    "daily_stats": [
        {"stat_date": "2026-02-05", "steps": 8000, "resting_hr": 52, "stress_avg": 30},
    ],
    "sleep": [
        {"sleep_date": "2026-02-05", "total_sleep_seconds": 25200, "sleep_score": 78},
    ],
    "hrv": [
        {"hrv_date": "2026-02-05", "last_night_avg": 55.0, "status": "BALANCED"},
    ],
    "vo2max": [
        {"vo2max_date": "2026-02-05", "vo2max_running": 52.0, "vo2max_cycling": None},
    ],
    "training_readiness": [
        {
            "readiness_date": "2026-02-05", "score": 72, "level": "GOOD",
            "sleep_score": 80, "recovery_time_minutes": 12, "hrv_weekly_avg": 53.0,
        },
    ],
    "training_status": [
        {"status_date": "2026-02-05", "training_status": 5},
    ],
    "body_battery": [
        {"bb_date": "2026-02-05", "charged": 35, "drained": 28},
    ],
    "spo2": [
        {"spo2_date": "2026-02-05", "avg_spo2": 96.0, "avg_sleep_spo2": 95.0, "lowest_spo2": 93.0},
    ],
    "respiration": [
        {
            "respiration_date": "2026-02-05", "avg_sleep": 14.0,
            "avg_waking": 16.0, "lowest_value": 12.0, "highest_value": 20.0,
        },
    ],
    "floors": [
        {"floors_date": "2026-02-05", "floors_ascended": 8, "floors_descended": 6},
    ],
    "intensity_minutes": [
        {
            "intensity_date": "2026-02-05", "weekly_moderate": 45,
            "weekly_vigorous": 30, "weekly_total": 105, "week_goal": 150,
        },
    ],
    "hydration": [
        {"hydration_date": "2026-02-05", "value_ml": 1800.0, "goal_ml": 2000.0, "sweat_loss_ml": 600.0},
    ],
    "personal_records": [
        {"activity_type": "running", "value": 1234.5, "pr_date": "2026-02-01"},
    ],
    "race_predictions": [
        {
            "prediction_date": "2026-02-05",
            "time_5k_seconds": 1320, "time_10k_seconds": 2700,
            "time_half_marathon_seconds": 5760, "time_marathon_seconds": 12000,
        },
    ],
}


def test_markdown_exporter_regression_all_sections_present(tmp_path):
    content = MarkdownExporter().export(_RICH_DATA, tmp_path / "out.md").read_text()
    assert "## Activities" in content
    assert "Morning run" in content
    assert "## Daily stats" in content
    assert "## Sleep" in content
    assert "## HRV" in content
    assert "## VO2max" in content
    assert "## Training readiness" in content
    assert "## Training status" in content
    assert "Improving" in content       # status code 5
    assert "## Body battery" in content
    assert "## SpO2" in content
    assert "## Respiration" in content
    assert "## Floors" in content
    assert "## Intensity minutes" in content
    assert "## Hydration" in content
    assert "## Personal records" in content
    assert "## Race predictions" in content


# ---------------------------------------------------------------------------
# MetricsMarkdownExporter
# ---------------------------------------------------------------------------

def test_metrics_exporter_excludes_activities(tmp_path):
    content = MetricsMarkdownExporter().export(_RICH_DATA, tmp_path / "out.md").read_text()
    assert "## Activities" not in content
    assert "Morning run" not in content


def test_metrics_exporter_includes_all_non_activity_sections(tmp_path):
    content = MetricsMarkdownExporter().export(_RICH_DATA, tmp_path / "out.md").read_text()
    assert "## Daily stats" in content
    assert "## Sleep" in content
    assert "## HRV" in content
    assert "## VO2max" in content
    assert "## Training readiness" in content
    assert "## Training status" in content
    assert "## Body battery" in content
    assert "## SpO2" in content
    assert "## Respiration" in content
    assert "## Floors" in content
    assert "## Intensity minutes" in content
    assert "## Hydration" in content
    assert "## Personal records" in content
    assert "## Race predictions" in content


def test_metrics_exporter_title(tmp_path):
    content = MetricsMarkdownExporter().export(_RICH_DATA, tmp_path / "out.md").read_text()
    assert content.startswith("# Garmin Connect metrics export")


def test_metrics_exporter_empty_optional_sections_omitted(tmp_path):
    # Optional sections (vo2max, body_battery, etc.) are absent when data is empty.
    sparse = {"activities": [], "daily_stats": [], "sleep": [], "hrv": []}
    content = MetricsMarkdownExporter().export(sparse, tmp_path / "out.md").read_text()
    assert "## VO2max" not in content
    assert "## Body battery" not in content
    assert "## Activities" not in content
    # Always-present sections are still there even when empty
    assert "## Daily stats" in content
    assert "## Sleep" in content
    assert "## HRV" in content
