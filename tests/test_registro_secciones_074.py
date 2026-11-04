"""Unit 074 (R1): the sixth section «Nutrición en carrera» (`carrera`), after dinner, and `guardar` into a section.

`RegistroComidas` over a temporary SQLite. Every value is invented (a gel de prueba).
Plans: `apuntar-comida` R-212 (C-262), S-43, S-71 · `preguntar-equipo` R-214.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from tridata.nutricion import Alimento, RegistroComidas
from tridata.nutricion.registro import SECCIONES

HOY = date(2026, 10, 21)


def _gel(**campos) -> Alimento:
    return Alimento(**{"nombre": "gel de prueba", "dia": HOY, "cantidad": 1, "unidad": "unidad", "kcal": 100,
                       **campos})


@pytest.fixture
def registro(tmp_path) -> RegistroComidas:
    return RegistroComidas(tmp_path / "data.sqlite3")


def _filas(db: Path) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT * FROM alimento_apuntado ORDER BY id").fetchall()


def test_seis_secciones_en_el_orden_del_dia():  # R1 · R-212, S-43
    assert SECCIONES == ("desayuno", "media_manana", "comida", "merienda", "cena", "carrera")


def test_mover_a_carrera_vale_y_solo_cambia_la_seccion(registro):  # R1 · C-262
    gel = registro.anadir(_gel(), hoy=HOY)
    movido = registro.mover_seccion(gel["id"], "carrera")
    assert movido["seccion"] == "carrera"
    assert (movido["kcal"], movido["cantidad"], movido["estado"]) == (100, 1, "comido")
    assert registro.apuntes_del_dia(HOY)[0]["seccion"] == "carrera"


@pytest.mark.parametrize("mala", ["Nutrición en carrera", "carreras", "brunch", ""])
def test_una_que_no_es_de_las_seis_sigue_dando_error(registro, tmp_path, mala):  # R1
    gel = registro.anadir(_gel(), hoy=HOY)
    antes = _filas(tmp_path / "data.sqlite3")
    with pytest.raises(ValueError, match="sección"):
        registro.mover_seccion(gel["id"], mala)
    assert _filas(tmp_path / "data.sqlite3") == antes


def test_guardar_sin_seccion_como_siempre(registro):  # S-42: dictation and «Añadir a mano», without section
    guardados = registro.guardar([_gel()], hoy=HOY, texto="un gel", sin_cambios=True)
    assert [g["seccion"] for g in guardados] == [None]


@pytest.mark.parametrize("seccion", SECCIONES)
def test_guardar_en_una_seccion_la_pone_en_cada_alimento(registro, seccion):  # R-214 (what the team uses)
    guardados = registro.guardar([_gel(), _gel(nombre="bebida de prueba", cantidad=500, unidad="ml", kcal=120)],
                                 hoy=HOY, texto="dos cosas", sin_cambios=False, seccion=seccion)
    assert [g["seccion"] for g in guardados] == [seccion, seccion]
    assert [a["seccion"] for a in registro.apuntes_del_dia(HOY)] == [seccion, seccion]


@pytest.mark.parametrize("mala", ["brunch", "Cena", "", 3])
def test_guardar_en_una_seccion_que_no_existe_no_guarda_nada(registro, tmp_path, mala):  # R-214: all or nothing
    registro.anadir(_gel(), hoy=HOY)
    antes = _filas(tmp_path / "data.sqlite3")
    with sqlite3.connect(tmp_path / "data.sqlite3") as conn:
        interpretaciones = conn.execute("SELECT COUNT(*) FROM interpretacion").fetchone()
    with pytest.raises(ValueError, match="sección"):
        registro.guardar([_gel()], hoy=HOY, texto="un gel", sin_cambios=False, seccion=mala)
    assert _filas(tmp_path / "data.sqlite3") == antes
    with sqlite3.connect(tmp_path / "data.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM interpretacion").fetchone() == interpretaciones
