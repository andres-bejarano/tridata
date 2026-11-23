"""Unit 102 in a real Chromium at 390 × 844, light and dark: the shoes in Fitness and their warning in Hoy (E2E-17).

Plans `ver-fitness` REC-46: C-364, C-365, C-366, C-367, C-369. Uses the `conftest.py` of unit 010 («today» is
Wednesday 2026-10-21; Garmin is a `MagicMock`; Strava is never connected here: the shoes are put in the base as a
read of Strava would). Invented data only.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.zapatillas import Zapatillas  # noqa: E402

TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
AYER = datetime(2026, 10, 20, 8, 0)  # the day before the `HOY` of conftest.py


def _zapatillas(servidor, meta_km: float = 350.0) -> Zapatillas:
    repo = Zapatillas(servidor.db)
    repo.guardar_lectura([
        {"id": "g1001", "nombre": "Asics SonicBlast", "km": 303.2, "retirada": False},
        {"id": "g1002", "nombre": "Asics Gel Nimbus 28", "km": 141.0, "retirada": False},
        {"id": "g1003", "nombre": "Asics Meta Speed Sky Tokyo", "km": meta_km, "retirada": False},
        {"id": "g1004", "nombre": "Nike Vomero 18", "km": 812.0, "retirada": True},
    ], AYER)
    return repo


def _abrir_fitness(abrir, ir_a, servidor, **k):
    p = abrir(servidor, "/", esperar=False, **k)
    expect(p.locator("section#hoy")).to_be_visible()
    ir_a(p, "fitness")
    expect(p.locator("#fitness-zapatillas-lista .zapatilla")).to_have_count(3)
    return p


def _fila(p, nombre: str):
    return p.locator("#fitness-zapatillas-lista .zapatilla", has_text=nombre)


@TEMAS
def test_fitness_lista_las_zapatillas_y_hoy_avisa(servidor, abrir, ir_a, capturar, oscuro):  # C-364, C-366
    _zapatillas(servidor)
    p = abrir(servidor, "/", esperar=False, oscuro=oscuro)
    hoy = p.locator("section#hoy")
    expect(hoy).to_be_visible()
    aviso = hoy.locator(".aviso-zapatillas")
    expect(aviso).to_be_visible()
    expect(aviso).to_contain_text("Zapatillas")
    expect(aviso).to_contain_text("Asics Meta Speed Sky Tokyo 350 de 400 km, toca cambiarla pronto")
    expect(aviso).not_to_contain_text("SonicBlast")
    expect(hoy.locator(".tarjeta-hoy")).to_have_count(3)  # the cards of always are still three
    capturar(p, f"hoy-aviso-zapatillas-{'oscuro' if oscuro else 'claro'}")
    ir_a(p, "fitness")
    lista = p.locator("#fitness-zapatillas-lista .zapatilla")
    expect(lista).to_have_count(3)
    expect(p.locator("#fitness-zapatillas")).not_to_contain_text("Vomero")  # retired in Strava: not shown
    expect(_fila(p, "SonicBlast")).to_contain_text("303 de 700 km · quedan 397")
    expect(_fila(p, "SonicBlast")).to_contain_text("de Strava, el 20/10")
    expect(_fila(p, "Meta Speed")).to_contain_text("toca cambiarla pronto")
    expect(_fila(p, "Nimbus")).not_to_contain_text("toca cambiarla")
    p.locator("#fitness-zapatillas").scroll_into_view_if_needed()
    capturar(p, f"fitness-zapatillas-{'oscuro' if oscuro else 'claro'}")


def test_sin_ninguna_cerca_del_tope_hoy_no_dice_nada(servidor, abrir):  # C-367
    _zapatillas(servidor, meta_km=103.4)
    p = abrir(servidor, "/", esperar=False)
    hoy = p.locator("section#hoy")
    expect(hoy.locator("#hoy-comida")).to_be_visible()
    expect(hoy.locator(".aviso-zapatillas")).to_be_hidden()
    expect(hoy).not_to_contain_text("Zapatillas")
    expect(hoy.locator(".tarjeta-hoy")).to_have_count(3)


def test_cambiar_el_tope_y_los_km_en_fitness(servidor, abrir, ir_a, capturar):  # C-365, C-369
    _zapatillas(servidor, meta_km=103.4)
    p = _abrir_fitness(abrir, ir_a, servidor)
    meta = _fila(p, "Meta Speed")
    meta.get_by_role("button", name="Cambiar tope").click()
    meta.get_by_label("Tope en km").fill("120")
    meta.get_by_role("button", name="Guardar").click()
    expect(meta).to_contain_text("103 de 120 km · quedan 17")
    expect(meta).to_contain_text("toca cambiarla pronto")
    # a cap that is not valid: the server says why and nothing changes
    meta.get_by_role("button", name="Cambiar tope").click()
    meta.get_by_label("Tope en km").fill("20")
    meta.get_by_role("button", name="Guardar").click()
    expect(meta.locator(".aviso")).to_contain_text("50")
    expect(meta).to_contain_text("de 120 km")
    meta.get_by_role("button", name="Cancelar").click()
    # the km by hand
    nimbus = _fila(p, "Nimbus")
    nimbus.get_by_role("button", name="Cambiar km").click()
    nimbus.get_by_label("Km que lleva").fill("160")
    nimbus.get_by_role("button", name="Guardar").click()
    expect(nimbus).to_contain_text("160 de 700 km")
    expect(nimbus).to_contain_text("dicho por ti, hoy")
    capturar(p, "fitness-zapatillas-editadas")
    # Hoy follows the new cap
    ir_a(p, "hoy")
    expect(p.locator("section#hoy .aviso-zapatillas")).to_contain_text("Asics Meta Speed Sky Tokyo 103 de 120 km")
    ir_a(p, "fitness")
    meta = _fila(p, "Meta Speed")
    meta.get_by_role("button", name="Cambiar tope").click()
    meta.get_by_label("Tope en km").fill("400")
    meta.get_by_role("button", name="Guardar").click()
    expect(meta).to_contain_text("quedan 297")
    ir_a(p, "hoy")
    expect(p.locator("section#hoy .aviso-zapatillas")).to_be_hidden()


def test_los_botones_de_las_zapatillas_miden_44_px(servidor, abrir, ir_a):  # ergonomics of the phone
    _zapatillas(servidor)
    p = _abrir_fitness(abrir, ir_a, servidor)
    botones = p.locator("#fitness-zapatillas button:visible")
    assert botones.count() >= 6
    for i in range(botones.count()):
        caja = botones.nth(i).bounding_box()
        assert caja and caja["height"] >= 44, botones.nth(i).inner_text()
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, ancho
    assert re.search(r"quedan \d+", p.locator("#fitness-zapatillas").inner_text())


# -- unit 103 (REC-47, E2E-18): the shoes given here, the question in Hoy and Fitness, and changing a run's shoe ------

ALTA = datetime(2026, 10, 18, 9, 0)  # before the run of these tests (20/10, the day before `HOY`)


def _tres_de_aqui(servidor) -> Zapatillas:
    repo = Zapatillas(servidor.db)
    for nombre, km, tope, uso in (("SonicBlast", 310, 700, "calidad"), ("Gel Nimbus 28", 150, 700, "suave"),
                                  ("Meta Speed Sky Tokyo", 90, 400, "competicion")):
        repo.dar_de_alta(nombre, km, tope, uso, ALTA)
    from tridata.models import Activity

    servidor.store.save_activities([Activity(
        activity_id="e2e-carrera", activity_date=AYER.date(), activity_type="running", name="Carrera",
        duration_seconds=2700, distance_meters=8100, start_time_local="2026-10-20T07:30:00")])
    return repo


def test_dar_de_alta_en_fitness(servidor, abrir, ir_a, capturar):  # R1 (103) · C-377
    p = abrir(servidor, "/", esperar=False)
    expect(p.locator("section#hoy")).to_be_visible()
    ir_a(p, "fitness")
    caja = p.locator("#fitness-zapatillas")
    for nombre, km, tope, uso in (("SonicBlast", "310", "700", "calidad"), ("Gel Nimbus 28", "150", "700", "suave"),
                                  ("Meta Speed Sky Tokyo", "90", "400", "competicion")):
        caja.get_by_role("button", name="Dar de alta una zapatilla").click()
        form = caja.locator("form.alta-zapatilla")
        form.get_by_label("Nombre").fill(nombre)
        form.get_by_label("Km que lleva").fill(km)
        form.get_by_label("Tope en km").fill(tope)
        form.get_by_label("Para qué la usas").select_option(uso)
        form.get_by_role("button", name="Guardar").click()
        expect(form).to_have_count(0)
    expect(p.locator("#fitness-zapatillas-lista .zapatilla")).to_have_count(3)
    expect(_fila(p, "SonicBlast")).to_contain_text("310 de 700 km · quedan 390")
    expect(_fila(p, "SonicBlast")).to_contain_text("contados aquí desde hoy")
    # a use that is not valid is not offered; an empty name: the server says why
    caja.get_by_role("button", name="Dar de alta una zapatilla").click()
    form = caja.locator("form.alta-zapatilla")
    form.get_by_role("button", name="Guardar").click()
    expect(form.locator(".aviso")).to_be_visible()
    form.get_by_role("button", name="Cancelar").click()
    p.locator("#fitness-zapatillas").scroll_into_view_if_needed()
    capturar(p, "fitness-zapatillas-alta")
    # retiring one takes it out of the list (unit 106, R3: after «Sí» in its row)
    _fila(p, "Gel Nimbus 28").get_by_role("button", name="Retirar").click()
    _fila(p, "Gel Nimbus 28").get_by_role("button", name="Sí").click()
    expect(p.locator("#fitness-zapatillas-lista .zapatilla")).to_have_count(2)
    expect(caja).not_to_contain_text("Gel Nimbus 28")
    # unit 106 (R1, C-385): the use «trail»
    caja.get_by_role("button", name="Dar de alta una zapatilla").click()
    form = caja.locator("form.alta-zapatilla")
    form.get_by_label("Nombre").fill("Asics Trabuco 13")
    form.get_by_label("Km que lleva").fill("200")
    form.get_by_label("Tope en km").fill("700")
    form.get_by_label("Para qué la usas").select_option("trail")
    form.get_by_role("button", name="Guardar").click()
    expect(_fila(p, "Asics Trabuco 13")).to_contain_text("200 de 700 km · quedan 500")


@TEMAS
def test_la_pregunta_en_hoy_y_en_fitness(servidor, abrir, ir_a, capturar, oscuro):  # E2E-18 · C-375, C-376
    _tres_de_aqui(servidor)
    p = abrir(servidor, "/", esperar=False, oscuro=oscuro)
    hoy = p.locator("section#hoy")
    aviso = hoy.locator(".aviso-zapatillas")
    expect(aviso).to_be_visible()
    # unit 106 (R6, S-104): a run of yesterday is a line with «Elegir», not the question
    expect(aviso).to_contain_text("20/10: 8,1 km · Sin zapatilla")
    expect(aviso).not_to_contain_text("¿Con qué zapatilla")
    aviso.get_by_role("button", name="Elegir").click()
    expect(aviso.locator(".opciones-zapatilla button")).to_have_count(3)
    expect(hoy.locator(".tarjeta-hoy")).to_have_count(3)  # the cards of always are still three
    expect(aviso.locator(".tarjeta-hoy")).to_have_count(0)
    capturar(p, f"hoy-pregunta-zapatilla-{'oscuro' if oscuro else 'claro'}")
    # in Fitness the same question, and the run without a shoe in «Últimas carreras»
    ir_a(p, "fitness")
    pregunta = p.locator("#fitness-zapatillas-pregunta")
    expect(pregunta).to_contain_text("¿Con qué zapatilla corriste el 20/10 (8,1 km)?")
    expect(p.locator("#fitness-carreras .carrera-zapatilla")).to_have_count(1)
    p.locator("#fitness-zapatillas").scroll_into_view_if_needed()
    capturar(p, f"fitness-pregunta-zapatilla-{'oscuro' if oscuro else 'claro'}")
    # answering in Hoy: the line says the shoe, and the km go to it
    ir_a(p, "hoy")
    if not aviso.locator(".opciones-zapatilla button").first.is_visible():
        aviso.get_by_role("button", name="Elegir").click()
    aviso.get_by_role("button", name="Gel Nimbus 28").click()
    expect(aviso).to_contain_text("20/10: 8,1 km con Gel Nimbus 28")
    expect(aviso.get_by_role("button", name="Cambiar")).to_be_visible()
    expect(hoy.locator(".tarjeta-hoy")).to_have_count(3)
    ir_a(p, "fitness")
    expect(_fila(p, "Gel Nimbus 28")).to_contain_text("158 de 700 km")
    expect(pregunta).to_be_hidden()
    carrera = p.locator("#fitness-carreras .carrera-zapatilla")
    expect(carrera).to_contain_text("20/10 · 8,1 km")
    expect(carrera).to_contain_text("elegida por ti")
    # changing it in «Últimas carreras» moves the km (C-376)
    carrera.get_by_label("Zapatilla de la carrera del 20/10").select_option(label="SonicBlast")
    expect(_fila(p, "SonicBlast")).to_contain_text("318 de 700 km")
    expect(_fila(p, "Gel Nimbus 28")).to_contain_text("150 de 700 km")
    p.locator("#fitness-carreras").scroll_into_view_if_needed()
    capturar(p, f"fitness-ultimas-carreras-{'oscuro' if oscuro else 'claro'}")


def test_la_pregunta_en_fitness_se_contesta(servidor, abrir, ir_a):  # C-375 in Fitness
    _tres_de_aqui(servidor)
    p = abrir(servidor, "/", esperar=False)
    expect(p.locator("section#hoy")).to_be_visible()
    ir_a(p, "fitness")
    pregunta = p.locator("#fitness-zapatillas-pregunta")
    pregunta.get_by_role("button", name="SonicBlast").click()
    expect(pregunta).to_be_hidden()
    expect(_fila(p, "SonicBlast")).to_contain_text("318 de 700 km")
    botones = p.locator("#fitness-zapatillas button:visible, #fitness-carreras select:visible")
    for i in range(botones.count()):
        caja = botones.nth(i).bounding_box()
        assert caja and caja["height"] >= 44, botones.nth(i).inner_text()
    assert p.evaluate("document.documentElement.scrollWidth") <= 390


# -- unit 106 (REC-50, E2E-19): the line of a run in Hoy and in Fitness, and retiring with confirmation -------------------


def _cuatro_y_ayer(servidor) -> Zapatillas:
    """SonicBlast, Nimbus, Meta and Trabuco given on 18/10; the plan of 20/10 says series and yesterday's run (12,4 km)
    is counted with the SonicBlast."""
    from tridata.models import Activity
    from tridata.plan import Planes

    repo = Zapatillas(servidor.db)
    for nombre, km, tope, uso in (("SonicBlast", 310, 700, "calidad"), ("Gel Nimbus 28", 150, 700, "suave"),
                                  ("Meta Speed Sky Tokyo", 90, 400, "competicion"),
                                  ("Asics Trabuco 13", 200, 700, "trail")):
        repo.dar_de_alta(nombre, km, tope, uso, ALTA)
    Planes(servidor.db).crear({
        "nombre": "Plan de prueba", "apartados": [{"titulo": "Sesiones", "parte": "sesiones_clave"}],
        "carreras": [{"apartado": 0, "nombre": "Maratón de prueba", "fecha": "2026-12-06", "objetivo": "",
                      "estado": "", "principal": True}],
        "sesiones": [{"apartado": 0, "fecha": "2026-10-20", "titulo": "Series 3×2 km", "bloques": [],
                      "clave": False}]}, datetime(2026, 10, 1, 9, 0))
    servidor.store.save_activities([Activity(
        activity_id="e2e-series", activity_date=AYER.date(), activity_type="running", name="Series",
        duration_seconds=3600, distance_meters=12400, start_time_local="2026-10-20T07:30:00")])
    return repo


@TEMAS
def test_la_linea_de_la_carrera_en_hoy_y_en_fitness(servidor, abrir, ir_a, capturar, oscuro):  # E2E-19 · C-386, C-391
    _cuatro_y_ayer(servidor)
    tema = "oscuro" if oscuro else "claro"
    p = abrir(servidor, "/", esperar=False, oscuro=oscuro)
    hoy = p.locator("section#hoy")
    aviso = hoy.locator(".aviso-zapatillas")
    expect(aviso).to_contain_text("20/10: 12,4 km con SonicBlast")
    expect(aviso.locator(".tarjeta-hoy")).to_have_count(0)
    expect(hoy.locator(".tarjeta-hoy")).to_have_count(3)  # the cards of always are still three
    aviso.get_by_role("button", name="Cambiar").click()
    expect(aviso.locator(".opciones-zapatilla button")).to_have_count(4)  # one per shoe in use
    capturar(p, f"hoy-linea-zapatilla-cambiar-{tema}")
    aviso.get_by_role("button", name="Gel Nimbus 28").click()
    expect(aviso).to_contain_text("20/10: 12,4 km con Gel Nimbus 28")
    expect(hoy.locator(".tarjeta-hoy")).to_have_count(3)
    ir_a(p, "fitness")
    expect(_fila(p, "Gel Nimbus 28")).to_contain_text("162 de 700 km")
    expect(_fila(p, "SonicBlast")).to_contain_text("310 de 700 km")
    # the same line in the detail of the run (the last activity), above its laps
    expect(p.locator("#fitness-ultima")).to_contain_text("Series")
    p.locator("#fitness-ultima").click()
    detalle = p.locator("#fitness-detalle")
    linea = detalle.locator(".linea-zapatilla")
    expect(linea).to_contain_text("20/10: 12,4 km con Gel Nimbus 28")
    linea.get_by_role("button", name="Cambiar").click()
    linea.get_by_role("button", name="SonicBlast").click()
    expect(linea).to_contain_text("20/10: 12,4 km con SonicBlast")
    expect(_fila(p, "SonicBlast")).to_contain_text("322 de 700 km")
    expect(_fila(p, "Gel Nimbus 28")).to_contain_text("150 de 700 km")
    detalle.scroll_into_view_if_needed()
    capturar(p, f"fitness-detalle-linea-zapatilla-{tema}")


def test_una_actividad_que_no_es_carrera_no_lleva_linea(servidor, abrir, ir_a):  # R7
    _cuatro_y_ayer(servidor)
    from tridata.models import Activity

    servidor.store.save_activities([Activity(
        activity_id="e2e-bici", activity_date=date(2026, 10, 21), activity_type="cycling", name="Bici",
        duration_seconds=3600, distance_meters=30000, start_time_local="2026-10-21T08:00:00")])  # today: the last
    p = abrir(servidor, "/", esperar=False)
    expect(p.locator("section#hoy")).to_be_visible()
    ir_a(p, "fitness")
    expect(p.locator("#fitness-ultima")).to_contain_text("Bici")
    p.locator("#fitness-ultima").click()
    detalle = p.locator("#fitness-detalle")
    expect(detalle).to_contain_text("Vueltas de Bici")
    expect(detalle.locator(".linea-zapatilla")).to_have_count(0)


def test_retirar_con_confirmacion_y_volver_a_activo(servidor, abrir, ir_a, capturar):  # E2E-19 · C-388
    _cuatro_y_ayer(servidor)
    p = abrir(servidor, "/", esperar=False)
    expect(p.locator("section#hoy")).to_be_visible()
    ir_a(p, "fitness")
    nimbus = _fila(p, "Gel Nimbus 28")
    nimbus.get_by_role("button", name="Retirar").click()
    expect(nimbus).to_contain_text("¿Retirar la Gel Nimbus 28?")
    nimbus.get_by_role("button", name="No").click()  # «No» changes nothing
    expect(p.locator("#fitness-zapatillas-lista .zapatilla")).to_have_count(4)
    expect(p.locator("#fitness-retiradas")).to_be_hidden()
    nimbus.get_by_role("button", name="Retirar").click()
    nimbus.get_by_role("button", name="Sí").click()
    expect(p.locator("#fitness-zapatillas-lista .zapatilla")).to_have_count(3)
    retiradas = p.locator("#fitness-retiradas")
    expect(retiradas).to_be_visible()
    retiradas.get_by_text("Retiradas").click()
    fila = retiradas.locator(".zapatilla", has_text="Gel Nimbus 28")
    expect(fila).to_contain_text("150 de 700 km")
    retiradas.scroll_into_view_if_needed()
    capturar(p, "fitness-zapatillas-retiradas")
    fila.get_by_role("button", name="Volver a ponerla en activo").click()
    expect(p.locator("#fitness-zapatillas-lista .zapatilla")).to_have_count(4)
    expect(_fila(p, "Gel Nimbus 28")).to_contain_text("150 de 700 km")
    expect(retiradas).to_be_hidden()
