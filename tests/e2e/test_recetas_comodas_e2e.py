"""Unit 013 in a real Chromium at 390 × 844, light and dark: the calculator and «Apuntar en hoy», «Pesar
ahora», and hand-written kcal (and kcal/100 g) in Hoy (unit 080: the review is gone with the box).

Uses the `conftest.py` of unit 010 as it is (temporary SQLite, every outside service doubled); the saved
recipes, fixed foods and pot are written straight into that SQLite. Invented data only.
Plans: `guardar-receta` R-57, R-58, R-59 (C-67, C-68, C-70, C-71) · `apuntar-comida` R-8, R-56 (C-63, C-65, C-66).
R1, R2, R3, R4, R5, R7, R9 of the unit.
"""
from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.nutricion.recetas import Recetario  # noqa: E402

ANCHO = 390
INGREDIENTES = [{"nombre": "garbanzo seco", "cantidad": 600, "unidad": "g", "kcal": 1},
                {"nombre": "aceite", "cantidad": 50, "unidad": "g", "kcal": 1},
                {"nombre": "cebolla", "cantidad": 125, "unidad": "g", "kcal": 1}]  # 2.600 kcal


def _guardado(servidor) -> None:
    rec = Recetario(servidor.db)
    for nombre, kcal, por in (("dátiles de prueba", 280, "100g"), ("gel de prueba", 90, "unidad"),
                              ("garbanzo seco", 350, "100g"), ("aceite", 900, "100g"), ("cebolla", 40, "100g")):
        rec.crear_fijo({"nombre": nombre, "kcal": kcal, "por": por})
    olla = rec.crear_olla({"nombre": "olla grande", "tara_g": 1500})["id"]
    rec.crear_receta({"nombre": "garbanzos de la abuela", "ingredientes": INGREDIENTES, "olla_id": olla,
                      "peso_lleno_g": 3500}, date(2026, 10, 19))
    rec.crear_receta({"nombre": "lentejas de prueba", "ingredientes": INGREDIENTES}, date(2026, 10, 19))


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


def _abrir_cosa(p, apartado: str, nombre: str):
    caja = p.locator(f"#apartado-{apartado}")
    caja.locator(".elemento", has_text=nombre).click()
    return caja.locator(".ficha")


@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_calcular_y_apuntar_en_hoy(servidor, abrir, capturar, oscuro, ir_a):  # R1, R2, R3, R9a · R-57, R-58, C-67, C-68
    _guardado(servidor)
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#recetas", oscuro=oscuro)
    ficha = _abrir_cosa(p, "recetas", "garbanzos de la abuela")
    calc = ficha.locator(".calculadora")
    expect(calc).to_be_visible()
    # the card starts with it: nothing of the card comes before the calculator
    assert ficha.evaluate("(f) => f.firstElementChild.classList.contains('calculadora')")
    expect(ficha.locator(".pesar-ahora")).to_have_count(0)  # already weighed: no «Pesar ahora» on top
    cuanto = calc.get_by_label("¿Cuánto has cogido? (g)")
    cuanto.fill("250")
    expect(calc.locator(".resultado")).to_have_text("325 kcal")  # at once, nothing pressed
    _ergonomia(p, "calculadora" + sufijo)
    capturar(p, f"calculadora{sufijo}")
    # 0 does not log anything and says why
    cuanto.fill("0")
    calc.get_by_role("button", name="Apuntar en hoy").click()
    expect(calc.locator(".aviso-calc")).to_contain_text("Escribe cuánto has cogido")
    cuanto.fill("250")
    calc.get_by_role("button", name="Apuntar en hoy").click()
    expect(p.locator("#recetas-mensaje")).to_contain_text("Apuntado en hoy")

    # a fixed food per unit asks for units
    ficha = _abrir_cosa(p, "fijos", "gel de prueba")
    calc = ficha.locator(".calculadora")
    calc.get_by_label("¿Cuánto has cogido? (unidades)").fill("2")
    expect(calc.locator(".resultado")).to_have_text("180 kcal")
    ficha = _abrir_cosa(p, "fijos", "dátiles de prueba")
    calc = ficha.locator(".calculadora")
    calc.get_by_label("¿Cuánto has cogido? (g)").fill("6")
    expect(calc.locator(".resultado")).to_have_text("17 kcal")
    capturar(p, f"calculadora-fijo{sufijo}")

    # back in Hoy, without reloading the page: the list has it and the total went up 325
    ir_a(p, "dia")
    fila = p.locator("#lista-dia .fila", has_text="garbanzos de la abuela")
    expect(fila.locator(".kcal")).to_have_text("325")
    expect(fila).to_contain_text("de tu receta «garbanzos de la abuela»")
    expect(fila.locator(".marca", has_text="estimado")).to_have_count(0)
    expect(p.locator("#total")).to_have_text("325 kcal comidas")
    capturar(p, f"hoy-tras-apuntar{sufijo}")


