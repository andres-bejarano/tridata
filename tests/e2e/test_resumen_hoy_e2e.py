"""Unit 030 in a real Chromium at 390 × 844, light and dark: «Hoy de un vistazo» (recorrido E2E-3).

The app opens in Hoy with four cards (Comida, Entreno, Cómo llegas, Asistente); each card takes to its place;
«↻ Actualizar Garmin» says it is running, brings the new activity and the new hour, or says why it failed; a
datum that has not arrived says so; the box dictates from Hoy as in Nutrición › Día.
Plans `ver-dia-movil` REC-18: C-138, C-139, C-141..C-145. R1-R9 of the unit. Uses the `conftest.py` of unit 010:
Claude, Whisper and «Actualizar Garmin» are `MagicMock`s; «today» is Wednesday 2026-10-21. Invented data only.
"""
from __future__ import annotations

import re
import threading
from datetime import date, datetime, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.garmin_client import GarminAuthError  # noqa: E402
from tridata.ia import AlimentoInterpretado, Interpretacion, PesoInterpretado  # noqa: E402
from tridata.models import Activity, DailyStats, HRVRecord, SleepRecord  # noqa: E402
from tridata.sync import MOTIVOS, cerrojo_sync  # noqa: E402

HOY = date(2026, 10, 21)  # the `HOY` of conftest.py
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
EN_MARCHA = "Ya estoy actualizando Garmin: espera a que acabe."


def _garmin_de_prueba(servidor, *, hrv_de_anoche: bool = True, carrera: bool = True) -> None:
    """A run of 45 min today, last night's sleep, 7 nights of HRV, resting HR and a good sync at 9:05."""
    if carrera:
        servidor.store.save_activities([Activity(
            activity_id="e2e-carrera", activity_date=HOY, activity_type="running", name="Carrera suave",
            duration_seconds=2700, avg_hr=142, calories=520)])
    servidor.store.save_sleep(SleepRecord(sleep_date=HOY, total_sleep_seconds=25920, sleep_score=82))
    servidor.store.save_daily_stats(DailyStats(stat_date=HOY, steps=11000, resting_hr=48, total_calories=2700))
    for i in range(7):
        if i or hrv_de_anoche:
            servidor.store.save_hrv(HRVRecord(hrv_date=HOY - timedelta(days=i), last_night_avg=60 + i % 3))
    servidor.sync_de_prueba(datetime(2026, 10, 21, 9, 5))


def _platano_y_peso(texto, hoy):
    return Interpretacion(
        alimentos=[AlimentoInterpretado(nombre="plátano", cantidad=120, unidad="g", dia=hoy, kcal=107,
                                        proteina_g=None, hidratos_g=None, grasa_g=None, estimado=True, dudoso=False,
                                        supuestos=[])],
        peso=PesoInterpretado(kg=70.2, dia=hoy, nota=None), objetivo_dia=None, dudosas=[])


def _abrir_hoy(abrir, servidor, ruta="/", **k):
    p = abrir(servidor, ruta, esperar=False, **k)
    expect(p.locator("section#hoy")).to_be_visible()
    expect(p.locator("#hoy-comida .cifra")).not_to_have_text("—")
    return p


def _marcada(p) -> str:
    return p.locator('nav.pestanas .pestana[aria-current="page"]').inner_text().strip()


def _sin_scroll(p) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"horizontal scroll ({ancho}px)"


# -- R1 · C-138: the app opens in Hoy with the four cards ------------------------------------------------------

