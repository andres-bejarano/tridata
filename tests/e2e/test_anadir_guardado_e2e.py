"""Unit 019 in a real Chromium at 390 × 844 and 390 × 500 (the height the keyboard leaves), light and dark: the
search box of recipes and ingredients in «Añadir a mano», in the review and in the lines of a recipe; a recipe
whole or by fractions; «Ingredientes» on every screen.

Uses the `conftest.py` of unit 010 as it is (temporary SQLite, every outside service doubled); the saved
things are written straight into that SQLite. Invented data only (§Criterios de aceptación of the unit).
Plans: `apuntar-comida` R-93 (C-131, C-132) · `guardar-receta` R-94 (C-133), R-57 (C-134), R-58 · S-21.
R1, R2, R4, R5, R6, R7, R8 of the unit.
"""
from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.ia import AlimentoInterpretado, ListaInterpretada  # noqa: E402
from tridata.nutricion import Alimento  # noqa: E402
from tridata.nutricion.comunes import COMUNES  # noqa: E402
from tridata.nutricion.recetas import Recetario  # noqa: E402

HOY = date(2026, 10, 21)  # the same fixed «today» as conftest.py

ANCHO = 390
ALTO_CON_TECLADO = 500
FIJOS = (("lenteja seca de prueba", 340, "100g"), ("aceite de prueba", 900, "100g"), ("cebolla de prueba", 40, "100g"),
         ("avena de prueba", 370, "100g"), ("leche de prueba", 46, "100g"), ("plátano de prueba", 89, "100g"),
         ("gel de prueba", 90, "unidad"))


def _linea(nombre: str, gramos: float) -> dict:
    return {"nombre": nombre, "cantidad": gramos, "unidad": "g", "kcal": 1, "guardado": nombre}


def _guardado(servidor, comido: float = 1000) -> None:
    rec = Recetario(servidor.db)
    for nombre, kcal, por in FIJOS:
        rec.crear_fijo({"nombre": nombre, "kcal": kcal, "por": por})
    rec.crear_fijo({"nombre": "Lentejas cocidas bote", "kcal": 90, "por": "100g", "alias": ["bote de lentejas"]})
    rec.crear_receta({"nombre": "Lentejas de la abuela", "peso_neto_g": 2500, "ingredientes": [
        _linea("lenteja seca de prueba", 500), _linea("aceite de prueba", 30), _linea("cebolla de prueba", 75)]}, HOY)
    rec.crear_receta({"nombre": "Porridge de prueba", "ingredientes": [
        _linea("avena de prueba", 80), _linea("leche de prueba", 250), _linea("plátano de prueba", 100)]}, HOY)
    rec.crear_receta({"nombre": "Lentejas con kfjd", "peso_neto_g": 1000, "ingredientes": [
        _linea("lenteja seca de prueba", 200), {"nombre": "kfjd", "cantidad": 50, "unidad": "g", "kcal": None,
                                                "dudoso": True}]}, HOY)
    if comido:
        servidor.apuntar("comida de antes", HOY, 500, comido)


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


def _anadir(p):
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    p.locator("#anadir").click()
    return p.locator("#anadir-ficha .tarjeta")


def _opciones(caja):
    return caja.get_by_role("option")


def _nombres(caja) -> list[str]:
    return [t.split("\n")[0].strip() for t in _opciones(caja).all_inner_texts()]


