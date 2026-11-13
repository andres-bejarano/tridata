"""Unit 080 in a real Chromium at 390 × 844, light and dark: what the app without the paid AI adds.

R8 (R-223, C-276, C-277): where the weights are (Nutrición › Balance), «Cambiar» changes the weight of today or of
another day already written down; the 7-day mean is painted again recomputed; «abc» is not saved and the app says
how it goes. R9 (R-224, C-278, C-279): Ajustes shows «Lo que ha hecho el coach», the newest first, only to look at;
without any, it says so. R1, R2, R5 seen together: four tabs, Hoy with three cards and no box, Ajustes without the
team nor the cap.

Uses the `conftest.py` of unit 010 (temporary SQLite, no AI). Every value is invented.
"""
from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.conversaciones import Conversaciones  # noqa: E402
from tridata.conversaciones.desde_pc import conversacion_pc  # noqa: E402

TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


def _pequenos(p) -> list[str]:
    return p.evaluate("""() => [...document.querySelectorAll('button, a[href], input, .ajuste')]
      .filter((e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !e.closest('[hidden]'); })
      .filter((e) => e.getBoundingClientRect().height < 44)
      .map((e) => `${e.tagName}#${e.id}.${e.className} ${e.getBoundingClientRect().height}`)""")


def _cambio_del_coach(db, cuando: datetime, papel: str, texto: str) -> None:
    conversaciones = Conversaciones(db)
    conv = conversacion_pc(conversaciones, cuando)
    conversaciones.anadir_mensaje(conv["id"], "asistente", texto, cuando, tarjetas=[], papel=papel)


# -- R1, R2: four tabs, Hoy with three cards and no box ---------------------------------------------------------------

@TEMAS
def test_cuatro_pestanas_y_hoy_sin_caja(servidor, abrir, capturar, oscuro):  # R1, R2 · S-23, S-27
    servidor.dia_de_prueba()
    p = abrir(servidor, "/", oscuro=oscuro, esperar=False)
    expect(p.locator("section#hoy")).to_be_visible()
    assert [t.strip() for t in p.locator("nav.pestanas .pestana").all_inner_texts()] == [
        "Hoy", "Nutrición", "Fitness", "Ajustes"]
    expect(p.locator("section#hoy .tarjeta-hoy")).to_have_count(3)
    expect(p.locator("#hoy-asistente")).to_have_count(0)
    expect(p.locator("#texto")).to_have_count(0)
    expect(p.locator("#grabar")).to_have_count(0)
    _sin_scroll(p, "hoy")
    capturar(p, f"080-hoy-{'oscuro' if oscuro else 'claro'}", completa=True)


# -- R8 · C-276, C-277: change a weight already written down ----------------------------------------------------------

@TEMAS
def test_cambiar_el_peso_de_hoy(servidor, abrir, capturar, oscuro):  # R8 · C-276
    servidor.pesos_de_prueba()  # 72,4 today, 72,6 yesterday: mean 72,5
    tema = "oscuro" if oscuro else "claro"
    p = abrir(servidor, "/#balance", oscuro=oscuro)
    expect(p.locator("#peso-cifra")).to_contain_text("72,4")
    expect(p.locator("#peso-media")).to_contain_text("72,5 kg")
    p.locator("#peso-cambiar").click()
    caja = p.locator("#peso-hoy .editor-peso")
    expect(caja).to_be_visible()
    entrada = caja.get_by_label("Peso del 21 de octubre (kg)")
    expect(entrada).to_have_value("72,4")
    assert _pequenos(p) == []
    _sin_scroll(p, "cambiar peso")
    capturar(p, f"080-peso-cambiar-{tema}")

    # C-277: «abc» is not saved and the app says how it goes
    entrada.fill("abc")
    caja.get_by_role("button", name="Guardar").click()
    expect(caja.locator(".aviso")).to_contain_text("No se ha cambiado nada")
    capturar(p, f"080-peso-no-vale-{tema}")
    assert p.request.get(servidor.url + "/api/peso").json()["pesos"][-1]["kg"] == 72.4

    entrada.fill("72,0")
    caja.get_by_role("button", name="Guardar").click()
    expect(p.locator("#peso-hoy .editor-peso")).to_have_count(0)
    expect(p.locator("#peso-cifra")).to_contain_text("72,0")
    expect(p.locator("#peso-media")).to_contain_text("72,3 kg")  # (72,6 + 72,0) / 2, recomputed
    capturar(p, f"080-peso-cambiado-{tema}")
    assert [x["kg"] for x in p.request.get(servidor.url + "/api/peso").json()["pesos"]][-2:] == [72.6, 72.0]


