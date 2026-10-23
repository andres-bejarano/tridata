"""Unit 026 in a real Chromium at 390 × 844, light and dark: the common foods in the search box of «Añadir» (his
first, then the common ones with the mark «común») and their folded group «Comunes» in Recetas › Ingredientes.

Uses the `conftest.py` of unit 010 as it is (temporary SQLite, every outside service doubled): the app loads the
common foods when it is created; the own foods are invented and written straight into that SQLite.
Plans: `apuntar-comida` (the search box orders his first), `guardar-receta` (the common list). R3, R4, R5, R6.
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.nutricion.comunes import COMUNES  # noqa: E402
from tridata.nutricion.recetas import Recetario  # noqa: E402

ANCHO = 390


def _suyos(servidor) -> None:
    rec = Recetario(servidor.db)
    rec.crear_fijo({"nombre": "Manzana", "kcal": 52, "por": "100g"})  # invented, his (not a common name)
    rec.crear_fijo({"nombre": "Pera limonera de prueba", "kcal": 57, "por": "100g"})
    servidor.apuntar("comida de antes", servidor.hoy, 500, 1000)


def _ergonomia(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= ANCHO, f"{pantalla}: horizontal scroll ({ancho}px)"
    pequenos = p.evaluate("""() => [...document.querySelectorAll(
        'button, a[href], input, select, textarea, summary, [role=button], [role=option], .fila, .elemento')]
      .filter((e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !e.closest('[hidden]'); })
      .filter((e) => e.getBoundingClientRect().height < 44)
      .map((e) => `${e.tagName}#${e.id}.${e.className} ${e.getBoundingClientRect().height}`)""")
    assert pequenos == [], f"{pantalla}: touch targets under 44 px: {pequenos}"


def _anadir(p):
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    p.locator("#anadir").click()
    return p.locator("#anadir-ficha .tarjeta")


def _nombres(caja) -> list[str]:
    return [t.split("\n")[0].strip() for t in caja.get_by_role("option").all_inner_texts()]


@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_buscador_tuyos_primero_y_la_pera_comun(servidor, abrir, capturar, oscuro):  # R3 — criterio portante
    _suyos(servidor)
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/", oscuro=oscuro)
    expect(p.locator("#total")).to_have_text("1.000 kcal comidas")
    ficha = _anadir(p)
    nombre = ficha.get_by_role("combobox", name="Alimento")
    opciones = ficha.get_by_role("option")
    nombre.click()  # empty and touched: his, then the common ones in their own group
    lista = ficha.get_by_role("listbox")
    expect(lista.locator(".grupo")).to_have_text(["Ingredientes", "Comunes"])
    expect(opciones).to_have_count(2 + len(COMUNES))
    assert _nombres(ficha)[:2] == ["Manzana", "Pera limonera de prueba"]
    capturar(p, f"comunes-buscador-vacio{sufijo}")
    nombre.fill("tomate")  # «Tomate · común · 20 kcal/100 g»
    expect(opciones).to_have_count(1)
    expect(opciones.nth(0)).to_contain_text("Tomate")
    expect(opciones.nth(0)).to_contain_text("común · 20 kcal/100 g")
    nombre.fill("manzana")  # his «Manzana», once, without «común»
    expect(opciones).to_have_count(1)
    expect(opciones.nth(0)).to_contain_text("Ingrediente · 52 kcal/100 g")
    expect(opciones.nth(0)).not_to_contain_text("común")
    nombre.fill("pera")  # both start with «pera»: his first all the same
    assert _nombres(ficha) == ["Pera limonera de prueba", "Pera"]
    expect(opciones.nth(1)).to_contain_text("común · 49 kcal/100 g")
    _ergonomia(p, "comunes-buscador" + sufijo)
    capturar(p, f"comunes-buscador-pera{sufijo}")
    opciones.nth(1).click()
    elegida = ficha.locator(".elegida")
    expect(elegida).to_contain_text("de la lista de comunes")
    elegida.get_by_label("Gramos").fill("180")
    expect(elegida.locator(".kcal-elegido")).to_have_text("88 kcal")
    ficha.get_by_role("button", name="Guardar").click()
    fila = p.locator("#lista-dia .fila", has_text="Pera")
    expect(fila.locator(".kcal")).to_have_text("88")
    expect(fila).to_contain_text("180 g")
    expect(fila).to_contain_text("de la lista de comunes")
    expect(fila.locator(".marca", has_text="estimado")).to_have_count(0)
    expect(p.locator("#total")).to_have_text("1.088 kcal comidas")


@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_grupo_comunes_plegado_corregir_y_quitar(servidor, abrir, capturar, oscuro):  # R4, R5, R6
    _suyos(servidor)
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#recetas", oscuro=oscuro)
    fijos = p.locator("#apartado-fijos")
    grupo = fijos.locator("details.comunes")
    expect(fijos.locator(".elemento", has_text="Manzana")).to_be_visible()
    expect(grupo.locator("summary")).to_have_text(f"Comunes ({len(COMUNES)})")
    expect(grupo).not_to_have_attribute("open", "")  # folded
    tomate = grupo.locator(".elemento", has_text="Tomate")
    expect(tomate).to_be_hidden()
    expect(fijos.locator(".vacio-apartado")).to_have_count(0)
    _ergonomia(p, "comunes-plegado" + sufijo)
    capturar(p, f"comunes-plegado{sufijo}")
    grupo.locator("summary").click()
    expect(tomate).to_be_visible()
    expect(tomate).to_contain_text("20 kcal/100 g")
    expect(grupo.locator(".elemento")).to_have_count(len(COMUNES))
    expect(fijos.locator(".elemento", has_text="Manzana").locator("xpath=ancestor::details")).to_have_count(0)
    _ergonomia(p, "comunes-abierto" + sufijo)
    capturar(p, f"comunes-abierto{sufijo}", completa=True)
    # R4: correcting «Pan de barra» makes it his
    grupo.locator(".elemento", has_text="Pan de barra").click()
    ficha = fijos.locator(".ficha-fijo")
    expect(ficha.get_by_label("Nombre")).to_have_value("Pan de barra")
    ficha.get_by_label("kcal").fill("250")
    ficha.get_by_role("button", name="Guardar").click()
    pan = fijos.locator(".elemento", has_text="Pan de barra")
    expect(pan).to_contain_text("250 kcal/100 g")
    expect(pan.locator("xpath=ancestor::details")).to_have_count(0)
    expect(grupo.locator("summary")).to_have_text(f"Comunes ({len(COMUNES) - 1})")
    # R5: removing one, it is gone from the group and from the search box
    expect(grupo).to_have_attribute("open", "")  # it stays as he left it
    grupo.locator(".elemento", has_text="Tomate").click()
    fijos.locator(".ficha-fijo").get_by_role("button", name="Borrar").click()
    fijos.locator(".confirmar-borrado").get_by_role("button", name="Sí, borrar").click()
    expect(grupo.locator(".elemento", has_text="Tomate")).to_have_count(0)
    expect(grupo.locator("summary")).to_have_text(f"Comunes ({len(COMUNES) - 2})")
    p.goto(servidor.url + "/")
    p.wait_for_selector("body[data-dia]", state="attached")
    ficha = _anadir(p)
    ficha.get_by_role("combobox", name="Alimento").fill("tomate")
    expect(ficha.get_by_role("option")).to_have_count(0)