# -- R1: the search box -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_buscador_en_anadir(servidor, abrir, capturar, oscuro):  # R1, R7
    _guardado(servidor)
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ficha = _anadir(p)
    nombre = ficha.get_by_role("combobox", name="Alimento")
    nombre.click()
    lista = ficha.get_by_role("listbox")
    expect(lista).to_be_visible()  # empty and touched: the whole list, grouped
    expect(nombre).to_have_attribute("aria-expanded", "true")
    expect(lista.locator(".grupo")).to_have_text(["Recetas", "Ingredientes", "Comunes"])  # unit 026: + the common
    expect(_opciones(ficha)).to_have_count(3 + len(FIJOS) + 1 + len(COMUNES))
    capturar(p, f"buscador-vacio{sufijo}")
    nombre.fill("lent")
    # «lenteja seca de prueba» (an ingredient of §Criterios) also has «lent»: R1 lists the three it is about
    expect(_opciones(ficha)).to_have_count(4 + 2)  # unit 026: + the common «Lentejas cocidas» and «… crudas», after
    assert _nombres(ficha) == ["Lentejas de la abuela", "lenteja seca de prueba", "Lentejas cocidas bote",
                               "Lentejas con kfjd", "Lentejas cocidas", "Lentejas crudas"]
    expect(_opciones(ficha).nth(0)).to_contain_text("Receta · 80 kcal/100 g")
    expect(_opciones(ficha).nth(2)).to_contain_text("Ingrediente · 90 kcal/100 g")
    expect(_opciones(ficha).nth(3)).to_contain_text("corrígela en Recetas")
    expect(_opciones(ficha).nth(3)).to_have_attribute("aria-disabled", "true")
    _ergonomia(p, "buscador" + sufijo)
    capturar(p, f"buscador-lent{sufijo}")
    nombre.fill("LENT")
    expect(_opciones(ficha)).to_have_count(4 + 2)
    nombre.fill("léntejas")  # without the accent: «lentejas», the three of R1 («lenteja seca» has no «s»)
    expect(_opciones(ficha)).to_have_count(3 + 2)  # unit 026: + the two common ones, after
    assert _nombres(ficha) == ["Lentejas de la abuela", "Lentejas cocidas bote", "Lentejas con kfjd",
                               "Lentejas cocidas", "Lentejas crudas"]
    nombre.fill("bote de")
    expect(_opciones(ficha)).to_have_count(1)
    expect(_opciones(ficha).nth(0)).to_contain_text("Lentejas cocidas bote")
    nombre.fill("porr")
    expect(_opciones(ficha).nth(0)).to_contain_text("Receta · sin pesar · 500 kcal entera")
    nombre.fill("xyz")
    expect(_opciones(ficha)).to_have_count(0)
    expect(lista).to_be_hidden()
    # the disabled one cannot be chosen
    nombre.fill("kfjd")
    _opciones(ficha).nth(0).click(force=True)  # aria-disabled: Playwright would not even try
    expect(ficha.get_by_label("Cantidad")).to_be_visible()
    expect(ficha.locator(".elegida")).to_have_count(0)


def test_sin_pc_el_buscador_no_ensena_nada(servidor, abrir):  # R1
    _guardado(servidor)
    p = abrir(servidor, "/#dia")
    p.route("**/api/recetas", lambda ruta: ruta.abort())
    ficha = _anadir(p)
    ficha.get_by_role("combobox", name="Alimento").fill("lent")
    expect(_opciones(ficha)).to_have_count(0)
    ficha.get_by_label("kcal", exact=True).fill("300")
    ficha.get_by_role("button", name="Guardar").click()
    expect(p.locator("#lista-dia .fila", has_text="lent")).to_be_visible()


# -- R2: choose and only say how much ------------------------------------------------------------------------------

@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_anadir_el_bote_eligiendolo(servidor, abrir, capturar, oscuro):  # R2 · C-131
    _guardado(servidor)
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    expect(p.locator("#total")).to_have_text("1.000 kcal comidas")
    ficha = _anadir(p)
    ficha.get_by_role("combobox", name="Alimento").fill("lent")
    _opciones(ficha).filter(has_text="Lentejas cocidas bote").click()
    elegida = ficha.locator(".elegida")
    expect(elegida).to_contain_text("de tu ingrediente")
    expect(ficha.get_by_label("Cantidad")).to_have_count(0)
    expect(ficha.get_by_label("kcal", exact=True)).to_have_count(0)
    elegida.get_by_label("Gramos").fill("400")
    expect(elegida.locator(".kcal-elegido")).to_have_text("360 kcal")
    _ergonomia(p, "elegido" + sufijo)
    capturar(p, f"anadir-elegido{sufijo}")
    ficha.get_by_role("button", name="Guardar").click()
    fila = p.locator("#lista-dia .fila", has_text="Lentejas cocidas bote")
    expect(fila.locator(".kcal")).to_have_text("360")
    expect(fila).to_contain_text("400 g")
    expect(fila).to_contain_text("de tu ingrediente «Lentejas cocidas bote»")
    expect(fila.locator(".marca", has_text="estimado")).to_have_count(0)
    expect(p.locator("#total")).to_have_text("1.360 kcal comidas")


