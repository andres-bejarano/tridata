"""Weight of the day and objective of the day (unit 007): repositories and pure rules.

All weights, settings and dates are INVENTED. "Today" is Tuesday 2026-03-10. Settings, all
"since always": 175 cm, 40 years, hand-written basal 1600 kcal, 0.04 kcal/step, 10,000 steps.
"""
import sqlite3
from dataclasses import replace
from datetime import date, timedelta

import pytest

from tridata.nutricion import (
    DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroPeso, basal_del_dia, basal_mifflin, gasto_simple,
    media_7_dias, niveles_del_dia, objetivo_valido, peso_valido, proyeccion, resumen_del_dia, resumen_peso,
)

HOY = date(2026, 3, 10)
AYER = HOY - timedelta(days=1)


def d(dia):
    return date(2026, 3, dia)


def _ajustes(db, **extra):
    ajustes = Ajustes(db)
    datos = {"altura_cm": 175, "edad": 40, "basal_kcal": 1600, "kcal_por_paso": 0.04,
             "pasos_por_defecto": 10000, **extra}
    ajustes.cambiar({k: v for k, v in datos.items() if v is not None}, desde=DESDE_SIEMPRE)
    return ajustes


def _filas(db, tabla):
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]  # noqa: S608 - fixed names


# -- R1 (criterio portante): one weight per day, the last one wins -------------------

def test_un_peso_por_dia_vale_el_ultimo(tmp_path):
    db = tmp_path / "data.sqlite3"
    pesos = RegistroPeso(db)
    pesos.apuntar(HOY, 70.4)
    pesos.apuntar(HOY, 70.2)

    assert pesos.del_dia(HOY)["kg"] == 70.2
    assert _filas(db, "peso_dia") == 1
    assert [p["kg"] for p in pesos.entre(HOY, HOY)] == [70.2]


# -- R2: note with the weight; a past day leaves today alone --------------------------

def test_nota_y_peso_de_ayer(tmp_path):
    pesos = RegistroPeso(tmp_path / "data.sqlite3")
    pesos.apuntar(HOY, 70.2, "retención tras la cena")
    pesos.apuntar(AYER, 70.5, None, hoy=HOY)

    assert pesos.del_dia(HOY) | {"creado": None} == {
        "dia": "2026-03-10", "kg": 70.2, "nota": "retención tras la cena", "creado": None}
    assert pesos.del_dia(AYER)["kg"] == 70.5
    assert pesos.ultimo_hasta(AYER)["kg"] == 70.5
    assert pesos.ultimo_hasta(HOY)["kg"] == 70.2
    assert pesos.ultimo_hasta(d(1)) is None


# -- R3: 7-day mean with gaps ---------------------------------------------------------

def _pesos_r3(pesos):
    for dia, kg in ((3, 75.0), (4, 71.0), (5, 70.8), (7, 70.6), (8, 70.4), (9, 70.2), (10, 70.0)):
        pesos.apuntar(d(dia), kg)


def test_media_de_7_dias_con_huecos(tmp_path):
    pesos = RegistroPeso(tmp_path / "data.sqlite3")
    _pesos_r3(pesos)
    ventana = pesos.entre(HOY - timedelta(days=6), HOY)

    media = media_7_dias(ventana, HOY)
    assert media.kg == 70.5
    assert media.dias_con_peso == 6  # 03-03 (75,0) is outside the window
    assert media_7_dias(pesos.entre(d(1), HOY), HOY).kg == 70.5  # the function itself cuts the window


def test_sin_pesos_en_la_ventana_no_hay_media(tmp_path):
    pesos = RegistroPeso(tmp_path / "data.sqlite3")
    pesos.apuntar(d(1), 71.0)
    assert media_7_dias(pesos.entre(d(1), HOY), HOY) is None


# -- R4: Mifflin-St Jeor basal with the last weight, fallback to the hand-written one ----

def test_basal_mifflin_redondeado():
    assert basal_mifflin(70.0, 175, 40) == 1599
    assert basal_mifflin(70.2, 175, 40) == 1601


def test_basal_del_dia_con_el_peso_de_hoy_y_su_gasto(tmp_path):
    db = tmp_path / "data.sqlite3"
    vigentes = _ajustes(db).vigentes(HOY)

    basal = basal_del_dia(vigentes, [{"dia": "2026-03-10", "kg": 70.0}], HOY)
    assert (basal.kcal, basal.origen, basal.kg, basal.dia_del_peso) == (1599, "peso", 70.0, HOY)
    assert gasto_simple(replace(vigentes.config_gasto(), basal_kcal=basal.kcal)) == 1999
    assert basal_del_dia(vigentes, [{"dia": "2026-03-10", "kg": 70.2}], HOY).kcal == 1601


def test_basal_del_dia_usa_el_ultimo_anterior_y_no_uno_futuro(tmp_path):
    vigentes = _ajustes(tmp_path / "data.sqlite3").vigentes(HOY)
    pesos = [{"dia": "2026-03-08", "kg": 71.0}, {"dia": "2026-03-09", "kg": 70.0},
             {"dia": "2026-03-11", "kg": 69.0}]
    basal = basal_del_dia(vigentes, pesos, HOY)
    assert (basal.kcal, basal.origen, basal.dia_del_peso) == (1599, "peso", AYER)


