"""E2E of unit 115 (R5): the planned km of a week in Fitness › Plan — seen in the line of the week next to the
planned time, set when a week is added and changed from «Cambiar semana». A week of before has no km and shows none.
Every value is invented; nothing talks to Garmin nor to a paid service.
"""
from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.plan import Planes  # noqa: E402

TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])


def _plan(servidor) -> int:
    """A plan of two weeks: S1 without km (as every week of before), S2 with 40 km."""
    return Planes(servidor.db).crear({
        "nombre": "Plan de km inventado",
        "apartados": [{"titulo": "Objetivos", "parte": "objetivos"}, {"titulo": "Previsto", "parte": "previsto"}],
        "carreras": [{"apartado": 0, "nombre": "Media inventada", "fecha": "2027-03-07", "objetivo": "1:30",
                      "estado": "pendiente", "principal": True}],
        "semanas": [{"apartado": 1, "n": 1, "desde": "2026-10-19", "hasta": "2026-10-25", "previsto_s": 16200,
                     "clave": "tirada", "hecho_plan_s": None, "hecho_plan_km": None},
                    {"apartado": 1, "n": 2, "desde": "2026-10-26", "hasta": "2026-11-01", "previsto_s": 18000,
                     "clave": "series", "hecho_plan_s": None, "hecho_plan_km": None, "previsto_km": 40.0}],
        "sesiones": [], "nutricion": [], "incidencias": [], "notas": []}, datetime(2026, 10, 21, 9, 0))


@TEMAS
def test_los_km_de_la_semana(servidor, abrir, capturar, oscuro):  # R5
    sufijo = "-oscuro" if oscuro else "-claro"
    _plan(servidor)
    p = abrir(servidor, "/#fitness/plan", oscuro=oscuro, esperar=False)
    semanas = p.locator("#plan-semanas .semana")
    expect(semanas).to_have_count(2)
    expect(semanas.nth(1)).to_contain_text("prev. ~5h00 · 40 km · series")
    expect(semanas.nth(0)).to_contain_text("prev. ~4h30 · tirada")
    expect(semanas.nth(0)).not_to_contain_text(" km ·")

    # a week added with its km
    p.locator("#plan-anadir-semana").click()
    form = p.locator("#plan-anadir-semana-form")
    form.locator("input[name=semana-previsto]").fill("5:30")
    form.locator("input[name=semana-km]").fill("45,5")
    form.get_by_role("button", name="Añadir semana").click()
    expect(semanas).to_have_count(3)
    expect(semanas.nth(2)).to_contain_text("prev. ~5h30 · 45,5 km")
    capturar(p, f"plan-semana-km{sufijo}", completa=True)

    # changed from «Cambiar semana»: its form brings the km it has
    semanas.nth(1).click()
    p.locator("#plan-cambiar-semana").click()
    km = p.locator("#plan-cambiar-semana-form input[name=semana-km]")
    expect(km).to_have_value("40")
    km.fill("48")
    p.locator("#plan-cambiar-semana-form").get_by_role("button", name="Guardar semana").click()
    expect(p.locator("#plan-semana")).to_contain_text("48 km")
    capturar(p, f"plan-semana-km-cambiada{sufijo}", completa=True)
    p.locator("#plan-semana").get_by_role("button", name="Volver al plan").click()
    expect(semanas.nth(1)).to_contain_text("prev. ~5h00 · 48 km · series")
