"""Walks of the new phone screen (unit 010) in a real Chromium at 390 × 844. Invented data only.

R1-R2 the «Te quedan» card · R4 the day list · R5 the sheet «Ajustar el día» · R6-R7 input,
dictation, queue and review · R8 tabs · R9 Peso · R11 Recetas · R12 Ajustes · R13 offline ·
R14 ergonomics and screenshots · R16 edge cases. R15 (nothing is lost) is the sum of all of them.
"""
from __future__ import annotations

import re
import time
from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.ia import ErrorTranscripcion  # noqa: E402

HOY = date(2026, 10, 21)  # the same fixed «today» as conftest.py (a Wednesday)
AYER = HOY - timedelta(days=1)
ANCHO = 390


def _texto(locator) -> str:
    return re.sub(r"\s+", " ", locator.inner_text()).strip()


def _textos(locator) -> list[str]:
    return [re.sub(r"\s+", " ", t).strip() for t in locator.all_inner_texts()]


def _api_dia(pagina, servidor, fecha=None) -> dict:
    ruta = f"/api/dia?fecha={fecha.isoformat()}" if fecha else "/api/dia"
    return pagina.request.get(servidor.url + ruta).json()


def _listo(pagina):
    expect(pagina.locator("#queda-cifra")).not_to_have_text("—")


def _filas(pagina, grupo: str):
    return pagina.locator(f"#lista-dia .fila[data-grupo='{grupo}']")


# -- R1, R2: the «Te quedan» card ----------------------------------------------------------------

def test_te_quedan_en_grande_y_nivel_que_miras(servidor, abrir):  # R1, R2
    servidor.dia_de_prueba()
    p = abrir(servidor)
    _listo(p)
    expect(p.locator("#queda-texto")).to_have_text("Te quedan para comer")
    expect(p.locator("#queda-cifra")).to_contain_text("1.040")
    expect(p.locator("#nivel-activo")).to_have_text("Nivel −10 %")
    assert _textos(p.locator("#otros .otro")) == ["Mantenimiento 1.240", "+10 % 1.440"]
    # the bar: eaten, then the planned striped, and its legend
    expect(p.locator("#barra-leyenda")).to_contain_text("42 % comido · 760 de 1.800")
    expect(p.locator("#barra-leyenda-planeado")).to_have_text("▨ planeado")
    relleno = p.locator("#barra-relleno").bounding_box()
    rayado = p.locator("#barra-planeado").bounding_box()
    pista = p.locator("#barra-pista").bounding_box()
    assert abs(relleno["width"] / pista["width"] - 760 / 1800) < 0.02
    assert abs(rayado["width"] / pista["width"] - 374 / 1800) < 0.02
    assert abs(rayado["x"] - (relleno["x"] + relleno["width"])) < 2

    antes = _api_dia(p, servidor)
    p.locator("#otros .otro", has_text="+10 %").click()
    expect(p.locator("#queda-cifra")).to_contain_text("1.440")
    expect(p.locator("#nivel-activo")).to_have_text("Nivel +10 %")
    assert _textos(p.locator("#otros .otro")) == ["−10 % 1.040", "Mantenimiento 1.240"]
    assert _api_dia(p, servidor) == antes  # the objective of today does not change

    p.reload()
    _listo(p)
    expect(p.locator("#queda-cifra")).to_contain_text("1.440")  # the phone remembers it
    expect(p.locator("#nivel-activo")).to_have_text("Nivel +10 %")


def test_cinco_niveles_y_uno_solo(servidor, abrir):  # R1
    servidor.dia_de_prueba()
    p = abrir(servidor)
    _listo(p)
    assert p.request.post(servidor.url + "/api/dia/objetivo", data={"niveles": [-20, -15, -10, 0, 10]}).ok
    p.reload()
    _listo(p)
    expect(p.locator("#otros .otro")).to_have_count(4)
    expect(p.locator("#queda-cifra")).to_contain_text("1.040")

    assert p.request.post(servidor.url + "/api/dia/objetivo", data={"niveles": [-15]}).ok
    p.reload()
    expect(p.locator("#queda-cifra")).to_contain_text("940")
    expect(p.locator("#nivel-activo")).to_have_text("Nivel −15 %")
    expect(p.locator("#otros .otro")).to_have_count(0)


def test_sin_planeado_no_hay_rayado_y_el_limite_se_mantiene(arrancar, abrir):  # R2, R3
    servidor = arrancar({"limite_saludable_pct": 5})
    servidor.apuntar("Avena", HOY, 60, 228)
    p = abrir(servidor)
    _listo(p)
    expect(p.locator("#barra-planeado")).to_be_hidden()
    expect(p.locator("#barra-leyenda-planeado")).to_be_hidden()
    expect(p.locator("#con-planeado")).to_be_hidden()
    expect(p.locator("#barra-limite")).to_be_visible()
    expect(p.locator("#barra-aviso")).to_contain_text("pasa del límite saludable")


