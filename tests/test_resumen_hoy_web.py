"""Unit 030: GET /api/hoy (the four cards in one answer) and «Actualizar Garmin» from Hoy, with the routine's lock.

Plans `ver-dia-movil` REC-18: R-97, R-99..R-103 · C-138, C-140..C-144 · G-48. R1, R3, R4, R5, R7, R8 and R9 of
the unit. `actualizar_garmin` is a `MagicMock`: Garmin is never called. Every value is invented.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.garmin_client import GarminAuthError
from tridata.models import Activity, DailyStats, HRVRecord, SleepRecord
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, ObjetivosDia, RegistroComidas
from tridata.storage import DataStore
from tridata.sync import MOTIVOS, cerrojo_sync
from tridata.web import crear_app
from tridata.web.app import ESTATICOS

HOY = date(2026, 10, 21)
EN_MARCHA = "Ya estoy actualizando Garmin: espera a que acabe."


class Entorno:
    def __init__(self, tmp_path, actualizar=None) -> None:
        self.carpeta = tmp_path
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                             desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.store = DataStore(self.db)
        self.reloj = {"ahora": datetime(2026, 10, 21, 10, 0)}
        self.actualizar = actualizar if actualizar is not None else MagicMock()
        self.cliente = TestClient(crear_app(self.registro, MagicMock(), self.ajustes, hoy=lambda: HOY,
                                            objetivos=self.objetivos, store=self.store,
                                            actualizar_garmin=self.actualizar, ahora=lambda: self.reloj["ahora"]))

    def dia_c138(self) -> None:
        """C-138: 1.240 kcal eaten, level marked −15 %, a run of 45 min synced, last good sync at 9:05."""
        self.registro.guardar([Alimento(nombre="Comida de prueba", dia=HOY, cantidad=1, unidad="ración", kcal=1240)],
                              hoy=HOY, texto="comida", sin_cambios=True)
        self.objetivos.cambiar(HOY, porcentaje=-15)
        self.store.save_activities([Activity(activity_id="20261021001", activity_date=HOY, activity_type="running",
                                             name="Carrera suave", duration_seconds=2700, avg_hr=142, calories=520)])
        self.store.save_daily_stats(DailyStats(stat_date=HOY, steps=11000, resting_hr=48, total_calories=2700))
        self.store.save_sleep(SleepRecord(sleep_date=HOY, total_sleep_seconds=25920, sleep_score=82))
        for i in range(7):
            self.store.save_hrv(HRVRecord(hrv_date=HOY - timedelta(days=i), last_night_avg=60 + i % 3))
        self.store.marcar_sync_ok(datetime(2026, 10, 21, 9, 5))


@pytest.fixture
def entorno(tmp_path) -> Entorno:
    return Entorno(tmp_path)


# -- R1, R3, R9 · C-138, C-140: the four cards in one answer --------------------------------------------------

def test_api_hoy_las_cuatro_tarjetas(entorno):  # R1, R3, R9 · C-138, C-140
    entorno.dia_c138()
    hoy = entorno.cliente.get("/api/hoy")
    assert hoy.status_code == 200
    h = hoy.json()
    assert set(h) >= {"fecha", "comida", "entreno", "como_llegas", "garmin_sync", "asistente"}
    assert h["fecha"] == HOY.isoformat()

    dia = entorno.cliente.get("/api/dia").json()  # the same figures as Nutrición › Día
    (marcado,) = [m for m in dia["margenes"] if m.get("objetivo")]
    assert h["comida"]["total_kcal"] == dia["total_kcal"] == 1240
    assert h["comida"]["nivel"] == marcado["nivel"] == -15
    assert h["comida"]["queda_kcal"] == marcado["kcal"]
    assert h["comida"]["objetivo_kcal"] == dia["barra"]["objetivo_kcal"]

    assert h["entreno"] == [{"actividad_id": "20261021001", "nombre": "Carrera suave", "deporte": "Carrera", "minutos": 45,
                             "kcal": 520}]
    c = h["como_llegas"]
    assert c["sueno"]["segundos"] == 25920 and c["sueno"]["puntuacion"] == 82
    assert c["pulso_reposo"] == {"estado": "ok", "valor": 48}
    assert c["hrv"]["estado"] == "ok" and c["hrv"]["media_7d"] is not None
    assert c["frescura"]["estado"] == "ok"

    assert h["garmin_sync"]["ok_en"] == "2026-10-21T09:05:00"
    assert h["garmin_sync"]["texto_hora"] == "Garmin: sincronizado a las 9:05"
    assert h["asistente"] is None  # R9: the last answer arrives with unit 032


def test_api_hoy_sin_nada(entorno):  # R1, R3, R6, R9 · G-47
    h = entorno.cliente.get("/api/hoy").json()
    assert h["entreno"] == []
    assert h["comida"]["total_kcal"] == 0  # nothing eaten is a real 0 (of food), not a Garmin datum
    assert all(v["estado"] == "no_ha_llegado" for v in h["como_llegas"].values())
    assert h["garmin_sync"]["texto_hora"] == "Garmin: aún no se ha sincronizado"
    assert h["asistente"] is None


def test_api_hoy_sin_ajustes_es_409(tmp_path):  # R1: without basal the day cannot be computed, as /api/dia
    db = tmp_path / "data.sqlite3"
    cliente = TestClient(crear_app(RegistroComidas(db), MagicMock(), Ajustes(db), hoy=lambda: HOY,
                                   store=DataStore(db)))
    assert cliente.get("/api/hoy").status_code == 409


# -- R4 · C-141: the button from Hoy brings the activity and the new hour ------------------------------------

def test_boton_bien_refresca_tarjetas_y_hora(entorno):  # R4 · C-141
    entorno.dia_c138()

    def traer(dia):  # Garmin's ids grow with time: the evening ride comes after the morning run
        entorno.store.save_activities([Activity(activity_id="20261021002", activity_date=dia, activity_type="road_biking",
                                                name="Bici", duration_seconds=3600, calories=700)])

    entorno.actualizar.side_effect = traer
    entorno.reloj["ahora"] = datetime(2026, 10, 21, 18, 42)
    r = entorno.cliente.post("/api/garmin/actualizar")
    assert r.status_code == 200
    entorno.actualizar.assert_called_once_with(HOY)
    h = entorno.cliente.get("/api/hoy").json()
    assert [a["actividad_id"] for a in h["entreno"]] == ["20261021001", "20261021002"]
    assert h["garmin_sync"]["texto_hora"] == "Garmin: sincronizado a las 18:42"


# -- R5 · C-142: a failure says why with the routine's words; data and hour stay ------------------------------

def test_boton_que_falla_deja_datos_y_hora(entorno):  # R5 · C-142
    entorno.dia_c138()
    entorno.actualizar.side_effect = GarminAuthError("token caducado")
    antes = entorno.cliente.get("/api/hoy").json()
    r = entorno.cliente.post("/api/garmin/actualizar")
    assert r.status_code == 502 and r.json()["motivo"] == "sesion"
    assert entorno.cliente.get("/api/hoy").json() == antes


def test_api_hoy_lleva_las_palabras_de_la_rutina(entorno):  # R5 · R-101: the same words as the routine's warning
    motivos = entorno.cliente.get("/api/hoy").json()["garmin_sync"]["motivos"]
    assert motivos == {"sesion": MOTIVOS["sesion"], "limite": MOTIVOS["limite"], "red": MOTIVOS["conexion"]}


# -- R7 · C-144, G-48: the routine is syncing ------------------------------------------------------------------

def test_boton_con_la_rutina_en_marcha_es_409(entorno):  # R7 · C-144
    entorno.dia_c138()
    antes = entorno.cliente.get("/api/hoy").json()
    with cerrojo_sync(entorno.carpeta):  # what `tridata sync` holds while it runs
        r = entorno.cliente.post("/api/garmin/actualizar")
    assert r.status_code == 409
    assert r.json() == {"error": EN_MARCHA, "motivo": "en_marcha"}
    entorno.actualizar.assert_not_called()
    assert entorno.cliente.get("/api/hoy").json() == antes
    assert entorno.cliente.post("/api/garmin/actualizar").status_code == 200  # free again once the routine ends


def test_el_boton_coge_el_cerrojo_de_la_rutina(entorno):  # R7 · G-48: the routine cannot start meanwhile
    from tridata.sync import SyncEnMarcha

    def mientras(dia):
        with pytest.raises(SyncEnMarcha), cerrojo_sync(entorno.carpeta):
            pass

    entorno.actualizar.side_effect = mientras
    assert entorno.cliente.post("/api/garmin/actualizar").status_code == 200
    entorno.actualizar.assert_called_once()


# -- R1, R8: the page ------------------------------------------------------------------------------------------

def test_la_pagina_tiene_las_tarjetas_y_hoy_js(entorno):  # R1 · R8
    html = entorno.cliente.get("/").text
    for ident in ("hoy-comida", "hoy-entreno", "hoy-como-llegas", "hoy-asistente", "hoy-actualizar"):
        assert f'id="{ident}"' in html, ident
    assert "Muy pronto, aquí: el resumen de tu día." not in html
    assert entorno.cliente.get("/static/hoy.js").status_code == 200
    assert 'from "./hoy.js"' in (ESTATICOS / "app.js").read_text(encoding="utf-8")


def test_el_cascaron_lleva_hoy_js_con_otra_version():  # R1 (sw.js)
    sw = (ESTATICOS / "sw.js").read_text(encoding="utf-8")
    assert '"/static/hoy.js"' in re.search(r"const CASCARON = \[(.*?)\];", sw, re.S).group(1)
    assert re.search(r'const VERSION = "([^"]+)"', sw).group(1) not in {f"tridata-cascaron-v{n}" for n in range(1, 8)}


# -- unit 060 · R2: Hoy and Peso say where the basal comes from (Garmin's BMR of the last complete day) -------------

def test_hoy_y_peso_dicen_basal_de_tu_garmin(entorno):  # R2
    ayer = HOY - timedelta(days=1)
    entorno.store.save_daily_stats(DailyStats(stat_date=ayer, steps=9000, total_calories=2600, bmr_calories=1850))
    entorno.store.save_daily_stats(DailyStats(stat_date=HOY, steps=4000, total_calories=900, bmr_calories=1990))
    entorno.store.marcar_sync_ok(datetime(2026, 10, 21, 9, 5))
    dia = entorno.cliente.get("/api/dia").json()
    assert dia["peso"]["basal"]["origen"] == "garmin" and dia["peso"]["basal"]["kcal"] == 1850
    assert dia["peso"]["basal"]["dia_garmin"] == ayer.isoformat()
    assert dia["gasto"]["desglose"]["basal_kcal"] == 1850  # the basal seen and the one added are the same
    assert "Garmin" in dia["gasto"]["explicacion"]
    app = (ESTATICOS / "app.js").read_text(encoding="utf-8")
    peso = (ESTATICOS / "peso.js").read_text(encoding="utf-8")
    assert "Basal (de tu Garmin)" in app and "Basal (con tu peso)" in app  # the reserve keeps its text
    assert "de tu Garmin" in peso and "con tu peso" in peso


def test_sin_bmr_hoy_dice_el_basal_de_siempre(entorno):  # R2, R6
    dia = entorno.cliente.get("/api/dia").json()
    assert dia["peso"]["basal"]["origen"] == "ajustes" and dia["peso"]["basal"]["kcal"] == 1600
