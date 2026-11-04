"""Unit 073: in the .md of a plan the sessions go by day, also after one is moved (P-20261104-76de1f75, point 4).

Invented data only: a plan made by hand in a temporary SQLite."""

from __future__ import annotations

import re

from test_plan import HOY, _con_semanas
from tridata.plan.markdown import escribir


def _dias_en_md(md: str) -> list[str]:
    return re.findall(r"^### (\d{4}-\d{2}-\d{2}) · ", md, flags=re.M)


def test_una_sesion_movida_sale_en_su_dia(tmp_path):  # 073 · R1
    planes, ident = _con_semanas(tmp_path, 3)
    primera = planes.anadir_sesion(ident, "2026-10-27", "Rodaje de prueba", ["40' suave"], HOY)
    planes.anadir_sesion(ident, "2026-10-29", "Series de prueba", ["6×400"], HOY)
    planes.anadir_sesion(ident, "2026-11-01", "Tirada de prueba", ["1h30 suave"], HOY)
    planes.mover_sesion(primera, "2026-10-31", HOY)  # the first one, two days after the second
    md = escribir(planes.leer(ident), None, HOY)
    assert _dias_en_md(md) == ["2026-10-29", "2026-10-31", "2026-11-01"]


def test_las_del_mismo_dia_y_sin_mover_salen_como_antes(tmp_path):  # 073 · R2
    planes, ident = _con_semanas(tmp_path, 2)
    planes.anadir_sesion(ident, "2026-10-28", "Natación de prueba", ["2000 m"], HOY)
    planes.anadir_sesion(ident, "2026-10-28", "Bici de prueba", ["1h"], HOY)
    planes.anadir_sesion(ident, "2026-10-30", "Rodaje de prueba", ["40'"], HOY)
    md = escribir(planes.leer(ident), None, HOY)
    assert re.findall(r"^### \S+ · (.+)$", md, flags=re.M) == ["Natación de prueba", "Bici de prueba",
                                                                 "Rodaje de prueba"]