def test_te_has_pasado(servidor, abrir):  # R16
    servidor.apuntar("Paella", HOY, 900, 2300)
    p = abrir(servidor)
    _listo(p)
    expect(p.locator("#queda-texto")).to_have_text("Te has pasado")
    expect(p.locator("#queda-cifra")).to_contain_text("500")
    expect(p.locator("#nivel-activo")).to_have_text("Nivel −10 %")
    expect(p.locator("#queda")).to_have_class(re.compile(r"\bpasado\b"))
    expect(p.locator("#barra")).to_have_class(re.compile(r"\bpasada\b"))


def test_dia_sin_ajustes_lleva_a_ajustes(arrancar, abrir):  # R16
    servidor = arrancar(sin_ajustes=True)
    p = abrir(servidor)
    expect(p.locator("#mensaje")).to_contain_text("Ponlos en Ajustes")
    enlace = p.locator("#mensaje a")
    expect(enlace).to_have_attribute("href", "/ajustes")
    enlace.click()
    expect(p).to_have_url(servidor.url + "/ajustes")


# -- R4, R16: the day list -------------------------------------------------------------------------

def test_lista_del_dia(servidor, abrir):  # R4
    servidor.dia_de_prueba()
    p = abrir(servidor)
    _listo(p)
    expect(p.locator("#t-comido")).to_contain_text("Tu día")
    expect(p.locator("#t-comido")).to_contain_text("760 kcal comidas")
    assert _textos(p.locator("#lista-dia .subtitulo")) == ["Comido", "Planeado"]
    comido, planeado = _filas(p, "comido"), _filas(p, "planeado")
    assert _textos(comido.locator(".que strong")) == ["Avena", "Arroz con pollo"]
    assert _textos(planeado.locator(".que strong")) == ["Salmón al horno", "Fruta"]
    expect(comido.nth(0)).to_contain_text("60 g")
    expect(comido.nth(0).locator(".marca")).to_have_text("estimado")
    expect(comido.nth(0).locator(".kcal")).to_have_text("228")
    fruta = planeado.nth(1)
    expect(fruta.locator(".marca")).to_have_text("falta cantidad")
    expect(fruta.locator(".kcal")).to_have_text("—")
    expect(planeado.get_by_role("button", name="Comido")).to_have_count(2)
    expect(comido.get_by_role("button", name="Comido")).to_have_count(0)
    expect(p.locator("#avisos")).to_contain_text("Falta la cantidad de: Fruta")
    expect(p.locator("#con-planeado")).to_have_text("Con lo planeado te quedarían 666 kcal")

    # «Comido» on the salmon: it moves to Comido and the big number goes down
    planeado.nth(0).get_by_role("button", name="Comido").click()
    expect(p.locator("#queda-cifra")).to_contain_text("666")
    assert _textos(_filas(p, "comido").locator(".que strong")) == ["Avena", "Arroz con pollo", "Salmón al horno"]
    expect(p.locator("#con-planeado")).to_be_hidden()  # only the fruit is left, with no kcal

    # tapping a row opens its change card: change the kcal
    _filas(p, "comido").nth(0).click()
    ficha = p.locator("#lista-dia .tarjeta")
    ficha.get_by_label("kcal").fill("230")
    ficha.get_by_role("button", name="Guardar cambios").click()
    expect(_filas(p, "comido").nth(0).locator(".kcal")).to_have_text("230")

    # move to another day
    _filas(p, "comido").nth(1).click()
    ficha.get_by_label("Día").fill((HOY + timedelta(days=1)).isoformat())
    ficha.get_by_role("button", name="Guardar cambios").click()
    expect(p.locator("#mensaje")).to_contain_text("Pasado a mañana (planeado)")
    expect(_filas(p, "comido").locator(".que strong")).to_have_text(["Avena", "Salmón al horno"])

    # delete asks first: No keeps it, Sí deletes it
    _filas(p, "comido").nth(0).click()
    ficha.get_by_role("button", name="Borrar").click()
    expect(ficha).to_contain_text("¿Borrar Avena?")
    ficha.get_by_role("button", name="No").click()
    ficha.get_by_role("button", name="Borrar").click()
    ficha.get_by_role("button", name="Sí, borrar").click()
    expect(_filas(p, "comido").locator(".que strong")).to_have_text(["Salmón al horno"])

    # add by hand, at the end of the list
    p.locator("#anadir").click()
    nueva = p.locator("#anadir-ficha .tarjeta")
    nueva.get_by_label("Alimento").fill("Yogur natural")
    nueva.get_by_label("Cantidad").fill("125")
    nueva.get_by_label("kcal").fill("77")
    nueva.get_by_role("button", name="Guardar").click()
    expect(_filas(p, "comido").locator(".que strong")).to_have_text(["Salmón al horno", "Yogur natural"])


