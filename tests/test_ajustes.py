"""Settings with a date from which they apply (unit 005): `nutricion.Ajustes` over a temporary
SQLite. Every day is computed with what was in force THAT day. All values are made up.
"""
import sqlite3
from dataclasses import fields
from datetime import date

import pytest

from tridata.nutricion import (
    DESDE_SIEMPRE, Ajustes, AjustesDelDia, Carrera, ConfigGasto, ObjetivoPeso, gasto_simple,
)

HOY = date(2026, 3, 4)


def d(dia, mes=3):
    return date(2026, mes, dia)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "data.sqlite3"


@pytest.fixture
def ajustes(db):
    a = Ajustes(db)
    a.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000}, desde=DESDE_SIEMPRE)
    return a


def _filas(db, clave):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT valor, desde FROM ajuste WHERE clave = ? ORDER BY id", (clave,)).fetchall()


# -- R3, R4, R5: what is in force on a date ---------------------------------------------

def test_cada_dia_con_lo_vigente_ese_dia(ajustes):  # R3, criterio portante
    ajustes.cambiar({"basal_kcal": 1700}, desde=d(10))
    ajustes.cambiar({"objetivo_pct": -20}, desde=DESDE_SIEMPRE)
    ajustes.cambiar({"objetivo_pct": -15}, desde=d(11))

    assert ajustes.vigentes(d(9)).basal_kcal == 1600
    assert gasto_simple(ajustes.vigentes(d(9)).config_gasto()) == 2000
    assert ajustes.vigentes(d(10)).basal_kcal == 1700
    assert gasto_simple(ajustes.vigentes(d(10)).config_gasto()) == 2100
    assert ajustes.vigentes(d(9)).objetivo_pct == -20
    assert ajustes.vigentes(d(10)).objetivo_pct == -20
    assert ajustes.vigentes(d(11)).objetivo_pct == -15
    assert ajustes.vigentes(d(11)).motivo_objetivo == "ajuste"


def test_un_cambio_futuro_no_cambia_hoy(ajustes):  # R4
    ajustes.cambiar({"basal_kcal": 1700}, desde=d(10))
    assert ajustes.vigentes(HOY).basal_kcal == 1600
    assert ajustes.cambios_futuros(HOY) == [{"clave": "basal_kcal", "valor": 1700, "desde": "2026-03-10"}]
    assert ajustes.cambios_futuros(d(10)) == []


def test_misma_fecha_vale_el_ultimo_guardado(ajustes):  # R5
    ajustes.cambiar({"basal_kcal": 1650}, desde=d(10))
    ajustes.cambiar({"basal_kcal": 1700}, desde=d(10))
    assert ajustes.vigentes(d(10)).basal_kcal == 1700
    assert ajustes.vigentes(d(9)).basal_kcal == 1600
    assert ajustes.cambios_futuros(HOY) == [{"clave": "basal_kcal", "valor": 1700, "desde": "2026-03-10"}]


def test_vigentes_dice_desde_cuando(ajustes):
    ajustes.cambiar({"pasos_por_defecto": 8000}, desde=HOY)
    hoy = ajustes.vigentes(HOY)
    assert hoy.desde["pasos_por_defecto"] == HOY
    assert hoy.desde["basal_kcal"] == DESDE_SIEMPRE
    assert "edad" not in hoy.desde


# -- R1: history, never overwritten ------------------------------------------------------

def test_el_historial_conserva_el_valor_anterior(ajustes, db):  # R1
    ajustes.cambiar({"pasos_por_defecto": 8000}, desde=HOY)

    assert _filas(db, "pasos_por_defecto") == [("10000", "0001-01-01"), ("8000", "2026-03-04")]
    assert ajustes.vigentes(d(3)).pasos_por_defecto == 10000
    assert ajustes.vigentes(HOY).pasos_por_defecto == 8000
    assert gasto_simple(ajustes.vigentes(HOY).config_gasto()) == 1920
    assert [(f["clave"], f["valor"], f["desde"]) for f in ajustes.historial("pasos_por_defecto")] == [
        ("pasos_por_defecto", 10000, "0001-01-01"), ("pasos_por_defecto", 8000, "2026-03-04"),
    ]


