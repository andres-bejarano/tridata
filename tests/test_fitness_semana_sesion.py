"""Unit 093 (REC-37 of `ver-fitness`): tap a week of «Tus semanas» → its activities with the totals of that week next
to the mean of the 4 before → one of them → its session, whose laps now carry cadence, stride, SWOLF and climb.

Plans v37: R-239..R-243 · C-298..C-304, C-308 · S-83. R1-R5 of the unit, over a temporary SQLite. Every value is
invented: the sessions, the HR, the laps.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata import fitness
from tridata.models import Activity, ActivityLap
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroComidas
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 10, 21)  # a Wednesday
LUNES = date(2026, 10, 12)  # the week before this one: Monday 12 → Sunday 18
ZONAS = [[55, 120], [121, 140], [141, 155], [156, 168], [169, 180]]
PULSO = {"fc_max": 180, "fc_reposo": 55, "zonas_fc": ZONAS, "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}


@pytest.fixture(autouse=True)
def _entorno(monkeypatch):
    monkeypatch.setenv("TRIDATA_HR_MAX", "190")
    monkeypatch.setenv("TRIDATA_HR_REST", "60")


def _act(store: DataStore, id_: str, dia: date, tipo: str, segundos: float, metros: float | None,
         pulso: float | None = 140, laps: list[ActivityLap] | None = None) -> None:
    store.save_activities([Activity(activity_id=id_, activity_date=dia, activity_type=tipo, name=f"Sesión {id_}",
                                    duration_seconds=segundos, distance_meters=metros, avg_hr=pulso)])
    if laps:
        store.save_activity_laps(laps)


def _semana_del_9(store: DataStore, *, previas: int = 4) -> None:
    """C-298: the week of LUNES with 3 runs, 1 ride and 1 swim; each of the `previas` weeks before it, one run of
    1 h and 10 km (C-300: the mean of those weeks is 1 h, 10 km, 1 session)."""
    _act(store, "c1", LUNES, "running", 3600, 10000)
    _act(store, "c2", LUNES + timedelta(days=2), "running", 3600, 10000,
         laps=[ActivityLap("c2", 1, distance_meters=1000, duration_seconds=300, avg_hr=150, avg_cadence=172,
                           avg_stride_length_cm=105, elevation_gain_m=12)])
    _act(store, "b1", LUNES + timedelta(days=3), "road_biking", 7200, 60000)
    _act(store, "n1", LUNES + timedelta(days=4), "lap_swimming", 1800, 2000, pulso=None)
    _act(store, "c3", LUNES + timedelta(days=5), "trail_running", 3600, 10000)
    for k in range(1, previas + 1):
        _act(store, f"p{k}", LUNES - timedelta(weeks=k), "running", 3600, 10000)


class Web:
    def __init__(self, tmp_path) -> None:
        db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                             desde=DESDE_SIEMPRE)
        self.store = DataStore(db)
        self.cliente = TestClient(crear_app(RegistroComidas(db), MagicMock(), self.ajustes, hoy=lambda: HOY,
                                            objetivos=ObjetivosDia(db), store=self.store,
                                            actualizar_garmin=MagicMock()))

    def semana(self, lunes: date | str, deporte: str | None = None):
        consulta = f"/api/fitness/semana?lunes={lunes}" + (f"&deporte={deporte}" if deporte else "")
        return self.cliente.get(consulta)


@pytest.fixture
def web(tmp_path) -> Web:
    return Web(tmp_path)


# -- R1 · R-239, C-298, C-299: the activities of the week tapped (criterio portante) --------------------------------

def test_la_semana_tocada_ensena_sus_actividades_la_mas_nueva_arriba(web):  # R1 · R-239, C-298
    _semana_del_9(web.store)
    r = web.semana(LUNES)
    assert r.status_code == 200
    s = r.json()
    assert (s["lunes"], s["domingo"], s["en_curso"], s["deporte"]) == ("2026-10-12", "2026-10-18", False, "todos")
    assert [a["actividad_id"] for a in s["actividades"]] == ["c3", "n1", "b1", "c2", "c1"]
    c2 = next(a for a in s["actividades"] if a["actividad_id"] == "c2")
    assert (c2["fecha"], c2["deporte"], c2["nombre"], c2["distancia_m"], c2["duracion_s"], c2["pulso_medio"]) == (
        "2026-10-14", "Carrera", "Sesión c2", 10000, 3600, 140)
    assert c2["carga"] is not None and c2["carga"] > 0
    n1 = next(a for a in s["actividades"] if a["actividad_id"] == "n1")
    assert n1["pulso_medio"] is None and n1["carga"] is None  # never a 0 where Garmin gave nothing


def test_con_un_deporte_solo_las_de_ese_deporte(web):  # R1 · C-299
    _semana_del_9(web.store)
    s = web.semana(LUNES, "correr").json()
    assert s["deporte"] == "correr"
    assert [a["actividad_id"] for a in s["actividades"]] == ["c3", "c2", "c1"]
    assert (s["totales"]["horas"], s["totales"]["km"], s["totales"]["sesiones"]) == (3.0, 30.0, 3)


# -- R2 · R-240, C-300, C-301: totals next to the mean of the 4 weeks before ----------------------------------------

def test_totales_junto_a_la_media_de_las_4_anteriores(web):  # R2 · C-300
    _semana_del_9(web.store)
    s = web.semana(LUNES).json()
    assert (s["totales"]["horas"], s["totales"]["km"], s["totales"]["sesiones"]) == (5.5, 92.0, 5)
    assert s["semanas_media"] == 4
    assert (s["media"]["horas"], s["media"]["km"], s["media"]["sesiones"]) == (1.0, 10.0, 1.0)
    # the load is the one of «Tus semanas» (no second aggregation): the same figures as /api/fitness?dias=42
    semanas = {x["lunes"]: x for x in web.cliente.get("/api/fitness?dias=42").json()["semanas"]}
    assert s["totales"]["carga"] == semanas["2026-10-12"]["total"]["carga"]
    previas = [semanas[(LUNES - timedelta(weeks=k)).isoformat()]["total"]["carga"] for k in range(1, 5)]
    assert s["media"]["carga"] == pytest.approx(sum(previas) / 4, abs=0.05)


def test_con_solo_2_semanas_de_historia_la_media_es_de_esas_2(web):  # R2 · C-301
    _semana_del_9(web.store, previas=2)
    s = web.semana(LUNES).json()
    assert s["semanas_media"] == 2
    assert (s["media"]["horas"], s["media"]["sesiones"]) == (1.0, 1.0)


def test_sin_historia_antes_no_hay_media(web):  # R2
    _semana_del_9(web.store, previas=0)
    s = web.semana(LUNES).json()
    assert s["semanas_media"] == 0 and s["media"] is None


def test_la_semana_en_curso_lo_dice_y_no_cuenta_manana(web):  # R2
    esta = HOY - timedelta(days=HOY.weekday())
    _act(web.store, "hoy", HOY, "running", 3600, 10000)
    _act(web.store, "manana", HOY + timedelta(days=1), "running", 3600, 10000)
    s = web.semana(esta).json()
    assert s["en_curso"] is True
    assert [a["actividad_id"] for a in s["actividades"]] == ["hoy"]


def test_la_fuerza_no_suma_km(web):  # R2
    _act(web.store, "f1", LUNES, "strength_training", 2700, None, pulso=110)
    s = web.semana(LUNES, "fuerza").json()
    assert s["totales"]["km"] is None and s["totales"]["sesiones"] == 1


# -- R5 · R-243, C-304: a week without activities of the sport --------------------------------------------------------

def test_semana_sin_actividades_del_deporte(web):  # R5 · C-304
    _semana_del_9(web.store)
    s = web.semana(LUNES - timedelta(weeks=1), "nadar").json()
    assert s["actividades"] == []
    assert (s["totales"]["horas"], s["totales"]["sesiones"]) == (0.0, 0)


# -- R1: what comes by URL is checked (like ?dias=) ---------------------------------------------------------------------

@pytest.mark.parametrize("lunes", ["2026-10-13", "2026-13-01", "", "lunes", "2026-10-26"])
def test_un_lunes_que_no_vale_es_422(web, lunes):  # R1
    r = web.semana(lunes)
    assert r.status_code == 422 and r.json()["error"]


def test_sin_lunes_es_422(web):
    assert web.cliente.get("/api/fitness/semana").status_code == 422


def test_un_deporte_que_no_existe_es_422(web):  # R1
    r = web.semana(LUNES, "patinar")
    assert r.status_code == 422 and r.json()["error"]


# -- R3, R4 · R-241, R-242, C-302, C-303, C-308: the session, with cadence and climb by sport -----------------------

def test_vuelta_de_carrera_con_cadencia_zancada_y_subida():  # R4 · C-302
    v = fitness._vuelta(1, {"distance_meters": 1000, "duration_seconds": 300, "avg_hr": 150, "avg_cadence": 172,
                            "avg_stride_length_cm": 105, "elevation_gain_m": 12}, "running", None)
    assert (v["cadencia"], v["zancada_m"], v["subida_m"]) == (172, 1.05, 12)
    assert "swolf" not in v


def test_vuelta_de_bici_con_cadencia_y_subida():  # R4 · C-305
    v = fitness._vuelta(1, {"distance_meters": 5000, "duration_seconds": 600, "avg_bike_cadence": 86,
                            "avg_cadence": None, "elevation_gain_m": 40}, "road_biking", None)
    assert (v["cadencia"], v["subida_m"]) == (86, 40)
    assert "zancada_m" not in v and "swolf" not in v


def test_vuelta_de_natacion_con_brazadas_y_swolf():  # R4
    v = fitness._vuelta(1, {"distance_meters": 100, "duration_seconds": 110, "avg_swim_cadence": 28,
                            "avg_swolf": 38}, "lap_swimming", None)
    assert (v["cadencia"], v["swolf"]) == (28, 38)
    assert "subida_m" not in v and "zancada_m" not in v


@pytest.mark.parametrize("tipo, claves", [("running", ("cadencia", "zancada_m", "subida_m")),
                                          ("cycling", ("cadencia", "subida_m")),
                                          ("lap_swimming", ("cadencia", "swolf"))])
def test_lo_que_garmin_no_dio_es_none_nunca_0(tipo, claves):  # R4 · C-303
    for vuelta in ({}, {"avg_cadence": 0, "avg_bike_cadence": 0, "avg_swim_cadence": 0, "avg_swolf": 0,
                        "avg_stride_length_cm": 0, "elevation_gain_m": None}):
        v = fitness._vuelta(1, {"distance_meters": 100, "duration_seconds": 60, **vuelta}, tipo, None)
        assert all(v[k] is None for k in claves), (tipo, vuelta, v)


def test_otros_deportes_no_inventan_cadencia():  # R4
    v = fitness._vuelta(1, {"duration_seconds": 600, "avg_cadence": 50}, "strength_training", None)
    assert not {"cadencia", "zancada_m", "swolf", "subida_m"} & set(v)


def test_la_sesion_de_una_actividad_de_la_lista(web):  # R3, R4 · C-302, C-308
    _semana_del_9(web.store)
    web.ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)
    d = web.cliente.get("/api/actividad/c2").json()
    (v,) = d["vueltas"]
    assert (v["zona"], v["cadencia"], v["zancada_m"], v["subida_m"]) == (3, 172, 1.05, 12)
    # Fitness › Plan reads the same detail: same or better
    assert web.cliente.get("/api/plan/actividad/c2").json()["vueltas"][0]["cadencia"] == 172
