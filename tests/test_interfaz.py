"""Unit 010 (the new phone screen): what the API adds and the app icon, with TestClient.

R3: `/api/dia` also brings the planned kcal and the day «with the planned», computed with the
same `simular` as the review. R10: `GET /api/peso`, the weights of 30 days and their 7-day mean.
R13: the icon, the manifest and the service worker shell. All data are invented; the Claude
interpreter is a `MagicMock` (no API).
"""
import re
import sqlite3
import struct
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas, RegistroPeso
from tridata.web import crear_app

HOY = date(2026, 10, 21)
AYER = HOY - timedelta(days=1)


class Entorno:
    def __init__(self, tmp_path, **extra_ajustes):
        self.db = tmp_path / "data.sqlite3"
        self.registro = RegistroComidas(self.db)
        self.pesos = RegistroPeso(self.db)
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000,
                              "objetivo_pct": -10, **extra_ajustes}, desde=DESDE_SIEMPRE)
        self.cliente = TestClient(crear_app(self.registro, MagicMock(), self.ajustes, hoy=lambda: HOY,
                                            pesos=self.pesos))

    def apuntar(self, nombre, dia, cantidad, kcal, *, hoy=HOY, unidad="g", estimado=False):
        alimento = Alimento(nombre=nombre, dia=dia, cantidad=cantidad, unidad=unidad if cantidad else None,
                            kcal=kcal, estimado=estimado)
        return self.registro.guardar([alimento], hoy=hoy, texto=nombre, sin_cambios=True)[0]

    def filas(self, tabla):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]  # noqa: S608 - fixed names


def _dia_de_prueba(tmp_path):
    """The test day of the contract: 760 kcal eaten, salmon (374) and fruit (no quantity) planned yesterday."""
    e = Entorno(tmp_path)
    e.apuntar("Avena", HOY, 60, 228, estimado=True)
    e.apuntar("Arroz con pollo", HOY, 350, 532)
    e.apuntar("Salmón al horno", HOY, 180, 374, hoy=AYER)
    e.apuntar("Fruta", HOY, None, None, hoy=AYER)
    return e


# -- R3: the planned kcal and the day «with the planned» ---------------------------------------

def test_dia_trae_lo_planeado_y_lo_que_quedaria(tmp_path):  # R3
    e = _dia_de_prueba(tmp_path)
    dia = e.cliente.get("/api/dia").json()

    assert [(a["nombre"], a["estado"]) for a in dia["alimentos"]] == [
        ("Avena", "comido"), ("Arroz con pollo", "comido"), ("Salmón al horno", "planeado"), ("Fruta", "planeado")]
    assert dia["planeado_kcal"] == 374  # the fruit without quantity does not add
    con = dia["con_planeado"]
    assert con["comido_kcal"] == 1134
    assert [m["kcal"] for m in con["margenes"]] == [666, 866, 1066]
    assert con["barra"]["comido_kcal"] == 1134 and con["barra"]["objetivo_kcal"] == 1800
    # nothing that was there changes: the planned does not subtract from the margin (rule of unit 004)
    assert [m["kcal"] for m in dia["margenes"]] == [1040, 1240, 1440]
    assert dia["total_kcal"] == 760
    assert dia["barra"]["comido_kcal"] == 760 and dia["barra"]["pct"] == 42


def test_con_planeado_es_lo_mismo_que_simular(tmp_path):  # R3
    e = _dia_de_prueba(tmp_path)
    dia = e.cliente.get("/api/dia").json()
    from tridata.nutricion import simular
    assert dia["con_planeado"] == simular(dia, 374)


def test_dia_sin_planeado(tmp_path):  # R3
    e = Entorno(tmp_path)
    e.apuntar("Avena", HOY, 60, 228)
    dia = e.cliente.get("/api/dia").json()
    assert dia["planeado_kcal"] == 0
    assert dia["con_planeado"]["margenes"] == dia["margenes"]
    assert dia["con_planeado"]["comido_kcal"] == dia["total_kcal"] == 228


def test_dia_futuro_con_500_planeadas(tmp_path):  # R3, R16
    e = Entorno(tmp_path)
    futuro = HOY + timedelta(days=2)
    e.apuntar("Lentejas", futuro, 300, 345)
    e.apuntar("Pan", futuro, 60, 155)
    dia = e.cliente.get(f"/api/dia?fecha={futuro.isoformat()}").json()
    assert dia["planeado_kcal"] == 500
    assert [m["kcal"] for m in dia["margenes"]] == [1800, 2000, 2200]  # nothing planned subtracts
    assert [m["kcal"] for m in dia["con_planeado"]["margenes"]] == [1300, 1500, 1700]


# -- R10: the weights of 30 days --------------------------------------------------------------------

def _con_pesos(tmp_path, objetivo=True):
    extra = {"objetivo_peso": {"kg": 70.0, "fecha": (HOY + timedelta(days=130)).isoformat()}} if objetivo else {}
    e = Entorno(tmp_path, **extra)
    for hace, kg in ((40, 74.1), (10, 73.0), (1, 72.6), (0, 72.4)):
        e.pesos.apuntar(HOY - timedelta(days=hace), kg, hoy=HOY)
    return e


