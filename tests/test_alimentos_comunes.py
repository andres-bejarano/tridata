"""Unit 026 · common foods: the app brings ~90 everyday foods with their kcal/100 g (the egg, per unit).

They are loaded as ingredients (`alimento_fijo`) marked `comun`, once and never over what is Andrés's: a name
or alias of his wins. Correcting one makes it his; removing one keeps it out. Recetario over a temporary SQLite
and the Starlette TestClient; the Claude interpreter is a unittest.mock double (no API). Every own food is
invented; the common values are the ones of §Qué of the unit.

Plans: `guardar-receta` (the common list), `apuntar-comida` (the search box). R1-R7 of the unit (R3 and R6 on
screen: tests/e2e/test_alimentos_comunes_e2e.py).
"""
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas
from tridata.nutricion.comunes import COMUNES, cargar_comunes
from tridata.nutricion.recetas import Recetario, normalizar
from tridata.web import crear_app

HOY = date(2026, 10, 21)


def _por_nombre(rec: Recetario) -> dict[str, dict]:
    return {normalizar(f["nombre"]): f for f in rec.fijos()}


def _comunes(rec: Recetario) -> list[dict]:
    return [f for f in rec.fijos() if f["comun"]]


# -- the list itself ------------------------------------------------------------------------------------------

def test_la_lista_es_la_del_contrato():
    nombres = [normalizar(c["nombre"]) for c in COMUNES]
    assert len(nombres) == len(set(nombres)), "a common name is repeated"
    assert 80 <= len(COMUNES) <= 95
    valores = {normalizar(c["nombre"]): (c["kcal"], c["por"]) for c in COMUNES}
    assert valores["pera"] == (49, "100g")
    assert valores["tomate"] == (20, "100g")
    assert valores["pan de barra"] == (260, "100g")
    assert valores["huevo"] == (75, "unidad")
    assert valores["aceite de oliva"] == (899, "100g")
    assert all(c["por"] == "100g" for c in COMUNES if normalizar(c["nombre"]) != "huevo")
    assert all(0 < c["kcal"] <= 900 for c in COMUNES)


# -- R1: loaded once, never duplicated ------------------------------------------------------------------------

def test_carga_una_vez_sin_duplicar(tmp_path):  # R1
    rec = Recetario(tmp_path / "data.sqlite3")
    assert rec.fijos() == []
    cargar_comunes(rec)
    cargados = _comunes(rec)
    assert len(cargados) == len(COMUNES) == len(rec.fijos())
    cargar_comunes(rec)
    cargar_comunes(Recetario(tmp_path / "data.sqlite3"))  # another start over the same file
    assert len(rec.fijos()) == len(COMUNES)
    assert _por_nombre(rec)["pera"]["kcal"] == 49 and _por_nombre(rec)["pera"]["comun"] is True


def test_al_arrancar_la_app_estan_los_comunes(tmp_path):  # R1: «al arrancar la app»
    db = tmp_path / "data.sqlite3"
    ajustes = Ajustes(db)
    for _ in range(2):
        cliente = TestClient(crear_app(RegistroComidas(db), MagicMock(), ajustes, hoy=lambda: HOY))
        fijos = cliente.get("/api/recetas").json()["fijos"]
        assert len(fijos) == len(COMUNES)
        assert all(f["comun"] is True for f in fijos)


def test_una_base_antigua_gana_la_columna(tmp_path):  # R1: the base of before unit 026 (no `comun` column)
    db = tmp_path / "data.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE alimento_fijo (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, "
                     "alias TEXT NOT NULL DEFAULT '[]', kcal REAL NOT NULL, por TEXT NOT NULL "
                     "CHECK (por IN ('100g', 'unidad')), nota TEXT)")
        conn.execute("INSERT INTO alimento_fijo (nombre, alias, kcal, por) VALUES ('avena de prueba', '[]', 370, '100g')")
    rec = Recetario(db)
    assert rec.fijos()[0]["comun"] is False
    cargar_comunes(rec)
    assert _por_nombre(rec)["avena de prueba"]["comun"] is False
    assert len(rec.fijos()) == len(COMUNES) + 1


# -- R2, R7: what is Andrés's wins --------------------------------------------------------------------------------