def test_dia_vacio(servidor, abrir):  # R4
    p = abrir(servidor)
    _listo(p)
    expect(p.locator("#vacio")).to_have_text("Aún no has apuntado nada hoy.")
    p.locator("#anterior").click()
    expect(p.locator("#vacio")).to_have_text("No hay nada apuntado el martes 20 de octubre.")


def test_dia_futuro_con_planeado(servidor, abrir):  # R16, R4, R8
    futuro = HOY + timedelta(days=2)
    servidor.apuntar("Lentejas", futuro, 300, 345)
    servidor.apuntar("Pan", futuro, 60, 155)
    p = abrir(servidor)
    _listo(p)
    expect(p.locator("#ir-hoy")).to_be_hidden()
    p.locator("#siguiente").click()
    p.locator("#siguiente").click()
    expect(p.locator("#fecha")).to_have_text("viernes 23 de octubre")
    expect(p.locator("#queda-cifra")).to_contain_text("1.800")  # nothing planned subtracts
    expect(p.locator("#con-planeado")).to_have_text("Con lo planeado te quedarían 1.300 kcal")
    expect(p.locator("#lista-dia").get_by_role("button", name="Comido")).to_have_count(0)
    expect(p.locator("#ir-hoy")).to_have_text("Volver a hoy")
    p.locator("#ir-hoy").click()
    expect(p.locator("#titulo")).to_have_text("Hoy")
    expect(p.locator("#ir-hoy")).to_be_hidden()


# -- R5: the sheet «Ajustar el día» -------------------------------------------------------------------

