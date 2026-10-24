"""Unit 034 (asked by Andrés on 2026-10-23): the planned training, the objective and the levels of a day that has
not come yet.

Plans: `consultar-margen` R-23 (and the scenario this unit adds). `POST /api/dia/objetivo` with an optional
`"fecha"` (today or a future day) goes through a Starlette TestClient over a temporary SQLite and `DataStore`. The
Claude interpreter is a `MagicMock`; Garmin is never called. Every value is invented. «Today» is Wednesday
2026-10-21 and the server's «today» can be moved (R3: the day comes).
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.models import Activity
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Cierres, ObjetivosDia, RegistroComidas
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 10, 21)
AYER = HOY - timedelta(days=1)
MANANA = HOY + timedelta(days=1)
NATACION_45 = {"actividad_id": "n0", "minutos": 45}  # 400 kcal in 45 min, three days ago
ERROR_FECHA = "No se ha cambiado nada: solo se puede ajustar hoy o un día que aún no ha llegado."


class Web:
    def __init__(self, tmp_path):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000,
                              "objetivo_pct": -10, "limite_saludable_pct": 25}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.store = DataStore(self.db)
        self.cierres = Cierres(self.db, ajustes=self.ajustes, store=self.store, registro=self.registro,
                               objetivos=self.objetivos)
        self.store.save_activities([Activity(activity_id="n0", activity_date=HOY - timedelta(days=3),
                                             activity_type="lap_swimming", name="Natación",
                                             duration_seconds=2700, calories=400)])
        self.hoy = HOY
        self.cliente = TestClient(crear_app(self.registro, MagicMock(), self.ajustes, hoy=lambda: self.hoy,
                                            objetivos=self.objetivos, store=self.store, cierres=self.cierres))

    def dia(self, fecha: date | None = None) -> dict:
        r = self.cliente.get("/api/dia" if fecha is None else f"/api/dia?fecha={fecha.isoformat()}")
        assert r.status_code == 200, r.text
        return r.json()

    def objetivo(self, cuerpo: dict, fecha: date | str | None = None):
        if fecha is not None:
            cuerpo = {**cuerpo, "fecha": fecha.isoformat() if isinstance(fecha, date) else fecha}
        return self.cliente.post("/api/dia/objetivo", json=cuerpo)


@pytest.fixture
def w(tmp_path):
    return Web(tmp_path)


def _previsto(dia: dict):
    return (dia["gasto"].get("desglose") or {}).get("previsto")


def test_el_entreno_previsto_va_a_manana_y_hoy_no_cambia(w):  # R1 · R-23 (criterio portante)
    hoy_antes, manana_antes = w.dia(), w.dia(MANANA)
    r = w.objetivo({"previsto": NATACION_45}, MANANA)
    assert r.status_code == 200, r.text
    assert r.json()["dia"]["fecha"] == MANANA.isoformat()  # the answer is that day, recomputed
    manana = w.dia(MANANA)
    assert manana["gasto"]["kcal"] == manana_antes["gasto"]["kcal"] + 400
    assert _previsto(manana)["minutos"] == 45 and _previsto(manana)["kcal"] == 400
    hoy = w.dia()
    assert hoy["gasto"]["kcal"] == hoy_antes["gasto"]["kcal"]
    assert _previsto(hoy) is None
    assert w.objetivos.del_dia(HOY) is None  # nothing was written in today


def test_el_objetivo_y_los_niveles_van_a_manana(w):  # R2 · R-23
    assert w.objetivo({"porcentaje": 0}, MANANA).status_code == 200
    r = w.objetivo({"niveles": [-20, 0]}, MANANA)
    assert r.status_code == 200, r.text
    manana = w.dia(MANANA)
    assert (manana["objetivo"]["porcentaje"], manana["objetivo"]["origen"]) == (0, "dia")
    assert manana["barra"]["objetivo_kcal"] == manana["gasto"]["kcal"]  # the bar goes against maintenance
    assert [m["nivel"] for m in manana["margenes"]] == [-20, 0]
    hoy = w.dia()
    assert (hoy["objetivo"]["porcentaje"], hoy["objetivo"]["origen"]) == (-10, "plan")
    assert [m["nivel"] for m in hoy["margenes"]] != [-20, 0]
    assert hoy["barra"]["objetivo_kcal"] == round(hoy["gasto"]["kcal"] * 0.9)


def test_hoy_explicito_es_como_sin_fecha(w):  # R2: today keeps working, with or without the date
    r = w.objetivo({"porcentaje": 0}, HOY)
    assert r.status_code == 200, r.text
    assert r.json()["dia"]["fecha"] == HOY.isoformat()
    assert w.dia()["objetivo"]["origen"] == "dia"


def test_cuando_llega_el_dia_sigue_puesto_y_se_quita(w):  # R3
    w.objetivo({"previsto": NATACION_45}, MANANA)
    w.objetivo({"porcentaje": 0}, MANANA)
    puesto = w.dia(MANANA)
    w.hoy = MANANA  # the day comes
    dia = w.dia()
    assert dia["fecha"] == MANANA.isoformat()
    assert _previsto(dia) == _previsto(puesto)
    assert dia["gasto"]["kcal"] == puesto["gasto"]["kcal"]
    assert (dia["objetivo"]["porcentaje"], dia["objetivo"]["origen"]) == (0, "dia")
    r = w.objetivo({"previsto": None})  # «Ya hecho», that same day, as always
    assert r.status_code == 200, r.text
    assert _previsto(w.dia()) is None


@pytest.mark.parametrize("fecha", [AYER, "2026-13-40", "mañana", 20261022, ""])
def test_un_dia_pasado_o_una_fecha_mala_no_cambia_nada(w, fecha):  # R4
    hoy_antes, ayer_antes = w.dia(), w.dia(AYER)
    r = w.objetivo({"previsto": NATACION_45, "porcentaje": 0}, fecha)
    assert r.status_code == 422, r.text
    assert r.json()["error"] == ERROR_FECHA
    assert w.objetivos.del_dia(HOY) is None and w.objetivos.del_dia(AYER) is None
    assert w.dia() == hoy_antes and w.dia(AYER) == ayer_antes
