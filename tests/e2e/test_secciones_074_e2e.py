"""E2E of unit 074 in a real Chromium at 390 px: «Nutrición en carrera» the last one of «Añadir sección», and the
change card of a logged food whose name is the search box of the saved recipes and ingredients (R1, R3, R4).

The app of `conftest.py` (every outside service doubled, «today» Wednesday 2026-10-21); the saved things are written
straight into its SQLite. Invented data only. With `TRIDATA_CAPTURAS`, the card with the suggestions and the row
changed. Plans: `apuntar-comida` R-212 (C-262), R-213 (C-263), S-43, S-71.
"""
from __future__ import annotations

import re
from datetime import date

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.nutricion import Alimento  # noqa: E402
from tridata.nutricion.recetas import Recetario  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py


def _textos(locator) -> list[str]:
    return [re.sub(r"\s+", " ", t).strip() for t in locator.all_text_contents()]


def _fila(p, nombre: str):
    return p.locator("#lista-dia .fila[data-seccion]", has=p.locator(".que strong", has_text=nombre))


def _con_lentejas(servidor, abrir) -> tuple:
    """«Lentejas de prueba 100 g» at dinner and a saved recipe «Frijoles de prueba» of 70 kcal/100 g."""
    rec = Recetario(servidor.db)
    rec.crear_fijo({"nombre": "frijol seco de prueba", "kcal": 350, "por": "100g"})
    receta = rec.crear_receta({"nombre": "Frijoles de prueba", "peso_neto_g": 1000, "ingredientes": [
        {"nombre": "frijol seco de prueba", "cantidad": 200, "unidad": "g", "kcal": 1,
         "guardado": "frijol seco de prueba"}]}, HOY)
    lentejas = servidor.registro.anadir(Alimento(nombre="Lentejas de prueba", dia=HOY, cantidad=100, unidad="g",
                                                 kcal=116), hoy=HOY)
    servidor.registro.mover_seccion(lentejas["id"], "cena")
    p = abrir(servidor)
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    _fila(p, "Lentejas de prueba").click()
    return p, p.locator("#lista-dia .tarjeta"), lentejas, receta, rec


def test_nutricion_en_carrera_es_la_ultima_de_anadir_seccion(servidor, abrir, capturar):  # R1 · C-262
    servidor.dia_de_prueba()
    p = abrir(servidor)
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    p.locator("#anadir-seccion").click()
    assert _textos(p.locator("#opciones-seccion button")) == ["Desayuno", "Media mañana", "Comida", "Merienda",
                                                              "Cena", "Nutrición en carrera"]
    p.locator("#opciones-seccion button", has_text="Nutrición en carrera").click()
    assert _textos(p.locator("#lista-dia .seccion-cab .seccion-nombre")) == ["Nutrición en carrera", "Sin sección"]
    capturar(p, "074-anadir-seccion-carrera")


def test_elegir_una_receta_en_la_ficha_cambia_la_misma_fila(servidor, abrir, capturar):  # R3 · C-263
    p, ficha, lentejas, _, _ = _con_lentejas(servidor, abrir)
    nombre = ficha.get_by_role("combobox", name="Alimento")
    nombre.fill("frij")
    opcion = ficha.get_by_role("option").filter(has_text="Frijoles de prueba")
    expect(opcion).to_be_visible()
    capturar(p, "074-ficha-cambio-sugerencias")
    opcion.click()
    elegida = ficha.locator(".elegida")
    expect(elegida.get_by_label("Gramos")).to_have_value("100")  # the quantity it had
    expect(elegida.locator(".kcal-elegido")).to_have_text("70 kcal")
    ficha.get_by_role("button", name="Guardar cambios").click()
    fila = _fila(p, "Frijoles de prueba")
    expect(fila).to_have_attribute("data-seccion", "cena")
    expect(fila.locator(".kcal")).to_have_text("70")
    expect(_fila(p, "Lentejas de prueba")).to_have_count(0)
    capturar(p, "074-fila-cambiada")
    a = servidor.registro.alimento(lentejas["id"])
    assert (a["nombre"], a["kcal"], a["seccion"], a["dia"]) == ("Frijoles de prueba", 70, "cena", HOY.isoformat())
    assert len(servidor.registro.apuntes_del_dia(HOY)) == 1


def test_sin_elegir_la_ficha_sigue_como_hoy(servidor, abrir):  # R3: bug 025 kept
    p, ficha, lentejas, _, _ = _con_lentejas(servidor, abrir)
    ficha.get_by_label("Cantidad", exact=True).fill("200")
    expect(ficha.get_by_label("kcal", exact=True)).to_have_value("232")
    ficha.get_by_role("button", name="Guardar cambios").click()
    expect(_fila(p, "Lentejas de prueba").locator(".kcal")).to_have_text("232")
    assert servidor.registro.alimento(lentejas["id"])["seccion"] == "cena"


def test_receta_borrada_antes_de_guardar_no_cambia_nada(servidor, abrir):  # R4
    p, ficha, lentejas, receta, rec = _con_lentejas(servidor, abrir)
    ficha.get_by_role("combobox", name="Alimento").fill("frij")
    ficha.get_by_role("option").filter(has_text="Frijoles de prueba").click()
    rec.borrar_receta(receta["id"], confirmado=True)
    ficha.get_by_role("button", name="Guardar cambios").click()
    expect(ficha.locator(".aviso")).to_contain_text("ya no está guardado")
    a = servidor.registro.alimento(lentejas["id"])
    assert (a["nombre"], a["kcal"], a["seccion"]) == ("Lentejas de prueba", 116, "cena")
