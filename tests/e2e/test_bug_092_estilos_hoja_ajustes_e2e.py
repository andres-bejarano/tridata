"""Bug 092 in a real Chromium at 390 × 844: unit 080 (66996c3) deleted the CSS block of the sheet «Ajustes de hoy»
together with the box and the assistant, but its classes are still in use. The segmented selectors (Nutrición › partes,
Fitness › Forma/Plan and its range, «Nivel que miras») lost their bar and the pressed button, and the sheet opened as a
centred box. Reads the computed style, not the HTML. Invented data only (R1, R2, R3 of the bug).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py

_PARTES = ('a[aria-current="page"]', "a:not([aria-current])")  # the parts are links marked with aria-current
_ESTILO = """(sel) => {
  const e = document.querySelector(sel); const s = getComputedStyle(e);
  return {display: s.display, position: s.position, radio: s.borderTopLeftRadius, abajo: s.borderBottomLeftRadius,
          fondo: s.backgroundColor, color: s.color, justify: s.justifyContent, ancho: s.width};
}"""


def _pulsado_resaltado(p, grupo: str, elegido: str = 'button[aria-pressed="true"]',
                       suelto: str = 'button[aria-pressed="false"]') -> None:
    barra = p.evaluate(_ESTILO, grupo)
    assert barra["display"] == "flex", f"{grupo}: {barra}"
    pulsado = p.evaluate(_ESTILO, f"{grupo} {elegido}")
    suelto = p.evaluate(_ESTILO, f"{grupo} {suelto}")
    assert pulsado["fondo"] != suelto["fondo"], f"{grupo}: the pressed button looks like the others ({pulsado})"
    assert pulsado["color"] != suelto["color"], f"{grupo}: the pressed button has the colour of the others"


def test_selectores_con_barra_y_elegido_resaltado(arrancar, abrir, ir_a):  # R1
    servidor = arrancar({"objetivo_peso": {"kg": 70.0, "fecha": (HOY + timedelta(days=130)).isoformat()}})
    servidor.dia_de_prueba()
    p = abrir(servidor)
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    _pulsado_resaltado(p, "#partes", *_PARTES)
    ir_a(p, "fitness")
    expect(p.locator("#fitness-rango button").first).to_be_visible()
    _pulsado_resaltado(p, "#fitness-partes", *_PARTES)
    _pulsado_resaltado(p, "#fitness-rango")


def test_hoja_de_ajustes_pegada_abajo(arrancar, abrir):  # R2
    servidor = arrancar({"objetivo_peso": {"kg": 70.0, "fecha": (HOY + timedelta(days=130)).isoformat()}})
    servidor.dia_de_prueba()
    p = abrir(servidor)
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    p.locator("#abrir-hoja").click()
    expect(p.locator("#hoja")).to_be_visible()
    hoja = p.evaluate(_ESTILO, "#hoja")
    assert hoja["position"] == "fixed", hoja
    assert hoja["radio"] != "0px" and hoja["abajo"] == "0px", f"rounded only on top: {hoja}"
    caja = p.locator("#hoja").bounding_box()
    assert abs(caja["y"] + caja["height"] - 844) <= 1, f"the sheet does not sit at the bottom: {caja}"
    assert p.evaluate(_ESTILO, "#hoja .hoja-cab")["justify"] == "space-between"
    assert p.evaluate(_ESTILO, "#hoja .asa")["ancho"] == "40px"
    _pulsado_resaltado(p, "#segmentado")
