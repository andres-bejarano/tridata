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
import threading
import time
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError
from starlette.testclient import TestClient
from test_calendario import AHORA, HOY, MARCADOR, SERIES, TOKEN, GoogleDoble, plan_inventado

from tridata.calendario import NOMBRE, Calendario
from tridata.ia import CambioInterpretado
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.nutricion.asistente import Contexto, Peticion, proponer
from tridata.nutricion.recetas import Recetario
from tridata.web import calendario as web_calendario
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
        self.revocados: list[str] = []
        self.fallo_al_revocar: Exception | None = None
        self.calendario = Calendario(self.db, self.token, str(cliente) if configurado else None, flujo=self._flujo,
                                     servicio=self._servicio, revocar=self._revocar)
        self.ajustes = ajustes
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

    def _revocar(self, token) -> None:
        self.revocados.append(token.read_text(encoding="utf-8"))
        if self.fallo_al_revocar is not None:
            raise self.fallo_al_revocar

    def pasadas_iniciadas(self) -> int:
        """How many passes have started: each one begins asking Google if «TriData» is still there."""
        return len([ll for ll in self.google.llamadas if ll[:2] == ("calendars", "get")])

    def tarjeta_cambiar_series(self) -> dict:
        """The card of the coach that changes the Wednesday's series, as /api/asistente/confirmar receives it."""
        ctx = Contexto(registro=RegistroComidas(self.db), objetivos=ObjetivosDia(self.db), ajustes=self.ajustes,
                       pesos=RegistroPeso(self.db), recetario=Recetario(self.db), hoy=lambda: HOY,
                       planes=self.planes)
        (tarjeta,) = proponer([CambioInterpretado(accion="plan_cambiar_sesion", dia=HOY.replace(day=28),
                                                  texto="Series 3×10' | 20' suave | 3×10' a 4:00 | 10' suave")],
                              ctx, Peticion(texto="texto de prueba", fecha=HOY))
        assert tarjeta["tipo"] == "propuesta", tarjeta
        return tarjeta

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


# -- unit 062: a more solid Google Calendar ---------------------------------------------------------------------------

def _esperar(condicion, segundos: float = 5) -> None:
    limite = time.monotonic() + segundos
    while not condicion():
        assert time.monotonic() < limite, "it did not happen in time"
        time.sleep(0.02)


ACCIONES = {
    "tarjeta": lambda web, plan_id: web.cliente.post("/api/asistente/confirmar",
                                                     json={"tarjeta": web.tarjeta_cambiar_series()}),
    "carrera": lambda web, plan_id: web.cliente.post("/api/plan/carrera", json={
        "id": plan_id, "carrera": "Maratón inventado", "objetivo": "3:00"}),
    "garmin": lambda web, plan_id: web.cliente.post("/api/garmin/actualizar"),
}


@pytest.mark.parametrize("accion", list(ACCIONES))
def test_tras_cambiar_el_plan_el_calendario_se_actualiza_enseguida(tmp_path, accion):  # 062 R1 (criterio portante)
    web = Web(tmp_path, conectado=True)
    plan_id = web.planes.crear(plan_inventado([SERIES]), AHORA)
    with TestClient(web.app) as cliente:  # the thread of the calendar runs, asleep for 10 minutes after its first pass
        web.cliente = cliente
        _esperar(lambda: web.calendario.estado()["ultima"] is not None)
        antes = web.pasadas_iniciadas()
        web.google.puerta = threading.Event()  # Google is slow from now on
        inicio = time.monotonic()
        r = ACCIONES[accion](web, plan_id)
        assert r.status_code == 200, r.text
        assert time.monotonic() - inicio < 3  # the answer does not wait for Google
        _esperar(lambda: web.pasadas_iniciadas() > antes, segundos=5)  # a pass started, not in 10 minutes
        web.google.puerta.set()
        web.google.puerta = None
        if accion == "tarjeta":
            _esperar(lambda: [e["summary"] for e in web.eventos()] == ["🏃 Series 3×10'"])


