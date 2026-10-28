"""E2E of unit 047: three planned trainings in the sheet «Ajustes de hoy», in a real Chromium at 390 px (R1-R3).

The app of `conftest.py` (every outside service doubled, «today» Wednesday 2026-10-21, an invented swim of 40 min
and 310 kcal three days ago) plus an invented ride (60 min, 540 kcal) and run (30 min, 330 kcal). Andrés adds the
three with «Añadir otro», sees one row each and the expenditure going up by the three; «Cambiar» on the ride puts
it at 90 min and only the ride changes; «Ya hecho» on the swim removes only the swim. With `TRIDATA_CAPTURAS` the
sheet with the three is saved as `047-tres-previstos.png`.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.models import Activity  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py


def _kcal(texto: str) -> int:
    return int(re.sub(r"\D", "", texto))


def _gasto_es(p, kcal: int) -> None:
    p.wait_for_function(f"() => document.querySelector('#gasto-total').textContent.replace(/\\D/g, '') === '{kcal}'")


def _anadir(p, actividad_id: str, minutos: int) -> None:
    p.locator("#abrir-previsto").click()
    expect(p.locator("#caja-previsto")).to_be_visible()
    expect(p.locator(f"#previsto-actividad option[value='{actividad_id}']")).to_have_count(1)
    p.locator("#previsto-actividad").select_option(actividad_id)
    p.locator("#previsto-minutos").fill(str(minutos))
    p.locator("#previsto-guardar").click()


def test_tres_entrenos_previstos_en_la_hoja(servidor, abrir, capturar):  # R1, R2, R3
    servidor.store.save_activities([
        Activity(activity_id="e2e-bici", activity_date=HOY - timedelta(days=2), activity_type="cycling",
                 name="Bici de prueba", duration_seconds=3600, calories=540),
        Activity(activity_id="e2e-carrera", activity_date=HOY - timedelta(days=4), activity_type="running",
                 name="Carrera de prueba", duration_seconds=1800, calories=330),
    ])
    p = abrir(servidor)
    p.locator("#abrir-hoja").click()
    expect(p.locator("#hoja")).to_be_visible()
    expect(p.locator("#previsto-valor")).to_have_text("—")
    filas = p.locator("#previsto-lista .previsto-fila")
    expect(filas).to_have_count(0)
    gasto = _kcal(p.locator("#gasto-total").text_content())

    # R1: three, one row each, and the expenditure up by the three
    _anadir(p, "e2e-natacion", 40)
    expect(filas).to_have_count(1)
    expect(p.locator("#abrir-previsto")).to_have_text("Añadir otro")
    _anadir(p, "e2e-bici", 60)
    expect(filas).to_have_count(2)
    _anadir(p, "e2e-carrera", 30)
    expect(filas).to_have_count(3)
    expect(filas.nth(0)).to_contain_text("Natación técnica")
    expect(filas.nth(0)).to_contain_text("40 min")
    expect(filas.nth(1)).to_contain_text("Bici de prueba")
    expect(filas.nth(1)).to_contain_text("60 min")
    expect(filas.nth(2)).to_contain_text("Carrera de prueba")
    expect(filas.nth(2)).to_contain_text("30 min")
    for i in range(3):
        expect(filas.nth(i).get_by_role("button", name="Cambiar")).to_be_visible()
        expect(filas.nth(i).get_by_role("button", name="Ya hecho")).to_be_visible()
    _gasto_es(p, gasto + 310 + 540 + 330)
    for texto in ("Previsto: Natación técnica (40 min)", "Previsto: Bici de prueba (60 min)",
                  "Previsto: Carrera de prueba (30 min)"):
        expect(p.locator("#desglose")).to_contain_text(texto)
    expect(p.locator("#ajustar-resumen")).to_contain_text("3 entrenos")
    capturar(p, "047-tres-previstos", completa=True)

    # R2: «Cambiar» on the ride, 90 min: only the ride changes
    bici = filas.nth(1)
    bici.get_by_role("button", name="Cambiar").click()
    bici.locator("input").fill("90")
    bici.get_by_role("button", name="Guardar").click()
    expect(filas.nth(1)).to_contain_text("90 min")
    expect(filas.nth(0)).to_contain_text("40 min")
    expect(filas.nth(2)).to_contain_text("30 min")
    _gasto_es(p, gasto + 310 + 810 + 330)

    # R3: «Ya hecho» on the swim: only the swim goes
    filas.nth(0).get_by_role("button", name="Ya hecho").click()
    expect(filas).to_have_count(2)
    expect(filas.nth(0)).to_contain_text("Bici de prueba")
    expect(filas.nth(1)).to_contain_text("Carrera de prueba")
    _gasto_es(p, gasto + 810 + 330)
    expect(p.locator("#desglose")).not_to_contain_text("Natación técnica")

    stored = servidor.objetivos.del_dia(HOY)["previstos"]
    assert [(x["actividad_id"], x["minutos"]) for x in stored] == [("e2e-bici", 90), ("e2e-carrera", 30)]