def test_hoja_de_ajustes_del_dia(arrancar, abrir):  # R5
    servidor = arrancar({"objetivo_peso": {"kg": 70.0, "fecha": (HOY + timedelta(days=130)).isoformat()}})
    servidor.dia_de_prueba()
    p = abrir(servidor)
    _listo(p)
    expect(p.locator("#abrir-hoja")).to_contain_text("Ajustar el día")
    expect(p.locator("#abrir-hoja")).to_contain_text("gasto 2.000")
    hoja = p.locator("#hoja")
    expect(hoja).to_be_hidden()
    p.locator("#abrir-hoja").click()
    expect(hoja).to_be_visible()
    expect(p.locator("#hoja-titulo")).to_have_text("Ajustes de hoy")

    # (a) the level being looked at: same effect as the card
    segmento = p.locator("#segmentado button")
    assert _textos(segmento) == ["−10 %", "Mant.", "+10 %"]
    expect(segmento.nth(0)).to_have_attribute("aria-pressed", "true")
    segmento.nth(2).click()
    expect(segmento.nth(2)).to_have_attribute("aria-pressed", "true")
    expect(p.locator("#queda-cifra")).to_contain_text("1.440")
    segmento.nth(0).click()
    # levels: a 60 % level is refused and nothing changes
    expect(p.locator("#niveles")).to_be_hidden()
    p.locator("#abrir-niveles").click()
    p.locator("#niveles-texto").fill("60")
    p.locator("#niveles-guardar").click()
    expect(p.locator("#niveles-error")).to_contain_text("No se ha cambiado nada")
    expect(segmento).to_have_count(3)
    p.locator("#niveles-texto").fill("-20; -10")
    p.locator("#niveles-guardar").click()
    expect(segmento).to_have_count(2)
    p.locator("#niveles-defecto").click()
    expect(segmento).to_have_count(3)

    # (b) the objective of today: % with ±, maintenance, back to the plan, days of delay
    expect(p.locator("#objetivo-valor")).to_have_text("−10 % · plan")
    expect(p.locator("#objetivo-kcal")).to_have_text("1.800 kcal de 2.000 de gasto.")
    expect(p.locator("#plan")).to_contain_text("Plan: −10 %")
    expect(p.locator("#objetivo-plan")).to_be_hidden()
    p.locator("#abrir-pct").click()
    p.locator("#objetivo-pct").fill("15")
    p.locator("#objetivo-hoy button.signo").click()
    expect(p.locator("#objetivo-pct")).to_have_value("-15")
    p.locator("#objetivo-guardar").click()
    expect(p.locator("#objetivo-valor")).to_have_text("−15 % · hoy")
    expect(p.locator("#retraso")).to_be_visible()
    p.locator("#objetivo-mantenimiento").click()
    expect(p.locator("#objetivo-valor")).to_have_text("Mantenimiento · hoy")
    expect(p.locator("#retraso")).to_contain_text("día")
    p.locator("#objetivo-plan").click()
    expect(p.locator("#objetivo-valor")).to_have_text("−10 % · plan")
    expect(p.locator("#retraso")).to_be_hidden()

    # (c) planned training: choose, minutes, put, remove
    expect(p.locator("#previsto-valor")).to_have_text("—")
    expect(p.locator("#previsto-quitar")).to_be_hidden()
    p.locator("#abrir-previsto").click()
    expect(p.locator("#previsto-actividad option")).to_contain_text(["Natación técnica"])
    p.locator("#previsto-minutos").fill("40")
    p.locator("#previsto-guardar").click()
    expect(p.locator("#previsto-valor")).to_have_text("40 min")
    expect(p.locator("#desglose")).to_contain_text("Previsto: Natación técnica (40 min)")
    p.locator("#previsto-quitar").click()
    expect(p.locator("#previsto-valor")).to_have_text("—")

    # (d) the expenditure with its breakdown and the Garmin text
    expect(p.locator("#gasto-total")).to_have_text("2.000 kcal")
    expect(p.locator("#desglose")).to_contain_text("Basal")
    expect(p.locator("#desglose")).to_contain_text("Pasos")
    expect(p.locator("#desglose")).to_contain_text("Total")
    expect(p.locator("#desglose")).to_contain_text("Sin datos de Garmin de hoy")
    expect(p.locator("#gasto")).to_contain_text("basal 1600 kcal")

    # (e) «Actualizar Garmin»: «Actualizando…», then ok; then an error, in view
    servidor.actualizar_garmin.side_effect = lambda dia: time.sleep(0.6)
    p.locator("#actualizar-garmin").click()
    expect(p.locator("#actualizar-garmin")).to_have_text("Actualizando…")
    expect(p.locator("#garmin-estado")).to_have_text("Garmin actualizado.")
    servidor.actualizar_garmin.side_effect = OSError("sin red")
    p.locator("#actualizar-garmin").click()
    expect(p.locator("#garmin-estado")).to_contain_text("No se ha podido conectar con Garmin")
    expect(p.locator("#actualizar-garmin")).to_have_text("↻ Actualizar Garmin")

    # closes with «Listo» and tapping outside
    p.locator("#cerrar-hoja").click()
    expect(hoja).to_be_hidden()
    p.locator("#abrir-hoja").click()
    expect(hoja).to_be_visible()
    p.mouse.click(ANCHO / 2, 10)
    expect(hoja).to_be_hidden()

    # another day: only (a) and (d)
    p.locator("#anterior").click()
    expect(p.locator("#titulo")).to_have_text("Ayer")
    p.locator("#abrir-hoja").click()
    expect(p.locator("#segmentado button")).to_have_count(3)
    expect(p.locator("#gasto-total")).to_be_visible()
    for oculto in ("#abrir-niveles", "#objetivo-hoy", "#previsto", "#actualizar-garmin"):
        expect(p.locator(oculto)).to_be_hidden()


# -- R6, R7: input, review, dictation and queue --------------------------------------------------

