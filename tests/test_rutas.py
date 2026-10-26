"""Unit 045 · the profile of a route (`plan/rutas.py`, REC-25 of `preparar-carrera`): from a GPX or from the points of
a course of Garmin, the altitude every 100 m (smoothed) and the stretches of climb, descent and flat with their
climb; a route without altitude says so and never invents a profile (R2, R3 · C-194, C-195); what goes to the model is
the profile, never a latitude or a longitude (R4 · C-196, G-57).

`tests/fixtures/ruta-inventada.gpx` is invented: 20 km due east on a line in the open Atlantic (lat 10.5, lon from
-30.25), flat with two climbs, of 70 m (km 4 to 6.5, down to km 9) and of 45 m (km 14 to 15.5, down to km 17), and a
small wobble of the altitude that the smoothing must not turn into climbs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tridata.plan.rutas import RutaMal, SinAltitud, perfil, perfil_para_modelo, puntos_garmin, puntos_gpx

GPX = Path(__file__).parent / "fixtures" / "ruta-inventada.gpx"
# what of the invented coordinates would betray them in a text: the latitude and the beginning of the longitude
COORDENADAS = ("10.5", "10,5", "-30.2", "-30,2", "30.2", "30,2")


def _sin_ele(texto: str) -> str:
    return re.sub(r"<ele>[^<]*</ele>", "", texto)


def _perfil() -> dict:
    return perfil(puntos_gpx(GPX.read_bytes()), "Ruta inventada", "gpx")


def test_perfil_cada_100_m_con_dos_subidas_y_su_desnivel():  # R2 · C-194
    p = _perfil()
    assert p["nombre"] == "Ruta inventada" and p["fuente"] == "gpx"
    assert p["distancia_km"] == pytest.approx(20.0, abs=0.2)
    assert p["paso_m"] == 100
    assert len(p["altitudes"]) == pytest.approx(201, abs=3)  # one every 100 m, from km 0
    assert min(p["altitudes"]) == pytest.approx(10, abs=3) and max(p["altitudes"]) == pytest.approx(80, abs=4)
    subidas = [t for t in p["tramos"] if t["terreno"] == "subida"]
    bajadas = [t for t in p["tramos"] if t["terreno"] == "bajada"]
    assert len(subidas) == 2, p["tramos"]  # the wobble does not make climbs
    assert len(bajadas) == 2, p["tramos"]
    primera, segunda = subidas
    assert primera["desde_km"] == pytest.approx(4.0, abs=0.6) and primera["hasta_km"] == pytest.approx(6.5, abs=0.6)
    assert primera["desnivel_m"] == pytest.approx(70, abs=8)
    assert segunda["desde_km"] == pytest.approx(14.0, abs=0.6) and segunda["hasta_km"] == pytest.approx(15.5, abs=0.6)
    assert segunda["desnivel_m"] == pytest.approx(45, abs=8)
    assert all(t["desnivel_m"] < 0 for t in bajadas)
    # the stretches cover the route, one after the other, never two of the same terrain together
    assert p["tramos"][0]["desde_km"] == 0 and p["tramos"][-1]["hasta_km"] == p["distancia_km"]
    for a, b in zip(p["tramos"], p["tramos"][1:], strict=False):
        assert a["hasta_km"] == b["desde_km"] and a["terreno"] != b["terreno"]
    assert p["sube_m"] == pytest.approx(115, abs=15) and p["baja_m"] == pytest.approx(115, abs=15)


def test_sin_altitud_lo_dice_y_no_inventa_un_perfil():  # R3 · C-195
    with pytest.raises(SinAltitud, match="altitud"):
        perfil(puntos_gpx(_sin_ele(GPX.read_text())), "Sin altitud", "gpx")


@pytest.mark.parametrize("datos", [b"", b"esto no es un gpx", b"<gpx xmlns='http://www.topografix.com/GPX/1/1'/>"],
                         ids=["vacio", "texto", "sin-puntos"])
def test_lo_que_no_es_una_ruta_lo_dice(datos):  # R3
    with pytest.raises(RutaMal):
        perfil(puntos_gpx(datos), "Nada", "gpx")


def _de_garmin(con_altitud: bool = True) -> list[dict]:
    """The points of a course of Garmin as `GarminClient.ruta` gives them, made from the invented GPX."""
    puntos = puntos_gpx(GPX.read_bytes())
    return [{"distancia_m": x.distancia_m, "altitud_m": x.altitud_m if con_altitud else None, "lat": x.lat,
             "lon": x.lon} for x in puntos]


def test_una_ruta_de_garmin_da_el_mismo_perfil():  # R1, R2
    desde_garmin = perfil(puntos_garmin(_de_garmin()), "Ruta inventada", "garmin")
    assert desde_garmin["fuente"] == "garmin"
    assert [t["terreno"] for t in desde_garmin["tramos"]] == [t["terreno"] for t in _perfil()["tramos"]]
    with pytest.raises(SinAltitud):
        perfil(puntos_garmin(_de_garmin(con_altitud=False)), "Sin altitud", "garmin")


def test_al_modelo_va_el_perfil_y_ninguna_coordenada():  # R4 · C-196, G-57
    p = _perfil()
    guardado = json.dumps(p, ensure_ascii=False)
    texto = perfil_para_modelo(p)
    for dicho in (guardado, texto):  # neither what is kept with the conversation nor what goes to the model
        for c in COORDENADAS:
            assert c not in dicho, c
        assert not re.search(r"\b(lat|lon|latitud|longitud)\b", dicho.lower().replace("latitudes ni longitudes", ""))
    assert "Ruta inventada" in texto
    assert texto.count("subida") >= 2 and "bajada" in texto and "llano" in texto  # the stretches
    assert "cada 100 m" in texto  # the altitude every 100 m
    assert re.search(r"subida \+(6[2-9]|7[0-8]) m", texto), texto  # the first climb with its climb
    # every altitude, in its order, and nothing else of the route
    lista = re.search(r"cada 100 m[^:]*: ([0-9, ]+)", texto)
    assert lista is not None
    assert [int(x) for x in lista.group(1).replace(" ", "").split(",") if x] == p["altitudes"]
