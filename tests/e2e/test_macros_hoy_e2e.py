"""Unit 099 in a real Chromium at 390 × 844, light and dark: the card «Macros» of Hoy and its 8 weeks.

Plans `consultar-margen` REC-43: R-276, R-278 · C-338, C-341, C-344, C-346. R4, R5 and R6 of the unit. Unit 101
(REC-45, R-277 · C-341): below 80 % only the warning and the objective, without bars. Uses the
`conftest.py` of unit 010 («today» is Wednesday 2026-10-21; Garmin is a `MagicMock`). Invented data only.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.models import Activity  # noqa: E402
from tridata.nutricion import Alimento  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py
AYER = HOY - timedelta(days=1)
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])


def _comer(servidor, dia, kcal, p=None, h=None, g=None) -> None:
    servidor.registro.guardar([Alimento(nombre="Comida inventada", dia=dia, cantidad=1, unidad="ración", kcal=kcal,
                                        proteina_g=p, hidratos_g=h, grasa_g=g)],
                              hoy=HOY, texto="comida inventada", sin_cambios=True)


def _macros_de_prueba(servidor) -> None:
    """70 kg; 1 h 30 of bike today (C-338) and yesterday; yesterday 150 g P, 380 g H, 25 % fat (C-346); today 95 g P;
    and the week before last, 5 days with macros."""
    servidor.pesos.apuntar(HOY - timedelta(days=30), 70, hoy=HOY)
    servidor.store.save_activities([
        Activity(activity_id="bici-hoy", activity_date=HOY, activity_type="road_biking", name="Bici inventada",
                 duration_seconds=5400),
        Activity(activity_id="bici-ayer", activity_date=AYER, activity_type="road_biking", name="Bici inventada",
                 duration_seconds=5400)])
    _comer(servidor, AYER, 2000, 150, 380, 2000 * 0.25 / 9)
    _comer(servidor, HOY, 1000, 95, 300, 1000 * 0.22 / 9)
    lunes = HOY - timedelta(days=HOY.weekday() + 14)
    for i in range(5):
        _comer(servidor, lunes + timedelta(days=i), 2000, 119, 300, 2000 * 0.25 / 9)


def _abrir_hoy(abrir, servidor, **k):
    p = abrir(servidor, "/", esperar=False, **k)
    expect(p.locator("section#hoy")).to_be_visible()
    expect(p.locator("#hoy-macros-barras .macro-fila")).to_have_count(3)
    return p


@TEMAS
def test_tarjeta_macros_y_sus_ocho_semanas(servidor, abrir, capturar, oscuro):  # R4, R6 · C-338, C-344, C-346
    _macros_de_prueba(servidor)
    p = _abrir_hoy(abrir, servidor, oscuro=oscuro)
    tarjeta = p.locator("#hoy-macros")
    # below Comida
    assert tarjeta.bounding_box()["y"] > p.locator("#hoy-comida").bounding_box()["y"]
    expect(tarjeta).to_contain_text("Macros")
    barras = p.locator("#hoy-macros-barras")
    expect(barras).to_contain_text("llevas 95 g")
    expect(barras).to_contain_text("de 112-140 g")
    expect(barras).to_contain_text("de 420-700 g")
    expect(barras).to_contain_text("22 % de las kcal")
    expect(barras).to_contain_text("≥ 20 %")
    expect(p.locator("#hoy-macros-barras .barra-macro")).to_have_count(3)
    expect(p.locator("#hoy-macros-ayer")).to_have_text("Ayer: proteína alto · hidratos bajo · grasa bien")
    expect(p.locator("#hoy-macros-semanas")).to_be_hidden()
    assert p.evaluate("document.documentElement.scrollWidth") <= 390
    capturar(p, f"macros-tarjeta-{'oscuro' if oscuro else 'claro'}", completa=True)

    tarjeta.click()  # the 8 weeks open in the card; Hoy stays
    semanas = p.locator("#hoy-macros-semanas")
    expect(semanas).to_be_visible()
    expect(p.locator("section#hoy")).to_be_visible()
    expect(semanas.locator(".macro-semana")).to_have_count(8)
    expect(semanas).to_contain_text("proteína 1,7 g/kg · 5 de 5 días")
    expect(semanas.locator(".macro-semana", has_text="sin macros")).to_have_count(6)  # weeks 1-5 and last week
    assert p.evaluate("document.documentElement.scrollWidth") <= 390
    semanas.scroll_into_view_if_needed()
    capturar(p, f"macros-semanas-{'oscuro' if oscuro else 'claro'}", completa=True)

    tarjeta.click()
    expect(semanas).to_be_hidden()


def test_tarjeta_aproximado_y_sin_peso(servidor, abrir, capturar):  # R5, R2 · C-341, C-347; unit 101 R5 (R-277)
    _comer(servidor, HOY, 1300, 80, 150, 40)
    _comer(servidor, HOY, 700)
    p = _abrir_hoy(abrir, servidor)
    expect(p.locator("#hoy-macros")).to_contain_text(
        "Macros de solo el 65 % de lo comido · 1 alimento sin macros: pídeselos al equipo")
    expect(p.locator("#hoy-macros")).to_contain_text("apunta tu peso para verlo en gramos")
    expect(p.locator("#hoy-macros-barras")).to_contain_text("1,6-2,0 g/kg")
    expect(p.locator("#hoy-macros-barras .barra-macro")).to_have_count(0)
    expect(p.locator("#hoy-macros-ayer")).to_have_text("Ayer: sin macros")
    capturar(p, "macros-aproximado-sin-peso-claro", completa=True)


@TEMAS
def test_tarjeta_bajo_el_80_solo_el_aviso_y_su_objetivo(servidor, abrir, capturar, oscuro):  # 101 R5 · C-341
    """2000 kcal today, 480 of them with macros (24 %) and 12 foods without: no bars, no grams, no % of fat."""
    servidor.pesos.apuntar(HOY - timedelta(days=30), 70, hoy=HOY)
    servidor.store.save_activities([Activity(activity_id="bici-hoy", activity_date=HOY, activity_type="road_biking",
                                             name="Bici inventada", duration_seconds=5400)])
    _comer(servidor, HOY, 480, 40, 50, 13)
    for _ in range(12):
        _comer(servidor, HOY, 1520 / 12)
    p = _abrir_hoy(abrir, servidor, oscuro=oscuro)
    aviso = p.locator("#hoy-macros-aviso")
    expect(aviso).to_be_visible()
    expect(aviso).to_have_text("Macros de solo el 24 % de lo comido · 12 alimentos sin macros: pídeselos al equipo")
    barras = p.locator("#hoy-macros-barras")
    expect(barras.locator(".barra-macro")).to_have_count(0)
    expect(barras).to_contain_text("112-140 g")  # its objective of the day (S-95)
    expect(barras).to_contain_text("420-700 g")
    expect(barras).to_contain_text("≥ 20 %")
    expect(barras).not_to_contain_text("llevas")
    expect(barras).not_to_contain_text("de las kcal")  # no % of fat
    expect(p.locator("#hoy-macros")).not_to_contain_text(re.compile(r"(?<![\d-])40 g"))  # the 40 g eaten, not 112-140
    assert p.evaluate("document.documentElement.scrollWidth") <= 390
    capturar(p, f"macros-bajo-80-{'oscuro' if oscuro else 'claro'}", completa=True)


def test_las_otras_tarjetas_siguen_igual(servidor, abrir):  # «NO debe haber cambiado»: Comida still goes to Día
    servidor.dia_de_prueba()
    p = _abrir_hoy(abrir, servidor)
    expect(p.locator("#hoy-comida")).to_contain_text("760 kcal comidas")
    expect(p.locator("section#hoy .tarjeta-hoy")).to_have_count(3)
    p.locator("#hoy-macros").click()
    expect(p.locator("section#hoy")).to_be_visible()  # the card of the macros does not navigate
    p.locator("#hoy-comida").click()
    expect(p.locator("section#dia")).to_be_visible()