def test_lo_que_no_cambia_nada_no_despierta_el_calendario(tmp_path):  # 062 R1: only a 2xx of those actions
    web = Web(tmp_path, conectado=True)
    plan_id = web.planes.crear(plan_inventado([SERIES]), AHORA)
    with TestClient(web.app) as cliente:
        web.cliente = cliente
        _esperar(lambda: web.calendario.estado()["ultima"] is not None)
        antes = web.pasadas_iniciadas()
        r = cliente.post("/api/plan/carrera", json={"id": plan_id, "carrera": "No existe inventada", "objetivo": "x"})
        assert r.status_code == 404
        assert cliente.get("/api/plan").status_code == 200
        time.sleep(0.5)
        assert web.pasadas_iniciadas() == antes


def test_un_error_no_previsto_se_dice_en_ajustes_y_la_siguiente_vuelta_se_hace(tmp_path, monkeypatch,
                                                                               caplog):  # 062 R3
    caplog.set_level(logging.DEBUG)
    web = Web(tmp_path, conectado=True)
    web.planes.crear(plan_inventado([SERIES]), AHORA)
    real = web_calendario.sincronizar

    def rota(*_, **__):
        raise RuntimeError("TEXTO-INTERNO-INVENTADO")

    monkeypatch.setattr(web_calendario, "sincronizar", rota)
    r = web.cliente.post("/api/calendario/sincronizar")
    assert r.status_code == 200
    assert web.estado()["error"] == ("No se pudo actualizar el calendario: error inesperado. Se reintentará en la "
                                     "próxima vuelta.")
    assert "RuntimeError" in caplog.text and "TEXTO-INTERNO-INVENTADO" not in caplog.text
    assert "TEXTO-INTERNO-INVENTADO" not in r.text
    monkeypatch.setattr(web_calendario, "sincronizar", real)
    web.cliente.post("/api/calendario/sincronizar")
    assert web.estado()["error"] is None and len(web.eventos()) == 1


@pytest.mark.parametrize("fallo", [None, OSError("red inventada caída")], ids=["google-responde", "google-no"])
def test_desconectar_y_volver_a_conectar_sin_duplicar(tmp_path, fallo):  # 062 R4, R5 · REC-27
    web = Web(tmp_path, conectado=True)
    web.planes.crear(plan_inventado([SERIES]), AHORA)
    web.cliente.post("/api/calendario/sincronizar")
    (evento,) = web.eventos()
    web.fallo_al_revocar = fallo

    r = web.cliente.post("/api/calendario/desconectar")
    assert r.status_code == 200, r.text
    estado = r.json()
    assert estado["conectado"] is False and estado["texto"] == SIN_CONECTAR
    assert web.revocados == [TOKEN] and not web.token.exists()
    if fallo is None:
        assert not estado.get("aviso")
    else:  # disconnected all the same, and said
        assert "Google no responde" in estado["aviso"] and "red inventada" not in r.text
    assert web.estado()["conectado"] is False
    assert [c["summary"] for c in web.google.calendarios] == [NOMBRE]  # «TriData» stays in Google
    llamadas = len(web.google.llamadas)
    assert web.cliente.post("/api/calendario/sincronizar").json()["texto"] == SIN_CONECTAR
    assert len(web.google.llamadas) == llamadas

    r = web.cliente.post("/api/calendario/conectar")
    assert r.status_code == 200, r.text
    assert r.json()["conectado"] is True and r.json()["error"] is None
    assert [c["summary"] for c in web.google.calendarios] == [NOMBRE]  # not a second «TriData»
    assert [e["id"] for e in web.eventos()] == [evento["id"]]  # nor repeated events
    assert MARCADOR not in r.text


def test_desconectar_con_una_vuelta_en_curso_espera_a_que_acabe(tmp_path):  # 062 R4
    web = Web(tmp_path, conectado=True)
    web.planes.crear(plan_inventado([SERIES]), AHORA)
    web.google.puerta = threading.Event()
    respuestas: dict[str, object] = {}
    vuelta = threading.Thread(target=lambda: respuestas.setdefault(
        "vuelta", web.cliente.post("/api/calendario/sincronizar")), daemon=True)
    vuelta.start()
    _esperar(lambda: web.google.llamadas)  # the pass is talking to Google
    desconectar = threading.Thread(target=lambda: respuestas.setdefault(
        "desconectar", web.cliente.post("/api/calendario/desconectar")), daemon=True)
    desconectar.start()
    time.sleep(0.3)
    assert web.token.exists() and "desconectar" not in respuestas  # it waits for the pass
    web.google.puerta.set()
    vuelta.join(10)
    desconectar.join(10)
    assert respuestas["vuelta"].status_code == 200 and respuestas["desconectar"].status_code == 200
    assert not web.token.exists()
    llamadas, construidos = len(web.google.llamadas), web.construidos
    web.cliente.post("/api/calendario/sincronizar")
    assert len(web.google.llamadas) == llamadas and web.construidos == construidos  # no pass talks to Google


