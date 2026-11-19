"""Unit 095 (P-20261119-71e1bc19, plans v39: R-253 · C-317 · C-319): «Tus semanas» says `aproximado` only while some
session of a type whose detail IS asked for (`sync.DETALLE_TYPES`) has not been asked yet. Pool swimming, strength and
the sessions Garmin already answered without detail (`intentado`) keep counting by laps, but do not light the warning.

R1-R3 of the unit. Every value is invented; nothing here talks to Garmin nor touches ~/.tridata.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from tridata import fitness
from tridata.models import Activity, ActivityLap
from tridata.nutricion import DESDE_SIEMPRE, Ajustes
from tridata.storage import DataStore

ZONAS = [[55, 120], [121, 140], [141, 155], [156, 168], [169, 180]]
PULSO = {"fc_max": 180, "fc_reposo": 55, "zonas_fc": ZONAS, "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}
LUNES = date(2026, 11, 9)
HOY = LUNES + timedelta(days=9)  # Wednesday of the week after


@pytest.fixture(autouse=True)
def _entorno(monkeypatch):
    monkeypatch.setenv("TRIDATA_HR_MAX", "190")
    monkeypatch.setenv("TRIDATA_HR_REST", "60")


@pytest.fixture
def base(tmp_path) -> tuple[DataStore, Ajustes]:
    db = tmp_path / "data.sqlite3"
    store, ajustes = DataStore(db), Ajustes(db)
    ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)
    return store, ajustes


def _detalle(segundos: int, pulso: float) -> dict:
    return {"t": [float(i) for i in range(segundos)], "distancia": [i * 3.0 for i in range(segundos)],
            "pulso": [pulso] * segundos, "velocidad": [3.0] * segundos, "altura": [80.0] * segundos,
            "potencia": [None] * segundos}


def _actividad(store: DataStore, id_: str, dia: date, tipo: str, segundos: int, avg_hr: int) -> None:
    store.save_activities([Activity(activity_id=id_, activity_date=dia, activity_type=tipo, name=id_,
                                    duration_seconds=segundos, avg_hr=avg_hr)])
    store.save_activity_laps([ActivityLap(id_, 1, duration_seconds=segundos, avg_hr=avg_hr)])


def _semana(store: DataStore, ajustes: Ajustes) -> dict:
    """The week of LUNES (the middle one of three)."""
    return fitness.semanas(store, ajustes.historial_fc(), HOY, 3)[1]


def test_piscina_y_fuerza_no_encienden_el_aviso(base):  # R1 · R-253, C-317
    store, ajustes = base
    _actividad(store, "carrera", LUNES, "running", 600, avg_hr=110)  # laps say Z1, detail says Z5
    store.guardar_detalle("carrera", _detalle(600, 175.0))
    _actividad(store, "bici", LUNES + timedelta(days=1), "cycling", 300, avg_hr=110)
    store.guardar_detalle("bici", _detalle(300, 140.0))  # Z2
    _actividad(store, "piscina", LUNES + timedelta(days=2), "lap_swimming", 900, avg_hr=150)  # Z3 by laps
    _actividad(store, "fuerza", LUNES + timedelta(days=3), "strength_training", 1200, avg_hr=110)  # Z1 by laps

    semana = _semana(store, ajustes)
    assert semana["aproximado"] is False
    assert semana["total"]["zonas"] == {"z1": 1200, "z2": 300, "z3": 900, "z4": 0, "z5": 600, "sin_pulso": 0}


def test_bici_sin_pedir_enciende_el_aviso_hasta_que_llega_su_detalle(base):  # R2 · R-253, C-319
    store, ajustes = base
    _actividad(store, "piscina", LUNES, "lap_swimming", 900, avg_hr=150)
    _actividad(store, "bici", LUNES + timedelta(days=1), "cycling", 600, avg_hr=150)  # never asked
    assert store.estado_detalle("bici") is None
    antes = _semana(store, ajustes)
    assert antes["aproximado"] is True
    assert antes["total"]["zonas"]["z3"] == 1500  # both by laps

    store.guardar_detalle("bici", _detalle(600, 175.0))
    despues = _semana(store, ajustes)
    assert despues["aproximado"] is False
    assert despues["total"]["zonas"]["z3"] == 900 and despues["total"]["zonas"]["z5"] == 600


@pytest.mark.parametrize("tipo", ["running", "open_water_swimming", "walking", "hiking"])
def test_cada_tipo_pedible_sin_pedir_enciende_el_aviso(base, tipo):  # R2 · R-253, C-319
    store, ajustes = base
    _actividad(store, "s", LUNES, tipo, 600, avg_hr=130)
    assert _semana(store, ajustes)["aproximado"] is True


def test_intentado_no_enciende_el_aviso_y_cuenta_por_vueltas(base):  # R3 · R-253
    store, ajustes = base
    _actividad(store, "carrera", LUNES, "running", 900, avg_hr=150)
    store.marcar_detalle_intentado("carrera")
    semana = _semana(store, ajustes)
    assert semana["aproximado"] is False
    assert semana["total"]["zonas"]["z3"] == 900  # by laps, as today


def test_sin_store_no_hay_aviso():  # R-253: no Garmin, weeks at zero, no warning
    assert all(s["aproximado"] is False for s in fitness.semanas(None, None, HOY, 3))