def test_anadir_gel_por_unidades_receta_por_gramos_y_cambiar(servidor, abrir):  # R2
    _guardado(servidor)
    p = abrir(servidor, "/#dia")
    ficha = _anadir(p)
    nombre = ficha.get_by_role("combobox", name="Alimento")
    nombre.fill("gel")
    _opciones(ficha).first.click()
    ficha.get_by_label("Unidades").fill("2")
    expect(ficha.locator(".kcal-elegido")).to_have_text("180 kcal")
    ficha.get_by_role("button", name="Cambiar").click()  # back to the free fields
    expect(ficha.locator(".elegida")).to_have_count(0)
    expect(ficha.get_by_label("Cantidad")).to_be_visible()
    expect(ficha.get_by_label("kcal", exact=True)).to_be_visible()
    nombre.fill("abuela")
    _opciones(ficha).first.click()
    ficha.get_by_label("Gramos").fill("300")
    expect(ficha.locator(".kcal-elegido")).to_have_text("240 kcal")
    ficha.get_by_role("button", name="Guardar").click()
    fila = p.locator("#lista-dia .fila", has_text="Lentejas de la abuela")
    expect(fila.locator(".kcal")).to_have_text("240")
    expect(fila).to_contain_text("de tu receta «Lentejas de la abuela»")


# -- R4: whole or by fractions -------------------------------------------------------------------------------------

@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_porridge_entero_y_media_abuela(servidor, abrir, capturar, oscuro):  # R4 · R-58
    _guardado(servidor)
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ficha = _anadir(p)
    ficha.get_by_role("combobox", name="Alimento").fill("porr")
    _opciones(ficha).first.click()
    elegida = ficha.locator(".elegida")
    expect(elegida.get_by_role("button")).to_contain_text(["Entera", "½", "⅓", "¼"])
    expect(elegida.get_by_label("Gramos")).to_have_count(0)
    expect(elegida).to_contain_text("sin pesar: apúntala entera o por fracciones, o pésala")
    elegida.get_by_role("button", name="½").click()
    expect(elegida.locator(".kcal-elegido")).to_have_text("250 kcal · provisional")
    elegida.get_by_role("button", name="Entera").click()
    expect(elegida.get_by_role("button", name="Entera")).to_have_attribute("aria-pressed", "true")
    expect(elegida.locator(".kcal-elegido")).to_have_text("500 kcal · provisional")
    _ergonomia(p, "fracciones" + sufijo)
    capturar(p, f"fracciones{sufijo}")
    ficha.get_by_role("button", name="Guardar").click()
    fila = p.locator("#lista-dia .fila", has_text="Porridge de prueba")
    expect(fila.locator(".kcal")).to_have_text("500")
    expect(fila).to_contain_text("la receta entera")
    expect(fila.locator(".marca", has_text="provisional")).to_be_visible()

    ficha = _anadir(p)
    ficha.get_by_role("combobox", name="Alimento").fill("abuela")
    _opciones(ficha).first.click()
    elegida = ficha.locator(".elegida")
    expect(elegida.get_by_label("Gramos")).to_be_visible()  # weighed: grams too
    for boton, kcal in (("Entera", "2.000 kcal"), ("¼", "500 kcal"), ("½", "1.000 kcal")):
        elegida.get_by_role("button", name=boton).click()
        expect(elegida.locator(".kcal-elegido")).to_have_text(kcal)
    ficha.get_by_role("button", name="Guardar").click()
    fila = p.locator("#lista-dia .fila", has_text="Lentejas de la abuela")
    expect(fila.locator(".kcal")).to_have_text("1.000")
    expect(fila).to_contain_text("½ receta")
    expect(fila.locator(".marca", has_text="provisional")).to_have_count(0)
    expect(p.locator("#total")).to_have_text("2.500 kcal comidas")
    capturar(p, f"lista-con-fracciones{sufijo}")


