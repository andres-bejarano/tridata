"""Unit 077 (P-20261105-243cea10, R-131, R-132, R-220 of `llevar-plan`): the hour of a session comes back when its
.md is imported, and `Planes.crear` keeps it.

Since unit 076 «Bajar .md» writes «### 2026-11-06 · 20:00 · Rodaje». The AI is always doubled
(`test_plan_importar.OrdenadorFalso`, which reads the «### fecha · título» line as it is asked to). Invented data only,
in a temporary SQLite.
"""
from __future__ import annotations

from test_plan import HOY, _con_semanas
from test_plan_importar import AHORA, OrdenadorFalso
from tridata.plan import Planes
from tridata.plan import importar
from tridata.plan.markdown import escribir

_MD = """# Plan de prueba

## Sesiones clave

### 2026-11-06 · 20:00 · Rodaje de prueba
- 45' a 5:40

### 2026-11-07 · 7:30 · Series de prueba
- 6×400

### 2026-11-08 · Tirada de prueba
- 1h30 suave

### 2026-11-09 · 25:00 · Fartlek de prueba
- 10×1'
"""


def _sesiones(texto: str) -> list[dict]:
    return importar.proponer(texto, OrdenadorFalso(), None, HOY)["sesiones"]


def test_la_hora_del_md_vuelve_y_el_titulo_queda_limpio():  # R1
    s = _sesiones(_MD)
    assert [(x["fecha"], x.get("hora"), x["titulo"]) for x in s[:3]] == [
        ("2026-11-06", "20:00", "Rodaje de prueba"),
        ("2026-11-07", "07:30", "Series de prueba"),
        ("2026-11-08", None, "Tirada de prueba"),  # without an hour in the .md, without one, as before
    ]


def test_una_hora_que_no_vale_se_queda_en_el_titulo():  # R2 · R-132: nothing of the .md is lost
    fartlek = _sesiones(_MD)[3]
    assert fartlek.get("hora") is None
    assert fartlek["titulo"] == "25:00 · Fartlek de prueba"


def test_crear_guarda_la_hora_y_leer_solo_la_da_si_la_hay(tmp_path):  # R3
    planes = Planes(tmp_path / "data.sqlite3")
    p = importar.proponer(_MD, OrdenadorFalso(), None, HOY)
    ident = importar.confirmar(p, {}, planes, AHORA)
    leidas = planes.leer(ident)["sesiones"]
    assert [x.get("hora") for x in leidas] == ["20:00", "07:30", None, None]
    assert "hora" not in leidas[2]  # as in unit 076: the key only when there is an hour


def test_bajar_importar_bajar_da_las_mismas_sesiones(tmp_path):  # R3 · R-220: the round trip stays exact
    planes, ident = _con_semanas(tmp_path, 3)
    planes.anadir_sesion(ident, "2026-10-28", "Rodaje de prueba", ["45' a 5:40"], HOY, hora="20:00")
    planes.anadir_sesion(ident, "2026-10-30", "Tirada de prueba", ["1h30 suave"], HOY)
    antes = escribir(planes.leer(ident), None, HOY)
    assert "### 2026-10-28 · 20:00 · Rodaje de prueba" in antes

    otra = Planes(tmp_path / "otra.sqlite3")
    nuevo = importar.confirmar(importar.proponer(antes, OrdenadorFalso(), None, HOY), {}, otra, AHORA)
    despues = escribir(otra.leer(nuevo), None, HOY)

    def sesiones(md: str) -> list[str]:
        return [linea for linea in md.splitlines() if linea.startswith("### ")]

    assert sesiones(despues) == sesiones(antes)
    # and not because the hour went into the title: it is the session's hour again
    assert [(x["titulo"], x.get("hora")) for x in otra.leer(nuevo)["sesiones"]] == [
        ("Rodaje de prueba", "20:00"), ("Tirada de prueba", None)]
