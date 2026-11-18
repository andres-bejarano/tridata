"""Unit 093 in a real Chromium at 390 × 844, light and dark (E2E-13 of `ver-fitness`): tap the bar of a week in «Tus
semanas» → it is marked and its activities appear under the chart, with the totals next to the mean of the 4 weeks
before → tap one → its session, each lap with its cadence (and stride and climb for a run); a week without the chosen
sport says so; «Cerrar» closes it. Uses the `conftest.py` of unit 010. Every value is invented (C-298, C-300, C-302,
C-304, C-308).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.models import Activity, ActivityLap  # noqa: E402
from tridata.nutricion import DESDE_SIEMPRE  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py (a Wednesday)
LUNES = date(2026, 10, 12)  # last week; conftest's «Natación técnica» is on its Sunday (18/10)
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
ZONAS = [[55, 120], [121, 140], [141, 155], [156, 168], [169, 180]]
PULSO = {"fc_max": 180, "fc_reposo": 55, "zonas_fc": ZONAS, "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}


def _sembrar(servidor) -> None:
    store = servidor.store
    dia = lambda n: LUNES + timedelta(days=n)  # noqa: E731
    store.save_activities([
        Activity(activity_id="c1", activity_date=dia(0), activity_type="running", name="Rodaje suave",
                 duration_seconds=3600, distance_meters=10000, avg_hr=140),
        Activity(activity_id="c2", activity_date=dia(2), activity_type="running", name="Series de prueba",
                 duration_seconds=3000, distance_meters=10000, avg_hr=150),
        Activity(activity_id="b1", activity_date=dia(3), activity_type="road_biking", name="Salida en bici",
                 duration_seconds=7200, distance_meters=60000, avg_hr=135),
        Activity(activity_id="c3", activity_date=dia(5), activity_type="running", name="Tirada larga",
                 duration_seconds=5400, distance_meters=16000, avg_hr=145),
    ] + [Activity(activity_id=f"p{k}", activity_date=LUNES - timedelta(weeks=k), activity_type="running",
                  name="Rodaje de antes", duration_seconds=3600, distance_meters=10000, avg_hr=140)
         for k in range(1, 5)])
    store.save_activity_laps([ActivityLap("c2", i, distance_meters=1000, duration_seconds=300, avg_hr=150,
                                          avg_cadence=172, avg_stride_length_cm=105, elevation_gain_m=12)
                              for i in range(1, 4)]
                             + [ActivityLap("c2", 4, distance_meters=1000, duration_seconds=300, avg_hr=150)])
    store.save_activity_laps([ActivityLap("b1", i, distance_meters=20000, duration_seconds=2400, avg_hr=135,
                                          avg_bike_cadence=86, elevation_gain_m=150) for i in range(1, 4)])
    store.save_activity_weather("c2", {"temp_c": 14.0, "humidity_pct": 70.0, "wind_speed_kmh": 8.0,
                                       "condition": "Despejado"})


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


@TEMAS
def test_semana_lista_y_sesion(servidor, abrir, ir_a, capturar, oscuro):  # E2E-13 · C-298, C-300, C-302, C-308
    _sembrar(servidor)
    servidor.ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ir_a(p, "fitness")
    grafica = p.locator("#fitness-grafica-semanas")
    barra = grafica.locator(f'g.semana[data-lunes="{LUNES.isoformat()}"]')
    expect(barra).to_be_visible()

    semana = p.locator("#fitness-semana")
    expect(semana).to_be_hidden()
    barra.click()
    expect(semana).to_be_visible()
    expect(grafica.locator("g.semana.elegida")).to_have_count(1)
    expect(grafica.locator("g.semana.elegida")).to_have_attribute("data-lunes", LUNES.isoformat())
    # C-298: the 5 activities (4 of the test + conftest's swim on Sunday 18), the newest on top
    filas = semana.locator(".actividad-semana")
    expect(filas).to_have_count(5)
    expect(filas.first).to_contain_text("Natación técnica")
    expect(filas.nth(1)).to_contain_text("Tirada larga")
    expect(filas.nth(1)).to_contain_text("16,0 km")
    expect(filas.last).to_contain_text("Rodaje suave")
    # C-300: the totals of the week, each next to the mean of the 4 before
    totales = semana.locator(".totales-semana")
    expect(totales).to_contain_text("sesiones")
    expect(totales).to_contain_text("media 4 semanas")
    _sin_scroll(p, "semana")
    semana.scroll_into_view_if_needed()
    capturar(p, f"fitness-semana{sufijo}", completa=True)

    # C-308: a ride of the list opens its session, with its cadence (R-244)
    filas.filter(has_text="Salida en bici").click()
    sesion = p.locator("#fitness-sesion")
    expect(sesion).to_be_visible()
    expect(sesion.locator(".vuelta")).to_have_count(3)
    expect(sesion.locator(".vuelta").first).to_contain_text("86 ppm")
    expect(sesion.locator(".vuelta").first).to_contain_text("+150 m")

    # C-299: «Correr» leaves only the runs; C-302: a run with steps per minute, stride and climb, and the weather
    p.locator("#fitness-deporte").get_by_role("button", name="Correr").click()
    expect(filas).to_have_count(3)
    expect(sesion).to_be_hidden()
    filas.filter(has_text="Series de prueba").click()
    expect(sesion.locator(".vuelta")).to_have_count(4)
    primera = sesion.locator(".vuelta").first
    expect(primera).to_contain_text("172 ppm")
    expect(primera).to_contain_text("1,05 m")
    expect(primera).to_contain_text("+12 m")
    expect(primera).to_contain_text("Z3")
    expect(sesion.locator(".vuelta").last).to_contain_text("cadencia —")  # never a 0 (C-303)
    expect(sesion).to_contain_text("Despejado")
    _sin_scroll(p, "sesión")
    sesion.scroll_into_view_if_needed()
    capturar(p, f"fitness-sesion{sufijo}", completa=True)

    # C-304: «Nadar» in a week without swimming
    p.locator("#fitness-deporte").get_by_role("button", name="Nadar").click()
    grafica.locator(f'g.semana[data-lunes="{(LUNES - timedelta(weeks=1)).isoformat()}"]').click()
    expect(semana).to_contain_text("sin actividades esta semana")
    expect(filas).to_have_count(0)

    # tapping the same week, or «Cerrar», closes it; the chart is still there
    grafica.locator(f'g.semana[data-lunes="{(LUNES - timedelta(weeks=1)).isoformat()}"]').click()
    expect(semana).to_be_hidden()
    expect(grafica.locator("g.semana.elegida")).to_have_count(0)
    barra.click()
    expect(semana).to_be_visible()
    semana.get_by_role("button", name="Cerrar").click()
    expect(semana).to_be_hidden()


def test_la_semana_se_elige_con_el_teclado(servidor, abrir, ir_a):  # R1: accessible, not only by touch
    _sembrar(servidor)
    p = abrir(servidor, "/#dia")
    ir_a(p, "fitness")
    barra = p.locator(f'#fitness-grafica-semanas g.semana[data-lunes="{LUNES.isoformat()}"]')
    barra.focus()
    p.keyboard.press("Enter")
    expect(p.locator("#fitness-semana")).to_be_visible()
    expect(barra).to_have_attribute("aria-pressed", "true")
