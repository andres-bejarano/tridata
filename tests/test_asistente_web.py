"""The assistant that acts (unit 021) through the web.

Unit 080 (P-20261105-cf32bea5, S-5): interpreting, dictating and the cards of the assistant (/api/interpretar,
/api/dictado, /api/asistente/*) are gone from the app; the coach makes those cards from the PC with the same functions
(`nutricion.asistente`, tested in test_asistente*.py and test_mcp_equipo.py). What stays here is the hand path the
cards were compared with: the steps of a day changed and put back by hand. Every value is invented.
"""
from __future__ import annotations

from datetime import date

import pytest
from starlette.testclient import TestClient

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroComidas
from tridata.web import crear_app

HOY = date(2026, 10, 22)


class Web:
    def __init__(self, tmp_path):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1980, "kcal_por_paso": 0.04, "pasos_por_defecto": 8000},
                             desde=DESDE_SIEMPRE)
        self.cliente = TestClient(crear_app(RegistroComidas(self.db), None, self.ajustes, hoy=lambda: HOY,
                                            objetivos=ObjetivosDia(self.db)))

    def pasos(self, dia=HOY):
        return self.cliente.get(f"/api/dia?fecha={dia.isoformat()}").json()["gasto"]["pasos"]

    def post(self, ruta, cuerpo):
        return self.cliente.post(ruta, json=cuerpo)


@pytest.fixture
def w(tmp_path):
    return Web(tmp_path)


def test_a_mano_los_pasos_tambien_se_pueden_cambiar_y_volver(w):  # R12: «a mano», the same method
    assert w.post("/api/dia/objetivo", {"pasos": 12000}).status_code == 200
    assert w.pasos() == 12000
    assert w.post("/api/dia/objetivo", {"pasos": 80000}).status_code == 400
    assert w.post("/api/dia/objetivo", {"pasos": None}).status_code == 200
    assert w.pasos() == 8000
