import asyncio
from datetime import date, timedelta

import pytest

from tridata.mcp_server import (
    build_server, get_activity, get_daily_metrics, get_records, get_training_load,
    get_weekly_summary, list_activities,
)
from tridata.models import Activity, ActivityLap, DailyStats, HRVRecord, PersonalRecord
from tridata.storage import DataStore

TODAY = date.today()


@pytest.fixture
def store(tmp_path):
    store = DataStore(tmp_path / "test.sqlite3")
    store.save_activities([
        Activity(
            activity_id="run1", activity_date=TODAY - timedelta(days=1),
            activity_type="running", name="Easy run", duration_seconds=3000,
            distance_meters=10000, avg_hr=140, avg_pace_seconds_per_km=300,
        ),
        Activity(
            activity_id="ride1", activity_date=TODAY - timedelta(days=2),
            activity_type="cycling", name="Long ride", duration_seconds=7200,
            distance_meters=60000, avg_hr=130, avg_pace_seconds_per_km=120,
        ),
        Activity(
            activity_id="old1", activity_date=TODAY - timedelta(days=60),
            activity_type="running", name="Old run", duration_seconds=1800,
            distance_meters=5000, avg_hr=150,
        ),
    ])
    store.save_activity_laps([
        ActivityLap(activity_id="ride1", lap_index=1, duration_seconds=3600,
                    distance_meters=30000, avg_hr=128, avg_pace_seconds_per_km=120),
    ])
    store.save_daily_stats(DailyStats(stat_date=TODAY - timedelta(days=1), steps=12000, resting_hr=50))
    store.save_daily_stats(DailyStats(stat_date=TODAY - timedelta(days=30), steps=5000, resting_hr=55))
    store.save_hrv(HRVRecord(hrv_date=TODAY - timedelta(days=1), last_night_avg=60, status="BALANCED"))
    store.save_personal_records([
        PersonalRecord(record_id="pr1", activity_type="running", value=1234.0, pr_date=date(2026, 3, 1)),
    ])
    return store


def test_list_activities_defaults_to_recent_window(store):
    out = list_activities(store)
    assert "Easy run" in out and "Long ride" in out
    assert "Old run" not in out
    assert "id run1" in out


def test_list_activities_filters_by_sport(store):
    out = list_activities(store, sport="cycling")
    assert "Long ride" in out
    assert "Easy run" not in out
    assert "km/h" in out  # cycling formatter, not running pace


def test_list_activities_rejects_unknown_sport(store):
    with pytest.raises(ValueError):
        list_activities(store, sport="rowing")


def test_list_activities_rejects_inverted_range(store):
    with pytest.raises(ValueError):
        list_activities(store, start_date="2026-05-10", end_date="2026-05-01")


def test_get_activity_includes_laps_with_sport_formatter(store):
    out = get_activity(store, "ride1")
    assert "## Laps (1)" in out
    assert "30.0 km/h" in out


def test_get_activity_unknown_id(store):
    with pytest.raises(ValueError):
        get_activity(store, "nope")


def test_get_daily_metrics_slices_by_date(store):
    out = get_daily_metrics(store)
    assert "steps: 12000" in out
    assert "steps: 5000" not in out
    assert "BALANCED" in out
    assert "Personal records" not in out


def test_get_training_load_extends_to_today(store):
    out = get_training_load(store, days=3)
    assert f"Today {TODAY}" in out
    assert out.count("\n| 20") == 3  # three table rows


def test_get_weekly_summary_counts_recent_sessions(store):
    out = get_weekly_summary(store, weeks=2)
    assert "| cycling | 1 | 2.0 | 60.0 |" in out
    assert "Old run" not in out and out.count("| running |") == 1


def test_get_records(store):
    out = get_records(store)
    assert "Personal records (1)" in out


def test_server_registers_read_only_tools(store):
    server = build_server(store)
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "list_activities", "get_activity", "get_daily_metrics",
        "get_training_load", "get_weekly_summary", "get_records",
    }
    assert all(t.annotations.read_only_hint for t in tools)


