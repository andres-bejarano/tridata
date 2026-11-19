"""Unit 097 (REC-42 of `ver-fitness`): a period chosen with dates (or a shortcut) and its comparison with the period
before of the same length, sport by sport.

Plans v41: R-263..R-268 · C-331..C-337. R1-R6 of the unit. Every value is invented.
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
ORDEN = ["correr", "bici", "nadar", "fuerza", "otros", "total"]


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


# -- R1, R3 · R-263, R-265, C-331, C-333: the period and the one before ---------------------------------------------

def test_periodo_desde_atajo_son_los_ultimos_n_dias():  # R3 · C-333
    hoy = date(2026, 11, 19)
    assert fitness.periodo(hoy, dias=91) == (date(2026, 8, 21), hoy)
    assert fitness.anterior(date(2026, 8, 21), hoy) == (date(2026, 5, 22), date(2026, 8, 20))
    assert fitness.periodo(hoy, dias=14) == (date(2026, 11, 6), hoy)
    assert fitness.periodo(hoy) == (hoy - timedelta(days=41), hoy)  # 42 by default


def test_periodo_con_fechas_y_su_anterior():  # R1, R3 · C-331, C-333
    assert fitness.periodo(HOY, desde=DESDE, hasta=HASTA) == (DESDE, HASTA)
    assert fitness.periodo(HOY, desde="2026-09-01", hasta="2026-10-15") == (DESDE, HASTA)
    assert fitness.anterior(DESDE, HASTA) == (date(2026, 7, 18), date(2026, 8, 31))


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


# -- R2, R5 · R-264, R-267, C-332, C-335, C-336: the table (criterio portante R-264) ------------------------------

@pytest.fixture
def dos_periodos(db):
    """This one (1/9-15/10) and the one before (18/7-31/8), with a first activity before both (no warning)."""
    store = DataStore(db)
    este = lambda n: DESDE + timedelta(days=n)  # noqa: E731
    antes = lambda n: date(2026, 7, 18) + timedelta(days=n)  # noqa: E731
    store.save_activities([
        _act("primera", date(2026, 7, 1), "running", 1800, 5000),
        # this period: run 2 h 20 km 2 · bike 2 h 60 km 1 · swim 5 h 6 km 2 · strength 1 h 1 · walk 0.5 h 3 km 1
        _act("c1", este(0), "running", 3600, 10000), _act("c2", este(44), "running", 3600, 10000),
        _act("b1", este(10), "cycling", 7200, 60000),
        _act("n1", este(3), "lap_swimming", 9000, 3000), _act("n2", este(20), "lap_swimming", 9000, 3000),
        _act("f1", este(5), "strength_training", 3600, 0),
        _act("o1", este(7), "walking", 1800, 3000),
        # the one before: run 1.5 h 15 km 1 · bike 4 h 120 km 2 · strength 1 h 2 · no swim, no other
        _act("pc1", antes(0), "running", 5400, 15000),
        _act("pb1", antes(3), "cycling", 7200, 60000), _act("pb2", antes(44), "cycling", 7200, 60000),
        _act("pf1", antes(1), "strength_training", 1800, 0), _act("pf2", antes(2), "strength_training", 1800, 0),
        # outside both
        _act("despues", date(2026, 10, 16), "running", 3600, 10000),
    ])
    return store


def _filas(comparacion: dict) -> dict[str, dict]:
    return {f["deporte"]: f for f in comparacion["filas"]}


def test_tabla_horas_km_sesiones_y_horas_por_semana(dos_periodos):  # R2, R3 · R-264, C-332 (portante)
    c = fitness.comparar(dos_periodos, DESDE, HASTA)
    assert c["este"] == {"desde": "2026-09-01", "hasta": "2026-10-15", "dias": 45}
    assert c["anterior"] == {"desde": "2026-07-18", "hasta": "2026-08-31", "dias": 45}
    assert c["historial_desde"] is None
    assert [f["deporte"] for f in c["filas"]] == ORDEN
    f = _filas(c)
    assert f["correr"]["este"] == {"horas": 2.0, "km": 20.0, "sesiones": 2, "horas_semana": 0.31}
    assert f["correr"]["anterior"] == {"horas": 1.5, "km": 15.0, "sesiones": 1, "horas_semana": 0.23}
    assert f["correr"]["diferencia"] == {"horas": 0.5, "km": 5.0, "sesiones": 1, "horas_semana": 0.08}
    assert f["correr"]["porcentaje"] == {"horas": 33, "km": 33, "sesiones": 100, "horas_semana": 33}
    assert f["bici"]["diferencia"] == {"horas": -2.0, "km": -60.0, "sesiones": -1, "horas_semana": -0.31}
    assert f["bici"]["porcentaje"] == {"horas": -50, "km": -50, "sesiones": -50, "horas_semana": -50}
    # strength never has km
    assert f["fuerza"]["este"] == {"horas": 1.0, "km": None, "sesiones": 1, "horas_semana": 0.16}
    assert f["fuerza"]["anterior"]["km"] is None and f["fuerza"]["diferencia"]["km"] is None
    assert f["fuerza"]["porcentaje"] == {"horas": 0, "km": None, "sesiones": -50, "horas_semana": 0}
    assert f["total"]["este"] == {"horas": 10.5, "km": 89.0, "sesiones": 7, "horas_semana": 1.63}
    assert f["total"]["anterior"] == {"horas": 6.5, "km": 135.0, "sesiones": 5, "horas_semana": 1.01}
    assert f["total"]["diferencia"] == {"horas": 4.0, "km": -46.0, "sesiones": 2, "horas_semana": 0.62}
    assert f["total"]["porcentaje"] == {"horas": 62, "km": -34, "sesiones": 40, "horas_semana": 62}


def test_anterior_a_cero_da_porcentaje_none(dos_periodos):  # R5 · R-267, C-336
    f = _filas(fitness.comparar(dos_periodos, DESDE, HASTA))
    assert f["nadar"]["anterior"] == {"horas": 0.0, "km": 0.0, "sesiones": 0, "horas_semana": 0.0}
    assert f["nadar"]["diferencia"]["horas"] == 5.0  # +5 h
    assert f["nadar"]["porcentaje"] == {"horas": None, "km": None, "sesiones": None, "horas_semana": None}
    assert f["otros"]["porcentaje"]["horas"] is None and f["otros"]["diferencia"]["km"] == 3.0


def test_historial_corto_avisa_y_no_da_porcentajes(db):  # R5 · R-267, C-335
    store = DataStore(db)
    store.save_activities([_act("a1", date(2026, 8, 10), "running", 3600, 10000),
                           _act("a2", date(2026, 9, 10), "running", 7200, 20000)])
    c = fitness.comparar(store, DESDE, HASTA)
    assert c["historial_desde"] == "2026-08-10"
    f = _filas(c)
    assert f["correr"]["diferencia"]["horas"] == 1.0 and f["correr"]["anterior"]["horas"] == 1.0
    assert all(v is None for fila in c["filas"] for v in fila["porcentaje"].values())


def test_sin_garmin_la_tabla_a_cero_y_sin_aviso():  # app without store
    c = fitness.comparar(None, DESDE, HASTA)
    assert c["historial_desde"] is None and [f["deporte"] for f in c["filas"]] == ORDEN
    assert all(f["este"]["horas"] == 0 and f["anterior"]["sesiones"] == 0 for f in c["filas"])


def test_la_tabla_va_en_fitness_con_atajos_y_fechas(dos_periodos, db):  # R4 · R-266, C-334
    historial = Ajustes(db).historial_fc()
    con_fechas = fitness.fitness(dos_periodos, historial, HOY, desde=DESDE, hasta=HASTA)
    assert con_fechas["comparacion"] == fitness.comparar(dos_periodos, DESDE, HASTA)
    con_atajo = fitness.fitness(dos_periodos, historial, HOY, dias=91)
    assert con_atajo["comparacion"] == fitness.comparar(dos_periodos, HOY - timedelta(days=90), HOY)
    assert [f["deporte"] for f in con_atajo["comparacion"]["filas"]] == ORDEN