@TEMAS
def test_abre_en_hoy_con_las_cuatro_tarjetas(servidor, abrir, capturar, oscuro):  # R1, R3, R9 · C-138, C-140
    servidor.dia_de_prueba()
    _garmin_de_prueba(servidor)
    p = _abrir_hoy(abrir, servidor, oscuro=oscuro)
    assert _marcada(p) == "Hoy"
    expect(p.locator("section#dia")).to_be_hidden()
    expect(p.locator("#hoy-comida")).to_contain_text("760 kcal comidas")
    expect(p.locator("#hoy-entreno")).to_contain_text("Carrera suave")
    expect(p.locator("#hoy-entreno")).to_contain_text("45 min")
    expect(p.locator("#hoy-entreno")).to_contain_text("520 kcal")
    llegas = p.locator("#hoy-como-llegas")
    expect(llegas).to_contain_text("7 h 12 min")
    expect(llegas).to_contain_text("reposo 48")
    expect(llegas).to_contain_text("HRV 60 ms")
    expect(llegas).to_contain_text("media 7 días")
    expect(llegas).to_contain_text("Frescura")
    expect(p.locator("#hoy-garmin-hora")).to_have_text("Garmin: sincronizado a las 9:05")
    expect(p.locator("#hoy-actualizar")).to_have_text("↻ Actualizar Garmin")
    expect(p.locator("#hoy-asistente")).to_contain_text("Aún no has hablado con el asistente")
    expect(p.locator("#entrada")).to_be_visible()  # R8: the box and the microphone in Hoy too
    expect(p.locator("#grabar")).to_be_visible()
    for t in p.locator("section#hoy .tarjeta-hoy").all():
        assert t.bounding_box()["height"] >= 44
    _sin_scroll(p)
    capturar(p, f"hoy-con-datos-{'oscuro' if oscuro else 'claro'}", completa=True)

    # R1: the same «te quedan» as Nutrición › Día for the marked level
    queda_hoy = p.locator("#hoy-comida .cifra").inner_text()
    p.locator("#hoy-comida").click()
    expect(p.locator("section#dia")).to_be_visible()
    expect(p.locator("#queda-cifra")).to_have_text(queda_hoy.split()[0])


def test_hoy_sin_entreno_y_sin_hrv(servidor, abrir, capturar):  # R1, R6 · C-143, G-47
    _garmin_de_prueba(servidor, hrv_de_anoche=False, carrera=False)
    p = _abrir_hoy(abrir, servidor)
    expect(p.locator("#hoy-entreno")).to_contain_text("Hoy aún no has entrenado")
    expect(p.locator("#hoy-hrv")).to_contain_text("aún no ha llegado")
    assert "HRV 0" not in p.locator("#hoy-como-llegas").inner_text()
    capturar(p, "hoy-sin-hrv-claro", completa=True)
    p2 = _abrir_hoy(abrir, servidor, oscuro=True)
    expect(p2.locator("#hoy-hrv")).to_contain_text("aún no ha llegado")
    capturar(p2, "hoy-sin-hrv-oscuro", completa=True)


# -- R2 · C-139: each card takes to its place ------------------------------------------------------------------

def test_cada_tarjeta_lleva_a_su_sitio(servidor, abrir):  # R2 · C-139
    _garmin_de_prueba(servidor)
    p = _abrir_hoy(abrir, servidor)
    for tarjeta, sitio, pestana in (("hoy-comida", "dia", "Nutrición"), ("hoy-entreno", "fitness", "Fitness"),
                                    ("hoy-como-llegas", "fitness", "Fitness"), ("hoy-asistente", "asistente",
                                                                                "Asistente")):
        p.locator(f"#{tarjeta} .ver-mas").click()
        expect(p).to_have_url(f"{servidor.url}/#{sitio}")
        expect(p.locator(f"section#{sitio}")).to_be_visible()
        assert _marcada(p) == pestana
        p.locator("nav.pestanas .pestana", has_text="Hoy").click()
        expect(p.locator("section#hoy")).to_be_visible()


# -- R4 · C-141, Q-13: the button runs, then the activity and the new hour show --------------------------------

@TEMAS
def test_actualizar_garmin_desde_hoy(servidor, abrir, capturar, oscuro):  # R4 · C-141, Q-13
    _garmin_de_prueba(servidor)
    dentro, soltar = threading.Event(), threading.Event()

    def traer(dia):
        dentro.set()
        assert soltar.wait(10)
        servidor.store.save_activities([Activity(activity_id="e2e-bici", activity_date=dia, activity_type="road_biking",
                                                 name="Bici por la tarde", duration_seconds=3600, calories=700)])

    servidor.actualizar_garmin.side_effect = traer
    p = _abrir_hoy(abrir, servidor, oscuro=oscuro)
    servidor.ahora = datetime(2026, 10, 21, 18, 42)
    try:
        p.locator("#hoy-actualizar").click()
        expect(p.locator("#hoy-actualizar")).to_have_text("Actualizando Garmin…")
        expect(p.locator("#hoy-actualizar")).to_be_disabled()
        assert dentro.wait(10)
        capturar(p, f"hoy-boton-en-marcha-{'oscuro' if oscuro else 'claro'}", completa=True)
    finally:
        soltar.set()
    expect(p.locator("#hoy-entreno")).to_contain_text("Bici por la tarde")
    expect(p.locator("#hoy-garmin-hora")).to_have_text("Garmin: sincronizado a las 18:42")
    expect(p.locator("#hoy-actualizar")).to_have_text("↻ Actualizar Garmin")
    servidor.actualizar_garmin.assert_called_once_with(HOY)