def test_server_with_the_team_adds_its_tools(tmp_path, store):  # unit 065 R-186; unit 066 R-190, G-55 (R8)
    from tridata.conversaciones.desde_pc import montar_equipo
    from tridata.nutricion.asistente import texto_acciones

    equipo = montar_equipo(tmp_path / "test.sqlite3", store=store, token=tmp_path / "google-token.json")
    tools = {t.name: t for t in asyncio.run(build_server(store, equipo=equipo).list_tools())}
    assert set(tools) == {
        "list_activities", "get_activity", "get_daily_metrics", "get_training_load", "get_weekly_summary",
        "get_records", "get_contexto", "preparar_cambio", "hacer_cambio", "apuntar_comida", "apuntar_peso",
        # unit 081: the plan .md, the medical PDF, the recipes and the routes, by the coach
        "preparar_plan", "importar_plan", "get_analiticas_pendientes", "preparar_transcripcion",
        "guardar_transcripcion", "get_recetas", "get_rutas", "get_perfil_ruta",
        "apuntar_toma", "apuntar_leche_cafes",  # unit 082: coffees and B12 with their hour, and the milk of the coffees
        "preparar_macros", "guardar_macros",  # unit 098: the macros of what is saved, filled in once
        "get_comidas",  # unit 100: the foods of older days, by dates (R-281)
        "preparar_zapatilla", "apuntar_zapatilla",  # unit 102: the km of a shoe said by Andrés (R-291)
    }  # `proponer_cambio` (065) is gone: no card is left anywhere
    escriben = {"hacer_cambio", "apuntar_comida", "apuntar_peso", "importar_plan", "guardar_transcripcion",
                "apuntar_toma", "apuntar_leche_cafes", "guardar_macros", "apuntar_zapatilla"}
    assert {n for n, t in tools.items() if not t.annotations.read_only_hint} == escriben
    preparar = tools["preparar_cambio"]
    assert texto_acciones() in preparar.description  # the same catalogue the team of the app sees
    assert set(preparar.input_schema["required"]) == {"cambio"}
    assert set(tools["hacer_cambio"].input_schema["required"]) == {"papel", "cambio", "huella"}
    assert set(tools["apuntar_comida"].input_schema["required"]) == {"papel", "dia", "alimentos"}
    assert set(tools["apuntar_peso"].input_schema["required"]) == {"papel", "kg", "dia"}
    assert set(tools["preparar_zapatilla"].input_schema["required"]) == {"zapatilla", "km"}  # unit 102
    assert set(tools["apuntar_zapatilla"].input_schema["required"]) == {"papel", "zapatilla", "km", "huella"}
    assert "apuntar_zapatilla" in tools["preparar_zapatilla"].description
    for nombre in ("preparar_zapatilla", "apuntar_zapatilla"):  # unit 103: also the shoes given here and their runs
        assert {"accion", "tope", "uso", "carrera"} <= set(tools[nombre].input_schema["properties"])
        assert "alta" in tools[nombre].description and "carrera" in tools[nombre].description
    for nombre in escriben:  # each one says: show it to Andrés and wait for his «sí»
        descripcion = tools[nombre].description
        assert "«sí»" in descripcion and "Andrés" in descripcion, nombre
    assert "preparar_cambio" in tools["hacer_cambio"].description
    assert "«sí»" in preparar.description and "hacer_cambio" in preparar.description
    # bug 085: with two foods of the same name, it says how to choose one by its número
    assert "Hay más de uno" in preparar.description and "eleccion" in preparar.description


def test_get_weekly_summary_same_output_as_before(tmp_path, monkeypatch):
    """Unit 090: the grouping by week and sport now lives in `fitness.py` (shared with Fitness › weeks); the coach's
    text must not change by a character. Pinned with the output of the code before that unit (invented data)."""
    monkeypatch.setenv("TRIDATA_HR_MAX", "190")
    monkeypatch.setenv("TRIDATA_HR_REST", "60")
    store = DataStore(tmp_path / "semanas.sqlite3")
    lunes = TODAY - timedelta(days=TODAY.weekday())
    previa = lunes - timedelta(days=7)
    store.save_activities([
        Activity(activity_id="r1", activity_date=lunes, activity_type="running", name="r", duration_seconds=3600,
                 distance_meters=10000, avg_hr=140),
        Activity(activity_id="g1", activity_date=lunes, activity_type="strength_training", name="g",
                 duration_seconds=2700, distance_meters=300, avg_hr=110),
        Activity(activity_id="b1", activity_date=previa + timedelta(days=2), activity_type="cycling", name="b",
                 duration_seconds=7200, distance_meters=60000, avg_hr=130),
        Activity(activity_id="n1", activity_date=previa + timedelta(days=3), activity_type="lap_swimming", name="n",
                 duration_seconds=2400, distance_meters=2000),
        Activity(activity_id="r2", activity_date=previa + timedelta(days=4), activity_type="trail_running", name="r2",
                 duration_seconds=1800, distance_meters=5000, avg_hr=150),
        Activity(activity_id="r3", activity_date=previa + timedelta(days=6), activity_type="running", name="r3",
                 duration_seconds=1800, distance_meters=6000, avg_hr=155),
        Activity(activity_id="w1", activity_date=previa + timedelta(days=1), activity_type="walking", name="w",
                 duration_seconds=3600, distance_meters=5000, avg_hr=100),
    ])
    store.save_activity_laps([ActivityLap(activity_id="b1", lap_index=0, duration_seconds=3600, avg_hr=125),
                              ActivityLap(activity_id="b1", lap_index=1, duration_seconds=3600, avg_hr=135)])
    antes = lunes - timedelta(days=14)
    assert get_weekly_summary(store, 3) == "\n".join([
        "# Weekly summary, last 3 weeks (week starts Monday; current week is partial)",
        "",
        "| Week | Sport | Sessions | Hours | km | TRIMP |",
        "|---|---|---:|---:|---:|---:|",
        f"| {antes} | — | 0 | 0.0 | 0.0 | 0 |",
        f"| {previa} | cycling | 1 | 2.0 | 60.0 | 117 |",
        f"| {previa} | running | 2 | 1.0 | 11.0 | 107 |",
        f"| {previa} | swimming | 1 | 0.7 | 2.0 | 0 |",
        f"| {previa} | **total** | | **3.7** | | **225** |",
        f"| {lunes} | gym | 1 | 0.8 | — | 23 |",
        f"| {lunes} | running | 1 | 1.0 | 10.0 | 77 |",
        f"| {lunes} | **total** | | **1.8** | | **100** |",
    ])