def test_revision_con_todas_las_piezas(servidor, abrir):  # R6, R7
    servidor.dia_de_prueba()
    p = abrir(servidor)
    _listo(p)
    expect(p.locator("#entrada")).to_be_visible()
    micro = p.locator("#grabar").bounding_box()
    assert round(micro["width"]) == 64 and round(micro["height"]) == 64
    expect(p.locator("#texto")).to_have_attribute("placeholder", re.compile("peso"))

    # interpreting from Peso goes to Hoy
    p.locator(".pestana", has_text="Peso").click()
    expect(p.locator("#entrada")).to_be_visible()
    p.locator("#texto").fill("avena 60 g, peso 72,4, hoy mantenimiento y algo raro")
    p.locator("#enviar").click()
    expect(p).to_have_url(re.compile(r"#hoy$"))
    revision = p.locator("#revision")
    expect(revision).to_be_visible()
    expect(revision).to_contain_text("Esto he entendido")
    expect(revision.locator("#lista input[type=text]").first).to_have_value("avena")
    expect(revision.locator(".tarjeta.peso input").first).to_have_value("72,4")
    expect(revision.locator(".tarjeta.objetivo")).to_be_visible()
    expect(revision.locator(".tarjeta.objetivo button.signo")).to_be_visible()
    expect(revision.locator(".tarjeta.dudosa")).to_contain_text("algo raro")
    # the card sits above the review, the list below it
    y_tarjeta = p.locator("#queda").bounding_box()["y"]
    y_revision = revision.bounding_box()["y"]
    y_lista = p.locator("#lista-dia").bounding_box()["y"]
    assert y_tarjeta < y_revision < y_lista

    # the doubtful phrase blocks the confirmation
    expect(p.locator("#bloqueo")).to_contain_text("algo raro")
    p.locator("#confirmar").click()
    assert _api_dia(p, servidor)["total_kcal"] == 760
    # «si te lo comes» and «¿cuánto me cabe?» save nothing
    expect(p.locator("#simulacion")).to_contain_text("Si te lo comes")
    revision.get_by_role("button", name="¿Cuánto me cabe?").click()
    expect(revision.locator(".cabe")).to_contain_text("Te caben")
    assert _api_dia(p, servidor)["total_kcal"] == 760
    # «Añadir alimento» adds an empty card
    revision.get_by_role("button", name="Añadir alimento").click()
    expect(revision.locator("#lista .tarjeta")).to_have_count(2)
    revision.locator("#lista .tarjeta").nth(1).get_by_role("button", name="Quitar").click()
    revision.locator(".tarjeta.dudosa").get_by_role("button", name="Quitar").click()
    expect(p.locator("#bloqueo")).to_be_hidden()
    p.locator("#confirmar").click()
    expect(p.locator("#mensaje")).to_contain_text("Guardado.")
    expect(revision).to_be_hidden()
    expect(p.locator("#queda-cifra")).to_contain_text("1.012")  # maintenance today: 2000 − 988
    assert _textos(_filas(p, "comido").locator(".que strong")) == ["Avena", "Arroz con pollo", "avena"]
    pesos = p.request.get(servidor.url + "/api/peso").json()["pesos"]
    assert [x["kg"] for x in pesos] == [72.4]

    # «Descartar» asks first
    p.locator("#texto").fill("avena 60 g, peso 72,4, hoy mantenimiento y algo raro")
    p.locator("#enviar").click()
    expect(revision).to_be_visible()
    p.locator("#descartar").click()
    expect(p.locator("#confirmar-descarte")).to_contain_text("¿Descartar esta lista?")
    p.locator("#descarte-no").click()
    expect(revision).to_be_visible()
    p.locator("#descartar").click()
    p.locator("#descarte-si").click()
    expect(revision).to_be_hidden()


def _grabar(p, segundos=1.2):
    p.locator("#grabar").click()
    expect(p.locator("#grabar")).to_have_attribute("aria-label", "Parar la grabación")
    expect(p.locator("#grabar-tiempo")).to_have_text(re.compile(r"^0:0\d$"))
    p.wait_for_timeout(segundos * 1000)
    p.locator("#grabar").click()
    expect(p.locator("#grabar")).to_have_attribute("aria-label", "Grabar lo que has comido")


def test_dictado_y_cola_sin_conexion(servidor, abrir):  # R6, R7
    servidor.dia_de_prueba()
    p = abrir(servidor)
    _listo(p)
    # dictation: the list of the recording opens by itself, with what was said
    _grabar(p)
    expect(p.locator("#revision")).to_be_visible()
    expect(p.locator("#dicho")).to_contain_text("algo raro")
    p.locator("#descartar").click()
    p.locator("#descarte-si").click()

    # without the PC: notice, the recording waits, and when it comes back, «Revisar»
    p.context.set_offline(True)
    p.locator("#anterior").click()
    expect(p.locator("#sin-conexion")).to_contain_text("Sin conexión con el PC")
    _grabar(p)
    expect(p.locator("#sin-enviar")).to_contain_text("1 grabación sin enviar")
    p.context.set_offline(False)
    pendientes = p.locator("#pendientes")
    expect(pendientes).to_contain_text("1 lista pendiente de revisar")
    expect(p.locator("#sin-enviar")).to_be_hidden()
    assert pendientes.bounding_box()["y"] < p.locator("#lista-dia").bounding_box()["y"]
    pendientes.get_by_role("button", name="Revisar").click()
    expect(p.locator("#revision")).to_be_visible()
    expect(p.locator("#dicho")).to_contain_text("avena 60 g")


