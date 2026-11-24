"""Unit 109 (REC-52): the routes of gels, salt and saved products, with the Starlette TestClient over a temporary
SQLite. No AI, no Garmin. Every value is invented.

    POST   /api/toma                also {"que": "gel"|"sal", "producto"?, "producto_id"?, "mg"?, "sodio_mg"?,
                                    "cantidad"?, "hora"?, "minuto"?}
    POST   /api/toma/N              changes its product, number, mg, hour or minute
    DELETE /api/toma/N              only with {"confirmado": true}
    GET    /api/producto            the saved products
    POST   /api/producto[/N]        saves / changes one {"nombre", "tipo", "mg_cafeina", "sodio_mg"}
    DELETE /api/producto/N          only with {"confirmado": true}

Plans: `apuntar-comida` R-317..R-321, R-323, C-404..C-409, C-412.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas
from tridata.nutricion.suplementacion import Suplementacion
from tridata.web import crear_app

HOY = date(2026, 11, 24)
DOMINGO = date(2026, 11, 29)
AHORA = datetime(2026, 11, 24, 16, 40)


class Entorno:
    """Today 1.200 kcal of an invented rice at dinner; Sunday, nothing."""

    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.registro = RegistroComidas(self.db)
        ajustes = Ajustes(self.db)
        ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
        self.registro.guardar([Alimento(nombre="arroz inventado", dia=HOY, cantidad=300, unidad="g", kcal=1200)],
                              hoy=HOY, texto="arroz", sin_cambios=True, seccion="cena")
        self.cliente = TestClient(crear_app(self.registro, None, ajustes, hoy=lambda: HOY, ahora=lambda: AHORA))

    def dia(self, fecha: date = HOY) -> dict:
        r = self.cliente.get(f"/api/dia?fecha={fecha.isoformat()}")
        assert r.status_code == 200, r.text
        return r.json()

    def toma(self, fecha: date = HOY, **datos) -> dict:
        r = self.cliente.post("/api/toma", json={"dia": fecha.isoformat(), **datos})
        assert r.status_code == 200, r.text
        return r.json()

    def producto(self, **datos) -> dict:
        r = self.cliente.post("/api/producto", json=datos)
        assert r.status_code == 200, r.text
        return r.json()

    def filas(self) -> list[tuple]:
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT * FROM alimento_apuntado ORDER BY id").fetchall()


@pytest.fixture
def entorno(tmp_path) -> Entorno:
    return Entorno(tmp_path)


def test_geles_por_minuto_en_un_dia_futuro(entorno):  # R1, R2 · C-404
    antes = (entorno.filas(), entorno.dia(DOMINGO)["total_kcal"])
    entorno.toma(DOMINGO, que="gel", producto="Gel cola de prueba", mg=100, minuto=60)
    r = entorno.toma(DOMINGO, que="gel", producto="Gel cola de prueba", mg=100, minuto=90)
    s = r["dia"]["suplementacion"]
    assert [(t["minuto"], t["texto"]) for t in s["en_carrera"]] == [(60, "Gel cola de prueba"),
                                                                   (90, "Gel cola de prueba")]
    assert (s["cafeina_mg"], s["cafeina_cafe_mg"], s["cafeina_geles_mg"]) == (200, 0, 100 + 100)
    assert (entorno.filas(), r["dia"]["total_kcal"]) == antes  # G-68: no kcal, no food


def test_el_dia_con_cafe_gel_y_sal(entorno):  # R2, R4 · C-405, C-408
    entorno.toma(que="cafe", forma="doble", hora="10:00", mg=130)
    entorno.toma(que="gel", mg=100, hora="11:15")
    s = entorno.toma(que="sal", sodio_mg=215, minuto=75)["dia"]["suplementacion"]
    assert (s["cafeina_mg"], s["cafeina_cafe_mg"], s["cafeina_geles_mg"], s["ultima"], s["sodio_mg"]) == (
        230, 130, 100, "11:15", 215)
    assert [t["texto"] for t in s["en_carrera"]] == ["pastilla de sal"]
    assert entorno.dia()["total_kcal"] == 1200


def test_productos_guardar_usar_cambiar_y_borrar(entorno):  # R3 · C-406, C-407
    r = entorno.producto(nombre="Gel 226 cola", tipo="gel", mg_cafeina=100, sodio_mg=50)
    gel = r["producto"]
    assert r["productos"] == [gel]
    assert entorno.cliente.get("/api/producto").json() == {"productos": [gel]}
    assert entorno.dia()["productos"] == [gel]  # the day carries them for «Añadir»
    toma = entorno.toma(producto_id=gel["id"], cantidad=2, hora="09:30")["toma"]
    assert (toma["que"], toma["producto"], toma["mg"], toma["sodio_mg"]) == ("gel", "Gel 226 cola", 200, 100)
    cambiado = entorno.cliente.post(f"/api/producto/{gel['id']}", json={"mg_cafeina": 75})
    assert cambiado.status_code == 200 and cambiado.json()["producto"]["mg_cafeina"] == 75
    assert entorno.dia()["suplementacion"]["tomas"][0]["mg"] == 200  # G-70: the dose logged stays
    sin = entorno.cliente.request("DELETE", f"/api/producto/{gel['id']}", json={})
    assert sin.status_code == 400 and "confirmar" in sin.json()["error"]
    assert len(entorno.cliente.get("/api/producto").json()["productos"]) == 1
    borrado = entorno.cliente.request("DELETE", f"/api/producto/{gel['id']}", json={"confirmado": True})
    assert borrado.status_code == 200 and borrado.json()["productos"] == []
    assert entorno.dia()["suplementacion"]["tomas"][0]["mg"] == 200
    assert entorno.cliente.request("DELETE", f"/api/producto/{gel['id']}",
                                   json={"confirmado": True}).status_code == 404


@pytest.mark.parametrize("cuerpo, motivo", [
    ({"nombre": "gel 226 COLA", "tipo": "gel", "mg_cafeina": 90}, "ya"),
    ({"nombre": "Otro", "tipo": "gel", "mg_cafeina": "nan"}, "finito"),
    ({"nombre": "Otro", "tipo": "zumo", "mg_cafeina": 10}, "gel o sal"),
])
def test_producto_no_valido_es_422(entorno, cuerpo, motivo):  # R3, R7
    entorno.producto(nombre="Gel 226 cola", tipo="gel", mg_cafeina=100)
    r = entorno.cliente.post("/api/producto", json=cuerpo)
    assert r.status_code == 422 and motivo in r.json()["error"]
    assert len(entorno.cliente.get("/api/producto").json()["productos"]) == 1


def test_cambiar_un_gel_y_borrarlo_con_confirmacion(entorno):  # R5 · C-409
    gel = entorno.toma(DOMINGO, que="gel", mg=100, minuto=60)["toma"]
    r = entorno.cliente.post(f"/api/toma/{gel['id']}", json={"cantidad": 2, "minuto": 45})
    assert r.status_code == 200, r.text
    assert (r.json()["toma"]["cantidad"], r.json()["toma"]["mg"], r.json()["toma"]["minuto"]) == (2, 200, 45)
    sin = entorno.cliente.request("DELETE", f"/api/toma/{gel['id']}", json={})
    assert sin.status_code == 400
    assert len(entorno.dia(DOMINGO)["suplementacion"]["en_carrera"]) == 1  # C-409: still there
    ok = entorno.cliente.request("DELETE", f"/api/toma/{gel['id']}", json={"confirmado": True})
    assert ok.status_code == 200 and "en_carrera" not in ok.json()["dia"]["suplementacion"]


@pytest.mark.parametrize("cuerpo, motivo", [
    ({"que": "gel", "mg": 100}, "falta la hora o el minuto"),
    ({"que": "sal", "sodio_mg": 215}, "falta la hora o el minuto"),
    ({"que": "gel", "mg": "nan", "minuto": 60}, "finito"),
    ({"que": "gel", "mg": 100, "minuto": 60, "cantidad": 0}, "mayor que 0"),
    ({"que": "cafe", "forma": "doble", "hora": "10:00", "mg": "1e400"}, "finito"),
])
def test_tomas_no_validas_son_422(entorno, cuerpo, motivo):  # R7 · C-411, C-412
    r = entorno.cliente.post("/api/toma", json={"dia": HOY.isoformat(), **cuerpo})
    assert r.status_code == 422 and motivo in r.json()["error"]
    assert Suplementacion(entorno.db).tomas_del_dia(HOY) == []


def test_cambio_no_valido_de_un_gel_no_cambia(entorno):  # R7
    gel = entorno.toma(que="gel", mg=100, minuto=60)["toma"]
    for malo in ({"minuto": None}, {"cantidad": "inf"}, {"mg": -3}):
        r = entorno.cliente.post(f"/api/toma/{gel['id']}", json=malo)
        assert r.status_code == 422, malo
    assert Suplementacion(entorno.db).toma(gel["id"]) == gel


@pytest.mark.parametrize("cuerpo", [
    {"que": "gel", "mg": 1e308, "cantidad": 10, "minuto": 60},
    {"que": "gel", "mg": 100, "cantidad": 1e308, "minuto": 60},
])
def test_numeros_enormes_son_422_no_500(entorno, cuerpo):  # R7 (review of the 109)
    r = entorno.cliente.post("/api/toma", json={"dia": HOY.isoformat(), **cuerpo})
    assert r.status_code == 422 and "como mucho" in r.json()["error"]
    assert Suplementacion(entorno.db).tomas_del_dia(HOY) == []
