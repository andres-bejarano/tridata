"""Unit 017: the Balance tab in a real Chromium at 390 × 844, light and dark. Invented data only.

R1-R2 the week · R3-R4, R6 spread, apply and back to the plan · R7 the chart and its ranges · R8 the list of
weeks, opening one and a day of it in Hoy. Claude, Whisper and «Actualizar Garmin» are `MagicMock`s
(conftest.py); «today» is Wednesday 2026-10-21.
"""
from __future__ import annotations

import re
from datetime import date

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

ANCHO = 390


def _texto(locator) -> str:
    return re.sub(r"\s+", " ", locator.inner_text()).strip()


def _textos(locator) -> list[str]:
    return [re.sub(r"\s+", " ", t).strip() for t in locator.all_inner_texts()]


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= ANCHO, f"{pantalla}: horizontal scroll ({ancho}px)"


def _pequenos(p) -> list[str]:
    return p.evaluate("""() => [...document.querySelectorAll('#balance button, #balance a[href], #balance .fila-dia')]
      .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !e.closest('[hidden]'); })
      .filter((e) => e.getBoundingClientRect().height < 44)
      .map((e) => `${e.tagName}#${e.id}.${e.className} ${e.getBoundingClientRect().height}`)""")


def _lado(fila) -> str:
    """Whether the bar of a row goes up (surplus) or down (deficit) from its zero line, measured on screen."""
    cero = fila.locator(".barra-dia-cero").bounding_box()
    barra = fila.locator(".barra-dia-relleno").bounding_box()
    if barra["y"] + barra["height"] <= cero["y"] + cero["height"] + 1 and barra["y"] < cero["y"]:
        return "arriba"
    if barra["y"] >= cero["y"] - 1:
        return "abajo"
    return "?"


# -- R1, R2: the week ------------------------------------------------------------------------------------

@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_la_semana(servidor, abrir, capturar, oscuro):  # R1, R2 · R-24, R-25
    servidor.semana_de_prueba()
    p = abrir(servidor, "/#balance", oscuro=oscuro)
    expect(p.locator("section#balance")).to_be_visible()
    expect(p.locator("#balance-titulo")).to_have_text("Esta semana · del 19 al 25 de octubre")
    filas = p.locator("#balance-semana .fila-dia")
    expect(filas).to_have_count(7)
    lunes, martes, hoy = filas.nth(0), filas.nth(1), filas.nth(2)
    for texto in ("Lunes 19", "2.650 comidas", "gasto 2.500 real", "+150 kcal (+6 %)"):
        expect(lunes).to_contain_text(texto)
    for texto in ("Martes 20", "2.150 comidas", "gasto 2.000 estimado", "+150 kcal (+7,5 %)"):
        expect(martes).to_contain_text(texto)
    for texto in ("Hoy", "760 comidas", "gasto 2.000 estimado", "−1.240 kcal", "provisional"):
        expect(hoy).to_contain_text(texto)
    expect(lunes.locator(".barra-dia")).to_have_class(re.compile(r"\bsuperavit\b"))
    assert _lado(lunes) == "arriba" and _lado(martes) == "arriba"
    expect(hoy).to_have_class(re.compile(r"\bprovisional\b"))
    expect(filas.nth(3)).to_contain_text("−10 %")
    expect(filas.nth(4)).to_contain_text("−10 %")
    expect(filas.nth(5)).to_contain_text("0 % · carrera")
    expect(filas.nth(6)).to_contain_text("0 % · carrera")
    for i in (3, 4, 5, 6):
        expect(filas.nth(i)).not_to_contain_text("kcal (")
    expect(p.locator("#balance-resumen")).to_have_text("Acumulado +300 kcal · media +150 kcal/día (+6,7 %) · 2 días")
    _sin_scroll(p, "balance")
    assert _pequenos(p) == []
    capturar(p, "balance" + ("-oscuro" if oscuro else ""))
    if not oscuro:
        capturar(p, "balance-completo", completa=True)

    # seeing the week changes nothing: Hoy keeps its 1,040
    p.locator(".pestana", has_text="Hoy").click()
    expect(p.locator("#queda-cifra")).to_contain_text("1.040")


