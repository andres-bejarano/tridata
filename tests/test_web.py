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


# == unit 007: weight of the day and "nuevo día" in one message =============================
# "Today" is Tuesday 2026-03-10. Settings since always: 175 cm, 40 years, basal 1600 by hand,
# 0.04 kcal/step, 10,000 steps. All weights are invented.

HOY7 = date(2026, 3, 10)
AYER7 = HOY7 - timedelta(days=1)
TABLAS7 = ("alimento_apuntado", "interpretacion", "peso_dia", "objetivo_dia")


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


def _piezas7(alimentos=(), peso=None, objetivo=None, dudosas=()):
    from tridata.ia import ListaInterpretada, ObjetivoDiaInterpretado, PesoInterpretado

    return ListaInterpretada(
        alimentos=list(alimentos),
        peso=PesoInterpretado(**{"dia": HOY7, "nota": None, **peso}) if peso else None,
        objetivo_dia=ObjetivoDiaInterpretado(**{"tipo": None, "porcentaje": None, "niveles": None, "entreno": None,
                                                "pasos": None, **objetivo}) if objetivo else None,
        dudosas=list(dudosas),
    )


def _cuentas(env):
    return {t: env.filas(t) for t in TABLAS7}


def _confirmar7(env, **cuerpo):
    return env.cliente.post("/api/confirmar", json={"texto": "x", "sin_cambios": True, **cuerpo})


def _comida7(nombre, **campos):
    return _json(nombre, dia=HOY7, **campos)


def test_nuevo_dia_se_ve_junto_y_no_guarda_nada(dia7):  # R6
    porridge = _item("porridge", cantidad=None, unidad=None, kcal=None, dia=HOY7)
    dia7.interprete.interpretar.return_value = _piezas7(
        [porridge], peso={"kg": 70.2}, objetivo={"tipo": "descanso", "entreno": "30 min suaves"})
    texto = "nuevo día 70,2, descanso, 30 minutos suaves, desayuno porridge"

    r = dia7.cliente.post("/api/interpretar", json={"texto": texto})

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["peso"] == {"kg": 70.2, "dia": "2026-03-10", "nota": None}
    assert cuerpo["objetivo_dia"]["tipo"] == "descanso" and cuerpo["objetivo_dia"]["entreno"] == "30 min suaves"
    assert [a["nombre"] for a in cuerpo["alimentos"]] == ["porridge"]
    assert cuerpo["alimentos"][0]["cantidad"] is None
    assert cuerpo["dudosas"] == []
    assert _cuentas(dia7) == dict.fromkeys(TABLAS7, 0)


def test_solo_comida_sigue_igual_con_piezas_vacias(dia7):  # R10
    dia7.interprete.interpretar.return_value = [_item("yogur", dia=HOY7)]  # an interpreter that only knows foods
    cuerpo = dia7.cliente.post("/api/interpretar", json={"texto": "un yogur"}).json()
    assert [a["nombre"] for a in cuerpo["alimentos"]] == ["yogur"]
    assert cuerpo["peso"] is None and cuerpo["objetivo_dia"] is None and cuerpo["dudosas"] == []

    r = _confirmar7(dia7, alimentos=[_comida7("yogur", kcal=80)])
    assert r.status_code == 200
    assert r.json()["dia"]["total_kcal"] == 80
    assert _cuentas(dia7) == {"alimento_apuntado": 1, "interpretacion": 1, "peso_dia": 0, "objetivo_dia": 0}


def test_confirmar_nuevo_dia_guarda_cada_pieza_en_su_sitio(dia7):  # R6, R7, R10
    r = _confirmar7(
        dia7, sin_cambios=False,
        peso={"kg": 70.1, "dia": "2026-03-10", "nota": None},
        objetivo_dia={"tipo": "descanso", "porcentaje": None, "niveles": None, "entreno": "30 min suaves", "pasos": None},
        alimentos=[_comida7("porridge", cantidad=None, unidad=None, kcal=None)],
    )
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["peso"]["kg"] == 70.1
    assert cuerpo["objetivo_dia"]["tipo"] == "descanso"
    dia = cuerpo["dia"]
    assert dia["peso"]["del_dia"]["kg"] == 70.1
    assert dia["objetivo_dia"]["entreno"] == "30 min suaves"
    assert [p["nombre"] for p in dia["pendientes"]] == ["porridge"]
    assert _cuentas(dia7) == {"alimento_apuntado": 1, "interpretacion": 1, "peso_dia": 1, "objetivo_dia": 1}
    assert RegistroComidas(dia7.db).interpretaciones()[0]["confirmada_sin_cambios"] is False