# -- R5 · C-142: a failure, with the routine's words; the hour stays -------------------------------------------

def test_actualizar_que_falla(servidor, abrir):  # R5 · C-142
    _garmin_de_prueba(servidor)
    servidor.actualizar_garmin.side_effect = GarminAuthError("token caducado")
    p = _abrir_hoy(abrir, servidor)
    p.locator("#hoy-actualizar").click()
    expect(p.locator("#hoy-garmin-error")).to_contain_text(MOTIVOS["sesion"])
    expect(p.locator("#hoy-garmin-hora")).to_have_text("Garmin: sincronizado a las 9:05")
    expect(p.locator("#hoy-entreno")).to_contain_text("Carrera suave")


# -- R7 · C-144: the routine is syncing ------------------------------------------------------------------------

def test_con_la_rutina_en_marcha(servidor, abrir):  # R7 · C-144
    _garmin_de_prueba(servidor)
    p = _abrir_hoy(abrir, servidor)
    with cerrojo_sync(servidor.db.parent):
        p.locator("#hoy-actualizar").click()
        expect(p.locator("#hoy-garmin-error")).to_have_text(EN_MARCHA)
    servidor.actualizar_garmin.assert_not_called()
    expect(p.locator("#hoy-garmin-hora")).to_have_text("Garmin: sincronizado a las 9:05")


# -- R8 · C-145: dictating from Hoy ----------------------------------------------------------------------------

def test_dictar_desde_hoy(servidor, abrir, capturar):  # R8 · C-145
    _garmin_de_prueba(servidor)
    servidor.interprete.interpretar.side_effect = _platano_y_peso
    p = _abrir_hoy(abrir, servidor)
    expect(p.locator("#hoy-comida")).to_contain_text("0 kcal comidas")
    p.locator("#texto").fill("un plátano y peso 70,2")
    p.locator("#enviar").click()
    revision = p.locator("section#hoy #revision")
    expect(revision).to_be_visible()
    expect(revision.locator(".tarjeta.peso input").first).to_have_value("70,2")
    expect(revision.locator("#lista input[type=text]").first).to_have_value("Plátano")  # the review writes names capitalised
    assert _marcada(p) == "Hoy"
    capturar(p, "hoy-revision-claro", completa=True)
    revision.get_by_role("button", name="Confirmar y guardar").click()
    # the banana's kcal are the review's (a common food may correct Claude's estimate): never 0 any more
    expect(p.locator("#hoy-comida-detalle")).to_have_text(re.compile(r"^[1-9][\d.]* kcal comidas · "))
    expect(p.locator("section#hoy")).to_be_visible()
    comidas = p.locator("#hoy-comida-detalle").inner_text().split(" · ")[0]
    # the same review goes back to Nutrición › Día, and Día says the same kcal
    p.locator("nav.pestanas .pestana", has_text="Nutrición").click()
    expect(p.locator("section#dia #revision")).to_have_count(1)
    expect(p.locator("#lista-dia .fila", has_text="plátano")).to_be_visible()
    expect(p.locator("#total")).to_have_text(comidas)


# -- R1: the addresses that open Hoy (decision of 2026-10-24) and reloading in Hoy ----------------------------

@pytest.mark.parametrize("ruta", ["/", "/#hoy", "/?ir=hoy#hoy"], ids=["nada", "hoy", "desde-ajustes"])
def test_direcciones_que_abren_hoy(servidor, abrir, ruta):  # R1 · decision 2026-10-24
    p = _abrir_hoy(abrir, servidor, ruta)
    assert _marcada(p) == "Hoy"
    expect(p).to_have_url(servidor.url + "/#hoy")
    p.reload()
    expect(p.locator("section#hoy")).to_be_visible()
    expect(p.locator("section#dia")).to_be_hidden()
    assert _marcada(p) == "Hoy"
