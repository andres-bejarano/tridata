"""Unit 108 (REC-51 of `ver-fitness`): «Tus semanas» as a line — each week also with its climb and its km in each zone
(by laps) — and a second period to compare with, by week number.

Plans v51: R-310..R-316 · C-393..C-403 · R-264, C-332. R1-R8 of the unit. Every value is invented.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata import fitness
from tridata.models import Activity, ActivityLap
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, RegistroComidas
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 11, 25)  # a Wednesday
LUNES = date(2026, 11, 9)  # the week of the Behobia (two weeks before the one in course)
ZONAS = [[55, 120], [121, 140], [141, 155], [156, 168], [169, 180]]
PULSO = {"fc_max": 180, "fc_reposo": 55, "zonas_fc": ZONAS, "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}
CEROS = {"z1": 0, "z2": 0, "z3": 0, "z4": 0, "z5": 0, "sin_pulso": 0}


@pytest.fixture(autouse=True)
def _entorno(monkeypatch):
    monkeypatch.setenv("TRIDATA_HR_MAX", "190")
    monkeypatch.setenv("TRIDATA_HR_REST", "60")


def _act(id_: str, dia: date, tipo: str, segundos: float, metros: float | None = None, pulso: float | None = 140,
         subida: float | None = None) -> Activity:
    return Activity(activity_id=id_, activity_date=dia, activity_type=tipo, name=f"Prueba {id_}",
                    duration_seconds=segundos, distance_meters=metros, avg_hr=pulso, elevation_gain_m=subida)


def _semana(semanas: list[dict], lunes: date) -> dict:
    (s,) = [s for s in semanas if s["lunes"] == lunes.isoformat()]
    return s


# -- R2, R3 · R-311, R-312, C-395: km by zone by laps and the climb of the week -------------------------------------

@pytest.fixture
def behobia(tmp_path):
    db = tmp_path / "data.sqlite3"
    store, ajustes = DataStore(db), Ajustes(db)
    ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)
    # C-395: 3 laps, 4 km in Z2, 5 km in Z4 and 1 km in Z1, and 120 m of climb
    store.save_activities([_act("c3", LUNES, "running", 3600, 10000, 145, subida=120)])
    store.save_activity_laps([ActivityLap(activity_id="c3", lap_index=i, distance_meters=m, duration_seconds=s,
                                          avg_hr=h)
                              for i, (m, s, h) in enumerate(((4000, 1400, 130), (5000, 1600, 160), (1000, 600, 100)))])
    # a run without laps: all its km «sin pulso» (its 223 m of climb add up too)
    store.save_activities([_act("sin", LUNES + timedelta(days=2), "running", 1800, 6000, 150, subida=223)])
    # a ride with laps, one without HR → that lap's km «sin pulso»
    store.save_activities([_act("b1", LUNES + timedelta(days=3), "cycling", 3600, 30000, 135)])
    store.save_activity_laps([ActivityLap(activity_id="b1", lap_index=0, distance_meters=20000, duration_seconds=2400,
                                          avg_hr=135),
                              ActivityLap(activity_id="b1", lap_index=1, distance_meters=10000, duration_seconds=1200)])
    # strength: never km, never km by zone
    store.save_activities([_act("f1", LUNES + timedelta(days=4), "strength_training", 2700, 400, 110)])
    return store, ajustes


def test_km_por_zona_por_vueltas_y_desnivel(behobia):  # R2, R3 · R-311, R-312, C-395
    store, ajustes = behobia
    s = _semana(fitness.semanas(store, ajustes.historial_fc(), HOY, 3), LUNES)
    correr = s["deportes"]["correr"]
    assert correr["km"] == 16.0
    assert correr["zonas_km"] == {**CEROS, "z1": 1.0, "z2": 4.0, "z4": 5.0, "sin_pulso": 6.0}
    assert correr["subida_m"] == 343
    assert s["deportes"]["bici"]["zonas_km"] == {**CEROS, "z2": 20.0, "sin_pulso": 10.0}
    assert s["deportes"]["bici"]["subida_m"] is None  # Garmin gave no climb: «—», never an invented 0
    assert s["deportes"]["fuerza"]["km"] is None and s["deportes"]["fuerza"]["zonas_km"] is None
    assert s["total"]["zonas_km"] == {**CEROS, "z1": 1.0, "z2": 24.0, "z4": 5.0, "sin_pulso": 16.0}
    assert s["total"]["subida_m"] == 343
    # R2: the bar of km adds up to the km of the week
    assert sum(s["total"]["zonas_km"].values()) == pytest.approx(s["total"]["km"], abs=0.1)
    # the zones in seconds stay as they were (R-235)
    assert correr["zonas"]["z2"] == 1400 and correr["zonas"]["z4"] == 1600


def test_las_vueltas_que_no_suman_la_distancia_se_reparten_hasta_cuadrar(tmp_path):  # R2: ±0,1 km
    db = tmp_path / "data.sqlite3"
    store, ajustes = DataStore(db), Ajustes(db)
    ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)
    store.save_activities([_act("c", LUNES, "running", 3000, 10050, 150)])
    store.save_activity_laps([ActivityLap(activity_id="c", lap_index=i, distance_meters=1000, duration_seconds=300,
                                          avg_hr=130 if i < 5 else 160) for i in range(10)])
    s = _semana(fitness.semanas(store, ajustes.historial_fc(), HOY, 3), LUNES)
    assert sum(s["total"]["zonas_km"].values()) == pytest.approx(s["total"]["km"], abs=0.1)
    assert s["total"]["zonas_km"]["z2"] == pytest.approx(s["total"]["zonas_km"]["z4"], abs=0.01)


def test_semana_sin_nada_en_cero_y_sin_desnivel(tmp_path):  # R1 · C-394
    db = tmp_path / "data.sqlite3"
    vacia = fitness.semanas(DataStore(db), Ajustes(db).historial_fc(), HOY, 2)[0]
    assert vacia["total"]["km"] == 0 and vacia["total"]["horas"] == 0
    assert vacia["total"]["zonas_km"] == CEROS and vacia["total"]["subida_m"] is None


def test_km_con_dos_decimales_como_strava(tmp_path):  # R3: «81,96 km»
    db = tmp_path / "data.sqlite3"
    store = DataStore(db)
    store.save_activities([_act("a", LUNES, "running", 3600, 34000), _act("b", LUNES, "running", 4800, 20030),
                           _act("c", LUNES + timedelta(days=1), "running", 6000, 27930)])
    s = _semana(fitness.semanas(store, Ajustes(db).historial_fc(), HOY, 3), LUNES)
    assert s["total"]["km"] == 81.96


# -- R4, R7 · R-313, R-316, C-398, C-401: a second period -----------------------------------------------------------

def test_periodo_futuro_solo_para_comparar():  # R7 · R-316
    hasta = date(2026, 12, 6)
    assert fitness.periodo(HOY, desde=date(2026, 9, 1), hasta=hasta, futuro=True) == (date(2026, 9, 1), hasta)
    with pytest.raises(ValueError, match="el “hasta” no puede ser después de hoy"):
        fitness.periodo(HOY, desde=date(2026, 9, 1), hasta=hasta)
    with pytest.raises(ValueError, match="2 años"):
        fitness.periodo(HOY, desde=date(2024, 12, 31), hasta=date(2027, 1, 1), futuro=True)  # 732 days


@pytest.fixture
def dos_anos(tmp_path):
    db = tmp_path / "data.sqlite3"
    store, ajustes = DataStore(db), Ajustes(db)
    actividades, dia = [], date(2025, 9, 3)
    while dia <= HOY:
        actividades.append(_act(f"c{dia.isoformat()}", dia, "running", 3600, 10000 + 1000 * (dia.year - 2025)))
        dia += timedelta(days=7)
    store.save_activities(actividades)
    return store, ajustes


def test_fitness_con_otro_periodo(dos_anos):  # R4, R7 · R-313, R-316, C-398
    store, ajustes = dos_anos
    f = fitness.fitness(store, ajustes.historial_fc(), HOY, desde=date(2026, 9, 1), hasta=date(2026, 12, 6),
                        otro=(date(2025, 9, 1), date(2025, 12, 6)))
    # this one: from the week of 1/9/2026 to the one in course (the weeks still to come are not there)
    lunes = [s["lunes"] for s in f["semanas"]]
    assert lunes[0] == "2026-08-31" and lunes[-1] == "2026-11-23" and len(lunes) == 13
    assert f["semanas"][-1]["en_curso"] is True
    assert f["periodo"]["semanas"] == 14  # from the week of 31/8 to the one of 30/11 (6/12 is its Sunday)
    otro = f["otro"]
    assert otro["periodo"]["desde"] == "2025-09-01" and otro["periodo"]["hasta"] == "2025-12-06"
    assert [s["lunes"] for s in otro["semanas"]][0] == "2025-09-01"
    assert len(otro["semanas"]) == otro["periodo"]["semanas"] == 14
    assert otro["semanas"][0]["total"]["km"] == 10.0 and f["semanas"][1]["total"]["km"] == 11.0
    assert otro["historial_desde"] == "2025-09-03"  # the history starts after its «desde»
    assert f["historial_desde"] is None
    assert "comparacion" not in f  # R8 · R-264


def test_un_periodo_entero_por_llegar_no_tiene_semanas(dos_anos):  # R7
    store, ajustes = dos_anos
    f = fitness.fitness(store, ajustes.historial_fc(), HOY, dias=42, otro=(date(2027, 1, 4), date(2027, 2, 28)))
    assert f["otro"]["semanas"] == [] and f["otro"]["periodo"]["semanas"] == 8
    assert len(f["semanas"]) == 6 and f["periodo"]["semanas"] == 6


# -- R4, R7, R8 · the HTTP frontier ---------------------------------------------------------------------------------

@pytest.fixture
def cliente(dos_anos, tmp_path):
    store, ajustes = dos_anos
    return TestClient(crear_app(RegistroComidas(tmp_path / "data.sqlite3"), MagicMock(), ajustes, hoy=lambda: HOY,
                                store=store))


def test_api_fitness_con_otro_periodo(cliente):  # R4, R7 · C-398, C-401
    r = cliente.get("/api/fitness?desde=2026-09-01&hasta=2026-12-06&otro_desde=2025-09-01&otro_hasta=2025-12-06")
    assert r.status_code == 200
    f = r.json()
    assert f["periodo"]["hasta"] == "2026-12-06" and len(f["semanas"]) == 13
    assert f["otro"]["periodo"]["desde"] == "2025-09-01" and len(f["otro"]["semanas"]) == 14
    assert f["otro"]["historial_desde"] == "2025-09-03"
    assert f["carga"]["serie"][-1]["fecha"] == HOY.isoformat()  # the load stops today
    # a shortcut can be compared too
    g = cliente.get("/api/fitness?dias=91&otro_desde=2025-08-25&otro_hasta=2025-11-23").json()
    assert len(g["semanas"]) == 13 and len(g["otro"]["semanas"]) == 13


@pytest.mark.parametrize(("consulta", "texto"), [
    ("otro_desde=2025-12-06&otro_hasta=2025-09-01", "el “desde” tiene que ser antes del “hasta”"),
    ("otro_desde=2025-09-01&otro_hasta=2025-09-03", "7 días"),
    ("otro_desde=2024-01-01&otro_hasta=2026-01-02", "2 años"),
    ("otro_desde=2025-13-01&otro_hasta=2025-12-06", "no es una fecha"),
    ("otro_desde=2025-09-01", "«desde» y «hasta»"),
])
def test_api_fitness_otro_periodo_que_no_vale(cliente, consulta, texto):  # R7 · R-268
    r = cliente.get(f"/api/fitness?desde=2026-09-01&hasta=2026-11-20&{consulta}")
    assert r.status_code == 400
    assert texto in r.json()["error"]


def test_sin_comparar_el_hasta_no_pasa_de_hoy(cliente):  # R7
    r = cliente.get("/api/fitness?desde=2026-09-01&hasta=2026-12-06")
    assert r.status_code == 400 and "después de hoy" in r.json()["error"]


def test_api_fitness_ya_no_trae_la_tabla(cliente):  # R8 · R-264, C-332
    for consulta in ("dias=42", "dias=365", "desde=2026-09-01&hasta=2026-10-15"):
        f = cliente.get(f"/api/fitness?{consulta}").json()
        assert "comparacion" not in f and "otro" not in f
        assert f["historial_desde"] is None or f["historial_desde"] == "2025-09-03"
    assert not hasattr(fitness, "comparar") and not hasattr(fitness, "anterior")


def test_api_fitness_sin_garmin_compara_a_cero(tmp_path):  # app without store
    db = tmp_path / "data.sqlite3"
    cliente = TestClient(crear_app(RegistroComidas(db), MagicMock(), Ajustes(db), hoy=lambda: HOY, store=None))
    f = cliente.get("/api/fitness?dias=42&otro_desde=2025-10-13&otro_hasta=2025-11-23").json()
    assert len(f["otro"]["semanas"]) == 6 and f["otro"]["historial_desde"] is None
    assert "comparacion" not in f and all(s["total"]["km"] == 0 for s in f["otro"]["semanas"])
