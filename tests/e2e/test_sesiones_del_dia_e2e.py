"""Unit 048 in a real Chromium at 390 × 844, light and dark: a day of the plan with two sessions shows one row per
session, each with its title, ONLY its blocks and its «Mandar a mi Garmin», whose card is the one of THAT session;
confirmed, «⌚ en tu Garmin» is in the row of that session (R1, R2). In a conversation, the card «Mandar a tu
Garmin» whose confirmation fails shows the error and «Bajar fichero» of that session (R7).

Garmin is doubled where the app connects to it (`plan.reloj.cliente_garmin`, the double of `tests/test_plan_reloj.py`)
and Claude is doubled as in `test_molestias_e2e.py`: nothing leaves this PC. The plan is invented.
"""
from __future__ import annotations

import importlib.util
import re
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402
from test_conversaciones_e2e import ServidorAsistente, _Flujo  # noqa: E402

from tridata.garmin_client import GarminWorkoutError  # noqa: E402
from tridata.plan import Planes, reloj  # noqa: E402

_ruta = Path(__file__).parents[1] / "test_plan_reloj.py"
_spec = importlib.util.spec_from_file_location("doble_de_garmin_048", _ruta)
assert _spec is not None and _spec.loader is not None
doble = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doble)

TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
RODAJE = {"apartado": 1, "fecha": "2026-10-28", "titulo": "Rodaje 40'", "bloques": ["40' a 5:40"], "clave": False}
SERIES = {"apartado": 1, "fecha": "2026-10-28", "titulo": "Series 4×12'",
          "bloques": ["20' a 5:45", "4×12' a 4:00 (2' rec)", "10' suave"], "clave": True}
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
    "sesiones": [RODAJE, SERIES,
                 {"apartado": 1, "fecha": "2026-10-30", "titulo": "Tirada 1h", "bloques": ["1h a 5:35"],
                  "clave": False}],
    "nutricion": [], "incidencias": [], "notas": [],
}


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


@pytest.fixture
def garmin(servidor, monkeypatch):
    Planes(servidor.db).crear(PLAN, datetime(2026, 10, 20, 9, 0))
    g = doble.GarminDoble()
    monkeypatch.setattr(reloj, "cliente_garmin", lambda entorno=None: g)
    return g


def _miercoles(p):
    p.locator("#plan-semanas .semana").filter(has_text="S2").click()
    semana = p.locator("#plan-semana")
    expect(semana).to_be_visible()
    dia = semana.locator(".dia-plan").nth(2)
    expect(dia).to_contain_text("Mié 28/10")
    return dia


@TEMAS
def test_un_dia_con_dos_sesiones_una_fila_por_sesion(servidor, abrir, capturar, garmin, oscuro):  # R1, R2
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#fitness/plan", oscuro=oscuro, esperar=False)
    expect(p.locator("#plan-nombre")).to_have_text("Road to Ejemplo 26")
    dia = _miercoles(p)
    expect(dia).not_to_contain_text(" + ")  # R1: no title joined with « + »
    filas = dia.locator(".sesion-plan")
    expect(filas).to_have_count(2)
    rodaje, series = filas.nth(0), filas.nth(1)
    expect(rodaje.locator(".titulo-sesion")).to_have_text("Rodaje 40'")
    expect(rodaje.locator(".bloques li")).to_have_text(["40' a 5:40"])  # only its blocks
    expect(series.locator(".titulo-sesion")).to_have_text("Series 4×12'")
    expect(series.locator(".bloques li")).to_have_text(["20' a 5:45", "4×12' a 4:00 (2' rec)", "10' suave"])
    expect(rodaje.get_by_role("button", name="Mandar a mi Garmin")).to_have_count(1)  # R2: one each
    expect(series.get_by_role("button", name="Mandar a mi Garmin")).to_have_count(1)
    _sin_scroll(p, "dos sesiones")
    capturar(p, f"048-dia-dos-sesiones{sufijo}", completa=True)

    # a day with one session is painted as before: its title in the head of the day, no rows
    viernes = p.locator("#plan-semana .dia-plan").nth(4)
    expect(viernes).to_contain_text("Vie 30/10 · Tirada 1h")
    expect(viernes.locator(".sesion-plan")).to_have_count(0)

    series.get_by_role("button", name="Mandar a mi Garmin").click()
    tarjeta = series.locator(".tarjeta-reloj")
    expect(tarjeta).to_contain_text("Voy a mandar a tu Garmin: Series 4×12' (mié 28/10)")  # the card of THAT one
    expect(tarjeta.locator(".lineas li")).to_have_count(3)
    expect(rodaje.locator(".tarjeta-reloj")).to_have_count(0)
    capturar(p, f"048-tarjeta-de-la-segunda{sufijo}", completa=True)
    tarjeta.get_by_role("button", name="Confirmar").click()

    dia = _dia_repintado(p)
    rodaje, series = dia.locator(".sesion-plan").nth(0), dia.locator(".sesion-plan").nth(1)
    expect(series.locator(".en-garmin")).to_contain_text("en tu Garmin")  # R2: in the row of the one sent
    expect(rodaje.locator(".en-garmin")).to_have_count(0)
    expect(rodaje.get_by_role("button", name="Mandar a mi Garmin")).to_have_count(1)
    assert [e["workoutName"] for e in garmin.del_dia(date(2026, 10, 28))] == ["Series 4×12'"]
    _sin_scroll(p, "en tu Garmin")
    capturar(p, f"048-en-tu-garmin-la-segunda{sufijo}", completa=True)