def test_un_nombre_o_alias_tuyo_manda(tmp_path):  # R2
    rec = Recetario(tmp_path / "data.sqlite3")
    pera = rec.crear_fijo({"nombre": "Pera", "kcal": 55, "por": "100g"})
    bote = rec.crear_fijo({"nombre": "Lentejas cocidas bote", "kcal": 90, "por": "100g",
                           "alias": ["bote de lentejas", "lentejas cocidas"]})
    rec.crear_receta({"nombre": "Tomate frito de la abuela", "alias": ["TOMATE"], "ingredientes": [
        {"nombre": "algo de prueba", "cantidad": 100, "unidad": "g", "kcal": 50}]}, HOY)
    rec.crear_olla({"nombre": "Sandía", "tara_g": 1200})  # an odd pot name, but his
    antes = rec.listado()
    cargar_comunes(rec)
    nombres = _por_nombre(rec)
    assert nombres["pera"] == {**pera, "comun": False}
    assert nombres["lentejas cocidas bote"] == {**bote, "comun": False}
    for no_esta in ("lentejas cocidas", "tomate", "sandia"):
        assert no_esta not in nombres
    assert sum(normalizar(f["nombre"]) == "pera" for f in rec.fijos()) == 1
    assert len(_comunes(rec)) == len(COMUNES) - 4  # pera, lentejas cocidas, tomate and sandía (his pot)
    despues = rec.listado()
    assert despues["recetas"] == antes["recetas"] and despues["ollas"] == antes["ollas"]


def test_guardar_uno_tuyo_con_el_nombre_de_un_comun(tmp_path):  # R7
    rec = Recetario(tmp_path / "data.sqlite3")
    cargar_comunes(rec)
    mia = rec.crear_fijo({"nombre": "pera", "kcal": 55, "por": "100g"})
    assert mia["comun"] is False and mia["kcal"] == 55
    peras = [f for f in rec.fijos() if normalizar(f["nombre"]) == "pera"]
    assert peras == [mia]
    # by alias as well, and with a recipe: the common one goes, his stays
    rec.crear_fijo({"nombre": "Pan de mi panadería", "kcal": 250, "por": "100g", "alias": ["pan de barra"]})
    assert "pan de barra" not in _por_nombre(rec)
    rec.crear_receta({"nombre": "Tomate", "ingredientes": [
        {"nombre": "algo de prueba", "cantidad": 100, "unidad": "g", "kcal": 50}]}, HOY)
    assert "tomate" not in _por_nombre(rec)
    cargar_comunes(rec)  # and they do not come back at the next start
    assert [f for f in rec.fijos() if normalizar(f["nombre"]) == "pera"] == [mia]
    assert "pan de barra" not in _por_nombre(rec) and "tomate" not in _por_nombre(rec)


def test_lo_tuyo_sigue_chocando_con_lo_tuyo(tmp_path):  # R7 is only about common ones: G-34 stays
    rec = Recetario(tmp_path / "data.sqlite3")
    cargar_comunes(rec)
    rec.crear_fijo({"nombre": "avena de prueba", "kcal": 370, "por": "100g"})
    with pytest.raises(ValueError, match="ya es tu ingrediente"):
        rec.crear_fijo({"nombre": "Avena de prueba", "kcal": 380, "por": "100g"})


# -- R4, R5 with the TestClient ------------------------------------------------------------------------------

class Entorno:
    def __init__(self, db: Path):
        self.db = db
        self.ajustes = Ajustes(db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "objetivo_pct": -10}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(db)
        rec = Recetario(db)
        self.manzana = rec.crear_fijo({"nombre": "Manzana", "kcal": 52, "por": "100g"})  # invented, his
        self.registro.anadir(Alimento(nombre="comida de antes", dia=HOY, cantidad=500, unidad="g", kcal=1000), HOY)
        self.cliente = self.arrancar()

    def arrancar(self) -> TestClient:
        return TestClient(crear_app(self.registro, MagicMock(), self.ajustes, hoy=lambda: HOY))

    def ok(self, ruta, cuerpo):
        r = self.cliente.post(ruta, json=cuerpo)
        assert r.status_code == 200, r.text
        return r.json()

    def fijos(self) -> list[dict]:
        return self.cliente.get("/api/recetas").json()["fijos"]

    def fijo(self, nombre: str) -> dict | None:
        return next((f for f in self.fijos() if normalizar(f["nombre"]) == normalizar(nombre)), None)


