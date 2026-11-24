"""E2E of unit 109 in a real Chromium at 390 px: gels and salt in the block «Suplementación» of Nutrición › Día —
the caffeine by origin, «En carrera» by minute, the sodium, adding a gel from a saved product, deleting one with
confirmation and managing the saved products (R2, R3, R4, R5).

The app of `conftest.py` (every outside service doubled, «today» Wednesday 2026-10-21, the phone at 10:00); doses and
products are written straight into its SQLite with `Suplementacion`. Invented data only. With `TRIDATA_CAPTURAS`,
the block with gels and salt, «Añadir» with a product and the saved products.
Plans: `apuntar-comida` R-318, R-319, R-320, R-321, C-405, C-406, C-408, C-409.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.nutricion.suplementacion import Suplementacion  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py
AHORA = datetime(2026, 10, 21, 10, 0)


def _textos(locator) -> list[str]:
    return [re.sub(r"\s+", " ", t).strip() for t in locator.all_text_contents()]


def _cafe_gel_sal(servidor) -> tuple[Suplementacion, dict]:
    sup = Suplementacion(servidor.db)
    sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    gel = sup.apuntar(HOY, {"que": "gel", "producto": "Gel cola de prueba", "mg": 100, "hora": "11:15"}, hoy=HOY,
                      ahora=AHORA)
    sup.apuntar(HOY, {"que": "sal", "producto": "Sal de prueba", "sodio_mg": 215, "minuto": 75}, hoy=HOY, ahora=AHORA)
    return sup, gel


def test_bloque_con_gel_sal_y_en_carrera(servidor, abrir, capturar):  # R2, R4 · C-405, C-408
    servidor.dia_de_prueba()
    _cafe_gel_sal(servidor)
    sup = Suplementacion(servidor.db)
    sup.apuntar(HOY, {"que": "gel", "producto": "Gel limón de prueba", "mg": 50, "cantidad": 2, "minuto": 60},
                hoy=HOY, ahora=AHORA)
    p = abrir(servidor)
    bloque = p.locator("#suplementacion")
    expect(bloque.locator(".sup-resumen")).to_have_text("330 mg de cafeína (café 130 · geles 200) · última: 11:15")
    assert _textos(bloque.locator(".sup-tomas:not(.sup-carrera) .toma-que")) == ["Café doble", "Gel cola de prueba"]
    expect(bloque.locator(".sup-sub")).to_have_text("En carrera")
    assert _textos(bloque.locator(".sup-carrera .toma-hora")) == ["min 60", "min 75"]
    assert _textos(bloque.locator(".sup-carrera .toma-que")) == ["2 × Gel limón de prueba", "Sal de prueba"]
    assert _textos(bloque.locator(".sup-carrera .toma-mg")) == ["100 mg", "215 mg sodio"]
    expect(bloque.locator(".sup-sodio")).to_have_text("Sodio: 215 mg")
    expect(p.locator("#total")).to_have_text("760 kcal comidas")  # G-68: gels and salt add no kcal
    capturar(p, "109-bloque-geles-sal")


def test_sin_geles_el_resumen_de_siempre(servidor, abrir):  # R2: «sin geles, como hoy»
    Suplementacion(servidor.db).apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY,
                                        ahora=AHORA)
    p = abrir(servidor)
    bloque = p.locator("#suplementacion")
    expect(bloque.locator(".sup-resumen")).to_have_text("130 mg de cafeína · última: 10:00")
    expect(bloque.locator(".sup-sodio")).to_have_count(0)
    expect(bloque.locator(".sup-carrera")).to_have_count(0)


def test_anadir_un_gel_eligiendo_el_producto(servidor, abrir, capturar):  # R3 · C-406
    sup = Suplementacion(servidor.db)
    gel = sup.guardar_producto({"nombre": "Gel 226 cola", "tipo": "gel", "mg_cafeina": 100, "sodio_mg": 50})
    p = abrir(servidor)
    bloque = p.locator("#suplementacion")
    bloque.get_by_role("button", name="Añadir", exact=True).click()
    ficha = bloque.locator(".ficha-toma")
    ficha.get_by_label("Qué", exact=True).select_option("gel")
    ficha.get_by_label("Producto", exact=True).select_option(str(gel["id"]))
    expect(ficha.get_by_label("mg de cafeína por unidad", exact=True)).to_have_value("100")  # without typing it
    ficha.get_by_label("Número", exact=True).fill("2")
    ficha.get_by_label("Hora", exact=True).fill("09:30")
    capturar(p, "109-anadir-gel-producto")
    ficha.get_by_role("button", name="Guardar", exact=True).click()
    expect(bloque.locator(".toma .toma-que")).to_have_text(["2 × Gel 226 cola"])
    expect(bloque.locator(".sup-resumen")).to_have_text("200 mg de cafeína (café 0 · geles 200) · última: 09:30")
    expect(bloque.locator(".sup-sodio")).to_have_text("Sodio: 100 mg")
    (toma,) = sup.tomas_del_dia(HOY)
    assert (toma["que"], toma["producto"], toma["cantidad"], toma["mg"], toma["sodio_mg"], toma["hora"]) == (
        "gel", "Gel 226 cola", 2, 200, 100, "09:30")


def test_anadir_un_gel_por_minuto_sin_producto(servidor, abrir):  # R1, R2: by minute, with its mg
    p = abrir(servidor)
    bloque = p.locator("#suplementacion")
    bloque.get_by_role("button", name="Añadir", exact=True).click()
    ficha = bloque.locator(".ficha-toma")
    ficha.get_by_label("Qué", exact=True).select_option("gel")
    expect(ficha.get_by_label("Hora", exact=True)).to_have_value("")  # a gel does not take the hour it is now
    ficha.get_by_role("button", name="Guardar", exact=True).click()
    expect(ficha.locator(".aviso")).to_have_text("Pon la hora o el minuto.")
    ficha.get_by_label("mg de cafeína por unidad", exact=True).fill("100")
    ficha.get_by_label("Minuto de carrera", exact=True).fill("60")
    ficha.get_by_role("button", name="Guardar", exact=True).click()
    expect(bloque.locator(".sup-carrera .toma-hora")).to_have_text(["min 60"])
    (toma,) = Suplementacion(servidor.db).tomas_del_dia(HOY)
    assert (toma["minuto"], toma["hora"], toma["mg"], toma["cantidad"]) == (60, None, 100, 1)


def test_cambiar_y_borrar_un_gel_con_confirmacion(servidor, abrir):  # R5 · C-409
    sup, gel = _cafe_gel_sal(servidor)
    p = abrir(servidor)
    bloque = p.locator("#suplementacion")
    bloque.locator(".toma", has_text="Gel cola de prueba").click()
    ficha = bloque.locator(".ficha-toma")
    expect(ficha.get_by_label("mg de cafeína por unidad", exact=True)).to_have_value("100")
    ficha.get_by_label("Número", exact=True).fill("2")
    ficha.get_by_role("button", name="Guardar", exact=True).click()
    expect(bloque.locator(".toma", has_text="Gel cola de prueba").locator(".toma-que")).to_have_text(
        "2 × Gel cola de prueba")
    assert sup.toma(gel["id"])["mg"] == 200
    bloque.locator(".toma", has_text="Gel cola de prueba").click()
    ficha = bloque.locator(".ficha-toma")
    ficha.get_by_role("button", name="Borrar").click()
    ficha.get_by_role("button", name="No", exact=True).click()
    expect(bloque.locator(".toma")).to_have_count(3)  # C-409: not confirmed, still there
    ficha.get_by_role("button", name="Borrar").click()
    ficha.get_by_role("button", name="Sí, borrar").click()
    expect(bloque.locator(".toma")).to_have_count(2)
    expect(bloque.locator(".sup-resumen")).to_have_text("130 mg de cafeína · última: 10:00")


def test_guardar_cambiar_y_borrar_productos(servidor, abrir, capturar):  # R3 · R-319, S-107
    sup = Suplementacion(servidor.db)
    antes = sup.apuntar(HOY, {"que": "sal", "producto": "Pastilla de prueba", "sodio_mg": 200, "minuto": 30},
                        hoy=HOY, ahora=AHORA)
    p = abrir(servidor)
    bloque = p.locator("#suplementacion")
    bloque.get_by_role("button", name="Añadir", exact=True).click()
    ficha = bloque.locator(".ficha-toma")
    ficha.get_by_role("button", name="Productos").click()
    gestor = ficha.locator(".sup-productos")
    expect(gestor).to_contain_text("Aún no hay productos guardados.")
    gestor.get_by_role("button", name="Nuevo producto").click()
    nuevo = gestor.locator(".ficha-producto")
    nuevo.get_by_label("Nombre", exact=True).fill("Pastilla de prueba")
    nuevo.get_by_label("Tipo", exact=True).select_option("sal")
    nuevo.get_by_label("Sodio (mg)", exact=True).fill("215")
    nuevo.get_by_role("button", name="Guardar producto").click()
    expect(gestor.locator(".producto")).to_have_count(1)
    expect(gestor.locator(".producto")).to_contain_text("215 mg sodio")
    capturar(p, "109-productos")
    (sal,) = sup.productos()
    assert (sal["nombre"], sal["tipo"], sal["sodio_mg"], sal["mg_cafeina"]) == ("Pastilla de prueba", "sal", 215, None)
    # the new product is offered at once in «Añadir»
    ficha.get_by_label("Qué", exact=True).select_option("sal")
    expect(ficha.get_by_label("Producto", exact=True).locator("option")).to_have_text(["— Ninguno —",
                                                                                       "Pastilla de prueba"])
    # changed: the dose already logged does not change (G-70)
    gestor.locator(".producto", has_text="Pastilla de prueba").click()
    gestor.get_by_label("Sodio (mg)", exact=True).fill("250")
    gestor.get_by_role("button", name="Guardar producto").click()
    expect(gestor.locator(".producto")).to_contain_text("250 mg sodio")
    assert sup.toma(antes["id"]) == antes
    # deleted only after «Sí, borrar producto»
    gestor.locator(".producto", has_text="Pastilla de prueba").click()
    gestor.get_by_role("button", name="Borrar producto").click()
    gestor.get_by_role("button", name="No", exact=True).click()
    assert len(sup.productos()) == 1
    gestor.get_by_role("button", name="Borrar producto").click()
    gestor.get_by_role("button", name="Sí, borrar producto").click()
    expect(gestor).to_contain_text("Aún no hay productos guardados.")
    assert sup.productos() == [] and sup.toma(antes["id"]) == antes
