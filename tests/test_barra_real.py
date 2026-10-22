"""Unit 017 (asked by Andrés on 2026-10-22): the bar of today in two layers, planned and done now.

Plans: `consultar-margen` R-13, R-15, R-23 (with the text delta of this unit), R-25, R-26; `cerrar-dia`
R-25. `gasto_hecho` is pure over the breakdown of `estimar_gasto`; `barra.real` goes through `/api/dia`
with a Starlette TestClient over a temporary SQLite and `DataStore`. The Claude interpreter is a
`MagicMock`; Garmin is never called. Every value is invented. «Today» is Wednesday 2026-10-21.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.models import Activity, DailyStats
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, Cierres, ConfigGasto, RegistroComidas, estimar_gasto
from tridata.nutricion.gasto import gasto_hecho
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 10, 21)
AYER = HOY - timedelta(days=1)
JUEVES = HOY + timedelta(days=1)
CONFIG = ConfigGasto(basal_kcal=1600, kcal_por_paso=0.04, pasos=10000)
NATACION = {"actividad_id": "n0", "nombre": "Natación", "minutos": 45, "kcal": 400}


def _carrera(dia, id_="c1"):
    return {"activity_id": id_, "activity_date": dia.isoformat(), "activity_type": "running", "name": "Carrera",
            "duration_seconds": 1800, "calories": 400, "avg_cadence": 170}


# -- gasto_hecho: pure, from the breakdown -------------------------------------------------------

@pytest.mark.parametrize(("stats", "actividades", "previsto", "previsto_kcal", "hecho", "aviso"), [
    ({"steps": 6000}, [], NATACION, 2400, 1840, None),                       # R10
    ({"steps": 14000}, [], NATACION, 2560, 2160, None),                      # R11
    ({"steps": 3000}, [_carrera(HOY)], None, 2400, 2000, "incompletos"),     # R11: 5,100 running steps > 3,000
    (None, [], None, 2000, 1600, "sin datos de Garmin"),                    # R11
])
def test_lo_hecho_sale_del_desglose(stats, actividades, previsto, previsto_kcal, hecho, aviso):  # R10, R11 · R-13
    estimado = estimar_gasto(CONFIG, stats, actividades, previsto)
    assert estimado["kcal"] == previsto_kcal
    h = gasto_hecho(estimado)
    assert h["kcal"] == hecho
    assert h["kcal"] <= estimado["kcal"]  # by construction, never past the planned
    assert h["previsto_kcal"] == (400 if previsto else 0)
    assert h["pasos_del_dia"] == 10000
    if aviso is None:
        assert h["aviso"] is None
    else:
        assert aviso in h["aviso"]


def test_lo_hecho_campos():  # R10
    h = gasto_hecho(estimar_gasto(CONFIG, {"steps": 6000}, [], NATACION))
    assert h == {"kcal": 1840, "pasos_hechos": 6000, "pasos_del_dia": 10000, "entreno_hecho_kcal": 0,
                 "previsto_kcal": 400, "previsto_nombre": "Natación", "aviso": None}


def test_lo_hecho_pasos_incompletos_lo_dice():  # R11 · R-26
    h = gasto_hecho(estimar_gasto(CONFIG, {"steps": 3000}, [_carrera(HOY)]))
    assert h["aviso"] == "los pasos de Garmin de hoy están incompletos: lo hecho no cuenta pasos"
    assert h["pasos_hechos"] is None and h["entreno_hecho_kcal"] == 400


def test_lo_hecho_sin_garmin_lo_dice():  # R11
    h = gasto_hecho(estimar_gasto(CONFIG, None, []))
    assert h["aviso"] == "sin datos de Garmin de hoy: lo hecho es solo el basal"


# -- barra.real in /api/dia -------------------------------------------------------------------------

class Web:
    def __init__(self, tmp_path):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000,
                              "objetivo_pct": -10, "limite_saludable_pct": 25}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.store = DataStore(self.db)
        self.cierres = Cierres(self.db, ajustes=self.ajustes, store=self.store, registro=self.registro)
        self.store.save_activities([Activity(activity_id="n0", activity_date=HOY - timedelta(days=3),
                                             activity_type="lap_swimming", name="Natación",
                                             duration_seconds=2700, calories=400)])
        self.cliente = TestClient(crear_app(self.registro, MagicMock(), self.ajustes, hoy=lambda: HOY,
                                            store=self.store, cierres=self.cierres))

    def comer(self, dia, kcal):
        self.registro.guardar([Alimento(nombre="Comida", dia=dia, cantidad=1, unidad="ración", kcal=kcal)],
                              hoy=dia, texto="Comida", sin_cambios=True)

    def dia(self, fecha=None):
        r = self.cliente.get("/api/dia" if fecha is None else f"/api/dia?fecha={fecha.isoformat()}")
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def w(tmp_path):
    web = Web(tmp_path)
    web.store.save_daily_stats(DailyStats(stat_date=HOY, steps=6000, total_calories=None))
    r = web.cliente.post("/api/dia/objetivo", json={"previsto": {"actividad_id": "n0", "minutos": 45}})
    assert r.status_code == 200, r.text
    web.comer(HOY, 1000)
    return web


def test_barra_real_de_hoy(w):  # R10 · R-23
    dia = w.dia()
    assert dia["gasto"]["kcal"] == 2400 and dia["gasto"]["hecho_kcal"] == 1840
    b = dia["barra"]
    assert (b["objetivo_kcal"], b["quedan_kcal"]) == (2160, 1160)  # the big number, as always
    r = b["real"]
    assert (r["objetivo_kcal"], r["quedan_kcal"], r["comido_kcal"], r["pct"]) == (1656, 656, 1000, 60)
    assert r["hecho"]["pasos_hechos"] == 6000 and r["hecho"]["previsto_kcal"] == 400
    assert r["texto"] == "quedan 656 kcal"


def test_la_capa_nueva_no_cambia_lo_de_antes(w):  # R10 · R-13, R-15
    dia = w.dia()
    assert [(m["nivel"], m["kcal"]) for m in dia["margenes"]] == [(-10, 1160), (0, 1400), (10, 1640)]
    assert {k: v for k, v in dia["barra"].items() if k != "real"} == {
        "objetivo_kcal": 2160, "comido_kcal": 1000, "pct": 46, "quedan_kcal": 1160, "pasado_kcal": 0,
        "texto": "quedan 1.160 kcal", "limite": dia["barra"]["limite"]}
    assert dia["con_planeado"]["barra"]["objetivo_kcal"] == 2160  # «Con lo planeado…» still with the planned


def test_cuando_garmin_trae_la_natacion(w):  # R10
    w.store.save_activities([Activity(activity_id="n1", activity_date=HOY, activity_type="lap_swimming",
                                      name="Natación", duration_seconds=2700, calories=400)])
    r = w.cliente.post("/api/dia/objetivo", json={"previsto": None})  # «Ya hecho»
    assert r.status_code == 200, r.text
    dia = w.dia()
    assert dia["gasto"]["kcal"] == 2400 and dia["gasto"]["hecho_kcal"] == 2240
    assert (dia["barra"]["real"]["objetivo_kcal"], dia["barra"]["real"]["quedan_kcal"]) == (2016, 1016)


def test_con_14000_pasos_y_pasado(tmp_path):  # R11
    w = Web(tmp_path)
    w.store.save_daily_stats(DailyStats(stat_date=HOY, steps=14000, total_calories=None))
    w.cliente.post("/api/dia/objetivo", json={"previsto": {"actividad_id": "n0", "minutos": 45}})
    w.comer(HOY, 2100)
    dia = w.dia()
    assert (dia["gasto"]["kcal"], dia["gasto"]["hecho_kcal"]) == (2560, 2160)
    assert dia["barra"]["quedan_kcal"] == 204
    assert dia["barra"]["real"]["pasado_kcal"] == 156 and dia["barra"]["real"]["texto"] == "te has pasado 156 kcal"


def test_pasos_incompletos_y_sin_garmin(tmp_path):  # R11 · R-26
    w = Web(tmp_path)
    w.store.save_daily_stats(DailyStats(stat_date=HOY, steps=3000, total_calories=None))
    w.store.save_activities([Activity(activity_id="c1", activity_date=HOY, activity_type="running", name="Carrera",
                                      duration_seconds=1800, calories=400, avg_cadence=170)])
    dia = w.dia()
    assert dia["gasto"]["hecho_kcal"] == 2000
    assert "incompletos" in dia["barra"]["real"]["hecho"]["aviso"]
    sin = Web(tmp_path / "otra").dia()
    assert sin["gasto"]["hecho_kcal"] == 1600 and "sin datos de Garmin" in sin["barra"]["real"]["hecho"]["aviso"]


def test_ayer_cerrado_con_garmin_y_el_jueves_una_capa(w):  # R11
    w.comer(AYER, 2000)
    w.store.save_daily_stats(DailyStats(stat_date=AYER, steps=10000, total_calories=2550))
    w.dia()  # today's screen brings the days up to date (014): yesterday closes with Garmin
    ayer = w.dia(AYER)
    assert ayer["gasto"]["origen"] == "garmin" and ayer["gasto"]["kcal"] == 2550
    assert ayer["barra"]["real"] is None and ayer["gasto"]["hecho_kcal"] is None
    jueves = w.dia(JUEVES)
    assert jueves["barra"]["real"] is None and jueves["gasto"]["hecho_kcal"] is None


# -- R13: the texts the server gives ready, in whole kcal with a thousands point ---------------------

def test_barra_texto_en_kcal_enteras():  # R13
    from tridata.nutricion import barra

    assert barra(1800, 0)["texto"] == "quedan 1.800 kcal"
    assert barra(1800.4, 0)["texto"] == "quedan 1.800 kcal" and barra(1800.4, 0)["quedan_kcal"] == 1800.4
    assert barra(1800, 2950.6)["texto"] == "te has pasado 1.151 kcal"


def test_textos_del_cierre_en_kcal_enteras(tmp_path):  # R13
    db = tmp_path / "data.sqlite3"
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1600.6, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000}, desde=DESDE_SIEMPRE)
    registro = RegistroComidas(db)
    store = DataStore(db)
    cierres = Cierres(db, ajustes=ajustes, store=store, registro=registro)
    abierto = cierres.bloque_del_dia(HOY, 2010.2, 3800)
    assert abierto["balance_texto"] == "balance hasta ahora −1.790 kcal (−47,1 %), con el gasto estimado"
    assert abierto["balance_kcal"] == -1789.8  # the number stays exact
    registro.guardar([Alimento(nombre="Comida", dia=AYER, cantidad=1, unidad="ración", kcal=760.3)], hoy=AYER,
                     texto="Comida", sin_cambios=True)
    store.save_daily_stats(DailyStats(stat_date=AYER, steps=10000, total_calories=2550))
    cierres.al_dia(HOY)
    bloque = cierres.bloque_del_dia(AYER, 760.3, 2000.6)
    assert bloque["texto"] == "Cerrado con Garmin: gasto real 2.550 (estimado 2.001)"
    assert bloque["balance_texto"] == "balance −1.790 kcal (−70,2 %), con el gasto real de Garmin"
    assert bloque["semana"]["texto"] == "media de la semana −1.790 kcal/día (−70,2 %), 1 día"
