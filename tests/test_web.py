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
from tridata.nutricion import ConfigGasto, RegistroComidas
from tridata.web import crear_app

HOY = date(2026, 3, 4)
MANANA = HOY + timedelta(days=1)
CONFIG = ConfigGasto(basal_kcal=1600, kcal_por_paso=0.04)


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
    cliente = TestClient(crear_app(registro, interprete, CONFIG, hoy=lambda: HOY))
    return SimpleEnv(cliente, interprete, db)


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
