"""Unit 040 in a real Chromium at 390 × 844, light and dark (E2E-6 of `llevar-plan`): Fitness › Plan without a plan
says «Importa tu plan»; importing the invented .md shows its parts and its difference with Garmin proposing the
plan's; confirming shows the plan with its races and its weeks (today's marked); a week shows its days (a past one
without anything, «no hecho») and a done session its laps. Fitness › Forma stays as it was.

Uses the `conftest.py` of unit 010; the AI is the double of `tests/test_plan_importar.py` (never the API) and the
Garmin data its `sembrar_garmin`. Every value is invented (C-169, C-170, C-171, C-172, C-173).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.ia.plan import OrdenadorPlan  # noqa: E402

_ruta = Path(__file__).parents[1] / "test_plan_importar.py"
_spec = importlib.util.spec_from_file_location("doble_del_plan", _ruta)
assert _spec is not None and _spec.loader is not None
doble = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doble)

TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])


@pytest.fixture
def ia_doblada(monkeypatch):
    llamadas = []

    def ordenar(self, titulo, texto, hoy):
        llamadas.append(titulo)
        return doble.ordenar_como_la_ia(titulo, texto), dict(doble.USO), "claude-haiku-5-5"

    monkeypatch.setattr(OrdenadorPlan, "ordenar", ordenar)
    return llamadas


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


@TEMAS
def test_recorrido_del_plan(servidor, abrir, ir_a, capturar, ia_doblada, oscuro):  # E2E-6 · C-169, C-170, C-173
    doble.sembrar_garmin(servidor.store)
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ir_a(p, "fitness")
    partes = p.locator("#fitness-partes")
    expect(partes).to_be_visible()
    expect(p.locator("#fitness-carga")).to_be_visible()  # Forma first, as always
    partes.locator("a", has_text="Plan").click()
    plan = p.locator("#fitness-plan")
    expect(plan).to_be_visible()
    expect(p.locator("#fitness-carga")).to_be_hidden()
    expect(plan).to_contain_text("Importa tu plan")
    _sin_scroll(p, "plan-vacio")

    # import: the parts read and the difference with Garmin, proposing the plan's
    p.locator("#plan-fichero").set_input_files(str(doble.FIXTURE))
    importacion = p.locator("#plan-importar")
    expect(importacion).to_be_visible()
    expect(importacion).to_contain_text("plan-inventado.md")
    expect(importacion).to_contain_text("4 carreras")
    expect(importacion).to_contain_text("1 diferencia con Garmin")
    diferencia = importacion.locator(".diferencia")
    expect(diferencia).to_have_count(1)
    expect(diferencia).to_contain_text("S4")
    expect(diferencia).to_contain_text("3h16 · 39,1 km")
    expect(diferencia).to_contain_text("3h22 · 39,1 km")
    expect(diferencia.get_by_role("button", name="Lo del plan")).to_have_attribute("aria-pressed", "true")
    expect(importacion).to_contain_text("Hasta que confirmes no se guarda nada")
    assert len(ia_doblada) == 9
    _sin_scroll(p, "importar")
    capturar(p, f"plan-importar{sufijo}", completa=True)
    importacion.get_by_role("button", name="Confirmar importación").click()

    # the plan: its races (the 10K with its result) and its weeks, today's marked
    expect(p.locator("#plan-nombre")).to_have_text("Road to Ejemplo 26")
    carreras = p.locator("#plan-carreras li")
    expect(carreras).to_have_count(4)
    expect(carreras.first).to_contain_text("10K de ejemplo")
    expect(carreras.first).to_contain_text("✅ 38:10")
    semanas = p.locator("#plan-semanas .semana")
    expect(semanas).to_have_count(11)
    hoy = p.locator("#plan-semanas .semana.hoy")
    expect(hoy).to_have_count(1)
    expect(hoy).to_contain_text("S9")
    expect(hoy).to_contain_text("hoy")
    expect(hoy).to_contain_text("48'")  # done up to today in moving time: the run of today, 2900 s
    expect(hoy).to_contain_text("9,0 km")
    expect(p.locator("#plan-bajar")).to_have_attribute("href", "/api/plan/1.md")
    _sin_scroll(p, "plan")
    capturar(p, f"plan{sufijo}", completa=True)

    # a week: its days with the session and what was done; a past day without anything, «no hecho»
    semanas.filter(has_text="S8").click()
    semana = p.locator("#plan-semana")
    expect(semana).to_be_visible()
    expect(semana.locator("h2")).to_contain_text("S8 · 12–18/10")
    dias = semana.locator(".dia-plan")
    expect(dias).to_have_count(7)
    expect(dias.nth(1)).to_contain_text("40' suaves")
    expect(dias.nth(1)).to_contain_text("no hecho")
    expect(dias.nth(2)).to_contain_text("Series 4×1000")
    expect(dias.nth(2)).to_contain_text("4×1000 a 4:00 (2' rec)")
    _sin_scroll(p, "semana")
    capturar(p, f"plan-semana{sufijo}", completa=True)

    # a done session: its laps with pace, HR and cadence
    dias.nth(2).locator("button.hecha").click()
    vueltas = semana.locator(".vuelta")
    expect(vueltas).to_have_count(12)
    expect(vueltas.first).to_contain_text("5:10 /km")
    expect(vueltas.first).to_contain_text("141")
    expect(vueltas.first).to_contain_text("171 ppm")
    _sin_scroll(p, "sesion")
    capturar(p, f"plan-sesion{sufijo}", completa=True)

    # back to the plan, and Forma as it was
    semana.get_by_role("button", name="Volver al plan").click()
    expect(p.locator("#plan-vista")).to_be_visible()
    partes.locator("a", has_text="Forma").click()
    expect(p.locator("#fitness-carga")).to_be_visible()
    expect(plan).to_be_hidden()


def test_si_la_ia_falla_se_puede_reintentar(servidor, abrir, ir_a, monkeypatch):  # R5
    fallos = {"quedan": 1}

    def ordenar(self, titulo, texto, hoy):
        if fallos["quedan"]:
            fallos["quedan"] -= 1
            from tridata.ia.plan import ErrorPlan
            raise ErrorPlan("no hay conexión con la API")
        return doble.ordenar_como_la_ia(titulo, texto), dict(doble.USO), "claude-haiku-5-5"

    monkeypatch.setattr(OrdenadorPlan, "ordenar", ordenar)
    p = abrir(servidor, "/#fitness/plan", esperar=False)
    plan = p.locator("#fitness-plan")
    expect(plan).to_contain_text("Importa tu plan")
    p.locator("#plan-fichero").set_input_files(str(doble.FIXTURE))
    aviso = p.locator("#plan-aviso")
    expect(aviso).to_contain_text("Inténtalo de nuevo")
    expect(plan).to_contain_text("Importa tu plan")  # nothing saved
    p.get_by_role("button", name="Reintentar").click()
    expect(p.locator("#plan-importar")).to_be_visible()


@TEMAS
def test_el_plan_a_mano(servidor, abrir, capturar, oscuro):  # unit 067 · REC-30, C-247, C-249, C-250, C-253
    """A new plan built by hand on the screen: two weeks, the phase «Base» over them, a session added on Friday,
    changed, moved to Saturday and deleted (confirming); nothing goes through the team."""
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#fitness/plan", oscuro=oscuro, esperar=False)
    vista = p.locator("#plan-vista")
    expect(vista).to_contain_text("Importa tu plan")
    p.locator("#plan-nuevo-boton").click()
    nuevo = p.locator("#plan-nuevo-form")
    nuevo.locator("input[name=plan-nombre]").fill("Prueba a mano 27")
    nuevo.locator("input[name=carrera-nombre]").fill("Media de prueba")
    nuevo.locator("input[name=carrera-fecha]").fill("2027-03-07")
    nuevo.get_by_role("button", name="Crear plan").click()
    expect(p.locator("#plan-nombre")).to_have_text("Prueba a mano 27")
    expect(vista).to_contain_text("Este plan no tiene semanas.")

    # two weeks, the second one with the dates it proposes (the day after the first)
    p.locator("#plan-anadir-semana").click()
    semana = p.locator("#plan-anadir-semana-form")
    semana.locator("input[name=semana-desde]").fill("2026-10-19")
    semana.locator("input[name=semana-desde]").dispatch_event("change")
    expect(semana.locator("input[name=semana-hasta]")).to_have_value("2026-10-25")
    semana.locator("input[name=semana-previsto]").fill("4:30")
    semana.get_by_role("button", name="Añadir semana").click()
    expect(p.locator("#plan-semanas .semana")).to_have_count(1)
    p.locator("#plan-anadir-semana").click()
    expect(p.locator("#plan-anadir-semana-form input[name=semana-n]")).to_have_value("2")
    expect(p.locator("#plan-anadir-semana-form input[name=semana-desde]")).to_have_value("2026-10-26")
    p.locator("#plan-anadir-semana-form").get_by_role("button", name="Añadir semana").click()
    expect(p.locator("#plan-semanas .semana")).to_have_count(2)

    # the phase «Base» over both
    p.locator("#plan-anadir-fase").click()
    fase = p.locator("#plan-anadir-fase-form")
    fase.locator("input[name=fase-nombre]").fill("Base")
    fase.locator("input[name=fase-hasta]").fill("2")
    fase.get_by_role("button", name="Añadir fase").click()
    expect(p.locator("#plan-semanas .fase-plan")).to_have_count(1)
    expect(p.locator("#plan-semanas .fase-plan")).to_contain_text("Base · S1–S2")
    _sin_scroll(p, "plan-a-mano")

    # a session on Friday 23/10, at once
    p.locator("#plan-semanas .semana").filter(has_text="S1").click()
    s1 = p.locator("#plan-semana")
    expect(s1.locator("h2")).to_contain_text("S1 · 19–25/10 · Base")
    dias = s1.locator(".dia-plan")
    expect(dias).to_have_count(7)
    expect(dias.nth(0).locator(".anadir-sesion")).to_have_count(0)  # Monday is over: no «Añadir sesión»
    viernes = dias.nth(4)
    viernes.locator(".anadir-sesion").click()
    viernes.locator("input[name=sesion-titulo]").fill("Rodaje")
    viernes.locator("textarea[name=sesion-bloques]").fill("45' a 5:30")
    viernes.get_by_role("button", name="Guardar sesión").click()
    expect(dias.nth(4)).to_contain_text("Vie 23/10 · Rodaje")
    expect(dias.nth(4)).to_contain_text("45' a 5:30")

    # changed to 50' and moved to Saturday: the same session, the Friday free
    dias.nth(4).locator(".cambiar-sesion").click()
    dias.nth(4).locator(".form-cambiar-sesion textarea").fill("50' a 5:30")
    dias.nth(4).get_by_role("button", name="Guardar cambios").click()
    expect(dias.nth(4)).to_contain_text("50' a 5:30")
    dias.nth(4).locator(".mover-sesion").click()
    dias.nth(4).locator(".form-mover-sesion input[name=sesion-dia]").fill("2026-10-24")
    dias.nth(4).locator(".form-mover-sesion button[type=submit]").click()
    expect(dias.nth(5)).to_contain_text("Sáb 24/10 · Rodaje")
    expect(dias.nth(5)).to_contain_text("50' a 5:30")
    expect(dias.nth(4)).to_contain_text("sin sesión prevista")
    _sin_scroll(p, "semana-a-mano")
    capturar(p, f"plan-a-mano-semana{sufijo}", completa=True)

    # deleted: cancelling keeps it, confirming takes it out
    p.once("dialog", lambda d: d.dismiss())
    dias.nth(5).locator(".borrar-sesion").click()
    expect(dias.nth(5)).to_contain_text("Rodaje")
    p.once("dialog", lambda d: d.accept())
    dias.nth(5).locator(".borrar-sesion").click()
    expect(dias.nth(5)).to_contain_text("sin sesión prevista")

    s1.get_by_role("button", name="Volver al plan").click()
    expect(p.locator("#plan-semanas .fase-plan")).to_contain_text("Base")
    capturar(p, f"plan-a-mano{sufijo}", completa=True)
