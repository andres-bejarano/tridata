"""Unit 096 in a real Chromium at 390 × 844, light and dark (E2E-15 of `ver-fitness`): Ajustes › Pulso y zonas shows
three blocks — run, ride and swim, the last two «estimado desde carrera» —; a race brought from Garmin leaves a
proposal of a new run threshold in Fitness and in Ajustes, and only its «Sí» saves it from today; a ride is coloured
with the ride's zones. Uses the `conftest.py` of unit 010. Every value is invented (C-320, C-321, C-322, C-323).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata import umbral  # noqa: E402
from tridata.garmin_client import ActividadGarmin  # noqa: E402
from tridata.models import Activity, ActivityLap  # noqa: E402
from tridata.nutricion import DESDE_SIEMPRE  # noqa: E402

HOY = date(2026, 10, 21)  # conftest's
AYER = HOY - timedelta(days=1)
LUNES = HOY - timedelta(days=HOY.weekday())
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
ZONAS = [[48, 148], [149, 157], [158, 165], [166, 174], [175, 192]]  # run threshold 175
PULSO = {"fc_max": 192, "fc_reposo": 48, "zonas_fc": ZONAS, "fc_origen": {"tipo": "configuracion"}}


def _sembrar(servidor) -> None:
    store = servidor.store
    store.save_activities([
        ActividadGarmin(activity_id="r-1", activity_date=AYER, activity_type="running", name="10K de la ciudad",
                        duration_seconds=38 * 60, distance_meters=10000, avg_hr=171, event_type="race"),
        Activity(activity_id="b-1", activity_date=LUNES, activity_type="road_biking", name="Salida en bici",
                 duration_seconds=1200, distance_meters=10000, avg_hr=162),
    ])
    store.save_activity_laps([ActivityLap("r-1", 1, distance_meters=10000, duration_seconds=2280, avg_hr=171),
                              ActivityLap("b-1", 1, distance_meters=10000, duration_seconds=1200, avg_hr=162)])
    servidor.ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)
    umbral.revisar_sesion(store, "r-1", HOY)  # what the sync does after bringing the race


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


@TEMAS
def test_tres_bloques_aviso_y_si(servidor, abrir, ir_a, capturar, oscuro):  # E2E-15 · C-320, C-322, C-323
    _sembrar(servidor)
    sufijo = "-oscuro" if oscuro else "-claro"

    # C-320: three blocks, ride and swim estimated from the run
    p = abrir(servidor, "/ajustes", oscuro=oscuro)
    grupo = p.locator("#pulso")
    bloques = grupo.locator(".bloque-deporte")
    expect(bloques).to_have_count(3)
    expect(bloques.nth(0)).to_contain_text("Carrera")
    expect(bloques.nth(0)).to_contain_text("umbral 175")
    expect(bloques.nth(0)).not_to_contain_text("estimado desde carrera")
    expect(bloques.nth(1)).to_contain_text("Bici")
    expect(bloques.nth(1)).to_contain_text("umbral 168")
    expect(bloques.nth(1)).to_contain_text("estimado desde carrera")
    expect(bloques.nth(1)).to_contain_text("Z4 158–167")
    expect(bloques.nth(2)).to_contain_text("Natación")
    expect(bloques.nth(2)).to_contain_text("umbral 168")
    expect(bloques.nth(2)).to_contain_text("estimado desde carrera")
    # the proposal is also in Ajustes › Pulso y zonas
    expect(grupo.locator(".propuesta-umbral")).to_contain_text("tu umbral de carrera pasa de 175 a 171")
    _sin_scroll(p, "ajustes-zonas")
    grupo.scroll_into_view_if_needed()
    capturar(p, f"ajustes-zonas-por-deporte{sufijo}", completa=True)

    # C-322: Fitness shows the proposal with the zones it would give, and Sí / No
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ir_a(p, "fitness")
    aviso = p.locator("#fitness-propuestas .propuesta-umbral")
    expect(aviso).to_have_count(1)
    expect(aviso).to_contain_text("tu umbral de carrera pasa de 175 a 171 (10K de la ciudad, 20/10), ¿actualizo desde hoy?")
    expect(aviso).to_contain_text("Z5 desde 171")
    expect(aviso.get_by_role("button", name="Sí")).to_be_visible()
    expect(aviso.get_by_role("button", name="No")).to_be_visible()
    _sin_scroll(p, "fitness-propuesta")
    aviso.scroll_into_view_if_needed()
    capturar(p, f"fitness-propuesta-umbral{sufijo}")

    # C-323: «Sí» saves it from today; the run block says 171
    aviso.get_by_role("button", name="Sí").click()
    expect(p.locator("#fitness-propuestas .propuesta-umbral")).to_have_count(0)
    historial = servidor.ajustes.historial_fc()
    assert historial.vigente(HOY).zonas_de("carrera")[0][4][0] == 171
    assert historial.vigente(AYER).zonas_de("carrera")[0][4][0] == 175
    p = abrir(servidor, "/ajustes", oscuro=oscuro)
    bloques = p.locator("#pulso .bloque-deporte")
    expect(bloques.nth(0)).to_contain_text("umbral 171")
    expect(bloques.nth(1)).to_contain_text("umbral 164")
    expect(p.locator("#pulso .propuesta-umbral")).to_have_count(0)


@TEMAS
def test_salida_en_bici_con_zonas_de_bici(servidor, abrir, ir_a, capturar, oscuro):  # C-321, C-329
    _sembrar(servidor)
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ir_a(p, "fitness")
    p.locator(f'#fitness-grafica-semanas g.semana[data-lunes="{LUNES.isoformat()}"]').click()
    p.locator("#fitness-semana .actividad-semana", has_text="Salida en bici").click()
    sesion = p.locator("#fitness-sesion")
    expect(sesion.locator(".vuelta").first).to_be_visible()
    expect(sesion.locator(".vuelta .zona-vuelta").first).to_have_text("Z4")  # with the run's it would be Z3
    expect(sesion).to_contain_text("zonas de bici estimadas desde carrera")
    _sin_scroll(p, "sesion-bici")
    sesion.scroll_into_view_if_needed()
    capturar(p, f"sesion-bici-zonas{sufijo}")


def test_no_quita_el_aviso_y_no_vuelve(servidor, abrir, ir_a):  # C-326
    _sembrar(servidor)
    p = abrir(servidor, "/#dia")
    ir_a(p, "fitness")
    aviso = p.locator("#fitness-propuestas .propuesta-umbral")
    aviso.get_by_role("button", name="No").click()
    expect(p.locator("#fitness-propuestas .propuesta-umbral")).to_have_count(0)
    p = abrir(servidor, "/ajustes")
    expect(p.locator("#pulso .bloque-deporte").nth(0)).to_contain_text("umbral 175")
    expect(p.locator("#pulso .propuesta-umbral")).to_have_count(0)