def test_grabacion_no_transcrita_se_quita_con_confirmacion(servidor, abrir):  # R6
    servidor.transcriptor.transcribir.side_effect = ErrorTranscripcion("vacio")
    p = abrir(servidor)
    _listo(p)
    _grabar(p)
    tarjeta = p.locator("#no-transcritas .tarjeta")
    expect(tarjeta).to_contain_text("no se ha podido transcribir")
    tarjeta.get_by_role("button", name="Descartar").click()
    tarjeta.get_by_role("button", name="Sí, descartar").click()
    expect(p.locator("#no-transcritas .tarjeta")).to_have_count(0)


# -- R8: tabs -----------------------------------------------------------------------------------------

def test_pestanas_hash_y_atras(servidor, abrir):  # R8, R6
    p = abrir(servidor)
    _listo(p)
    pestanas = p.locator("nav.pestanas .pestana")
    assert _textos(pestanas) == ["Hoy", "Peso", "Recetas", "Ajustes"]
    assert round(p.locator("nav.pestanas .pestana").first.bounding_box()["height"]) >= 56
    expect(pestanas.nth(0)).to_have_attribute("aria-current", "page")
    expect(p.get_by_text("Ajustes ▸")).to_have_count(0)

    pestanas.nth(1).click()
    expect(p).to_have_url(servidor.url + "/#peso")
    expect(p.locator("section#peso")).to_be_visible()
    expect(p.locator("section#hoy")).to_be_hidden()
    expect(pestanas.nth(1)).to_have_attribute("aria-current", "page")
    expect(p.locator("#entrada")).to_be_visible()
    pestanas.nth(2).click()
    expect(p.locator("section#recetas")).to_be_visible()
    expect(p.locator("#entrada")).to_be_hidden()
    p.go_back()
    expect(p.locator("section#peso")).to_be_visible()
    p.go_back()
    expect(p.locator("section#hoy")).to_be_visible()

    # tapping Hoy while on Hoy goes back to today
    p.locator("#anterior").click()
    expect(p.locator("#titulo")).to_have_text("Ayer")
    pestanas.nth(0).click()
    expect(p.locator("#titulo")).to_have_text("Hoy")

    pestanas.nth(3).click()
    expect(p).to_have_url(servidor.url + "/ajustes")
    en_ajustes = p.locator("nav.pestanas .pestana")
    assert _textos(en_ajustes) == ["Hoy", "Peso", "Recetas", "Ajustes"]
    expect(en_ajustes.nth(3)).to_have_attribute("aria-current", "page")
    expect(p.get_by_text("◀ Hoy")).to_have_count(0)
    expect(p.locator("#entrada")).to_have_count(0)
    en_ajustes.nth(1).click()
    expect(p).to_have_url(servidor.url + "/#peso")
    expect(p.locator("section#peso")).to_be_visible()


# -- R9: Peso ------------------------------------------------------------------------------------------

def test_peso_siempre_de_hoy_con_grafica(arrancar, abrir):  # R9
    servidor = arrancar({"objetivo_peso": {"kg": 70.0, "fecha": (HOY + timedelta(days=130)).isoformat()}})
    servidor.pesos_de_prueba()
    p = abrir(servidor, "/#peso")
    seccion = p.locator("section#peso")
    expect(p.locator("#peso-cabecera")).to_have_text("Hoy · miércoles 21 oct")
    expect(p.locator("#peso-cifra")).to_contain_text("72,4")
    expect(seccion).to_contain_text("Para apuntarlo, di o escribe «peso 72,4».")
    expect(p.locator("#peso-media")).to_contain_text("72,5 kg")
    expect(p.locator("#peso-media")).to_contain_text("2 días con peso")
    expect(p.locator("#peso-basal")).to_contain_text("1.600")
    grafica = p.locator("#peso-grafica svg")
    expect(grafica.locator("circle.punto-peso")).to_have_count(3)
    expect(grafica.locator("polyline.linea-media")).to_have_count(1)
    objetivo = grafica.locator("line.linea-objetivo")
    expect(objetivo).to_have_count(1)
    alto = float(grafica.get_attribute("viewBox").split()[3])
    assert 0 <= float(objetivo.get_attribute("y1")) <= alto  # the objective inside the scale
    expect(p.locator("#peso-objetivo")).to_contain_text("Objetivo 70,0 kg")
    expect(p.locator("#peso-objetivo")).to_contain_text("hace falta")
    assert _textos(p.locator("#peso-ultimos .kcal")) == ["72,4 kg", "72,6 kg", "73,0 kg"]

    # Peso is always today, even after going back a day in Hoy
    p.locator(".pestana", has_text="Hoy").click()
    p.locator("#anterior").click()
    expect(p.locator("#titulo")).to_have_text("Ayer")
    p.locator(".pestana", has_text="Peso").click()
    expect(p.locator("#peso-cabecera")).to_have_text("Hoy · miércoles 21 oct")
    expect(p.locator("#peso-cifra")).to_contain_text("72,4")
    expect(p.locator("#peso-media")).to_contain_text("72,5 kg")