def test_peso_de_hoy_se_sustituye(dia7):  # R1 (criterio portante), R2
    assert _confirmar7(dia7, peso={"kg": 70.4, "dia": "2026-03-10", "nota": None}).status_code == 200
    r = _confirmar7(dia7, peso={"kg": 70.2, "dia": "2026-03-10", "nota": "retención tras la cena"})
    assert r.status_code == 200
    assert r.json()["dia"]["peso"]["del_dia"] == {"dia": "2026-03-10", "kg": 70.2, "nota": "retención tras la cena"}
    assert dia7.filas("peso_dia") == 1

    assert _confirmar7(dia7, peso={"kg": 70.5, "dia": "2026-03-09", "nota": None}).status_code == 200
    assert dia7.pesos.del_dia(AYER7)["kg"] == 70.5
    assert dia7.pesos.del_dia(HOY7)["kg"] == 70.2


def test_solo_peso_deja_su_interpretacion(dia7):  # R10
    r = _confirmar7(dia7, texto="peso 70,2", sin_cambios=True, peso={"kg": 70.2, "dia": "2026-03-10", "nota": None})
    assert r.status_code == 200
    assert r.json()["guardados"] == []
    (interpretacion,) = RegistroComidas(dia7.db).interpretaciones()
    assert interpretacion["texto"] == "peso 70,2" and interpretacion["confirmada_sin_cambios"] is True


def test_objetivo_del_dia_entra_en_el_margen(dia7):  # R7
    r = _confirmar7(dia7, objetivo_dia={"tipo": "entreno", "porcentaje": -15, "niveles": [-15, 0],
                                        "entreno": "30 min suaves", "pasos": 8000})
    assert r.status_code == 200
    dia = r.json()["dia"]
    assert dia["gasto"]["kcal"] == 1920
    assert [(m["nivel"], m["kcal"], m.get("objetivo", False)) for m in dia["margenes"]] == [
        (-15, 1632, True), (0, 1920, False)]
    assert dia["gasto"]["entreno_previsto"] == "30 min suaves"
    assert "no suma" in dia["gasto"]["explicacion"]
    assert dia["peso"]["basal"]["origen"] == "ajustes" and dia["peso"]["basal"]["kcal"] == 1600

    _confirmar7(dia7, objetivo_dia={"tipo": "descanso"})  # the last one wins; no % keeps only the type
    dia = _dia(dia7)
    assert dia["objetivo_dia"]["tipo"] == "descanso" and dia["objetivo_dia"]["porcentaje"] is None
    assert dia7.filas("objetivo_dia") == 1
    assert [m["nivel"] for m in dia["margenes"]] == [-10, 0, 10]
    assert dia["gasto"]["kcal"] == 2000

    ayer = _dia(dia7, AYER7)  # a day without objective, as before
    assert ayer["objetivo_dia"] is None
    assert [m["nivel"] for m in ayer["margenes"]] == [-10, 0, 10] and ayer["gasto"]["pasos"] == 10000


def test_una_dudosa_bloquea_y_al_quitarla_se_guarda_el_resto(dia7):  # R8
    piezas = {"peso": {"kg": 70.2, "dia": "2026-03-10", "nota": None}}
    r = _confirmar7(dia7, dudosas=["lo del martes ya sabes"], **piezas)
    assert r.status_code == 422
    assert "lo del martes ya sabes" in r.json()["error"]
    assert _cuentas(dia7) == dict.fromkeys(TABLAS7, 0)

    r = _confirmar7(dia7, dudosas=[], **piezas)
    assert r.status_code == 200
    assert _cuentas(dia7) == {"alimento_apuntado": 0, "interpretacion": 1, "peso_dia": 1, "objetivo_dia": 0}


def test_confirmar_sin_ninguna_pieza_se_rechaza(dia7):
    r = _confirmar7(dia7, alimentos=[], peso=None, objetivo_dia=None)
    assert r.status_code == 400
    assert _cuentas(dia7) == dict.fromkeys(TABLAS7, 0)