@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_ficha_del_porridge_entera_y_apuntar(servidor, abrir, capturar, oscuro, ir_a):  # R4 · C-134
    _guardado(servidor, comido=0)
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#recetas", oscuro=oscuro)
    p.locator("#apartado-recetas .elemento", has_text="Porridge de prueba").click()
    calc = p.locator("#apartado-recetas .ficha .calculadora")
    calc.get_by_role("button", name="Entera").click()
    expect(calc.locator(".resultado")).to_have_text("500 kcal · provisional")
    _ergonomia(p, "calculadora-fracciones" + sufijo)
    capturar(p, f"calculadora-fracciones{sufijo}")
    calc.get_by_role("button", name="Apuntar en hoy").click()
    expect(p.locator("#recetas-mensaje")).to_contain_text("Apuntado en hoy: Porridge de prueba, la receta entera, 500 kcal")
    # the one with a doubtful ingredient cannot be logged
    p.locator("#apartado-recetas .elemento", has_text="Lentejas con kfjd").click()
    calc = p.locator("#apartado-recetas .ficha .calculadora")
    calc.get_by_role("button", name="Entera").click()
    calc.get_by_role("button", name="Apuntar en hoy").click()
    expect(calc.locator(".aviso-calc")).to_contain_text("Corrige la receta")
    ir_a(p, "dia")
    fila = p.locator("#lista-dia .fila", has_text="Porridge de prueba")
    expect(fila.locator(".kcal")).to_have_text("500")
    expect(fila).to_contain_text("la receta entera")
    expect(p.locator("#total")).to_have_text("500 kcal comidas")


# -- R5: in the review · C-132 -------------------------------------------------------------------------------------

def _unas_lentejas(texto: str, hoy: date) -> ListaInterpretada:
    return ListaInterpretada(alimentos=[AlimentoInterpretado(
        nombre="unas lentejas", cantidad=300, unidad="g", dia=hoy, kcal=350, proteina_g=None, hidratos_g=None,
        grasa_g=None, estimado=True, dudoso=False, supuestos=["un plato"])])


@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_en_la_revision(servidor, abrir, capturar, oscuro):  # R5 · C-132
    _guardado(servidor)
    servidor.interprete.interpretar.side_effect = _unas_lentejas
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    p.locator("#texto").fill("unas lentejas")
    p.locator("#enviar").click()
    tarjeta = p.locator("#lista article").first
    expect(tarjeta.locator(".marca", has_text="kcal estimadas")).to_be_visible()
    nombre = tarjeta.get_by_role("combobox", name="Alimento")
    nombre.fill("lent")
    capturar(p, f"revision-buscador{sufijo}")
    _opciones(tarjeta).filter(has_text="Lentejas de la abuela").click()
    tarjeta = p.locator("#lista article").first
    tarjeta.get_by_role("button", name="½").click()
    tarjeta = p.locator("#lista article").first
    expect(tarjeta.locator(".kcal-elegido")).to_have_text("1.000 kcal")
    expect(tarjeta.locator(".marca", has_text="de tu receta «Lentejas de la abuela»")).to_be_visible()
    expect(tarjeta.locator(".marca", has_text="kcal estimadas")).to_have_count(0)
    expect(p.locator("#simulacion")).to_contain_text("1.000 kcal de hoy")  # the simulation with those kcal
    tarjeta.get_by_role("button", name="¿Cuánto me cabe?").click()
    expect(tarjeta.locator(".cabe")).to_be_visible()
    _ergonomia(p, "revision-elegida" + sufijo)
    capturar(p, f"revision-elegida{sufijo}", completa=True)
    p.locator("#confirmar").click()
    fila = p.locator("#lista-dia .fila", has_text="Lentejas de la abuela")
    expect(fila.locator(".kcal")).to_have_text("1.000")
    expect(fila).to_contain_text("½ receta")
    expect(p.locator("#total")).to_have_text("2.000 kcal comidas")


