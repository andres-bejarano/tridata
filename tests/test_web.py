"""Web app and `tridata web` (unit 002): Starlette TestClient over a temporary SQLite.

Unit 080 (P-20261105-cf32bea5): the app calls no AI. The tests of interpreting and confirming what was written
(/api/interpretar, /api/confirmar) are gone with those routes; what they set up (a food, a weight, an objective of the
day) is set up now by hand (/api/alimento, /api/peso/DIA) or with the classes, as the coach does.
"""
import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata import cli
from tridata.ia import AlimentoInterpretado
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, RegistroComidas
from tridata.web import crear_app

HOY = date(2026, 3, 4)
MANANA = HOY + timedelta(days=1)


def _item(nombre, **campos):
    datos = {
        "nombre": nombre, "cantidad": 100.0, "unidad": "g", "dia": HOY, "kcal": 100.0,
        "proteina_g": None, "hidratos_g": None, "grasa_g": None, "estimado": True,
        "dudoso": False, "supuestos": [],
    }
    datos.update(campos)
    return AlimentoInterpretado(**datos)


def _json(nombre, **campos):
    return _item(nombre, **campos).model_dump(mode="json")


@pytest.fixture
def entorno(tmp_path):
    db = tmp_path / "data.sqlite3"
    registro = RegistroComidas(db)
    interprete = MagicMock()
    ajustes = _ajustes(db)
    cliente = TestClient(crear_app(registro, interprete, ajustes, hoy=lambda: HOY))
    return SimpleEnv(cliente, interprete, db)


def _ajustes(db):
    """Settings in a temporary SQLite with the same values the fixed config had: 1600 + 10000 x 0.04."""
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
    return ajustes


class SimpleEnv:
    def __init__(self, cliente, interprete, db):
        self.cliente, self.interprete, self.db = cliente, interprete, db

    def filas(self, tabla):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]  # noqa: S608 - fixed names


# -- the page ----------------------------------------------------------------------

def test_sirve_la_pagina_del_movil(entorno):
    r = entorno.cliente.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert 'name="viewport"' in r.text and 'type="module"' in r.text
    assert entorno.cliente.get("/static/app.js").status_code == 200
    assert entorno.cliente.get("/static/estilos.css").status_code == 200


def test_dia_vacio_con_gasto_y_tres_margenes(entorno):
    r = entorno.cliente.get("/api/dia")
    assert r.status_code == 200
    dia = r.json()
    assert dia["fecha"] == HOY.isoformat()
    assert dia["alimentos"] == [] and dia["total_kcal"] == 0
    assert dia["gasto"]["kcal"] == 2000
    assert [m["kcal"] for m in dia["margenes"]] == [1800, 2000, 2200]


# -- R4, R5 (unit 080: by hand): the margin drops exactly what is saved --------------------

