"""Unit 031 in a real Chromium at 390 × 844, light and dark (E2E-4 of `ver-fitness`): Fitness with its three blocks
(load with its 14-day chart, last activity, recovery) and where today's HR comes from; tapping the activity opens its
laps with the zone of each one and its weather; Ajustes › «Pulso y zonas» saves and refuses a resting HR above the
max. Uses the `conftest.py` of unit 010. Every value is invented (C-146, C-147, C-152, R6, R7).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.models import Activity, ActivityLap, DailyStats, HRVRecord, SleepRecord  # noqa: E402
from tridata.nutricion import DESDE_SIEMPRE  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
ZONAS = [[55, 120], [121, 140], [141, 155], [156, 168], [169, 180]]
PULSO = {"fc_max": 180, "fc_reposo": 55, "zonas_fc": ZONAS, "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}


def _sembrar(servidor) -> None:
    store, ayer = servidor.store, HOY - timedelta(days=1)
    store.save_activities([
        Activity(activity_id=f"h{i:03d}", activity_date=HOY - timedelta(days=i), activity_type="cycling",
                 name="Rodaje de prueba", duration_seconds=3600, distance_meters=30000, avg_hr=130 + i % 9)
        for i in range(2, 40, 2)
    ] + [Activity(activity_id="c-ayer", activity_date=ayer, activity_type="running", name="Carrera de prueba",
                  duration_seconds=3000, distance_meters=10000, avg_hr=147, calories=610)])
    store.save_activity_laps([ActivityLap(activity_id="c-ayer", lap_index=i, distance_meters=1000, duration_seconds=300,
                                          avg_pace_seconds_per_km=300, avg_hr=125 + 5 * i) for i in range(10)])
    store.save_activity_weather("c-ayer", {"temp_c": 14.0, "humidity_pct": 70.0, "wind_speed_kmh": 8.0,
                                           "condition": "Despejado"})
    for i in range(0, 7):
        store.save_sleep(SleepRecord(sleep_date=HOY - timedelta(days=i), total_sleep_seconds=25200 - i * 300,
                                     sleep_score=80))
        store.save_hrv(HRVRecord(hrv_date=HOY - timedelta(days=i), last_night_avg=60 + i % 3))
        if i:
            store.save_daily_stats(DailyStats(stat_date=HOY - timedelta(days=i), steps=9000, resting_hr=48 + i % 2,
                                              total_calories=2500))


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


@TEMAS
def test_recorrido_de_fitness(servidor, abrir, ir_a, capturar, oscuro):  # E2E-4 · C-146, C-147, C-152, R6
    _sembrar(servidor)
    servidor.ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ir_a(p, "fitness")
    pantalla = p.locator("section#fitness")
    expect(pantalla).to_be_visible()

    carga = p.locator("#fitness-carga")
    for texto in ("Forma", "Fatiga", "Frescura"):
        expect(carga).to_contain_text(texto)
    expect(p.locator("#fitness-grafica svg")).to_be_visible()
    expect(p.locator("#fitness-grafica svg .linea-ctl")).to_have_count(1)
    expect(p.locator("#fitness-grafica svg .barra-tsb")).to_have_count(14)
    expect(p.locator("#fitness-pulso")).to_contain_text("de tu prueba de esfuerzo de 2025-11")

    ultima = p.locator("#fitness-ultima")
    expect(ultima).to_contain_text("Carrera de prueba")
    expect(ultima).to_contain_text("10,0 km")
    expect(ultima).to_contain_text("50 min")
    expect(ultima).to_contain_text("Pulso medio 147")
    expect(ultima).to_contain_text("10 vueltas")

    recuperacion = p.locator("#fitness-recuperacion")
    expect(recuperacion).to_contain_text("Sueño")
    expect(recuperacion).to_contain_text("7 h 0 min")
    expect(recuperacion).to_contain_text("aún no ha llegado")  # today's resting HR (G-47)
    expect(recuperacion).to_contain_text("media 7 días")
    _sin_scroll(p, "fitness")
    capturar(p, f"fitness{sufijo}", completa=True)

    expect(p.locator("#fitness-detalle")).to_be_hidden()
    ultima.click()
    detalle = p.locator("#fitness-detalle")
    expect(detalle).to_be_visible()
    expect(detalle.locator(".vuelta")).to_have_count(10)
    expect(detalle.locator(".vuelta").first).to_contain_text("Z2")
    expect(detalle.locator(".vuelta").last).to_contain_text("Z5")
    expect(detalle.locator(".vuelta").first).to_contain_text("5:00 /km")
    expect(detalle).to_contain_text("Despejado")
    expect(detalle).to_contain_text("14 °C")
    _sin_scroll(p, "detalle")
    detalle.scroll_into_view_if_needed()
    capturar(p, f"fitness-detalle{sufijo}", completa=True)


@TEMAS
def test_pulso_y_zonas_en_ajustes(servidor, abrir, capturar, oscuro):  # R6, R7
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/ajustes", oscuro=oscuro)
    grupo = p.locator("#pulso")
    expect(grupo).to_be_visible()
    expect(grupo).to_contain_text("sin confirmar")
    grupo.locator("button.ajuste").first.click()
    ficha = grupo.locator(".tarjeta.nueva")
    expect(ficha).to_be_visible()
    ficha.get_by_label("FC máxima (ppm)").fill("180")
    ficha.get_by_label("FC en reposo (ppm)").fill("55")
    for i, (desde, hasta) in enumerate(ZONAS, start=1):
        ficha.get_by_label(f"Zona {i} desde").fill(str(desde))
        ficha.get_by_label(f"Zona {i} hasta").fill(str(hasta))
    ficha.get_by_label("De dónde salen").select_option("prueba")
    ficha.get_by_label("Mes de la prueba").fill("2025-11")
    _sin_scroll(p, "ajustes-pulso")
    capturar(p, f"ajustes-pulso{sufijo}", completa=True)
    ficha.get_by_role("button", name="Guardar").click()
    expect(p.locator("#mensaje")).to_contain_text("Guardado")
    expect(grupo).to_contain_text("180")
    expect(grupo).to_contain_text("prueba de esfuerzo de 2025-11")
    assert servidor.ajustes.historial_fc().limites(HOY) == (180, 55)

    # R7: a resting HR above the max is not saved and says why
    grupo.locator("button.ajuste").first.click()
    ficha = grupo.locator(".tarjeta.nueva")
    ficha.get_by_label("FC en reposo (ppm)").fill("95")
    ficha.get_by_label("FC máxima (ppm)").fill("90")
    ficha.get_by_role("button", name="Guardar").click()
    expect(ficha.locator(".aviso")).to_be_visible()
    expect(ficha.locator(".aviso")).to_contain_text("No se ha guardado nada")
    assert servidor.ajustes.historial_fc().limites(HOY) == (180, 55)
