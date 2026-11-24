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
    expect(sesion.locator(".vuelta").first).to_contain_text("cadencia 86 rpm")  # unit 107 R3: a ride's, in rpm
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


# -- unit 107 (REC-37): the date of every week on the axis, and ‹ › between the weeks ---------------------------------

def _eje(grafica) -> list[dict]:
    """Per bar of «Tus semanas»: its Monday as the chart says it («Semana del 12 oct al …»), the texts of its axis
    labels and their boxes."""
    return grafica.locator("svg").evaluate("""svg => [...svg.querySelectorAll("g.semana")].map(g => ({
        desde: g.getAttribute("aria-label").match(/^Semana del (.+?) al /)[1],
        en_curso: g.classList.contains("en-curso"),
        textos: [...g.querySelectorAll("text.etiqueta-x")].map(t => t.textContent.trim()),
        cajas: [...g.querySelectorAll("text.etiqueta-x")].map(t => { const r = t.getBoundingClientRect();
            return [r.left, r.top, r.right, r.bottom]; }),
    }))""")


def _se_cruzan(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _sin_solapes(eje, rango: str) -> None:
    cajas = [c for s in eje for c in s["cajas"]]
    for i, a in enumerate(cajas):
        for b in cajas[i + 1:]:
            assert not _se_cruzan(a, b), f"{rango}: two labels of the axis overlap ({a} · {b})"


@TEMAS
def test_cada_semana_con_su_fecha_y_en_curso_aparte(servidor, abrir, ir_a, capturar, oscuro):  # unit 107 R1
    _sembrar(servidor)
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ir_a(p, "fitness")
    grafica = p.locator("#fitness-grafica-semanas")
    expect(grafica.locator("g.semana")).to_have_count(6)
    eje = _eje(grafica)
    # «6 semanas»: the six dates (the week before the one in course too) and, besides, «en curso»
    for s in eje:
        fechas = [t for t in s["textos"] if t != "en curso"]
        assert fechas == [s["desde"]], f"week of {s['desde']}: {s['textos']}"
    assert [s["en_curso"] for s in eje] == [False] * 5 + [True]
    assert "en curso" in eje[-1]["textos"] and all("en curso" not in s["textos"] for s in eje[:-1])
    _sin_solapes(eje, "6 semanas")
    grafica.scroll_into_view_if_needed()
    capturar(p, f"fitness-eje-6-semanas{'-oscuro' if oscuro else '-claro'}")
    # 3 months and 1 year: dates skipped by the minimum separation, counted back from the week in course — never the
    # one before it because of «en curso» — and nothing overlapping
    rango = p.locator("#fitness-rango")
    for boton, n in (("3 meses", 13), ("1 año", 52)):
        rango.get_by_role("button", name=boton).click()
        expect(grafica.locator("g.semana")).to_have_count(n)
        eje = _eje(grafica)
        con_fecha = [len(eje) - 1 - i for i, s in enumerate(eje) if [t for t in s["textos"] if t != "en curso"]]
        con_fecha.reverse()
        assert con_fecha[0] == 0, f"{boton}: the week in course without its date"
        cada = con_fecha[1] if len(con_fecha) > 1 else len(eje)
        assert con_fecha == list(range(0, len(eje), cada)), f"{boton}: {con_fecha}"
        assert "en curso" in eje[-1]["textos"]
        _sin_solapes(eje, boton)


@TEMAS
def test_anterior_y_siguiente_entre_semanas(servidor, abrir, ir_a, capturar, oscuro):  # unit 107 R2
    _sembrar(servidor)
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ir_a(p, "fitness")
    grafica = p.locator("#fitness-grafica-semanas")
    p.locator("#fitness-deporte").get_by_role("button", name="Bici").click()  # unit 091's filter is kept
    grafica.locator(f'g.semana[data-lunes="{LUNES.isoformat()}"]').click()
    semana = p.locator("#fitness-semana")
    titulo = p.locator("#fitness-semana-titulo")
    anterior = semana.get_by_role("button", name="Semana anterior")
    siguiente = semana.get_by_role("button", name="Semana siguiente")
    expect(anterior).to_have_text("‹")
    expect(siguiente).to_have_text("›")
    expect(semana.get_by_role("button", name="Cerrar")).to_be_visible()
    expect(titulo).to_have_text("Semana del 12 oct al 18 oct")
    expect(semana.locator(".actividad-semana")).to_have_count(1)
    expect(semana.locator(".actividad-semana")).to_contain_text("Salida en bici")
    elegida = grafica.locator("g.semana.elegida")
    semana.scroll_into_view_if_needed()
    capturar(p, f"fitness-semana-anterior-siguiente{'-oscuro' if oscuro else '-claro'}", completa=True)

    anterior.click()  # the week before: its title, its totals and its list (no ride), marked on the chart
    expect(titulo).to_have_text("Semana del 5 oct al 11 oct")
    expect(elegida).to_have_attribute("data-lunes", (LUNES - timedelta(weeks=1)).isoformat())
    expect(semana).to_contain_text("sin actividades esta semana")
    expect(semana.locator(".totales-semana")).to_contain_text("sesiones")
    siguiente.click()
    expect(titulo).to_have_text("Semana del 12 oct al 18 oct")
    expect(semana.locator(".actividad-semana")).to_contain_text("Salida en bici")
    siguiente.click()  # the week in course: the last one, «›» off
    expect(titulo).to_contain_text("en curso")
    expect(elegida).to_have_attribute("data-lunes", (LUNES + timedelta(weeks=1)).isoformat())
    expect(siguiente).to_be_disabled()
    expect(anterior).to_be_enabled()
    for _ in range(5):
        anterior.click()
    expect(titulo).to_have_text("Semana del 14 sept al 20 sept")
    expect(elegida).to_have_attribute("data-lunes", (LUNES - timedelta(weeks=4)).isoformat())
    expect(anterior).to_be_disabled()  # the first week of the range
    expect(siguiente).to_be_enabled()
    expect(grafica.locator("g.semana.elegida")).to_have_count(1)
    _sin_scroll(p, "semana con ‹ ›")
    # tapping the bar marked still closes it
    elegida.click()
    expect(semana).to_be_hidden()
