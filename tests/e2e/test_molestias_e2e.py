"""Unit 044 in a real Chromium at 390 × 844, light and dark (E2E-8 of `seguir-molestia`): Fitness › Plan ›
Incidencias shows an incident open first, with its visit, above the closed one of his .md; he closes it by hand with
its lesson and it goes down, closed, with the lesson.

C-189 (and C-190 by hand). Unit 080 (S-5): the incident and its visit are opened by the coach from the PC (there is no
Asistente in the app any more), so here they are written the way the coach's confirmed cards write them
(`Planes.abrir_incidencia`, `Planes.anadir_visita`). The plan is invented.
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from datetime import date, datetime  # noqa: E402

from playwright.sync_api import expect  # noqa: E402

from tridata.plan import Planes  # noqa: E402

PLAN = {
    "nombre": "Road to Ejemplo 26",
    "apartados": [{"titulo": "Objetivos", "parte": "objetivos"}, {"titulo": "Semanas", "parte": "previsto"}],
    "carreras": [{"apartado": 0, "nombre": "Maratón de ejemplo", "fecha": "2026-12-06", "objetivo": "3:05",
                  "estado": "pendiente", "principal": True}],
    "semanas": [{"apartado": 1, "n": 1, "desde": "2026-10-19", "hasta": "2026-10-25", "previsto_s": 14400,
                 "clave": "rodajes", "hecho_plan_s": None, "hecho_plan_km": None}],
    "sesiones": [], "nutricion": [],
    "incidencias": [{"apartado": 1, "fecha": "2026-09-23", "texto": "Rampas inventadas en los gemelos (piscina)"}],
    "notas": [],
}
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])


@pytest.fixture
def plan(servidor):
    """The plan, with the incident and the visit the coach left from the PC (its confirmed cards)."""
    planes = Planes(servidor.db)
    ident = planes.crear(PLAN, datetime(2026, 10, 20, 9, 0))
    incidencia = planes.abrir_incidencia(ident, "tobillo", "derecho, lateral", date(2026, 10, 20),
                                         "molestia inventada en el lateral al acabar el rodaje",
                                         datetime(2026, 10, 20, 19, 0))
    planes.anadir_visita(incidencia, date(2026, 10, 21), "fisio", "sin lesión, alta para correr",
                         datetime(2026, 10, 21, 9, 0))
    return ident


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


@TEMAS
def test_seguir_una_molestia_y_cerrarla(servidor, plan, abrir, capturar, oscuro):  # E2E-8 · C-189, C-190
    tema = "oscuro" if oscuro else "claro"
    p = abrir(servidor, "/#fitness/plan", oscuro=oscuro, esperar=False)
    expect(p.locator("#plan-nombre")).to_have_text("Road to Ejemplo 26")
    boton = p.locator("#plan-incidencias-boton")
    expect(boton).to_contain_text("Incidencias (1)")
    boton.click()
    pantalla = p.locator("#plan-incidencias")
    expect(pantalla).to_be_visible()
    filas = pantalla.locator(".incid")
    expect(filas).to_have_count(2)
    abierta, del_md = filas.nth(0), filas.nth(1)  # C-189: the open one first
    expect(abierta.locator(".estado")).to_have_text("ABIERTA · desde 20/10")
    expect(abierta).to_contain_text("Tobillo derecho, lateral")
    expect(abierta).to_contain_text("21/10 · Fisio: sin lesión, alta para correr")
    expect(abierta).to_contain_text("todo el equipo la tiene en cuenta")
    expect(del_md.locator(".estado")).to_have_text("DEL PLAN · 23/9")  # unit 048 (R5): its state is not known
    expect(del_md).not_to_contain_text("Lección: —")
    _sin_scroll(p, "incidencias")
    capturar(p, f"044-incidencias-{tema}", completa=True)

    abierta.get_by_role("button", name="Cerrar con lección…").click()
    abierta.locator("textarea").fill("bici nunca fuerte el día después de la tirada larga")
    abierta.get_by_role("button", name="Cerrar incidencia").click()
    expect(pantalla.locator(".incid .estado.abierta")).to_have_count(0)
    cerrada = pantalla.locator(".incid").filter(has_text="Tobillo derecho, lateral")
    expect(cerrada.locator(".estado")).to_have_text("CERRADA · 20–21/10")  # C-190: closed today, the 21st
    expect(cerrada).to_contain_text("Lección: bici nunca fuerte el día después de la tirada larga")
    _sin_scroll(p, "cerrada")
    capturar(p, f"044-incidencia-cerrada-{tema}", completa=True)