# -- R6: the lines of a recipe · C-133 ------------------------------------------------------------------------------

@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_receta_nueva_con_ingredientes_elegidos(servidor, abrir, capturar, oscuro):  # R6 · C-133
    _guardado(servidor, comido=0)
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#recetas", oscuro=oscuro)
    p.locator("#apartado-recetas").get_by_role("button", name="＋ Añadir receta").click()
    ficha = p.locator("#apartado-recetas .ficha")
    ficha.get_by_label("Nombre").fill("Lentejas rápidas")
    ficha.get_by_role("button", name="Añadir ingrediente").click()
    linea = ficha.locator(".ingrediente").nth(0)
    linea.get_by_role("combobox", name="Ingrediente").fill("lent")
    expect(_opciones(linea)).to_have_count(2 + 2)  # unit 026: + the two common ones, after
    assert _nombres(linea) == ["lenteja seca de prueba", "Lentejas cocidas bote",  # only ingredients, never a recipe
                               "Lentejas cocidas", "Lentejas crudas"]
    capturar(p, f"linea-buscador{sufijo}")
    _opciones(linea).filter(has_text="Lentejas cocidas bote").click()
    linea = ficha.locator(".ingrediente").nth(0)
    linea.get_by_label("Gramos").fill("400")
    expect(linea.locator(".kcal-linea")).to_have_text("360 kcal · ingrediente")
    ficha.get_by_role("button", name="Añadir ingrediente").click()
    otra = ficha.locator(".ingrediente").nth(1)
    otra.get_by_role("combobox", name="Ingrediente").fill("cebolla de")
    _opciones(otra).first.click()
    otra = ficha.locator(".ingrediente").nth(1)
    otra.get_by_label("Gramos").fill("40")
    expect(otra.locator(".kcal-linea")).to_have_text("16 kcal · ingrediente")
    expect(ficha.locator(".total-receta")).to_contain_text("376 kcal la receta entera")
    # a line written by hand without choosing stays as before
    ficha.get_by_role("button", name="Añadir ingrediente").click()
    mano = ficha.locator(".ingrediente").nth(2)
    mano.get_by_role("combobox", name="Ingrediente").fill("sal a ojo")
    mano.get_by_label("Cantidad").fill("2")
    mano.get_by_label("kcal").fill("0")
    _ergonomia(p, "lineas-elegidas" + sufijo)
    capturar(p, f"lineas-elegidas{sufijo}")
    ficha.get_by_role("button", name="Guardar receta").click()
    expect(p.locator("#recetas-mensaje")).to_contain_text("Guardada: Lentejas rápidas")
    receta = next(r for r in Recetario(servidor.db).recetas() if r["nombre"] == "Lentejas rápidas")
    assert [(i["nombre"], i["kcal"], i["estado"]) for i in receta["en_uso"]["ingredientes"]] == [
        ("Lentejas cocidas bote", 360, "guardado"), ("cebolla de prueba", 16, "guardado"), ("sal a ojo", 0, "estimado")]
    assert receta["en_uso"]["kcal_total"] == 376


# -- R7: with the keyboard open -------------------------------------------------------------------------------------

