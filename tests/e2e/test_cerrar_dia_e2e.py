"""Unit 014 in a real Chromium at 390 × 844, light and dark: «Cerrar el día», yesterday closed with Garmin,
a day waiting for Garmin, the warning of a correction with «Entendido» and the calibration in the breakdown.

Uses the `conftest.py` of unit 010 (temporary SQLite, Claude and Garmin are `MagicMock`s); the Garmin rows
are written straight into the temporary `DataStore`. Invented data only.
Plans: `cerrar-dia` R-24..R-28 (C-24, C-25, C-26, C-27, C-28), G-25, states of «Día de comida».
R1, R3, R4, R5, R6, R8 of the unit.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.models import Activity, DailyStats  # noqa: E402

HOY = date(2026, 10, 21)  # the same fixed «today» as conftest.py (a Wednesday)

ANCHO = 390
MARTES = HOY - timedelta(days=1)
LUNES = HOY - timedelta(days=2)
TEXTO_ESPERA = "Garmin aún no tiene el día completo: sigue con el gasto estimado"
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])


def _garmin(servidor, dia, total, pasos=10000, actividades=()):
    servidor.store.save_daily_stats(DailyStats(stat_date=dia, steps=pasos, total_calories=total))
    if actividades:
        servidor.store.save_activities(list(actividades))


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= ANCHO, f"{pantalla}: horizontal scroll ({ancho}px)"


def _alto_minimo(locator, pantalla: str) -> None:
    caja = locator.bounding_box()
    assert caja is not None and caja["height"] >= 44, f"{pantalla}: touch target under 44 px"


@TEMAS
def test_cerrar_el_dia_de_hoy(servidor, abrir, capturar, oscuro):  # R1, R8 · R-24, C-24
    servidor.apuntar("Comida", LUNES, 1, 2000, hoy=LUNES, unidad="ración")
    _garmin(servidor, LUNES, 2500)  # Monday closes with −500 on 2,500
    servidor.apuntar("Comida", HOY, 1, 1400, unidad="ración")
    servidor.apuntar("Cena", HOY, 1, 600, hoy=MARTES, unidad="ración")  # planned yesterday for today
    _garmin(servidor, HOY, None, pasos=4000, actividades=[Activity(
        activity_id="e2e-hoy", activity_date=HOY, activity_type="lap_swimming", name="Natación",
        duration_seconds=2700, calories=300)])
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, oscuro=oscuro)
    boton = p.get_by_role("button", name="Cerrar el día")
    expect(boton).to_be_visible()
    expect(p.locator("#cierre-estado")).to_have_text("Abierto")
    _alto_minimo(boton, "cerrar")

    boton.click()

    expect(p.locator("#cierre-estado")).to_have_text("Comida cerrada · gasto estimado")
    expect(p.locator("#total")).to_have_text("2.000 kcal comidas")
    expect(p.locator("#cierre-balance")).to_have_text("balance provisional −300 kcal (−13 %), con el gasto estimado")
    expect(p.locator("#cierre-semana")).to_have_text("media de la semana −400 kcal/día (−16,7 %), 2 días")
    expect(boton).to_have_count(0)
    expect(p.locator(".fila[data-grupo='planeado']")).to_have_count(0)  # the dinner is eaten now
    _sin_scroll(p, "cerrado" + sufijo)
    p.locator("#cierre").scroll_into_view_if_needed()
    capturar(p, f"cierre-hoy{sufijo}")


@TEMAS
def test_ayer_cerrado_con_garmin(servidor, abrir, capturar, oscuro):  # R3, R8 · R-25, C-25
    servidor.apuntar("Comida", MARTES, 1, 2000, hoy=MARTES, unidad="ración")
    _garmin(servidor, MARTES, 2550, pasos=15100, actividades=[Activity(
        activity_id="e2e-carrera", activity_date=MARTES, activity_type="running", name="Carrera",
        duration_seconds=1800, calories=400, avg_cadence=170)])
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, oscuro=oscuro)
    expect(p.locator("#titulo")).to_have_text("Hoy")

    p.locator("#anterior").click()

    expect(p.locator("#titulo")).to_have_text("Ayer")
    expect(p.locator("#cierre-estado")).to_have_text("Cerrado con Garmin: gasto real 2.550 (estimado 2.400)")
    expect(p.locator("#cierre-balance")).to_contain_text("balance −550 kcal (−21,6 %)")
    expect(p.get_by_role("button", name="Cerrar el día")).to_have_count(0)
    expect(p.locator("#ajustar-resumen")).to_contain_text("gasto 2.550")
    _sin_scroll(p, "garmin" + sufijo)
    p.locator("#cierre").scroll_into_view_if_needed()
    capturar(p, f"cierre-garmin{sufijo}")

    p.locator("#siguiente").click()
    p.locator("#siguiente").click()  # tomorrow: a future day has no close
    expect(p.locator("#titulo")).to_have_text("Mañana")
    expect(p.get_by_role("button", name="Cerrar el día")).to_have_count(0)


@TEMAS
def test_dia_que_espera_a_garmin(servidor, abrir, capturar, oscuro):  # R4, R8 · R-26, C-26
    servidor.apuntar("Comida", MARTES, 1, 2000, hoy=MARTES, unidad="ración")
    _garmin(servidor, MARTES, 1450)  # below the basal: not the whole day yet
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, oscuro=oscuro)
    expect(p.locator("#titulo")).to_have_text("Hoy")

    p.locator("#anterior").click()

    expect(p.locator("#cierre-estado")).to_have_text(TEXTO_ESPERA)
    expect(p.locator("#cierre-balance")).to_contain_text("balance provisional")
    _sin_scroll(p, "espera" + sufijo)
    p.locator("#cierre").scroll_into_view_if_needed()
    capturar(p, f"cierre-espera{sufijo}")


@TEMAS
def test_aviso_de_correccion_con_entendido(servidor, abrir, capturar, oscuro):  # R5 · R-27, C-27, G-25
    catorce = date(2026, 10, 14)
    servidor.apuntar("Comida", catorce, 1, 2000, hoy=catorce, unidad="ración")
    _garmin(servidor, catorce, 2500)
    servidor.cierres.al_dia(HOY)  # closed with Garmin some days ago
    _garmin(servidor, catorce, 2800)  # the weekly sweep corrects it
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, oscuro=oscuro)
    aviso = p.locator(".aviso-cierre")
    expect(aviso).to_have_count(1)
    expect(aviso).to_contain_text("Garmin ha corregido el gasto del 2026-10-14: 2.500 → 2.800 kcal; balance −500 → −800")
    entendido = aviso.get_by_role("button", name="Entendido")
    _alto_minimo(entendido, "aviso")
    _sin_scroll(p, "aviso" + sufijo)
    aviso.scroll_into_view_if_needed()
    capturar(p, f"cierre-aviso{sufijo}")

    entendido.click()

    expect(p.locator(".aviso-cierre")).to_have_count(0)
    p.reload()
    expect(p.locator("#titulo")).to_have_text("Hoy")
    expect(p.locator(".aviso-cierre")).to_have_count(0)


@TEMAS
def test_desglose_con_la_calibracion(servidor, abrir, capturar, oscuro):  # R6 · R-28, C-28
    for i, dia in enumerate((date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15))):
        servidor.apuntar("Comida", dia, 1, 2000, hoy=dia, unidad="ración")
        _garmin(servidor, dia, 2450, actividades=[Activity(
            activity_id=f"e2e-n{i}", activity_date=dia, activity_type="lap_swimming", name="Natación",
            duration_seconds=2700, calories=300)])
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, oscuro=oscuro)
    p.locator("#abrir-hoja").click()

    expect(p.locator("#desglose")).to_contain_text("calibrado con Garmin el 2026-10-21")
    expect(p.locator("#gasto-total")).to_have_text("2.150 kcal")
    _sin_scroll(p, "calibracion" + sufijo)
    p.locator("#desglose").scroll_into_view_if_needed()
    capturar(p, f"cierre-calibracion{sufijo}")
