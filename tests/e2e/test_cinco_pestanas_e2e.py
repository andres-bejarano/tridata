"""Unit 029 in a real Chromium at 390 × 844, light and dark: the app in five tabs (Hoy · Nutrición · Fitness ·
Asistente · Ajustes); Nutrición with its selector Día · Balance · Recetas; Hoy, Fitness and Asistente say in one
sentence what they will bring; the dictation box only in Nutrición › Día; the addresses of before.

Uses the `conftest.py` of unit 010 (temporary SQLite, every outside service doubled). Invented data only.
Plans v6 (surface, P-20261023-a848b00b) · `pedir-al-asistente` S-16. R1-R7 of the unit.
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.nutricion.recetas import Recetario  # noqa: E402

TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
PESTANAS = ["Hoy", "Nutrición", "Fitness", "Asistente", "Ajustes"]
PARTES = ["Día", "Balance", "Recetas"]
PRONTO = {"hoy": "el resumen de tu día", "fitness": "tu carga, tu último entreno y tu recuperación",
          "asistente": "tus conversaciones con el asistente"}


def _textos(locator) -> list[str]:
    return [t.strip() for t in locator.all_inner_texts()]


def _marcada(p) -> str:
    return p.locator('nav.pestanas .pestana[aria-current="page"]').inner_text().strip()


def _parte_marcada(p) -> str:
    return p.locator('#partes [aria-current="page"]').inner_text().strip()


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


# -- R1, R2: five tabs, and the app opens in Nutrición › Día ---------------------------------------------------

@TEMAS
def test_cinco_pestanas_y_abre_en_dia(servidor, abrir, capturar, oscuro):  # R1 · R2
    servidor.dia_de_prueba()
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/", oscuro=oscuro)
    assert _textos(p.locator("nav.pestanas .pestana")) == PESTANAS
    assert _marcada(p) == "Nutrición"
    expect(p.locator("section#dia")).to_be_visible()
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    expect(p.locator("#barra")).to_be_visible()
    expect(p.locator("#lista-dia .fila", has_text="Avena")).to_be_visible()
    expect(p.locator("#partes")).to_be_visible()
    assert _textos(p.locator("#partes a")) == PARTES
    assert _parte_marcada(p) == "Día"
    for b in p.locator("nav.pestanas .pestana").all() + p.locator("#partes a").all():
        assert b.bounding_box()["height"] >= 44
    _sin_scroll(p, "día")
    capturar(p, f"dia-con-selector{sufijo}")


# -- R2, R7: the selector of Nutrición -------------------------------------------------------------------------

@TEMAS
def test_selector_de_nutricion(servidor, abrir, capturar, oscuro):  # R2 · R7
    servidor.semana_de_prueba()
    Recetario(servidor.db).crear_fijo({"nombre": "dátiles de prueba", "kcal": 280, "por": "100g"})
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#dia", oscuro=oscuro)

    p.locator("#partes a", has_text="Balance").click()
    expect(p).to_have_url(servidor.url + "/#balance")
    expect(p.locator("section#balance")).to_be_visible()
    expect(p.locator("section#dia")).to_be_hidden()
    expect(p.locator("#balance-semana .fila-dia")).to_have_count(7)
    assert _marcada(p) == "Nutrición"
    assert _parte_marcada(p) == "Balance"
    _sin_scroll(p, "balance")
    capturar(p, f"balance{sufijo}")
    # a day tapped in Balance opens in Nutrición › Día (unit 017)
    p.locator("#balance-semana .fila-dia").nth(0).click()
    expect(p.locator("section#dia")).to_be_visible()
    expect(p.locator("body")).to_have_attribute("data-dia", "2026-10-19")
    assert _parte_marcada(p) == "Día"
    # «Día» while in Día goes back to today (what Hoy in Hoy did)
    p.locator("#partes a", has_text="Día").click()
    expect(p.locator("body")).to_have_attribute("data-dia", "2026-10-21")
    expect(p.locator("section#dia")).to_be_visible()

    p.locator("#partes a", has_text="Recetas").click()
    expect(p.locator("section#recetas")).to_be_visible()
    assert _parte_marcada(p) == "Recetas"
    assert _marcada(p) == "Nutrición"
    _sin_scroll(p, "recetas")
    capturar(p, f"recetas{sufijo}")
    caja = p.locator("#apartado-fijos")
    caja.locator(".elemento", has_text="dátiles de prueba").click()
    calc = caja.locator(".ficha .calculadora")
    calc.get_by_label("¿Cuánto has cogido? (g)").fill("6")
    calc.get_by_role("button", name="Apuntar en hoy").click()
    expect(p.locator("#recetas-mensaje")).to_contain_text("Apuntado en hoy: dátiles de prueba")
    p.locator("#partes a", has_text="Día").click()
    expect(p.locator("#lista-dia .fila", has_text="dátiles de prueba")).to_be_visible()


# -- R3: the «coming soon» screens ------------------------------------------------------------------------------

@TEMAS
def test_pantallas_llega_pronto(servidor, abrir, capturar, oscuro):  # R3
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/", oscuro=oscuro)
    for sitio, nombre in (("hoy", "Hoy"), ("fitness", "Fitness"), ("asistente", "Asistente")):
        p.locator("nav.pestanas .pestana", has_text=nombre).click()
        expect(p).to_have_url(f"{servidor.url}/#{sitio}")
        pantalla = p.locator(f"section#{sitio}")
        expect(pantalla).to_be_visible()
        expect(pantalla.locator("h1")).to_have_text(nombre)
        expect(pantalla).to_contain_text(PRONTO[sitio])
        expect(p.locator("section#dia")).to_be_hidden()
        expect(p.locator("#partes")).to_be_hidden()
        expect(p.locator("#entrada")).to_be_hidden()
        assert _marcada(p) == nombre
        _sin_scroll(p, sitio)
        capturar(p, f"pronto-{sitio}{sufijo}")


# -- R4: where the box shows (S-16) ------------------------------------------------------------------------------

def test_la_caja_solo_en_dia(servidor, abrir):  # R4 · S-16
    p = abrir(servidor, "/#dia")
    expect(p.locator("#entrada")).to_be_visible()
    expect(p.locator("#grabar")).to_be_visible()
    for sitio in ("balance", "recetas", "fitness", "asistente"):
        p.evaluate(f"location.hash = '{sitio}'")
        expect(p.locator(f"section#{sitio}")).to_be_visible()
        expect(p.locator("#entrada")).to_be_hidden()
        expect(p.locator("#grabar")).to_be_hidden()
    p.evaluate("location.hash = 'dia'")
    expect(p.locator("#entrada")).to_be_visible()
    p.locator("nav.pestanas .pestana", has_text="Ajustes").click()
    expect(p).to_have_url(servidor.url + "/ajustes")
    expect(p.locator("#entrada")).to_have_count(0)
    expect(p.locator("#grabar")).to_have_count(0)


# -- R5: the bar in Ajustes -------------------------------------------------------------------------------------

def test_la_barra_en_ajustes(servidor, abrir, capturar):  # R5
    p = abrir(servidor, "/ajustes")
    assert _textos(p.locator("nav.pestanas .pestana")) == PESTANAS
    assert _marcada(p) == "Ajustes"
    capturar(p, "ajustes-barra")
    p.locator("nav.pestanas .pestana", has_text="Nutrición").click()
    expect(p.locator("section#dia")).to_be_visible()
    expect(p.locator("body[data-dia]")).to_have_count(1)
    assert _parte_marcada(p) == "Día"
    for sitio, nombre in (("hoy", "Hoy"), ("fitness", "Fitness"), ("asistente", "Asistente")):
        p.locator("nav.pestanas .pestana", has_text="Ajustes").click()
        expect(p).to_have_url(servidor.url + "/ajustes")
        p.locator("nav.pestanas .pestana", has_text=nombre).click()
        expect(p.locator(f"section#{sitio}")).to_be_visible()
        expect(p.locator(f"section#{sitio}")).to_contain_text(PRONTO[sitio])
        assert _marcada(p) == nombre


# -- R6: the addresses of before ---------------------------------------------------------------------------------

@pytest.mark.parametrize(("ruta", "sitio", "parte"), [
    ("/#balance", "balance", "Balance"), ("/#recetas", "recetas", "Recetas"), ("/#peso", "balance", "Balance"),
    ("/#hoy", "dia", "Día"), ("/", "dia", "Día"), ("/#nada", "dia", "Día"), ("/#dia", "dia", "Día"),
], ids=["balance", "recetas", "peso", "hoy", "ninguna", "no-existe", "dia"])
def test_direcciones_de_antes(servidor, abrir, ruta, sitio, parte):  # R6
    p = abrir(servidor, ruta, esperar=False)
    expect(p.locator(f"section#{sitio}")).to_be_visible()
    for otro in ("hoy", "dia", "balance", "recetas", "fitness", "asistente"):
        if otro != sitio:
            expect(p.locator(f"section#{otro}")).to_be_hidden()
    assert _marcada(p) == "Nutrición"
    assert _parte_marcada(p) == parte
