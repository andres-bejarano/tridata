"""Unit 098 (REC-43, R7 · R-273, C-345) in a real Chromium at 390 × 844, light and dark: the card of an ingredient
shows and changes its protein, carbs and fat (per 100 g or per unit, «estimado» or «de etiqueta»), and the card of a
recipe shows its macros per 100 g and how many ingredients have none.

Uses the `conftest.py` of unit 010 as it is (temporary SQLite, every outside service doubled); the saved things are
written straight into that SQLite. Invented data only.
"""
from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.nutricion.recetas import Recetario  # noqa: E402

ANCHO = 390


def _linea(nombre: str, gramos: float) -> dict:
    return {"nombre": nombre, "cantidad": gramos, "unidad": "g", "kcal": 1, "guardado": nombre}


def _guardado(servidor) -> dict:
    rec = Recetario(servidor.db)
    yogur = rec.crear_fijo({"nombre": "Yougurt 0.0", "kcal": 50, "por": "100g"})
    rec.guardar_macros([{"clave": f"fijo:{yogur['id']}", "proteina": 8, "hidratos": 4, "grasa": 0.1}])
    rec.crear_fijo({"nombre": "garbanzo de prueba", "kcal": 350, "por": "100g", "proteina": 20, "hidratos": 55,
                    "grasa": 5})
    rec.crear_fijo({"nombre": "aceite de prueba", "kcal": 900, "por": "100g", "proteina": 0, "hidratos": 0,
                    "grasa": 100})
    rec.crear_fijo({"nombre": "cebolla de prueba", "kcal": 40, "por": "100g"})
    rec.crear_fijo({"nombre": "huevo de prueba", "kcal": 70, "por": "unidad", "proteina": 6, "hidratos": 0.5,
                    "grasa": 5})
    rec.crear_receta({"nombre": "garbanzos de prueba", "peso_neto_g": 2000, "ingredientes": [
        _linea("garbanzo de prueba", 600), _linea("aceite de prueba", 50)]}, date(2026, 10, 19))
    rec.crear_receta({"nombre": "garbanzos con cebolla", "peso_neto_g": 2000, "ingredientes": [
        _linea("garbanzo de prueba", 600), _linea("aceite de prueba", 50), _linea("cebolla de prueba", 125)]},
        date(2026, 10, 19))
    return {"yogur": yogur}


def _sin_desborde(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= ANCHO, f"{pantalla}: horizontal scroll ({ancho}px)"


def _abrir_cosa(p, apartado: str, nombre: str):
    caja = p.locator(f"#apartado-{apartado}")
    caja.locator(".elemento", has_text=nombre).first.click()
    return caja.locator(".ficha")


@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_ficha_de_ingrediente_ensena_y_cambia_sus_macros(servidor, abrir, capturar, oscuro):  # R7 · C-345
    cosas = _guardado(servidor)
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#recetas", oscuro=oscuro)
    ficha = _abrir_cosa(p, "fijos", "Yougurt 0.0")
    macros = ficha.locator(".macros-fijo")
    expect(macros).to_contain_text("por 100 g")
    expect(macros.locator(".marca-macros")).to_have_text("estimado")
    proteina = macros.get_by_label("Proteína (g)")
    expect(proteina).to_have_value("8")
    expect(macros.get_by_label("Hidratos (g)")).to_have_value("4")
    expect(macros.get_by_label("Grasa (g)")).to_have_value("0,1")
    _sin_desborde(p, "ficha-ingrediente" + sufijo)
    macros.scroll_into_view_if_needed()
    capturar(p, f"macros-ficha-ingrediente{sufijo}")
    proteina.fill("10,2")
    ficha.get_by_role("button", name="Guardar").click()
    expect(p.locator("#recetas-mensaje")).to_contain_text("Guardado: Yougurt 0.0")
    fijo = next(f for f in Recetario(servidor.db).fijos() if f["id"] == cosas["yogur"]["id"])
    assert (fijo["proteina"], fijo["macros_origen"]) == (10.2, "etiqueta")
    ficha = _abrir_cosa(p, "fijos", "Yougurt 0.0")
    expect(ficha.locator(".macros-fijo .marca-macros")).to_have_text("de etiqueta")
    expect(ficha.locator(".macros-fijo").get_by_label("Proteína (g)")).to_have_value("10,2")
    ficha.locator(".macros-fijo").scroll_into_view_if_needed()
    capturar(p, f"macros-ficha-ingrediente-etiqueta{sufijo}")
    # per unit, as its kcal
    ficha = _abrir_cosa(p, "fijos", "huevo de prueba")
    expect(ficha.locator(".macros-fijo")).to_contain_text("por unidad")
    # without macros it says so; one or two of the three is not saved
    ficha = _abrir_cosa(p, "fijos", "cebolla de prueba")
    macros = ficha.locator(".macros-fijo")
    expect(macros.locator(".marca-macros")).to_have_text("sin macros")
    macros.get_by_label("Proteína (g)").fill("1")
    ficha.get_by_role("button", name="Guardar").click()
    expect(ficha.locator(".aviso")).to_contain_text("los tres")


@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_ficha_de_receta_ensena_sus_macros_por_100g(servidor, abrir, capturar, oscuro):  # R7
    _guardado(servidor)
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#recetas", oscuro=oscuro)
    ficha = _abrir_cosa(p, "recetas", "garbanzos de prueba")
    expect(ficha.locator(".macros-receta")).to_contain_text("por 100 g")
    expect(ficha.locator(".macros-receta")).to_contain_text("proteína 6")
    expect(ficha.locator(".macros-receta")).to_contain_text("hidratos 16,5")
    expect(ficha.locator(".macros-receta")).to_contain_text("grasa 4")
    _sin_desborde(p, "ficha-receta" + sufijo)
    capturar(p, f"macros-ficha-receta{sufijo}", completa=True)
    ficha = _abrir_cosa(p, "recetas", "garbanzos con cebolla")
    expect(ficha.locator(".macros-receta")).to_contain_text("sin macros")
    expect(ficha.locator(".macros-receta")).to_contain_text("1 ingrediente sin macros")
    capturar(p, f"macros-ficha-receta-incompleta{sufijo}", completa=True)