def test_cambiar_varios_datos_es_todo_o_nada(ajustes, db):
    with pytest.raises(ValueError, match="basal"):
        ajustes.cambiar({"pasos_por_defecto": 8000, "basal_kcal": 0}, desde=HOY)
    assert len(_filas(db, "pasos_por_defecto")) == 1
    assert ajustes.vigentes(HOY).pasos_por_defecto == 10000


# -- R7: races ---------------------------------------------------------------------------

def _plan(ajustes, dia):
    v = ajustes.vigentes(d(dia))
    return v.objetivo_pct, v.motivo_objetivo


def test_plan_con_carreras(ajustes):  # R7
    ajustes.cambiar({"objetivo_pct": -15}, desde=DESDE_SIEMPRE)
    carrera = ajustes.anadir_carrera("Triatlón Sprint Prueba", d(20), 2)
    carrera_motivo = "carrera Triatlón Sprint Prueba"

    for dia in (18, 19, 20):
        assert _plan(ajustes, dia) == (0, carrera_motivo)
    for dia in (17, 21):
        assert _plan(ajustes, dia) == (-15, "ajuste")

    ajustes.cambiar_carrera(carrera["id"], "Triatlón Sprint Prueba", d(22), 2)
    for dia in (20, 21, 22):
        assert _plan(ajustes, dia) == (0, carrera_motivo)
    for dia in (18, 19):
        assert _plan(ajustes, dia) == (-15, "ajuste")
    assert [c.fecha for c in ajustes.vigentes(d(4)).carreras] == [d(22)]

    ajustes.quitar_carrera(carrera["id"])
    for dia in range(17, 24):
        assert _plan(ajustes, dia) == (-15, "ajuste")
    assert ajustes.vigentes(d(4)).carreras == ()


def test_carreras_desde_ese_dia_en_adelante(ajustes):
    ajustes.anadir_carrera("Acuatlón Prueba", d(1), 0)
    ajustes.anadir_carrera("Duatlón Prueba", d(15, 4), 3)
    assert [c.nombre for c in ajustes.vigentes(d(1)).carreras] == ["Acuatlón Prueba", "Duatlón Prueba"]
    assert [c.nombre for c in ajustes.vigentes(d(2)).carreras] == ["Duatlón Prueba"]
    assert ajustes.vigentes(d(1)).objetivo_pct == 0  # race day is maintenance even without a plan
    assert ajustes.vigentes(d(2)).objetivo_pct is None
    assert ajustes.vigentes(d(2)).motivo_objetivo is None


def test_cambiar_o_quitar_una_carrera_que_no_existe(ajustes):
    with pytest.raises(KeyError):
        ajustes.quitar_carrera(99)
    with pytest.raises(KeyError):
        ajustes.cambiar_carrera(99, "X", d(20), 1)


@pytest.mark.parametrize(("nombre", "fecha", "dias"), [
    ("", d(20), 2), ("   ", d(20), 2), ("Prueba", "nunca", 2), ("Prueba", d(20), -1),
    ("Prueba", d(20), 15), ("Prueba", d(20), 1.5), ("Prueba", d(20), "dos"),
])
def test_carrera_invalida_no_se_guarda(ajustes, nombre, fecha, dias):
    with pytest.raises(ValueError):
        ajustes.anadir_carrera(nombre, fecha, dias)
    assert ajustes.carreras() == []


# -- R6: the healthy limit is never assumed -----------------------------------------------

@pytest.mark.parametrize("objetivo", [-15, 15])
def test_objetivo_sin_limite_pide_preguntar(ajustes, objetivo):  # R6
    r = ajustes.cambiar({"objetivo_pct": objetivo}, desde=HOY)
    assert r["preguntar_limite"] is True
    v = ajustes.vigentes(HOY)
    assert v.objetivo_pct == objetivo
    assert v.limite_saludable_pct is None


