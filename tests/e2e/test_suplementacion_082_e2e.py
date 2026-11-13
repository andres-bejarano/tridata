"""E2E of unit 082 in a real Chromium at 390 px: the block «Suplementación» on top of Nutrición › Día, changing a
dose by tapping it, adding one by hand, and the hour of each section (R2, R3, R4, R5).

The app of `conftest.py` (every outside service doubled, «today» Wednesday 2026-10-21, the phone at 10:00); the doses
are written straight into its SQLite with `Suplementacion`. Invented data only. With `TRIDATA_CAPTURAS`, the block
with its doses, the change card and the hour of dinner. Plans: `apuntar-comida` R-226, R-227, R-228, R-229, C-281,
C-282, C-283, C-286.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.nutricion import Alimento  # noqa: E402
from tridata.nutricion.suplementacion import Suplementacion  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py
AHORA = datetime(2026, 10, 21, 10, 0)


def _textos(locator) -> list[str]:
    return [re.sub(r"\s+", " ", t).strip() for t in locator.all_text_contents()]


def _tres_tomas(servidor) -> tuple[Suplementacion, dict]:
    sup = Suplementacion(servidor.db)
    sup.apuntar(HOY, {"que": "cafe", "forma": "italiana", "gramos": 20, "fraccion": 0.5, "hora": "17:30", "mg": 90},
                hoy=HOY, ahora=AHORA)
    doble = sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    sup.apuntar(HOY, {"que": "b12", "hora": "09:00"}, hoy=HOY, ahora=AHORA)
    return sup, doble


def test_bloque_arriba_ordenado_con_total_y_ultima(servidor, abrir, capturar):  # R2 · C-281
    servidor.dia_de_prueba()
    _tres_tomas(servidor)
    p = abrir(servidor)
    bloque = p.locator("#suplementacion")
    expect(bloque).to_be_visible()
    expect(bloque.locator(".sup-resumen")).to_have_text("220 mg de cafeína · última: 17:30")
    assert _textos(bloque.locator(".toma .toma-hora")) == ["09:00", "10:00", "17:30"]
    assert _textos(bloque.locator(".toma .toma-que")) == ["B12", "Café doble", "Café de italiana 20 g (½)"]
    # on top of the meals, apart from them
    assert bloque.bounding_box()["y"] < p.locator("#lista-dia").bounding_box()["y"]
    expect(p.locator("#lista-dia .toma")).to_have_count(0)
    expect(p.locator("#total")).to_have_text("760 kcal comidas")  # G-60: the doses add no kcal
    capturar(p, "082-bloque-suplementacion")


def test_sin_tomas_plegado_con_anadir(servidor, abrir, capturar):  # R2
    servidor.dia_de_prueba()
    p = abrir(servidor)
    bloque = p.locator("#suplementacion")
    expect(bloque.get_by_role("button", name="Añadir", exact=True)).to_be_visible()
    expect(bloque.locator(".toma")).to_have_count(0)
    expect(bloque.locator(".sup-resumen")).to_have_count(0)
    capturar(p, "082-bloque-plegado")


def test_tocar_una_toma_y_cambiar_la_hora(servidor, abrir, capturar):  # R3 · C-282
    sup = Suplementacion(servidor.db)
    doble = sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    p = abrir(servidor)
    bloque = p.locator("#suplementacion")
    bloque.locator(".toma", has_text="Café doble").click()
    ficha = bloque.locator(".ficha-toma")
    expect(ficha.get_by_label("Hora", exact=True)).to_have_value("10:00")
    ficha.get_by_label("Hora", exact=True).fill("10:30")
    capturar(p, "082-ficha-toma")
    ficha.get_by_role("button", name="Guardar").click()
    expect(bloque.locator(".toma .toma-hora")).to_have_text(["10:30"])
    expect(bloque.locator(".sup-resumen")).to_have_text("130 mg de cafeína · última: 10:30")
    assert sup.toma(doble["id"])["hora"] == "10:30"


def test_borrar_sin_confirmar_no_borra(servidor, abrir):  # R3 · C-283
    _, doble = _tres_tomas(servidor)
    p = abrir(servidor)
    bloque = p.locator("#suplementacion")
    bloque.locator(".toma", has_text="Café doble").click()
    ficha = bloque.locator(".ficha-toma")
    ficha.get_by_role("button", name="Borrar").click()
    ficha.get_by_role("button", name="No", exact=True).click()
    expect(bloque.locator(".toma")).to_have_count(3)
    assert Suplementacion(servidor.db).toma(doble["id"])["hora"] == "10:00"
    ficha.get_by_role("button", name="Borrar").click()
    ficha.get_by_role("button", name="Sí, borrar").click()
    expect(bloque.locator(".toma")).to_have_count(2)


def test_anadir_una_toma_a_mano(servidor, abrir):  # R3
    p = abrir(servidor)
    bloque = p.locator("#suplementacion")
    bloque.get_by_role("button", name="Añadir", exact=True).click()
    ficha = bloque.locator(".ficha-toma")
    ficha.get_by_label("Qué", exact=True).select_option("cafe")
    ficha.get_by_label("Forma", exact=True).select_option("expreso")
    ficha.get_by_label("Hora", exact=True).fill("08:15")
    ficha.get_by_label("mg de cafeína", exact=True).fill("70")
    ficha.get_by_role("button", name="Guardar").click()
    expect(bloque.locator(".toma .toma-que")).to_have_text(["Café expreso"])
    expect(bloque.locator(".sup-resumen")).to_have_text("70 mg de cafeína · última: 08:15")
    (toma,) = Suplementacion(servidor.db).tomas_del_dia(HOY)
    assert (toma["forma"], toma["hora"], toma["mg"], toma["mg_estimado"]) == ("expreso", "08:15", 70, False)


def test_la_leche_repartida_en_cada_cafe(servidor, abrir):  # R4 · C-284
    sup = Suplementacion(servidor.db)
    sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    sup.apuntar(HOY, {"que": "cafe", "forma": "expreso", "hora": "16:00", "mg": 70, "solo": True}, hoy=HOY,
                ahora=AHORA)
    (leche,) = servidor.registro.guardar([Alimento(nombre="leche de prueba", dia=HOY, cantidad=200, unidad="ml",
                                                   kcal=92)], hoy=HOY, texto="leche", sin_cambios=False)
    sup.marcar_leche(HOY, leche["id"])
    p = abrir(servidor)
    bloque = p.locator("#suplementacion")
    expect(bloque.locator(".toma", has_text="Café doble").locator(".toma-leche")).to_have_text("+200 ml")
    expect(bloque.locator(".toma", has_text="Café expreso").locator(".toma-leche")).to_have_text("solo")
    expect(bloque.locator(".sup-leche")).to_contain_text("200 ml")


def test_hora_de_la_cena(servidor, abrir, capturar):  # R5 · C-286
    cena = servidor.apuntar("Sopa de prueba", HOY, 300, 120)
    servidor.registro.mover_seccion(cena["id"], "cena")
    p = abrir(servidor)
    cabecera = p.locator("#lista-dia .seccion-cab[data-seccion='cena']")
    hora = cabecera.locator(".seccion-hora")
    expect(hora).to_have_text("~21:30")
    expect(p.locator("#lista-dia .seccion-cab[data-seccion=''] .seccion-hora")).to_have_count(0)  # «Sin sección»
    hora.click()
    cabecera.get_by_label("Hora de Cena", exact=True).fill("22:15")
    cabecera.get_by_role("button", name="Guardar").click()
    expect(cabecera.locator(".seccion-hora")).to_have_text("22:15")
    expect(p.locator("#total")).to_have_text("120 kcal comidas")
    capturar(p, "082-hora-cena")
    p.reload()
    expect(p.locator("#lista-dia .seccion-cab[data-seccion='cena'] .seccion-hora")).to_have_text("22:15")
    assert Suplementacion(servidor.db).horas_del_dia(HOY)["cena"] == {"hora": "22:15", "aproximada": False}
