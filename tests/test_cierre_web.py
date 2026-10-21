"""Close the day (unit 014) through the web routes and at the end of `tridata sync`.

Plans: `cerrar-dia` R-24..R-28 (C-24..C-28, C-79..C-81), G-25 and the states of «Día de comida»;
`sincronizar-dia` (when `tridata sync` ends well, it closes the pending days). Starlette TestClient over a
temporary SQLite; the Claude interpreter is a `MagicMock`; in the CLI wiring `GarminClient` and
`SyncService` are patched with `unittest.mock.patch` and the credentials are invented. Garmin is never
called. Every value is invented. «Today» is Wednesday 2026-10-21.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from tridata import cli
from tridata.garmin_client import GarminAuthError
from tridata.models import Activity, DailyStats
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, Cierres, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 10, 21)  # a Wednesday
MARTES = date(2026, 10, 20)
LUNES = date(2026, 10, 19)
MANANA = HOY + timedelta(days=1)
TEXTO_ESPERA = "Garmin aún no tiene el día completo: sigue con el gasto estimado"


class Web:
    def __init__(self, tmp_path, **extra):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000, **extra},
                             desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.store = DataStore(self.db)
        self.cliente = TestClient(crear_app(self.registro, MagicMock(), self.ajustes, hoy=lambda: HOY,
                                            store=self.store))

    def comer(self, dia, nombre, kcal, *, planeado=False):
        hoy = dia - timedelta(days=1) if planeado else dia
        alimento = Alimento(nombre=nombre, dia=dia, cantidad=1, unidad="ración", kcal=kcal)
        return self.registro.guardar([alimento], hoy=hoy, texto=nombre, sin_cambios=True)[0]

    def garmin(self, dia, total, pasos=10000, actividades=()):
        self.store.save_daily_stats(DailyStats(stat_date=dia, steps=pasos, total_calories=total))
        if actividades:
            self.store.save_activities(list(actividades))

    def dia(self, fecha=None):
        r = self.cliente.get("/api/dia" if fecha is None else f"/api/dia?fecha={fecha.isoformat()}")
        assert r.status_code == 200, r.text
        return r.json()

    def volcado(self):
        with sqlite3.connect(self.db) as conn:
            return list(conn.iterdump())


@pytest.fixture
def w(tmp_path):
    return Web(tmp_path)


def _carrera(dia):
    return Activity(activity_id="c1", activity_date=dia, activity_type="running", name="Carrera",
                    duration_seconds=1800, calories=400, avg_cadence=170)


def _natacion(dia, id_="n1"):
    return Activity(activity_id=id_, activity_date=dia, activity_type="lap_swimming", name="Natación",
                    duration_seconds=2700, calories=300)


# -- R1: «Cerrar el día» --------------------------------------------------------------------------

def test_cerrar_el_dia_de_hoy(w):  # R1 · R-24, C-24
    w.comer(LUNES, "Comida", 2000)
    w.garmin(LUNES, 2500)  # Monday closed with −500 on 2,500
    w.comer(HOY, "Comida", 1400)
    w.comer(HOY, "Cena", 600, planeado=True)
    w.garmin(HOY, None, pasos=4000, actividades=[_natacion(HOY)])
    antes = w.dia()
    assert antes["cierre"]["estado"] == "abierto" and antes["cierre"]["texto"] == "Abierto"
    assert antes["gasto"]["kcal"] == 2300 and antes["total_kcal"] == 1400

    r = w.cliente.post("/api/dia/cerrar", json={})

    assert r.status_code == 200, r.text
    dia = r.json()["dia"]
    assert dia["total_kcal"] == 2000
    assert {a["nombre"]: a["estado"] for a in dia["alimentos"]} == {"Comida": "comido", "Cena": "comido"}
    c = dia["cierre"]
    assert c["estado"] == "comida_cerrada" and c["motivo_cierre"] == "boton" and c["comida_cerrada_en"]
    assert c["texto"] == "Comida cerrada · gasto estimado"
    assert (c["comido_kcal"], c["gasto_estimado_kcal"], c["gasto_real_kcal"]) == (2000, 2300, None)
    assert (c["balance_kcal"], c["balance_pct"]) == (-300, -13.0)
    assert c["balance_texto"] == "balance provisional −300 kcal (−13 %), con el gasto estimado"
    s = c["semana"]
    assert (s["desde"], s["hasta"], s["dias"]) == ("2026-10-19", "2026-10-25", 2)
    assert (s["balance_medio_kcal"], s["balance_pct"]) == (-400, -16.7)
    assert s["texto"] == "media de la semana −400 kcal/día (−16,7 %), 2 días"


def test_tras_cerrar_se_puede_corregir_y_el_balance_se_recalcula(w):  # R1 · R-24
    w.comer(HOY, "Comida", 2000)
    w.cliente.post("/api/dia/cerrar", json={})

    r = w.cliente.post("/api/alimento", json={"nombre": "Yogur", "cantidad": 1, "unidad": "ud", "kcal": 100,
                                              "dia": HOY.isoformat()})

    assert r.status_code == 200
    c = r.json()["dia"]["cierre"]
    assert c["estado"] == "comida_cerrada" and c["comido_kcal"] == 2100 and c["balance_kcal"] == 100


# -- R2: the next day it closes by itself ------------------------------------------------------------

def test_al_abrir_hoy_se_cierra_ayer(w):  # R2 · R-24, C-79
    w.comer(MARTES, "Comida", 1500)
    w.comer(MARTES, "Merienda", 200, planeado=True)

    w.dia()

    martes = w.dia(MARTES)
    assert martes["cierre"]["estado"] == "comida_cerrada"
    assert martes["cierre"]["motivo_cierre"] == "dia_siguiente"
    assert {a["nombre"]: a["estado"] for a in martes["alimentos"]}["Merienda"] == "comido"
    assert w.dia(LUNES)["cierre"]["estado"] == "abierto"  # nothing logged
    volcado = w.volcado()
    w.dia()
    assert w.volcado() == volcado


def test_mirar_un_dia_pasado_no_cierra_nada(w):  # R2 · only today's /api/dia closes
    w.comer(MARTES, "Comida", 1500)
    assert w.dia(MARTES)["cierre"]["estado"] == "abierto"


# -- R3: closed with Garmin (criterio portante) -------------------------------------------------------

def test_dia_cerrado_con_garmin_calcula_con_el_real(tmp_path):  # R3 · R-25, C-25
    w = Web(tmp_path, objetivo_pct=-10)
    w.comer(MARTES, "Comida", 2000)
    w.garmin(MARTES, 2550, pasos=15100, actividades=[_carrera(MARTES)])
    w.dia()  # today: closes Tuesday's food and, with Garmin, its expenditure

    dia = w.dia(MARTES)

    g = dia["gasto"]
    assert g["kcal"] == 2550 and g["origen"] == "garmin"
    assert g["estimado_kcal"] == 2400 and g["real_kcal"] == 2550
    assert dia["objetivo"]["kcal"] == 2295
    assert dia["barra"]["objetivo_kcal"] == 2295
    assert next(m["kcal"] for m in dia["margenes"] if m["nivel"] == -10) == 295
    c = dia["cierre"]
    assert c["estado"] == "cerrado_garmin" and c["cerrado_garmin_en"]
    assert c["texto"] == "Cerrado con Garmin: gasto real 2.550 (estimado 2.400)"
    assert (c["balance_kcal"], c["balance_pct"]) == (-550, -21.6)

    r = w.cliente.post("/api/alimento", json={"nombre": "Olvido", "cantidad": 1, "unidad": "ud", "kcal": 150,
                                              "dia": MARTES.isoformat()})

    c = r.json()["dia"]["cierre"]
    assert c["estado"] == "cerrado_garmin" and c["balance_kcal"] == -400


def test_un_dia_abierto_lleva_el_estimado(w):  # R3 · the fields agreed with unit 015
    dia = w.dia()
    g = dia["gasto"]
    assert g["origen"] == "estimado" and g["estimado_kcal"] == g["kcal"] == 2000 and g["real_kcal"] is None
    claves = {"estado", "texto", "espera_motivo", "comido_kcal", "gasto_estimado_kcal", "gasto_real_kcal",
              "balance_kcal", "balance_pct", "comida_cerrada_en", "motivo_cierre", "cerrado_garmin_en", "rastro",
              "semana"}
    assert claves <= set(dia["cierre"])
    assert {"desde", "hasta", "dias", "balance_medio_kcal", "balance_pct"} <= set(dia["cierre"]["semana"])
    assert dia["avisos_cierre"] == []


# -- R4: Garmin does not have the whole day yet ------------------------------------------------------

def test_dia_que_espera_a_garmin(w):  # R4 · R-26, C-26
    w.comer(MARTES, "Comida", 2000)
    w.garmin(MARTES, 1450)
    w.dia()

    martes = w.dia(MARTES)

    c = martes["cierre"]
    assert c["estado"] == "comida_cerrada" and c["espera_motivo"]
    assert c["texto"] == TEXTO_ESPERA
    assert martes["gasto"]["origen"] == "estimado" and martes["gasto"]["real_kcal"] is None

    w.garmin(MARTES, 2550)
    w.dia()

    assert w.dia(MARTES)["cierre"]["estado"] == "cerrado_garmin"


# -- R5: corrections, the trail and the warning ---------------------------------------------------------

def test_aviso_de_correccion_hasta_entendido(w):  # R5 · R-27, C-27, G-25
    catorce = date(2026, 10, 14)
    w.comer(catorce, "Comida", 2000)
    w.garmin(catorce, 2500)
    w.dia()
    w.garmin(catorce, 2800)

    hoy = w.dia()

    assert len(hoy["avisos_cierre"]) == 1
    aviso = hoy["avisos_cierre"][0]
    assert aviso["texto"] == "Garmin ha corregido el gasto del 2026-10-14: 2.500 → 2.800 kcal; balance −500 → −800"
    assert {"id", "dia", "gasto_antes", "gasto_despues", "balance_antes", "balance_despues"} <= set(aviso)
    rastro = w.dia(catorce)["cierre"]["rastro"]
    assert [(r["gasto_antes"], r["gasto_despues"], r["relevante"]) for r in rastro] == [(2500, 2800, True)]
    assert rastro[0]["fecha"]

    r = w.cliente.post(f"/api/aviso-cierre/{aviso['id']}/visto")

    assert r.status_code == 200
    assert r.json()["avisos_cierre"] == []
    assert w.dia()["avisos_cierre"] == []
    assert len(w.dia(catorce)["cierre"]["rastro"]) == 1


def test_aviso_que_no_existe_es_404_y_no_cambia_nada(w):  # R5
    w.dia()
    volcado = w.volcado()
    assert w.cliente.post("/api/aviso-cierre/99/visto").status_code == 404
    assert w.volcado() == volcado


# -- R6, R7: the calibration in the breakdown ---------------------------------------------------------

def _tres_dias(w, real):
    for i, dia in enumerate((date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18))):
        w.comer(dia, "Comida", 2000)
        w.garmin(dia, real, actividades=[_natacion(dia, f"n{i}")])


def test_desglose_dice_la_calibracion(w):  # R6 · R-28, C-28
    _tres_dias(w, 2450)

    g = w.dia()["gasto"]

    assert g["kcal"] == 2150 and g["kcal_por_paso"] == 0.055
    assert "calibrado con Garmin el 2026-10-21" in g["calibracion"]["texto"]
    assert w.dia(date(2026, 10, 16))["gasto"]["estimado_kcal"] == 2300
    assert w.cliente.get("/api/ajustes").json()["vigentes"]["kcal_por_paso"] == {"valor": 0.055,
                                                                                 "desde": "2026-10-21"}


def test_desglose_dice_la_calibracion_descartada(w):  # R7 · R-28, C-80
    _tres_dias(w, 4900)

    g = w.dia()["gasto"]

    assert g["kcal"] == 2000 and g["kcal_por_paso"] == 0.04
    assert "descartada" in g["calibracion"]["texto"] and "fuera de rango" in g["calibracion"]["texto"]


# -- R8: the routes are protected ---------------------------------------------------------------------

@pytest.mark.parametrize(("cuerpo", "estado"), [
    ({"fecha": MANANA.isoformat()}, 422),  # a future day has no close
    ({"fecha": LUNES.isoformat()}, 422),  # a past day with nothing logged
    ({}, 422),  # today with nothing logged
    ({"fecha": "ayer"}, 400),
])
def test_cerrar_rechaza_y_no_toca_nada(w, cuerpo, estado):  # R8 · R-24
    w.comer(MANANA, "Plan", 500)
    w.dia()
    volcado = w.volcado()

    r = w.cliente.post("/api/dia/cerrar", json=cuerpo)

    assert r.status_code == estado and r.json()["error"]
    assert w.volcado() == volcado


def test_cerrar_un_dia_pasado_abierto_con_el_boton(w):  # R8 · R-24
    w.comer(MARTES, "Comida", 1500)
    r = w.cliente.post("/api/dia/cerrar", json={"fecha": MARTES.isoformat()})
    assert r.status_code == 200
    c = r.json()["dia"]["cierre"]
    assert r.json()["dia"]["fecha"] == MARTES.isoformat()
    assert c["estado"] == "comida_cerrada" and c["motivo_cierre"] == "boton"


def test_cerrar_dos_veces_no_cambia_nada(w):  # R8 · R-24
    w.comer(HOY, "Comida", 1500)
    w.cliente.post("/api/dia/cerrar", json={})
    volcado = w.volcado()
    r = w.cliente.post("/api/dia/cerrar", json={})
    assert r.status_code == 200 and r.json()["dia"]["cierre"]["estado"] == "comida_cerrada"
    assert w.volcado() == volcado


def test_crear_app_acepta_sus_cierres(tmp_path):  # R8
    db = tmp_path / "data.sqlite3"
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
    cierres = Cierres(db, ajustes=ajustes, pesos=RegistroPeso(db), objetivos=ObjetivosDia(db))
    cliente = TestClient(crear_app(RegistroComidas(db), MagicMock(), ajustes, hoy=lambda: HOY, cierres=cierres))
    assert cliente.get("/api/dia").json()["cierre"]["estado"] == "abierto"


# -- `tridata sync` closes the pending days when it ends well (Q-4) ----------------------------------

@pytest.fixture
def sync(tmp_path, monkeypatch):
    """`cli.main(["sync"])` over a temporary SQLite, Garmin patched, invented credentials."""
    db = tmp_path / "data.sqlite3"
    Ajustes(db).cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                        desde=DESDE_SIEMPRE)
    store = DataStore(db)
    monkeypatch.setenv("GARMIN_EMAIL", "persona@example.invalid")
    monkeypatch.setenv("GARMIN_PASSWORD", "inventada")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(cli, "DataStore", lambda *a, **k: store)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    ayer = date.today() - timedelta(days=1)  # the CLI closes with the real clock
    registro = RegistroComidas(db)
    registro.guardar([Alimento(nombre="Comida", dia=ayer, cantidad=1, unidad="ración", kcal=2000)], hoy=ayer,
                     texto="comida", sin_cambios=True)
    store.save_daily_stats(DailyStats(stat_date=ayer, steps=10000, total_calories=2550))
    return db, ayer


def _estado(db, dia):
    with sqlite3.connect(db) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'cierre_dia'").fetchone():
            return None  # the close never ran: no table, no row
        fila = conn.execute("SELECT estado FROM cierre_dia WHERE dia = ?", (dia.isoformat(),)).fetchone()
    return fila[0] if fila else None


def test_sync_bien_cierra_los_dias(sync, capsys):  # R2, R3 · Q-4, sincronizar-dia
    db, ayer = sync
    with patch("tridata.cli.GarminClient"), patch("tridata.cli.SyncService") as servicio:
        assert cli.main(["sync"]) == 0
    servicio.return_value.sync.assert_called_once()
    assert _estado(db, ayer) == "cerrado_garmin"
    salida = capsys.readouterr().out
    assert "Cierre" in salida and "2550" not in salida and "2.550" not in salida  # no health values in sync.log


def test_sync_que_falla_no_cierra(sync):  # R2 · Q-4
    db, ayer = sync
    with patch("tridata.cli.GarminClient"), patch("tridata.cli.SyncService") as servicio:
        servicio.return_value.sync.side_effect = GarminAuthError("sesión caducada (inventado)")
        assert cli.main(["sync"]) == 1
    assert _estado(db, ayer) is None


def test_si_el_cierre_falla_el_sync_sale_igual(sync, capsys):  # R2 · Q-4
    with patch("tridata.cli.GarminClient"), patch("tridata.cli.SyncService"), \
            patch("tridata.nutricion.Cierres") as cierres:
        cierres.return_value.al_dia.side_effect = RuntimeError("roto a propósito")
        assert cli.main(["sync"]) == 0
    assert "roto a propósito" in capsys.readouterr().err


@pytest.mark.parametrize("opcion", ["--laps", "--weather"])
def test_laps_y_weather_no_cierran(sync, opcion):  # R2 · Q-4
    db, ayer = sync
    with patch("tridata.cli.GarminClient"), patch("tridata.cli.SyncService"):
        assert cli.main(["sync", opcion]) == 0
    assert _estado(db, ayer) is None