def test_desconectar_sin_conectar_no_habla_con_google(tmp_path):  # 062 R4
    web = Web(tmp_path)
    r = web.cliente.post("/api/calendario/desconectar")
    assert r.status_code == 200 and r.json()["texto"] == SIN_CONECTAR
    assert web.revocados == [] and web.google.llamadas == []


# -- unit 062, round 2: the routes of the 063 (plans by hand in Fitness › Plan) wake the calendar too -----------------

DIEZ = {"apartado": 0, "nombre": "10K inventado", "fecha": "2026-11-15", "objetivo": "40:00", "estado": "pendiente",
        "principal": False}
RUTAS_063 = {
    "nuevo": lambda plan_id, otro: ("/api/plan/nuevo", {"nombre": "Plan nuevo inventado", "carrera": {
        "nombre": "Provisional inventada", "fecha": "2027-09-12", "objetivo": "acabar"}}),
    "renombrar": lambda plan_id, otro: (f"/api/plan/{plan_id}/renombrar", {"nombre": "Otro nombre inventado"}),
    "borrar": lambda plan_id, otro: (f"/api/plan/{otro}/borrar", {}),
    "carrera-anadir": lambda plan_id, otro: ("/api/plan/carrera/anadir", {
        "id": plan_id, "nombre": "Sprint inventado", "fecha": "2027-04-18", "objetivo": ""}),
    "carrera-quitar": lambda plan_id, otro: ("/api/plan/carrera/quitar", {"id": plan_id, "carrera": "10K inventado"}),
    "carrera-mover": lambda plan_id, otro: ("/api/plan/carrera/mover", {
        "id": plan_id, "carrera": "10K inventado", "destino": otro}),
    "carrera-principal": lambda plan_id, otro: ("/api/plan/carrera/principal", {
        "id": plan_id, "carrera": "10K inventado"}),
}


def _con_dos_planes(tmp_path) -> tuple[Web, int, int]:
    web = Web(tmp_path, conectado=True)
    otro = web.planes.crear(plan_inventado([], nombre="Otro plan inventado"), AHORA)
    base = plan_inventado([SERIES])
    plan_id = web.planes.crear({**base, "carreras": [*base["carreras"], DIEZ]}, AHORA)
    return web, plan_id, otro


@pytest.mark.parametrize("ruta", list(RUTAS_063))
def test_lo_que_se_guarda_a_mano_en_fitness_plan_despierta_el_calendario(tmp_path, ruta):  # 062 R1 · 063
    web, plan_id, otro = _con_dos_planes(tmp_path)
    with TestClient(web.app) as cliente:
        web.cliente = cliente
        _esperar(lambda: web.calendario.estado()["ultima"] is not None)
        antes = web.pasadas_iniciadas()
        camino, cuerpo = RUTAS_063[ruta](plan_id, otro)
        r = cliente.post(camino, json=cuerpo)
        assert r.status_code == 200, r.text
        _esperar(lambda: web.pasadas_iniciadas() > antes, segundos=5)  # a pass started, not in 10 minutes


def test_la_tarjeta_del_reloj_no_cambia_el_plan_ni_despierta_el_calendario(tmp_path):  # 062 R1
    web, plan_id, _ = _con_dos_planes(tmp_path)
    (sesion,) = web.planes.sesiones_reloj(plan_id)
    with TestClient(web.app) as cliente:
        web.cliente = cliente
        _esperar(lambda: web.calendario.estado()["ultima"] is not None)
        antes = web.pasadas_iniciadas()
        r = cliente.post(f"/api/plan/sesion/{sesion['id']}/garmin")
        assert r.status_code == 200, r.text  # only a card: nothing changes until it is confirmed
        time.sleep(0.5)
        assert web.pasadas_iniciadas() == antes