def test_cambiar_el_peso_de_otro_dia(servidor, abrir, capturar):  # R8: one of «Últimos pesos»
    servidor.pesos_de_prueba()
    p = abrir(servidor, "/#balance")
    p.locator("#balance-ultimos-pesos summary").click()
    ayer = p.locator("#peso-ultimos .fila-peso", has_text="Ayer")
    ayer.get_by_role("button", name="Cambiar").click()
    caja = ayer.locator(".editor-peso")
    caja.get_by_label("Peso del 20 de octubre (kg)").fill("72,8")
    caja.get_by_role("button", name="Guardar").click()
    expect(p.locator("#peso-ultimos .fila-peso", has_text="Ayer").locator(".kcal")).to_have_text("72,8 kg")
    expect(p.locator("#peso-cifra")).to_contain_text("72,4")  # today's stays
    expect(p.locator("#peso-media")).to_contain_text("72,6 kg")  # (72,8 + 72,4) / 2
    _sin_scroll(p, "ultimos pesos")
    capturar(p, "080-peso-de-ayer-claro", completa=True)


def test_sin_peso_no_hay_cambiar(servidor, abrir):  # R8: no field to write down a new weight
    p = abrir(servidor, "/#balance")
    expect(p.locator("#peso-hoy")).to_contain_text("díselo al coach")
    expect(p.locator("#peso-cambiar")).to_have_count(0)


# -- R9 · C-278, C-279: «Lo que ha hecho el coach» --------------------------------------------------------------------

@TEMAS
def test_lo_que_ha_hecho_el_coach(servidor, abrir, capturar, oscuro):  # R9 · C-278, C-279 · R5
    _cambio_del_coach(servidor.db, datetime(2026, 10, 21, 8, 5), "nutricionista",
                      "Hecho: apuntado en el desayuno de hoy: avena inventada 60 g (228 kcal).")
    _cambio_del_coach(servidor.db, datetime(2026, 10, 21, 9, 15), "entrenador",
                      "Hecho: series inventadas pasan del miércoles al jueves.")
    p = abrir(servidor, "/ajustes", oscuro=oscuro)
    coach = p.locator("#coach")
    expect(p.locator("#t-coach")).to_have_text("Lo que ha hecho el coach")
    filas = coach.locator(".cambio-coach")
    expect(filas).to_have_count(2)
    expect(filas.nth(0)).to_contain_text("series inventadas pasan del miércoles al jueves")  # the newest first
    expect(filas.nth(0)).to_contain_text("Entrenador")
    expect(filas.nth(0)).to_contain_text("9:15")
    expect(filas.nth(1)).to_contain_text("avena inventada 60 g")
    expect(filas.nth(1)).to_contain_text("Nutricionista")
    expect(coach.locator("button, input, textarea, select")).to_have_count(0)  # only to look at
    expect(p.locator("body")).not_to_contain_text("El equipo")  # R5: no model per role
    expect(p.locator("body")).not_to_contain_text("tope")  # R5: no cap of spending
    _sin_scroll(p, "ajustes coach")
    coach.scroll_into_view_if_needed()
    capturar(p, f"080-ajustes-coach-{'oscuro' if oscuro else 'claro'}")


def test_sin_cambios_del_coach_lo_dice(servidor, abrir):  # R9
    p = abrir(servidor, "/ajustes")
    expect(p.locator("#coach-vacio")).to_have_text("El coach aún no ha hecho ningún cambio desde el PC.")
