"""Dictation (unit 006): POST /api/dictado, GET /sw.js and `tridata web` without the `voz` extra.

Starlette TestClient over a temporary SQLite; the transcriber and the Claude interpreter are
unittest.mock doubles: no test loads Whisper, touches the GPU or calls the API. Invented data.
"""
import logging
import re
import sqlite3
import sys
import tempfile
import threading
import types
from datetime import date
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata import cli
from tridata.ia import AlimentoInterpretado, ErrorInterpretacion, ErrorTranscripcion, ListaInterpretada
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, RegistroComidas
from tridata.web import crear_app

HOY = date(2026, 3, 5)
AYER = date(2026, 3, 4)
TEXTO = "300 gramos de lentejas y un plátano"
AUDIO = b"\x00\x00\x00\x18ftypmp42-audio-inventado"
TABLAS = ("alimento_apuntado", "interpretacion", "peso_dia", "objetivo_dia")


def _alimento(nombre, cantidad, unidad, kcal, dia=HOY):
    return AlimentoInterpretado(nombre=nombre, cantidad=cantidad, unidad=unidad, dia=dia, kcal=kcal,
                                proteina_g=None, hidratos_g=None, grasa_g=None, estimado=True, dudoso=False,
                                supuestos=[])


def _lista(dia=HOY):
    return ListaInterpretada(alimentos=[_alimento("lentejas", 300, "g", 360, dia),
                                        _alimento("plátano", 1, "unidad", 105, dia)])


class Entorno:
    def __init__(self, tmp_path, transcriptor="doble"):
        self.db = tmp_path / "data.sqlite3"
        ajustes = Ajustes(self.db)
        ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
        self.interprete = MagicMock()
        self.interprete.interpretar.side_effect = lambda texto, hoy: _lista(hoy)
        self.transcriptor = MagicMock() if transcriptor == "doble" else transcriptor
        if self.transcriptor is not None:
            self.transcriptor.transcribir.return_value = TEXTO
        self.cliente = TestClient(crear_app(RegistroComidas(self.db), self.interprete, ajustes, hoy=lambda: HOY,
                                            transcriptor=self.transcriptor))

    def dictar(self, audio=AUDIO, tipo="audio/mp4", consulta="?id=11111111-2222-4333-8444-555555555555&fecha=2026-03-05"):
        cabeceras = {} if tipo is None else {"Content-Type": tipo}
        return self.cliente.post(f"/api/dictado{consulta}", content=audio, headers=cabeceras)

    def filas(self):
        with sqlite3.connect(self.db) as conn:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLAS}  # noqa: S608


VACIAS = dict.fromkeys(TABLAS, 0)


@pytest.fixture
def entorno(tmp_path):
    return Entorno(tmp_path)


# -- R1 / R2: recording -> text + the same interpretation, nothing saved, nothing leaked ---------

def test_grabacion_vuelve_como_texto_y_lista_sin_guardar_nada(tmp_path, monkeypatch, caplog):  # R1, R2
    casa = tmp_path / "home"
    temporal = tmp_path / "tmp"
    casa.mkdir()
    temporal.mkdir()
    monkeypatch.setenv("HOME", str(casa))
    monkeypatch.setattr(tempfile, "tempdir", str(temporal))
    caplog.set_level(logging.DEBUG)
    e = Entorno(tmp_path)
    antes = sorted(p.name for p in tmp_path.rglob("*"))

    r = e.dictar()

    assert r.status_code == 200
    datos = r.json()
    assert datos["id"] == "11111111-2222-4333-8444-555555555555"
    assert datos["fecha"] == "2026-03-05"
    assert datos["texto"] == TEXTO
    i = datos["interpretacion"]
    assert [(a["nombre"], a["cantidad"], a["unidad"], a["kcal"]) for a in i["alimentos"]] == [
        ("lentejas", 300, "g", 360), ("Plátano", 1, "unidad", 105)]  # unit 026: the common «Plátano» (per 100 g)
    assert i["texto"] == TEXTO and i["peso"] is None and i["objetivo_dia"] is None and i["dudosas"] == []
    # the same answer /api/interpretar gives for that text (the 007 pieces)
    assert i == e.cliente.post("/api/interpretar", json={"texto": TEXTO}).json()
    assert e.filas() == VACIAS
    # R2: only the audio reaches the transcriber; only (text, date) reach Claude
    e.transcriptor.transcribir.assert_called_once_with(AUDIO)
    assert e.interprete.interpretar.call_args_list[0].args == (TEXTO, HOY)
    assert e.interprete.interpretar.call_args_list[0].kwargs == {}
    assert TEXTO not in caplog.text and "lentejas" not in caplog.text and "audio-inventado" not in caplog.text
    assert sorted(p.name for p in tmp_path.rglob("*")) == antes  # no file written: not in HOME, not in tmp