def _dia_repintado(p):
    dia = p.locator("#plan-semana .dia-plan").nth(2)
    expect(dia.locator(".en-garmin")).to_have_count(1)
    return dia


# -- R7: «Bajar fichero» in the card of a conversation --------------------------------------------------------------

def _uso():
    return SimpleNamespace(input_tokens=30, output_tokens=60, cache_creation_input_tokens=0, cache_read_input_tokens=0)


def _entrenador(**peticion):
    """Asks for the card «Mandar a tu Garmin» of the series; with its result, answers in words."""
    ultimo = peticion["messages"][-1]["content"]
    if isinstance(ultimo, list):
        texto = "Te la dejo lista: confírmala."
        return _Flujo([texto], SimpleNamespace(content=[SimpleNamespace(type="text", text=texto)],
                                               stop_reason="end_turn", usage=_uso()))
    return _Flujo([], SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="t1", name="proponer_cambio",
                                 input={"accion": "mandar_a_garmin", "dia": "2026-10-28", "objeto": "series"})],
        stop_reason="tool_use", usage=_uso()))


@pytest.fixture
def asistente(tmp_path, monkeypatch):
    carpeta = tmp_path / "sesiones"
    s = ServidorAsistente(carpeta)
    s.claude.stream = _entrenador
    Planes(carpeta / "data.sqlite3").crear(PLAN, datetime(2026, 10, 20, 9, 0))
    g = doble.GarminDoble()
    g.falla_en, g.error = "subir_entreno", GarminWorkoutError("no answer")
    monkeypatch.setattr(reloj, "cliente_garmin", lambda entorno=None: g)
    s.garmin, s.base = g, carpeta / "data.sqlite3"
    yield s
    s.parar()


def test_en_una_conversacion_si_garmin_falla_se_baja_el_fichero(asistente, abrir, capturar):  # R7 · R-159
    p = abrir(asistente, "/#asistente", esperar=False)
    p.locator("#conv-nueva").click()
    p.locator('#conv-papeles button[data-papel="entrenador"]').click()
    p.locator("#texto").fill("mándame las series del miércoles al reloj")
    p.locator("#enviar").click()
    tarjeta = p.locator("#conv-mensajes .tarjeta-cambio").last
    expect(tarjeta).to_contain_text("Voy a mandar a tu Garmin: Series 4×12' (mié 28/10)")
    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(tarjeta).to_contain_text("No ha llegado a tu Garmin")
    bajar = tarjeta.get_by_role("link", name="Bajar fichero")
    expect(bajar).to_have_attribute("href", re.compile(r"^/api/plan/sesion/\d+\.fit$"))
    ident = int(re.search(r"(\d+)\.fit$", bajar.get_attribute("href")).group(1))
    assert Planes(asistente.base).sesion(ident)["titulo"] == "Series 4×12'"  # the session of the card
    with p.expect_download() as descarga:
        bajar.click()
    assert descarga.value.suggested_filename.endswith(".fit")
    _sin_scroll(p, "fallo en la conversación")
    capturar(p, "048-conversacion-bajar-fichero-claro", completa=True)
