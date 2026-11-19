"""Unit 094 in a real Chromium at 390 × 844, light and dark (E2E-14 of `ver-fitness`): a session with its detail
second by second shows, over its laps, the curve of its HR on the bands of the zones, each lap segment coloured by its
zone; tapping a lap of the list highlights its segment; under the curve, the minutes and % in each zone counted second
by second. A session without detail says «sin curva de pulso para esta sesión». Uses the `conftest.py` of unit 010.
Every value is invented (C-314, C-315, C-316, C-318).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.models import Activity, ActivityLap  # noqa: E402
from tridata.nutricion import DESDE_SIEMPRE  # noqa: E402

LUNES = date(2026, 10, 12)  # last week of conftest's HOY (21/10)
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
ZONAS = [[55, 120], [121, 140], [141, 155], [156, 168], [169, 180]]
PULSO = {"fc_max": 180, "fc_reposo": 55, "zonas_fc": ZONAS, "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}


def _pulso(i: int) -> float | None:
    """600 s in Z1, 300 in Z2, 200 in Z3, 50 without HR and 50 in Z4: 1,200 s."""
    if i < 600:
        return 105.0 + (i % 10)
    if i < 900:
        return 130.0 + (i % 6)
    if i < 1100:
        return 148.0 + (i % 4)
    return None if i < 1150 else 160.0


def _sembrar(servidor) -> None:
    store = servidor.store
    store.save_activities([
        Activity(activity_id="con", activity_date=LUNES + timedelta(days=6), activity_type="running",
                 name="Tirada con pulso", duration_seconds=1200, distance_meters=3600, avg_hr=125),
        Activity(activity_id="sin", activity_date=LUNES + timedelta(days=2), activity_type="running",
                 name="Rodaje de marzo", duration_seconds=1800, distance_meters=5000, avg_hr=140),
    ])
    store.save_activity_laps([ActivityLap("con", 1, distance_meters=1800, duration_seconds=600, avg_hr=110),
                              ActivityLap("con", 2, distance_meters=900, duration_seconds=300, avg_hr=132),
                              ActivityLap("con", 3, distance_meters=600, duration_seconds=200, avg_hr=149),
                              ActivityLap("con", 4, distance_meters=300, duration_seconds=100, avg_hr=160)])
    store.save_activity_laps([ActivityLap("sin", i, distance_meters=1000, duration_seconds=360, avg_hr=140)
                              for i in range(1, 6)])
    store.guardar_detalle("con", {"t": [float(i) for i in range(1200)],
                                  "distancia": [round(i * 3.0, 1) for i in range(1200)],
                                  "pulso": [_pulso(i) for i in range(1200)],
                                  "velocidad": [3.0] * 1200, "altura": [100.0] * 1200, "potencia": [None] * 1200})
    servidor.ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


def _abrir_sesion(p, ir_a, nombre: str):
    ir_a(p, "fitness")
    p.locator(f'#fitness-grafica-semanas g.semana[data-lunes="{LUNES.isoformat()}"]').click()
    p.locator("#fitness-semana .actividad-semana", has_text=nombre).click()
    sesion = p.locator("#fitness-sesion")
    expect(sesion.locator(".vuelta").first).to_be_visible()
    return sesion


@TEMAS
def test_sesion_con_curva_de_pulso(servidor, abrir, ir_a, capturar, oscuro):  # E2E-14 · C-314, C-315, C-316
    _sembrar(servidor)
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    sesion = _abrir_sesion(p, ir_a, "Tirada con pulso")

    # C-314: the curve over the 5 bands of the zones, above the laps
    curva = sesion.locator(".curva-pulso")
    expect(curva).to_be_visible()
    svg = curva.locator("svg")
    expect(svg).to_have_attribute("role", "group")
    expect(svg).to_have_attribute("aria-label", "Pulso de la sesión sobre tus zonas")
    expect(svg.locator("rect.banda-zona")).to_have_count(5)
    expect(svg.locator("polyline.linea-pulso")).to_have_count(1)
    arriba = curva.bounding_box()["y"]
    assert arriba < sesion.locator(".vueltas").bounding_box()["y"], "the curve goes above the laps"

    # C-315: each lap segment with the colour of its zone; tapping lap 3 highlights its segment
    tramos = svg.locator("rect.tramo-vuelta")
    expect(tramos).to_have_count(4)
    for n, z in ((1, 1), (2, 2), (3, 3), (4, 4)):
        expect(svg.locator(f'rect.tramo-vuelta[data-n="{n}"]')).to_have_class(f"tramo-vuelta capa-z{z}")
    sesion.locator(".vuelta").nth(2).click()
    expect(svg.locator('rect.tramo-vuelta[data-n="3"]')).to_have_class("tramo-vuelta capa-z3 resaltado")
    expect(svg.locator("rect.tramo-vuelta.resaltado")).to_have_count(1)
    expect(sesion.locator(".vuelta").nth(2)).to_have_attribute("aria-current", "true")
    expect(svg.locator("rect.resalte-vuelta")).to_have_count(1)
    # the stroke of the curve is really painted (CSS, not only an element)
    trazo = svg.locator("polyline.linea-pulso").evaluate("e => getComputedStyle(e).stroke")
    assert trazo not in ("none", ""), trazo

    # C-316: the time in each zone counted second by second, in minutes and %, with «sin pulso» apart
    zonas = sesion.locator(".zonas-sesion")
    expect(zonas).to_contain_text("Z1 10 min · 50 %")
    expect(zonas).to_contain_text("Z2 5 min · 25 %")
    expect(zonas).to_contain_text("Z3 3 min · 17 %")
    expect(zonas).to_contain_text("Z4 1 min · 4 %")
    expect(zonas).to_contain_text("sin pulso 1 min · 4 %")
    _sin_scroll(p, "sesión con curva")
    curva.scroll_into_view_if_needed()
    capturar(p, f"pulso-sesion{sufijo}")

    # tapping it again (or with the keyboard) unmarks it
    sesion.locator(".vuelta").nth(2).click()
    expect(svg.locator("rect.tramo-vuelta.resaltado")).to_have_count(0)
    sesion.locator(".vuelta").nth(1).focus()
    p.keyboard.press("Enter")
    expect(svg.locator('rect.tramo-vuelta[data-n="2"]')).to_have_class("tramo-vuelta capa-z2 resaltado")


@TEMAS
def test_sesion_sin_detalle_y_semanas_aproximadas(servidor, abrir, ir_a, capturar, oscuro):  # C-318, C-317
    _sembrar(servidor)
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    sesion = _abrir_sesion(p, ir_a, "Rodaje de marzo")
    expect(sesion).to_contain_text("sin curva de pulso para esta sesión")
    expect(sesion.locator(".curva-pulso svg")).to_have_count(0)
    expect(sesion.locator(".vuelta")).to_have_count(5)  # its laps, as today
    _sin_scroll(p, "sesión sin curva")
    sesion.scroll_into_view_if_needed()
    capturar(p, f"pulso-sin-curva{sufijo}")
    # «Tus semanas» in zones: conftest's swim and this run have no detail → «aproximado por vueltas»
    p.locator("#fitness-medida").get_by_role("button", name="Zonas").click()
    expect(p.locator(".leyenda-semanas")).to_contain_text("aproximado por vueltas")


def test_sin_sesiones_sin_detalle_no_dice_aproximado(servidor, abrir, ir_a):  # C-317: only while one is missing
    store = servidor.store
    with store._connect() as conn:  # conftest's swim, out: every session of the range has its detail
        conn.execute("DELETE FROM activities")
    _sembrar(servidor)
    with store._connect() as conn:
        conn.execute("DELETE FROM activities WHERE activity_id = 'sin'")
    p = abrir(servidor, "/#dia")
    ir_a(p, "fitness")
    p.locator("#fitness-medida").get_by_role("button", name="Zonas").click()
    leyenda = p.locator(".leyenda-semanas")
    expect(leyenda).to_contain_text("Z1")
    expect(leyenda).not_to_contain_text("aproximado")