@pytest.mark.parametrize("tipo", ["audio/mp4", "audio/x-m4a", "audio/aac", "audio/webm;codecs=opus",
                                  "audio/ogg", "audio/wav", "audio/mpeg", "Audio/MP4"])
def test_acepta_los_formatos_de_audio(entorno, tipo):  # R1
    assert entorno.dictar(tipo=tipo).status_code == 200


# -- R3: the day of the recording, not of the sending -------------------------------------------

def test_manda_el_dia_de_la_grabacion(entorno):  # R3
    r = entorno.dictar(consulta="?id=a&fecha=2026-03-04")
    assert r.status_code == 200
    assert entorno.interprete.interpretar.call_args.args == (TEXTO, AYER)
    assert r.json()["fecha"] == "2026-03-04"
    assert r.json()["interpretacion"]["alimentos"][0]["dia"] == "2026-03-04"


def test_sin_fecha_es_hoy(entorno):  # R3
    r = entorno.dictar(consulta="?id=a")
    assert r.status_code == 200
    assert entorno.interprete.interpretar.call_args.args == (TEXTO, HOY)
    assert r.json()["fecha"] == "2026-03-05"


@pytest.mark.parametrize("fecha", ["04/03/2026", "2026-13-01", "ayer"])
def test_fecha_mal_escrita_es_400(entorno, fecha):  # R3, R4
    r = entorno.dictar(consulta=f"?id=a&fecha={fecha}")
    assert r.status_code == 400
    assert r.json()["reintentar"] is False
    entorno.transcriptor.transcribir.assert_not_called()
    assert entorno.filas() == VACIAS


# -- R4: each failure says whether to retry, and nothing is saved ------------------------------

def test_sin_transcriptor_reintentar_mas_tarde(tmp_path):  # R4
    e = Entorno(tmp_path, transcriptor=None)
    r = e.dictar()
    assert r.status_code == 503
    assert r.json()["reintentar"] is True and r.json()["error"]
    e.interprete.interpretar.assert_not_called()
    assert e.filas() == VACIAS


def test_gpu_que_falla_reintentar_mas_tarde(entorno):  # R4
    entorno.transcriptor.transcribir.side_effect = ErrorTranscripcion("no_disponible")
    r = entorno.dictar()
    assert r.status_code == 503
    assert r.json()["reintentar"] is True
    entorno.interprete.interpretar.assert_not_called()
    assert entorno.filas() == VACIAS


def test_claude_no_responde_devuelve_el_texto(entorno):  # R4
    entorno.interprete.interpretar.side_effect = ErrorInterpretacion("no hay conexión con la API")
    r = entorno.dictar()
    assert r.status_code == 502
    assert r.json()["texto"] == TEXTO
    assert r.json()["reintentar"] is False
    assert r.json()["error"]
    assert entorno.filas() == VACIAS


@pytest.mark.parametrize("motivo", ["vacio", "audio_no_valido"])
def test_sin_voz_o_no_decodificable_es_422(entorno, motivo):  # R4
    entorno.transcriptor.transcribir.side_effect = ErrorTranscripcion(motivo)
    r = entorno.dictar()
    assert r.status_code == 422
    assert r.json()["reintentar"] is False
    if motivo == "vacio":
        assert "No he oído nada" in r.json()["error"]
    entorno.interprete.interpretar.assert_not_called()
    assert entorno.filas() == VACIAS


def test_cuerpo_vacio_es_422(entorno):  # R4
    r = entorno.dictar(audio=b"")
    assert r.status_code == 422
    assert r.json()["reintentar"] is False
    entorno.transcriptor.transcribir.assert_not_called()


@pytest.mark.parametrize("tipo", ["text/plain", "application/json", "image/jpeg", None])
def test_lo_que_no_es_audio_es_415(entorno, tipo):  # R4
    r = entorno.dictar(tipo=tipo)
    assert r.status_code == 415
    assert r.json()["reintentar"] is False
    entorno.transcriptor.transcribir.assert_not_called()
    assert entorno.filas() == VACIAS


