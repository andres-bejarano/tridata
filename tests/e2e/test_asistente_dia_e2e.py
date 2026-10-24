"""Unit 022 in a real Chromium at 390 × 844, light and dark: substitute a food from Hoy (recorrido E2E-2, C-94).

«quita el yogur, al final fue una manzana» → the card with what goes and what comes (and where its kcal come from)
→ Confirmar → the list with the apple and the day 5 kcal higher. Also the card of a past date of a setting (R-87)
and the delay of the weight target (R-76), for the evidence screenshots. Uses the `conftest.py` of unit 010
(Claude is a `MagicMock` that returns the `cambios` of each case; «today» is Wednesday 2026-10-21). Plans
`pedir-al-asistente`: C-94, C-98, C-116; R1, R5, R10 of the unit. Every value is invented.
"""
from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.ia import CambioInterpretado, Interpretacion  # noqa: E402
from tridata.nutricion.recetas import Recetario  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py
LUNES = date(2026, 10, 19)
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])


def _claude(servidor, *cambios):
    servidor.interprete.interpretar.side_effect = lambda texto, hoy: Interpretacion(alimentos=[],
                                                                                    cambios=list(cambios))


def _escribir(p, texto):
    p.locator("#texto").fill(texto)
    p.locator("#enviar").click()


def _sin_scroll(p):
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"horizontal scroll ({ancho}px)"


def _abrir_hoy(abrir, servidor, **k):
    p = abrir(servidor, "/", esperar=False, **k)
    expect(p.locator("section#hoy")).to_be_visible()
    expect(p.locator("#hoy-comida .cifra")).not_to_have_text("—")
    return p


def _total(servidor):
    return round(sum(a["kcal"] or 0 for a in servidor.registro.apuntes_del_dia(HOY)))


@TEMAS
def test_sustituir_el_yogur_por_una_manzana(servidor, abrir, ir_a, capturar, oscuro):  # R1 · C-94
    Recetario(servidor.db).crear_fijo({"nombre": "manzana de prueba", "kcal": 80, "por": "unidad"})
    servidor.apuntar("yogur natural de prueba", HOY, 125, 75)
    servidor.apuntar("pasta de prueba", HOY, 100, 360)
    antes = _total(servidor)
    _claude(servidor, CambioInterpretado(accion="cambiar_alimento", objeto="yogur", texto="manzana de prueba",
                                         cantidad=1, unidad="unidad"))
    p = _abrir_hoy(abrir, servidor, oscuro=oscuro)
    _escribir(p, "quita el yogur, al final fue una manzana")
    tarjeta = p.locator("section#hoy #revision .tarjeta-cambio")
    expect(tarjeta).to_have_count(1)
    expect(tarjeta).to_contain_text("Voy a cambiar en el 21-10: yogur natural de prueba 125 g (75 kcal) → "
                                    "manzana de prueba 1 unidad (80 kcal), de tu ingrediente")
    _sin_scroll(p)
    capturar(p, f"sustituir-tarjeta-{'oscuro' if oscuro else 'claro'}")
    assert _total(servidor) == antes  # nothing changed yet

    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(p.locator("#mensaje")).to_contain_text("Hecho")
    assert _total(servidor) == antes + 5
    ir_a(p, "dia")
    expect(p.locator("#lista-dia .fila", has_text="manzana de prueba")).to_be_visible()
    expect(p.locator("#lista-dia .fila", has_text="yogur natural de prueba")).to_have_count(0)
    _sin_scroll(p)
    capturar(p, f"sustituir-hecho-{'oscuro' if oscuro else 'claro'}")


def test_fecha_pasada_y_retraso_del_objetivo(servidor, abrir, capturar):  # R5, R10 · C-98, C-116
    servidor.ajustes.cambiar({"limite_saludable_pct": 25, "objetivo_peso": {"kg": 72, "fecha": "2027-02-01"}},
                             desde="0001-01-01")
    _claude(servidor,
            CambioInterpretado(accion="cambiar_ajuste", campo="limite_saludable_pct", valor=20, desde=LUNES),
            CambioInterpretado(accion="objetivo_dia", valor=0, dia=HOY))
    p = _abrir_hoy(abrir, servidor)
    _escribir(p, "desde el lunes el límite saludable es 20 y hoy mantenimiento")
    tarjetas = p.locator("section#hoy #revision .tarjeta-cambio")
    expect(tarjetas).to_have_count(2)
    expect(tarjetas.nth(0)).to_contain_text("Límite saludable: 25 → 20 % · desde 2026-10-19")
    expect(tarjetas.nth(0)).to_contain_text("días del 19 al 20-10 se evaluarán con el 20 %; lo comido no cambia")
    expect(tarjetas.nth(1)).to_contain_text("Voy a cambiar el objetivo del 21-10: −10 % → 0 %")
    expect(tarjetas.nth(1)).to_contain_text("1 día más tarde")
    _sin_scroll(p)
    capturar(p, "fecha-pasada-y-retraso", completa=True)