def test_basal_del_dia_sin_peso_o_sin_perfil_es_el_de_ajustes(tmp_path):
    vigentes = _ajustes(tmp_path / "a.sqlite3").vigentes(HOY)
    basal = basal_del_dia(vigentes, [], HOY)
    assert (basal.kcal, basal.origen, basal.kg, basal.dia_del_peso) == (1600, "ajustes", None, None)

    sin_edad = _ajustes(tmp_path / "b.sqlite3", edad=None).vigentes(HOY)
    basal = basal_del_dia(sin_edad, [{"dia": "2026-03-10", "kg": 70.0}], HOY)
    assert (basal.kcal, basal.origen) == (1600, "ajustes")
    assert "edad" in basal.explicacion

    sin_altura = _ajustes(tmp_path / "c.sqlite3", altura_cm=None).vigentes(HOY)
    assert basal_del_dia(sin_altura, [{"dia": "2026-03-10", "kg": 70.0}], HOY).origen == "ajustes"


def test_explicacion_del_basal_dice_de_donde_sale(tmp_path):
    vigentes = _ajustes(tmp_path / "data.sqlite3").vigentes(HOY)
    con_peso = basal_del_dia(vigentes, [{"dia": "2026-03-10", "kg": 70.0}], HOY)
    assert "Mifflin" in con_peso.explicacion and "70" in con_peso.explicacion
    sin_peso = basal_del_dia(vigentes, [], HOY)
    assert "a mano" in sin_peso.explicacion and "Ajustes" in sin_peso.explicacion


# -- R5: projection towards the weight target -------------------------------------------

def test_proyeccion_hacia_el_objetivo():
    p = proyeccion(70.5, 68.0, date(2026, 5, 19), HOY)
    assert p == {"objetivo_kg": 68.0, "fecha": "2026-05-19", "dias": 70, "faltan_kg": 2.5, "kg_por_semana": 0.25}


def test_resumen_de_peso_sin_objetivo_no_da_numeros(tmp_path):
    db = tmp_path / "data.sqlite3"
    vigentes = _ajustes(db).vigentes(HOY)
    pesos = RegistroPeso(db)
    _pesos_r3(pesos)
    ventana = pesos.entre(HOY - timedelta(days=6), HOY)
    basal = basal_del_dia(vigentes, ventana, HOY)

    bloque = resumen_peso(ventana, HOY, vigentes, basal)
    assert bloque["del_dia"]["kg"] == 70.0
    assert bloque["media_7_dias"] == {"kg": 70.5, "dias_con_peso": 6}
    assert bloque["basal"]["kcal"] == 1599 and bloque["basal"]["origen"] == "peso"
    assert bloque["proyeccion"] is None
    assert bloque["proyeccion_texto"] == "sin objetivo de peso"


def test_resumen_de_peso_con_objetivo(tmp_path):
    db = tmp_path / "data.sqlite3"
    vigentes = _ajustes(db, objetivo_peso={"kg": 68.0, "fecha": "2026-05-19"}).vigentes(HOY)
    pesos = RegistroPeso(db)
    _pesos_r3(pesos)
    ventana = pesos.entre(HOY - timedelta(days=6), HOY)
    bloque = resumen_peso(ventana, HOY, vigentes, basal_del_dia(vigentes, ventana, HOY))
    assert bloque["proyeccion"]["faltan_kg"] == 2.5
    assert bloque["proyeccion"]["kg_por_semana"] == 0.25


def test_resumen_de_peso_sin_pesos_lo_dice(tmp_path):
    vigentes = _ajustes(tmp_path / "data.sqlite3").vigentes(HOY)
    bloque = resumen_peso([], HOY, vigentes, basal_del_dia(vigentes, [], HOY))
    assert bloque["del_dia"] is None and bloque["media_7_dias"] is None
    assert "sin" in bloque["media_texto"]
    assert bloque["basal"]["origen"] == "ajustes"


# -- R7: objective of the day: the last one wins; levels and steps go into the margin ------

def test_objetivo_del_dia_vale_el_ultimo(tmp_path):
    db = tmp_path / "data.sqlite3"
    objetivos = ObjetivosDia(db)
    objetivos.guardar(HOY, {"tipo": "descanso", "porcentaje": None, "niveles": None, "entreno": None, "pasos": None})
    objetivos.guardar(HOY, {"tipo": "entreno", "porcentaje": -15, "niveles": [-15, 0],
                            "entreno": "30 min suaves", "pasos": 8000})

    obj = objetivos.del_dia(HOY)
    assert {k: obj[k] for k in ("tipo", "porcentaje", "niveles", "entreno", "pasos")} == {
        "tipo": "entreno", "porcentaje": -15, "niveles": [-15, 0], "entreno": "30 min suaves", "pasos": 8000}
    assert _filas(db, "objetivo_dia") == 1
    assert objetivos.del_dia(AYER) is None