def test_mas_de_10_mb_es_413(entorno):  # R4
    r = entorno.dictar(audio=b"\x00" * (11 * 1024 * 1024))
    assert r.status_code == 413
    assert r.json()["reintentar"] is False
    entorno.transcriptor.transcribir.assert_not_called()
    assert entorno.filas() == VACIAS


def test_10_mb_justos_pasan(entorno):  # R4
    assert entorno.dictar(audio=b"\x00" * (10 * 1024 * 1024)).status_code == 200


# -- R7: the service worker at the root, never caching /api/ -------------------------------------

def test_sw_en_la_raiz_sin_cache_y_solo_el_cascaron(entorno):  # R7
    r = entorno.cliente.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"
    lista = re.search(r"const CASCARON = \[(.*?)\];", r.text, re.S)
    assert lista, "sw.js declares its precache list as `const CASCARON = [...]`"
    rutas = re.findall(r"\"([^\"]+)\"", lista.group(1))
    assert set(rutas) == {"/", "/static/app.js", "/static/cola.js", "/static/dictado.js", "/static/estilos.css",
                          # unit 010 (R13): the weight tab, the icon, the manifest and the settings page
                          "/static/peso.js", "/static/manifest.json", "/static/icono.svg", "/static/icono-32.png",
                          "/static/icono-180.png", "/static/icono-512.png", "/ajustes", "/static/ajustes.js",
                          "/static/recetas.js",  # unit 011: the Recetas tab
                          "/static/balance.js", "/static/grafica.js",  # unit 017: the Balance tab and its chart
                          "/static/buscador.js",  # unit 019: picking from what is saved
                          "/static/balance.css",  # unit 027: the styles of the Balance tab
                          "/static/hoy.js"}  # unit 030: the cards of Hoy
    assert '"/api/"' in r.text  # the fetch handler leaves /api/* to the network
    for ruta in rutas:
        assert entorno.cliente.get(ruta).status_code == 200


def test_la_pagina_registra_el_service_worker_y_tiene_grabar(entorno):  # R5, R7
    assert 'id="grabar"' in entorno.cliente.get("/").text
    assert 'serviceWorker.register("/sw.js")' in entorno.cliente.get("/static/app.js").text


# -- R8: `tridata web` with and without the `voz` extra ------------------------------------------

def _arrancar(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIDATA_BASAL_KCAL", "1600")
    monkeypatch.setenv("TRIDATA_KCAL_POR_PASO", "0.04")
    arrancado = MagicMock()
    monkeypatch.setattr("uvicorn.run", arrancado)
    assert cli._cmd_web("127.0.0.1", 8765, db_path=tmp_path / "data.sqlite3") == 0
    return arrancado.call_args.args[0]


def test_tridata_web_sin_el_extra_voz_arranca_y_avisa(tmp_path, monkeypatch, capsys):  # R8
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # not importable
    app = _arrancar(tmp_path, monkeypatch)
    salida = capsys.readouterr()
    assert "dictado desactivado" in salida.out + salida.err
    cliente = TestClient(app)
    assert cliente.post("/api/dictado", content=AUDIO, headers={"Content-Type": "audio/mp4"}).status_code == 503
    assert cliente.get("/api/dia").status_code == 200  # writing still works


def test_tridata_web_con_el_extra_voz_carga_en_segundo_plano(tmp_path, monkeypatch):  # R8
    monkeypatch.setitem(sys.modules, "faster_whisper", types.ModuleType("faster_whisper"))
    cargado = threading.Event()
    soltar = threading.Event()
    hilos = []

    def cargar(self):
        hilos.append(threading.current_thread())
        cargado.set()
        soltar.wait(5)

    monkeypatch.setattr("tridata.ia.whisper.TranscriptorWhisper.cargar", cargar)
    app = _arrancar(tmp_path, monkeypatch)
    assert not cargado.is_set()  # building the app loads nothing
    try:
        with TestClient(app) as cliente:  # the server starts without waiting for the model
            assert cliente.get("/api/dia").status_code == 200
            assert cargado.wait(5)
    finally:
        soltar.set()
    assert hilos and hilos[0] is not threading.main_thread()
