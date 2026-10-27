"""Unit 044 in a real Chromium at 390 × 844, light and dark (E2E-8 of `seguir-molestia`): Andrés tells the Fisio
«al acabar el rodaje noto el lateral del tobillo», the Fisio leaves the card «Voy a abrir una incidencia: …», he
confirms it; later he tells «fisio hoy: sin lesión, alta para correr» and confirms the card of the visit. Fitness ›
Plan › Incidencias shows it open first, with its visit, above the closed one of his .md; he closes it by hand with
its lesson and it goes down, closed, with the lesson.

C-187, C-188, C-189 (and C-190 by hand). Uses the server of `test_conversaciones_e2e.py` with Claude doubled (no
API, no cost): the double asks for the card that fits what Andrés said. The plan is invented.
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from datetime import datetime  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from playwright.sync_api import expect  # noqa: E402
from test_conversaciones_e2e import TEMAS, ServidorAsistente, _Flujo  # noqa: E402

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
MOLESTIA = "al acabar el rodaje noto el lateral del tobillo"
VISITA = "fisio hoy: sin lesión, alta para correr"
CAMBIOS = {
    MOLESTIA: {"accion": "abrir_incidencia", "objeto": "tobillo", "campo": "derecho, lateral", "desde": "2026-10-20",
               "texto": "molestia inventada en el lateral al acabar el rodaje"},
    VISITA: {"accion": "anadir_visita", "objeto": "tobillo", "campo": "fisio", "dia": "2026-10-21",
             "texto": "sin lesión, alta para correr"},
}


def _uso():
    return SimpleNamespace(input_tokens=30, output_tokens=60, cache_creation_input_tokens=0, cache_read_input_tokens=0)


def _fisio(**peticion):
    """Asks for the card of what Andrés said; with its result, answers in words."""
    ultimo = peticion["messages"][-1]["content"]
    if isinstance(ultimo, list):  # the result of proponer_cambio
        texto = "Te lo dejo en una tarjeta: confírmala si te parece."
        return _Flujo([texto], SimpleNamespace(content=[SimpleNamespace(type="text", text=texto)],
                                               stop_reason="end_turn", usage=_uso()))
    dicho = next(c for c in CAMBIOS if c in str(ultimo))
    return _Flujo([], SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id=f"t{len(peticion['messages'])}", name="proponer_cambio",
                                 input=CAMBIOS[dicho])],
        stop_reason="tool_use", usage=_uso()))


@pytest.fixture
def servidor(tmp_path):
    carpeta = tmp_path / "molestias"
    s = ServidorAsistente(carpeta)
    s.claude.stream = _fisio
    Planes(carpeta / "data.sqlite3").crear(PLAN, datetime(2026, 10, 20, 9, 0))
    yield s
    s.parar()


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


def _contar(p, texto: str):
    p.locator("#texto").fill(texto)
    p.locator("#enviar").click()
    tarjeta = p.locator("#conv-mensajes .tarjeta-cambio").last
    expect(tarjeta).to_be_visible()
    return tarjeta


@TEMAS
def test_contar_una_molestia_al_fisio_y_seguirla(servidor, abrir, capturar, oscuro):  # E2E-8 · C-187, C-188, C-189
    tema = "oscuro" if oscuro else "claro"
    p = abrir(servidor, "/#asistente", oscuro=oscuro, esperar=False)
    p.locator("#conv-nueva").click()
    p.locator('#conv-papeles button[data-papel="fisio"]').click()

    tarjeta = _contar(p, MOLESTIA)
    expect(tarjeta).to_contain_text("Voy a abrir una incidencia: tobillo derecho, lateral, desde el 20/10")  # C-187
    expect(tarjeta).to_contain_text("molestia inventada en el lateral al acabar el rodaje")
    _sin_scroll(p, "tarjeta")
    capturar(p, f"044-tarjeta-incidencia-{tema}", completa=True)
    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(p.locator("#conv-mensajes .tarjeta-cambio")).to_have_count(0)

    tarjeta = _contar(p, VISITA)
    expect(tarjeta).to_contain_text("Voy a añadir a la incidencia: visita al fisio del 21/10 · sin lesión, alta para "
                                    "correr")  # C-188
    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(p.locator("#conv-mensajes .tarjeta-cambio")).to_have_count(0)

    p.evaluate("location.hash = '#fitness/plan'")
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