def test_objetivo_sin_porcentaje_guarda_solo_el_tipo(tmp_path):
    objetivos = ObjetivosDia(tmp_path / "data.sqlite3")
    objetivos.guardar(HOY, {"tipo": "descanso"})
    obj = objetivos.del_dia(HOY)
    assert obj["tipo"] == "descanso"
    assert obj["porcentaje"] is None and obj["niveles"] is None and obj["pasos"] is None


def test_margen_con_el_objetivo_del_dia(tmp_path):
    vigentes = _ajustes(tmp_path / "data.sqlite3").vigentes(HOY)
    objetivo = {"tipo": "entreno", "porcentaje": -15, "niveles": [-15, 0], "entreno": "30 min suaves", "pasos": 8000}
    basal = basal_del_dia(vigentes, [], HOY)  # no weight: hand-written 1600

    resumen = resumen_del_dia([], HOY, vigentes.config_gasto(), objetivo_dia=objetivo, basal=basal)
    assert resumen["gasto"]["kcal"] == 1920
    assert resumen["gasto"]["pasos"] == 8000
    assert [(m["nivel"], m["kcal"], m.get("objetivo", False)) for m in resumen["margenes"]] == [
        (-15, 1632, True), (0, 1920, False)]
    assert resumen["gasto"]["entreno_previsto"] == "30 min suaves"
    assert "no suma" in resumen["gasto"]["explicacion"]
    assert resumen["objetivo_dia"]["tipo"] == "entreno"


def test_dia_sin_objetivo_sigue_como_antes(tmp_path):
    vigentes = _ajustes(tmp_path / "data.sqlite3").vigentes(HOY)
    resumen = resumen_del_dia([], HOY, vigentes.config_gasto(), objetivo_dia=None, basal=basal_del_dia(vigentes, [], HOY))
    assert resumen["gasto"]["kcal"] == 2000 and resumen["gasto"]["pasos"] == 10000
    assert [m["nivel"] for m in resumen["margenes"]] == [-10, 0, 10]
    assert not any(m.get("objetivo") for m in resumen["margenes"])
    assert resumen["objetivo_dia"] is None


def test_niveles_del_dia():
    assert niveles_del_dia(None) == ((-10, 0, 10), None)
    assert niveles_del_dia({"tipo": "descanso", "porcentaje": None, "niveles": None}) == ((-10, 0, 10), None)
    assert niveles_del_dia({"porcentaje": -15, "niveles": [0, -15]}) == ((-15, 0), -15)
    assert niveles_del_dia({"porcentaje": -15, "niveles": None}) == ((-15, -10, 0, 10), -15)


# -- R11: invalid pieces are rejected with a message that says which -----------------------

@pytest.mark.parametrize("kg", [0, 300, 29.9, 250.1])
def test_peso_fuera_de_rango(tmp_path, kg):
    pesos = RegistroPeso(tmp_path / "data.sqlite3")
    with pytest.raises(ValueError, match="el peso tiene que estar entre 30 y 250 kg"):
        pesos.apuntar(HOY, kg)
    with pytest.raises(ValueError, match="el peso tiene que estar entre 30 y 250 kg"):
        peso_valido({"kg": kg, "dia": HOY.isoformat()}, HOY)
    assert pesos.entre(d(1), HOY) == []


def test_peso_de_un_dia_futuro_o_no_numerico(tmp_path):
    with pytest.raises(ValueError, match="futuro|aún no ha llegado"):
        peso_valido({"kg": 70.2, "dia": "2026-03-11"}, HOY)
    with pytest.raises(ValueError, match="peso"):
        peso_valido({"kg": "mucho", "dia": "2026-03-10"}, HOY)
    with pytest.raises(ValueError, match="día"):
        peso_valido({"kg": 70.2, "dia": "nunca"}, HOY)
    pesos = RegistroPeso(tmp_path / "data.sqlite3")
    with pytest.raises(ValueError):
        pesos.apuntar(date(2026, 3, 11), 70.2, hoy=HOY)
    assert peso_valido({"kg": "70,2", "dia": "2026-03-10", "nota": " algo "}, HOY) == (HOY, 70.2, "algo")


@pytest.mark.parametrize(("datos", "palabra"), [
    ({"tipo": "entreno", "porcentaje": "mucho"}, "%"),
    ({"tipo": "entreno", "pasos": "muchos"}, "pasos"),
    ({"tipo": "entreno", "pasos": 8000.5}, "pasos"),
    ({"tipo": "entreno", "niveles": ["menos quince"]}, "niveles"),
    ({"tipo": "entreno", "porcentaje": 80}, "%"),
])
def test_objetivo_no_valido(datos, palabra):
    with pytest.raises(ValueError, match=palabra):
        objetivo_valido(datos)


def test_objetivo_valido_normaliza():
    assert objetivo_valido({"tipo": " Descanso ", "porcentaje": "-15", "niveles": ["-15", 0], "pasos": "8000"}) == {
        "tipo": "descanso", "porcentaje": -15, "niveles": [-15, 0], "entreno": None, "pasos": 8000}
    assert objetivo_valido({"tipo": None, "porcentaje": None, "niveles": [], "entreno": "", "pasos": None}) is None
