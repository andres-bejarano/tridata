"""Unit 097 (REC-42 of `ver-fitness`): a period chosen with dates (or a shortcut).

Plans v41: R-263, R-268 · C-331, C-337. R1 and R6 of the unit. Unit 108 (plans v51, R-264): the table of this period
against the one before (R-265..R-267, C-333..C-336) is gone, and its tests with it. Every value is invented.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from tridata import fitness
from tridata.models import Activity
from tridata.nutricion import Ajustes
from tridata.storage import DataStore

HOY = date(2026, 10, 21)
DESDE, HASTA = date(2026, 9, 1), date(2026, 10, 15)  # 45 days


@pytest.fixture(autouse=True)
def _entorno(monkeypatch):
    monkeypatch.setenv("TRIDATA_HR_MAX", "190")
    monkeypatch.setenv("TRIDATA_HR_REST", "60")


def _act(id_: str, dia: date, tipo: str, segundos: float, metros: float = 0, pulso: float | None = 140) -> Activity:
    return Activity(activity_id=id_, activity_date=dia, activity_type=tipo, name=f"Prueba {id_}",
                    duration_seconds=segundos, distance_meters=metros, avg_hr=pulso)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "data.sqlite3"


# -- R1 · R-263, C-331: the period ----------------------------------------------------------------------------------

def test_periodo_desde_atajo_son_los_ultimos_n_dias():  # R1
    hoy = date(2026, 11, 19)
    assert fitness.periodo(hoy, dias=91) == (date(2026, 8, 21), hoy)
    assert fitness.periodo(hoy, dias=14) == (date(2026, 11, 6), hoy)
    assert fitness.periodo(hoy) == (hoy - timedelta(days=41), hoy)  # 42 by default


def test_periodo_con_fechas():  # R1 · C-331
    assert fitness.periodo(HOY, desde=DESDE, hasta=HASTA) == (DESDE, HASTA)
    assert fitness.periodo(HOY, desde="2026-09-01", hasta="2026-10-15") == (DESDE, HASTA)


def test_periodo_atajo_que_no_vale():
    with pytest.raises(ValueError, match="14, 42, 91 o 365"):
        fitness.periodo(HOY, dias=30)


# -- R6 · R-268, C-337: dates that are not valid --------------------------------------------------------------------

@pytest.mark.parametrize(("desde", "hasta", "texto"), [
    (date(2026, 10, 15), date(2026, 9, 1), "el “desde” tiene que ser antes del “hasta”"),
    (date(2026, 10, 1), date(2026, 10, 22), "el “hasta” no puede ser después de hoy"),
    (date(2026, 10, 1), date(2026, 10, 6), "7 días"),  # 6 days
    (date(2024, 10, 20), date(2026, 10, 21), "2 años"),  # 732 days
    ("2026-13-01", "2026-10-15", "no es una fecha"),
    ("ayer", "2026-10-15", "no es una fecha"),
])
def test_fechas_que_no_valen(desde, hasta, texto):  # R6 · C-337
    with pytest.raises(ValueError, match=texto):
        fitness.periodo(HOY, desde=desde, hasta=hasta)


def test_los_limites_7_dias_y_2_anos_valen():  # R6
    assert fitness.periodo(HOY, desde=date(2026, 10, 15), hasta=HOY) == (date(2026, 10, 15), HOY)  # 7 days
    assert fitness.periodo(HOY, desde=date(2024, 10, 21), hasta=HOY) == (date(2024, 10, 21), HOY)  # 731 days
    assert fitness.periodo(HOY, desde=HOY - timedelta(days=6), hasta=HOY)[0] == date(2026, 10, 15)


# -- R1 · R-263, C-331: the load and the weeks cut to the period ----------------------------------------------------

@pytest.fixture
def julio_a_octubre(db):
    store = DataStore(db)
    dia, actividades = date(2026, 7, 1), []
    while dia <= HOY:
        actividades.append(_act(f"b{dia.isoformat()}", dia, "cycling", 3600, 30000, 130 + dia.day % 9))
        dia += timedelta(days=2)
    store.save_activities(actividades)
    return store, Ajustes(db)


def test_carga_recortada_con_los_valores_de_la_serie_entera(julio_a_octubre):  # R1 · C-331
    store, ajustes = julio_a_octubre
    historial = ajustes.historial_fc()
    entera = fitness.carga(store, historial, HOY, dias=365)
    recortada = fitness.carga(store, historial, HOY, periodo=(DESDE, HASTA))
    fechas = [p["fecha"] for p in recortada["serie"]]
    assert fechas[0] == "2026-09-01" and fechas[-1] == "2026-10-15" and len(fechas) == 45
    assert recortada["serie"] == [p for p in entera["serie"] if DESDE.isoformat() <= p["fecha"] <= HASTA.isoformat()]
    assert recortada["hoy"] == entera["hoy"] and recortada["hoy"]["fecha"] == HOY.isoformat()


def test_fitness_con_fechas_semanas_que_tocan_el_periodo(julio_a_octubre):  # R1 · C-331
    store, ajustes = julio_a_octubre
    f = fitness.fitness(store, ajustes.historial_fc(), HOY, desde=DESDE, hasta=HASTA)
    assert f["periodo"]["desde"] == "2026-09-01" and f["periodo"]["hasta"] == "2026-10-15"
    lunes = [s["lunes"] for s in f["semanas"]]
    assert lunes == ["2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12"]
    assert f["carga"]["serie"][0]["fecha"] == "2026-09-01" and f["carga"]["hoy"]["fecha"] == HOY.isoformat()
    assert f["fecha"] == HOY.isoformat()


def test_fitness_con_atajo_como_siempre(julio_a_octubre):  # R1: the four shortcuts as today
    store, ajustes = julio_a_octubre
    historial = ajustes.historial_fc()
    f = fitness.fitness(store, historial, HOY, dias=42)
    assert f["carga"] == fitness.carga(store, historial, HOY, dias=42)
    assert f["semanas"] == fitness.semanas(store, historial, HOY, 6)
    assert f["periodo"]["desde"] == (HOY - timedelta(days=41)).isoformat() and f["periodo"]["hasta"] == HOY.isoformat()
