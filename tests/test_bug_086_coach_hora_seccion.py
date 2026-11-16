"""Bug 086 (P-20261115-3dde47f9): the coach could not change the hour of a section that already has food.

R-229 promises it («cuando Andrés … se la dice al equipo, deberá guardarla para ese día sin tocar las kcal»), but from
the PC the hour was only kept when logging NEW food (`apuntar_comida` with `seccion` and `hora`). Now `hora_seccion`
goes the usual way: `preparar_cambio` shows the hour before and the new one, and `hacer_cambio` with its fingerprint
keeps it.

Every datum is invented; Garmin and Google Calendar are not connected; nothing talks to a paid service.
"""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime

import pytest

from tridata.conversaciones.desde_pc import montar_equipo
from tridata.mcp_server import build_server
from tridata.nutricion import Alimento, RegistroComidas
from tridata.nutricion.suplementacion import Suplementacion
from tridata.storage import DataStore

HOY = date(2026, 11, 15)
AHORA = datetime(2026, 11, 15, 9, 0)
DIA = "2026-11-14"
HUELLA = re.compile(r"Huella: ([0-9a-f]{12})")


class Mundo:
    """On the 14th: a banana at merienda, bread and fish at cena, nothing at comida."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.registro = RegistroComidas(self.db)
        for nombre, cantidad, kcal, seccion in (("banana", 80, 71, "merienda"), ("pan inventado", 40, 100, "cena"),
                                                ("merluza inventada", 150, 120, "cena")):
            self.registro.guardar([Alimento(nombre=nombre, dia=date.fromisoformat(DIA), cantidad=cantidad,
                                            unidad="g", kcal=kcal)],
                                  hoy=HOY, texto=nombre, sin_cambios=True, seccion=seccion)
        store = DataStore(self.db)
        equipo = montar_equipo(self.db, store=store, token=tmp_path / "google-token.json",
                               hoy=lambda: HOY, ahora=lambda: AHORA)
        self.server = build_server(store, equipo=equipo)

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

    def horas(self) -> dict:
        return Suplementacion(self.db).horas_del_dia(date.fromisoformat(DIA))

    def kcal(self) -> float:
        return sum(a["kcal"] for a in self.registro.apuntes_del_dia(date.fromisoformat(DIA)))


def hora(seccion: str, la_hora: str, dia: str = DIA) -> dict:
    return {"accion": "hora_seccion", "texto": seccion, "hora": la_hora, "dia": dia}


@pytest.fixture
def mundo(tmp_path) -> Mundo:
    return Mundo(tmp_path)


def test_ensena_la_hora_de_antes_y_la_nueva_con_su_huella(mundo):  # R1
    texto = mundo.preparar(hora("merienda", "16:00"))
    assert HUELLA.search(texto), texto
    assert "Merienda" in texto and "16:00" in texto and "18:00" in texto, texto
    assert mundo.horas()["merienda"] == {"hora": "18:00", "aproximada": True}  # preparing changes nothing


def test_con_el_si_guarda_merienda_y_cena_sin_tocar_las_kcal(mundo):  # R1 (portante)
    antes = mundo.kcal()
    assert mundo.hacer(hora("merienda", "16:00")).startswith("Hecho")
    assert mundo.hacer(hora("Cena", "19:30")).startswith("Hecho")
    horas = mundo.horas()
    assert horas["merienda"] == {"hora": "16:00", "aproximada": False}
    assert horas["cena"] == {"hora": "19:30", "aproximada": False}
    assert mundo.kcal() == antes
    contexto = mundo.llamar("get_contexto", {})
    assert "16:00" in contexto and "19:30" in contexto


@pytest.mark.parametrize("cambio", [
    hora("merienda", "25:00"), hora("merienda", "tarde"), hora("merienda", ""), hora("postre", "16:00"),
    hora("sin sección", "16:00"), hora("", "16:00"),
])
def test_hora_o_seccion_que_no_vale_dice_que_no_y_no_hace_nada(mundo, cambio):  # R2
    texto = mundo.preparar(cambio)
    assert not HUELLA.search(texto), texto
    assert "No se puede" in texto or "No " in texto, texto
    assert all(h["aproximada"] for h in mundo.horas().values() if h["hora"])


def test_una_seccion_sin_comida_ese_dia_dice_que_no(mundo):  # R3
    texto = mundo.preparar(hora("comida", "14:00"))
    assert not HUELLA.search(texto), texto
    assert "Comida" in texto, texto
    assert mundo.horas()["comida"]["aproximada"] is True


def test_la_misma_hora_que_ya_tiene_dice_ya_esta(mundo):  # R4
    assert mundo.hacer(hora("merienda", "16:00")).startswith("Hecho")
    texto = mundo.preparar(hora("merienda", "16:00"))
    assert not HUELLA.search(texto), texto
    assert "ya" in texto, texto


def test_apuntar_comida_con_hora_sigue_igual(mundo):  # R4
    texto = mundo.llamar("apuntar_comida", {"papel": "nutricionista", "dia": DIA, "seccion": "comida",
                                            "hora": "14:15", "texto": "arroz",
                                            "alimentos": [{"nombre": "arroz inventado", "cantidad": 100,
                                                           "unidad": "g", "kcal": 130}]})
    assert texto.startswith("Hecho"), texto
    assert mundo.horas()["comida"] == {"hora": "14:15", "aproximada": False}
