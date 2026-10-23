"""Bug 024 · «¿Cuánto me cabe?» of a recipe chosen by fraction (½, ⅓, ¼) said «Te caben 0».

With unit «receta» what fits is said in grams and kcal when the recipe is weighed (kcal/100 g, exact or
approximate) and in kcal when it is not; Andrés chose kcal and grams over fractions (2026-10-23). Grams and
units stay as they were. Every value is invented.
"""
import math

import pytest

from tridata.nutricion.margen import cabe_de_receta
from test_anadir_guardado import HOY, Entorno


def test_receta_pesada_en_gramos_y_kcal():
    assert cabe_de_receta(800, "Lentejas de prueba", 80) == {
        "cantidad": 1000, "unidad": "g", "texto": "te caben 1000 g de Lentejas de prueba (800 kcal)"}


def test_receta_sin_pesar_en_kcal():
    assert cabe_de_receta(667.4, "Porridge de prueba", None) == {
        "cantidad": 667, "unidad": "kcal", "texto": "te caben 667 kcal de Porridge de prueba"}


@pytest.mark.parametrize("margen", [0, -150, 0.6])
def test_sin_margen_no_cabe(margen):
    assert cabe_de_receta(margen, "Lentejas de prueba", 80) == {"cantidad": 0, "unidad": "g", "texto": "no te cabe hoy"}


# -- POST /api/simular with a card chosen by fraction ------------------------------------------------------------

@pytest.fixture
def e(tmp_path):
    return Entorno(tmp_path / "data.sqlite3")


def _margen(e) -> float:
    d = e.dia()
    margen = d["barra"]["objetivo_kcal"] - d["total_kcal"]
    assert margen > 0
    return margen


def _cabe(e, nombre: str, kcal: float) -> dict:
    tarjeta = {"nombre": nombre, "dia": HOY.isoformat(), "cantidad": 0.5, "unidad": "receta", "kcal": kcal,
               "guardado": {"tipo": "receta", "id": e.ids[nombre]}}
    return e.ok("/api/simular", {"alimentos": [tarjeta]})["caben"][0]


def test_simular_media_receta_pesada(e):
    margen = _margen(e)
    cabe = _cabe(e, "Lentejas de la abuela", 1000)
    gramos = math.floor(margen / 80 * 100)
    assert (cabe["cantidad"], cabe["unidad"]) == (gramos, "g")
    assert cabe["texto"] == f"te caben {gramos} g de Lentejas de la abuela ({round(gramos * 0.8)} kcal)"


def test_simular_media_receta_sin_pesar(e):
    margen = _margen(e)
    cabe = _cabe(e, "Porridge de prueba", 250)
    assert (cabe["cantidad"], cabe["unidad"]) == (math.floor(margen), "kcal")
    assert cabe["texto"] == f"te caben {math.floor(margen)} kcal de Porridge de prueba"


def test_simular_por_gramos_sigue_igual(e):
    margen = _margen(e)
    tarjeta = {"nombre": "avena suelta", "dia": HOY.isoformat(), "cantidad": 100, "unidad": "g", "kcal": 370}
    cabe = e.ok("/api/simular", {"alimentos": [tarjeta]})["caben"][0]
    n = math.floor(margen / 370 * 100)
    assert cabe["texto"] == f"te caben {n} g de avena suelta"


# -- review of 2026-10-23: a dictated card, approximate kcal/100 g, a boolean id ---------------------------------

def _simular(e, tarjeta: dict) -> dict:
    return e.ok("/api/simular", {"alimentos": [{"dia": HOY.isoformat(), "cantidad": 0.5, "unidad": "receta",
                                                **tarjeta}]})["caben"][0]


def test_simular_receta_dictada_sin_elegir_va_en_gramos(e):
    margen = _margen(e)  # dictated: the card brings the recipe's name, not `guardado: {tipo, id}`
    cabe = _simular(e, {"nombre": "lentejas de la abuela", "kcal": 1000, "guardado": "Lentejas de la abuela"})
    assert (cabe["cantidad"], cabe["unidad"]) == (math.floor(margen / 80 * 100), "g")


def test_simular_receta_con_kcal_aproximadas_va_en_gramos(e):
    margen = _margen(e)
    e.recetario.corregir(e.ids["Porridge de prueba"], {"kcal_100g_aprox": 125})
    cabe = _simular(e, {"nombre": "Porridge de prueba", "kcal": 250,
                        "guardado": {"tipo": "receta", "id": e.ids["Porridge de prueba"]}})
    assert (cabe["cantidad"], cabe["unidad"]) == (math.floor(margen / 125 * 100), "g")


def test_simular_id_booleano_no_es_una_receta(e):
    margen = _margen(e)  # `True` is not the recipe 1 (Lentejas de la abuela, weighed): it goes by the name
    cabe = _simular(e, {"nombre": "Porridge de prueba", "kcal": 250, "guardado": {"tipo": "receta", "id": True}})
    assert (cabe["cantidad"], cabe["unidad"]) == (math.floor(margen), "kcal")