def test_peso_sin_pesos(servidor, abrir):  # R9
    p = abrir(servidor, "/#peso")
    expect(p.locator("section#peso")).to_contain_text("Aún no hay pesos")
    expect(p.locator("section#peso svg")).to_have_count(0)


# -- R11: Recetas ---------------------------------------------------------------------------------------

def test_recetas_vacia(servidor, abrir):  # R11
    p = abrir(servidor)
    _listo(p)
    llamadas = []
    p.on("request", lambda r: llamadas.append(r.url) if "/api/" in r.url else None)
    p.locator(".pestana", has_text="Recetas").click()
    seccion = p.locator("section#recetas")
    # unit 011 replaced the empty placeholder by the real tab: its three sections, each one empty
    # with its own add button, and no «próximamente» any more
    for texto in ("Recetas", "Taras", "Alimentos fijos"):
        expect(seccion).to_contain_text(texto)
    expect(seccion.locator(".vacio-apartado")).to_have_count(3)
    for boton in ("＋ Añadir receta", "＋ Añadir tara", "＋ Añadir alimento fijo"):
        expect(seccion.get_by_role("button", name=boton)).to_be_enabled()
    expect(seccion.get_by_text("próximamente")).to_have_count(0)
    p.wait_for_timeout(300)
    assert [u for u in llamadas if "/api/recetas" not in u] == []  # only the tab's own listing


# -- R12: Ajustes -----------------------------------------------------------------------------------------

def test_ajustes_en_filas(servidor, abrir):  # R12
    p = abrir(servidor, "/ajustes")
    filas = p.locator(".ajuste")
    expect(p.locator(".ajuste", has_text="Basal a mano")).to_contain_text("1.600 kcal")
    expect(p.locator(".ajuste", has_text="Objetivo del plan")).to_contain_text("−10 %")
    expect(p.locator(".ajuste", has_text="kcal por paso").locator(".chev")).to_have_text("›")
    expect(p.locator("#conexion")).to_contain_text("PC de casa")
    expect(p.locator("#conexion")).to_contain_text("conectado")
    assert filas.count() >= 8

    # a row opens its change card: validation, then saved
    p.locator(".ajuste", has_text="Edad").click()
    ficha = p.locator(".tarjeta.nueva")
    ficha.get_by_label("Edad (años)").fill("200")
    ficha.get_by_role("button", name="Guardar").click()
    expect(ficha.locator(".aviso")).to_contain_text("No se ha guardado nada")
    ficha.get_by_label("Edad (años)").fill("38")
    ficha.get_by_role("button", name="Guardar").click()
    expect(p.locator(".ajuste", has_text="Edad")).to_contain_text("38 años")

    # a change to come
    p.locator(".ajuste", has_text="Altura").click()
    ficha.get_by_label("Altura (cm)").fill("178")
    ficha.get_by_label("Desde").fill((HOY + timedelta(days=5)).isoformat())
    ficha.get_by_role("button", name="Guardar").click()
    expect(p.locator("main")).to_contain_text("178 cm a partir del 26 de octubre")

    # the plan without a healthy limit asks for it
    p.locator(".ajuste", has_text="Objetivo del plan").click()
    ficha.get_by_label("Objetivo del plan (%)").fill("15")
    ficha.locator("button.signo").click()
    ficha.get_by_role("button", name="Guardar").click()
    expect(p.locator(".pregunta")).to_contain_text("No tienes límite saludable")
    p.locator(".pregunta").get_by_role("button", name="Ahora no").click()
    expect(p.locator(".ajuste", has_text="Objetivo del plan")).to_contain_text("−15 %")

    # races: add, change, remove with confirmation
    p.locator("#anadir-carrera").click()
    carrera = p.locator(".tarjeta.nueva")
    carrera.get_by_label("Carrera").fill("Triatlón sprint")
    carrera.get_by_label("Fecha").fill("2027-04-12")
    carrera.get_by_label("Días de mantenimiento antes").fill("3")
    carrera.get_by_role("button", name="Guardar").click()
    fila = p.locator(".ajuste", has_text="Triatlón sprint")
    expect(fila).to_contain_text("3 días de mantenimiento antes")
    fila.click()
    carrera.get_by_label("Carrera").fill("Triatlón olímpico")
    carrera.get_by_role("button", name="Guardar").click()
    fila = p.locator(".ajuste", has_text="Triatlón olímpico")
    expect(fila).to_be_visible()
    fila.click()
    carrera.get_by_role("button", name="Quitar carrera").click()
    expect(carrera).to_contain_text("¿Quitar Triatlón olímpico?")
    carrera.get_by_role("button", name="Sí, quitar").click()
    expect(p.locator(".ajuste", has_text="Triatlón")).to_have_count(0)


