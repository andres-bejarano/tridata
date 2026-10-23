"""Unit 028 · R5 in a real Chromium at 390 × 844: correcting only the name or the note of a recipe weighed in a
pot whose tare changed afterwards keeps its net weight and its kcal/100 g; changing the full weight recomputes.

Uses `conftest.py` (temporary SQLite, every outside service doubled). Invented data only.
"""
from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.nutricion.recetas import Recetario  # noqa: E402


def _receta_en_olla(servidor) -> tuple[Recetario, int, int]:
    """600 g of a fixed food at 350 kcal/100 g = 2.100 kcal, in a 1.500 g pot weighing 3.500 g full:
    2.000 g net, 105 kcal/100 g. Then the pot's tare is changed to 1.000 g."""
    rec = Recetario(servidor.db)
    rec.crear_fijo({"nombre": "legumbre de prueba", "kcal": 350, "por": "100g"})
    olla = rec.crear_olla({"nombre": "olla de prueba", "tara_g": 1500})["id"]
    receta = rec.crear_receta({"nombre": "guiso de prueba", "ingredientes": [
        {"nombre": "legumbre de prueba", "cantidad": 600, "unidad": "g", "kcal": 1}],
        "olla_id": olla, "peso_lleno_g": 3500}, hoy=date(2026, 10, 20))["id"]
    rec.cambiar_olla(olla, {"nombre": "olla de prueba", "tara_g": 1000})
    return rec, receta, olla


def test_corregir_nombre_y_nota_no_cambia_las_kcal_y_el_peso_si(servidor, abrir, capturar):  # R5
    rec, receta, _ = _receta_en_olla(servidor)
    assert rec.receta(receta)["en_uso"]["kcal_100g"] == 105
    p = abrir(servidor, "/#recetas")
    recetas = p.locator("#apartado-recetas")
    fila = recetas.locator(".elemento", has_text="guiso de prueba")
    expect(fila).to_contain_text("105 kcal/100 g")

    # only the name and the note: the same 2.000 g net and 105 kcal/100 g
    fila.click()
    ficha = recetas.locator(".ficha-receta")
    ficha.get_by_label("Nombre", exact=True).fill("guiso de prueba corregido")
    ficha.get_by_label("Nota").fill("nota inventada")
    capturar(p, "028-r5-corregir-nombre", completa=True)
    ficha.get_by_role("button", name="Guardar cambios").click()
    fila = recetas.locator(".elemento", has_text="guiso de prueba corregido")
    expect(fila).to_contain_text("105 kcal/100 g")
    en_uso = rec.receta(receta)["en_uso"]
    assert (en_uso["tara_g"], en_uso["peso_lleno_g"], en_uso["peso_neto_g"], en_uso["kcal_100g"]) == (
        1500, 3500, 2000, 105)
    assert rec.receta(receta)["nota"] == "nota inventada"

    # the full weight changes: recomputed with today's tare, 3.600 − 1.000 = 2.600 g, 80,8 kcal/100 g
    fila.click()
    ficha = recetas.locator(".ficha-receta")
    ficha.get_by_label("Olla llena (g)").fill("3600")
    ficha.get_by_role("button", name="Guardar cambios").click()
    expect(recetas.locator(".elemento", has_text="guiso de prueba corregido")).to_contain_text("80,8 kcal/100 g")
    en_uso = rec.receta(receta)["en_uso"]
    assert (en_uso["tara_g"], en_uso["peso_lleno_g"], en_uso["peso_neto_g"]) == (1000, 3600, 2600)
