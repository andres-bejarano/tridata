"""Unit 021 in a real Chromium at 390 × 844, light and dark: the assistant that acts (recorrido E2E-2).

(a) the steps of today: card → Confirmar → «Hecho» and Hoy with the new margin; (b) a new ingredient from
Nutrición › Día → Confirmar → it is in Recetas › Ingredientes; (c) a food and a change together: «Confirmar y
guardar» saves the food and leaves the card waiting. Also the choice between two, a «no», the box only in Hoy and
Nutrición › Día, no horizontal scroll and buttons of 44 px or more. Plans `pedir-al-asistente`: C-82, C-83,
C-104; R1, R2, R5, R10, R15 of the unit. Uses the `conftest.py` of unit 010 (Claude is a `MagicMock` that returns
the `cambios` of each case; «today» is Wednesday 2026-10-21, 10,000 default steps, 0.04 kcal per step).
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.ia import AlimentoInterpretado, CambioInterpretado, Interpretacion  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])


def _claude(servidor, *cambios, alimentos=()):
    servidor.interprete.interpretar.side_effect = lambda texto, hoy: Interpretacion(
        alimentos=list(alimentos), cambios=list(cambios))


def _avena(hoy=HOY):
    return AlimentoInterpretado(nombre="avena", cantidad=60, unidad="g", dia=hoy, kcal=228, proteina_g=None,
                                hidratos_g=None, grasa_g=None, estimado=True, dudoso=False, supuestos=[])


def _escribir(p, texto):
    p.locator("#texto").fill(texto)
    p.locator("#enviar").click()


def _sin_scroll(p):
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"horizontal scroll ({ancho}px)"


def _botones_44(caja):
    for b in caja.locator("button:visible").all():
        assert b.bounding_box()["height"] >= 44, b.inner_text()


def _entera_a_la_vista(p, tarjeta):
    caja = tarjeta.bounding_box()
    assert caja["x"] >= 0 and caja["x"] + caja["width"] <= 390


def _abrir_hoy(abrir, servidor, **k):
    p = abrir(servidor, "/", esperar=False, **k)
    expect(p.locator("section#hoy")).to_be_visible()
    expect(p.locator("#hoy-comida .cifra")).not_to_have_text("—")
    return p


# -- (a) R1, R2 · C-82, C-83: the steps of today from Hoy ----------------------------------------------------------

@TEMAS
def test_pasos_de_hoy_tarjeta_confirmar_y_hecho(servidor, abrir, capturar, oscuro):  # R1, R2, R15
    _claude(servidor, CambioInterpretado(accion="pasos_dia", valor=15000, dia=HOY))
    p = _abrir_hoy(abrir, servidor, oscuro=oscuro)
    antes = p.locator("#hoy-comida .cifra").inner_text()
    _escribir(p, "hoy en vez de 10 mil pasos son 15 mil")
    tarjeta = p.locator("section#hoy #revision .tarjeta-cambio")
    expect(tarjeta).to_have_count(1)
    expect(tarjeta).to_contain_text("Voy a cambiar los pasos previstos del 21-10: 10.000 → 15.000")
    expect(tarjeta).to_contain_text("solo ese día, los pasos por defecto siguen en 10.000")
    for nombre in ("Confirmar", "Corregir", "Descartar"):
        expect(tarjeta.get_by_role("button", name=nombre, exact=True)).to_be_visible()
    # only changes: «Confirmar y guardar» is not shown
    expect(p.locator("section#hoy #confirmar")).to_be_hidden()
    _sin_scroll(p)
    _entera_a_la_vista(p, tarjeta)
    _botones_44(tarjeta)
    capturar(p, f"tarjeta-pasos-{'oscuro' if oscuro else 'claro'}")
    assert servidor.objetivos.del_dia(HOY) is None  # nothing changed yet

    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(p.locator("#mensaje")).to_contain_text("Hecho: los pasos previstos del 21-10 son 15.000.")
    expect(p.locator("section#hoy #revision")).to_be_hidden()  # nothing left to decide: the review closes
    assert servidor.objetivos.del_dia(HOY)["pasos"] == 15000
    expect(p.locator("#hoy-comida .cifra")).not_to_have_text(antes)  # 200 kcal more: 5,000 steps × 0.04
    capturar(p, f"tarjeta-hecho-{'oscuro' if oscuro else 'claro'}")


# -- (b) R5 · C-104: a new ingredient from Nutrición › Día ---------------------------------------------------------

@TEMAS
def test_ingrediente_nuevo_desde_nutricion(servidor, abrir, ir_a, oscuro):  # R5, R15
    _claude(servidor, CambioInterpretado(accion="guardar_ingrediente", objeto="queso de prueba nuevo", valor=300,
                                         unidad="g"))
    p = abrir(servidor, oscuro=oscuro)
    _escribir(p, "queso de prueba nuevo son 300 por cada 100")
    tarjeta = p.locator("section#dia #revision .tarjeta-cambio")
    expect(tarjeta).to_contain_text("Voy a guardar: queso de prueba nuevo 300 kcal/100 g en Ingredientes")
    _sin_scroll(p)
    _botones_44(tarjeta)
    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(p.locator("#mensaje")).to_contain_text("Hecho")
    ir_a(p, "recetas")
    expect(p.locator("#apartado-fijos")).to_contain_text("queso de prueba nuevo")


# -- (c) R10 · R-72: a food and a change in the same review --------------------------------------------------------

@TEMAS
def test_comida_y_cambio_juntos(servidor, abrir, capturar, oscuro):  # R10, R15
    _claude(servidor, CambioInterpretado(accion="pasos_dia", valor=15000, dia=HOY), alimentos=[_avena()])
    p = abrir(servidor, oscuro=oscuro)
    _escribir(p, "avena 60 g y hoy son 15 mil pasos")
    revision = p.locator("section#dia #revision")
    expect(revision.locator(".tarjeta-cambio")).to_have_count(1)
    expect(revision.locator("#lista article")).to_have_count(1)
    _sin_scroll(p)
    capturar(p, f"comida-y-cambio-{'oscuro' if oscuro else 'claro'}", completa=True)
    revision.get_by_role("button", name="Confirmar y guardar").click()
    expect(p.locator("#lista-dia .fila", has_text="avena")).to_be_visible()
    expect(revision).to_be_visible()  # the card is still waiting
    expect(revision.locator(".tarjeta-cambio")).to_contain_text("10.000 → 15.000")
    expect(revision.locator("#confirmar")).to_be_hidden()
    assert servidor.objetivos.del_dia(HOY) is None
    revision.locator(".tarjeta-cambio").get_by_role("button", name="Descartar", exact=True).click()
    expect(revision).to_be_hidden()
    with sqlite3.connect(servidor.db) as conn:
        assert conn.execute("SELECT estado FROM tarjeta_asistente").fetchall() == [("descartada",)]


# -- R7: choosing between two, and a «no» ---------------------------------------------------------------------------

@TEMAS
def test_elegir_entre_dos_y_un_no(servidor, abrir, capturar, oscuro):  # R7, R13, R15
    for hora in ("10:00", "17:30"):
        fila = servidor.apuntar("yogur natural de prueba", HOY, 125, 75)
        with sqlite3.connect(servidor.db) as conn:
            conn.execute("UPDATE alimento_apuntado SET creado = ? WHERE id = ?", (f"{HOY}T{hora}:00", fila["id"]))
    _claude(servidor, CambioInterpretado(accion="quitar_alimento", objeto="yogur"),
            CambioInterpretado(accion="borrar_dia", dia=HOY))
    p = abrir(servidor, oscuro=oscuro)
    _escribir(p, "quita el yogur y borra el día de ayer")
    revision = p.locator("section#dia #revision")
    eleccion = revision.locator(".tarjeta-elegir")
    expect(eleccion.get_by_role("button")).to_have_count(3)  # the two yogurts and «Descartar»
    expect(eleccion).to_contain_text("17:30")
    no = revision.locator(".tarjeta-no")
    expect(no).to_contain_text("Eso no lo cambio yo: en Nutrición › Día")
    _sin_scroll(p)
    _botones_44(revision.locator("#cambios"))
    capturar(p, f"elegir-y-no-{'oscuro' if oscuro else 'claro'}")
    eleccion.get_by_role("button", name="17:30").click()
    tarjeta = revision.locator(".tarjeta-cambio")
    expect(tarjeta).to_contain_text("Voy a quitar del 21-10: yogur natural de prueba 125 g (75 kcal), 17:30")
    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(p.locator("#mensaje")).to_contain_text("Hecho")
    expect(p.locator("#lista-dia .fila", has_text="yogur")).to_have_count(1)
    no.get_by_role("button", name="Vale").click()
    expect(revision).to_be_hidden()


# -- R9: correcting on the card -------------------------------------------------------------------------------------

def test_corregir_en_la_tarjeta(servidor, abrir):  # R9
    _claude(servidor, CambioInterpretado(accion="guardar_ingrediente", objeto="queso mozzarella de prueba",
                                         valor=280, unidad="g"))
    p = abrir(servidor)
    _escribir(p, "queso mozzarella de prueba son 280 por cada 100")
    tarjeta = p.locator("#revision .tarjeta-cambio")
    tarjeta.get_by_role("button", name="Corregir", exact=True).click()
    tarjeta.get_by_label("kcal/100 g").fill("1280")
    tarjeta.get_by_role("button", name="Rehacer").click()
    expect(tarjeta).to_contain_text("nada pasa de 900 kcal/100 g")
    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(tarjeta).to_contain_text("nada pasa de 900 kcal/100 g")
    tarjeta.get_by_role("button", name="Corregir", exact=True).click()
    tarjeta.get_by_label("kcal/100 g").fill("285")
    tarjeta.get_by_role("button", name="Rehacer").click()
    expect(tarjeta).to_contain_text("285 kcal/100 g en Ingredientes")
    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(p.locator("#mensaje")).to_contain_text("Hecho")


# -- R12: what was before changed while the card was in sight --------------------------------------------------------

def test_lo_de_antes_cambio(servidor, abrir):  # R12
    _claude(servidor, CambioInterpretado(accion="pasos_dia", valor=15000, dia=HOY))
    p = abrir(servidor)
    _escribir(p, "hoy son 15 mil pasos")
    tarjeta = p.locator("#revision .tarjeta-cambio")
    expect(tarjeta).to_contain_text("10.000 → 15.000")
    servidor.objetivos.cambiar(HOY, pasos=12000)  # by hand, meanwhile
    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(tarjeta).to_contain_text("Ha cambiado desde que la viste: mírala otra vez")
    expect(tarjeta).to_contain_text("12.000 → 15.000")
    assert servidor.objetivos.del_dia(HOY)["pasos"] == 12000
    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(p.locator("#mensaje")).to_contain_text("Hecho")
    assert servidor.objetivos.del_dia(HOY)["pasos"] == 15000


# -- R15 · S-16: the box only in Hoy and Nutrición › Día ------------------------------------------------------------

def test_la_caja_solo_en_hoy_y_en_dia(servidor, abrir, ir_a):  # R15
    p = _abrir_hoy(abrir, servidor)
    expect(p.locator("#entrada")).to_be_visible()
    for sitio in ("balance", "recetas", "fitness", "asistente"):
        ir_a(p, sitio)
        expect(p.locator("#entrada")).to_be_hidden()
    ir_a(p, "dia")
    expect(p.locator("#entrada")).to_be_visible()