@pytest.fixture
def e(tmp_path):
    return Entorno(tmp_path / "data.sqlite3")


def test_la_lista_lleva_la_marca(e):  # R1, R2: GET /api/recetas says `comun` on every ingredient
    assert e.fijo("Manzana")["comun"] is False
    assert e.fijo("Pera")["comun"] is True
    assert len(e.fijos()) == len(COMUNES) + 1


@pytest.mark.parametrize("cambio", [{"kcal": 250}, {"nombre": "Pan de barra del súper"},
                                    {"alias": ["barra"]}])
def test_corregir_un_comun_lo_hace_tuyo(e, cambio):  # R4
    pan = e.fijo("Pan de barra")
    datos = {"nombre": pan["nombre"], "kcal": pan["kcal"], "por": pan["por"], "alias": pan["alias"], **cambio}
    cambiado = e.ok(f"/api/fijos/{pan['id']}", datos)["fijo"]
    assert cambiado["comun"] is False
    assert {k: cambiado[k] for k in cambio} == cambio
    e.cliente = e.arrancar()  # another start: it is not loaded again beside his
    assert len(e.fijos()) == len(COMUNES) + 1
    assert [f for f in e.fijos() if f["id"] == pan["id"]][0]["comun"] is False


def test_quitar_un_comun_no_vuelve(e):  # R5
    pera = e.fijo("Pera")
    apuntado = e.ok(f"/api/fijos/{pera['id']}/apuntar", {"cantidad": 200})["alimento"]
    assert apuntado["kcal"] == 98
    e.ok(f"/api/fijos/{pera['id']}/borrar", {"confirmado": True})
    assert e.fijo("Pera") is None
    e.cliente = e.arrancar()
    assert e.fijo("Pera") is None
    cargar_comunes(Recetario(e.db))
    assert e.fijo("Pera") is None
    hoy = [a for a in e.registro.apuntes_del_dia(HOY) if a["id"] == apuntado["id"]]
    assert [(a["nombre"], a["kcal"], a["origen"]) for a in hoy] == [("Pera", 98, "de la lista de comunes")]


def test_quitar_uno_tuyo_no_lo_cambia_un_comun(e):  # R5 does not touch his own
    e.ok(f"/api/fijos/{e.manzana['id']}/borrar", {"confirmado": True})
    e.cliente = e.arrancar()
    assert e.fijo("Manzana") is None  # «manzana» is not a common one: nothing comes in its place


# -- R3: Pera 180 g = 88 kcal, worked out on the server (the bearing criterion) -------------------------------

def test_pera_180_g_apunta_88_kcal(e):  # R3 — criterio portante
    pera = e.fijo("Pera")
    a = e.ok("/api/alimento", {"nombre": "pera", "dia": HOY.isoformat(), "cantidad": 180, "unidad": "g",
                               "kcal": 999, "guardado": {"tipo": "fijo", "id": pera["id"]}})["alimento"]
    assert (a["nombre"], a["cantidad"], a["unidad"], a["estimado"]) == ("Pera", 180, "g", False)
    assert a["kcal"] == pytest.approx(88.2)  # 49 × 180 / 100; the screen shows it whole: 88
    assert round(a["kcal"]) == 88
    assert a["origen"] == "de la lista de comunes"
    assert e.cliente.get(f"/api/dia?fecha={HOY.isoformat()}").json()["total_kcal"] == pytest.approx(1088.2, abs=0.5)


def test_el_huevo_va_por_unidad(e):  # R3 with the only one per unit
    huevo = e.fijo("Huevo")
    a = e.ok(f"/api/fijos/{huevo['id']}/apuntar", {"cantidad": 2})["alimento"]
    assert (a["kcal"], a["unidad"], a["origen"]) == (150, "unidad", "de la lista de comunes")


def test_lo_tuyo_sigue_diciendo_tu_ingrediente(e):  # NO debe haber cambiado
    a = e.ok(f"/api/fijos/{e.manzana['id']}/apuntar", {"cantidad": 100})["alimento"]
    assert (a["kcal"], a["origen"]) == (52, "de tu ingrediente «Manzana»")