def _a_mano(entorno, nombre, kcal, dia=HOY):
    r = entorno.cliente.post("/api/alimento", json={"nombre": nombre, "cantidad": 100, "unidad": "g", "kcal": kcal,
                                                    "dia": dia.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


def test_el_margen_baja_exactamente_lo_apuntado(entorno):
    _a_mano(entorno, "arroz", 130)
    antes = entorno.cliente.get("/api/dia").json()
    _a_mano(entorno, "avena", 110)
    _a_mano(entorno, "plátano", 90)
    despues = entorno.cliente.get("/api/dia").json()

    assert despues["total_kcal"] - antes["total_kcal"] == 200
    assert [a["kcal"] - d["kcal"] for a, d in zip(antes["margenes"], despues["margenes"], strict=True)] == [200, 200, 200]


def test_para_manana_queda_planeado(entorno):  # R9 of unit 002, by hand
    r = _a_mano(entorno, "pasta", 360, dia=MANANA)
    assert r["alimento"]["estado"] == "planeado" and r["alimento"]["dia"] == MANANA.isoformat()
    assert entorno.cliente.get("/api/dia").json()["alimentos"] == []
    assert entorno.cliente.get(f"/api/dia?fecha={MANANA.isoformat()}").json()["alimentos"][0]["nombre"] == "pasta"


# -- R11: `tridata web` listens on 127.0.0.1:8765 by default -----------------------------

def test_tridata_web_por_defecto_solo_en_este_pc():
    args = cli._build_parser().parse_args(["web"])
    assert args.host == "127.0.0.1"
    assert args.port == 8765


def test_cmd_web_arranca_uvicorn_en_local(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIDATA_BASAL_KCAL", "1600")
    monkeypatch.setenv("TRIDATA_KCAL_POR_PASO", "0.04")
    arrancado = MagicMock()
    monkeypatch.setattr("uvicorn.run", arrancado)

    assert cli._cmd_web("127.0.0.1", 8765, db_path=tmp_path / "data.sqlite3") == 0

    assert arrancado.call_args.kwargs["host"] == "127.0.0.1"
    assert arrancado.call_args.kwargs["port"] == 8765


def test_cmd_web_sin_configuracion_avisa_y_no_arranca(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TRIDATA_BASAL_KCAL", raising=False)
    monkeypatch.delenv("TRIDATA_KCAL_POR_PASO", raising=False)
    arrancado = MagicMock()
    monkeypatch.setattr("uvicorn.run", arrancado)

    assert cli._cmd_web("127.0.0.1", 8765, db_path=tmp_path / "data.sqlite3") == 1
    assert "TRIDATA_BASAL_KCAL" in capsys.readouterr().err
    arrancado.assert_not_called()


# == unit 005: settings with a date from which they apply ================================

def _dia(entorno, fecha=None):
    r = entorno.cliente.get(f"/api/dia?fecha={fecha.isoformat()}" if fecha else "/api/dia")
    assert r.status_code == 200
    return r.json()


def _guardar_ajustes(entorno, datos, desde=None):
    cuerpo = {"datos": datos} if desde is None else {"datos": datos, "desde": desde.isoformat()}
    return entorno.cliente.post("/api/ajustes", json=cuerpo)


def test_guardar_en_ajustes_recalcula_hoy_sin_reiniciar(entorno):  # R1, R2
    r = _guardar_ajustes(entorno, {"pasos_por_defecto": 8000})  # no `desde`: today
    assert r.status_code == 200
    assert r.json()["preguntar_limite"] is False

    hoy = _dia(entorno)
    assert hoy["gasto"]["kcal"] == 1920
    assert [m["kcal"] for m in hoy["margenes"]] == [1728, 1920, 2112]
    assert "ajustes" in hoy["gasto"]["explicacion"] and ".env" not in hoy["gasto"]["explicacion"]
    assert r.json()["dia"]["gasto"]["kcal"] == 1920
    assert _dia(entorno, HOY - timedelta(days=1))["gasto"]["kcal"] == 2000

    vista = entorno.cliente.get("/api/ajustes").json()
    assert vista["fecha"] == HOY.isoformat()
    assert vista["vigentes"]["pasos_por_defecto"] == {"valor": 8000, "desde": "2026-03-04"}
    assert vista["vigentes"]["basal_kcal"] == {"valor": 1600, "desde": "0001-01-01"}
    assert vista["vigentes"]["edad"] == {"valor": None, "desde": None}
    assert [(h["valor"], h["desde"]) for h in Ajustes(entorno.db).historial("pasos_por_defecto")] == [
        (8000, "2026-03-04")]  # the 10,000 default was never saved: nothing was overwritten
    assert entorno.filas("ajuste") == 3


def test_cambio_futuro_no_cambia_hoy_y_se_anuncia(entorno):  # R4
    assert _guardar_ajustes(entorno, {"basal_kcal": 1700}, date(2026, 3, 10)).status_code == 200
    assert _dia(entorno)["gasto"]["kcal"] == 2000
    vista = entorno.cliente.get("/api/ajustes").json()
    assert vista["vigentes"]["basal_kcal"]["valor"] == 1600
    assert vista["futuros"] == [{"clave": "basal_kcal", "valor": 1700, "desde": "2026-03-10"}]


def test_cada_dia_con_su_valor_en_la_web(entorno):  # R3
    _guardar_ajustes(entorno, {"basal_kcal": 1700}, date(2026, 3, 10))
    _guardar_ajustes(entorno, {"objetivo_pct": -20}, date(1, 1, 1))
    _guardar_ajustes(entorno, {"objetivo_pct": -15}, date(2026, 3, 11))

    dia9, dia10, dia11 = (_dia(entorno, date(2026, 3, n)) for n in (9, 10, 11))
    assert (dia9["gasto"]["kcal"], dia10["gasto"]["kcal"]) == (2000, 2100)
    assert dia9["plan"] == {"objetivo_pct": -20, "motivo": "ajuste", "limite_saludable_pct": None}
    assert dia11["plan"]["objetivo_pct"] == -15


def test_objetivo_sin_limite_pregunta_y_no_supone(entorno):  # R6
    r = _guardar_ajustes(entorno, {"objetivo_pct": -15})
    assert r.status_code == 200 and r.json()["preguntar_limite"] is True
    assert _dia(entorno)["plan"] == {"objetivo_pct": -15, "motivo": "ajuste", "limite_saludable_pct": None}

    r = _guardar_ajustes(entorno, {"limite_saludable_pct": 25})
    assert r.status_code == 200 and r.json()["preguntar_limite"] is False
    assert _dia(entorno)["plan"]["limite_saludable_pct"] == 25


def test_carreras_por_la_web(entorno):  # R7
    _guardar_ajustes(entorno, {"objetivo_pct": -15}, date(1, 1, 1))
    r = entorno.cliente.post("/api/carrera", json={
        "nombre": "Triatlón Sprint Prueba", "fecha": "2026-03-20", "dias_mantenimiento": 2})
    assert r.status_code == 200
    carrera = r.json()["carrera"]
    assert _dia(entorno, date(2026, 3, 18))["plan"]["objetivo_pct"] == 0
    assert _dia(entorno, date(2026, 3, 18))["plan"]["motivo"] == "carrera Triatlón Sprint Prueba"
    assert _dia(entorno, date(2026, 3, 17))["plan"]["objetivo_pct"] == -15

    r = entorno.cliente.post(f"/api/carrera/{carrera['id']}", json={
        "nombre": "Triatlón Sprint Prueba", "fecha": "2026-03-22", "dias_mantenimiento": 2})
    assert r.status_code == 200
    assert [c["fecha"] for c in entorno.cliente.get("/api/ajustes").json()["carreras"]] == ["2026-03-22"]
    assert _dia(entorno, date(2026, 3, 19))["plan"]["objetivo_pct"] == -15
    assert _dia(entorno, date(2026, 3, 20))["plan"]["objetivo_pct"] == 0

    r = entorno.cliente.post(f"/api/carrera/{carrera['id']}/quitar", json={})
    assert r.status_code == 400  # removing asks for confirmation first
    assert entorno.filas("carrera") == 1
    r = entorno.cliente.post(f"/api/carrera/{carrera['id']}/quitar", json={"confirmado": True})
    assert r.status_code == 200
    assert entorno.filas("carrera") == 0
    assert entorno.cliente.post(f"/api/carrera/{carrera['id']}/quitar", json={"confirmado": True}).status_code == 404


def test_objetivo_de_peso_por_la_web(entorno):  # R8
    r = _guardar_ajustes(entorno, {"objetivo_peso": {"kg": 72.0, "fecha": "2026-06-30"}})
    assert r.status_code == 200
    assert entorno.cliente.get("/api/ajustes").json()["vigentes"]["objetivo_peso"] == {
        "valor": {"kg": 72.0, "fecha": "2026-06-30"}, "desde": "2026-03-04"}


@pytest.mark.parametrize(("ruta", "cuerpo"), [
    ("/api/ajustes", {"datos": {"basal_kcal": 0}}),
    ("/api/ajustes", {"datos": {"pasos_por_defecto": -5}}),
    ("/api/ajustes", {"datos": {"basal_kcal": "mucho"}}),
    ("/api/ajustes", {"datos": {"objetivo_pct": 60}}),
    ("/api/ajustes", {"datos": {"basal_kcal": 1700}, "desde": "nunca"}),
    ("/api/ajustes", {"datos": {"objetivo_peso": {"kg": 72, "fecha": "2026-01-01"}}}),
    ("/api/ajustes", {"datos": "no es un objeto"}),
    ("/api/ajustes", {}),
    ("/api/carrera", {"nombre": "", "fecha": "2026-03-20", "dias_mantenimiento": 2}),
    ("/api/carrera", {"nombre": "Prueba", "fecha": "nunca", "dias_mantenimiento": 2}),
    ("/api/carrera", {"nombre": "Prueba", "fecha": "2026-03-20", "dias_mantenimiento": 20}),
])
def test_rutas_de_ajustes_rechazan_lo_invalido(entorno, ruta, cuerpo):  # R9
    r = entorno.cliente.post(ruta, json=cuerpo)
    assert r.status_code == 400
    assert r.json()["error"]
    assert entorno.filas("ajuste") == 2
    assert entorno.filas("carrera") == 0
    assert _dia(entorno)["gasto"]["kcal"] == 2000


def test_cambiar_carrera_invalida_no_la_toca(entorno):  # R9
    carrera = entorno.cliente.post("/api/carrera", json={
        "nombre": "Prueba", "fecha": "2026-03-20", "dias_mantenimiento": 2}).json()["carrera"]
    r = entorno.cliente.post(f"/api/carrera/{carrera['id']}", json={
        "nombre": "Prueba", "fecha": "2026-03-20", "dias_mantenimiento": -1})
    assert r.status_code == 400
    assert entorno.cliente.get("/api/ajustes").json()["carreras"] == [carrera]
    assert entorno.cliente.post("/api/carrera/99", json={
        "nombre": "Prueba", "fecha": "2026-03-20", "dias_mantenimiento": 1}).status_code == 404


def test_pagina_de_ajustes_servida_y_enlazada(entorno):  # R1
    r = entorno.cliente.get("/ajustes")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert 'name="viewport"' in r.text and "/static/ajustes.js" in r.text
    assert entorno.cliente.get("/static/ajustes.js").status_code == 200
    assert 'href="/ajustes"' in entorno.cliente.get("/").text


# -- R10: `tridata web` seeds the settings from .env once -------------------------------

def test_cmd_web_siembra_de_env_una_sola_vez(tmp_path, monkeypatch):  # R10
    db = tmp_path / "data.sqlite3"
    arrancado = MagicMock()
    monkeypatch.setattr("uvicorn.run", arrancado)
    monkeypatch.setenv("TRIDATA_BASAL_KCAL", "1500")
    monkeypatch.setenv("TRIDATA_KCAL_POR_PASO", "0,05")
    assert cli._cmd_web("127.0.0.1", 8765, db_path=db) == 0
    assert TestClient(arrancado.call_args.args[0]).get("/api/dia").json()["gasto"]["kcal"] == 2000

    monkeypatch.setenv("TRIDATA_BASAL_KCAL", "1800")
    assert cli._cmd_web("127.0.0.1", 8765, db_path=db) == 0
    assert TestClient(arrancado.call_args.args[0]).get("/api/dia").json()["gasto"]["kcal"] == 2000
    assert Ajustes(db).vigentes(date.today()).basal_kcal == 1500

    monkeypatch.delenv("TRIDATA_BASAL_KCAL")
    monkeypatch.delenv("TRIDATA_KCAL_POR_PASO")
    assert cli._cmd_web("127.0.0.1", 8765, db_path=db) == 0  # .env no longer needed


# == unit 007: weight of the day and "nuevo día" in one message =============================
# "Today" is Tuesday 2026-03-10. Settings since always: 175 cm, 40 years, basal 1600 by hand,
# 0.04 kcal/step, 10,000 steps. All weights are invented.

HOY7 = date(2026, 3, 10)
AYER7 = HOY7 - timedelta(days=1)


@pytest.fixture
def dia7(tmp_path):
    from tridata.nutricion import ObjetivosDia, RegistroPeso

    db = tmp_path / "data.sqlite3"
    registro = RegistroComidas(db)
    ajustes = Ajustes(db)
    ajustes.cambiar({"altura_cm": 175, "edad": 40, "basal_kcal": 1600, "kcal_por_paso": 0.04,
                     "pasos_por_defecto": 10000}, desde=DESDE_SIEMPRE)
    interprete = MagicMock()
    cliente = TestClient(crear_app(registro, interprete, ajustes, hoy=lambda: HOY7))  # pesos/objetivos by default
    env = SimpleEnv(cliente, interprete, db)
    env.ajustes, env.pesos, env.objetivos = ajustes, RegistroPeso(db), ObjetivosDia(db)
    return env


def test_objetivo_del_dia_entra_en_el_margen(dia7):  # R7 (unit 080: saved as the coach saves it)
    dia7.objetivos.guardar(HOY7, {"tipo": "entreno", "porcentaje": -15, "niveles": [-15, 0],
                                  "entreno": "30 min suaves", "pasos": 8000})
    dia = _dia(dia7)
    assert dia["gasto"]["kcal"] == 1920
    assert [(m["nivel"], m["kcal"], m.get("objetivo", False)) for m in dia["margenes"]] == [
        (-15, 1632, True), (0, 1920, False)]
    assert dia["gasto"]["entreno_previsto"] == "30 min suaves"
    assert "no suma" in dia["gasto"]["explicacion"]
    assert dia["peso"]["basal"]["origen"] == "ajustes" and dia["peso"]["basal"]["kcal"] == 1600

    dia7.objetivos.guardar(HOY7, {"tipo": "descanso"})  # the last one wins; no % keeps only the type
    dia = _dia(dia7)
    assert dia["objetivo_dia"]["tipo"] == "descanso" and dia["objetivo_dia"]["porcentaje"] is None
    assert dia7.filas("objetivo_dia") == 1
    assert [m["nivel"] for m in dia["margenes"]] == [-10, 0, 10]
    assert dia["gasto"]["kcal"] == 2000

    ayer = _dia(dia7, AYER7)  # a day without objective, as before
    assert ayer["objetivo_dia"] is None
    assert [m["nivel"] for m in ayer["margenes"]] == [-10, 0, 10] and ayer["gasto"]["pasos"] == 10000


def test_el_dia_trae_peso_media_basal_y_proyeccion(dia7):  # R3, R4, R5
    for dia, kg in ((3, 75.0), (4, 71.0), (5, 70.8), (7, 70.6), (8, 70.4), (9, 70.2), (10, 70.0)):
        dia7.pesos.apuntar(date(2026, 3, dia), kg)
    dia7.ajustes.cambiar({"objetivo_peso": {"kg": 68.0, "fecha": "2026-05-19"}}, desde=DESDE_SIEMPRE)

    dia = _dia(dia7)
    peso = dia["peso"]
    assert peso["del_dia"]["kg"] == 70.0
    assert peso["media_7_dias"] == {"kg": 70.5, "dias_con_peso": 6}
    assert (peso["basal"]["kcal"], peso["basal"]["origen"]) == (1599, "peso")
    assert dia["gasto"]["kcal"] == 1999 and dia["gasto"]["basal_kcal"] == 1599
    assert "Mifflin" in dia["gasto"]["explicacion"]
    assert peso["proyeccion"]["faltan_kg"] == 2.5 and peso["proyeccion"]["kg_por_semana"] == 0.25

    r = dia7.cliente.post("/api/peso/2026-03-10", json={"kg": 70.2})  # corrected in the app (unit 080): basal 1601
    assert r.status_code == 200 and r.json()["resumen"]["basal"]["kcal"] == 1601
    assert _dia(dia7)["peso"]["basal"]["kcal"] == 1601


def test_el_dia_sin_hoy_usa_el_ultimo_anterior_y_sin_objetivo_no_proyecta(dia7):  # R4, R5
    dia7.pesos.apuntar(date(2026, 3, 8), 70.0)
    dia = _dia(dia7)
    assert dia["peso"]["del_dia"] is None
    assert dia["peso"]["basal"]["dia_del_peso"] == "2026-03-08" and dia["peso"]["basal"]["kcal"] == 1599
    assert dia["peso"]["proyeccion"] is None
    assert dia["peso"]["proyeccion_texto"] == "sin objetivo de peso"


def test_el_dia_sin_pesos_lo_dice(dia7):  # R3, R4
    dia = _dia(dia7)
    assert dia["peso"]["media_7_dias"] is None and "sin" in dia["peso"]["media_texto"]
    assert dia["peso"]["basal"]["origen"] == "ajustes"
    assert dia["gasto"]["kcal"] == 2000
    assert "a mano" in dia["gasto"]["explicacion"]


def test_cmd_web_crea_pesos_y_objetivos_en_la_misma_base(tmp_path, monkeypatch):
    db = tmp_path / "data.sqlite3"
    arrancado = MagicMock()
    monkeypatch.setattr("uvicorn.run", arrancado)
    monkeypatch.setenv("TRIDATA_BASAL_KCAL", "1600")
    monkeypatch.setenv("TRIDATA_KCAL_POR_PASO", "0.04")
    assert cli._cmd_web("127.0.0.1", 8765, db_path=db) == 0
    app = TestClient(arrancado.call_args.args[0])
    from tridata.nutricion import RegistroPeso

    RegistroPeso(db).apuntar(date.today(), 70.4)  # written down by the coach, in the same base (unit 080)
    r = app.post(f"/api/peso/{date.today().isoformat()}", json={"kg": 70.2})
    assert r.status_code == 200, r.text
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT kg FROM peso_dia").fetchall() == [(70.2,)]


# == unit 008: the full margin of today ====================================================
# "Today" is Tuesday 2026-03-10. Settings since always: basal 1600 by hand, no weight, 0.04
# kcal/step, 10,000 steps, no plan unless said. Garmin rows are invented and saved in a
# temporary SQLite through `DataStore`; Garmin itself is never called.

HOY8 = date(2026, 3, 10)
TABLAS8 = ("alimento_apuntado", "interpretacion", "objetivo_dia", "activities", "daily_stats")


@pytest.fixture
def dia8(tmp_path):
    from tridata.nutricion import ObjetivosDia
    from tridata.storage import DataStore

    db = tmp_path / "data.sqlite3"
    registro = RegistroComidas(db)
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000}, desde=DESDE_SIEMPRE)
    store = DataStore(db)
    interprete = MagicMock()
    cliente = TestClient(crear_app(registro, interprete, ajustes, hoy=lambda: HOY8, store=store))
    env = SimpleEnv(cliente, interprete, db)
    env.ajustes, env.store, env.registro, env.objetivos = ajustes, store, registro, ObjetivosDia(db)
    return env


def _garmin8(env, pasos=None, actividades=()):
    from tridata.models import DailyStats

    if pasos is not None:
        env.store.save_daily_stats(DailyStats(stat_date=HOY8, steps=pasos, total_calories=1234))
    if actividades:
        env.store.save_activities(list(actividades))


def _act8(id_, tipo, kcal, minutos, dia=HOY8, cadencia=None, nombre=None):
    from tridata.models import Activity

    return Activity(activity_id=id_, activity_date=dia, activity_type=tipo, name=nombre or tipo,
                    duration_seconds=minutos * 60, calories=kcal, avg_cadence=cadencia)


def _comer8(env, kcal, dia=HOY8):
    """Eaten by hand (unit 080: /api/alimento; it was /api/confirmar)."""
    r = env.cliente.post("/api/alimento", json={"nombre": "comida", "cantidad": 100, "unidad": "g", "kcal": kcal,
                                                "dia": dia.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()["dia"]


def _cuentas8(env):
    return {t: env.filas(t) for t in TABLAS8}


def test_dia_con_garmin_trae_gasto_desglose_niveles_y_barra(dia8):  # R1, R11
    _garmin8(dia8, pasos=4000, actividades=[_act8("n1", "lap_swimming", 300, 45, nombre="Natación")])
    dia8.objetivos.cambiar(HOY8, niveles=[-20, -15])
    _comer8(dia8, 1000)

    dia = _dia(dia8)
    assert dia["gasto"]["kcal"] == 2300
    d = dia["gasto"]["desglose"]
    assert (d["basal_kcal"], d["pasos_hechos"], d["pasos_supuestos"], d["kcal_por_paso"]) == (1600, 4000, 6000, 0.04)
    assert [(a["nombre"], a["kcal"]) for a in d["entreno_hecho"]] == [("Natación", 300)]
    assert d["previsto"] is None
    assert [(m["nivel"], m["kcal"]) for m in dia["margenes"]] == [(-20, 840), (-15, 955)]
    assert dia["gasto"]["garmin"]["hay_datos"] is True
    assert dia["objetivo"]["origen"] == "sin_plan" and "mantenimiento" in dia["objetivo"]["texto"]
    assert dia["barra"]["objetivo_kcal"] == 2300 and dia["barra"]["quedan_kcal"] == 1300


def test_dia_con_pasos_incompletos_avisa(dia8):  # R3
    _garmin8(dia8, pasos=3000, actividades=[_act8("c1", "running", 400, 30, cadencia=170)])
    dia = _dia(dia8)
    assert dia["gasto"]["kcal"] == 2400
    assert any("incompleto" in a for a in dia["gasto"]["avisos"])


def test_dia_sin_garmin_lo_dice(dia8):  # R4
    dia = _dia(dia8)
    assert dia["gasto"]["kcal"] == 2000
    assert "sin datos de Garmin de hoy" in dia["gasto"]["garmin"]["texto"]


def test_niveles_desde_la_web_solo_para_hoy(dia8):  # R6
    assert [m["nivel"] for m in _dia(dia8)["margenes"]] == [-10, 0, 10]
    r = dia8.cliente.post("/api/dia/objetivo", json={"niveles": [-20, -15, 0]})
    assert r.status_code == 200
    assert [m["nivel"] for m in r.json()["dia"]["margenes"]] == [-20, -15, 0]
    r = dia8.cliente.post("/api/dia/objetivo", json={"niveles": [-30]})  # changed again the same day
    assert [m["nivel"] for m in r.json()["dia"]["margenes"]] == [-30]
    assert dia8.filas("objetivo_dia") == 1
    manana = _dia(dia8, HOY8 + timedelta(days=1))
    assert [m["nivel"] for m in manana["margenes"]] == [-10, 0, 10]


@pytest.mark.parametrize("cuerpo", [
    {"niveles": [-60]}, {"niveles": [-20, 55]}, {"niveles": []}, {"niveles": [1, 2, 3, 4, 5, 6]},
    {"niveles": "mucho"}, {"porcentaje": 70}, {"nada": 1}, {},
])
def test_objetivo_no_valido_no_cambia_nada(dia8, cuerpo):  # R6
    dia8.cliente.post("/api/dia/objetivo", json={"niveles": [-20, -15]})
    r = dia8.cliente.post("/api/dia/objetivo", json=cuerpo)
    assert 400 <= r.status_code < 500
    assert dia8.objetivos.del_dia(HOY8)["niveles"] == [-20, -15]


def test_cambiar_el_objetivo_de_hoy_y_los_dias_de_retraso(dia8):  # R7
    dia8.ajustes.cambiar({"objetivo_pct": -20, "limite_saludable_pct": 25,
                          "objetivo_peso": {"kg": 68.0, "fecha": "2026-03-30"}}, desde=DESDE_SIEMPRE)
    antes = _dia(dia8)
    assert antes["objetivo"]["porcentaje"] == -20 and antes["objetivo"]["origen"] == "plan"
    assert antes["objetivo"]["retraso"] is None

    r = dia8.cliente.post("/api/dia/objetivo", json={"porcentaje": 0})
    assert r.status_code == 200
    dia = r.json()["dia"]
    assert dia["objetivo"]["porcentaje"] == 0 and dia["barra"]["objetivo_kcal"] == 2000
    assert dia["objetivo"]["retraso"]["dias"] == 1
    assert dia["objetivo"]["retraso"]["texto"] == "llegas al objetivo 1 día más tarde"
    assert dia8.ajustes.vigentes(HOY8).objetivo_pct == -20  # the plan does not change
    assert _dia(dia8, HOY8 + timedelta(days=1))["objetivo"]["porcentaje"] == -20

    r = dia8.cliente.post("/api/dia/objetivo", json={"porcentaje": -10})
    assert r.json()["dia"]["objetivo"]["retraso"]["dias"] == 0.5
    r = dia8.cliente.post("/api/dia/objetivo", json={"porcentaje": -30})
    assert r.json()["dia"]["objetivo"]["retraso"]["texto"] == "llegas al objetivo 0,5 días antes"
    assert r.json()["dia"]["barra"]["limite"]["superado"] is True  # R12: -30 % is past the 25 % limit


def test_cambiar_objetivo_sin_objetivo_de_peso_no_da_dias(dia8):  # R7
    dia8.ajustes.cambiar({"objetivo_pct": -20}, desde=DESDE_SIEMPRE)
    r = dia8.cliente.post("/api/dia/objetivo", json={"porcentaje": 0})
    assert r.status_code == 200
    assert r.json()["dia"]["objetivo"]["porcentaje"] == 0
    assert r.json()["dia"]["objetivo"]["retraso"] is None


def test_ayer_no_cuenta_en_el_margen_de_hoy(dia8):  # R8
    _comer8(dia8, 3000, dia=HOY8 - timedelta(days=1))  # a past day is saved as eaten
    dia = _dia(dia8)
    assert dia["total_kcal"] == 0 and [m["kcal"] for m in dia["margenes"]] == [1800, 2000, 2200]


def test_barra_se_actualiza_al_apuntar_cambiar_y_borrar(dia8):  # R11
    dia = _comer8(dia8, 1500)
    assert dia["barra"]["pct"] == 75 and dia["barra"]["texto"] == "quedan 500 kcal"
    (comida,) = dia8.registro.apuntes_del_dia(HOY8)
    r = dia8.cliente.post(f"/api/alimento/{comida['id']}", json={"nombre": "comida", "cantidad": 100, "unidad": "g",
                                                                  "kcal": 2200})
    assert r.json()["dia"]["barra"]["pct"] == 110 and r.json()["dia"]["barra"]["texto"] == "te has pasado 200 kcal"
    r = dia8.cliente.post(f"/api/alimento/{comida['id']}/borrar", json={"confirmado": True})
    assert r.json()["dia"]["barra"]["pct"] == 0


def test_limite_saludable_en_la_barra(dia8):  # R12
    dia8.ajustes.cambiar({"limite_saludable_pct": 25, "objetivo_pct": -20}, desde=DESDE_SIEMPRE)
    dia = _dia(dia8)
    assert dia["barra"]["limite"]["kcal"] == 1500 and dia["barra"]["limite"]["superado"] is False
    sin = Ajustes(dia8.db)
    sin.cambiar({"objetivo_pct": 0}, desde=HOY8)  # still a limit: maintenance does not pass it
    assert _dia(dia8)["barra"]["limite"]["superado"] is False


def test_simular_no_guarda_nada(dia8):  # R10
    _garmin8(dia8, pasos=4000, actividades=[_act8("n1", "lap_swimming", 300, 45)])
    dia8.objetivos.cambiar(HOY8, niveles=[-20, -15])
    _comer8(dia8, 1000)
    antes = _cuentas8(dia8)

    lista = [_json("arroz", dia=HOY8, cantidad=200, kcal=300), _json("pollo", dia=HOY8, cantidad=150, kcal=200)]
    r = dia8.cliente.post("/api/simular", json={"alimentos": lista})

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["kcal_lista"] == 500
    assert [(m["nivel"], m["kcal"]) for m in cuerpo["margenes"]] == [(-20, 340), (-15, 455)]
    assert cuerpo["barra"]["comido_kcal"] == 1500
    assert _cuentas8(dia8) == antes
    assert _dia(dia8)["total_kcal"] == 1000


def test_cuanto_me_cabe_desde_la_web(dia8):  # R9
    _comer8(dia8, 1600)  # margin 400 kcal for maintenance (no plan) with an expenditure of 2000
    antes = _cuentas8(dia8)
    pasta = _json("pasta seca", dia=HOY8, cantidad=100, unidad="g", kcal=360)
    r = dia8.cliente.post("/api/simular", json={"alimentos": [pasta]})
    assert r.status_code == 200
    (cabe,) = r.json()["caben"]
    assert cabe["cantidad"] == 111 and cabe["unidad"] == "g"
    assert _cuentas8(dia8) == antes

    _comer8(dia8, 400)  # margin 0
    (cabe,) = dia8.cliente.post("/api/simular", json={"alimentos": [pasta]}).json()["caben"]
    assert cabe["cantidad"] == 0 and cabe["texto"] == "no te cabe hoy"


def test_simular_con_datos_malos_se_rechaza(dia8):
    r = dia8.cliente.post("/api/simular", json={"alimentos": "pan"})
    assert r.status_code == 400


def test_previsto_desde_la_web(dia8):  # R5
    hace3 = HOY8 - timedelta(days=3)
    hace20 = HOY8 - timedelta(days=20)
    _garmin8(dia8, actividades=[_act8("l1", "running", 350, 35, dia=hace3, nombre="Rodaje"),
                                _act8("v1", "running", 500, 50, dia=hace20, nombre="Viejo")])

    recientes = dia8.cliente.get("/api/actividades-recientes").json()["actividades"]
    assert [a["actividad_id"] for a in recientes] == ["l1"]
    assert recientes[0]["minutos"] == 35 and recientes[0]["kcal"] == 350

    r = dia8.cliente.post("/api/dia/objetivo", json={"previsto": {"actividad_id": "v1", "minutos": 30}})
    assert 400 <= r.status_code < 500
    assert dia8.objetivos.del_dia(HOY8) is None

    sin = _dia(dia8)["gasto"]["kcal"]
    r = dia8.cliente.post("/api/dia/objetivo", json={"previsto": {"actividad_id": "l1", "minutos": 30}})
    assert r.status_code == 200
    dia = r.json()["dia"]
    assert dia["gasto"]["kcal"] == sin + 300
    assert dia["gasto"]["desglose"]["previsto"]["nombre"] == "Rodaje"
    assert dia["gasto"]["desglose"]["previsto_kcal"] == 300

    r = dia8.cliente.post("/api/dia/objetivo", json={"previsto": None})
    assert r.json()["dia"]["gasto"]["kcal"] == sin


@pytest.mark.parametrize("previsto", [{"actividad_id": "l1", "minutos": 0}, {"actividad_id": "l1"},
                                      {"actividad_id": "nada", "minutos": 30}, "rodaje"])
def test_previsto_no_valido_no_cambia_nada(dia8, previsto):  # R5
    _garmin8(dia8, actividades=[_act8("l1", "running", 350, 35, dia=HOY8 - timedelta(days=3))])
    r = dia8.cliente.post("/api/dia/objetivo", json={"previsto": previsto})
    assert 400 <= r.status_code < 500
    assert dia8.objetivos.del_dia(HOY8) is None


def test_sin_store_el_dia_es_sin_garmin_y_lo_de_antes_sigue(entorno):  # R4
    dia = _dia(entorno)
    assert dia["gasto"]["kcal"] == 2000
    assert "sin datos de Garmin" in dia["gasto"]["garmin"]["texto"]
    assert entorno.cliente.get("/api/actividades-recientes").json()["actividades"] == []


def test_la_pantalla_tiene_barra_boton_y_niveles(dia8):
    html = dia8.cliente.get("/").text
    for ident in ('id="barra"', 'id="actualizar-garmin"', 'id="niveles"', 'id="desglose"', 'id="previsto"',
                  'id="objetivo-hoy"'):
        assert ident in html