def test_objetivo_cero_no_pregunta(ajustes):  # R6
    assert ajustes.cambiar({"objetivo_pct": 0}, desde=HOY)["preguntar_limite"] is False


def test_con_limite_no_pregunta_y_vale_desde_su_fecha(ajustes):  # R6
    ajustes.cambiar({"objetivo_pct": -15}, desde=HOY)
    ajustes.cambiar({"limite_saludable_pct": 25}, desde=d(6))
    assert ajustes.vigentes(d(5)).limite_saludable_pct is None
    assert ajustes.vigentes(d(6)).limite_saludable_pct == 25
    assert ajustes.cambiar({"objetivo_pct": 10}, desde=d(7))["preguntar_limite"] is False
    assert ajustes.cambiar({"objetivo_pct": -10, "limite_saludable_pct": 20}, desde=HOY)["preguntar_limite"] is False


# -- R8, R9: validation per key ------------------------------------------------------------

def test_objetivo_de_peso(ajustes):  # R8
    ajustes.cambiar({"objetivo_peso": {"kg": "72,0", "fecha": "2026-06-30"}}, desde=HOY)
    assert ajustes.vigentes(HOY).objetivo_peso == ObjetivoPeso(kg=72.0, fecha=date(2026, 6, 30))
    assert ajustes.vigentes(d(3)).objetivo_peso is None


@pytest.mark.parametrize(("datos", "motivo"), [
    ({"basal_kcal": 0}, "basal"),
    ({"basal_kcal": 4001}, "basal"),
    ({"basal_kcal": "mucho"}, "basal"),
    ({"basal_kcal": True}, "basal"),
    ({"basal_kcal": None}, "basal"),
    ({"pasos_por_defecto": -1}, "pasos"),
    ({"pasos_por_defecto": 50001}, "pasos"),
    ({"pasos_por_defecto": 8000.5}, "pasos"),
    ({"kcal_por_paso": 0.005}, "paso"),
    ({"kcal_por_paso": 0.3}, "paso"),
    ({"edad": 9}, "edad"),
    ({"edad": 101}, "edad"),
    ({"altura_cm": 99}, "altura"),
    ({"altura_cm": 231}, "altura"),
    ({"objetivo_pct": -51}, "objetivo"),
    ({"objetivo_pct": 50.5}, "objetivo"),
    ({"limite_saludable_pct": 0}, "límite"),
    ({"limite_saludable_pct": 51}, "límite"),
    ({"objetivo_peso": {"kg": 29, "fecha": "2026-06-30"}}, "peso"),
    ({"objetivo_peso": {"kg": 251, "fecha": "2026-06-30"}}, "peso"),
    ({"objetivo_peso": {"kg": 72, "fecha": "30/06/2026"}}, "fecha"),
    ({"objetivo_peso": {"kg": 72, "fecha": "2026-03-03"}}, "anterior"),
    ({"objetivo_peso": 72}, "peso"),
    ({"color_favorito": 3}, "color_favorito"),
    ({}, "nada"),
])
def test_valor_imposible_se_rechaza_y_no_guarda(ajustes, db, datos, motivo):  # R9
    with sqlite3.connect(db) as conn:
        antes = conn.execute("SELECT COUNT(*) FROM ajuste").fetchone()[0]
    with pytest.raises(ValueError, match=motivo):
        ajustes.cambiar(datos, desde=HOY)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ajuste").fetchone()[0] == antes


@pytest.mark.parametrize("desde", ["nunca", "2026-02-30", None, 20260304])
def test_fecha_desde_no_valida(ajustes, desde):  # R9
    with pytest.raises(ValueError, match="fecha"):
        ajustes.cambiar({"basal_kcal": 1700}, desde=desde)
    assert ajustes.vigentes(HOY).basal_kcal == 1600


def test_acepta_numeros_escritos_con_coma(ajustes):
    ajustes.cambiar({"kcal_por_paso": "0,05", "edad": "40", "altura_cm": "178,5"}, desde="2026-03-04")
    v = ajustes.vigentes(HOY)
    assert (v.kcal_por_paso, v.edad, v.altura_cm) == (0.05, 40, 178.5)


