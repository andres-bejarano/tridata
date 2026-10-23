"""Unit 018 in a real Chromium at 390 × 844, light and dark. Invented data only.

R2, R3 the Garmin warning in Hoy and its «Actualizar Garmin» · R7 the Balance chart between two dates · R13 the
same with the weight of today above it (unit 027: the Peso tab is inside Balance) · R9, R11 spreading again and
the «repartido» label · R12 ◀ before the first day arrives.
Claude, Whisper and «Actualizar Garmin» are `MagicMock`s (conftest.py); «today» is Wednesday 2026-10-21 and the
server's clock says 10:00.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.models import DailyStats  # noqa: E402

HOY = date(2026, 10, 21)
MARTES = HOY - timedelta(days=1)
ANCHO = 390
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
AVISO = ("Garmin no se ha sincronizado desde el martes 20 a las 08:01. Se vuelve a intentar sola cada hora hasta "
         "las 14:00.")
AL_REVES = "La fecha de inicio tiene que ser anterior o igual a la de fin"
LUNES_DE_AGOSTO = ["3 ago", "10 ago", "17 ago", "24 ago", "31 ago"]
TEXTO_ESPERA = "Garmin aún no tiene el día completo: sigue con el gasto estimado"


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= ANCHO, f"{pantalla}: horizontal scroll ({ancho}px)"


def _alto_minimo(locator, pantalla: str) -> None:
    caja = locator.bounding_box()
    assert caja is not None and caja["height"] >= 44, f"{pantalla}: touch target under 44 px"


def _textos(locator) -> list[str]:
    return [re.sub(r"\s+", " ", t).strip() for t in locator.all_inner_texts()]


def _etiquetas_separadas(grafica) -> list[str]:
    """The X labels, checked: none overlaps the next one and their centres are 44 px or more apart."""
    etiquetas = grafica.locator(".etiqueta-x")
    cajas = [etiquetas.nth(i).bounding_box() for i in range(etiquetas.count())]
    for a, b in zip(cajas, cajas[1:], strict=False):
        assert a["x"] + a["width"] <= b["x"], "two X labels overlap"
        assert (b["x"] + b["width"] / 2) - (a["x"] + a["width"] / 2) >= 44, "two X labels closer than 44 px"
    return [t.strip() for t in etiquetas.all_text_contents()]


# -- R2, R3: the Garmin warning in Hoy ---------------------------------------------------------------------

@TEMAS
def test_aviso_de_garmin_y_su_boton(servidor, abrir, capturar, oscuro):  # R2, R3 · R-91, R-90
    servidor.sync_de_prueba(datetime(2026, 10, 20, 8, 1), datetime(2026, 10, 21, 8, 3), "sesion")
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, oscuro=oscuro)
    aviso = p.locator("#avisos .aviso-sync")
    expect(aviso).to_have_count(1)
    expect(aviso.locator("p").first).to_have_text(AVISO)
    boton = p.locator("#aviso-sync-actualizar")
    expect(boton).to_have_text("Actualizar Garmin")
    _alto_minimo(boton, "aviso-sync")
    _sin_scroll(p, "aviso" + sufijo)
    capturar(p, f"aviso-sync{sufijo}")

    boton.click()

    expect(p.locator(".aviso-sync")).to_have_count(0)
    servidor.actualizar_garmin.assert_called_once_with(HOY)
    assert servidor.store.estado_sync()["ok_en"] == "2026-10-21T10:00:00"


def test_aviso_que_no_sale_en_un_dia_pasado(servidor, abrir):  # R2
    servidor.sync_de_prueba(datetime(2026, 10, 19, 8, 1))
    p = abrir(servidor)
    expect(p.locator(".aviso-sync")).to_contain_text("desde el lunes 19 a las 08:01")
    p.locator("#anterior").click()
    expect(p.locator("#titulo")).to_have_text("Ayer")
    expect(p.locator(".aviso-sync")).to_have_count(0)


def test_sin_aviso_un_dia_normal(servidor, abrir):  # R2
    servidor.sync_de_prueba(datetime(2026, 10, 21, 8, 1))
    p = abrir(servidor)
    expect(p.locator("#titulo")).to_have_text("Hoy")
    expect(p.locator(".aviso-sync")).to_have_count(0)


def test_el_boton_con_garmin_limitando(servidor, abrir):  # R3
    from tridata.garmin_client import GarminRateLimitError

    servidor.sync_de_prueba(datetime(2026, 10, 19, 8, 1))
    servidor.actualizar_garmin.side_effect = GarminRateLimitError("429")
    p = abrir(servidor)
    p.locator("#aviso-sync-actualizar").click()
    expect(p.locator(".aviso-sync")).to_contain_text(
        "Garmin está limitando las peticiones: espera unos minutos y vuelve a probar.")
    expect(p.locator(".aviso-sync")).to_contain_text("desde el lunes 19 a las 08:01")


# -- R7: the Balance chart between two dates ---------------------------------------------------------------

def _elegir(p, prefijo: str, desde: str, hasta: str) -> None:
    p.locator(f"#{prefijo}-desde").fill(desde)
    p.locator(f"#{prefijo}-hasta").fill(hasta)
    p.locator(f"#{prefijo}-ver").click()


@TEMAS
def test_grafica_de_balance_entre_fechas(servidor, abrir, capturar, oscuro):  # R7 · P-20261022-83532c43
    servidor.historial_de_prueba(date(2026, 7, 27))
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#balance", oscuro=oscuro)
    grafica = p.locator("#balance-grafica svg")
    expect(grafica.locator(".barra-kcal")).to_have_count(28)
    selector = p.locator("#balance-selector button")
    assert _textos(selector) == ["4 semanas", "3 meses", "Todo", "Fechas…"]
    fechas = p.locator("#balance-fechas-boton")
    fechas.click()
    expect(fechas).to_have_attribute("aria-pressed", "true")
    expect(p.locator("#balance-desde")).to_have_attribute("min", "2026-07-27")
    expect(p.locator("#balance-desde")).to_have_attribute("max", "2026-10-21")
    expect(p.locator("#balance-hasta")).to_have_attribute("max", "2026-10-21")
    _alto_minimo(p.locator("#balance-ver"), "ver")

    _elegir(p, "balance", "2026-08-01", "2026-09-01")

    expect(grafica.locator(".barra-kcal")).to_have_count(32)
    expect(grafica.locator(".barra-kcal.provisional")).to_have_count(0)
    assert _etiquetas_separadas(grafica) == LUNES_DE_AGOSTO
    expect(p.locator("#balance-periodo")).to_have_text("del 1 ago al 1 sept")
    _sin_scroll(p, "balance-fechas" + sufijo)
    p.locator("#balance-grafica").scroll_into_view_if_needed()
    capturar(p, f"balance-fechas{sufijo}")

    # another range and back: the two dates are kept
    p.locator("#balance-rangos button", has_text="4 semanas").click()
    expect(grafica.locator(".barra-kcal")).to_have_count(28)
    expect(fechas).to_have_attribute("aria-pressed", "false")
    fechas.click()
    expect(p.locator("#balance-desde")).to_have_value("2026-08-01")
    expect(p.locator("#balance-hasta")).to_have_value("2026-09-01")
    expect(grafica.locator(".barra-kcal")).to_have_count(32)

    # the other way round: said, nothing asked, the chart stays
    pedidas = []
    p.on("request", lambda r: pedidas.append(r.url) if "/api/balance?" in r.url else None)
    _elegir(p, "balance", "2026-09-10", "2026-09-01")
    expect(p.locator("#balance-fechas-error")).to_have_text(AL_REVES)
    expect(grafica.locator(".barra-kcal")).to_have_count(32)
    assert pedidas == []
    p.locator("#balance-grafica").scroll_into_view_if_needed()
    capturar(p, f"balance-fechas-al-reves{sufijo}")


def test_fechas_antes_del_primer_dia_y_sin_datos(servidor, abrir):  # R7, R8
    servidor.historial_de_prueba(date(2026, 7, 27))
    p = abrir(servidor, "/#balance")
    p.locator("#balance-fechas-boton").click()
    _elegir(p, "balance", "2026-07-01", "2026-08-15")
    expect(p.locator("#balance-grafica svg .barra-kcal")).to_have_count(20)
    expect(p.locator("#balance-fechas-nota")).to_have_text("Tus datos empiezan el 27 de julio")
    p.locator("#balance-desde").evaluate("(e) => e.removeAttribute('min')")
    _elegir(p, "balance", "2026-05-01", "2026-05-31")
    expect(p.locator("#balance-grafica")).to_contain_text("No hay días con datos entre esas fechas")
    expect(p.locator("#balance-grafica svg")).to_have_count(0)


# -- R13: between dates, the weight of today stays today's (unit 027: the Peso tab is inside Balance) --------

@TEMAS
def test_peso_entre_fechas(servidor, abrir, capturar, oscuro):  # R13
    servidor.historial_de_prueba(date(2026, 7, 27))
    servidor.pesos_de_prueba()
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#balance", oscuro=oscuro)
    tarjeta = p.locator("#balance-grafica")
    grafica = tarjeta.locator("svg")
    expect(p.locator("#balance-rangos button[aria-pressed=true]")).to_have_text("4 semanas")
    expect(grafica.locator(".barra-kcal")).to_have_count(28)
    expect(p.locator("#peso-cifra")).to_contain_text("72,4")
    p.locator("#balance-ultimos-pesos summary").click()  # folded at first
    basal = p.locator("#peso-basal").inner_text()
    objetivo = p.locator("#peso-objetivo").inner_text()
    ultimos = _textos(p.locator("#peso-ultimos .kcal"))
    assert ultimos
    boton = p.locator("#balance-fechas-boton")
    expect(boton).to_have_text("Fechas…")
    _alto_minimo(boton, "balance-fechas")

    boton.click()
    _alto_minimo(p.locator("#balance-ver"), "balance-ver")
    _elegir(p, "balance", "2026-08-01", "2026-09-01")

    expect(grafica.locator(".barra-kcal")).to_have_count(32)
    assert _etiquetas_separadas(grafica) == LUNES_DE_AGOSTO
    expect(p.locator("#balance-periodo")).to_have_text("del 1 ago al 1 sept")
    expect(p.locator("#peso-cifra")).to_contain_text("72,4")  # the rest is still today's
    assert p.locator("#peso-basal").inner_text() == basal
    assert p.locator("#peso-objetivo").inner_text() == objetivo
    assert _textos(p.locator("#peso-ultimos .kcal")) == ultimos
    _sin_scroll(p, "peso-fechas" + sufijo)
    tarjeta.scroll_into_view_if_needed()
    capturar(p, f"peso-fechas{sufijo}")

    _elegir(p, "balance", "2026-09-10", "2026-09-01")
    expect(p.locator("#balance-fechas-error")).to_have_text(AL_REVES)
    expect(grafica.locator(".barra-kcal")).to_have_count(32)

    p.locator("#balance-rangos button", has_text="4 semanas").click()
    expect(grafica.locator(".barra-kcal")).to_have_count(28)
    expect(p.locator("#peso-cifra")).to_contain_text("72,4")
    _sin_scroll(p, "balance tras fechas")


# -- R9, R11: spreading again and the label ------------------------------------------------------------------

def test_repartir_otra_vez_y_la_etiqueta(servidor, abrir, capturar):  # R9, R11 · R-20
    servidor.semana_de_prueba()
    p = abrir(servidor, "/#balance")
    filas = p.locator("#balance-semana .fila-dia")
    expect(filas).to_have_count(7)
    p.locator("#repartir").click()
    p.locator("#reparto-aplicar").click()
    for i in (2, 3, 4):
        expect(filas.nth(i)).to_contain_text("−22,5 % · repartido")

    p.locator("#repartir").click()
    expect(p.locator("#reparto-texto")).to_have_text(
        "Lo que llevas por encima ya está repartido en los días que quedan.")
    expect(p.locator("#reparto-aplicar")).to_have_count(0)
    _sin_scroll(p, "ya-repartido")
    capturar(p, "balance-ya-repartido")

    # Hoy, −15 % by hand: today loses «repartido», the others keep it
    p.locator(".pestana", has_text="Hoy").click()
    p.locator("#abrir-hoja").click()
    p.locator("#abrir-pct").click()
    p.locator("#objetivo-pct").fill("15")
    p.locator("#objetivo-hoy button.signo").click()
    p.locator("#objetivo-guardar").click()
    expect(p.locator("#objetivo-valor")).to_have_text("−15 % · hoy")
    p.locator("#cerrar-hoja").click()
    p.locator(".pestana", has_text="Balance").click()
    expect(filas.nth(2)).to_contain_text("−15 %")
    expect(filas.nth(2)).not_to_contain_text("repartido")
    for i in (3, 4):
        expect(filas.nth(i)).to_contain_text("−22,5 % · repartido")


# -- R12: ◀ before the first day arrives -------------------------------------------------------------------

def test_primer_dia_lento(servidor, abrir):  # R12 · R-26, P-20261022-ebe54104
    servidor.apuntar("Comida", MARTES, 1, 2000, hoy=MARTES, unidad="ración")
    servidor.store.save_daily_stats(DailyStats(stat_date=MARTES, steps=10000, total_calories=1450))
    retenidas = []

    def retener(ruta):
        if not retenidas and ruta.request.url.endswith("/api/dia"):
            retenidas.append(ruta)  # the first answer of /api/dia is held 1,5 s
        else:
            ruta.continue_()

    p = abrir(servidor, antes=lambda pagina: pagina.route("**/api/dia*", retener), esperar=False)
    anterior = p.locator("#anterior")
    anterior.click(force=True, no_wait_after=True)  # nada más abrir
    expect(anterior).to_be_disabled()
    p.wait_for_timeout(1500)
    assert len(retenidas) == 1
    retenidas[0].continue_()

    expect(p.locator("body")).to_have_attribute("data-dia", "2026-10-21")
    expect(p.locator("#titulo")).to_have_text("Hoy")  # ◀ did nothing while the day was not there
    anterior.click()
    expect(p.locator("#titulo")).to_have_text("Ayer")
    expect(p.locator("#cierre-estado")).to_have_text(TEXTO_ESPERA)
