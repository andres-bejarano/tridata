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
    # unit 090: it opens in 6 weeks (the 39 days this history has); «14 días» gives the 14 bars of before
    expect(p.locator("#fitness-grafica svg .barra-tsb")).to_have_count(39)
    p.locator("#fitness-rango").get_by_role("button", name="14 días").click()
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
    # the card puts the cursor in «FC máxima» a moment later (a timer, late under the test clock): typing before
    # it lands sends the next field's digits to «FC máxima» («18055»)
    expect(ficha.get_by_label("FC máxima (ppm)")).to_be_focused()
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
    expect(ficha.get_by_label("FC máxima (ppm)")).to_be_focused()
    ficha.get_by_label("FC en reposo (ppm)").fill("95")
    ficha.get_by_label("FC máxima (ppm)").fill("90")
    ficha.get_by_role("button", name="Guardar").click()
    expect(ficha.locator(".aviso")).to_be_visible()
    expect(ficha.locator(".aviso")).to_contain_text("No se ha guardado nada")
    assert servidor.ajustes.historial_fc().limites(HOY) == (180, 55)


# -- unit 090 (E2E-11 of `ver-fitness`): the range, the weeks by sport (km, zones) and «¿Cómo se calcula?» ----------

LUNES = HOY - timedelta(days=HOY.weekday())  # 19/10; the week before, 12-18/10, is the «Behobia» one
PREVIA = LUNES - timedelta(days=7)


def _sembrar_un_ano(servidor) -> None:
    store = servidor.store
    store.save_activities([
        Activity(activity_id=f"r{i:03d}", activity_date=HOY - timedelta(days=i), activity_type="cycling",
                 name="Rodaje de prueba", duration_seconds=3600, distance_meters=30000, avg_hr=130 + i % 9)
        for i in range(4, 400, 3)
    ] + [
        Activity(activity_id="tirada", activity_date=PREVIA + timedelta(days=5), activity_type="trail_running",
                 name="Tirada de prueba", duration_seconds=3600, distance_meters=34000, avg_hr=140),
        Activity(activity_id="behobia", activity_date=PREVIA + timedelta(days=6), activity_type="running",
                 name="Carrera de prueba", duration_seconds=4800, distance_meters=20000, avg_hr=160),
        Activity(activity_id="nado", activity_date=PREVIA + timedelta(days=2), activity_type="lap_swimming",
                 name="Natación de prueba", duration_seconds=2700, distance_meters=2500),
        Activity(activity_id="fuerza", activity_date=LUNES, activity_type="strength_training", name="Fuerza de prueba",
                 duration_seconds=2700, distance_meters=400, avg_hr=110),
    ])
    # C-292: 20 min in Z2, 30 in Z4 and 10 in Z1; C-293: the swim's laps without HR
    store.save_activity_laps([ActivityLap(activity_id="tirada", lap_index=i, duration_seconds=s, avg_hr=h)
                              for i, (s, h) in enumerate(((1200, 130), (1800, 160), (600, 100)))]
                             + [ActivityLap(activity_id="nado", lap_index=i, duration_seconds=900, distance_meters=800)
                                for i in range(3)])


@TEMAS
def test_rango_semanas_y_como_se_calcula(servidor, abrir, ir_a, capturar, oscuro):  # E2E-11 · C-289..C-294
    _sembrar_un_ano(servidor)
    servidor.ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ir_a(p, "fitness")

    # C-289: it opens in 6 weeks
    rango = p.locator("#fitness-rango")
    expect(rango.locator('button[aria-pressed="true"]')).to_have_text("6 semanas")
    expect(p.locator("#fitness-grafica svg .barra-tsb")).to_have_count(42)
    semanas = p.locator("#fitness-grafica-semanas svg .semana")
    expect(semanas).to_have_count(6)
    expect(p.locator("#fitness-grafica-semanas svg .semana.en-curso")).to_have_count(1)
    expect(p.locator("#fitness-grafica-semanas")).to_contain_text("en curso")
    expect(p.locator("#fitness-medida button[aria-pressed=\"true\"]")).to_have_text("Horas")

    # C-291, C-294: km stacked by sport, the week total, strength without km
    p.locator("#fitness-medida").get_by_role("button", name="Km").click()
    previa = p.locator(f'#fitness-grafica-semanas svg .semana[data-lunes="{PREVIA.isoformat()}"]')
    for deporte in ("correr", "bici", "nadar"):
        expect(previa.locator(f'.barra-semana[data-deporte="{deporte}"]')).to_have_count(1)
    expect(previa.locator(".total-semana")).to_have_text("116,5")  # 54 running + 60 cycling + 2,5 swimming
    en_curso = p.locator("#fitness-grafica-semanas svg .semana.en-curso")
    expect(en_curso.locator('.barra-semana[data-deporte="fuerza"]')).to_have_count(0)
    expect(p.locator("#fitness-semanas .leyenda-grafica")).to_contain_text("correr")
    _sin_scroll(p, "semanas-km")
    p.locator("#fitness-semanas").scroll_into_view_if_needed()
    capturar(p, f"fitness-semanas-km{sufijo}")

    # C-292, C-293: zones by laps, «aproximado por vueltas»
    p.locator("#fitness-medida").get_by_role("button", name="Zonas").click()
    expect(p.locator("#fitness-semanas")).to_contain_text("aproximado por vueltas")
    expect(previa.locator('.barra-semana[data-zona="sin_pulso"]')).to_have_count(1)
    expect(previa.locator('.barra-semana[data-zona="z4"]')).to_have_count(1)
    expect(p.locator("#fitness-semanas .leyenda-grafica")).to_contain_text("sin pulso")
    capturar(p, f"fitness-semanas-zonas{sufijo}")

    # C-290: «¿Cómo se calcula?», folded when it opens
    como = p.locator("#fitness-como")
    expect(como.locator("p").first).to_be_hidden()
    como.locator("summary").click()
    for texto in ("TRIMP", "42 días", "7 días", "no es el TSS de potencia", "tendencia"):
        expect(como).to_contain_text(texto)
    como.scroll_into_view_if_needed()
    capturar(p, f"fitness-como-se-calcula{sufijo}")

    # C-289: «1 año» shows the 365 days and the 52 weeks
    rango.get_by_role("button", name="1 año").click()
    expect(p.locator("#fitness-grafica svg .barra-tsb")).to_have_count(365)
    expect(semanas).to_have_count(52)
    expect(rango.locator('button[aria-pressed="true"]')).to_have_text("1 año")
    expect(p.locator("#fitness-carga")).to_contain_text("último año")
    _sin_scroll(p, "fitness-1-ano")
    capturar(p, f"fitness-1-ano{sufijo}", completa=True)


