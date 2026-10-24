"""E2E of unit 034: the sheet «Ajustes» of tomorrow, in a real Chromium at 390 px (R1, R5).

The app of `conftest.py` (every outside service doubled, «today» Wednesday 2026-10-21, one invented swim of
40 min and 310 kcal three days ago). Andrés goes to tomorrow, opens «Ajustes», sees the objective, the levels
and the planned training (no Garmin), puts the swim for 60 min and the expenditure of tomorrow goes up; today
keeps its own. With `TRIDATA_CAPTURAS` the sheet of tomorrow is saved as `034-hoja-manana.png`.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py (a Wednesday)
MANANA = HOY + timedelta(days=1)


def _kcal(texto: str) -> int:
    return int(re.sub(r"\D", "", texto))


def test_la_hoja_de_manana_pone_el_entreno_previsto(servidor, abrir, capturar):  # R1, R5
    p = abrir(servidor)
    expect(p.locator("#titulo")).to_have_text("Hoy")
    p.locator("#abrir-hoja").click()
    gasto_hoy = _kcal(p.locator("#gasto-total").text_content())
    p.locator("#cerrar-hoja").click()

    p.locator("#siguiente").click()
    expect(p.locator("#titulo")).to_have_text("Mañana")
    p.locator("#abrir-hoja").click()
    hoja = p.locator("#hoja")
    expect(hoja).to_be_visible()
    expect(p.locator("#hoja-titulo")).to_have_text("Ajustes del jueves 22 de octubre")
    for visible in ("#abrir-niveles", "#objetivo-hoy", "#previsto"):
        expect(p.locator(visible)).to_be_visible()
    for oculto in ("#bloque-garmin", "#actualizar-garmin"):
        expect(p.locator(oculto)).to_be_hidden()
    # R5: the titles say «del día», not «de hoy»
    expect(p.locator("#objetivo-titulo")).to_have_text("Objetivo del día")
    expect(p.locator("#gasto-titulo")).to_have_text("Gasto del día")
    expect(p.locator("#objetivo-guardar")).to_have_text("Cambiar el del día")
    expect(p.locator("#hoja")).not_to_contain_text("de hoy")

    gasto_antes = _kcal(p.locator("#gasto-total").text_content())
    p.locator("#abrir-previsto").click()
    expect(p.locator("#previsto-actividad option")).to_have_count(1)
    p.locator("#previsto-minutos").fill("60")
    p.locator("#previsto-guardar").click()
    expect(p.locator("#previsto-valor")).to_have_text("60 min")
    expect(p.locator("#gasto-total")).to_have_text(re.compile(r"\d"))
    expect(p.locator("#previsto-quitar")).to_have_text("Quitar")
    p.wait_for_function(f"() => document.querySelector('#gasto-total').textContent.replace(/\\D/g, '') "
                        f"=== '{gasto_antes + 465}'")  # 310 kcal in 40 min -> 465 in 60
    expect(p.locator("#titulo")).to_have_text("Mañana")  # the screen stays on tomorrow
    capturar(p, "034-hoja-manana")

    # saved in tomorrow, not in today
    assert servidor.objetivos.del_dia(MANANA)["previsto"]["minutos"] == 60
    assert servidor.objetivos.del_dia(HOY) is None
    p.locator("#cerrar-hoja").click()
    p.locator("#ir-hoy").click()
    expect(p.locator("#titulo")).to_have_text("Hoy")
    p.locator("#abrir-hoja").click()
    expect(p.locator("#hoja-titulo")).to_have_text("Ajustes de hoy")
    expect(p.locator("#bloque-garmin")).to_be_visible()
    expect(p.locator("#objetivo-titulo")).to_have_text("Objetivo de hoy")
    assert _kcal(p.locator("#gasto-total").text_content()) == gasto_hoy
