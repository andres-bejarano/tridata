"""Unit 082 (REC-34 of `apuntar-comida`): the supplements of the day (coffee and B12) with their hour, the milk of the
coffees spread among them, and the hour of each section of the day — `nutricion/suplementacion.py`.

Unit level: the pure rules (hour and form checks, the spread of the milk, the summary of the day, the typical hours)
and `Suplementacion` over a temporary SQLite next to `RegistroComidas`. Every value is invented.
Plans: `apuntar-comida` R-225..R-231, G-60, G-61, G-62, S-79, C-280, C-281, C-284, C-285, C-286.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from tridata.nutricion import Alimento, RegistroComidas
from tridata.nutricion.suplementacion import (
    FORMAS, HORAS_TIPICAS, Suplementacion, horas_de_secciones, repartir_leche, resumen_dia, validar_hora,
)

HOY = date(2026, 10, 21)
AHORA = datetime(2026, 10, 21, 16, 40)


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "data.sqlite3"


@pytest.fixture
def registro(db) -> RegistroComidas:
    registro = RegistroComidas(db)
    registro.guardar([Alimento(nombre="arroz inventado", dia=HOY, cantidad=300, unidad="g", kcal=1200)], hoy=HOY,
                     texto="arroz", sin_cambios=True)
    return registro


@pytest.fixture
def sup(db, registro) -> Suplementacion:
    return Suplementacion(db)


def _volcado(db: Path) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT * FROM alimento_apuntado ORDER BY id").fetchall()


# -- R1 (the load-bearing one): a dose is kept apart, with its hour and mg, without touching the foods -----------------

def test_una_toma_se_guarda_aparte_sin_tocar_los_alimentos(sup, registro, db):  # R1 · R-225, G-60, C-280
    antes = _volcado(db)
    toma = sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    assert (toma["que"], toma["forma"], toma["hora"], toma["mg"], toma["mg_estimado"]) == ("cafe", "doble", "10:00",
                                                                                          130, True)
    assert _volcado(db) == antes  # the day keeps its 1.200 kcal and its one food
    assert sum(a["kcal"] for a in registro.apuntes_del_dia(HOY)) == 1200
    assert [t["id"] for t in sup.tomas_del_dia(HOY)] == [toma["id"]]


def test_la_toma_no_tiene_kcal(sup):  # R1 · G-60: nothing with kcal in a dose
    toma = sup.apuntar(HOY, {"que": "b12", "hora": "09:00", "dosis": "1000 µg"}, hoy=HOY, ahora=AHORA)
    assert "kcal" not in toma
    assert (toma["que"], toma["dosis"], toma["mg"]) == ("b12", "1000 µg", None)


def test_mg_a_mano_no_son_estimados(sup):  # R1: «marcados estimados o a mano»
    toma = sup.apuntar(HOY, {"que": "cafe", "forma": "expreso", "hora": "08:15", "mg": 70, "mg_estimado": False},
                       hoy=HOY, ahora=AHORA)
    assert toma["mg_estimado"] is False


@pytest.mark.parametrize("forma, detalle", [
    ("expreso", {}), ("doble", {}), ("ingles", {}),
    ("italiana", {"gramos": 20, "fraccion": 0.5}),
    ("soluble", {"cucharadas": 2, "gramos": 2}),
])
def test_las_cinco_formas_del_cafe(sup, forma, detalle):  # R1 · S-75
    toma = sup.apuntar(HOY, {"que": "cafe", "forma": forma, "hora": "10:00", "mg": 80, **detalle}, hoy=HOY,
                       ahora=AHORA)
    assert toma["forma"] == forma
    for clave, valor in detalle.items():
        assert toma[clave] == valor
    assert set(FORMAS) == {"expreso", "doble", "ingles", "italiana", "soluble"}


# -- R2, R5: the summary of the day ------------------------------------------------------------------------------------

def test_resumen_ordenado_por_hora_total_y_ultima(sup):  # R2 · R-226, C-281
    sup.apuntar(HOY, {"que": "cafe", "forma": "italiana", "gramos": 20, "fraccion": 0.5, "hora": "17:30", "mg": 90},
                hoy=HOY, ahora=AHORA)
    sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    sup.apuntar(HOY, {"que": "b12", "hora": "9:00"}, hoy=HOY, ahora=AHORA)
    r = sup.resumen(HOY)
    assert [(t["hora"], t["que"], t["forma"]) for t in r["tomas"]] == [
        ("09:00", "b12", None), ("10:00", "cafe", "doble"), ("17:30", "cafe", "italiana")]
    assert r["cafeina_mg"] == 220
    assert r["ultima"] == "17:30"


def test_la_ultima_es_la_ultima_con_cafeina(sup):  # R2: B12 after the last coffee does not count
    sup.apuntar(HOY, {"que": "cafe", "forma": "expreso", "hora": "11:00", "mg": 70}, hoy=HOY, ahora=AHORA)
    sup.apuntar(HOY, {"que": "b12", "hora": "20:00"}, hoy=HOY, ahora=AHORA)
    assert sup.resumen(HOY)["ultima"] == "11:00"


def test_resumen_sin_tomas(sup):  # R2: the block folded
    r = sup.resumen(HOY)
    assert (r["tomas"], r["cafeina_mg"], r["ultima"], r["leche"]) == ([], 0, None, None)


def test_horas_tipicas_y_puestas():  # R5 · S-79, C-286
    assert HORAS_TIPICAS == {"desayuno": "08:00", "media_manana": "11:00", "comida": "14:30", "merienda": "18:00",
                             "cena": "21:30"}
    horas = horas_de_secciones({"cena": "22:15"})
    assert horas["cena"] == {"hora": "22:15", "aproximada": False}
    assert horas["desayuno"] == {"hora": "08:00", "aproximada": True}
    assert horas["carrera"] == {"hora": None, "aproximada": False}  # no typical hour
    assert "" not in horas  # «Sin sección» has no hour


def test_poner_hora_de_seccion_no_toca_alimentos(sup, db):  # R5 · R-229, G-62
    antes = _volcado(db)
    sup.poner_hora_seccion(HOY, "cena", "22:15")
    assert sup.horas_del_dia(HOY)["cena"] == {"hora": "22:15", "aproximada": False}
    sup.poner_hora_seccion(HOY, "cena", "22:30")  # changed again, the same row
    assert sup.horas_del_dia(HOY)["cena"]["hora"] == "22:30"
    sup.poner_hora_seccion(HOY, "cena", None)  # cleared: the typical one again
    assert sup.horas_del_dia(HOY)["cena"] == {"hora": "21:30", "aproximada": True}
    assert _volcado(db) == antes


def test_la_hora_de_una_seccion_es_de_su_dia(sup):  # R5: «se guarda para ese día»
    sup.poner_hora_seccion(HOY, "cena", "22:15")
    assert sup.horas_del_dia(date(2026, 10, 22))["cena"]["aproximada"] is True


# -- R3: changing and deleting -----------------------------------------------------------------------------------------

def test_cambiar_hora_forma_mg_y_solo(sup):  # R3 · R-227, C-282
    toma = sup.apuntar(HOY, {"que": "cafe", "forma": "italiana", "hora": "17:30", "mg": 90}, hoy=HOY, ahora=AHORA)
    cambiada = sup.cambiar(toma["id"], {"hora": "19:00"})
    assert (cambiada["hora"], cambiada["mg"], cambiada["mg_estimado"]) == ("19:00", 90, True)
    cambiada = sup.cambiar(toma["id"], {"forma": "doble", "mg": 120, "solo": True})
    assert (cambiada["forma"], cambiada["mg"], cambiada["mg_estimado"], cambiada["solo"]) == ("doble", 120, False,
                                                                                              True)
    assert sup.resumen(HOY)["ultima"] == "19:00"


def test_borrar_una_toma(sup):  # R3
    toma = sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    assert sup.borrar(toma["id"])["id"] == toma["id"]
    assert sup.tomas_del_dia(HOY) == []
    with pytest.raises(KeyError):
        sup.borrar(toma["id"])


def test_cambiar_una_que_no_existe(sup):
    with pytest.raises(KeyError):
        sup.cambiar(999, {"hora": "10:00"})


# -- R4: the milk spread among the coffees (G-61) ----------------------------------------------------------------------

def test_repartir_leche_a_partes_iguales():  # R4 · G-61, C-284, C-285
    cafes = [{"id": 1, "que": "cafe", "solo": False}, {"id": 2, "que": "cafe", "solo": False},
             {"id": 3, "que": "cafe", "solo": True}, {"id": 4, "que": "b12", "solo": False}]
    assert repartir_leche(250, cafes) == {1: 125, 2: 125}
    cafes.append({"id": 5, "que": "cafe", "solo": False})
    reparto = repartir_leche(250, cafes)
    assert set(reparto) == {1, 2, 5} and all(abs(ml - 250 / 3) < 1e-9 for ml in reparto.values())
    assert repartir_leche(250, [{"id": 3, "que": "cafe", "solo": True}]) == {}


def _leche(sup, registro, ml=250, kcal=115) -> dict:
    (leche,) = registro.guardar([Alimento(nombre="leche de prueba", dia=HOY, cantidad=ml, unidad="ml", kcal=kcal)],
                                hoy=HOY, texto="leche", sin_cambios=False)
    sup.marcar_leche(HOY, leche["id"])
    return leche


def test_la_leche_se_reparte_y_cuenta_una_vez(sup, registro):  # R4 · C-284
    a = sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    b = sup.apuntar(HOY, {"que": "cafe", "forma": "expreso", "hora": "12:00", "mg": 70}, hoy=HOY, ahora=AHORA)
    c = sup.apuntar(HOY, {"que": "cafe", "forma": "expreso", "hora": "15:00", "mg": 70, "solo": True}, hoy=HOY,
                    ahora=AHORA)
    _leche(sup, registro)
    r = sup.resumen(HOY)
    leche_de = {t["id"]: t["leche_ml"] for t in r["tomas"]}
    assert leche_de == {a["id"]: 125, b["id"]: 125, c["id"]: None}
    assert r["leche"]["ml"] == 250 and r["leche"]["kcal"] == 115 and r["leche"]["repartida"] is True
    assert sum(x["kcal"] for x in registro.apuntes_del_dia(HOY)) == 1200 + 115  # one time only


def test_un_tercer_cafe_rehace_el_reparto_sin_cambiar_kcal(sup, registro):  # R4 · C-285
    for hora in ("10:00", "12:00"):
        sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": hora, "mg": 130}, hoy=HOY, ahora=AHORA)
    _leche(sup, registro)
    sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "16:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    r = sup.resumen(HOY)
    assert [round(t["leche_ml"]) for t in r["tomas"]] == [83, 83, 83]
    assert sum(x["kcal"] for x in registro.apuntes_del_dia(HOY)) == 1315


def test_marcar_solo_o_quitar_un_cafe_rehace_el_reparto(sup, registro):  # R4
    a = sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    b = sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "12:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    _leche(sup, registro)
    sup.cambiar(a["id"], {"solo": True})
    assert {t["id"]: t["leche_ml"] for t in sup.resumen(HOY)["tomas"]} == {a["id"]: None, b["id"]: 250}
    sup.borrar(b["id"])
    r = sup.resumen(HOY)
    assert r["leche"]["repartida"] is False  # «a lo largo del día»
    assert [t["leche_ml"] for t in r["tomas"]] == [None]


def test_sin_cafes_la_leche_queda_a_lo_largo_del_dia(sup, registro):  # R4 · G-61
    _leche(sup, registro)
    r = sup.resumen(HOY)
    assert r["tomas"] == [] and r["leche"]["repartida"] is False and r["leche"]["ml"] == 250


def test_borrar_la_leche_quita_el_reparto(sup, registro):  # R4
    sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    leche = _leche(sup, registro)
    registro.borrar(leche["id"])
    r = sup.resumen(HOY)
    assert r["leche"] is None and r["tomas"][0]["leche_ml"] is None


def test_resumen_dia_es_puro():  # R2, R4: the same summary from plain data
    tomas = [{"id": 1, "que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130, "solo": False},
             {"id": 2, "que": "cafe", "forma": "doble", "hora": None, "mg": 100, "solo": False}]
    r = resumen_dia(tomas, {"alimento_id": 9, "ml": 200, "kcal": 92})
    assert [t["id"] for t in r["tomas"]] == [1, 2]  # without an hour, at the end
    assert r["cafeina_mg"] == 230 and r["ultima"] == "10:00"
    assert [t["leche_ml"] for t in r["tomas"]] == [100, 100]


# -- R7: a dose without an hour ----------------------------------------------------------------------------------------

def test_hoy_sin_hora_se_queda_con_la_de_ahora(sup):  # R7 · R-231, C-288
    toma = sup.apuntar(HOY, {"que": "cafe", "forma": "soluble", "cucharadas": 2, "gramos": 2, "mg": 60}, hoy=HOY,
                       ahora=AHORA)
    assert toma["hora"] == "16:40"


def test_otro_dia_sin_hora_no_se_apunta(sup):  # R7 · S-80
    with pytest.raises(ValueError, match="falta la hora"):
        sup.apuntar(date(2026, 10, 20), {"que": "cafe", "forma": "doble", "mg": 130}, hoy=HOY, ahora=AHORA)
    assert sup.tomas_del_dia(date(2026, 10, 20)) == []


def test_varias_todas_o_ninguna(sup):  # R7: several doses, all or none
    with pytest.raises(ValueError, match="falta la hora"):
        sup.apuntar_varias(date(2026, 10, 20), [{"que": "b12", "hora": "09:00"}, {"que": "cafe", "forma": "doble"}],
                           hoy=HOY, ahora=AHORA)
    assert sup.tomas_del_dia(date(2026, 10, 20)) == []
    hechas = sup.apuntar_varias(HOY, [{"que": "b12", "hora": "09:00"}, {"que": "cafe", "forma": "doble", "mg": 130,
                                                                        "hora": "10:00"}], hoy=HOY, ahora=AHORA)
    assert [t["que"] for t in hechas] == ["b12", "cafe"]


# -- R8: limits --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mala", ["25:00", "10:60", "diez", "10", "", "10:0", "1000"])
def test_hora_no_valida(mala):  # R8
    with pytest.raises(ValueError, match="hora"):
        validar_hora(mala)


def test_hora_valida_se_normaliza():
    assert validar_hora("9:05") == "09:05" and validar_hora("23:59") == "23:59" and validar_hora("00:00") == "00:00"


@pytest.mark.parametrize("datos, motivo", [
    ({"que": "cafe", "forma": "capuchino", "hora": "10:00"}, "forma"),
    ({"que": "cafe", "hora": "10:00"}, "forma"),
    ({"que": "cafe", "forma": "doble", "hora": "10:00", "mg": -5}, "negativ"),
    ({"que": "cafe", "forma": "doble", "hora": "25:00"}, "hora"),
    ({"que": "te", "hora": "10:00"}, "café o B12"),
    ({"que": "cafe", "forma": "italiana", "hora": "10:00", "gramos": 0}, "mayor"),
    ({"que": "cafe", "forma": "italiana", "hora": "10:00", "fraccion": 1.5}, "fracción"),
])
def test_tomas_no_validas_no_se_guardan(sup, datos, motivo):  # R8
    with pytest.raises(ValueError, match=motivo):
        sup.apuntar(HOY, datos, hoy=HOY, ahora=AHORA)
    assert sup.tomas_del_dia(HOY) == []


def test_cambio_no_valido_no_cambia_nada(sup):  # R8
    toma = sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    for malo in ({"hora": "24:00"}, {"forma": "moka"}, {"mg": -1}):
        with pytest.raises(ValueError):
            sup.cambiar(toma["id"], malo)
    assert sup.toma(toma["id"]) == toma


def test_hora_de_seccion_no_valida(sup):  # R8, R5
    with pytest.raises(ValueError, match="hora"):
        sup.poner_hora_seccion(HOY, "cena", "22:75")
    with pytest.raises(ValueError, match="sección"):
        sup.poner_hora_seccion(HOY, "brunch", "11:00")
    assert sup.horas_del_dia(HOY)["cena"]["aproximada"] is True


def test_una_base_de_antes_abre_igual(db):  # R8: no migration by hand, the foods untouched
    registro = RegistroComidas(db)
    registro.guardar([Alimento(nombre="pan inventado", dia=HOY, cantidad=60, unidad="g", kcal=150)], hoy=HOY,
                     texto="pan", sin_cambios=True, seccion="desayuno")
    antes = _volcado(db)
    Suplementacion(db)
    Suplementacion(db)  # twice: nothing breaks
    assert _volcado(db) == antes