def test_un_deficit_cerrado_con_garmin_va_hacia_abajo(servidor, abrir):  # R1
    from tridata.models import DailyStats

    lunes = date(2026, 10, 19)
    servidor.apuntar("Comida del lunes", lunes, None, 2000, hoy=lunes)
    servidor.store.save_daily_stats(DailyStats(stat_date=lunes, steps=20000, total_calories=2500))
    servidor.cierres.al_dia(date(2026, 10, 21))
    p = abrir(servidor, "/#balance")
    fila = p.locator("#balance-semana .fila-dia").nth(0)
    expect(fila).to_contain_text("−500 kcal (−20 %)")
    expect(fila.locator(".barra-dia")).to_have_class(re.compile(r"\bdeficit\b"))
    assert _lado(fila) == "abajo"


# -- R3, R4, R6: spread, apply, back to the plan ---------------------------------------------------------

def test_repartir_aplicar_y_volver(servidor, abrir, capturar):  # R3, R4 · R-20, C-20, G-22
    servidor.semana_de_prueba()
    p = abrir(servidor, "/#balance")
    expect(p.locator("#balance-semana .fila-dia")).to_have_count(7)
    p.locator("#repartir").click()
    propuesta = p.locator("#reparto")
    expect(propuesta).to_be_visible()
    expect(propuesta).to_contain_text(
        "Llevas 750 kcal por encima de tu objetivo esta semana. Para compensarlo: miércoles 21, jueves 22 y "
        "viernes 23 a −22,5 % (1.550 kcal; −250 cada uno). Sábado 24 y domingo 25 no cambian: mantenimiento "
        "por el Triatlón de prueba")
    _sin_scroll(p, "balance-reparto")
    assert _pequenos(p) == []
    capturar(p, "balance-reparto")
    assert servidor.objetivos.del_dia(date(2026, 10, 22)) is None  # nothing saved
    p.locator("#reparto-no").click()
    expect(propuesta).to_be_hidden()
    assert servidor.objetivos.del_dia(date(2026, 10, 21)) is None

    p.locator("#repartir").click()
    p.locator("#reparto-aplicar").click()
    filas = p.locator("#balance-semana .fila-dia")
    for i in (2, 3, 4):
        expect(filas.nth(i)).to_contain_text("−22,5 % · repartido")
    expect(filas.nth(5)).to_contain_text("0 % · carrera")
    p.locator(".pestana", has_text="Hoy").click()
    expect(p.locator("#queda-cifra")).to_contain_text("790")
    expect(p.locator("#nivel-activo")).to_have_text("Nivel −22,5 %")

    p.locator(".pestana", has_text="Balance").click()
    p.locator("#volver-al-plan").click()
    expect(p.locator("#volver-confirmar")).to_contain_text("también quita un % puesto a mano")
    p.locator("#volver-si").click()
    for i in (2, 3, 4):
        expect(filas.nth(i)).to_contain_text("−10 %")
        expect(filas.nth(i)).not_to_contain_text("repartido")
    assert servidor.objetivos.del_dia(date(2026, 10, 22))["porcentaje"] is None


def test_nada_que_repartir(servidor, abrir):  # R6 · R-19
    lunes = date(2026, 10, 19)
    servidor.apuntar("Comida del lunes", lunes, None, 1800, hoy=lunes)  # just on its objective
    servidor.cierres.al_dia(date(2026, 10, 21))
    p = abrir(servidor, "/#balance")
    expect(p.locator("#reparto-motivo")).to_have_text(
        "Vas en tu objetivo o por debajo esta semana: no hay nada que repartir")
    expect(p.locator("#repartir")).to_have_count(0)


