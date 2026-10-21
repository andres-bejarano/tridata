"""Unit 016 (small everyday fixes), the routes and the repository: Starlette TestClient over a temporary
SQLite, the Claude interpreter a `MagicMock` (no API). Every value is invented.

R1 the objective of a «nuevo día» goes to the day of the list · R2 /api/interpretar with the day of the
recording · R3 (support) /api/dia says which day is today · R7 new kcal drop «de tu receta» and «provisional».
R6 lives in `test_actualizar_garmin.py`.
"""
import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.ia import AlimentoInterpretado, Interpretacion, ObjetivoDiaInterpretado
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, ObjetivosDia, RegistroComidas
from tridata.web import crear_app

HOY = date(2026, 10, 21)  # a Wednesday
MARTES = HOY - timedelta(days=1)
JUEVES = HOY + timedelta(days=1)
TABLAS = ("alimento_apuntado", "interpretacion", "peso_dia", "objetivo_dia")
DESCANSO = {"tipo": "descanso", "porcentaje": 0, "niveles": None, "entreno": None, "pasos": None}


class Entorno:
    def __init__(self, db):
        self.db = db
        self.registro = RegistroComidas(db)
        ajustes = Ajustes(db)
        ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000, "objetivo_pct": -10},
                        desde=DESDE_SIEMPRE)
        self.objetivos = ObjetivosDia(db)
        self.interprete = MagicMock()
        self.cliente = TestClient(crear_app(self.registro, self.interprete, ajustes, hoy=lambda: HOY,
                                            objetivos=self.objetivos))

    def filas(self):
        with sqlite3.connect(self.db) as conn:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLAS}  # noqa: S608


@pytest.fixture
def entorno(tmp_path):
    return Entorno(tmp_path / "data.sqlite3")


def _avena(dia):
    return {"nombre": "avena de prueba", "cantidad": 60, "unidad": "g", "dia": dia.isoformat(), "kcal": 228,
            "estimado": True, "dudoso": False, "supuestos": []}


def _confirmar(entorno, **extra):
    cuerpo = {"texto": "nuevo día, descanso, mantenimiento, avena de prueba 60 g", "alimentos": [_avena(MARTES)],
              "peso": {"kg": 72.4, "dia": MARTES.isoformat(), "nota": None}, "objetivo_dia": DESCANSO,
              "dudosas": [], "sin_cambios": True, **extra}
    return entorno.cliente.post("/api/confirmar", json=cuerpo)


# -- R1: the objective of a «nuevo día» goes to the day of the list ------------------------------------

def test_lista_del_martes_guarda_el_objetivo_en_el_martes(entorno):  # R1 · R-31
    r = _confirmar(entorno, dia=MARTES.isoformat())

    assert r.status_code == 200, r.text
    assert entorno.objetivos.del_dia(MARTES)["tipo"] == "descanso"
    assert entorno.objetivos.del_dia(MARTES)["porcentaje"] == 0
    assert entorno.objetivos.del_dia(HOY) is None
    martes = entorno.cliente.get(f"/api/dia?fecha={MARTES.isoformat()}").json()
    assert martes["objetivo"]["origen"] == "dia" and martes["objetivo"]["porcentaje"] == 0
    hoy = entorno.cliente.get("/api/dia").json()
    assert hoy["objetivo"]["origen"] == "plan" and hoy["objetivo"]["porcentaje"] == -10
    assert [(a["nombre"], a["estado"]) for a in martes["alimentos"]] == [("avena de prueba", "comido")]
    assert r.json()["dia"]["fecha"] == HOY.isoformat()  # the answer is still today's day


@pytest.mark.parametrize("extra", [{"dia": HOY.isoformat()}, {}], ids=["lista-de-hoy", "sin-dia"])
def test_lista_de_hoy_o_sin_dia_guarda_el_objetivo_hoy(entorno, extra):  # R1
    r = _confirmar(entorno, alimentos=[_avena(HOY)], peso={"kg": 72.4, "dia": HOY.isoformat(), "nota": None}, **extra)

    assert r.status_code == 200, r.text
    assert entorno.objetivos.del_dia(HOY)["tipo"] == "descanso"
    assert entorno.objetivos.del_dia(MARTES) is None


@pytest.mark.parametrize("dia", [JUEVES.isoformat(), "ayer", 20261020, "2026-13-01"])
def test_dia_de_la_lista_posterior_o_mal_escrito_es_400_y_no_guarda_nada(entorno, dia):  # R1
    antes = entorno.filas()

    r = _confirmar(entorno, dia=dia)

    assert r.status_code == 400
    assert "error" in r.json()
    assert entorno.filas() == antes


