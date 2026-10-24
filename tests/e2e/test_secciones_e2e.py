"""E2E of unit 035: the sections of the day, in a real Chromium at 390 px with touch (R2, R3, R4, R5, R6, R7, R8).

The app of `conftest.py` (every outside service doubled, «today» Wednesday 2026-10-21) and its test day: Avena 228
and Arroz con pollo 532 eaten, Salmón al horno 374 and Fruta (no quantity) planned yesterday for today. The drag is
done with the FINGER: touch events sent through the Chrome DevTools Protocol, which Chromium turns into the pointer
events of a phone. With `TRIDATA_CAPTURAS`, a day with three sections (light and dark) and a drag in progress.
Plans v7: flow `ordenar-comida-secciones`, REC-21 (R-121..R-126, C-162..C-168), S-43..S-45.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py
AYER = HOY - timedelta(days=1)


def _textos(locator) -> list[str]:
    return [re.sub(r"\s+", " ", t).strip() for t in locator.all_text_contents()]


def _secciones(p) -> list[str]:
    return _textos(p.locator("#lista-dia .seccion-cab .seccion-nombre"))


def _kcal_secciones(p) -> dict[str, str]:
    cabeceras = p.locator("#lista-dia .seccion-cab")
    return {cabeceras.nth(i).locator(".seccion-nombre").text_content(): cabeceras.nth(i).locator(".seccion-kcal")
            .text_content() for i in range(cabeceras.count())}


def _fila(p, nombre: str):
    return p.locator("#lista-dia .fila[data-seccion]", has=p.locator(".que strong", has_text=nombre))


def _cabecera(p, nombre: str):
    return p.locator("#lista-dia .seccion-cab", has=p.locator(".seccion-nombre", has_text=nombre))


def _cuentas(p) -> tuple:
    return (p.locator("#total").text_content(), p.locator("#queda-cifra").text_content(),
            p.locator("#con-planeado").text_content(), _textos(p.locator("#otros .otro")))


def _arrastrar(p, nombre: str, destino, *, durante=None) -> None:
    """Drags the row `nombre` from its handle ⠿ to `destino` with a finger; `durante(p)` runs before lifting it."""
    asa = _fila(p, nombre).locator(".asa")
    asa.scroll_into_view_if_needed()
    a, d = asa.bounding_box(), destino.bounding_box()
    x0, y0 = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    x1, y1 = d["x"] + d["width"] / 2, d["y"] + d["height"] / 2
    cdp = p.context.new_cdp_session(p)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
    pasos = 10
    for i in range(1, pasos + 1):
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [
            {"x": x0 + (x1 - x0) * i / pasos, "y": y0 + (y1 - y0) * i / pasos}]})
    if durante is not None:
        durante(p)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    cdp.detach()


def _anadir_seccion(p, nombre: str) -> None:
    p.locator("#anadir-seccion").click()
    p.locator("#opciones-seccion button", has_text=nombre).click()


# -- R3 (load-bearing), R4 · C-164, C-168: drag with the finger, saved, same numbers after a reload ---------

def test_arrastrar_con_el_dedo_guarda_la_seccion_y_las_cuentas_no_cambian(servidor, abrir, capturar):  # R3, R4
    servidor.dia_de_prueba()
    p = abrir(servidor)
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    assert _secciones(p) == ["Sin sección"]  # R7: nothing logged has a section
    antes = _cuentas(p)
    assert antes[0] == "760 kcal comidas"

    _anadir_seccion(p, "Desayuno")
    assert _secciones(p) == ["Desayuno", "Sin sección"]  # R2: empty, in its place of the day
    expect(_cabecera(p, "Desayuno")).to_contain_text("Arrastra aquí un alimento")

    def en_curso(pagina):  # the target section is highlighted while the finger is on it
        expect(_cabecera(pagina, "Desayuno")).to_have_class(re.compile(r"\bdestino\b"))
        expect(pagina.locator("#lista-dia .fila.fantasma")).to_have_count(1)
        capturar(pagina, "035-arrastre-en-curso")

    _arrastrar(p, "Avena", _cabecera(p, "Desayuno"), durante=en_curso)
    expect(_fila(p, "Avena")).to_have_attribute("data-seccion", "desayuno")
    expect(p.locator("#lista-dia .fila.fantasma")).to_have_count(0)
    expect(p.locator("#lista-dia .destino")).to_have_count(0)
    assert _secciones(p) == ["Desayuno", "Sin sección"]
    assert _kcal_secciones(p) == {"Desayuno": "228 kcal", "Sin sección": "532 kcal"}
    assert _cuentas(p) == antes  # R4: total, margins and «Con lo planeado…» exactly the same
    assert servidor.registro.alimento(1)["seccion"] == "desayuno"

    p.reload()
    p.wait_for_selector("body[data-dia]", state="attached")
    expect(_fila(p, "Avena")).to_have_attribute("data-seccion", "desayuno")
    assert _secciones(p) == ["Desayuno", "Sin sección"]
    assert _cuentas(p) == antes

    # a planned food moved stays planned (C-168), and back to «Sin sección» is a drag too
    _anadir_seccion(p, "Cena")
    _arrastrar(p, "Salmón al horno", _cabecera(p, "Cena"))
    expect(_fila(p, "Salmón al horno")).to_have_attribute("data-seccion", "cena")
    expect(_fila(p, "Salmón al horno")).to_have_attribute("data-grupo", "planeado")
    expect(_fila(p, "Salmón al horno").get_by_role("button", name="Comido")).to_be_visible()
    assert _cuentas(p) == antes
    _arrastrar(p, "Avena", _cabecera(p, "Sin sección"))
    expect(_fila(p, "Avena")).to_have_attribute("data-seccion", "")
    assert _secciones(p) == ["Cena", "Sin sección"]  # R5: Desayuno, emptied, is gone
    assert servidor.registro.alimento(1)["seccion"] is None


# -- R2, R5 · C-163, C-165, C-167, S-45: only the missing ones; with five nothing; empty ones go ------------

def test_anadir_seccion_ofrece_las_que_faltan_y_la_vacia_desaparece(servidor, abrir):  # R2, R5
    servidor.dia_de_prueba()
    p = abrir(servidor)
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    _anadir_seccion(p, "Merienda")
    _arrastrar(p, "Arroz con pollo", _cabecera(p, "Merienda"))
    expect(_fila(p, "Arroz con pollo")).to_have_attribute("data-seccion", "merienda")
    _anadir_seccion(p, "Comida")
    assert _secciones(p) == ["Comida", "Merienda", "Sin sección"]
    _arrastrar(p, "Arroz con pollo", _cabecera(p, "Comida"))
    expect(_fila(p, "Arroz con pollo")).to_have_attribute("data-seccion", "comida")
    assert _secciones(p) == ["Comida", "Sin sección"]  # Merienda emptied: gone

    p.locator("#anadir-seccion").click()
    assert _textos(p.locator("#opciones-seccion button")) == ["Desayuno", "Media mañana", "Merienda", "Cena"]
    p.locator(".cancelar-seccion").click()
    for nombre in ("Cena", "Desayuno", "Media mañana", "Merienda"):
        _anadir_seccion(p, nombre)
    assert _secciones(p) == ["Desayuno", "Media mañana", "Comida", "Merienda", "Cena", "Sin sección"]
    p.locator("#anadir-seccion").click()
    expect(p.locator(".pie-secciones")).to_contain_text("Ya están las cinco secciones.")
    expect(p.locator("#opciones-seccion")).to_have_count(0)
    assert _secciones(p) == ["Desayuno", "Media mañana", "Comida", "Merienda", "Cena", "Sin sección"]

    # S-45: the empty ones only live on this screen while the day is on it
    p.locator("#anterior").click()
    expect(p.locator("#titulo")).to_have_text("Ayer")
    p.locator("#siguiente").click()
    expect(p.locator("#titulo")).to_have_text("Hoy")
    assert _secciones(p) == ["Comida", "Sin sección"]


# -- R6 · S-43, S-44: the order of the day, «Sin sección» last, kcal per section --------------------------

@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_orden_del_dia_y_kcal_por_seccion(servidor, abrir, capturar, oscuro):  # R6
    servidor.dia_de_prueba()
    servidor.apuntar("Yogur de prueba", HOY, 125, 77)
    for alimento_id, seccion in ((5, "comida"), (2, "comida"), (1, "cena"), (3, "desayuno")):
        servidor.registro.mover_seccion(alimento_id, seccion)
    p = abrir(servidor, oscuro=oscuro)
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    assert _secciones(p) == ["Desayuno", "Comida", "Cena", "Sin sección"]
    # eaten kcal (the planned salmon and the fruit without quantity don't add, as in the total)
    assert _kcal_secciones(p) == {"Desayuno": "0 kcal", "Comida": "609 kcal", "Cena": "228 kcal",
                                  "Sin sección": "0 kcal"}
    # inside a section, in the order they were logged (the rice before the yogurt it was moved after)
    assert _textos(p.locator("#lista-dia .fila[data-seccion='comida'] .que strong")) == ["Arroz con pollo",
                                                                                       "Yogur de prueba"]
    expect(p.locator("#total")).to_have_text("837 kcal comidas")
    capturar(p, f"035-tres-secciones-{'oscuro' if oscuro else 'claro'}", completa=True)


# -- R7 · C-166: a day logged before this change ----------------------------------------------------------

def test_dia_viejo_todo_sin_seccion_y_se_puede_ordenar(servidor, abrir):  # R7
    servidor.apuntar("Pasta de prueba", AYER, 100, 350, hoy=AYER)
    servidor.apuntar("Fruta de prueba", AYER, 150, 80, hoy=AYER)
    p = abrir(servidor)
    p.locator("#anterior").click()
    expect(p.locator("#titulo")).to_have_text("Ayer")
    assert _secciones(p) == ["Sin sección"]
    expect(p.locator("#total")).to_have_text("430 kcal comidas")
    _anadir_seccion(p, "Comida")
    _arrastrar(p, "Pasta de prueba", _cabecera(p, "Comida"))
    expect(_fila(p, "Pasta de prueba")).to_have_attribute("data-seccion", "comida")
    expect(p.locator("#total")).to_have_text("430 kcal comidas")
    assert servidor.registro.apuntes_del_dia(AYER)[0]["seccion"] == "comida"


# -- R8: the row still opens its card; «Comido» still works, inside its section ----------------------------

def test_tocar_la_fila_abre_la_ficha_y_comido_sigue(servidor, abrir):  # R8
    servidor.dia_de_prueba()
    servidor.registro.mover_seccion(3, "cena")  # the planned salmon
    p = abrir(servidor)
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    p.locator("#lista-dia .fila .asa").first.tap()  # the handle does not open anything
    expect(p.locator("#lista-dia .tarjeta")).to_have_count(0)
    _fila(p, "Avena").locator(".que").tap()
    expect(p.locator("#lista-dia .tarjeta")).to_be_visible()
    expect(p.locator("#lista-dia .tarjeta").get_by_role("button", name="Guardar cambios")).to_be_visible()
    p.locator("#lista-dia .tarjeta").get_by_role("button", name="Cancelar").click()

    _fila(p, "Salmón al horno").get_by_role("button", name="Comido").tap()
    expect(_fila(p, "Salmón al horno")).to_have_attribute("data-grupo", "comido")
    expect(_fila(p, "Salmón al horno")).to_have_attribute("data-seccion", "cena")
    expect(_cabecera(p, "Cena").locator(".seccion-kcal")).to_have_text("374 kcal")
    expect(p.locator("#total")).to_have_text("1.134 kcal comidas")
