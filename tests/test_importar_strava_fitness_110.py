"""Unit 110 (REC-53 of `recuperar-historico`): the imported activities in the base, the shoes and Fitness.

R5 (R-327, C-417) importing twice, or one already brought by Garmin in the same minute and sport, duplicates and
changes nothing; R6 (R-328, G-72, C-418) the imported never count for a shoe nor ask for one; R7 (R-330, C-420, C-421)
Fitness (km by week, by zones, the session) and the load count them as Garmin's, without HR their load is 0, and the
session says «de Strava». Every zip, track and value is invented (see `test_importar_strava_110.py`).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

import pytest
from test_importar_strava_110 import UTC, fila, gpx, hacer_zip

from tridata import fitness
from tridata.importar_strava import importar
from tridata.metrics import trimp_for_activity
from tridata.models import Activity, ActivityLap
from tridata.nutricion import DESDE_SIEMPRE, Ajustes
from tridata.storage import DataStore
from tridata.zapatillas import Zapatillas, pendientes_sin_crear

ZONAS = [[55, 120], [121, 140], [141, 155], [156, 168], [169, 180]]
PULSO = {"fc_max": 180, "fc_reposo": 55, "zonas_fc": ZONAS, "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}
LUNES = date(2025, 5, 5)  # a week of 2025, before the Garmin watch


@pytest.fixture(autouse=True)
def _entorno(monkeypatch):
    monkeypatch.setenv("TRIDATA_HR_MAX", "190")
    monkeypatch.setenv("TRIDATA_HR_REST", "60")


def _volcado(db) -> list[str]:
    with sqlite3.connect(db) as conn:
        return list(conn.iterdump())


def _zip_semana(tmp_path):
    """In the week of 5/5/2025: a run of 10 km with HR (Tuesday), one of 5 km without HR (Thursday) and, on Saturday
    at 06:00:30 UTC (08:00:30 in Madrid), a run that Garmin also has, started 08:00:00."""
    return hacer_zip(tmp_path / "e.zip", [
        fila(21, "6 may 2025, 17:00:00", "Rodaje con pulso", "Carrera", segundos=3000, metros=10000,
             archivo="activities/21.gpx", fc_media="150", fc_max="165"),
        fila(22, "8 may 2025, 17:00:00", "Rodaje sin pulso", "Carrera", segundos=1500, metros=5000,
             archivo="activities/22.gpx"),
        fila(23, "10 may 2025, 6:00:30", "Ya en Garmin", "Carrera", segundos=1800, metros=5000,
             archivo="activities/23.gpx"),
        fila(24, "10 may 2025, 6:00:30", "Bici a la misma hora", "Bicicleta", segundos=1800, metros=15000),
    ], {"activities/21.gpx": gpx(datetime(2025, 5, 6, 17, tzinfo=UTC), 10000, 3000, pulso=lambda i: 130 + i % 20),
        "activities/22.gpx": gpx(datetime(2025, 5, 8, 17, tzinfo=UTC), 5000, 1500),
        "activities/23.gpx": gpx(datetime(2025, 5, 10, 6, tzinfo=UTC), 5000, 1800)})


@pytest.fixture
def base(tmp_path):
    db = tmp_path / "data.sqlite3"
    store = DataStore(db)
    store.save_activities([Activity(activity_id="98765", activity_date=date(2025, 5, 10), activity_type="trail_running",
                                    name="De Garmin", duration_seconds=1790, distance_meters=4990, avg_hr=140,
                                    start_time_local="2025-05-10 08:00:00")])
    store.save_activity_laps([ActivityLap("98765", 1, distance_meters=4990, duration_seconds=1790, avg_hr=140)])
    store.marcar_detalle_intentado("98765")  # Garmin's one already asked: only the imported can leave a week «aproximado»
    return db


# -- R5 (R-327, C-417): never twice ------------------------------------------------------------------------------------

def test_importar_dos_veces_deja_la_base_igual(tmp_path, base):  # R5 · R-327, C-417
    zip_ = _zip_semana(tmp_path)
    garmin_antes = DataStore(base).get_activity("98765")
    primera = importar(zip_, base)
    assert primera.importadas == 3 and primera.ya_estaban == 1  # the run of Saturday is Garmin's
    antes = _volcado(base)
    segunda = importar(zip_, base)
    assert (segunda.importadas, segunda.ya_estaban) == (0, 4)
    assert _volcado(base) == antes
    store = DataStore(base)
    assert store.get_activity("strava-23") is None
    assert store.get_activity("98765") == garmin_antes
    assert len(store.get_laps("98765")) == 1
    assert store.get_activity("strava-24")["activity_type"] == "cycling"  # same minute, another sport: it enters


# -- R6 (R-328, G-72, C-418): the shoes stay as they were ------------------------------------------------------------

def test_las_importadas_no_tocan_las_zapatillas(tmp_path, base):  # R6 · R-328, G-72, C-418
    repo = Zapatillas(base)
    zapatilla = repo.dar_de_alta("Zapatilla de prueba", 310, 700, "suave", datetime(2026, 11, 23, 10, 0))
    km_antes = [z["km"] for z in repo.todas()]
    importar(_zip_semana(tmp_path), base)
    assert repo.contar() == {"nuevas": 0, "sin_zapatilla": 0}
    assert [z["km"] for z in repo.todas()] == km_antes
    assert repo.pendientes(date(2026, 11, 30)) == []
    assert pendientes_sin_crear(base, date(2026, 11, 30)) == []
    with sqlite3.connect(base) as conn:
        assert conn.execute("SELECT COUNT(*) FROM zapatilla_carrera WHERE activity_id LIKE 'strava-%'").fetchone()[0] == 0
    assert zapatilla["id"]


# -- R7 (R-330, C-420, C-421): Fitness and the load count them ------------------------------------------------------

def test_fitness_y_carga_cuentan_las_importadas(tmp_path, base):  # R7 · R-330, C-420, C-421
    importar(_zip_semana(tmp_path), base)
    Ajustes(base).cambiar(PULSO, desde=DESDE_SIEMPRE)
    store, historial = DataStore(base), Ajustes(base).historial_fc()
    (semana,) = fitness.semanas(store, historial, date(2025, 5, 11), 1)
    assert semana["lunes"] == LUNES.isoformat()
    correr = semana["deportes"]["correr"]
    assert correr["km"] == pytest.approx(10 + 5 + 4.99, abs=0.01)  # the two imported and Garmin's trail
    assert semana["deportes"]["bici"]["km"] == 15
    # by zones: the run with HR is in zones, not all «sin pulso»; the one without HR is
    assert sum(v for k, v in correr["zonas_km"].items() if k != "sin_pulso") == pytest.approx(10 + 4.99, abs=0.05)
    assert correr["zonas_km"]["sin_pulso"] == pytest.approx(5, abs=0.05)
    assert semana["aproximado"] is False  # nothing of them is waiting for Garmin's detail
    con = store.get_activity("strava-21")
    sin = store.get_activity("strava-22")
    assert trimp_for_activity(con, store.get_laps("strava-21")) > 0
    assert trimp_for_activity(sin, store.get_laps("strava-22")) == 0  # without HR, no load
    carga = fitness.carga(store, historial, date(2025, 5, 11), 14)
    assert {p["fecha"]: p["tss"] for p in carga["serie"]}["2025-05-06"] > 0


def test_la_sesion_dice_de_strava(tmp_path, base):  # R7 · C-420
    importar(_zip_semana(tmp_path), base)
    Ajustes(base).cambiar(PULSO, desde=DESDE_SIEMPRE)
    store, historial = DataStore(base), Ajustes(base).historial_fc()
    sesion = fitness.detalle_actividad(store, historial, "strava-21")
    assert sesion["actividad"]["fuente"] == "strava"
    assert len(sesion["vueltas"]) == 10
    assert all(v["zona"] is not None for v in sesion["vueltas"])
    assert sesion["curva"] is not None  # the track is its detail: the HR second by second
    assert fitness.detalle_actividad(store, historial, "98765")["actividad"]["fuente"] == "garmin"