@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_pesar_ahora(servidor, abrir, capturar, oscuro):  # R3, R4, R9b · R-58, R-59, C-70, C-71
    _guardado(servidor)
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#recetas", oscuro=oscuro)
    ficha = _abrir_cosa(p, "recetas", "lentejas de prueba")
    arriba = ficha.locator(".pesar-ahora")
    expect(arriba).to_contain_text("2.600 kcal la receta entera · sin pesar")
    pesar = arriba.get_by_role("button", name="Pesar ahora")
    expect(pesar).to_be_visible()
    # without weight nor approximate kcal the calculator says why, with «Pesar ahora» in sight
    calc = ficha.locator(".calculadora")
    calc.get_by_label("¿Cuánto has cogido? (g)").fill("300")
    expect(calc.locator(".aviso-calc")).to_contain_text("pésala o pon sus kcal/100 g aproximadas")
    calc.get_by_role("button", name="Apuntar en hoy").click()
    expect(calc.locator(".aviso-calc")).to_contain_text("pésala o pon sus kcal/100 g aproximadas")
    expect(pesar).to_be_in_viewport()
    capturar(p, f"pesar-ahora{sufijo}")
    pesar.click()
    panel = ficha.locator(".pesar")
    panel.get_by_label("Olla", exact=True).select_option(label="olla grande (1.500 g)")
    panel.get_by_label("Olla llena (g)").fill("1400")
    panel.get_by_role("button", name="Guardar el peso").click()
    expect(panel.locator(".aviso-calc")).to_contain_text("la olla llena no puede pesar menos que su tara")
    panel.get_by_label("Olla llena (g)").fill("3500")
    _ergonomia(p, "pesar" + sufijo)
    capturar(p, f"pesar-ahora-abierto{sufijo}")
    panel.get_by_role("button", name="Guardar el peso").click()
    ficha = p.locator("#apartado-recetas .ficha")
    expect(ficha.locator(".estado-receta")).to_have_text("130 kcal/100 g · definitiva")
    expect(ficha.locator(".pesar-ahora")).to_have_count(0)
    calc = ficha.locator(".calculadora")
    calc.get_by_label("¿Cuánto has cogido? (g)").fill("100")
    expect(calc.locator(".resultado")).to_have_text("130 kcal")
    capturar(p, f"pesada{sufijo}")


@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_kcal_a_mano_en_hoy(servidor, abrir, capturar, oscuro):  # R5, R7, R9c · R-8, R-56, C-63
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    p.locator("#anadir").click()
    nueva = p.locator("#anadir-ficha .tarjeta")
    nueva.get_by_label("Alimento").fill("dátiles de prueba")
    nueva.get_by_label("kcal", exact=True).fill("17")
    nueva.get_by_role("button", name="Guardar").click()
    fila = p.locator("#lista-dia .fila", has_text="dátiles de prueba")
    expect(fila.locator(".kcal")).to_have_text("17")
    expect(fila.locator(".marca", has_text="kcal a mano")).to_be_visible()
    expect(fila.locator(".marca", has_text="falta cantidad")).to_have_count(0)
    expect(p.locator("#avisos")).not_to_contain_text("Falta la cantidad")
    expect(p.locator("#total")).to_have_text("17 kcal comidas")
    _ergonomia(p, "kcal-a-mano" + sufijo)
    capturar(p, f"kcal-a-mano{sufijo}")

    fila.click()
    ficha = p.locator("#lista-dia .tarjeta")
    ficha.get_by_label("Cantidad").fill("6")
    ficha.get_by_role("button", name="kcal/100 g").click()
    ficha.get_by_label("kcal/100 g", exact=True).fill("280")
    expect(ficha.get_by_label("kcal", exact=True)).to_have_value("16.8")
    ficha.get_by_label("Cantidad").fill("10")
    expect(ficha.get_by_label("kcal", exact=True)).to_have_value("28")
    ficha.get_by_label("Cantidad").fill("6")
    expect(ficha.get_by_label("kcal", exact=True)).to_have_value("16.8")
    _ergonomia(p, "kcal-100g" + sufijo)
    capturar(p, f"kcal-100g{sufijo}")
    ficha.get_by_role("button", name="Guardar cambios").click()
    fila = p.locator("#lista-dia .fila", has_text="dátiles de prueba")
    expect(fila.locator(".kcal")).to_have_text("17")
    expect(fila.locator(".marca", has_text="kcal a mano")).to_have_count(0)
    expect(p.locator("#total")).to_have_text("17 kcal comidas")


# Unit 080 (S-6): the walk of «kcal a mano» and kcal/100 g in the review went with the box to write or dictate.


def test_el_icono_nuevo(servidor, abrir, capturar):  # R8 (010 R13)
    p = abrir(servidor, "/static/icono-512.png")
    tam = p.evaluate("() => { const i = document.querySelector('img'); return [i.naturalWidth, i.naturalHeight]; }")
    assert tam == [512, 512]
    capturar(p, "icono-512")
    p = abrir(servidor, "/static/icono.svg")
    capturar(p, "icono-svg")