# -- R11: the one door for units 007 and 008 ------------------------------------------------

def test_vigentes_completos(ajustes):  # R11
    ajustes.cambiar({
        "edad": 40, "altura_cm": 178, "limite_saludable_pct": 25, "objetivo_pct": -15,
        "objetivo_peso": {"kg": 72, "fecha": "2026-06-30"},
    }, desde=DESDE_SIEMPRE)
    ajustes.anadir_carrera("Triatlón Sprint Prueba", d(20), 2)
    v = ajustes.vigentes(HOY)

    assert isinstance(v, AjustesDelDia)
    assert {f.name for f in fields(v)} >= {
        "dia", "edad", "altura_cm", "pasos_por_defecto", "basal_kcal", "kcal_por_paso",
        "limite_saludable_pct", "objetivo_pct", "motivo_objetivo", "objetivo_peso", "carreras",
    }
    assert (v.dia, v.edad, v.altura_cm, v.pasos_por_defecto, v.basal_kcal, v.kcal_por_paso) == (
        HOY, 40, 178, 10000, 1600, 0.04)
    assert (v.limite_saludable_pct, v.objetivo_pct, v.motivo_objetivo) == (25, -15, "ajuste")
    assert v.objetivo_peso == ObjetivoPeso(kg=72.0, fecha=date(2026, 6, 30))
    (carrera,) = v.carreras
    assert isinstance(carrera, Carrera)
    assert (carrera.nombre, carrera.fecha, carrera.dias_mantenimiento) == ("Triatlón Sprint Prueba", d(20), 2)
    assert v.config_gasto() == ConfigGasto(basal_kcal=1600, kcal_por_paso=0.04, pasos=10000)
    with pytest.raises(AttributeError):
        v.basal_kcal = 1  # frozen


def test_vigentes_incompletos(db):  # R11
    v = Ajustes(db).vigentes(HOY)
    assert (v.edad, v.altura_cm, v.pasos_por_defecto, v.basal_kcal, v.kcal_por_paso) == (None,) * 5
    assert (v.limite_saludable_pct, v.objetivo_pct, v.motivo_objetivo, v.objetivo_peso) == (None,) * 4
    assert v.carreras == ()
    with pytest.raises(ValueError, match="basal"):
        v.config_gasto()


def test_sin_pasos_por_defecto_usa_la_constante(db):  # R11
    a = Ajustes(db)
    a.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
    assert a.vigentes(HOY).pasos_por_defecto is None
    assert a.vigentes(HOY).config_gasto() == ConfigGasto(basal_kcal=1600, kcal_por_paso=0.04, pasos=10000)


# -- R10: seeded once from .env -------------------------------------------------------------

def test_semilla_desde_el_entorno_una_sola_vez(db):  # R10
    a = Ajustes(db)
    assert a.sembrar_desde_entorno({"TRIDATA_BASAL_KCAL": "1500", "TRIDATA_KCAL_POR_PASO": "0,05"}) is True
    v = a.vigentes(DESDE_SIEMPRE)
    assert (v.basal_kcal, v.kcal_por_paso) == (1500, 0.05)
    assert v.desde["basal_kcal"] == DESDE_SIEMPRE

    assert a.sembrar_desde_entorno({"TRIDATA_BASAL_KCAL": "1800", "TRIDATA_KCAL_POR_PASO": "0,05"}) is False
    assert Ajustes(db).vigentes(HOY).basal_kcal == 1500
    assert len(_filas(db, "basal_kcal")) == 1


def test_semilla_sin_entorno_dice_que_falta(db):  # R10
    with pytest.raises(ValueError, match="TRIDATA_BASAL_KCAL"):
        Ajustes(db).sembrar_desde_entorno({})
    assert _filas(db, "basal_kcal") == []


def test_semilla_no_hace_falta_si_ya_hay_ajustes(ajustes):  # R10
    assert ajustes.sembrar_desde_entorno({}) is False
