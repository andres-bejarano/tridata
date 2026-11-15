"""Bug 085 (P-20261115-0e0d7502): with two foods of the same name on one day, the coach could not say which one to
change, remove or move. `preparar_cambio` answered «Hay más de uno … prepáralo otra vez diciéndolo», but the change
had no field to say it. Now each candidate comes with its number (its id), section, quantity and kcal, and the change
says which one with `eleccion` (as R-210 does with two activities of the same name).

Every datum is invented; Garmin and Google Calendar are not connected; nothing talks to a paid service.
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import date, datetime

import pytest

from tridata.conversaciones.desde_pc import montar_equipo
from tridata.mcp_server import build_server
from tridata.nutricion import Alimento, RegistroComidas
from tridata.storage import DataStore

HOY = date(2026, 11, 15)
AHORA = datetime(2026, 11, 15, 9, 0)
DIA = "2026-11-14"
HUELLA = re.compile(r"Huella: ([0-9a-f]{12})")


class Mundo:
    """Two «banana» on the 14th (80 g at merienda, 57 g at cena), a yogurt that day, and one banana on the 13th."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.registro = RegistroComidas(self.db)

        def guardar(nombre: str, dia: str, cantidad: float, kcal: float, seccion: str) -> int:
            (fila,) = self.registro.guardar([Alimento(nombre=nombre, dia=date.fromisoformat(dia), cantidad=cantidad,
                                                      unidad="g", kcal=kcal)],
                                            hoy=HOY, texto=nombre, sin_cambios=True, seccion=seccion)
            return fila["id"]

        self.merienda = guardar("banana", DIA, 80, 71, "merienda")
        self.cena = guardar("banana", DIA, 57, 51, "cena")
        self.yogur = guardar("yogur inventado", DIA, 125, 75, "merienda")
        self.otro_dia = guardar("banana", "2026-11-13", 100, 89, "desayuno")
        store = DataStore(self.db)
        self.equipo = montar_equipo(self.db, store=store, token=tmp_path / "google-token.json",
                                    hoy=lambda: HOY, ahora=lambda: AHORA)
        self.server = build_server(store, equipo=self.equipo)

    def llamar(self, nombre: str, argumentos: dict) -> str:
        resultado = asyncio.run(self.server.call_tool(nombre, argumentos))
        assert not resultado.is_error, resultado
        return "".join(c.text for c in resultado.content)

    def preparar(self, cambio: dict) -> str:
        return self.llamar("preparar_cambio", {"cambio": cambio})

    def hacer(self, cambio: dict) -> str:
        texto = self.preparar(cambio)
        encontrada = HUELLA.search(texto)
        assert encontrada, texto
        return self.llamar("hacer_cambio", {"papel": "nutricionista", "cambio": cambio,
                                            "huella": encontrada.group(1)})

    def fila(self, ident: int) -> dict | None:
        try:
            return self.registro.alimento(ident)
        except KeyError:
            return None

    def volcado(self) -> list[str]:
        conn = sqlite3.connect(self.db)
        try:
            return list(conn.iterdump())
        finally:
            conn.close()


@pytest.fixture
def mundo(tmp_path) -> Mundo:
    return Mundo(tmp_path)


CAMBIAR = {"accion": "cambiar_alimento", "objeto": "banana", "dia": DIA, "cantidad": 36, "unidad": "g"}
QUITAR = {"accion": "quitar_alimento", "objeto": "banana", "dia": DIA}
MOVER = {"accion": "mover_alimento", "objeto": "banana", "dia": DIA, "fecha": "2026-11-16"}


def test_sin_decir_cual_ensena_los_dos_con_su_numero_seccion_y_cantidad(mundo):  # R1
    antes = mundo.volcado()
    texto = mundo.preparar(CAMBIAR)
    assert "Hay más de uno" in texto and not HUELLA.search(texto)
    for numero, seccion, cuanto in ((mundo.merienda, "Merienda", "80 g (71 kcal)"),
                                    (mundo.cena, "Cena", "57 g (51 kcal)")):
        assert re.search(rf"número {numero}\b[^;]*{re.escape(cuanto)}[^;]*{seccion}", texto), texto
    assert "eleccion" in texto  # it says how to say which one
    assert mundo.volcado() == antes


def test_con_el_numero_de_la_merienda_cambia_esa_y_no_la_de_la_cena(mundo):  # R1 (portante)
    elegido = {**CAMBIAR, "eleccion": mundo.merienda}
    texto = mundo.preparar(elegido)
    assert HUELLA.search(texto), texto
    assert "80 g" in texto and "36 g" in texto
    hecho = mundo.hacer(elegido)
    assert hecho.startswith("Hecho"), hecho
    assert mundo.fila(mundo.merienda)["cantidad"] == 36
    assert mundo.fila(mundo.cena)["cantidad"] == 57 and mundo.fila(mundo.cena)["kcal"] == 51


def test_quitar_con_el_numero_quita_justo_ese(mundo):  # R2
    hecho = mundo.hacer({**QUITAR, "eleccion": mundo.cena})
    assert hecho.startswith("Hecho"), hecho
    assert mundo.fila(mundo.cena) is None
    assert mundo.fila(mundo.merienda)["cantidad"] == 80


def test_mover_con_el_numero_mueve_justo_ese(mundo):  # R2
    hecho = mundo.hacer({**MOVER, "eleccion": mundo.merienda})
    assert hecho.startswith("Hecho"), hecho
    assert mundo.fila(mundo.merienda)["dia"] == "2026-11-16"
    assert mundo.fila(mundo.cena)["dia"] == DIA


@pytest.mark.parametrize("cambio", [CAMBIAR, QUITAR, MOVER])
@pytest.mark.parametrize("cual", ["otro_dia", "yogur", "inventado"])
def test_un_numero_que_no_es_de_los_candidatos_no_hace_nada_y_lo_dice(mundo, cambio, cual):  # R3
    numero = 99999 if cual == "inventado" else getattr(mundo, cual)
    elegido = {**cambio, "eleccion": numero}
    antes = mundo.volcado()
    texto = mundo.preparar(elegido)
    assert texto.startswith("No se puede") and str(numero) in texto and not HUELLA.search(texto), texto
    hecho = mundo.llamar("hacer_cambio", {"papel": "nutricionista", "cambio": elegido, "huella": "000000000000"})
    assert not hecho.startswith("Hecho"), hecho
    assert mundo.volcado() == antes


def test_con_un_solo_alimento_de_ese_nombre_todo_sigue_como_hoy(mundo):  # R4
    texto = mundo.preparar({"accion": "cambiar_alimento", "objeto": "yogur inventado", "dia": DIA, "cantidad": 100})
    assert HUELLA.search(texto) and "número" not in texto, texto
    hecho = mundo.hacer({"accion": "quitar_alimento", "objeto": "yogur inventado", "dia": DIA})
    assert hecho.startswith("Hecho"), hecho
    assert mundo.fila(mundo.yogur) is None
