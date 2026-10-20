"""Web app and `tridata web` (unit 002): Starlette TestClient over a temporary
SQLite, with the Claude interpreter replaced by a unittest.mock double (no API).
"""
import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata import cli
from tridata.ia import AlimentoInterpretado, ErrorInterpretacion
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


# -- R1: interpreting shows the list and saves nothing -----------------------------

def test_interpretar_muestra_la_lista_y_no_guarda_nada(entorno):
    entorno.interprete.interpretar.return_value = [
        _item("lentejas", cantidad=300, kcal=345, supuestos=["lentejas cocidas"]),
        _item("plátano", cantidad=1, unidad="unidad", kcal=105),
    ]
    r = entorno.cliente.post("/api/interpretar", json={"texto": "300 gramos de lentejas y un plátano"})

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["texto"] == "300 gramos de lentejas y un plátano"
    lentejas, platano = cuerpo["alimentos"]
    assert lentejas == {
        "nombre": "lentejas", "cantidad": 300.0, "unidad": "g", "dia": HOY.isoformat(), "kcal": 345.0,
        "proteina_g": None, "hidratos_g": None, "grasa_g": None, "estimado": True, "dudoso": False,
        "supuestos": ["lentejas cocidas"],
    }
    assert platano["unidad"] == "unidad"
    entorno.interprete.interpretar.assert_called_once_with("300 gramos de lentejas y un plátano", HOY)
    assert entorno.filas("alimento_apuntado") == 0
    assert entorno.filas("interpretacion") == 0


def test_interpretar_texto_vacio_no_llama_a_la_ia(entorno):
    r = entorno.cliente.post("/api/interpretar", json={"texto": "   "})
    assert r.status_code == 400
    entorno.interprete.interpretar.assert_not_called()


# -- R10: Claude fails -> message, text kept, nothing saved --------------------------

def test_error_de_la_ia_conserva_el_texto_y_no_guarda(entorno):
    entorno.interprete.interpretar.side_effect = ErrorInterpretacion("sin red")
    r = entorno.cliente.post("/api/interpretar", json={"texto": "un yogur"})

    assert r.status_code == 502
    cuerpo = r.json()
    assert "No he podido interpretar" in cuerpo["error"]
    assert "inténtalo de nuevo" in cuerpo["error"].lower()
    assert cuerpo["texto"] == "un yogur"
    assert entorno.filas("alimento_apuntado") == 0


# -- R4, R5: confirming saves and returns the table and the margin -------------------

def test_confirmar_guarda_y_devuelve_tabla_y_margen(entorno):
    alimentos = [_json("lentejas", cantidad=300, kcal=600), _json("pan", cantidad=150, kcal=450)]
    r = entorno.cliente.post("/api/confirmar", json={
        "texto": "lentejas y pan", "alimentos": alimentos, "sin_cambios": False,
    })

    assert r.status_code == 200
    dia = r.json()["dia"]
    assert [a["nombre"] for a in dia["alimentos"]] == ["lentejas", "pan"]
    assert dia["total_kcal"] == 1050
    assert dia["gasto"]["kcal"] == 2000 and "explicacion" in dia["gasto"]
    assert [(m["nivel"], m["kcal"]) for m in dia["margenes"]] == [(-10, 750), (0, 950), (10, 1150)]
    assert entorno.filas("alimento_apuntado") == 2
    assert entorno.filas("interpretacion") == 1


def test_el_margen_baja_exactamente_lo_confirmado(entorno):
    entorno.cliente.post("/api/confirmar", json={
        "texto": "arroz", "alimentos": [_json("arroz", kcal=130)], "sin_cambios": True})
    antes = entorno.cliente.get("/api/dia").json()
    entorno.cliente.post("/api/confirmar", json={
        "texto": "avena y plátano", "alimentos": [_json("avena", kcal=110), _json("plátano", kcal=90)],
        "sin_cambios": True})
    despues = entorno.cliente.get("/api/dia").json()

    assert despues["total_kcal"] - antes["total_kcal"] == 200
    assert [a["kcal"] - d["kcal"] for a, d in zip(antes["margenes"], despues["margenes"], strict=True)] == [200, 200, 200]


# -- R6: a doubtful item blocks confirming --------------------------------------------

def test_confirmar_con_un_dudoso_se_rechaza_y_no_guarda(entorno):
    r = entorno.cliente.post("/api/confirmar", json={
        "texto": "200 g de jamón y un poco de kfjd",
        "alimentos": [_json("jamón", cantidad=200, kcal=210),
                      _json("kfjd", cantidad=None, unidad=None, kcal=None, dudoso=True)],
        "sin_cambios": True,
    })
    assert r.status_code == 422
    assert "dudoso" in r.json()["error"]
    assert entorno.filas("alimento_apuntado") == 0


# -- R7: no quantity -> pending and reminded --------------------------------------------

def test_confirmar_sin_cantidad_lo_recuerda(entorno):
    r = entorno.cliente.post("/api/confirmar", json={
        "texto": "leche en el café",
        "alimentos": [_json("leche", cantidad=None, unidad=None, kcal=None)], "sin_cambios": True,
    })
    assert r.status_code == 200
    dia = r.json()["dia"]
    assert [p["nombre"] for p in dia["pendientes"]] == ["leche"]
    assert dia["total_kcal"] == 0


# -- R9: another day -> planned there, today's table unchanged --------------------------

def test_confirmar_para_manana_queda_planeado(entorno):
    r = entorno.cliente.post("/api/confirmar", json={
        "texto": "mañana 100 g de pasta",
        "alimentos": [_json("pasta", dia=MANANA, kcal=360)], "sin_cambios": True,
    })
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["dia"]["alimentos"] == [] and cuerpo["dia"]["total_kcal"] == 0
    (pasta,) = cuerpo["guardados"]
    assert pasta["estado"] == "planeado" and pasta["dia"] == MANANA.isoformat()
    assert entorno.cliente.get(f"/api/dia?fecha={MANANA.isoformat()}").json()["alimentos"][0]["nombre"] == "pasta"


@pytest.mark.parametrize("cuerpo", [
    {"texto": "x", "alimentos": "no es una lista", "sin_cambios": True},
    {"texto": "x", "alimentos": [], "sin_cambios": True},
    {"texto": "x", "alimentos": [{"nombre": "pan", "dia": "nunca"}], "sin_cambios": True},
])
def test_confirmar_rechaza_datos_malos(entorno, cuerpo):
    r = entorno.cliente.post("/api/confirmar", json=cuerpo)
    assert r.status_code in (400, 422)
    assert entorno.filas("alimento_apuntado") == 0


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
