"""Unit 102 in a real Chromium at 390 × 844, light and dark: the shoes in Fitness and their warning in Hoy (E2E-17).

Plans `ver-fitness` REC-46: C-364, C-365, C-366, C-367, C-369. Uses the `conftest.py` of unit 010 («today» is
Wednesday 2026-10-21; Garmin is a `MagicMock`; Strava is never connected here: the shoes are put in the base as a
read of Strava would). Invented data only.
"""
from __future__ import annotations

import re
from datetime import datetime

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.zapatillas import Zapatillas  # noqa: E402

TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
AYER = datetime(2026, 10, 20, 8, 0)  # the day before the `HOY` of conftest.py


def _zapatillas(servidor, meta_km: float = 350.0) -> Zapatillas:
    repo = Zapatillas(servidor.db)
    repo.guardar_lectura([
        {"id": "g1001", "nombre": "Asics SonicBlast", "km": 303.2, "retirada": False},
        {"id": "g1002", "nombre": "Asics Gel Nimbus 28", "km": 141.0, "retirada": False},
        {"id": "g1003", "nombre": "Asics Meta Speed Sky Tokyo", "km": meta_km, "retirada": False},
        {"id": "g1004", "nombre": "Nike Vomero 18", "km": 812.0, "retirada": True},
    ], AYER)
    return repo


def _abrir_fitness(abrir, ir_a, servidor, **k):
    p = abrir(servidor, "/", esperar=False, **k)
    expect(p.locator("section#hoy")).to_be_visible()
    ir_a(p, "fitness")
    expect(p.locator("#fitness-zapatillas-lista .zapatilla")).to_have_count(3)
    return p


def _fila(p, nombre: str):
    return p.locator("#fitness-zapatillas-lista .zapatilla", has_text=nombre)


@TEMAS
def test_fitness_lista_las_zapatillas_y_hoy_avisa(servidor, abrir, ir_a, capturar, oscuro):  # C-364, C-366
    _zapatillas(servidor)
    p = abrir(servidor, "/", esperar=False, oscuro=oscuro)
    hoy = p.locator("section#hoy")
    expect(hoy).to_be_visible()
    aviso = hoy.locator(".aviso-zapatillas")
    expect(aviso).to_be_visible()
    expect(aviso).to_contain_text("Zapatillas")
    expect(aviso).to_contain_text("Asics Meta Speed Sky Tokyo 350 de 400 km, toca cambiarla pronto")
    expect(aviso).not_to_contain_text("SonicBlast")
    expect(hoy.locator(".tarjeta-hoy")).to_have_count(3)  # the cards of always are still three
    capturar(p, f"hoy-aviso-zapatillas-{'oscuro' if oscuro else 'claro'}")
    ir_a(p, "fitness")
    lista = p.locator("#fitness-zapatillas-lista .zapatilla")
    expect(lista).to_have_count(3)
    expect(p.locator("#fitness-zapatillas")).not_to_contain_text("Vomero")  # retired in Strava: not shown
    expect(_fila(p, "SonicBlast")).to_contain_text("303 de 700 km · quedan 397")
    expect(_fila(p, "SonicBlast")).to_contain_text("de Strava, el 20/10")
    expect(_fila(p, "Meta Speed")).to_contain_text("toca cambiarla pronto")
    expect(_fila(p, "Nimbus")).not_to_contain_text("toca cambiarla")
    p.locator("#fitness-zapatillas").scroll_into_view_if_needed()
    capturar(p, f"fitness-zapatillas-{'oscuro' if oscuro else 'claro'}")


def test_sin_ninguna_cerca_del_tope_hoy_no_dice_nada(servidor, abrir):  # C-367
    _zapatillas(servidor, meta_km=103.4)
    p = abrir(servidor, "/", esperar=False)
    hoy = p.locator("section#hoy")
    expect(hoy.locator("#hoy-comida")).to_be_visible()
    expect(hoy.locator(".aviso-zapatillas")).to_be_hidden()
    expect(hoy).not_to_contain_text("Zapatillas")
    expect(hoy.locator(".tarjeta-hoy")).to_have_count(3)


def test_cambiar_el_tope_y_los_km_en_fitness(servidor, abrir, ir_a, capturar):  # C-365, C-369
    _zapatillas(servidor, meta_km=103.4)
    p = _abrir_fitness(abrir, ir_a, servidor)
    meta = _fila(p, "Meta Speed")
    meta.get_by_role("button", name="Cambiar tope").click()
    meta.get_by_label("Tope en km").fill("120")
    meta.get_by_role("button", name="Guardar").click()
    expect(meta).to_contain_text("103 de 120 km · quedan 17")
    expect(meta).to_contain_text("toca cambiarla pronto")
    # a cap that is not valid: the server says why and nothing changes
    meta.get_by_role("button", name="Cambiar tope").click()
    meta.get_by_label("Tope en km").fill("20")
    meta.get_by_role("button", name="Guardar").click()
    expect(meta.locator(".aviso")).to_contain_text("50")
    expect(meta).to_contain_text("de 120 km")
    meta.get_by_role("button", name="Cancelar").click()
    # the km by hand
    nimbus = _fila(p, "Nimbus")
    nimbus.get_by_role("button", name="Cambiar km").click()
    nimbus.get_by_label("Km que lleva").fill("160")
    nimbus.get_by_role("button", name="Guardar").click()
    expect(nimbus).to_contain_text("160 de 700 km")
    expect(nimbus).to_contain_text("dicho por ti, hoy")
    capturar(p, "fitness-zapatillas-editadas")
    # Hoy follows the new cap
    ir_a(p, "hoy")
    expect(p.locator("section#hoy .aviso-zapatillas")).to_contain_text("Asics Meta Speed Sky Tokyo 103 de 120 km")
    ir_a(p, "fitness")
    meta = _fila(p, "Meta Speed")
    meta.get_by_role("button", name="Cambiar tope").click()
    meta.get_by_label("Tope en km").fill("400")
    meta.get_by_role("button", name="Guardar").click()
    expect(meta).to_contain_text("quedan 297")
    ir_a(p, "hoy")
    expect(p.locator("section#hoy .aviso-zapatillas")).to_be_hidden()


def test_los_botones_de_las_zapatillas_miden_44_px(servidor, abrir, ir_a):  # ergonomics of the phone
    _zapatillas(servidor)
    p = _abrir_fitness(abrir, ir_a, servidor)
    botones = p.locator("#fitness-zapatillas button:visible")
    assert botones.count() >= 6
    for i in range(botones.count()):
        caja = botones.nth(i).bounding_box()
        assert caja and caja["height"] >= 44, botones.nth(i).inner_text()
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, ancho
    assert re.search(r"quedan \d+", p.locator("#fitness-zapatillas").inner_text())
