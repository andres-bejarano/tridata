"""Unit 031: GET /api/fitness, GET /api/actividad/{id} and «Pulso y zonas» through /api/ajustes.

Plans `ver-fitness` REC-19: R-105..R-111 · C-146..C-152 · G-47, G-49. R1-R8 of the unit over a seeded SQLite (the
`sembrar` of test_fitness.py). Every value is invented.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient
from test_fitness import AYER, HOY, PULSO_A, PULSO_B, ZONAS_A, sembrar

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroComidas
from tridata.storage import DataStore
from tridata.web import crear_app


@pytest.fixture(autouse=True)
def _entorno(monkeypatch):
    monkeypatch.setenv("TRIDATA_HR_MAX", "190")
    monkeypatch.setenv("TRIDATA_HR_REST", "60")


class Web:
    def __init__(self, tmp_path) -> None:
        db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                             desde=DESDE_SIEMPRE)
        self.store = DataStore(db)
        sembrar(self.store)
        self.cliente = TestClient(crear_app(RegistroComidas(db), MagicMock(), self.ajustes, hoy=lambda: HOY,
                                            objetivos=ObjetivosDia(db), store=self.store,
                                            actualizar_garmin=MagicMock(), ahora=lambda: datetime(2026, 10, 21, 10)))


@pytest.fixture
def web(tmp_path) -> Web:
    return Web(tmp_path)


def test_api_fitness_los_tres_bloques(web):  # R1, R2, R4, R5 · C-146, C-147, C-148, C-150
    r = web.cliente.get("/api/fitness")
    assert r.status_code == 200
    f = r.json()
    assert f["fecha"] == HOY.isoformat()
    assert len(f["carga"]["serie"]) == 14 and f["carga"]["hoy"]["fecha"] == HOY.isoformat()
    assert f["carga"]["pulso"] == {"confirmada": False, "texto": "de la configuración: confírmala en Ajustes"}
    u = f["ultima_actividad"]
    assert (u["actividad_id"], u["deporte"], u["fecha"], u["vueltas"], u["clima"]) == (
        "c-ayer", "Carrera", AYER.isoformat(), 10, True)
    rec = f["recuperacion"]
    assert rec["sueno"]["estado"] == "incompleto"
    assert rec["pulso_reposo"]["estado"] == "no_ha_llegado" and rec["pulso_reposo"]["valor"] is None
    assert rec["hrv"]["estado"] == "ok"


def test_api_actividad_vueltas_zona_y_clima(web):  # R3 · C-152
    web.ajustes.cambiar(PULSO_A, desde=DESDE_SIEMPRE)
    r = web.cliente.get("/api/actividad/c-ayer")
    assert r.status_code == 200
    d = r.json()
    assert [v["zona"] for v in d["vueltas"]] == [2, 2, 2, 2, 3, 3, 3, 4, 4, 5]
    assert d["clima"]["condition"] == "Despejado"
    assert web.cliente.get("/api/actividad/no-existe").status_code == 404


def test_pulso_y_zonas_por_ajustes_y_su_texto_en_fitness(web):  # R6 · C-149
    r = web.cliente.post("/api/ajustes", json={"datos": PULSO_A, "desde": "2026-10-01"})
    assert r.status_code == 200
    vigentes = r.json()["ajustes"]["vigentes"]
    assert vigentes["fc_max"] == {"valor": 180, "desde": "2026-10-01"}
    assert vigentes["zonas_fc"]["valor"] == ZONAS_A
    assert vigentes["fc_origen"]["valor"] == {"tipo": "prueba", "mes": "2025-11"}
    pulso = web.cliente.get("/api/fitness").json()["carga"]["pulso"]
    assert pulso == {"confirmada": True, "texto": "de tu prueba de esfuerzo de 2025-11, desde el 1/10"}


def test_fc_no_valida_no_se_guarda_y_dice_por_que(web):  # R7
    r = web.cliente.post("/api/ajustes", json={"datos": {**PULSO_A, "fc_reposo": 90, "fc_max": 85}, "desde": "2026-10-01"})
    assert r.status_code == 400
    assert r.json()["error"].startswith("No se ha guardado nada:")
    assert web.cliente.get("/api/ajustes").json()["vigentes"]["fc_max"] == {"valor": None, "desde": None}


def test_hoy_y_fitness_dan_la_misma_frescura(web):  # R8
    web.ajustes.cambiar(PULSO_A, desde=DESDE_SIEMPRE)
    web.ajustes.cambiar(PULSO_B, desde="2026-10-15")
    hoy = web.cliente.get("/api/hoy").json()["como_llegas"]["frescura"]["valor"]
    assert hoy == web.cliente.get("/api/fitness").json()["carga"]["hoy"]["tsb"]


def test_la_pagina_trae_fitness(web):  # R1-R4 · the client
    html = web.cliente.get("/").text
    for id_ in ("fitness-carga", "fitness-grafica", "fitness-ultima", "fitness-detalle", "fitness-recuperacion",
                "fitness-pulso"):
        assert f'id="{id_}"' in html
    assert web.cliente.get("/static/fitness.js").status_code == 200
    assert "fitness.js" in web.cliente.get("/static/app.js").text
    assert "graficaCarga" in web.cliente.get("/static/grafica.js").text
    ajustes = web.cliente.get("/ajustes").text
    assert "Pulso y zonas" in ajustes and 'id="pulso"' in ajustes
