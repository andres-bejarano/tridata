"""Unit 042 in a real Chromium at 390 × 844, light and dark (E2E-10 of `mandar-sesion-reloj`): Fitness › Plan › a
week › the session of the Wednesday › «Mandar a mi Garmin» shows the card «Voy a mandar a tu Garmin» with its blocks,
the margin of pace of each one and its day; confirming it, the session says «en tu Garmin». If Garmin fails, the card
says it did not arrive and offers «Bajar fichero» with the same session.

Uses the `conftest.py` of unit 010 with Garmin doubled where the app connects to it (`plan.reloj.cliente_garmin`,
the double of `tests/test_plan_reloj.py`): nothing leaves for Garmin. The plan is invented (C-199, C-200, C-201).
"""
from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.garmin_client import GarminWorkoutError  # noqa: E402
from tridata.plan import Planes, reloj  # noqa: E402

_ruta = Path(__file__).parents[1] / "test_plan_reloj.py"
_spec = importlib.util.spec_from_file_location("doble_de_garmin", _ruta)
assert _spec is not None and _spec.loader is not None
doble = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doble)

TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
PLAN = {
    "nombre": "Road to Ejemplo 26",
    "apartados": [{"titulo": "Objetivos", "parte": "objetivos"}, {"titulo": "Semanas", "parte": "previsto"}],
    "carreras": [{"apartado": 0, "nombre": "Maratón de ejemplo", "fecha": "2026-12-06", "objetivo": "3:05",
                  "estado": "pendiente", "principal": True}],
    "semanas": [
        {"apartado": 1, "n": 1, "desde": "2026-10-19", "hasta": "2026-10-25", "previsto_s": 14400, "clave": "rodajes",
         "hecho_plan_s": None, "hecho_plan_km": None},
        {"apartado": 1, "n": 2, "desde": "2026-10-26", "hasta": "2026-11-01", "previsto_s": 18000, "clave": "series",
         "hecho_plan_s": None, "hecho_plan_km": None},
    ],
    "sesiones": [{"apartado": 1, "fecha": "2026-10-28", "titulo": "Series 4×12'",
                  "bloques": ["20' a 5:45", "4×12' a 4:00 (2' rec)", "10' suave"], "clave": True}],
    "nutricion": [], "incidencias": [], "notas": [],
}


@pytest.fixture
def garmin(servidor, monkeypatch):
    Planes(servidor.db).crear(PLAN, datetime(2026, 10, 20, 9, 0))
    g = doble.GarminDoble()
    monkeypatch.setattr(reloj, "cliente_garmin", lambda entorno=None: g)
    return g


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


def _miercoles(p):
    p.locator("#plan-semanas .semana").filter(has_text="S2").click()
    semana = p.locator("#plan-semana")
    expect(semana).to_be_visible()
    dia = semana.locator(".dia-plan").nth(2)
    expect(dia).to_contain_text("Series 4×12'")
    return dia


@TEMAS
def test_mandar_la_sesion_a_garmin(servidor, abrir, capturar, garmin, oscuro):  # E2E-10 · C-199, C-200
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#fitness/plan", oscuro=oscuro, esperar=False)
    expect(p.locator("#plan-nombre")).to_have_text("Road to Ejemplo 26")
    dia = _miercoles(p)
    dia.get_by_role("button", name="Mandar a mi Garmin").click()

    tarjeta = dia.locator(".tarjeta-reloj")
    expect(tarjeta).to_contain_text("Voy a mandar a tu Garmin: Series 4×12' (mié 28/10)")
    lineas = tarjeta.locator(".lineas li")
    expect(lineas).to_have_count(3)
    expect(lineas.nth(0)).to_have_text("Calentamiento: 20' a 5:40-5:50 /km")
    expect(lineas.nth(1)).to_have_text("4 × 12' a 3:55-4:05 /km, con 2' de recuperación")  # C-199
    expect(lineas.nth(2)).to_have_text("Vuelta a la calma: 10' sin ritmo")
    assert garmin.llamadas == []  # the card is not the sending (C-203)
    _sin_scroll(p, "tarjeta")
    capturar(p, f"reloj-tarjeta{sufijo}", completa=True)

    tarjeta.get_by_role("button", name="Confirmar").click()
    marca = dia.locator(".en-garmin")
    expect(marca).to_contain_text("en tu Garmin")  # C-200
    expect(dia.locator(".tarjeta-reloj")).to_have_count(0)
    assert [e["workoutName"] for e in garmin.del_dia(datetime(2026, 10, 28).date())] == ["Series 4×12'"]
    _sin_scroll(p, "en tu Garmin")
    capturar(p, f"reloj-en-tu-garmin{sufijo}", completa=True)


def test_si_garmin_falla_se_baja_el_fichero(servidor, abrir, capturar, garmin):  # R3 · C-201
    garmin.falla_en, garmin.error = "subir_entreno", GarminWorkoutError("no answer")
    p = abrir(servidor, "/#fitness/plan", esperar=False)
    expect(p.locator("#plan-nombre")).to_have_text("Road to Ejemplo 26")
    dia = _miercoles(p)
    dia.get_by_role("button", name="Mandar a mi Garmin").click()
    tarjeta = dia.locator(".tarjeta-reloj")
    tarjeta.get_by_role("button", name="Confirmar").click()
    expect(tarjeta).to_contain_text("No ha llegado a tu Garmin")
    bajar = tarjeta.get_by_role("link", name="Bajar fichero")
    expect(bajar).to_have_attribute("href", __import__("re").compile(r"^/api/plan/sesion/\d+\.fit$"))
    expect(dia.locator(".en-garmin")).to_have_count(0)
    with p.expect_download() as descarga:
        bajar.click()
    assert descarga.value.suggested_filename.endswith(".fit")
    _sin_scroll(p, "fallo")
    capturar(p, "reloj-fallo-claro", completa=True)


@TEMAS
def test_la_sesion_que_ya_no_esta_en_garmin_lo_dice(servidor, abrir, capturar, garmin, oscuro):  # 061 · R5
    from datetime import date

    from tridata.plan.desde_garmin import cambios

    planes = Planes(servidor.db)
    plan_id = planes.en_curso(date(2026, 10, 21))
    (sesion,) = planes.sesiones_garmin(plan_id, date(2026, 10, 19), date(2026, 12, 6))
    planes.marcar_garmin(sesion["id"], 801, 901, datetime(2026, 10, 20, 9, 0), "Series 4×12': enviada")
    (sesion,) = planes.sesiones_garmin(plan_id, date(2026, 10, 19), date(2026, 12, 6))
    planes.aplicar_garmin(plan_id, cambios([sesion], []))  # removed in Garmin Connect: the plan keeps it

    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#fitness/plan", oscuro=oscuro, esperar=False)
    expect(p.locator("#plan-nombre")).to_have_text("Road to Ejemplo 26")
    dia = _miercoles(p)
    expect(dia.locator(".garmin-aviso")).to_have_text("⚠️ Ya no está en tu Garmin")
    _sin_scroll(p, "aviso")
    capturar(p, f"reloj-ya-no-esta{sufijo}", completa=True)
