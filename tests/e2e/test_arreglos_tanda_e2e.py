"""Unit 016 in a real Chromium at 390 × 844: the day of a reinterpreted list, midnight with the app open, an
open card that survives a repaint, the mark of a past day in the review, a recipe food whose kcal are
corrected, and every kcal in whole numbers.

Uses `conftest.py` (temporary SQLite, every outside service doubled); `servidor.hoy` moves the server's
«today» and `page.clock` the phone's. Invented data only. R1, R2, R3, R4, R5, R7, R8 of the unit.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.ia import AlimentoInterpretado, ErrorInterpretacion, Interpretacion, ObjetivoDiaInterpretado  # noqa: E402
from tridata.nutricion import Alimento  # noqa: E402
from tridata.nutricion.recetas import Recetario  # noqa: E402

HOY = date(2026, 10, 21)  # the same Wednesday as conftest.py
MARTES = HOY - timedelta(days=1)
LUNES = HOY - timedelta(days=2)
JUEVES = HOY + timedelta(days=1)
NUEVO_DIA = "nuevo día, descanso, mantenimiento, avena de prueba 60 g"
CON_DECIMAL = re.compile(r"\d,\d")


def _listo(p):
    expect(p.locator("#queda-cifra")).not_to_have_text("—")


def _api_dia(p, servidor, fecha: date | None = None) -> dict:
    ruta = f"/api/dia?fecha={fecha.isoformat()}" if fecha else "/api/dia"
    return p.request.get(servidor.url + ruta).json()


def _item(nombre, dia, cantidad=60, kcal=228.0, unidad="g"):
    return AlimentoInterpretado(nombre=nombre, cantidad=cantidad, unidad=unidad, dia=dia, kcal=kcal, proteina_g=None,
                                hidratos_g=None, grasa_g=None, estimado=True, dudoso=False, supuestos=[])


def _nuevo_dia(texto: str, hoy: date) -> Interpretacion:
    return Interpretacion(alimentos=[_item("avena de prueba", hoy)],
                          objetivo_dia=ObjetivoDiaInterpretado(tipo="descanso", porcentaje=0, niveles=None,
                                                               entreno=None, pasos=None))


def _grabar(p, segundos=1.2):
    p.locator("#grabar").click()
    expect(p.locator("#grabar")).to_have_attribute("aria-label", "Parar la grabación")
    p.wait_for_timeout(segundos * 1000)
    p.locator("#grabar").click()
    expect(p.locator("#grabar")).to_have_attribute("aria-label", "Grabar lo que has comido")


def _a_la_vista(p):
    """The app comes back to the front (iOS: back from another app)."""
    p.evaluate("document.dispatchEvent(new Event('visibilitychange'))")


# -- R2 + R1: a list of Tuesday whose Claude failed, interpreted and confirmed on Wednesday --------------

def test_lista_de_ayer_reinterpretada_va_a_ayer(servidor, abrir, capturar):  # R2, R1 · R-10, R-31
    servidor.hoy = MARTES
    servidor.transcriptor.transcribir.return_value = NUEVO_DIA
    servidor.interprete.interpretar.side_effect = ErrorInterpretacion("Claude no responde (prueba)")
    p = abrir(servidor)
    p.clock.set_system_time(datetime(2026, 10, 20, 21, 30))
    _listo(p)
    _grabar(p)
    expect(p.locator("#mensaje")).to_contain_text("El PC no pudo interpretarlo")
    expect(p.locator("#texto")).to_have_value(NUEVO_DIA)

    # Wednesday morning: the phone and the PC are on the 21st, and Claude answers again
    servidor.hoy = HOY
    servidor.interprete.interpretar.side_effect = _nuevo_dia
    p.clock.set_system_time(datetime(2026, 10, 21, 9, 0))
    p.reload()
    _listo(p)
    p.locator("#pendientes").get_by_role("button", name="Revisar").click()
    expect(p.locator("#texto")).to_have_value(NUEVO_DIA)
    p.locator("#enviar").click()
    expect(p.locator("#revision")).to_be_visible()
    expect(p.locator("#dia-grabacion")).to_have_text("Grabado el martes 20 de octubre: se apunta en ese día")
    servidor.interprete.interpretar.assert_called_with(NUEVO_DIA, MARTES)
    tarjeta = p.locator("#lista article").first
    expect(tarjeta.get_by_label("Día")).to_have_value(MARTES.isoformat())
    expect(tarjeta.locator(".marca", has_text="planeado")).to_have_count(0)
    capturar(p, "r2-revision-lista-de-ayer", completa=True)

    p.locator("#confirmar").click()
    expect(p.locator("#mensaje")).to_contain_text("Guardado.")
    martes = _api_dia(p, servidor, MARTES)
    assert martes["objetivo"]["origen"] == "dia" and martes["objetivo"]["porcentaje"] == 0
    assert [(a["nombre"], a["estado"]) for a in martes["alimentos"]] == [("avena de prueba", "comido")]
    hoy = _api_dia(p, servidor)
    assert hoy["objetivo"]["origen"] == "plan" and hoy["objetivo"]["porcentaje"] == -10
    assert hoy["alimentos"] == []


# -- R3: midnight with the app open ----------------------------------------------------------------------

def _medianoche(servidor, p):
    servidor.hoy = JUEVES
    p.clock.set_system_time(datetime(2026, 10, 22, 0, 1))


def test_pasada_la_medianoche_en_hoy_salta_al_dia_nuevo(servidor, abrir, capturar):  # R3 · R-5
    servidor.apuntar("Yogur de prueba", JUEVES, 150, 95)
    p = abrir(servidor)
    p.clock.set_system_time(datetime(2026, 10, 21, 23, 58))
    _listo(p)
    expect(p.locator("#titulo")).to_have_text("Hoy")
    expect(p.locator("#fecha")).to_have_text("miércoles 21 de octubre")

    _medianoche(servidor, p)
    _a_la_vista(p)

    expect(p.locator("#fecha")).to_have_text("jueves 22 de octubre")
    expect(p.locator("#titulo")).to_have_text("Hoy")
    yogur = p.locator("#lista-dia .fila", has_text="Yogur de prueba")
    expect(yogur.get_by_role("button", name="Comido")).to_be_visible()
    capturar(p, "r3-despues-de-medianoche")
    p.locator("#anterior").click()
    expect(p.locator("#titulo")).to_have_text("Ayer")
    expect(p.locator("#fecha")).to_have_text("miércoles 21 de octubre")


def test_medianoche_a_la_vista_lo_hace_en_un_minuto(servidor, abrir):  # R3
    p = abrir(servidor)
    p.clock.set_system_time(datetime(2026, 10, 21, 23, 58))
    _listo(p)
    _medianoche(servidor, p)
    p.clock.run_for(61_000)
    expect(p.locator("#fecha")).to_have_text("jueves 22 de octubre")
    expect(p.locator("#titulo")).to_have_text("Hoy")


def test_mirando_otro_dia_se_queda_pero_los_nombres_se_ponen_al_dia(servidor, abrir):  # R3
    p = abrir(servidor)
    p.clock.set_system_time(datetime(2026, 10, 21, 23, 58))
    _listo(p)
    p.locator("#anterior").click()
    p.locator("#anterior").click()
    expect(p.locator("#fecha")).to_have_text("lunes 19 de octubre")

    _medianoche(servidor, p)
    _a_la_vista(p)
    p.clock.run_for(61_000)

    expect(p.locator("#fecha")).to_have_text("lunes 19 de octubre")
    p.locator("#siguiente").click()
    expect(p.locator("#fecha")).to_have_text("martes 20 de octubre")
    expect(p.locator("#titulo")).to_have_text("Martes 20 de octubre")
    p.locator("#siguiente").click()
    expect(p.locator("#fecha")).to_have_text("miércoles 21 de octubre")
    expect(p.locator("#titulo")).to_have_text("Ayer")


def test_sin_conexion_a_medianoche_lo_hace_al_volver(servidor, abrir):  # R3
    p = abrir(servidor)
    p.clock.set_system_time(datetime(2026, 10, 21, 23, 58))
    _listo(p)
    p.context.set_offline(True)
    _medianoche(servidor, p)
    _a_la_vista(p)
    expect(p.locator("#sin-conexion")).to_be_visible()
    expect(p.locator("#fecha")).to_have_text("miércoles 21 de octubre")
    p.context.set_offline(False)
    _a_la_vista(p)
    expect(p.locator("#fecha")).to_have_text("jueves 22 de octubre")
    expect(p.locator("#titulo")).to_have_text("Hoy")


# -- R4: an open card survives a repaint -----------------------------------------------------------------

def _valores(ficha) -> list[str]:
    return [ficha.get_by_label(c, exact=True).input_value() for c in ("Alimento", "Cantidad", "Unidad", "kcal")]


def test_la_ficha_anadir_sobrevive_al_comido(servidor, abrir, capturar):  # R4
    servidor.apuntar("Yogur de prueba", HOY, 150, 95, hoy=MARTES)  # planned yesterday for today
    servidor.apuntar("Fruta de prueba", HOY, 120, 60, hoy=MARTES)
    p = abrir(servidor)
    _listo(p)
    p.locator("#anadir").click()
    ficha = p.locator("#anadir-ficha .tarjeta")
    for etiqueta, valor in (("Alimento", "Manzana de prueba"), ("Cantidad", "150"), ("Unidad", "g"), ("kcal", "78")):
        ficha.get_by_label(etiqueta, exact=True).fill(valor)

    p.locator("#lista-dia .fila", has_text="Yogur de prueba").get_by_role("button", name="Comido").click()

    expect(p.locator("#lista-dia .fila[data-grupo='comido']", has_text="Yogur de prueba")).to_be_visible()
    ficha = p.locator("#anadir-ficha .tarjeta")
    expect(ficha).to_be_visible()
    assert _valores(ficha) == ["Manzana de prueba", "150", "g", "78"]
    capturar(p, "r4-ficha-conservada", completa=True)
    # the screen repaints by itself (the minute of R3): the card is still there
    p.clock.run_for(61_000)
    assert _valores(p.locator("#anadir-ficha .tarjeta")) == ["Manzana de prueba", "150", "g", "78"]
    p.locator("#anadir-ficha .tarjeta").get_by_role("button", name="Guardar").click()
    expect(p.locator("#lista-dia .fila", has_text="Manzana de prueba")).to_be_visible()


def test_la_ficha_de_cambio_sobrevive_y_se_cierra_si_lo_borran(servidor, abrir):  # R4
    arroz = servidor.apuntar("Arroz de prueba", HOY, 200, 260)
    servidor.apuntar("Yogur de prueba", HOY, 150, 95, hoy=MARTES)
    p = abrir(servidor)
    _listo(p)
    p.locator("#lista-dia .fila", has_text="Arroz de prueba").click()
    ficha = p.locator("#lista-dia .tarjeta")
    ficha.get_by_label("kcal", exact=True).fill("300")

    p.locator("#lista-dia .fila", has_text="Yogur de prueba").get_by_role("button", name="Comido").click()

    expect(p.locator("#lista-dia .fila[data-grupo='comido']", has_text="Yogur de prueba")).to_be_visible()
    assert _valores(p.locator("#lista-dia .tarjeta")) == ["Arroz de prueba", "200", "g", "300"]

    # deleted from another tab: on the next repaint the card closes, without an error
    r = p.request.post(f"{servidor.url}/api/alimento/{arroz['id']}/borrar", data={"confirmado": True})
    assert r.ok
    _a_la_vista(p)
    expect(p.locator("#lista-dia .fila", has_text="Arroz de prueba")).to_have_count(0)
    expect(p.locator("#lista-dia .tarjeta")).to_have_count(0)


# -- R5: a past day in the review is not «planeado» --------------------------------------------------------

def _cena_y_comida(texto: str, hoy: date) -> Interpretacion:
    return Interpretacion(alimentos=[_item("cena de prueba", hoy - timedelta(days=1), 200, 300),
                                     _item("comida de prueba", hoy + timedelta(days=1), 250, 400)])


def test_dia_pasado_sin_planeado_en_la_revision(servidor, abrir):  # R5 · R-6
    servidor.interprete.interpretar.side_effect = _cena_y_comida
    p = abrir(servidor)
    _listo(p)
    p.locator("#texto").fill("ayer cené cena de prueba y mañana comida de prueba")
    p.locator("#enviar").click()
    tarjetas = p.locator("#lista article")
    expect(tarjetas).to_have_count(2)
    cena, comida = tarjetas.nth(0), tarjetas.nth(1)
    expect(cena.locator(".marca", has_text="martes 20 de octubre")).to_have_text("martes 20 de octubre")
    expect(cena.locator(".marca", has_text="planeado")).to_have_count(0)
    expect(comida.locator(".marca", has_text="planeado")).to_have_text("planeado: jueves 22 de octubre")

    p.locator("#confirmar").click()
    expect(p.locator("#mensaje")).to_contain_text("Guardado.")
    assert [(a["nombre"], a["estado"]) for a in _api_dia(p, servidor, MARTES)["alimentos"]] == [("cena de prueba", "comido")]
    assert [(a["nombre"], a["estado"]) for a in _api_dia(p, servidor, JUEVES)["alimentos"]] == [("comida de prueba", "planeado")]


# -- R7: correcting the kcal of a recipe food drops its marks -----------------------------------------------

def test_corregir_kcal_de_una_receta_quita_las_marcas(servidor, abrir):  # R7 · R-8
    servidor.registro.guardar([Alimento(nombre="Lentejas de prueba", dia=HOY, cantidad=250, unidad="g", kcal=320,
                                        origen="de tu receta «Lentejas de prueba»", provisional=True)],
                              hoy=HOY, texto="lentejas de prueba", sin_cambios=True)
    p = abrir(servidor)
    _listo(p)
    fila = p.locator("#lista-dia .fila", has_text="Lentejas de prueba")
    expect(fila.locator(".marca.origen")).to_have_text("de tu receta «Lentejas de prueba»")
    expect(fila.locator(".marca", has_text="provisional")).to_be_visible()
    fila.click()
    ficha = p.locator("#lista-dia .tarjeta")
    ficha.get_by_label("kcal", exact=True).fill("300")
    ficha.get_by_role("button", name="Guardar cambios").click()

    fila = p.locator("#lista-dia .fila", has_text="Lentejas de prueba")
    expect(fila.locator(".kcal")).to_have_text("300")
    expect(fila.locator(".marca.origen")).to_have_count(0)
    expect(fila.locator(".marca", has_text="provisional")).to_have_count(0)


# -- R8: every kcal in whole numbers ----------------------------------------------------------------------

def _sin_decimal(p, selector: str) -> None:
    textos = [t.strip() for t in p.locator(selector).all_inner_texts()]
    assert textos, selector
    assert not [t for t in textos if CON_DECIMAL.search(t)], f"{selector}: {textos}"


def test_todas_las_kcal_en_enteros(servidor, abrir, capturar):  # R8 · R-5, R-23
    servidor.apuntar("Comida de prueba", HOY, 300, 210.2)
    servidor.apuntar("Merienda de prueba", HOY, 100, 50.6, hoy=MARTES)  # planned for today
    p = abrir(servidor)
    _listo(p)
    expect(p.locator("#lista-dia .fila", has_text="Comida de prueba").locator(".kcal")).to_have_text("210")
    expect(p.locator("#lista-dia .fila", has_text="Merienda de prueba").locator(".kcal")).to_have_text("51")
    expect(p.locator("#total")).to_have_text("210 kcal comidas")
    mant = p.locator("#otros button", has_text="Mantenimiento")
    if mant.count():
        mant.click()
    expect(p.locator("#nivel-activo")).to_have_text("Mantenimiento")
    expect(p.locator("#queda-cifra")).to_have_text("1.790")
    _sin_decimal(p, "#otros .num")
    _sin_decimal(p, "#barra-quedan")
    _sin_decimal(p, "#con-planeado strong")
    _sin_decimal(p, "#ajustar-resumen")
    capturar(p, "r8-margen-en-enteros")

    p.locator("#abrir-hoja").click()
    expect(p.locator("#gasto-total")).to_have_text("2.000 kcal")
    _sin_decimal(p, "#desglose .linea span:last-child")
    _sin_decimal(p, "#objetivo-kcal")
    p.locator("#cerrar-hoja").click()

    # the review: «Si te lo comes» in whole kcal, the kcal box keeps its exact number
    servidor.interprete.interpretar.side_effect = lambda texto, hoy: Interpretacion(
        alimentos=[_item("dátiles de prueba", hoy, 6, 16.8)])
    p.locator("#texto").fill("6 g de dátiles de prueba")
    p.locator("#enviar").click()
    expect(p.locator("#lista article").first.get_by_label("kcal", exact=True)).to_have_value("16.8")
    expect(p.locator("#simulacion")).to_contain_text("Si te lo comes (17 kcal de hoy)")
    _sin_decimal(p, "#simulacion p")

    # what the server answers and saves is not rounded
    dia = _api_dia(p, servidor)
    assert dia["total_kcal"] == 210.2
    assert [m["kcal"] for m in dia["margenes"] if m["nivel"] == 0] == [1789.8]


def test_recetas_en_enteros_y_kcal_100g_con_decimal(servidor, abrir, capturar):  # R8 · R-57
    rec = Recetario(servidor.db)
    rec.crear_fijo({"nombre": "dátiles de prueba", "kcal": 280, "por": "100g"})
    rec.crear_fijo({"nombre": "galleta de prueba", "kcal": 33.3, "por": "unidad"})
    p = abrir(servidor, "/#recetas")
    caja = p.locator("#apartado-fijos")
    expect(caja.locator(".elemento", has_text="galleta de prueba")).to_contain_text("33,3 kcal")  # a rate: 1 decimal
    caja.locator(".elemento", has_text="dátiles de prueba").click()
    calc = caja.locator(".ficha .calculadora")
    calc.get_by_label("¿Cuánto has cogido? (g)").fill("6")
    expect(calc.locator(".resultado")).to_have_text("17 kcal")
    capturar(p, "r8-calculadora-en-enteros")
    calc.get_by_role("button", name="Apuntar en hoy").click()
    expect(p.locator("#recetas-mensaje")).to_have_text(re.compile(r"Apuntado en hoy: dátiles de prueba, 6 g, 17 kcal"))
    assert [a["kcal"] for a in _api_dia(p, servidor)["alimentos"]] == [16.8]
