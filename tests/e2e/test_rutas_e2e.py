"""Unit 045 in a real Chromium at 390 × 844, light and dark (E2E-9 of `preparar-carrera`): in a conversation with the
Entrenador, «Adjuntar ruta» offers the routes saved in Garmin, the activities already run and uploading a GPX; the
GPX uploaded draws its profile with its two climbs; «Prepárame la carrera para 1:25» gives the strategy by stretches
and the card «Voy a guardar en el plan: estrategia de …»; confirmed, it is in that race of the plan. A route of
Garmin draws its profile too, and with Garmin not answering it says so and offers the GPX.
C-193, C-194, C-198 (R1-R3, R6).

Uses the server of `test_conversaciones_e2e.py` with Claude doubled (no API, no cost) and Garmin doubled where the app
connects to it (`plan.reloj.cliente_garmin`): nothing leaves for Garmin. The GPX (`tests/fixtures/ruta-inventada.gpx`)
and the plan are invented.
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from playwright.sync_api import expect  # noqa: E402
from test_conversaciones_e2e import TEMAS, ServidorAsistente, _Flujo  # noqa: E402

from tridata.garmin_client import GarminRutaError  # noqa: E402
from tridata.plan import Planes, reloj  # noqa: E402
from tridata.plan.rutas import puntos_gpx  # noqa: E402

GPX = Path(__file__).parents[1] / "fixtures" / "ruta-inventada.gpx"
PLAN = {
    "nombre": "Road to Ejemplo 26",
    "apartados": [{"titulo": "Objetivos", "parte": "objetivos"}, {"titulo": "Semanas", "parte": "previsto"}],
    "carreras": [{"apartado": 0, "nombre": "Carrera de ejemplo", "fecha": "2026-11-08", "objetivo": "1:25",
                  "estado": "pendiente", "principal": True}],
    "semanas": [{"apartado": 1, "n": 1, "desde": "2026-10-19", "hasta": "2026-10-25", "previsto_s": 14400,
                 "clave": "rodajes", "hecho_plan_s": None, "hecho_plan_km": None}],
    "sesiones": [], "nutricion": [], "incidencias": [], "notas": [],
}
PIDE = "Prepárame la carrera para 1:25"
TABLA = ("Estrategia por tramos para 1:25:\n"
         "- km 0-4 · llano · 4:15/km · paso 17:00\n"
         "- km 4-6,5 · subida +70 m · por esfuerzo, ~4:40 · paso 28:40\n"
         "- km 6,5-9 · bajada · 4:05/km · paso 38:52\n"
         "- km 9-14 · llano · 4:15/km · paso 1:00:07\n"
         "- km 14-15,5 · subida +45 m · por esfuerzo · paso 1:07:10\n"
         "- km 15,5-20 · bajada y llano · 4:10/km · paso 1:25:55\n"
         "Geles: km 6,5 coronando y km 14 al pie de la segunda.")
ESTRATEGIA = {"accion": "guardar_estrategia", "objeto": "Carrera de ejemplo",
              "texto": " | ".join(x[2:] for x in TABLA.splitlines()[1:-1]) + " | gel: km 6,5 | gel: km 14"}


def _uso():
    return SimpleNamespace(input_tokens=30, output_tokens=60, cache_creation_input_tokens=0, cache_read_input_tokens=0)


def _entrenador(**peticion):
    """With the route in the message, the strategy and its card; with the result of the card, a word; else a hello."""
    ultimo = peticion["messages"][-1]["content"]
    if isinstance(ultimo, list):  # the result of proponer_cambio
        texto = "¿La guardo en tu plan?"
        return _Flujo([texto], SimpleNamespace(content=[SimpleNamespace(type="text", text=texto)],
                                               stop_reason="end_turn", usage=_uso()))
    if PIDE in ultimo and "Ruta adjunta" in ultimo:
        return _Flujo([TABLA[:40], TABLA[40:]], SimpleNamespace(
            content=[SimpleNamespace(type="text", text=TABLA),
                     SimpleNamespace(type="tool_use", id="t1", name="proponer_cambio", input=ESTRATEGIA)],
            stop_reason="tool_use", usage=_uso()))
    texto = "Sin ruta no veo el perfil: adjúntala."
    return _Flujo([texto], SimpleNamespace(content=[SimpleNamespace(type="text", text=texto)],
                                           stop_reason="end_turn", usage=_uso()))


class GarminDoble:
    """Two saved routes and the GPX of an activity, invented; `falla` makes every call fail."""

    def __init__(self) -> None:
        self.falla: Exception | None = None
        self.llamadas: list[tuple] = []

    def _llamada(self, *que):
        self.llamadas.append(que)
        if self.falla is not None:
            raise self.falla

    def rutas_guardadas(self):
        self._llamada("rutas_guardadas")
        return [{"id": 11, "nombre": "Ruta de prueba", "distancia_m": 20000.0, "sube_m": 118.0, "baja_m": 117.0}]

    def ruta(self, ident):
        self._llamada("ruta", ident)
        return [{"distancia_m": x.distancia_m, "altitud_m": x.altitud_m, "lat": x.lat, "lon": x.lon}
                for x in puntos_gpx(GPX.read_bytes())]

    def gpx_actividad(self, ident):
        self._llamada("gpx_actividad", ident)
        return GPX.read_bytes()


@pytest.fixture
def servidor(tmp_path, monkeypatch):
    carpeta = tmp_path / "rutas"
    s = ServidorAsistente(carpeta)
    s.claude.stream = _entrenador
    s.db = carpeta / "data.sqlite3"
    Planes(s.db).crear(PLAN, datetime(2026, 10, 20, 9, 0))
    s.garmin = GarminDoble()
    monkeypatch.setattr(reloj, "cliente_garmin", lambda entorno=None: s.garmin)
    yield s
    s.parar()


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"{pantalla}: horizontal scroll ({ancho}px)"


def _adjuntar(p):
    p.locator("#conv-nueva").click()
    p.locator('#conv-papeles button[data-papel="entrenador"]').click()
    p.get_by_role("button", name="Adjuntar ruta").click()
    caja = p.locator("#conv-rutas")
    expect(caja).to_be_visible()
    return caja


@TEMAS
def test_adjuntar_un_gpx_ver_el_perfil_y_guardar_la_estrategia(servidor, abrir, capturar, oscuro):  # E2E-9
    tema = "oscuro" if oscuro else "claro"
    p = abrir(servidor, "/#asistente", oscuro=oscuro, esperar=False)
    caja = _adjuntar(p)
    expect(caja).to_contain_text("Ruta de prueba")  # C-193: the routes of Garmin…
    expect(caja).to_contain_text("20 km")
    expect(caja).to_contain_text("Carrera de prueba")  # …the activities already run…
    expect(caja.locator('input[type="file"]')).to_have_count(1)  # …or a GPX
    caja.locator('input[type="file"]').set_input_files(str(GPX))
    perfil = p.locator("#conv-ruta")
    expect(perfil).to_contain_text("Ruta adjunta: Ruta inventada")  # C-194
    expect(perfil).to_contain_text("20 km")
    expect(perfil.locator("svg .ruta-subida")).to_have_count(2)  # the two climbs drawn
    expect(perfil).to_contain_text("nunca las coordenadas")
    expect(p.locator("#conv-linea")).to_contain_text("ruta adjunta: Ruta inventada")
    _sin_scroll(p, "perfil")
    capturar(p, f"045-perfil-{tema}")

    p.locator("#texto").fill(PIDE)
    p.locator("#enviar").click()
    tarjeta = p.locator("#conv-mensajes .tarjeta-cambio").last
    expect(tarjeta).to_contain_text("Voy a guardar en el plan: estrategia de Carrera de ejemplo (6 tramos, 2 geles)")
    respuesta = p.locator("#conv-mensajes .burbuja.asistente").last
    expect(respuesta).to_contain_text("km 4-6,5 · subida +70 m")
    expect(tarjeta).to_contain_text("km 14-15,5 · subida +45 m · por esfuerzo · paso 1:07:10")
    _sin_scroll(p, "tramos")
    capturar(p, f"045-tramos-{tema}", completa=True)
    assert Planes(servidor.db).estrategia(1, "Carrera de ejemplo") == []  # not until it is confirmed (G-55)
    tarjeta.get_by_role("button", name="Confirmar", exact=True).click()
    expect(p.locator("#conv-mensajes .tarjeta-cambio")).to_have_count(0)
    expect(p.locator("#conv-aviso")).to_contain_text("Carrera de ejemplo")
    assert len(Planes(servidor.db).estrategia(1, "Carrera de ejemplo")) == 8  # C-198: in that race of the plan


def test_una_ruta_de_garmin_y_garmin_sin_responder(servidor, abrir):  # R1, R3 · C-193
    p = abrir(servidor, "/#asistente", esperar=False)
    caja = _adjuntar(p)
    caja.get_by_role("button", name="Ruta de prueba").click()
    perfil = p.locator("#conv-ruta")
    expect(perfil).to_contain_text("Ruta adjunta: Ruta de prueba")
    expect(perfil.locator("svg .ruta-subida")).to_have_count(2)
    assert ("ruta", 11) in servidor.garmin.llamadas
    servidor.garmin.falla = GarminRutaError("no answer")
    p.get_by_role("button", name="Adjuntar ruta").click()
    caja = p.locator("#conv-rutas")
    expect(caja).to_contain_text("Garmin no responde")  # R3: it says so…
    expect(caja.locator('input[type="file"]')).to_have_count(1)  # …and offers the GPX
    expect(perfil).to_contain_text("Ruta de prueba")  # the one attached stays