def test_peso_de_30_dias_con_medias_y_objetivo(tmp_path):  # R10
    e = _con_pesos(tmp_path)
    antes = e.filas("peso_dia")
    r = e.cliente.get(f"/api/peso?hasta={HOY.isoformat()}")

    assert r.status_code == 200
    datos = r.json()
    assert datos["hasta"] == HOY.isoformat() and datos["dias"] == 30
    assert [(p["dia"], p["kg"]) for p in datos["pesos"]] == [
        ((HOY - timedelta(days=10)).isoformat(), 73.0), (AYER.isoformat(), 72.6), (HOY.isoformat(), 72.4)]
    medias = {m["dia"]: m["kg"] for m in datos["medias"]}
    esperadas = {(HOY - timedelta(days=h)).isoformat(): 73.0 for h in range(4, 11)}
    esperadas[AYER.isoformat()] = 72.6
    esperadas[HOY.isoformat()] = 72.5
    assert medias == esperadas  # 3 and 2 days ago have none
    assert [m["dia"] for m in datos["medias"]] == sorted(medias)
    assert datos["objetivo"] == {"kg": 70.0, "fecha": (HOY + timedelta(days=130)).isoformat()}
    assert e.filas("peso_dia") == antes  # it writes nothing


def test_peso_sin_hasta_es_hoy_y_sin_objetivo_es_null(tmp_path):  # R10
    e = _con_pesos(tmp_path, objetivo=False)
    datos = e.cliente.get("/api/peso").json()
    assert datos["hasta"] == HOY.isoformat()
    assert len(datos["pesos"]) == 3
    assert datos["objetivo"] is None


def test_peso_hasta_otro_dia(tmp_path):  # R10
    e = _con_pesos(tmp_path)
    datos = e.cliente.get(f"/api/peso?hasta={AYER.isoformat()}").json()
    assert [p["kg"] for p in datos["pesos"]] == [73.0, 72.6]


@pytest.mark.parametrize("hasta", ["nunca", "2026-13-01"])
def test_peso_hasta_mal_escrito_es_400(tmp_path, hasta):  # R10
    e = _con_pesos(tmp_path)
    r = e.cliente.get(f"/api/peso?hasta={hasta}")
    assert r.status_code == 400
    assert "error" in r.json()


# -- R13: icon, manifest and the service worker shell ---------------------------------------------

def _tamano_png(contenido: bytes) -> tuple[int, int]:
    assert contenido[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", contenido[16:24])


@pytest.mark.parametrize("pagina", ["/", "/ajustes"])
def test_las_dos_paginas_enlazan_icono_y_manifest(tmp_path, pagina):  # R13
    html = Entorno(tmp_path).cliente.get(pagina).text
    assert '<link rel="manifest" href="/static/manifest.json">' in html
    assert re.search(r'<link rel="apple-touch-icon"[^>]*href="/static/icono-180.png"', html)
    assert re.search(r'<link rel="icon"[^>]*href="/static/icono.svg"[^>]*type="image/svg\+xml"', html)
    assert re.search(r'<link rel="icon"[^>]*href="/static/icono-32.png"', html)
    assert re.search(r'<meta name="theme-color"[^>]*content="#[0-9a-fA-F]{6}"', html)


def test_manifest(tmp_path):  # R13
    r = Entorno(tmp_path).cliente.get("/static/manifest.json")
    assert r.status_code == 200
    m = r.json()
    assert m["name"] == "TriData"
    assert m["display"] == "standalone"
    assert m["start_url"] == "/" and m["scope"] == "/"
    tamanos = {i["sizes"]: i["src"] for i in m["icons"]}
    assert tamanos["180x180"] == "/static/icono-180.png"
    assert tamanos["512x512"] == "/static/icono-512.png"


@pytest.mark.parametrize("fichero, lado", [("icono-180.png", 180), ("icono-512.png", 512), ("icono-32.png", 32)])
def test_iconos_png(tmp_path, fichero, lado):  # R13
    r = Entorno(tmp_path).cliente.get(f"/static/{fichero}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert _tamano_png(r.content) == (lado, lado)


def test_icono_svg(tmp_path):  # R13
    r = Entorno(tmp_path).cliente.get("/static/icono.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")


def test_el_cascaron_guarda_lo_nuevo_con_otra_version(tmp_path):  # R13
    cliente = Entorno(tmp_path).cliente
    sw = cliente.get("/sw.js").text
    version = re.search(r'const VERSION = "([^"]+)"', sw)
    assert version and version.group(1) != "tridata-cascaron-v1"
    rutas = set(re.findall(r"\"([^\"]+)\"", re.search(r"const CASCARON = \[(.*?)\];", sw, re.S).group(1)))
    for nueva in ("/static/peso.js", "/static/manifest.json", "/static/icono.svg", "/static/icono-32.png",
                  "/static/icono-180.png", "/static/icono-512.png", "/ajustes", "/static/ajustes.js"):
        assert nueva in rutas
    for ruta in rutas:
        assert cliente.get(ruta).status_code == 200
