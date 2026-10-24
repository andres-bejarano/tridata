"""Unit 023 in a real Chromium at 390 × 844, light and dark: the assistant in the kitchen (REC-14).

(a) a new recipe dictated from Hoy: the card with one line per ingredient and the whole recipe → Confirmar → it is
in Recetas; (b) weighing with its pot from Nutrición › Día: the subtraction with the tare of the base → Confirmar →
the recipe is definitive. No horizontal scroll and buttons of 44 px or more. Plans `pedir-al-asistente`: C-108,
C-110; R1, R3 of the unit. Uses the `conftest.py` of unit 010 (Claude is a `MagicMock` that returns the `cambios`
of each case; «today» is Wednesday 2026-10-21). Every value is invented.
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.ia import CambioInterpretado, Interpretacion, LineaInterpretada  # noqa: E402
from tridata.nutricion.recetas import Recetario  # noqa: E402

TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])


def _sembrar(servidor):
    recetario = Recetario(servidor.db)
    for nombre, kcal in (("calabaza de prueba", 25), ("aceite de prueba", 900), ("cebolla de prueba", 40)):
        recetario.crear_fijo({"nombre": nombre, "kcal": kcal, "por": "100g"})
    recetario.crear_olla({"nombre": "olla grande", "tara_g": 1500})
    recetario.crear_receta({"nombre": "lentejas de prueba", "ingredientes": [
        {"nombre": "lenteja seca de prueba", "cantidad": 700, "unidad": "g", "kcal": 2150},
        {"nombre": "aceite de prueba", "cantidad": 50, "unidad": "g"}]}, hoy=servidor.hoy)
    return recetario


def _claude(servidor, cambio):
    servidor.interprete.interpretar.side_effect = lambda texto, hoy: Interpretacion(alimentos=[], cambios=[cambio])


def _escribir(p, texto):
    p.locator("#texto").fill(texto)
    p.locator("#enviar").click()


def _sin_scroll(p):
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"horizontal scroll ({ancho}px)"


def _botones_44(caja):
    for b in caja.locator("button:visible").all():
        assert b.bounding_box()["height"] >= 44, b.inner_text()


def _l(nombre, cantidad):
    return LineaInterpretada(nombre=nombre, cantidad=cantidad, unidad="g", kcal_estimadas=None)


@TEMAS
def test_receta_nueva_desde_hoy(servidor, abrir, ir_a, capturar, oscuro):  # R1
    recetario = _sembrar(servidor)
    _claude(servidor, CambioInterpretado(accion="nueva_receta", objeto="crema de calabaza de prueba",
                                         lineas=[_l("calabaza", 800), _l("aceite", 20), _l("cebolla", 150)]))
    p = abrir(servidor, "/", esperar=False, oscuro=oscuro)
    expect(p.locator("section#hoy")).to_be_visible()
    _escribir(p, "receta nueva crema de calabaza de prueba: 800 gramos de calabaza, 20 de aceite y 150 de cebolla")
    tarjeta = p.locator("section#hoy #revision .tarjeta-cambio")
    expect(tarjeta).to_have_count(1)
    expect(tarjeta).to_contain_text("440 kcal la receta entera, sin pesar")
    lineas = tarjeta.locator("ul.lineas li")
    expect(lineas).to_have_count(3)
    expect(lineas.nth(0)).to_have_text("calabaza de prueba 800 g · 200 kcal")
    expect(lineas.nth(1)).to_have_text("aceite de prueba 20 g · 180 kcal")
    expect(lineas.nth(2)).to_have_text("cebolla de prueba 150 g · 60 kcal")
    _sin_scroll(p)
    _botones_44(tarjeta)
    capturar(p, f"receta-nueva-{'oscuro' if oscuro else 'claro'}")
    assert recetario.recetas()[0]["nombre"] == "lentejas de prueba" and len(recetario.recetas()) == 1

    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(p.locator("#mensaje")).to_contain_text("Hecho")
    ir_a(p, "recetas")
    expect(p.locator("#apartado-recetas")).to_contain_text("crema de calabaza de prueba")
    assert len(recetario.recetas()) == 2


@TEMAS
def test_pesar_desde_nutricion(servidor, abrir, capturar, oscuro):  # R3
    recetario = _sembrar(servidor)
    _claude(servidor, CambioInterpretado(accion="pesar_receta", objeto="lentejas de prueba", valor=3500,
                                         texto="olla grande"))
    p = abrir(servidor, oscuro=oscuro)
    _escribir(p, "las lentejas de prueba pesan 3.500 en la olla grande")
    tarjeta = p.locator("section#dia #revision .tarjeta-cambio")
    expect(tarjeta).to_contain_text(
        "Voy a pesar lentejas de prueba: 3.500 − 1.500 (olla grande) = 2.000 g → 130 kcal/100 g, definitiva")
    _sin_scroll(p)
    _botones_44(tarjeta)
    capturar(p, f"pesar-{'oscuro' if oscuro else 'claro'}")
    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(p.locator("#mensaje")).to_contain_text("Hecho")
    (lentejas,) = recetario.recetas()
    assert lentejas["en_uso"]["estado"] == "definitiva" and lentejas["en_uso"]["kcal_100g"] == 130
