"""E2E of bug 021: changing the quantity of a saved food works its kcal out again.

The change card scales the kcal with the quantity (kcal ÷ old quantity × new one) while the unit stays
the same and the kcal have not been typed by hand; kcal typed by hand win. Every value is invented.
"""
from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.nutricion import Alimento  # noqa: E402

HOY = date(2026, 10, 21)  # the same Wednesday as conftest.py


def _con_avena(servidor, abrir):
    servidor.registro.anadir(Alimento(nombre="Avena de prueba", dia=HOY, cantidad=100, unidad="g", kcal=380), hoy=HOY)
    p = abrir(servidor)
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    p.locator("#lista-dia .fila", has_text="Avena de prueba").click()
    return p, p.locator("#lista-dia .tarjeta")


def _kcal_guardadas(p, servidor) -> float:
    return p.request.get(servidor.url + "/api/dia").json()["alimentos"][0]["kcal"]


def test_cambiar_la_cantidad_recalcula_las_kcal(servidor, abrir):
    p, ficha = _con_avena(servidor, abrir)
    ficha.get_by_label("Cantidad", exact=True).fill("200")
    expect(ficha.get_by_label("kcal", exact=True)).to_have_value("760")
    ficha.get_by_role("button", name="Guardar cambios").click()
    expect(p.locator("#lista-dia .fila", has_text="Avena de prueba").locator(".kcal")).to_have_text("760")
    assert _kcal_guardadas(p, servidor) == 760


def test_las_kcal_escritas_a_mano_mandan(servidor, abrir):
    p, ficha = _con_avena(servidor, abrir)
    ficha.get_by_label("kcal", exact=True).fill("500")
    ficha.get_by_label("Cantidad", exact=True).fill("200")
    expect(ficha.get_by_label("kcal", exact=True)).to_have_value("500")
    ficha.get_by_role("button", name="Guardar cambios").click()
    expect(p.locator("#lista-dia .fila", has_text="Avena de prueba").locator(".kcal")).to_have_text("500")


def test_con_otra_unidad_no_recalcula(servidor, abrir):
    p, ficha = _con_avena(servidor, abrir)
    ficha.get_by_label("Unidad", exact=True).fill("unidad")
    ficha.get_by_label("Cantidad", exact=True).fill("2")
    expect(ficha.get_by_label("kcal", exact=True)).to_have_value("380")
