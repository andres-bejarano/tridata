"""The Recetas tab in a real Chromium at 390 × 844 (unit 011): E2E-1 and editing a fixed food and a pot.

Uses the `conftest.py` of unit 010 as it is (temporary SQLite, every outside service doubled). Invented data only.
R11 the three sections · R16 E2E-1 (C-42, C-43, C-44) · R18 editing.
Unit 080 (R-44, R-48 out): the ingredients of a recipe are put by hand, one line each (no «Interpretar ingredientes»
nor dictating them), and what is eaten is logged with «Apuntar en hoy» (no box in Hoy). R4 (the proposal of what was
said and is only alike) lived in the review of the box and went with it.
"""
from __future__ import annotations

import re
from datetime import date

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402


ANCHO = 390
INGREDIENTES = (("garbanzo seco", 600), ("aceite de oliva", 50), ("cebolla", 125))


def _ergonomia(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= ANCHO, f"{pantalla}: horizontal scroll ({ancho}px)"
    pequenos = p.evaluate("""() => [...document.querySelectorAll(
        'button, a[href], input, select, textarea, [role=button], .fila, .elemento')]
      .filter((e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !e.closest('[hidden]'); })
      .filter((e) => e.getBoundingClientRect().height < 44)
      .map((e) => `${e.tagName}#${e.id}.${e.className} ${e.getBoundingClientRect().height}`)""")
    assert pequenos == [], f"{pantalla}: touch targets under 44 px: {pequenos}"


def _apartado(p, cual: str):
    return p.locator(f"#apartado-{cual}")


def _nuevo_fijo(p, nombre, kcal, por="por 100 g", alias=""):
    apartado = _apartado(p, "fijos")
    apartado.get_by_role("button", name="＋ Añadir ingrediente").click()
    ficha = apartado.locator(".ficha-fijo")
    ficha.get_by_label("Nombre").fill(nombre)
    ficha.get_by_label("kcal").fill(str(kcal))
    ficha.get_by_label("Por").select_option(label=por)
    if alias:
        ficha.get_by_label("Alias").fill(alias)
    ficha.get_by_role("button", name="Guardar").click()
    expect(apartado.locator(".elemento", has_text=nombre)).to_be_visible()


def _nueva_olla(p, nombre, tara):
    apartado = _apartado(p, "taras")
    apartado.get_by_role("button", name="＋ Añadir tara").click()
    ficha = apartado.locator(".ficha-olla")
    ficha.get_by_label("Nombre").fill(nombre)
    ficha.get_by_label("Tara (g)").fill(str(tara))
    ficha.get_by_role("button", name="Guardar").click()
    expect(apartado.locator(".elemento", has_text=nombre)).to_be_visible()


def _garbanzos(p, capturar=None, sufijo=""):
    apartado = _apartado(p, "recetas")
    apartado.get_by_role("button", name="＋ Añadir receta").click()
    ficha = apartado.locator(".ficha-receta")
    ficha.get_by_label("Nombre").fill("garbanzos de la abuela")
    for n, (nombre, gramos) in enumerate(INGREDIENTES):  # unit 080: by hand, one line each, chosen from his
        ficha.get_by_role("button", name="Añadir ingrediente").click()
        linea = ficha.locator(".ingrediente").nth(n)
        linea.get_by_role("combobox", name="Ingrediente").fill(nombre)
        linea.locator('[role="option"]').filter(has_text=nombre).first.click()
        linea = ficha.locator(".ingrediente").nth(n)
        linea.get_by_label("Gramos").fill(str(gramos))
    expect(ficha.locator(".ingrediente")).to_have_count(3)
    expect(ficha.locator(".ingrediente .marca", has_text="estimado")).to_have_count(0)
    expect(ficha.locator(".total-receta")).to_contain_text("2.600 kcal")
    ficha.get_by_label("Olla", exact=True).select_option(label="olla grande (1.500 g)")
    ficha.get_by_label("Olla llena (g)").fill("1400")
    ficha.get_by_role("button", name="Guardar receta").click()
    expect(ficha.locator(".aviso")).to_contain_text("la olla llena no puede pesar menos que su tara")
    ficha.get_by_label("Olla llena (g)").fill("3500")
    if capturar:
        capturar(p, f"recetas-ficha-receta{sufijo}", completa=True)
    ficha.get_by_role("button", name="Guardar receta").click()
    fila = apartado.locator(".elemento", has_text="garbanzos de la abuela")
    expect(fila).to_contain_text("130 kcal/100 g · definitiva")


def test_recetas_vacia_tres_apartados(servidor, abrir, capturar):  # R11
    p = abrir(servidor, "/#recetas")
    for cual, titulo, boton in (("recetas", "Recetas", "＋ Añadir receta"), ("taras", "Taras", "＋ Añadir tara"),
                                ("fijos", "Ingredientes", "＋ Añadir ingrediente")):
        apartado = _apartado(p, cual)
        expect(apartado).to_contain_text(titulo)
        expect(apartado.locator(".vacio-apartado")).to_be_visible()
        expect(apartado.get_by_role("button", name=boton)).to_be_enabled()
    expect(p.get_by_text("próximamente")).to_have_count(0)
    expect(p.locator("#entrada")).to_be_hidden()
    _ergonomia(p, "recetas-vacia")
    capturar(p, "recetas-vacia")


@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_e2e_1_receta_pesada_y_apuntada(servidor, abrir, capturar, oscuro, ir_a):  # R16, E2E-1, R11
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#recetas", oscuro=oscuro)
    _nuevo_fijo(p, "garbanzo seco", 350)
    _nuevo_fijo(p, "aceite de oliva", 900)
    _nuevo_fijo(p, "cebolla", 40)
    _nueva_olla(p, "olla grande", 1500)
    expect(_apartado(p, "taras").locator(".elemento", has_text="olla grande")).to_contain_text("1.500 g")
    _garbanzos(p, capturar, sufijo)
    _ergonomia(p, "recetas" + sufijo)
    capturar(p, f"recetas{sufijo}", completa=True)

    # «Apuntar en hoy» 250 g of the recipe (unit 080: no box in Hoy): its kcal are the recipe's
    recetas = _apartado(p, "recetas")
    recetas.locator(".elemento", has_text="garbanzos de la abuela").click()
    calc = recetas.locator(".ficha .calculadora")
    calc.get_by_label("¿Cuánto has cogido? (g)").fill("250")
    calc.get_by_role("button", name="Apuntar en hoy").click()
    expect(p.locator("#recetas-mensaje")).to_contain_text("Apuntado en hoy: garbanzos de la abuela")
    ir_a(p, "dia")
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    fila = p.locator("#lista-dia .fila").first
    expect(fila).to_contain_text("garbanzos de la abuela")
    expect(fila.locator(".kcal")).to_have_text("325")
    expect(fila).to_contain_text("de tu receta")
    expect(p.locator("#total")).to_contain_text("325 kcal")
    expect(p.locator("#queda-cifra")).to_contain_text("1.475")  # 1.800 of objective − 325
    _ergonomia(p, "dia-de-tu-receta" + sufijo)
    capturar(p, f"dia-de-tu-receta{sufijo}")


def test_editar_fijo_y_olla_en_pantalla(servidor, abrir, capturar, ir_a):  # R18, R14
    p = abrir(servidor, "/#recetas")
    _nuevo_fijo(p, "dulce de guayaba de ciclismo", 90, por="por unidad", alias="guayaba")
    _nueva_olla(p, "olla grande", 1500)
    fijos = _apartado(p, "fijos")
    fila = fijos.locator(".elemento", has_text="dulce de guayaba de ciclismo")
    expect(fila).to_contain_text("90 kcal/unidad")
    expect(fila).to_contain_text("guayaba")
    fila.click()
    ficha = fijos.locator(".ficha-fijo")
    expect(ficha.get_by_label("kcal")).to_have_value("90")
    _ergonomia(p, "ficha-fijo")
    capturar(p, "recetas-ficha-fijo", completa=True)
    ficha.get_by_label("Por").select_option(label="por 100 g")
    ficha.get_by_label("kcal").fill("1500")
    ficha.get_by_role("button", name="Guardar").click()
    expect(ficha.locator(".aviso")).to_contain_text("nada pasa de 900 kcal/100 g")
    ficha.get_by_label("Por").select_option(label="por unidad")
    ficha.get_by_label("kcal").fill("95")
    ficha.get_by_role("button", name="Guardar").click()
    expect(fijos.locator(".elemento", has_text="dulce de guayaba")).to_contain_text("95 kcal/unidad")

    taras = _apartado(p, "taras")
    taras.locator(".elemento", has_text="olla grande").click()
    ficha = taras.locator(".ficha-olla")
    capturar(p, "recetas-ficha-olla", completa=True)
    ficha.get_by_label("Tara (g)").fill("0")
    ficha.get_by_role("button", name="Guardar").click()
    expect(ficha.locator(".aviso")).to_contain_text("tara")
    ficha.get_by_label("Tara (g)").fill("1600")
    ficha.get_by_role("button", name="Guardar").click()
    expect(taras.locator(".elemento", has_text="olla grande")).to_contain_text("1.600 g")

    # deleting asks first; cancelling keeps it
    taras.locator(".elemento", has_text="olla grande").click()
    ficha = taras.locator(".ficha-olla")
    ficha.get_by_role("button", name="Borrar").click()
    expect(ficha).to_contain_text("¿Borrar olla grande?")
    ficha.get_by_role("button", name="No").click()
    ficha.get_by_role("button", name="Cancelar").click()
    expect(taras.locator(".elemento", has_text="olla grande")).to_be_visible()
    taras.locator(".elemento", has_text="olla grande").click()
    ficha.get_by_role("button", name="Borrar").click()
    ficha.get_by_role("button", name="Sí, borrar").click()
    expect(taras.locator(".elemento")).to_have_count(0)
    expect(taras.locator(".vacio-apartado")).to_be_visible()

    # 2 of them logged now count 95 each (unit 080: with «Apuntar en hoy», no box in Hoy)
    fijos.locator(".elemento", has_text="dulce de guayaba de ciclismo").click()
    calc = fijos.locator(".calculadora")
    calc.get_by_label("¿Cuánto has cogido? (unidades)").fill("2")
    calc.get_by_role("button", name="Apuntar en hoy").click()
    expect(p.locator("#recetas-mensaje")).to_contain_text("Apuntado en hoy: dulce de guayaba de ciclismo")
    ir_a(p, "dia")
    expect(p.locator("#lista-dia .fila", has_text="dulce de guayaba de ciclismo").locator(".kcal")).to_have_text("190")


def test_receta_provisional_versiones_y_borrar(servidor, abrir, capturar):  # R11, R7, R14
    rec = servidor_recetario(servidor)
    rec.crear_fijo({"nombre": "garbanzo seco", "kcal": 350, "por": "100g"})
    rec.crear_olla({"nombre": "olla grande", "tara_g": 1500})
    olla = rec.ollas()[0]["id"]
    receta = rec.crear_receta({"nombre": "garbanzos de la abuela", "ingredientes": [
        {"nombre": "garbanzo seco", "cantidad": 600, "unidad": "g", "kcal": 1},
        {"nombre": "aceite", "cantidad": 50, "unidad": "g", "kcal": 450},
        {"nombre": "cebolla", "cantidad": 125, "unidad": "g", "kcal": 50}], "olla_id": olla, "peso_lleno_g": 3500},
        hoy=date(2026, 10, 20))
    rec.otra_tanda(receta["id"], {"ingredientes": [{"nombre": "garbanzo seco", "cantidad": 500, "unidad": "g", "kcal": 1},
                                                   {"nombre": "aceite", "cantidad": 50, "unidad": "g", "kcal": 450}],
                                  "olla_id": olla, "peso_lleno_g": 3500}, hoy=date(2026, 10, 21))
    rec.crear_receta({"nombre": "lasaña de la tía", "ingredientes": [
        {"nombre": "pasta de lasaña", "cantidad": 400, "unidad": "g", "kcal": 1400},
        {"nombre": "carne picada", "cantidad": 800, "unidad": "g", "kcal": 2000},
        {"nombre": "tomate frito", "cantidad": 500, "unidad": "g", "kcal": 600}]}, hoy=date(2026, 10, 21))
    p = abrir(servidor, "/#recetas")
    recetas = _apartado(p, "recetas")
    garbanzos = recetas.locator(".elemento", has_text="garbanzos de la abuela")
    expect(garbanzos).to_contain_text("110 kcal/100 g · definitiva")
    expect(garbanzos).to_contain_text("2 tandas")
    lasana = recetas.locator(".elemento", has_text="lasaña de la tía")
    expect(lasana).to_contain_text("provisional")
    expect(lasana).to_contain_text("3 estimados")
    capturar(p, "recetas-con-datos", completa=True)
    garbanzos.click()
    ficha = recetas.locator(".ficha-receta")
    versiones = ficha.locator(".version")
    expect(versiones).to_have_count(2)
    capturar(p, "recetas-versiones", completa=True)
    versiones.filter(has_text=re.compile("Tanda 1")).get_by_role("button", name="Usar esta").click()
    # unit 026: its «cebolla» (estimated 50 kcal) is now the common «Cebolla», 33 kcal/100 g: 129,6, not 130
    expect(recetas.locator(".elemento", has_text="garbanzos de la abuela")).to_contain_text("129,6 kcal/100 g")

    recetas.locator(".elemento", has_text="lasaña de la tía").click()
    ficha = recetas.locator(".ficha-receta")
    ficha.get_by_role("button", name="Borrar receta").click()
    expect(ficha).to_contain_text("¿Borrar lasaña de la tía?")
    ficha.get_by_role("button", name="No").click()
    expect(ficha).to_be_visible()
    ficha.get_by_role("button", name="Borrar receta").click()
    ficha.get_by_role("button", name="Sí, borrar").click()
    expect(recetas.locator(".elemento", has_text="lasaña de la tía")).to_have_count(0)
    expect(recetas.locator(".elemento")).to_have_count(1)


def servidor_recetario(servidor):
    from tridata.nutricion.recetas import Recetario

    return Recetario(servidor.db)
