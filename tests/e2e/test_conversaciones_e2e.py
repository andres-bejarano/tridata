"""Unit 032 in a real Chromium at 390 × 844, light and dark: the Asistente's conversations (recorrido E2E-5).

The list (empty, then with its conversations), a new conversation that titles itself, the line of what it sees, the
answer as it arrives and saved, «sin respuesta» with «Reintentar» (the API fails or the PC is off), «Pensar más»,
the card of Hoy that takes to the last conversation, renaming and deleting with confirmation.
Plans `preguntar-equipo` REC-20: E2E-5 · C-153, C-154, C-156, C-158, C-159 · R11 (R-97, R-98) of `ver-dia-movil`.

Uses the fixtures `abrir`, `capturar` and `ir_a` of the `conftest.py` of unit 010, with its own server: the app over a
temporary SQLite with invented data and Claude doubled (`ClaudeDeLaPrueba`: no API, no cost).
"""
from __future__ import annotations

import socket
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

import anthropic  # noqa: E402
import httpx2  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from tridata.ia.conversacion import ConversadorClaude  # noqa: E402
from tridata.models import Activity, DailyStats  # noqa: E402
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, ObjetivosDia, RegistroComidas, RegistroPeso  # noqa: E402
from tridata.storage import DataStore  # noqa: E402
from tridata.web import crear_app  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` and the phone's clock of conftest.py
AHORA = datetime(2026, 10, 21, 10, 0)
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
PRIMERO = "quiero hacer 10 km el 3 de diciembre en 45 minutos"
RESPUESTA = ("Vas bien encaminado: tu carrera de prueba del domingo salió a 4:40 el km y tu carga sube poco a poco. "
             "Para bajar de 45 minutos te propongo series de 1 km el jueves.")


class _Flujo:
    def __init__(self, trozos, final) -> None:
        self._trozos, self._final = trozos, final

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    @property
    def text_stream(self):
        for trozo in self._trozos:
            time.sleep(0.05)  # it arrives in pieces, as from the API
            yield trozo

    def get_final_message(self):
        return self._final


class ClaudeDeLaPrueba:
    """`messages.stream`: titles the first message of a conversation and answers `RESPUESTA` (or `fallo`)."""

    def __init__(self) -> None:
        self.messages = self
        self.fallo: Exception | None = None
        self.modelos: list[str] = []

    def stream(self, **peticion):
        if self.fallo is not None:
            raise self.fallo
        self.modelos.append(peticion["model"])
        uso = SimpleNamespace(input_tokens=30, output_tokens=60, cache_creation_input_tokens=0,
                              cache_read_input_tokens=0)
        ultimo = peticion["messages"][-1]
        if isinstance(ultimo["content"], list):  # the results of the title: the answer goes on
            return _Flujo([RESPUESTA[:40], RESPUESTA[40:]],
                          SimpleNamespace(content=[SimpleNamespace(type="text", text=RESPUESTA)],
                                          stop_reason="end_turn", usage=uso))
        if len(peticion["messages"]) == 1:
            return _Flujo([], SimpleNamespace(
                content=[SimpleNamespace(type="tool_use", id="t1", name="poner_titulo",
                                         input={"titulo": "10 km el 3 de diciembre"})],
                stop_reason="tool_use", usage=uso))
        texto = "Sí: el jueves, 5 × 1 km con 2 minutos de trote."
        return _Flujo([texto], SimpleNamespace(content=[SimpleNamespace(type="text", text=texto)],
                                               stop_reason="end_turn", usage=uso))


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServidorAsistente:
    """The app with conversations, on 127.0.0.1:<free port>; what `abrir` of conftest.py needs (`url`, `con_ajustes`)."""

    con_ajustes = True

    def __init__(self, carpeta: Path) -> None:
        import uvicorn

        carpeta.mkdir(parents=True, exist_ok=True)
        db = carpeta / "data.sqlite3"
        ajustes = Ajustes(db)
        ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000, "objetivo_pct": -10},
                        desde=DESDE_SIEMPRE)
        registro = RegistroComidas(db)
        registro.guardar([Alimento(nombre="avena de prueba", dia=HOY, cantidad=60, unidad="g", kcal=228)], hoy=HOY,
                         texto="avena", sin_cambios=True)
        pesos = RegistroPeso(db)
        pesos.apuntar(HOY, 72.4, hoy=HOY)
        store = DataStore(db)
        store.save_activities([Activity(activity_id="e2e-carrera", activity_date=HOY - timedelta(days=3),
                                        activity_type="running", name="Carrera de prueba", duration_seconds=2800,
                                        distance_meters=10000, avg_hr=150)])
        store.save_daily_stats(DailyStats(stat_date=HOY, steps=9000, resting_hr=48, total_calories=2600))
        self.claude = ClaudeDeLaPrueba()
        app = crear_app(registro, MagicMock(), ajustes, hoy=lambda: HOY, pesos=pesos, objetivos=ObjetivosDia(db),
                        store=store, ahora=lambda: AHORA, conversador=ConversadorClaude(cliente=self.claude))
        self.puerto = _puerto_libre()
        self.url = f"http://127.0.0.1:{self.puerto}"
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.puerto, log_level="warning"))
        self._hilo = threading.Thread(target=self._server.run, name="e2e-asistente", daemon=True)
        self._hilo.start()
        limite = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > limite:
                raise RuntimeError("the e2e server did not start")
            time.sleep(0.02)

    def parar(self) -> None:
        self._server.should_exit = True
        self._hilo.join(timeout=10)


@pytest.fixture
def servidor(tmp_path):
    s = ServidorAsistente(tmp_path / "asistente")
    yield s
    s.parar()


def _sin_scroll(p) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"horizontal scroll: {ancho} px"


def _conversacion_nueva(p) -> None:
    p.locator("#conv-nueva").click()
    expect(p.locator("#conv-chat")).to_be_visible()
    expect(p.locator("#entrada")).to_be_visible()  # S-37: in a conversation the box is the chat's
    expect(p.locator("#texto")).to_have_attribute("placeholder", "Escribe o dicta tu pregunta")
    p.locator("#texto").fill(PRIMERO)
    p.locator("#enviar").click()


@TEMAS
def test_recorrido_del_asistente(servidor, abrir, capturar, ir_a, oscuro):  # E2E-5 · C-153, C-154, C-156, C-159, R11
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#asistente", oscuro=oscuro, esperar=False)
    pantalla = p.locator("section#asistente")
    expect(pantalla).to_be_visible()
    expect(pantalla.locator("h1")).to_have_text("Asistente")
    expect(pantalla).to_contain_text("tus conversaciones con el asistente")
    expect(p.locator("#conv-vacia")).to_be_visible()  # R1: none, a sentence of what it can be asked
    expect(p.locator("#conv-nueva")).to_be_visible()
    expect(p.locator("#entrada")).to_be_hidden()
    _sin_scroll(p)
    capturar(p, f"asistente-vacio{sufijo}")

    _conversacion_nueva(p)
    respuesta = p.locator("#conv-mensajes .burbuja.asistente").last
    expect(respuesta).to_have_text(RESPUESTA)  # C-154: the answer, saved
    expect(p.locator("#conv-mensajes .burbuja.andres")).to_have_text(PRIMERO)
    expect(p.locator("#conv-titulo")).to_have_text("10 km el 3 de diciembre")  # C-161
    expect(p).to_have_url(f"{servidor.url}/#asistente/1")
    expect(p.locator("#conv-linea")).to_contain_text("Ve tus comidas, peso, entrenos, sueño, pulso y HRV")  # C-156
    expect(p.locator("#conv-linea")).to_contain_text("hasta hoy 10:00")
    expect(p.locator("#texto")).to_have_value("")
    _sin_scroll(p)
    capturar(p, f"asistente-conversacion{sufijo}")

    p.locator("#conv-volver").click()  # C-153: back to the list, with its title and date
    fila = p.locator("#conv-lista .conversacion").first
    expect(fila).to_contain_text("10 km el 3 de diciembre")
    expect(fila).to_contain_text("Vas bien encaminado")
    expect(p.locator("#conv-vacia")).to_be_hidden()
    expect(p.locator("#entrada")).to_be_hidden()
    capturar(p, f"asistente-lista{sufijo}")

    ir_a(p, "hoy")  # R11 (R-97, R-98): Hoy shows the beginning of the last answer and takes to it
    tarjeta = p.locator("#hoy-asistente")
    expect(tarjeta).to_contain_text("Vas bien encaminado")
    capturar(p, f"asistente-hoy{sufijo}", completa=True)
    tarjeta.locator("#hoy-asistente-texto").click()
    expect(p).to_have_url(f"{servidor.url}/#asistente/1")
    expect(p.locator("#conv-mensajes .burbuja")).to_have_count(2)  # C-157: everything said is there

    p.locator("#conv-renombrar").click()  # C-161: Andrés can change the title
    p.locator("#conv-titulo-nuevo").fill("Mi 10K de diciembre")
    p.locator("#conv-renombrar-caja").get_by_role("button", name="Guardar").click()
    expect(p.locator("#conv-titulo")).to_have_text("Mi 10K de diciembre")

    p.locator("#conv-borrar").click()  # C-159: deleting asks to confirm
    caja = p.locator("#conv-borrar-caja")
    expect(caja).to_contain_text("no se tocan")
    caja.get_by_role("button", name="No").click()
    expect(p.locator("#conv-chat")).to_be_visible()
    p.locator("#conv-borrar").click()
    caja.get_by_role("button", name="Sí, borrar").click()
    expect(p).to_have_url(f"{servidor.url}/#asistente")
    expect(p.locator("#conv-vacia")).to_be_visible()
    expect(p.locator("#conv-lista .conversacion")).to_have_count(0)


def test_sin_respuesta_se_reintenta_y_pensar_mas(servidor, abrir, capturar):  # C-158 · R6, R9
    p = abrir(servidor, "/#asistente", esperar=False)
    _conversacion_nueva(p)
    expect(p.locator("#conv-mensajes .burbuja.asistente")).to_have_text(RESPUESTA)

    # the API does not answer: the message stays, «sin respuesta» and «Reintentar»
    servidor.claude.fallo = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://example.invalid"))
    p.locator("#texto").fill("¿y el jueves qué hago?")
    p.locator("#enviar").click()
    pendiente = p.locator("#conv-mensajes .burbuja.andres").last
    expect(pendiente).to_contain_text("¿y el jueves qué hago?")
    expect(pendiente).to_contain_text("sin respuesta")
    capturar(p, "asistente-sin-respuesta-claro")
    servidor.claude.fallo = None
    pendiente.get_by_role("button", name="Reintentar").click()
    expect(p.locator("#conv-mensajes .burbuja.asistente").last).to_contain_text("5 × 1 km")
    expect(p.locator("#conv-mensajes")).not_to_contain_text("sin respuesta")
    expect(p.locator("#conv-mensajes .burbuja")).to_have_count(4)  # retried, not written twice

    # the PC is off: the phone keeps it with «sin respuesta» and sends it when tapped again
    p.route("**/api/conversaciones/*/mensajes", lambda ruta: ruta.abort())
    p.locator("#texto").fill("¿y el sábado?")
    p.locator("#enviar").click()
    pendiente = p.locator("#conv-mensajes .burbuja.andres").last
    expect(pendiente).to_contain_text("sin respuesta")
    p.unroute("**/api/conversaciones/*/mensajes")
    pendiente.get_by_role("button", name="Reintentar").click()
    expect(p.locator("#conv-mensajes")).not_to_contain_text("sin respuesta")
    expect(p.locator("#conv-mensajes .burbuja")).to_have_count(6)

    # «Pensar más»: that message with Sonnet, then back to Haiku
    p.locator("#pensar").click()
    expect(p.locator("#pensar")).to_have_attribute("aria-pressed", "true")
    p.locator("#texto").fill("piénsalo bien")
    p.locator("#enviar").click()
    expect(p.locator("#conv-mensajes .burbuja")).to_have_count(8)
    expect(p.locator("#pensar")).to_have_attribute("aria-pressed", "false")
    assert servidor.claude.modelos[-1] == "claude-sonnet-5-5"
    assert set(servidor.claude.modelos[:-1]) == {"claude-haiku-5-5"}