# -- unit 091 (E2E-12 of `ver-fitness`): «Tus semanas» of one sport (R-237, R-238) -----------------------------------

SIN_BICI = LUNES - timedelta(weeks=2)  # 5-11/10: only a run, the bike at zero


def _sembrar_deportes(servidor) -> None:
    """In course (19-25/10): 15 km of bike and a strength session. 12-18/10: a 10 km run (20 min Z2 + 10 Z4 by laps),
    30 km of bike (30 min Z5 by laps) and the conftest swim (no HR). 5-11/10: an 8 km run, no bike."""
    store = servidor.store
    store.save_activities([
        Activity(activity_id="bici-curso", activity_date=HOY - timedelta(days=1), activity_type="cycling",
                 name="Bici de prueba", duration_seconds=2700, distance_meters=15000, avg_hr=125),
        Activity(activity_id="fuerza-curso", activity_date=LUNES, activity_type="strength_training",
                 name="Fuerza de prueba", duration_seconds=2700, distance_meters=400, avg_hr=110),
        Activity(activity_id="carrera-previa", activity_date=PREVIA + timedelta(days=1), activity_type="running",
                 name="Carrera de prueba", duration_seconds=1800, distance_meters=10000, avg_hr=140),
        Activity(activity_id="bici-previa", activity_date=PREVIA + timedelta(days=3), activity_type="cycling",
                 name="Bici larga de prueba", duration_seconds=1800, distance_meters=30000, avg_hr=172),
        Activity(activity_id="carrera-sin-bici", activity_date=SIN_BICI + timedelta(days=2), activity_type="running",
                 name="Rodaje de prueba", duration_seconds=2400, distance_meters=8000, avg_hr=135),
    ])
    store.save_activity_laps([ActivityLap(activity_id="carrera-previa", lap_index=i, duration_seconds=s, avg_hr=h)
                              for i, (s, h) in enumerate(((1200, 130), (600, 160)))]
                             + [ActivityLap(activity_id="bici-previa", lap_index=0, duration_seconds=1800, avg_hr=175)])


def _semana(p, lunes: date):
    return p.locator(f'#fitness-grafica-semanas svg .semana[data-lunes="{lunes.isoformat()}"]')