def test_ajustes_sin_conexion(servidor, abrir):  # R12
    p = abrir(servidor, "/ajustes")
    expect(p.locator("#conexion")).to_contain_text("conectado")
    p.route("**/api/ajustes", lambda ruta: ruta.abort())
    p.reload()
    expect(p.locator("#conexion")).to_contain_text("sin conexión")


# -- R13: offline -------------------------------------------------------------------------------------------

def test_sin_conexion_la_app_se_abre(servidor, abrir):  # R13
    p = abrir(servidor, service_worker=True)
    _listo(p)
    p.evaluate("navigator.serviceWorker.ready.then(() => true)")
    p.reload()  # the worker now controls the page and keeps the shell
    _listo(p)
    p.wait_for_function("navigator.serviceWorker.controller !== null")
    p.context.set_offline(True)
    p.reload()
    expect(p.locator("#queda")).to_be_visible()
    expect(p.locator("nav.pestanas")).to_be_visible()
    expect(p.locator("#sin-conexion")).to_be_visible()


# -- R14: ergonomics at 390 px, light and dark, and the screenshots ---------------------------------------

def _ergonomia(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= ANCHO, f"{pantalla}: horizontal scroll ({ancho}px)"
    assert p.evaluate("getComputedStyle(document.body).fontSize") == "17px"
    pequenos = p.evaluate("""() => [...document.querySelectorAll(
        'button, a[href], input, select, textarea, summary, [role=button], .fila, .ajuste')]
      .filter((e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !e.closest('[hidden]'); })
      .filter((e) => e.getBoundingClientRect().height < 44)
      .map((e) => `${e.tagName}#${e.id}.${e.className} "${(e.innerText || e.value || '').slice(0, 30)}" ${e.getBoundingClientRect().height}`)""")
    assert pequenos == [], f"{pantalla}: touch targets under 44 px: {pequenos}"


@pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
def test_ergonomia_y_capturas(arrancar, abrir, capturar, oscuro):  # R14
    servidor = arrancar({"objetivo_peso": {"kg": 70.0, "fecha": (HOY + timedelta(days=130)).isoformat()},
                         "edad": 38, "altura_cm": 178, "limite_saludable_pct": 20})
    servidor.dia_de_prueba()
    servidor.apuntar("Leche semidesnatada", HOY, 250, 115, unidad="ml")
    servidor.apuntar("Plátano", HOY, 1, 105, unidad="unidad")
    servidor.apuntar("Yogur natural", HOY, 125, 77)
    servidor.apuntar("Patata cocida", HOY, 200, 172, hoy=AYER)
    servidor.pesos_de_prueba()
    servidor.ajustes.anadir_carrera("Triatlón sprint", "2027-04-12", 3)
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, oscuro=oscuro)
    _listo(p)
    _ergonomia(p, f"hoy{sufijo}")
    capturar(p, f"hoy{sufijo}")
    # the last row of the list stays visible above the box and the tabs
    p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    p.wait_for_timeout(200)
    ultima = p.locator("#lista-dia .fila").last.bounding_box()
    abajo = p.locator("#abajo").bounding_box()
    assert ultima["y"] + ultima["height"] <= abajo["y"] + 1
    if not oscuro:
        capturar(p, "hoy-completo", completa=True)
    p.evaluate("window.scrollTo(0, 0)")
    p.locator("#abrir-hoja").click()
    expect(p.locator("#hoja")).to_be_visible()
    _ergonomia(p, f"hoy-hoja{sufijo}")
    capturar(p, f"hoy-hoja{sufijo}")
    p.locator("#cerrar-hoja").click()
    for pantalla in ("peso", "recetas"):
        p.locator(".pestana", has_text=pantalla.capitalize()).click()
        expect(p.locator(f"section#{pantalla}")).to_be_visible()
        if pantalla == "peso":
            expect(p.locator("#peso-cifra")).to_contain_text("72,4")
        _ergonomia(p, pantalla + sufijo)
        capturar(p, pantalla + sufijo)
    p.locator(".pestana", has_text="Ajustes").click()
    expect(p.locator("#conexion")).to_contain_text("conectado")
    _ergonomia(p, "ajustes" + sufijo)
    capturar(p, "ajustes" + sufijo)
    p.locator(".ajuste", has_text="Edad").click()
    _ergonomia(p, "ajustes-ficha" + sufijo)
