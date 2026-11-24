"""Unit 108 in a real Chromium at 390 × 844, light and dark (E2E-20 of `ver-fitness`): «Tus semanas» as a line of
points with its area; tapping a point marks it, shows its dates, distance, time and climb in large above the chart and
its week below; «por zonas» puts a bar of zones under each point (not with «carga»). «Comparar con…» draws a second
period as a second line aligned by week number, with a sentence of totals; tapping a point shows both weeks side by
side; «Quitar comparación» goes back to one line. Uses the `conftest.py` of unit 010. Every value is invented (C-393,
C-396, C-397, C-398, C-399, C-400, C-401, C-402, C-403).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.models import Activity, ActivityLap  # noqa: E402
from tridata.nutricion import DESDE_SIEMPRE  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py (a Wednesday)
EN_CURSO = date(2026, 10, 19)
GRANDE = date(2026, 10, 12)  # the «Behobia» week: sem 7 of the period from 1/9/2026
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
ZONAS = [[55, 120], [121, 140], [141, 155], [156, 168], [169, 180]]
PULSO = {"fc_max": 180, "fc_reposo": 55, "zonas_fc": ZONAS, "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}


def _sembrar(servidor) -> None:
    """2025: a 10 km run every Wednesday from 3/9 to 3/12 (Z2, no laps). 2026: a 12 km run every Wednesday from 2/9,
    except the week of 12/10: a 34 km long run (2 h in Z2, 1 h 30 in Z4 by laps, +120 m) and a 20,03 km race (Z4,
    +223 m). A first walk on 1/6/2025: the history starts before both periods."""
    store = servidor.store
    actividades = [Activity(activity_id="primera", activity_date=date(2025, 6, 1), activity_type="walking",
                            name="Paseo de prueba", duration_seconds=1800, distance_meters=2000)]
    dia = date(2025, 9, 3)
    while dia <= date(2025, 12, 3):
        actividades.append(Activity(activity_id=f"v{dia.isoformat()}", activity_date=dia, activity_type="running",
                                    name="Rodaje de 2025", duration_seconds=3600, distance_meters=10000, avg_hr=135))
        dia += timedelta(days=7)
    dia = date(2026, 9, 2)
    while dia <= HOY:
        if dia < GRANDE or dia > GRANDE + timedelta(days=6):
            actividades.append(Activity(activity_id=f"n{dia.isoformat()}", activity_date=dia,
                                        activity_type="running", name="Rodaje de 2026", duration_seconds=4000,
                                        distance_meters=12000, avg_hr=135))
        dia += timedelta(days=7)
    actividades += [
        Activity(activity_id="tirada", activity_date=GRANDE + timedelta(days=5), activity_type="running",
                 name="Tirada de prueba", duration_seconds=12600, distance_meters=34000, avg_hr=145,
                 elevation_gain_m=120),
        Activity(activity_id="behobia", activity_date=GRANDE + timedelta(days=6), activity_type="running",
                 name="Behobia de prueba", duration_seconds=4800, distance_meters=20030, avg_hr=165,
                 elevation_gain_m=223),
    ]
    store.save_activities(actividades)
    store.save_activity_laps([
        ActivityLap(activity_id="tirada", lap_index=0, distance_meters=20000, duration_seconds=7200, avg_hr=130),
        ActivityLap(activity_id="tirada", lap_index=1, distance_meters=14000, duration_seconds=5400, avg_hr=160),
        ActivityLap(activity_id="behobia", lap_index=0, distance_meters=20030, duration_seconds=4800, avg_hr=165)])
    servidor.ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


def _semana(grafica, lunes: date):
    return grafica.locator(f'svg g.semana[data-lunes="{lunes.isoformat()}"]')


@TEMAS
def test_linea_punto_tocado_y_por_zonas(servidor, abrir, ir_a, capturar, oscuro):  # E2E-20 · C-393, C-396, C-397
    _sembrar(servidor)
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ir_a(p, "fitness")
    grafica = p.locator("#fitness-grafica-semanas")
    cabecera = p.locator("#fitness-semana-cabecera")

    # R1 (C-393): one line of 6 points with its area; the week in course says so; no bars
    expect(grafica.locator("svg path.linea-semanas")).to_have_count(1)
    expect(grafica.locator("svg path.area-semanas")).to_have_count(1)
    expect(grafica.locator("svg .punto-semana")).to_have_count(6)
    expect(grafica.locator("svg .barra-semana")).to_have_count(0)
    expect(grafica.locator("svg .semana.en-curso")).to_contain_text("en curso")
    # R3: when Fitness opens, the week in course is marked and its figures are above the chart
    expect(grafica.locator("svg .raya-semana")).to_have_count(1)
    expect(_semana(grafica, EN_CURSO).locator(".raya-semana")).to_have_count(1)
    expect(cabecera).to_contain_text("19 oct – 25 oct 2026")
    expect(p.locator("#fitness-semana")).to_be_hidden()

    # «Correr» + «km»: the week of 12/10 is the highest point
    p.locator("#fitness-deporte").get_by_role("button", name="Correr").click()
    medida = p.locator("#fitness-medida")
    medida.get_by_role("button", name="Km").click()
    valores = grafica.locator("svg .punto-semana").evaluate_all("ps => ps.map(c => Number(c.dataset.valor))")
    assert max(valores) == valores[4] == 54.03, valores  # 12/10 is the 5th of the 6 weeks

    # R3 (C-397): tapping its point marks it with the line and shows its dates, km, time and climb in large
    _semana(grafica, GRANDE).click()
    expect(_semana(grafica, GRANDE).locator(".raya-semana")).to_have_count(1)
    expect(grafica.locator("svg .raya-semana")).to_have_count(1)
    expect(cabecera).to_contain_text("12 oct – 18 oct 2026")
    expect(cabecera.locator(".dato-cabecera")).to_have_count(3)
    expect(cabecera).to_contain_text("54,03 km")
    expect(cabecera).to_contain_text("4h 50min")
    expect(cabecera).to_contain_text("343 m")
    semana = p.locator("#fitness-semana")
    expect(semana).to_be_visible()
    expect(semana.locator(".actividad-semana")).to_have_count(2)
    expect(semana).to_contain_text("Tirada de prueba")
    expect(p.locator("#fitness-semana-zonas .barra-zona-panel")).to_be_visible()
    _sin_scroll(p, "punto tocado")
    p.locator("#fitness-semanas").scroll_into_view_if_needed()
    capturar(p, f"linea-punto-tocado{sufijo}", completa=True)

    # R2 (C-395): «por zonas» in km: a bar of zones under each point; the week of 12/10 with Z2 and Z4
    medida.get_by_role("button", name="Por zonas").click()
    expect(medida.get_by_role("button", name="Por zonas")).to_have_attribute("aria-pressed", "true")
    grande = _semana(grafica, GRANDE)
    expect(grande.locator('.barra-zona[data-zona="z2"]')).to_have_count(1)
    expect(grande.locator('.barra-zona[data-zona="z4"]')).to_have_count(1)
    alturas = grande.locator(".barra-zona").evaluate_all("rs => rs.map(r => Number(r.dataset.valor))")
    assert sum(alturas) == pytest.approx(54.03, abs=0.1)  # the bar adds up to the km of the week
    expect(p.locator("#fitness-semanas .leyenda-grafica")).to_contain_text("aproximado por vueltas")
    expect(p.locator("#fitness-semanas .leyenda-grafica")).to_contain_text("Z2")
    _sin_scroll(p, "por zonas")
    capturar(p, f"linea-por-zonas{sufijo}", completa=True)

    # R2 (C-396): with «carga» there is no «por zonas»
    medida.get_by_role("button", name="Carga").click()
    expect(medida.get_by_role("button", name="Por zonas")).to_be_hidden()
    expect(grafica.locator("svg .barra-zona")).to_have_count(0)
    expect(grafica.locator("svg path.linea-semanas")).to_have_count(1)
    expect(p.locator("#fitness-semanas .leyenda-grafica")).not_to_contain_text("aproximado")

    # tapping the marked point again closes the week; the chart is still there
    _semana(grafica, GRANDE).click()
    expect(semana).to_be_hidden()


def _comparar(p, este: tuple[str, str], otro: tuple[str, str]) -> None:
    abrir_form = p.locator("#fitness-comparar-abrir")
    if p.locator("#fitness-comparar-form").is_hidden():
        abrir_form.click()
    p.locator("#fitness-este-desde").fill(este[0])
    p.locator("#fitness-este-hasta").fill(este[1])
    p.locator("#fitness-otro-desde").fill(otro[0])
    p.locator("#fitness-otro-hasta").fill(otro[1])
    p.locator("#fitness-comparar-aplicar").click()


@TEMAS
def test_comparar_dos_periodos(servidor, abrir, ir_a, capturar, oscuro):  # E2E-20 · C-398..C-403 (portante R4)
    _sembrar(servidor)
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ir_a(p, "fitness")
    grafica = p.locator("#fitness-grafica-semanas")
    p.locator("#fitness-deporte").get_by_role("button", name="Correr").click()
    p.locator("#fitness-medida").get_by_role("button", name="Km").click()

    # R4 (C-398): from 1/9 to 6/12/2026 (its «hasta» after today) against the same days of 2025
    _comparar(p, ("2026-09-01", "2026-12-06"), ("2025-09-01", "2025-12-06"))
    lineas = grafica.locator("svg path.linea-semanas")
    expect(lineas).to_have_count(2)
    expect(grafica.locator("svg path.linea-semanas.serie-b")).to_have_count(1)
    assert grafica.locator("svg path.linea-semanas.serie-b").evaluate(
        "l => getComputedStyle(l).strokeDasharray") not in ("none", "")  # also told apart without colour
    expect(grafica.locator("svg g.semana")).to_have_count(14)
    expect(grafica.locator("svg .punto-semana.serie-a")).to_have_count(8)  # cut in the week in course
    expect(grafica.locator("svg .punto-semana.serie-b")).to_have_count(14)
    expect(grafica.locator("svg text.etiqueta-x").first).to_have_text("sem 1")
    leyenda = p.locator("#fitness-semanas .leyenda-grafica")
    expect(leyenda).to_contain_text("1/9–6/12/2026")
    expect(leyenda).to_contain_text("1/9–6/12/2025")
    # R5 (C-399): one sentence with each total (the weeks still to come do not count) and the difference
    frase = p.locator("#fitness-comparar-frase")
    expect(frase).to_have_text("1/9–6/12/2026: 138 km en 8 semanas · 1/9–6/12/2025: 80 km en 8 semanas"
                               " → este año +58 km (+73 %)")
    expect(p.locator("#fitness-comparar-quitar")).to_be_visible()
    _sin_scroll(p, "comparando")
    p.locator("#fitness-semanas").scroll_into_view_if_needed()
    capturar(p, f"linea-comparar{sufijo}", completa=True)

    # R6 (C-400): sem 7, both weeks side by side with their real dates and a sentence of what changes
    p.locator("#fitness-medida").get_by_role("button", name="Por zonas").click()
    grafica.locator('svg g.semana[data-n="7"]').click()
    doble = p.locator("#fitness-comparar-semana")
    expect(doble).to_be_visible()
    tarjetas = doble.locator(".semana-comparada")
    expect(tarjetas).to_have_count(2)
    expect(tarjetas.first).to_contain_text("12 oct – 18 oct 2026")
    expect(tarjetas.first).to_contain_text("54,03 km")
    expect(tarjetas.first).to_contain_text("343 m")
    expect(tarjetas.last).to_contain_text("13 oct – 19 oct 2025")
    expect(tarjetas.last).to_contain_text("10,00 km")
    expect(tarjetas.last).to_contain_text("—")  # no climb that week
    expect(tarjetas.first.locator(".barra-zona-panel")).to_be_visible()
    expect(doble.locator(".frase-semana-comparada")).to_have_text("sem 7: este año +44 km y +2 h 50 min en Z4")
    _sin_scroll(p, "sem 7 lado a lado")
    doble.scroll_into_view_if_needed()
    capturar(p, f"linea-comparar-sem7{sufijo}", completa=True)
    # tapping one opens its week (R3)
    tarjetas.first.click()
    expect(p.locator("#fitness-semana")).to_be_visible()
    expect(p.locator("#fitness-semana-titulo")).to_have_text("Semana del 12 oct al 18 oct")
    expect(p.locator("#fitness-semana .actividad-semana")).to_have_count(2)

    # R7 (C-402): «Bici» changes both lines and the sentence
    p.locator("#fitness-deporte").get_by_role("button", name="Bici").click()
    expect(lineas).to_have_count(2)
    expect(frase).to_have_text("1/9–6/12/2026: 0 km en 8 semanas · 1/9–6/12/2025: 0 km en 8 semanas"
                               " → este año 0 km (—)")
    p.locator("#fitness-deporte").get_by_role("button", name="Correr").click()

    # R7: dates of the second period that are not valid say why and the chart stays as it was
    _comparar(p, ("2026-09-01", "2026-12-06"), ("2025-12-06", "2025-09-01"))
    expect(p.locator("#fitness-comparar-error")).to_contain_text("el “desde” tiene que ser antes del “hasta”")
    expect(lineas).to_have_count(2)
    expect(grafica.locator("svg g.semana")).to_have_count(14)

    # R7 (C-401): a shorter period — only the long one at sem 6, and «el otro periodo no llega a la sem 6»;
    # it starts before the history → «tu historial empieza el 1/6/2025»
    _comparar(p, ("2026-09-01", "2026-12-06"), ("2025-05-26", "2025-06-22"))
    expect(grafica.locator("svg .punto-semana.serie-b")).to_have_count(4)
    expect(p.locator("#fitness-comparar-aviso")).to_contain_text("tu historial empieza el 1/6/2025")
    grafica.locator('svg g.semana[data-n="6"]').click()
    expect(doble.locator(".semana-comparada")).to_have_count(1)
    expect(doble).to_contain_text("el otro periodo no llega a la sem 6")
    capturar(p, f"linea-comparar-corto{sufijo}", completa=True)

    # R4 (C-403): «Quitar comparación» → one line again
    p.locator("#fitness-comparar-quitar").click()
    expect(lineas).to_have_count(1)
    expect(frase).to_be_hidden()
    expect(doble).to_be_hidden()
    expect(p.locator("#fitness-comparar-quitar")).to_be_hidden()
    expect(grafica.locator("svg .punto-semana.serie-b")).to_have_count(0)