@TEMAS
def test_semanas_de_un_deporte(servidor, abrir, ir_a, capturar, oscuro):  # E2E-12 · C-295..C-297
    _sembrar_deportes(servidor)
    servidor.ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ir_a(p, "fitness")
    grafica = p.locator("#fitness-grafica-semanas")
    barras = grafica.locator("svg .barra-semana")
    deporte = p.locator("#fitness-deporte")
    medida = p.locator("#fitness-medida")
    previa, sin_bici, en_curso = _semana(p, PREVIA), _semana(p, SIN_BICI), grafica.locator("svg .semana.en-curso")

    # C-295: it opens in «Todos», the chart as before (every sport of the week stacked)
    expect(deporte.locator('button[aria-pressed="true"]')).to_have_text("Todos")
    for nombre in ("Todos", "Correr", "Bici", "Nadar", "Fuerza"):
        expect(deporte.get_by_role("button", name=nombre)).to_be_visible()
    medida.get_by_role("button", name="Km").click()
    expect(previa.locator(".total-semana")).to_have_text("40,0")  # 10 running + 30 cycling (the swim has no distance)
    expect(previa.locator('.barra-semana[data-deporte="correr"]')).to_have_count(1)

    # C-295 (R1, the load-bearing one): «Bici» + km shows only the bike and its total; a week without bike at zero
    deporte.get_by_role("button", name="Bici").click()
    expect(deporte.locator('button[aria-pressed="true"]')).to_have_text("Bici")
    expect(grafica.locator("svg .semana")).to_have_count(6)
    expect(barras).to_have_count(2)
    expect(grafica.locator('svg .barra-semana:not([data-deporte="bici"])')).to_have_count(0)
    expect(previa.locator(".total-semana")).to_have_text("30,0")
    expect(en_curso.locator(".total-semana")).to_have_text("15,0")
    expect(grafica).to_contain_text("en curso")
    expect(sin_bici.locator(".barra-semana")).to_have_count(0)
    expect(sin_bici.locator(".total-semana")).to_have_text("0,0")
    expect(p.locator("#fitness-semanas .leyenda-grafica")).to_contain_text("bici")
    expect(p.locator("#fitness-semanas .leyenda-grafica")).not_to_contain_text("correr")
    _sin_scroll(p, "semanas-bici-km")
    p.locator("#fitness-semanas").scroll_into_view_if_needed()
    capturar(p, f"fitness-semanas-bici-km{sufijo}")

    # changing the measure or the range keeps the sport
    medida.get_by_role("button", name="Horas").click()
    expect(deporte.locator('button[aria-pressed="true"]')).to_have_text("Bici")
    expect(previa.locator(".total-semana")).to_have_text("0,5")
    expect(en_curso.locator(".total-semana")).to_have_text("0,8")
    expect(grafica.locator('svg .barra-semana:not([data-deporte="bici"])')).to_have_count(0)
    p.locator("#fitness-rango").get_by_role("button", name="3 meses").click()
    expect(grafica.locator("svg .semana")).to_have_count(13)
    expect(deporte.locator('button[aria-pressed="true"]')).to_have_text("Bici")
    expect(barras).to_have_count(2)
    expect(grafica.locator('svg .barra-semana:not([data-deporte="bici"])')).to_have_count(0)
    p.locator("#fitness-rango").get_by_role("button", name="6 semanas").click()
    expect(grafica.locator("svg .semana")).to_have_count(6)

    # C-296 (R2): «Correr» + zones: only the zones of the runs (Z2 and Z4), not the bike's Z5 nor the swim without HR
    deporte.get_by_role("button", name="Correr").click()
    medida.get_by_role("button", name="Zonas").click()
    expect(p.locator("#fitness-semanas")).to_contain_text("aproximado por vueltas")
    expect(previa.locator('.barra-semana[data-zona="z2"]')).to_have_count(1)
    expect(previa.locator('.barra-semana[data-zona="z4"]')).to_have_count(1)
    expect(previa.locator('.barra-semana[data-zona="z5"]')).to_have_count(0)
    expect(previa.locator('.barra-semana[data-zona="sin_pulso"]')).to_have_count(0)
    expect(previa.locator(".total-semana")).to_have_text("0,5")  # 30 min of laps of the run
    expect(en_curso.locator(".barra-semana")).to_have_count(0)
    capturar(p, f"fitness-semanas-correr-zonas{sufijo}")
    # with «Todos» the zones add every sport again
    deporte.get_by_role("button", name="Todos").click()
    expect(previa.locator('.barra-semana[data-zona="z5"]')).to_have_count(1)
    expect(previa.locator('.barra-semana[data-zona="sin_pulso"]')).to_have_count(1)

    # C-297 (R3): «Fuerza» + km: no bars, it says strength adds no km, the week in course still «en curso»
    deporte.get_by_role("button", name="Fuerza").click()
    medida.get_by_role("button", name="Km").click()
    expect(barras).to_have_count(0)
    expect(grafica).to_contain_text("la fuerza no suma km")
    expect(grafica).to_contain_text("en curso")
    expect(grafica.locator("svg .semana")).to_have_count(6)
    capturar(p, f"fitness-semanas-fuerza-km{sufijo}")
    # with hours the strength session is there
    medida.get_by_role("button", name="Horas").click()
    expect(en_curso.locator('.barra-semana[data-deporte="fuerza"]')).to_have_count(1)
    expect(grafica.locator('svg .barra-semana:not([data-deporte="fuerza"])')).to_have_count(0)

    # back to «Todos»: the chart of always
    deporte.get_by_role("button", name="Todos").click()
    medida.get_by_role("button", name="Km").click()
    expect(previa.locator(".total-semana")).to_have_text("40,0")
    expect(previa.locator('.barra-semana[data-deporte="bici"]')).to_have_count(1)
    expect(p.locator("#fitness-semanas .leyenda-grafica")).to_contain_text("la fuerza no suma km")
    _sin_scroll(p, "semanas-todos")
