"""Unit 074 (R3, R4): change a logged food into a saved recipe or ingredient from its change card.

POST /api/alimento/N with `guardado: {"tipo", "id"}` goes through the same `_con_lo_guardado` as the add by hand
(unit 019): name, kcal, origin and provisional are the server's for the quantity, never the page's; the row is the
same one (id, day, section). Gone is a 422 «ya no está guardado» and nothing changes. Without `guardado`, as today.

Recetario over a temporary SQLite and the Starlette TestClient; the Claude interpreter is a MagicMock (no API).
Every value is invented. Plans: `apuntar-comida` R-213 (C-263).
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas
from tridata.nutricion.recetas import Recetario
from tridata.web import crear_app

HOY = date(2026, 10, 21)


class Entorno:
    """«Lentejas de prueba 100 g» logged at dinner, and a saved recipe «Frijoles de prueba» (70 kcal/100 g)."""

    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.registro = RegistroComidas(self.db)
        ajustes = Ajustes(self.db)
        ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
        self.recetario = Recetario(self.db)
        self.frijol = self.recetario.crear_fijo({"nombre": "frijol seco de prueba", "kcal": 350, "por": "100g"})
        self.receta = self.recetario.crear_receta({"nombre": "Frijoles de prueba", "peso_neto_g": 1000, "ingredientes": [
            {"nombre": "frijol seco de prueba", "cantidad": 200, "unidad": "g", "kcal": 1,
             "guardado": "frijol seco de prueba"}]}, HOY)
        self.lentejas = self.registro.anadir(Alimento(nombre="Lentejas de prueba", dia=HOY, cantidad=100, unidad="g",
                                                      kcal=116), hoy=HOY)
        self.registro.mover_seccion(self.lentejas["id"], "cena")
        self.cliente = TestClient(crear_app(self.registro, MagicMock(), ajustes, hoy=lambda: HOY,
                                            recetario=self.recetario))

    def cambiar(self, cuerpo: dict):
        return self.cliente.post(f"/api/alimento/{self.lentejas['id']}", json=cuerpo)

    def filas(self) -> list[tuple]:
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT * FROM alimento_apuntado ORDER BY id").fetchall()


@pytest.fixture
def entorno(tmp_path) -> Entorno:
    return Entorno(tmp_path)


def test_cambiar_por_una_receta_guardada_en_la_misma_fila(entorno):  # R3 · C-263
    r = entorno.cambiar({"nombre": "Frijoles de prueba", "cantidad": 150, "unidad": "g", "kcal": 1,
                         "dia": HOY.isoformat(), "guardado": {"tipo": "receta", "id": entorno.receta["id"]}})
    assert r.status_code == 200, r.text
    a = r.json()["alimento"]
    assert a["id"] == entorno.lentejas["id"]
    assert (a["nombre"], a["cantidad"], a["unidad"], a["kcal"]) == ("Frijoles de prueba", 150, "g", 105)  # not 1
    assert (a["dia"], a["seccion"], a["estado"]) == (HOY.isoformat(), "cena", "comido")
    assert a["origen"] == "de tu receta «Frijoles de prueba»"
    assert a["estimado"] is False
    assert len(entorno.filas()) == 1  # nothing deleted, nothing logged again
    assert entorno.registro.apuntes_del_dia(HOY)[0]["nombre"] == "Frijoles de prueba"


def test_cambiar_por_un_ingrediente_guardado(entorno):  # R3
    r = entorno.cambiar({"nombre": "frijol seco de prueba", "cantidad": 50, "unidad": "g", "dia": HOY.isoformat(),
                         "guardado": {"tipo": "fijo", "id": entorno.frijol["id"]}})
    assert r.status_code == 200, r.text
    a = r.json()["alimento"]
    assert (a["id"], a["nombre"], a["kcal"], a["seccion"]) == (entorno.lentejas["id"], "frijol seco de prueba", 175,
                                                               "cena")


def test_receta_que_ya_no_esta_guardada_es_422_y_no_cambia_nada(entorno):  # R4
    receta_id = entorno.receta["id"]
    entorno.recetario.borrar_receta(receta_id, confirmado=True)
    antes = entorno.filas()
    r = entorno.cambiar({"nombre": "Frijoles de prueba", "cantidad": 150, "unidad": "g", "dia": HOY.isoformat(),
                         "guardado": {"tipo": "receta", "id": receta_id}})
    assert r.status_code == 422
    assert "ya no está guardado" in r.json()["error"]
    assert entorno.filas() == antes


def test_guardado_mal_formado_es_400_y_no_cambia_nada(entorno):  # R4
    antes = entorno.filas()
    r = entorno.cambiar({"nombre": "Frijoles de prueba", "cantidad": 150, "unidad": "g", "dia": HOY.isoformat(),
                         "guardado": {"tipo": "otra", "id": 1}})
    assert r.status_code == 400
    assert entorno.filas() == antes


def test_sin_guardado_como_hoy(entorno):  # R3: without choosing, the card works as before
    r = entorno.cambiar({"nombre": "Lentejas de prueba", "cantidad": 200, "unidad": "g", "kcal": 232,
                         "dia": HOY.isoformat()})
    assert r.status_code == 200, r.text
    a = r.json()["alimento"]
    assert (a["id"], a["nombre"], a["kcal"], a["seccion"], a["origen"]) == (entorno.lentejas["id"],
                                                                            "Lentejas de prueba", 232, "cena", None)
