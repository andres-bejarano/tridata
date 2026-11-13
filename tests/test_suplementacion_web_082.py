"""Unit 082 (REC-34): the routes of the supplements block and of the hour of each section, with the Starlette
TestClient over a temporary SQLite. No AI, no Garmin. Every value is invented.

    GET    /api/dia                 carries `suplementacion` (doses by hour, total, last, milk) and `horas`
    POST   /api/toma                adds a dose by hand
    POST   /api/toma/N              changes its hour, form, mg or «solo»
    DELETE /api/toma/N              deletes it, only with {"confirmado": true}
    POST   /api/dia/AAAA-MM-DD/hora {"seccion", "hora"}: the hour of a section that day

Plans: `apuntar-comida` R-226, R-227, R-229, C-281, C-282, C-283, C-286.
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

HOY = date(2026, 10, 21)
AHORA = datetime(2026, 10, 21, 16, 40)


class Entorno:
    """A day with 1.200 kcal of an invented rice at dinner."""

    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.registro = RegistroComidas(self.db)
        ajustes = Ajustes(self.db)
        ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
        (arroz,) = self.registro.guardar([Alimento(nombre="arroz inventado", dia=HOY, cantidad=300, unidad="g",
                                                   kcal=1200)], hoy=HOY, texto="arroz", sin_cambios=True,
                                         seccion="cena")
        self.arroz = arroz
        self.cliente = TestClient(crear_app(self.registro, None, ajustes, hoy=lambda: HOY, ahora=lambda: AHORA))

    def dia(self, fecha: date = HOY) -> dict:
        r = self.cliente.get(f"/api/dia?fecha={fecha.isoformat()}")
        assert r.status_code == 200, r.text
        return r.json()

    def toma(self, **datos) -> dict:
        r = self.cliente.post("/api/toma", json={"dia": HOY.isoformat(), **datos})
        assert r.status_code == 200, r.text
        return r.json()

    def filas(self) -> list[tuple]:
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT * FROM alimento_apuntado ORDER BY id").fetchall()


@pytest.fixture
def entorno(tmp_path) -> Entorno:
    return Entorno(tmp_path)


def test_el_dia_lleva_el_bloque_vacio(entorno):  # R2: without doses, folded
    dia = entorno.dia()
    assert dia["suplementacion"] == {"tomas": [], "cafeina_mg": 0, "ultima": None, "leche": None}
    assert dia["horas"]["cena"] == {"hora": "21:30", "aproximada": True}


def test_anadir_tomas_a_mano_y_verlas_por_hora(entorno):  # R2, R3 · C-281
    antes_total = entorno.dia()["total_kcal"]
    entorno.toma(que="cafe", forma="italiana", gramos=20, fraccion=0.5, hora="17:30", mg=90)
    entorno.toma(que="cafe", forma="doble", hora="10:00", mg=130)
    r = entorno.toma(que="b12", hora="09:00")
    assert r["toma"]["que"] == "b12"
    s = r["dia"]["suplementacion"]
    assert [t["hora"] for t in s["tomas"]] == ["09:00", "10:00", "17:30"]
    assert (s["cafeina_mg"], s["ultima"]) == (220, "17:30")
    assert r["dia"]["total_kcal"] == antes_total == 1200  # G-60
    assert len(r["dia"]["alimentos"]) == 1


def test_mg_a_mano_opcionales_y_no_estimados(entorno):  # R3: «y, si quiere, mg»
    sin = entorno.toma(que="cafe", forma="expreso", hora="08:00")["toma"]
    con = entorno.toma(que="cafe", forma="expreso", hora="08:30", mg=70)["toma"]
    assert sin["mg"] is None
    assert con["mg_estimado"] is False


def test_hoy_sin_hora_toma_la_de_ahora(entorno):  # R7 in the app too
    assert entorno.toma(que="b12")["toma"]["hora"] == "16:40"


def test_otro_dia_sin_hora_es_422(entorno):  # R7
    r = entorno.cliente.post("/api/toma", json={"dia": "2026-10-20", "que": "b12"})
    assert r.status_code == 422 and "falta la hora" in r.json()["error"]


def test_cambiar_una_toma(entorno):  # R3 · C-282
    toma = entorno.toma(que="cafe", forma="italiana", hora="17:30", mg=90)["toma"]
    r = entorno.cliente.post(f"/api/toma/{toma['id']}", json={"hora": "19:00"})
    assert r.status_code == 200, r.text
    assert r.json()["toma"]["hora"] == "19:00"
    assert r.json()["dia"]["suplementacion"]["ultima"] == "19:00"
    assert r.json()["dia"]["total_kcal"] == 1200
    r = entorno.cliente.post(f"/api/toma/{toma['id']}", json={"forma": "doble", "mg": 120, "solo": True})
    assert (r.json()["toma"]["forma"], r.json()["toma"]["mg"], r.json()["toma"]["solo"]) == ("doble", 120, True)


def test_borrar_sin_confirmar_no_borra(entorno):  # R3 · C-283
    toma = entorno.toma(que="cafe", forma="doble", hora="10:00", mg=130)["toma"]
    for cuerpo in ({}, {"confirmado": False}, {"confirmado": "si"}):
        r = entorno.cliente.request("DELETE", f"/api/toma/{toma['id']}", json=cuerpo)
        assert r.status_code == 400 and "confirmar" in r.json()["error"]
    assert [t["id"] for t in entorno.dia()["suplementacion"]["tomas"]] == [toma["id"]]


def test_borrar_confirmado(entorno):  # R3
    toma = entorno.toma(que="cafe", forma="doble", hora="10:00", mg=130)["toma"]
    r = entorno.cliente.request("DELETE", f"/api/toma/{toma['id']}", json={"confirmado": True})
    assert r.status_code == 200
    assert r.json()["dia"]["suplementacion"]["tomas"] == []
    r = entorno.cliente.request("DELETE", f"/api/toma/{toma['id']}", json={"confirmado": True})
    assert r.status_code == 404


def test_hora_de_una_seccion(entorno):  # R5 · C-286
    antes = entorno.filas()
    r = entorno.cliente.post(f"/api/dia/{HOY.isoformat()}/hora", json={"seccion": "cena", "hora": "22:15"})
    assert r.status_code == 200, r.text
    assert r.json()["dia"]["horas"]["cena"] == {"hora": "22:15", "aproximada": False}
    assert r.json()["dia"]["total_kcal"] == 1200
    assert entorno.dia()["horas"]["cena"]["hora"] == "22:15"  # it stays on reloading
    assert entorno.filas() == antes
    r = entorno.cliente.post(f"/api/dia/{HOY.isoformat()}/hora", json={"seccion": "cena", "hora": None})
    assert r.json()["dia"]["horas"]["cena"] == {"hora": "21:30", "aproximada": True}


@pytest.mark.parametrize("ruta, cuerpo, motivo", [
    ("/api/toma", {"dia": "2026-10-21", "que": "cafe", "forma": "doble", "hora": "25:00"}, "hora"),
    ("/api/toma", {"dia": "2026-10-21", "que": "cafe", "forma": "moka", "hora": "10:00"}, "forma"),
    ("/api/toma", {"dia": "2026-10-21", "que": "cafe", "forma": "doble", "hora": "10:00", "mg": -3}, "negativ"),
    ("/api/toma", {"dia": "ayer", "que": "b12", "hora": "10:00"}, "día"),
    ("/api/dia/2026-10-21/hora", {"seccion": "cena", "hora": "22:99"}, "hora"),
    ("/api/dia/2026-10-21/hora", {"seccion": "brunch", "hora": "11:00"}, "sección"),
    ("/api/dia/2026-10-21/hora", {"hora": "11:00"}, "sección"),
    ("/api/dia/no-es-fecha/hora", {"seccion": "cena", "hora": "22:00"}, "Fecha"),
])
def test_lo_no_valido_no_se_guarda_y_dice_por_que(entorno, ruta, cuerpo, motivo):  # R8
    r = entorno.cliente.post(ruta, json=cuerpo)
    assert 400 <= r.status_code < 500
    assert motivo in r.json()["error"]
    dia = entorno.dia()
    assert dia["suplementacion"]["tomas"] == []
    assert dia["horas"]["cena"]["aproximada"] is True


def test_cambiar_con_algo_no_valido_no_cambia(entorno):  # R8
    toma = entorno.toma(que="cafe", forma="doble", hora="10:00", mg=130)["toma"]
    r = entorno.cliente.post(f"/api/toma/{toma['id']}", json={"hora": "10:75"})
    assert r.status_code == 422 and "hora" in r.json()["error"]
    assert entorno.dia()["suplementacion"]["tomas"][0]["hora"] == "10:00"
    assert entorno.cliente.post("/api/toma/999", json={"hora": "10:00"}).status_code == 404


def test_la_leche_en_el_dia(entorno):  # R4: the block shows each coffee's share
    a = entorno.toma(que="cafe", forma="doble", hora="10:00", mg=130)["toma"]
    (leche,) = entorno.registro.guardar([Alimento(nombre="leche de prueba", dia=HOY, cantidad=200, unidad="ml",
                                                  kcal=92)], hoy=HOY, texto="leche", sin_cambios=False)
    Suplementacion(entorno.db).marcar_leche(HOY, leche["id"])
    dia = entorno.dia()
    assert dia["total_kcal"] == 1292
    assert dia["suplementacion"]["leche"] == {"alimento_id": leche["id"], "ml": 200, "kcal": 92, "repartida": True}
    assert dia["suplementacion"]["tomas"][0]["id"] == a["id"] and dia["suplementacion"]["tomas"][0]["leche_ml"] == 200


def test_una_base_de_antes_da_el_mismo_dia(tmp_path):  # R8: same foods, sections and totals
    db = tmp_path / "data.sqlite3"
    registro = RegistroComidas(db)
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
    registro.guardar([Alimento(nombre="pan inventado", dia=HOY, cantidad=60, unidad="g", kcal=150)], hoy=HOY,
                     texto="pan", sin_cambios=True, seccion="desayuno")
    registro.guardar([Alimento(nombre="pasta inventada", dia=HOY, cantidad=100, unidad="g", kcal=350)], hoy=HOY,
                     texto="pasta", sin_cambios=True)
    with sqlite3.connect(db) as conn:
        antes = conn.execute("SELECT * FROM alimento_apuntado ORDER BY id").fetchall()
        tablas = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "toma_suplemento" not in tablas  # a store of before the 082
    dia = TestClient(crear_app(registro, None, ajustes, hoy=lambda: HOY)).get("/api/dia").json()
    assert dia["total_kcal"] == 500
    assert [(a["nombre"], a["seccion"]) for a in dia["alimentos"]] == [("pan inventado", "desayuno"),
                                                                       ("pasta inventada", None)]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM alimento_apuntado ORDER BY id").fetchall() == antes