def test_sin_datos(servidor, abrir):  # R9
    p = abrir(servidor, "/#balance")
    expect(p.locator("section#balance")).to_contain_text("Aún no hay días con comida para enseñar")
    expect(p.locator("section#balance svg")).to_have_count(0)
    expect(p.locator("#balance-semanas")).to_have_count(0)


# -- R7, R8: the chart, its ranges and the list of weeks ---------------------------------------------------

@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_grafica_y_rangos(servidor, abrir, capturar, oscuro):  # R7
    servidor.historial_de_prueba(date(2026, 6, 1))
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#balance", oscuro=oscuro)
    grafica = p.locator("#balance-grafica svg")
    expect(grafica).to_have_count(1)
    expect(grafica.locator(".barra-kcal")).to_have_count(28)
    expect(grafica.locator(".barra-kcal.provisional")).to_have_count(1)
    expect(grafica.locator("circle.punto-peso")).to_have_count(14)
    expect(grafica.locator("polyline.linea-media")).to_have_count(1)
    rangos = p.locator("#balance-rangos button")
    assert _textos(rangos) == ["4 semanas", "3 meses", "Todo"]
    for i in range(3):
        assert rangos.nth(i).bounding_box()["height"] >= 44
    p.locator("#balance-grafica").scroll_into_view_if_needed()
    capturar(p, "balance-4s" + sufijo)
    rangos.nth(1).click()
    expect(grafica.locator(".barra-kcal")).to_have_count(91)
    _sin_scroll(p, "3 meses")
    rangos.nth(2).click()
    expect(grafica.locator(".barra-kcal")).to_have_count(21)
    expect(grafica.locator("circle.punto-peso")).to_have_count(0)
    expect(grafica.locator("polyline.linea-media")).to_have_count(1)
    expect(rangos.nth(2)).to_have_attribute("aria-pressed", "true")
    _sin_scroll(p, "todo")
    p.locator("#balance-grafica").scroll_into_view_if_needed()
    capturar(p, "balance-todo" + sufijo)


def test_semanas_abrir_una_y_un_dia(servidor, abrir, capturar):  # R8
    servidor.historial_de_prueba(date(2026, 6, 1))
    p = abrir(servidor, "/#balance")
    semanas = p.locator("#balance-semanas .semana")
    expect(semanas).to_have_count(21)
    expect(semanas.first).to_contain_text("Semana del 19 al 25 oct")
    expect(semanas.last).to_contain_text("Semana del 1 al 7 jun")
    doce = semanas.filter(has_text="Semana del 12 al 18 oct")
    expect(doce).to_have_count(1)
    assert re.fullmatch(r"Semana del 12 al 18 oct · [+−][\d.]+ kcal · media [+−][\d.]+/día \([+−][\d,]+ %\) · "
                        r"7 días · \d+,\d kg", _texto(doce)), _texto(doce)
    doce.scroll_into_view_if_needed()
    capturar(p, "balance-semanas")
    assert all(b.bounding_box()["height"] >= 44 for b in semanas.all()[:3])
    doce.click()
    expect(p.locator("#balance-titulo")).to_have_text("Semana del 12 al 18 de octubre")
    expect(p.locator("#repartir")).to_have_count(0)
    expect(p.locator("#volver-a-esta")).to_be_visible()
    # the contract says «su miércoles 15», but 2026-10-15 is a Thursday: its Wednesday is the 14th
    p.locator("#balance-semana .fila-dia", has_text="Miércoles 14").click()
    expect(p.locator("section#hoy")).to_be_visible()
    expect(p.locator("#titulo")).to_have_text("Miércoles 14 de octubre")
    expect(p.locator("#lista-dia")).to_contain_text("total del día (importado)")

    p.locator(".pestana", has_text="Balance").click()
    p.locator("#volver-a-esta").click()
    expect(p.locator("#balance-titulo")).to_have_text("Esta semana · del 19 al 25 de octubre")
