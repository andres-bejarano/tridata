"""Unit 112 in a real Chromium at 390 × 844 (R2, R4 · R-341, R-343, C-434, C-436, C-437): in Nutrición › Día, the
search box of «Añadir» offers each variant of a recipe as «Receta · variante» and logs it with its kcal/100 g; a food
marked as dessert («Postre» on its card) goes to a «Postre» part at the end of its section, and the total stays.

Uses the `conftest.py` of unit 010 as it is (temporary SQLite, every outside service doubled); recipes, variants and
foods are written straight into that SQLite. Invented data only. With `TRIDATA_CAPTURAS`, the screenshots of both.
"""
from __future__ import annotations

import re
from datetime import date

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.nutricion import Alimento  # noqa: E402
from tridata.nutricion.recetas import Recetario  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py
ANCHO = 390
VARIANTE = "Garbanzos de la abuela · sin chorizo"


def _textos(locator) -> list[str]:
    return [re.sub(r"\s+", " ", t).strip() for t in locator.all_text_contents()]


def _ergonomia(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= ANCHO, f"{pantalla}: horizontal scroll ({ancho}px)"
    pequenos = p.evaluate("""() => [...document.querySelectorAll(
        'button, a[href], input, select, textarea, [role=button], [role=option], .fila, .elemento')]
      .filter((e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !e.closest('[hidden]'); })
      .filter((e) => e.getBoundingClientRect().height < 44)
      .map((e) => `${e.tagName}#${e.id}.${e.className} ${e.getBoundingClientRect().height}`)""")
    assert pequenos == [], f"{pantalla}: touch targets under 44 px: {pequenos}"


def _garbanzos(servidor) -> dict:
    rec = Recetario(servidor.db)
    receta = rec.crear_receta({"nombre": "Garbanzos de la abuela", "peso_neto_g": 1000, "ingredientes": [
        {"nombre": "garbanzo cocido de prueba", "cantidad": 1000, "unidad": "g", "kcal": 1260}]}, HOY)
    rec.crear_variante(receta["id"], {"nombre": "sin chorizo", "kcal_100g": 105})
    return receta


def _fila(p, nombre: str):
    return p.locator("#lista-dia .fila[data-seccion]", has=p.locator(".que strong", has_text=nombre))


# -- R2 · R-341, C-434: «garban» offers the recipe and its variant ----------------------------------------------

def test_el_buscador_ofrece_la_variante_y_la_apunta_con_sus_kcal(servidor, abrir, capturar):  # R2
    _garbanzos(servidor)
    p = abrir(servidor)
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    p.locator("#anadir").click()
    ficha = p.locator("#anadir-ficha .tarjeta")
    nombre = ficha.get_by_role("combobox", name="Alimento")
    nombre.fill("garban")
    opciones = ficha.get_by_role("option")
    # his recipe and its variant first; the app's common «Garbanzos cocidos» (unit 026) also has «garban», after them
    expect(opciones).to_have_count(3)
    assert [t.split("\n")[0].strip() for t in opciones.all_inner_texts()] == ["Garbanzos de la abuela", VARIANTE,
                                                                              "Garbanzos cocidos"]
    expect(opciones.nth(1)).to_contain_text("105 kcal/100 g")
    _ergonomia(p, "buscador con variante")
    capturar(p, "112-buscador-variante")
    opciones.nth(1).click()
    elegida = ficha.locator(".elegida")
    expect(elegida.locator(".origen-elegido")).to_have_text("de tu receta «Garbanzos de la abuela» · sin chorizo")
    expect(elegida.locator(".fracciones")).to_have_count(0)  # a variant goes by grams only
    elegida.get_by_label("Gramos").fill("350")
    expect(elegida.locator(".kcal-elegido")).to_have_text("368 kcal")
    ficha.get_by_role("button", name="Guardar").click()
    fila = _fila(p, VARIANTE)
    expect(fila.locator(".kcal")).to_have_text("368")
    (a,) = servidor.registro.apuntes_del_dia(HOY)
    assert a["nombre"] == VARIANTE and round(a["kcal"]) == 368
    capturar(p, "112-variante-apuntada")


# -- R4 · R-343, C-436, C-437: the dessert at the end of its section --------------------------------------------

def test_marcar_postre_lo_pasa_al_final_de_la_cena_y_el_total_no_cambia(servidor, abrir, capturar):  # R4
    for nombre, kcal in (("yogur de prueba", 77), ("merluza de prueba", 180), ("ensalada de prueba", 60)):
        a = servidor.registro.anadir(Alimento(nombre=nombre, dia=HOY, cantidad=100, unidad="g", kcal=kcal), HOY)
        servidor.registro.mover_seccion(a["id"], "cena")
    p = abrir(servidor)
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    cena = p.locator("#lista-dia .seccion-cab[data-seccion='cena'] .seccion-kcal")
    expect(cena).to_have_text("317 kcal")
    cifra = p.locator("#queda-cifra").text_content()
    expect(p.locator("#lista-dia .postre-cab")).to_have_count(0)
    _fila(p, "yogur de prueba").click()
    tarjeta = p.locator("#lista-dia .tarjeta")
    casilla = tarjeta.get_by_role("checkbox", name="Postre")
    expect(casilla).not_to_be_checked()
    _ergonomia(p, "ficha con la casilla de postre")
    capturar(p, "112-ficha-postre")
    casilla.check()
    postre = p.locator("#lista-dia .postre-cab[data-seccion='cena']")
    expect(postre).to_have_count(1)
    expect(postre).to_contain_text("Postre")
    # the order of the dinner: its foods, then «Postre» and the dessert, at the end
    orden = p.evaluate("""() => [...document.querySelectorAll('#lista-dia > *')]
      .filter((n) => n.dataset.seccion === 'cena')
      .map((n) => n.classList.contains('fila') ? n.querySelector('.que strong').textContent : n.className)""")
    assert orden == ["seccion-cab", "merluza de prueba", "ensalada de prueba", "postre-cab", "yogur de prueba"], orden
    expect(cena).to_have_text("317 kcal")  # the mark does not change what adds up
    expect(p.locator("#queda-cifra")).to_have_text(cifra)
    assert servidor.registro.alimento(a["id"] - 2)["postre"] is True
    _ergonomia(p, "día con postre")
    capturar(p, "112-postre-en-la-cena")
    # unmarking it takes it back into the section
    _fila(p, "yogur de prueba").click()
    p.locator("#lista-dia .tarjeta").get_by_role("checkbox", name="Postre").uncheck()
    expect(p.locator("#lista-dia .postre-cab")).to_have_count(0)
    assert servidor.registro.alimento(a["id"] - 2)["postre"] is False
