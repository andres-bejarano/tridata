"""Unit 041 in a real Chromium at 390 × 844, light and dark: the team in the Asistente (recorrido E2E-7).

«Nueva conversación» lets Andrés choose who he talks to (Entrenador, Fisio, Nutricionista or Asistente); he chooses
the coach, asks, the coach answers with its name and the line says the model; «Pedir opinión a…» › Fisio answers in
the same conversation with its name; the list says whose each conversation is. Ajustes › El equipo shows the model
of each role and changes the coach to Haiku.
Plans `preguntar-equipo` REC-23: E2E-7 · C-178, C-180, C-183 · R7 (C-184).

Uses the server of `test_conversaciones_e2e.py` with Claude doubled (no API, no cost): the double answers as the role
whose instructions close the request. Every datum is invented.
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from types import SimpleNamespace  # noqa: E402

from playwright.sync_api import expect  # noqa: E402
from test_conversaciones_e2e import TEMAS, ServidorAsistente, _Flujo  # noqa: E402

PREGUNTA = "¿cómo planteas la tirada larga de 2h30?"
RESPUESTAS = {
    "Entrenador": ("Tirada de 2h30 por bloques: 30' a 5:45 (0-5,2 km), 25' a 5:15, 2×30' a 4:25 y 30' a 5:30. "
                   "Geles: min 30, 60 y 90. Corta si el pulso pasa de 165 en llano."),
    "Fisio": "Con la presión de prueba en el gemelo: si no va a más en reposo, puedes hacer la tirada.",
}


def _responder_como_el_papel(**peticion):
    """The answer of the role whose block closes `system` (unit 041: the role goes after the cache mark)."""
    papel = next((n for n in RESPUESTAS if n in peticion["system"][-1]["text"]), "Entrenador")
    texto = RESPUESTAS[papel]
    uso = SimpleNamespace(input_tokens=30, output_tokens=60, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return _Flujo([texto[:30], texto[30:]], SimpleNamespace(content=[SimpleNamespace(type="text", text=texto)],
                                                            stop_reason="end_turn", usage=uso))


@pytest.fixture
def servidor(tmp_path):
    s = ServidorAsistente(tmp_path / "equipo")
    s.claude.stream = _responder_como_el_papel  # the client the conversador already holds
    yield s
    s.parar()


def _sin_scroll(p) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"horizontal scroll: {ancho} px"


@TEMAS
def test_recorrido_del_equipo(servidor, abrir, capturar, oscuro):  # E2E-7 · C-178, C-180, C-183
    tema = "oscuro" if oscuro else "claro"
    p = abrir(servidor, "/#asistente", oscuro=oscuro, esperar=False)
    p.locator("#conv-nueva").click()
    papeles = p.locator("#conv-papeles button")
    expect(papeles).to_have_count(4)  # C-178: who he talks to
    for i, nombre in enumerate(("Entrenador", "Fisio", "Nutricionista", "Asistente")):
        expect(papeles.nth(i)).to_contain_text(nombre)
    expect(p.locator('#conv-papeles button[data-papel="asistente"]')).to_have_attribute("aria-pressed", "true")
    _sin_scroll(p)
    capturar(p, f"041-nueva-{tema}")

    p.locator('#conv-papeles button[data-papel="entrenador"]').click()
    expect(p.locator('#conv-papeles button[data-papel="entrenador"]')).to_have_attribute("aria-pressed", "true")
    p.locator("#texto").fill(PREGUNTA)
    p.locator("#enviar").click()
    respuesta = p.locator("#conv-mensajes .burbuja.asistente").last
    expect(respuesta).to_contain_text("Geles: min 30, 60 y 90")  # C-183
    expect(respuesta.locator(".papel-nombre")).to_have_text("Entrenador")
    expect(p.locator("#conv-papeles")).to_be_hidden()  # chosen: the conversation is the coach's
    expect(p.locator("#conv-linea")).to_contain_text("· Sonnet")

    opinion = p.locator("#conv-opinion")
    expect(opinion).to_contain_text("Pedir opinión a")
    expect(opinion.locator("button")).to_have_count(3)  # the other three
    opinion.locator('button[data-papel="fisio"]').click()
    fisio = p.locator("#conv-mensajes .burbuja.asistente").last
    expect(fisio).to_contain_text("presión de prueba en el gemelo")  # C-180: in the same conversation
    expect(fisio.locator(".papel-nombre")).to_have_text("Fisio")
    expect(p.locator("#conv-mensajes .burbuja.asistente")).to_have_count(2)
    expect(p.locator("#conv-mensajes .burbuja.andres")).to_have_count(1)
    _sin_scroll(p)
    capturar(p, f"041-chat-{tema}", completa=True)

    p.reload()  # what was said stays, with who said it
    expect(p.locator("#conv-mensajes .papel-nombre")).to_have_text(["Entrenador", "Fisio"])

    p.locator("#conv-volver").click()
    fila = p.locator("#conv-lista .conversacion").first
    expect(fila).to_contain_text("Entrenador ·")  # the list says whose it is


@TEMAS
def test_el_equipo_en_ajustes(servidor, abrir, capturar, oscuro):  # R7 · C-184, R10
    tema = "oscuro" if oscuro else "claro"
    p = abrir(servidor, "/ajustes", oscuro=oscuro, esperar=False)
    equipo = p.locator("#equipo")
    expect(equipo.locator("button.ajuste")).to_have_count(4)
    expect(equipo).to_contain_text("Entrenador")
    expect(equipo.locator("button.ajuste").first).to_contain_text("Sonnet")
    expect(equipo.locator("button.ajuste").last).to_contain_text("Haiku")
    expect(p.locator("#equipo-gasto")).to_contain_text("de un tope de 15 $")
    _sin_scroll(p)
    capturar(p, f"041-ajustes-{tema}", completa=True)

    equipo.locator("button.ajuste").first.click()
    p.locator('#equipo input[value="claude-haiku-5-5"]').check()
    p.locator("#equipo button.principal").click()
    expect(p.locator("#mensaje")).to_contain_text("Guardado")
    expect(equipo.locator("button.ajuste").first).to_contain_text("Haiku")