@pytest.mark.parametrize(("piezas", "motivo"), [
    ({"peso": {"kg": 0, "dia": "2026-03-10", "nota": None}}, "el peso tiene que estar entre 30 y 250 kg"),
    ({"peso": {"kg": 300, "dia": "2026-03-10", "nota": None}}, "el peso tiene que estar entre 30 y 250 kg"),
    ({"peso": {"kg": 70.2, "dia": "2026-03-11", "nota": None}}, "peso"),
    ({"objetivo_dia": {"tipo": "entreno", "porcentaje": "mucho"}}, "%"),
    ({"objetivo_dia": {"tipo": "entreno", "pasos": "muchos"}}, "pasos"),
])
def test_todo_o_nada(dia7, piezas, motivo):  # R11
    cuerpo = {"peso": {"kg": 70.2, "dia": "2026-03-10", "nota": None},
              "objetivo_dia": {"tipo": "entreno", "porcentaje": -15}, **piezas}
    r = _confirmar7(dia7, alimentos=[_comida7("pan", kcal=250)], **cuerpo)
    assert 400 <= r.status_code < 500
    assert motivo in r.json()["error"]
    assert _cuentas(dia7) == dict.fromkeys(TABLAS7, 0)


def test_todo_o_nada_con_un_alimento_malo(dia7):  # R11
    r = _confirmar7(dia7, peso={"kg": 70.2, "dia": "2026-03-10", "nota": None},
                    alimentos=[_comida7("pan", kcal=None)])
    assert 400 <= r.status_code < 500
    assert _cuentas(dia7) == dict.fromkeys(TABLAS7, 0)


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

    _confirmar7(dia7, peso={"kg": 70.2, "dia": "2026-03-10", "nota": None})  # corrected: basal 1601
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


def test_peticion_a_claude_sin_datos_guardados(tmp_path):  # R9
    from tridata.ia import INSTRUCCION, InterpreteClaude, ListaInterpretada, PesoInterpretado
    from tridata.nutricion import ObjetivosDia, RegistroPeso

    db = tmp_path / "data.sqlite3"
    ajustes = Ajustes(db)
    ajustes.cambiar({"altura_cm": 175, "edad": 40, "basal_kcal": 1600, "kcal_por_paso": 0.04,
                     "objetivo_peso": {"kg": 68.0, "fecha": "2026-05-19"}}, desde=DESDE_SIEMPRE)
    RegistroPeso(db).apuntar(AYER7, 70.4)
    ObjetivosDia(db).guardar(AYER7, {"tipo": "entreno", "porcentaje": -15})
    claude = MagicMock()
    claude.messages.parse.return_value = MagicMock(
        parsed_output=ListaInterpretada(alimentos=[], peso=PesoInterpretado(kg=70.2, dia=HOY7, nota=None)),
        stop_reason="end_turn")
    cliente = TestClient(crear_app(RegistroComidas(db), InterpreteClaude(cliente=claude, modelo="m"), ajustes,
                                   hoy=lambda: HOY7))

    r = cliente.post("/api/interpretar", json={"texto": "peso 70,2"})

    assert r.status_code == 200 and r.json()["peso"]["kg"] == 70.2
    kwargs = claude.messages.parse.call_args.kwargs
    assert kwargs["system"] == INSTRUCCION
    assert kwargs["messages"] == [
        {"role": "user", "content": "Hoy es martes, 2026-03-10.\n\nLo que ha escrito:\npeso 70,2"}]
    enviado = repr(kwargs["messages"]) + kwargs["system"]
    for dato in ("70,4", "70.4", "1600", "175", "68", "2026-05-19", "-15"):
        assert dato not in enviado


def test_cmd_web_crea_pesos_y_objetivos_en_la_misma_base(tmp_path, monkeypatch):
    db = tmp_path / "data.sqlite3"
    arrancado = MagicMock()
    monkeypatch.setattr("uvicorn.run", arrancado)
    monkeypatch.setenv("TRIDATA_BASAL_KCAL", "1600")
    monkeypatch.setenv("TRIDATA_KCAL_POR_PASO", "0.04")
    assert cli._cmd_web("127.0.0.1", 8765, db_path=db) == 0
    app = TestClient(arrancado.call_args.args[0])
    r = app.post("/api/confirmar", json={"texto": "peso 70,2", "sin_cambios": True,
                                         "peso": {"kg": 70.2, "dia": date.today().isoformat(), "nota": None}})
    assert r.status_code == 200
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT kg FROM peso_dia").fetchall() == [(70.2,)]