@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_con_el_teclado_abierto(servidor, abrir, capturar, oscuro):  # R7
    _guardado(servidor)
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    p.set_viewport_size({"width": ANCHO, "height": ALTO_CON_TECLADO})
    ficha = _anadir(p)
    nombre = ficha.get_by_role("combobox", name="Alimento")
    nombre.fill("lentejas")
    expect(_opciones(ficha)).to_have_count(3 + 2)  # unit 026: + the common «Lentejas cocidas» and «… crudas»
    expect(_opciones(ficha).first).to_be_in_viewport()
    caja_campo = nombre.bounding_box()
    caja_lista = ficha.get_by_role("listbox").bounding_box()
    assert caja_lista["y"] >= caja_campo["y"] + caja_campo["height"] - 1, "the list goes under the field"
    _ergonomia(p, "teclado" + sufijo)
    capturar(p, f"buscador-teclado{sufijo}")
    nombre.press("ArrowDown")
    nombre.press("ArrowDown")
    nombre.press("Enter")  # the second: Lentejas cocidas bote
    expect(ficha.locator(".elegida")).to_contain_text("de tu ingrediente")
    expect(nombre).to_have_value("Lentejas cocidas bote")
    ficha.get_by_role("button", name="Cambiar").click()
    nombre.fill("porr")
    expect(ficha.get_by_role("listbox")).to_be_visible()
    nombre.press("Escape")
    expect(ficha.get_by_role("listbox")).to_be_hidden()


# -- R8: «Ingredientes» everywhere -----------------------------------------------------------------------------------

def _sin_alimento_fijo(p, pantalla: str) -> None:
    texto = p.evaluate("document.body.innerText")
    assert "alimento fijo" not in texto.lower() and "alimentos fijos" not in texto.lower(), pantalla


@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_se_llaman_ingredientes(servidor, abrir, capturar, oscuro, ir_a):  # R8 · S-21, R-55
    _guardado(servidor, comido=0)
    sufijo = "-oscuro" if oscuro else ""
    servidor.registro.guardar([Alimento(
        nombre="dátiles de prueba", dia=HOY, cantidad=6, unidad="g", kcal=16.8,
        origen="de tu alimento fijo «dátiles de prueba»")], hoy=HOY, texto="dátiles", sin_cambios=True)
    servidor.interprete.interpretar.side_effect = _unas_lentejas
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    fila = p.locator("#lista-dia .fila", has_text="dátiles de prueba")
    expect(fila).to_contain_text("de tu ingrediente «dátiles de prueba»")
    expect(fila.locator(".kcal")).to_have_text("17")
    _sin_alimento_fijo(p, "hoy")
    p.locator("#texto").fill("unas lentejas")
    p.locator("#enviar").click()
    expect(p.locator("#lista article")).to_have_count(1)
    _sin_alimento_fijo(p, "revisión")
    p.goto(servidor.url + "/#recetas")
    apartado = p.locator("#apartado-fijos")
    expect(apartado).to_contain_text("Ingredientes")
    expect(apartado.get_by_role("button", name="＋ Añadir ingrediente")).to_be_visible()
    capturar(p, f"recetas-ingredientes{sufijo}", completa=True)
    _sin_alimento_fijo(p, "recetas")
    apartado.locator(".elemento", has_text="gel de prueba").click()
    apartado.locator(".calculadora").get_by_label("¿Cuánto has cogido? (unidades)").fill("2")
    apartado.locator(".calculadora").get_by_role("button", name="Apuntar en hoy").click()
    expect(p.locator("#recetas-mensaje")).to_contain_text("Apuntado en hoy: gel de prueba")
    p.locator("#apartado-recetas .elemento", has_text="Lentejas de la abuela").click()
    expect(p.locator("#apartado-recetas .ficha .ingrediente").first).to_contain_text("ingrediente")
    _sin_alimento_fijo(p, "receta abierta")
    ir_a(p, "dia")
    expect(p.locator("#lista-dia .fila", has_text="gel de prueba")).to_contain_text("de tu ingrediente «gel de prueba»")
    p.goto(servidor.url + "/ajustes")
    expect(p.locator("body")).to_be_visible()
    _sin_alimento_fijo(p, "ajustes")


def test_sin_ingredientes(servidor, abrir):  # R8
    p = abrir(servidor, "/#recetas")
    expect(p.locator("#apartado-fijos")).to_contain_text("Aún no hay ingredientes.")
