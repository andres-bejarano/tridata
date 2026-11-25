"""Unit 110 in a real Chromium at 390 × 844, light and dark (R7, C-420 of `recuperar-historico`): a run imported from
an invented Strava export opens in Fitness like Garmin's — its week, its laps of 1 km — and its session says «de
Strava»; one of Garmin does not. Uses the `conftest.py` of unit 010. Every zip, track and value is invented here.
"""
from __future__ import annotations

import csv
import io
import math
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.importar_strava import importar  # noqa: E402
from tridata.models import Activity  # noqa: E402
from tridata.nutricion import DESDE_SIEMPRE  # noqa: E402

HOY = date(2026, 1, 14)  # the server's «today»: a Wednesday before the Garmin watch
LUNES = date(2026, 1, 12)
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
ZONAS = [[55, 120], [121, 140], [141, 155], [156, 168], [169, 180]]
PULSO = {"fc_max": 180, "fc_reposo": 55, "zonas_fc": ZONAS, "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}
_M_POR_GRADO = 6371008.8 * math.pi / 180


def _gpx(inicio: datetime, metros: float, segundos: float) -> bytes:
    puntos = []
    for i in range(int(metros // 10) + 1):
        d = min(i * 10.0, metros)
        t = inicio + timedelta(seconds=segundos * d / metros)
        puntos.append(f'<trkpt lat="{d / _M_POR_GRADO:.9f}" lon="0.0"><ele>50</ele>'
                      f'<time>{t.strftime("%Y-%m-%dT%H:%M:%SZ")}</time><extensions><gpxtpx:TrackPointExtension>'
                      f"<gpxtpx:hr>{145 + i % 10}</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>")
    return ('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1" '
            'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><trk><trkseg>'
            f'{"".join(puntos)}</trkseg></trk></gpx>').encode()


def _importar(servidor, carpeta) -> None:
    cabeceras = ["ID de actividad", "Fecha de la actividad", "Nombre de la actividad", "Tipo de actividad",
                 "Tiempo transcurrido", "Distancia", "Nombre del archivo", "Tiempo transcurrido", "Distancia",
                 "Frecuencia cardíaca promedio"]
    texto = io.StringIO()
    escritor = csv.writer(texto)
    escritor.writerow(cabeceras)
    escritor.writerow(["4242", "12 ene 2026, 17:00:00", "Rodaje importado", "Carrera", "1500", "5,20",
                       "activities/4242.gpx", "1500.0", "5200.0", "150"])
    ruta = carpeta / "export_inventado.zip"
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("activities.csv", texto.getvalue())
        z.writestr("activities/4242.gpx", _gpx(datetime(2026, 1, 12, 17, tzinfo=timezone.utc), 5200, 1500))
    informe = importar(ruta, servidor.db)
    assert informe.importadas == 1
    servidor.store.save_activities([Activity(
        activity_id="g1", activity_date=LUNES + timedelta(days=1), activity_type="running", name="Rodaje de Garmin",
        duration_seconds=1800, distance_meters=6000, avg_hr=140)])
    servidor.ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)
    servidor.hoy = HOY


@TEMAS
def test_la_sesion_importada_dice_de_strava(servidor, abrir, ir_a, capturar, tmp_path, oscuro):  # R7 · C-420
    _importar(servidor, tmp_path)
    sufijo = "-oscuro" if oscuro else "-claro"
    p = abrir(servidor, "/#dia", oscuro=oscuro)
    ir_a(p, "fitness")
    p.locator(f'#fitness-grafica-semanas g.semana[data-lunes="{LUNES.isoformat()}"]').click()
    semana = p.locator("#fitness-semana")
    expect(semana.locator(".actividad-semana")).to_have_count(2)

    semana.locator(".actividad-semana", has_text="Rodaje importado").click()
    sesion = p.locator("#fitness-sesion")
    expect(sesion.locator(".fuente-sesion")).to_have_text("de Strava")
    expect(sesion.locator(".vuelta")).to_have_count(6)  # 5 of 1 km and the 200 m that are left
    expect(sesion.locator(".curva-pulso")).to_be_visible()
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"horizontal scroll ({ancho}px)"
    sesion.scroll_into_view_if_needed()
    capturar(p, f"importar-strava-sesion{sufijo}", completa=True)

    semana.locator(".actividad-semana", has_text="Rodaje de Garmin").click()
    expect(sesion.locator(".fuente-sesion")).to_have_count(0)
    expect(sesion).to_contain_text("Esta actividad no tiene vueltas.")
