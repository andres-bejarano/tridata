"""Unit 043 (REC-27 of `mandar-sesion-reloj`): Ajustes › Google Calendar, the routes of `web/calendario.py`.

    GET  /api/calendario              the state: connected or not, the calendar «TriData», the last update, the failure
    POST /api/calendario/conectar     Google's permission (the installed-app flow, doubled here) and a first pass
    POST /api/calendario/sincronizar  «Sincronizar ahora»

R1, R4, R5, R6 · C-206, C-210. Google and its OAuth flow are ALWAYS doubled (`test_calendario.GoogleDoble`): no test
talks to Google or to Anthropic. Every value is invented.
"""
from __future__ import annotations

import json
import logging
import time
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError
from starlette.testclient import TestClient
from test_calendario import AHORA, HOY, MARCADOR, SERIES, TOKEN, GoogleDoble, plan_inventado

from tridata.calendario import NOMBRE, Calendario
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroComidas
from tridata.plan import Planes
from tridata.web import crear_app

SIN_CONECTAR = "sin conectar"


class Web:
    def __init__(self, tmp_path, *, configurado: bool = True, conectado: bool = False) -> None:
        self.db = tmp_path / "data.sqlite3"
        ajustes = Ajustes(self.db)
        ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000}, desde=DESDE_SIEMPRE)
        self.planes = Planes(self.db)
        self.token = tmp_path / "google-token.json"
        cliente = tmp_path / "cliente-inventado.json"
        if configurado:
            cliente.write_text("{}", encoding="utf-8")
        if conectado:
            self.token.write_text(TOKEN, encoding="utf-8")
        self.google = GoogleDoble()
        self.construidos = 0
        self.flujos = 0
        self.calendario = Calendario(self.db, self.token, str(cliente) if configurado else None, flujo=self._flujo,
                                     servicio=self._servicio)
        self.app = crear_app(RegistroComidas(self.db), MagicMock(), ajustes, hoy=lambda: HOY,
                             objetivos=ObjetivosDia(self.db), actualizar_garmin=MagicMock(), ahora=lambda: AHORA,
                             ordenador_plan=MagicMock(), conversador=MagicMock(), calendario=self.calendario)
        self.cliente = TestClient(self.app)

    def _flujo(self, cliente: str) -> str:
        self.flujos += 1
        return TOKEN

    def _servicio(self, token):
        self.construidos += 1
        return self.google

    def eventos(self) -> list[dict]:
        """The events of «TriData» in the doubled Google (none while it does not exist)."""
        cal = next((c["id"] for c in self.google.calendarios if c["summary"] == NOMBRE), None)
        return self.google.de(cal) if cal else []

    def estado(self) -> dict:
        r = self.cliente.get("/api/calendario")
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def web(tmp_path) -> Web:
    return Web(tmp_path)


def test_sin_conectar_lo_dice_y_nada_habla_con_google(tmp_path):  # R6
    web = Web(tmp_path)
    with TestClient(web.app) as cliente:  # the start of the app (and its periodic pass) runs too
        estado = cliente.get("/api/calendario").json()
        assert estado["conectado"] is False and estado["texto"] == SIN_CONECTAR
        r = cliente.post("/api/calendario/sincronizar")
        assert r.status_code == 200 and r.json()["texto"] == SIN_CONECTAR
    assert web.construidos == 0 and web.google.llamadas == [] and web.flujos == 0


def test_conectar_crea_o_reutiliza_tridata_y_manda_las_sesiones(web):  # R1, R2 · C-206
    web.planes.crear(plan_inventado([SERIES]), AHORA)
    r = web.cliente.post("/api/calendario/conectar")
    assert r.status_code == 200, r.text
    estado = r.json()
    assert estado["conectado"] is True and estado["calendario"] == NOMBRE
    assert estado["texto"] == f"Conectado · calendario {NOMBRE}"
    assert estado["error"] is None and estado["ultima"] == AHORA.isoformat(timespec="minutes")
    assert web.flujos == 1
    assert [c["summary"] for c in web.google.calendarios] == [NOMBRE]
    assert [e["summary"] for e in web.eventos()] == ["🏃 Series 4×12' a 4:00"]
    assert web.estado() == estado


def test_conectar_sin_el_cliente_de_google_dice_que_falta(tmp_path):  # R1
    web = Web(tmp_path, configurado=False)
    r = web.cliente.post("/api/calendario/conectar")
    assert r.status_code == 409
    assert "TRIDATA_GOOGLE_CLIENTE" in r.json()["error"]
    assert web.flujos == 0 and web.construidos == 0
    assert web.estado()["configurado"] is False


def test_al_arrancar_la_app_sincroniza_si_esta_conectado(tmp_path):  # R2: at start, then every 10 minutes
    web = Web(tmp_path, conectado=True)
    web.planes.crear(plan_inventado([SERIES]), AHORA)
    with TestClient(web.app):
        for _ in range(200):
            if web.eventos():
                break
            time.sleep(0.02)
    assert [e["summary"] for e in web.eventos()] == ["🏃 Series 4×12' a 4:00"]


@pytest.mark.parametrize("fallo, dice", [
    (HttpError(httplib2.Response({"status": 401}), b"{}"), "permiso"),
    (OSError("sin red"), "no responde"),
])
def test_si_google_falla_ajustes_lo_dice_el_plan_sigue_y_se_reintenta(tmp_path, fallo, dice):  # R4 · C-210
    web = Web(tmp_path, conectado=True)
    web.planes.crear(plan_inventado([SERIES]), AHORA)
    web.google.fallo = fallo
    r = web.cliente.post("/api/calendario/sincronizar")
    assert r.status_code == 200
    estado = web.estado()
    assert estado["conectado"] is True
    assert estado["error"].startswith("No se pudo actualizar el calendario: ") and dice in estado["error"]
    assert "reintentará" in estado["error"]
    # the plan answers the same
    plan = web.cliente.get("/api/plan")
    assert plan.status_code == 200 and plan.json()["plan"]["nombre"] == "Road to Inventada 26"
    # the next pass works and the warning goes
    web.google.fallo = None
    web.cliente.post("/api/calendario/sincronizar")
    estado = web.estado()
    assert estado["error"] is None and estado["ultima"] == AHORA.isoformat(timespec="minutes")
    assert len(web.eventos()) == 1


def test_el_token_no_sale_en_registros_ni_en_respuestas(tmp_path, caplog):  # R5
    caplog.set_level(logging.DEBUG)
    web = Web(tmp_path)
    web.planes.crear(plan_inventado([SERIES]), AHORA)
    respuestas = [web.cliente.post("/api/calendario/conectar").text]
    web.google.fallo = HttpError(httplib2.Response({"status": 401}), json.dumps({"token": MARCADOR}).encode())
    respuestas.append(web.cliente.post("/api/calendario/sincronizar").text)
    respuestas.append(web.cliente.get("/api/calendario").text)
    web.google.fallo = OSError(MARCADOR)
    respuestas.append(web.cliente.post("/api/calendario/sincronizar").text)
    assert web.token.exists()
    for texto in [*respuestas, caplog.text]:
        assert MARCADOR not in texto
        assert "cliente-inventado.json" not in texto  # nor the path of the client's JSON
