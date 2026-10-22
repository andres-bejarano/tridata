"""Unit 017 (asked by Andrés on 2026-10-22): today's bar in two layers and a readable weight-and-deficit chart.

R10-R11 the bar (planned and done now according to Garmin) · R12 the chart of Peso and Balance: two panels,
the «kg» and «kcal» axes, a mark every Monday whose labels never overlap and are 44 px or more apart, and
its legend. Real Chromium at 390 × 844, light and dark; Claude, Whisper and «Actualizar Garmin» are
`MagicMock`s (conftest.py). Invented data only; «today» is Wednesday 2026-10-21.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.models import DailyStats  # noqa: E402

HOY = date(2026, 10, 21)
AYER = HOY - timedelta(days=1)
ANCHO = 390
LEYENDA = "● peso · — media 7 d · ┄ objetivo 70,0 kg · ▮ déficit · ▮ superávit"
OBJETIVO_PESO = {"objetivo_peso": {"kg": 70.0, "fecha": (HOY + timedelta(days=130)).isoformat()}}


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= ANCHO, f"{pantalla}: horizontal scroll ({ancho}px)"


def _etiquetas_legibles(grafica) -> list[str]:
    """The X labels: none overlaps another and there are 44 px or more between two of them (on screen)."""
    cajas = [e.bounding_box() for e in grafica.locator(".etiqueta-x").all()]
    cajas.sort(key=lambda c: c["x"])
    for a, b in zip(cajas, cajas[1:], strict=False):
        assert a["x"] + a["width"] <= b["x"], f"labels overlap: {a} {b}"
        assert (b["x"] + b["width"] / 2) - (a["x"] + a["width"] / 2) >= 44, f"labels closer than 44 px: {a} {b}"
    return [t.strip() for t in grafica.locator(".etiqueta-x").all_text_contents()]


# -- R10, R11: the bar in two layers ------------------------------------------------------------------

@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_barra_de_dos_capas(arrancar, abrir, capturar, oscuro):  # R10, R11 · R-23
    servidor = arrancar({"limite_saludable_pct": 25})
    servidor.hoy_con_garmin(6000, 400)
    servidor.apuntar("Comida", HOY, None, 1000)
    servidor.apuntar("Comida de ayer", AYER, None, 2000, hoy=AYER)
    servidor.store.save_daily_stats(DailyStats(stat_date=AYER, steps=10000, total_calories=2550))
    p = abrir(servidor, oscuro=oscuro)
    expect(p.locator("#queda-cifra")).to_contain_text("1.160")
    real = p.locator("#barra-real")
    expect(real).to_be_visible()
    pista = p.locator("#barra-pista").bounding_box()
    ancho = real.bounding_box()["width"] / pista["width"]
    assert abs(ancho - 1656 / 2160) < 0.01, ancho
    comido = p.locator("#barra-relleno").bounding_box()["width"] / pista["width"]
    assert abs(comido - 1000 / 2160) < 0.01, comido
    expect(p.locator("#barra-real-texto")).to_have_text(
        "Con lo hecho ahora: te quedan 656 kcal · 6.000 de 10.000 pasos · natación prevista 400 kcal aún no está "
        "en Garmin")
    expect(p.locator("#barra-leyenda-real")).to_be_visible()
    _sin_scroll(p, "hoy-dos-capas")
    p.locator("#queda").scroll_into_view_if_needed()
    capturar(p, "hoy-dos-capas" + ("-oscuro" if oscuro else ""))

    p.locator("#anterior").click()
    expect(p.locator("#titulo")).to_have_text("Ayer")
    expect(real).to_be_hidden()
    expect(p.locator("#barra-real-texto")).to_have_text("Con el gasto real de Garmin.")
    if not oscuro:
        capturar(p, "ayer-una-capa")
    p.locator("#siguiente").click()
    p.locator("#siguiente").click()
    expect(p.locator("#titulo")).to_have_text("Mañana")
    expect(real).to_be_hidden()
    expect(p.locator("#barra-real-texto")).to_be_hidden()


def test_barra_pasos_incompletos(arrancar, abrir):  # R11 · R-26
    from tridata.models import Activity

    servidor = arrancar()
    servidor.store.save_daily_stats(DailyStats(stat_date=HOY, steps=3000, total_calories=None))
    servidor.store.save_activities([Activity(activity_id="c1", activity_date=HOY, activity_type="running",
                                             name="Carrera", duration_seconds=1800, calories=400, avg_cadence=170)])
    servidor.apuntar("Comida", HOY, None, 1000)
    p = abrir(servidor)
    expect(p.locator("#barra-real-texto")).to_contain_text(
        "los pasos de Garmin de hoy están incompletos: lo hecho no cuenta pasos")
    expect(p.locator("#barra-real-texto")).to_contain_text("te quedan 800 kcal")  # 2,000 × 0.9 − 1,000


# -- R12: the chart, readable -------------------------------------------------------------------------

@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_grafica_de_peso_legible(arrancar, abrir, capturar, oscuro):  # R12
    servidor = arrancar(OBJETIVO_PESO)
    servidor.pesos_de_prueba()
    servidor.semana_de_prueba()
    p = abrir(servidor, "/#peso", oscuro=oscuro)
    tarjeta = p.locator("#peso-grafica")
    grafica = tarjeta.locator("svg")
    expect(grafica).to_have_count(1)
    expect(grafica.locator("circle.punto-peso")).to_have_count(3)
    expect(grafica.locator("polyline.linea-media")).to_have_count(1)
    expect(grafica.locator("line.linea-objetivo")).to_have_count(1)
    assert _textos_ejes(grafica) == ["kg", "kcal"]
    expect(grafica.locator(".barra-superavit")).to_have_count(2)  # Monday and Tuesday
    expect(grafica.locator(".barra-deficit")).to_have_count(1)  # today, open and striped
    expect(grafica.locator(".barra-kcal.provisional")).to_have_count(1)
    expect(grafica.locator(".linea-cero")).to_have_count(1)
    expect(grafica.locator(".marca-x")).to_have_count(4)
    assert _etiquetas_legibles(grafica) == ["28 sept", "5 oct", "12 oct", "19 oct"]
    expect(tarjeta.locator(".leyenda-grafica")).to_have_text(LEYENDA)
    _sin_scroll(p, "peso")
    tarjeta.scroll_into_view_if_needed()
    capturar(p, "peso-grafica" + ("-oscuro" if oscuro else ""))


def _textos_ejes(grafica) -> list[str]:
    return [t.strip() for t in grafica.locator(".titulo-eje").all_text_contents()]


def test_grafica_de_balance_legible(servidor, abrir, capturar):  # R7, R12
    servidor.ajustes.cambiar(OBJETIVO_PESO, desde=date(1, 1, 1))
    servidor.historial_de_prueba(date(2026, 6, 1))
    p = abrir(servidor, "/#balance")
    grafica = p.locator("#balance-grafica svg")
    expect(grafica.locator(".barra-kcal")).to_have_count(28)
    assert _textos_ejes(grafica) == ["kg", "kcal"]
    expect(grafica.locator(".marca-x")).to_have_count(4)
    assert _etiquetas_legibles(grafica) == ["28 sept", "5 oct", "12 oct", "19 oct"]
    expect(grafica.locator(".barra-deficit")).not_to_have_count(0)
    expect(grafica.locator(".barra-superavit")).not_to_have_count(0)
    expect(p.locator("#balance-grafica .leyenda-grafica")).to_have_text(LEYENDA)

    p.locator("#balance-rangos button", has_text="3 meses").click()
    expect(grafica.locator(".barra-kcal")).to_have_count(91)
    expect(grafica.locator(".marca-x")).to_have_count(13)
    etiquetas = _etiquetas_legibles(grafica)
    assert etiquetas == ["27 jul", "10 ago", "24 ago", "7 sept", "21 sept", "5 oct", "19 oct"]  # one yes, one no
    _sin_scroll(p, "3 meses")
    p.locator("#balance-grafica").scroll_into_view_if_needed()
    capturar(p, "balance-3m")

    p.locator("#balance-rangos button", has_text="Todo").click()
    expect(grafica.locator(".barra-kcal")).to_have_count(21)
    expect(grafica.locator(".marca-x")).to_have_count(21)
    etiquetas = _etiquetas_legibles(grafica)
    assert etiquetas == ["1 jun", "29 jun", "27 jul", "24 ago", "21 sept", "19 oct"]  # every 4
    assert re.fullmatch(r"● peso · — media 7 d · ┄ objetivo 70,0 kg · ▮ déficit · ▮ superávit",
                        p.locator("#balance-grafica .leyenda-grafica").inner_text().strip())
    _sin_scroll(p, "todo")
