"""The service worker of the phone (unit 006), GET /sw.js.

Unit 080 (P-20261105-cf32bea5, S-5): dictation is gone from the app (/api/dictado, the recordings queue and
Whisper); every test that only tested it was removed with it (see the unit's hallazgos.md). What stays here is the
service worker that serves the page offline, without the retired modules. Invented data.
"""
import re
from datetime import date

import pytest
from starlette.testclient import TestClient

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, RegistroComidas
from tridata.web import crear_app

HOY = date(2026, 3, 5)


@pytest.fixture
def cliente(tmp_path):
    db = tmp_path / "data.sqlite3"
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
    return TestClient(crear_app(RegistroComidas(db), None, ajustes, hoy=lambda: HOY))


def test_sw_en_la_raiz_sin_cache_y_solo_el_cascaron(cliente):  # R7
    r = cliente.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"
    lista = re.search(r"const CASCARON = \[(.*?)\];", r.text, re.S)
    assert lista, "sw.js declares its precache list as `const CASCARON = [...]`"
    rutas = re.findall(r"\"([^\"]+)\"", lista.group(1))
    assert set(rutas) == {"/", "/static/app.js", "/static/estilos.css",
                          # unit 010 (R13): the weight tab, the icon, the manifest and the settings page
                          "/static/peso.js", "/static/manifest.json", "/static/icono.svg", "/static/icono-32.png",
                          "/static/icono-180.png", "/static/icono-512.png", "/ajustes", "/static/ajustes.js",
                          "/static/recetas.js",  # unit 011: the Recetas tab
                          "/static/balance.js", "/static/grafica.js",  # unit 017: the Balance tab and its chart
                          "/static/buscador.js",  # unit 019: picking from what is saved
                          "/static/balance.css",  # unit 027: the styles of the Balance tab
                          "/static/hoy.js",  # unit 030: the cards of Hoy
                          "/static/fitness.js",  # unit 031: the Fitness tab
                          "/static/plan.js",  # unit 040: Fitness › Plan
                          "/static/suplementacion.js"}  # unit 082: the block «Suplementación» of Nutrición › Día
    # unit 080: cola.js, dictado.js, asistente.js and conversaciones.js are retired
    assert '"/api/"' in r.text  # the fetch handler leaves /api/* to the network
    for ruta in rutas:
        assert cliente.get(ruta).status_code == 200


def test_la_pagina_registra_el_service_worker_sin_grabar(cliente):  # R7 · unit 080 (R2): no button to dictate
    assert 'id="grabar"' not in cliente.get("/").text
    assert 'serviceWorker.register("/sw.js")' in cliente.get("/static/app.js").text