# -- R2: reinterpreting a list with the day of its recording ------------------------------------------

def _interpretacion(texto, hoy):
    return Interpretacion(
        alimentos=[AlimentoInterpretado(nombre="avena de prueba", cantidad=60, unidad="g", dia=hoy, kcal=228,
                                        proteina_g=None, hidratos_g=None, grasa_g=None, estimado=True,
                                        dudoso=False, supuestos=[])],
        objetivo_dia=ObjetivoDiaInterpretado(tipo="descanso", porcentaje=0, niveles=None, entreno=None, pasos=None),
    )


def test_interpretar_con_la_fecha_de_la_grabacion(entorno):  # R2 · R-10
    entorno.interprete.interpretar.side_effect = _interpretacion

    r = entorno.cliente.post("/api/interpretar", json={"texto": "avena de prueba 60 g", "fecha": MARTES.isoformat()})

    assert r.status_code == 200, r.text
    entorno.interprete.interpretar.assert_called_once_with("avena de prueba 60 g", MARTES)
    assert [a["dia"] for a in r.json()["alimentos"]] == [MARTES.isoformat()]


def test_interpretar_sin_fecha_usa_el_hoy_del_servidor(entorno):  # R2
    entorno.interprete.interpretar.side_effect = _interpretacion

    r = entorno.cliente.post("/api/interpretar", json={"texto": "avena de prueba 60 g"})

    assert r.status_code == 200, r.text
    entorno.interprete.interpretar.assert_called_once_with("avena de prueba 60 g", HOY)


@pytest.mark.parametrize("fecha", [JUEVES.isoformat(), "20/10", 20, "2026-10-32"])
def test_interpretar_con_fecha_posterior_o_mal_escrita_es_400_sin_llamar(entorno, fecha):  # R2
    r = entorno.cliente.post("/api/interpretar", json={"texto": "avena de prueba 60 g", "fecha": fecha})

    assert r.status_code == 400
    assert "error" in r.json()
    entorno.interprete.interpretar.assert_not_called()


# -- R3 (support): every day says which day is today on the server ------------------------------------

def test_el_dia_dice_que_dia_es_hoy(entorno):  # R3
    assert entorno.cliente.get("/api/dia").json()["hoy"] == HOY.isoformat()
    otro = entorno.cliente.get(f"/api/dia?fecha={(HOY - timedelta(days=2)).isoformat()}").json()
    assert otro["fecha"] == (HOY - timedelta(days=2)).isoformat() and otro["hoy"] == HOY.isoformat()


# -- R7: new kcal drop «de tu receta» and «provisional» -----------------------------------------------

ORIGEN = "de tu receta «Lentejas de prueba»"


def _lentejas(registro):
    alimento = Alimento(nombre="Lentejas de prueba", dia=HOY, cantidad=250, unidad="g", kcal=320, origen=ORIGEN,
                        provisional=True)
    return registro.guardar([alimento], hoy=HOY, texto="lentejas de prueba", sin_cambios=True)[0]


def _datos(fila, **cambios):
    return {"nombre": fila["nombre"], "cantidad": fila["cantidad"], "unidad": fila["unidad"], "kcal": fila["kcal"],
            **cambios}


def test_cambiar_las_kcal_quita_origen_y_provisional(entorno):  # R7 · R-8
    fila = _lentejas(entorno.registro)

    cambiado = entorno.registro.cambiar(fila["id"], _datos(fila, kcal=300), hoy=HOY)

    assert cambiado["kcal"] == 300
    assert cambiado["origen"] is None and cambiado["provisional"] is False
    assert entorno.registro.alimento(fila["id"])["origen"] is None


@pytest.mark.parametrize("cambios", [{"nombre": "Lentejas de la abuela de prueba"}, {"cantidad": 300},
                                     {"dia": MARTES.isoformat()}], ids=["nombre", "cantidad", "dia"])
def test_sin_tocar_las_kcal_las_marcas_se_quedan(entorno, cambios):  # R7
    fila = _lentejas(entorno.registro)

    cambiado = entorno.registro.cambiar(fila["id"], _datos(fila, **cambios), hoy=HOY)

    assert cambiado["origen"] == ORIGEN and cambiado["provisional"] is True


def test_la_ruta_de_cambiar_devuelve_la_fila_sin_marcas(entorno):  # R7
    fila = _lentejas(entorno.registro)

    r = entorno.cliente.post(f"/api/alimento/{fila['id']}", json=_datos(fila, kcal=300))

    assert r.status_code == 200, r.text
    (lentejas,) = r.json()["dia"]["alimentos"]
    assert lentejas["origen"] is None and lentejas["provisional"] is False and lentejas["kcal"] == 300
