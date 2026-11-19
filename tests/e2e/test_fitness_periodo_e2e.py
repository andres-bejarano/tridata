"""Unit 097 in a real Chromium at 390 × 844, light and dark (E2E-16 of `ver-fitness`): «Elegir fechas» from 1/9 to
15/10 → the load chart of those 45 days, «Tus semanas» from the week of 31/8 to the one of 12/10 and, under it, the
table of this period against the one before (18/7-31/8) with every sport and the total; dates that are not valid say
why and leave the screen as it was; back to «6 semanas» with «Bici» chosen, the table keeps every sport. Uses the
`conftest.py` of unit 010. Every value is invented (C-331, C-332, C-334, C-337).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.models import Activity  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py (a Wednesday)
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
FILAS = ["correr", "bici", "nadar", "fuerza", "otros", "total"]


def _sembrar(servidor) -> None:
    """A ride every 2 days since 1/7, a run every 4 and a strength session every week (history before 18/7)."""
    actividades, dia = [], date(2026, 7, 1)
    while dia < HOY:
        n = (dia - date(2026, 7, 1)).days
        actividades.append(Activity(activity_id=f"b{n}", activity_date=dia, activity_type="cycling",
                                    name="Rodaje de prueba", duration_seconds=3600 + 60 * (n % 7),
                                    distance_meters=30000, avg_hr=130 + n % 9))
        if n % 4 == 0:
            actividades.append(Activity(activity_id=f"c{n}", activity_date=dia, activity_type="running",
                                        name="Carrera de prueba", duration_seconds=3000, distance_meters=10000,
                                        avg_hr=145))
        if n % 7 == 0 and dia >= date(2026, 9, 1):
            actividades.append(Activity(activity_id=f"f{n}", activity_date=dia, activity_type="strength_training",
                                        name="Fuerza de prueba", duration_seconds=2700, avg_hr=110))
        dia += timedelta(days=2) if n % 2 == 0 else timedelta(days=1)
    servidor.store.save_activities(actividades)


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


@TEMAS
def test_elegir_fechas_y_comparar(servidor, abrir, ir_a, capturar, oscuro):  # E2E-16 · C-331, C-332, C-334, C-337
    _sembrar(servidor)
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ir_a(p, "fitness")
    barras = p.locator("#fitness-grafica svg .barra-tsb")
    expect(barras).to_have_count(42)  # it opens in 6 weeks, as before
    tabla = p.locator("#fitness-comparar")
    expect(tabla).to_be_visible()  # the table also comes with the shortcuts (R4)
    expect(tabla.locator("tbody[data-deporte]")).to_have_count(6)

    # «Elegir fechas»: from 1/9 to 15/10
    rango = p.locator("#fitness-rango")
    expect(p.locator("#fitness-fechas")).to_be_hidden()
    rango.get_by_role("button", name="Elegir fechas").click()
    expect(p.locator("#fitness-fechas")).to_be_visible()
    expect(p.locator("#fitness-hasta")).to_have_attribute("max", HOY.isoformat())
    p.locator("#fitness-desde").fill("2026-09-01")
    p.locator("#fitness-hasta").fill("2026-10-15")
    p.locator("#fitness-aplicar").click()

    expect(barras).to_have_count(45)
    expect(p.locator("#fitness-grafica svg .barra-tsb").first).to_have_attribute("data-dia", "2026-09-01")
    expect(p.locator("#fitness-grafica svg .barra-tsb").last).to_have_attribute("data-dia", "2026-10-15")
    expect(p.locator("#fitness-rango-texto")).to_have_text("del 1/9 al 15/10")
    expect(rango.get_by_role("button", name="Elegir fechas")).to_have_attribute("aria-pressed", "true")
    semanas = p.locator("#fitness-grafica-semanas g.semana")
    expect(semanas).to_have_count(7)
    expect(semanas.first).to_have_attribute("data-lunes", "2026-08-31")
    expect(semanas.last).to_have_attribute("data-lunes", "2026-10-12")

    expect(tabla).to_contain_text("del 1/9 al 15/10")
    expect(tabla).to_contain_text("del 18/7 al 31/8")
    for texto in ("Este periodo", "El anterior", "Diferencia", "Carrera", "Bici", "Natación", "Fuerza", "Otros",
                  "Total", "Horas", "Km", "Sesiones", "Horas por semana", "%"):
        expect(tabla).to_contain_text(texto)
    expect(tabla.locator("tbody[data-deporte]")).to_have_count(6)
    assert tabla.locator("tbody[data-deporte]").evaluate_all("ns => ns.map(n => n.dataset.deporte)") == FILAS
    _sin_scroll(p, "fitness con fechas")
    p.locator("#fitness-semanas").scroll_into_view_if_needed()
    capturar(p, f"fitness-fechas{sufijo}", completa=True)

    # dates that are not valid: the reason, and nothing repainted (C-337)
    p.locator("#fitness-desde").fill("2026-10-15")
    p.locator("#fitness-hasta").fill("2026-09-01")
    p.locator("#fitness-aplicar").click()
    error = p.locator("#fitness-fechas-error")
    expect(error).to_be_visible()
    expect(error).to_contain_text("el “desde” tiene que ser antes del “hasta”")
    expect(barras).to_have_count(45)
    expect(p.locator("#fitness-rango-texto")).to_have_text("del 1/9 al 15/10")
    expect(semanas).to_have_count(7)
    expect(tabla).to_contain_text("del 18/7 al 31/8")
    p.locator("#fitness-fechas").scroll_into_view_if_needed()
    capturar(p, f"fitness-fechas-malas{sufijo}")

    # back to «6 semanas» and «Bici» in «Tus semanas»: the table keeps every sport (C-334)
    rango.get_by_role("button", name="6 semanas").click()
    expect(barras).to_have_count(42)
    expect(p.locator("#fitness-fechas")).to_be_hidden()
    p.locator("#fitness-deporte").get_by_role("button", name="Bici").click()
    expect(tabla.locator("tbody[data-deporte]")).to_have_count(6)
    expect(tabla).to_contain_text("Natación")
    expect(tabla).to_contain_text("del 30/7 al 9/9")
    _sin_scroll(p, "fitness 6 semanas bici")
