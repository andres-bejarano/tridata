"""The assistant in the kitchen (unit 023): create, correct, weigh and delete recipes, and delete ingredients, saying it.

Unit tests propose each action over a seeded temporary SQLite (the `CambioInterpretado`s are written by hand, what a
double of Claude would return); integration tests confirm through the routes of unit 021 with Starlette's
TestClient (Claude is a `MagicMock`). Every figure is computed on the PC with what is saved, with the checks and
texts of the Recetas tab. Plans `pedir-al-asistente` (REC-14): R1-R6 of the unit · R-82..R-85, G-32..G-35, G-37,
G-40, G-43. Every value is invented; Claude is never called.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.ia import CambioInterpretado, LineaInterpretada, ListaInterpretada
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.nutricion.asistente import Contexto, Peticion, catalogo, confirmar, proponer, rehacer, texto_acciones
from tridata.nutricion.recetas import Recetario
from tridata.web import crear_app

HOY = date(2026, 10, 22)  # a Thursday
MARTES = HOY - timedelta(days=2)
ACCIONES_023 = ("nueva_receta", "corregir_receta", "pesar_receta", "borrar_receta", "borrar_ingrediente")


def _l(nombre, cantidad, unidad="g", kcal_estimadas=None):
    return LineaInterpretada(nombre=nombre, cantidad=cantidad, unidad=unidad, kcal_estimadas=kcal_estimadas)


class Base:
    """The data of the contract (§Criterios de aceptación)."""

    def __init__(self, tmp_path):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1980, "kcal_por_paso": 0.04, "pasos_por_defecto": 8000},
                             desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.pesos = RegistroPeso(self.db)
        self.recetario = Recetario(self.db)
        for nombre, kcal in (("calabaza de prueba", 25), ("aceite de prueba", 900), ("cebolla de prueba", 40),
                             ("queso mozzarella de prueba", 280)):
            self.recetario.crear_fijo({"nombre": nombre, "kcal": kcal, "por": "100g"})
        self.olla = self.recetario.crear_olla({"nombre": "olla grande", "tara_g": 1500})
        self.garbanzos = self.recetario.crear_receta({"nombre": "garbanzos de prueba", "ingredientes": [
            {"nombre": "garbanzo seco de prueba", "cantidad": 600, "unidad": "g", "kcal": 2100},
            {"nombre": "aceite de prueba", "cantidad": 50, "unidad": "g"},
            {"nombre": "cebolla de prueba", "cantidad": 125, "unidad": "g"}], "peso_neto_g": 2000}, hoy=HOY)
        self.lentejas = self.recetario.crear_receta({"nombre": "lentejas de prueba", "ingredientes": [
            {"nombre": "lenteja seca de prueba", "cantidad": 700, "unidad": "g", "kcal": 2150},
            {"nombre": "aceite de prueba", "cantidad": 50, "unidad": "g"}]}, hoy=HOY)
        fila = self.registro.guardar([Alimento(nombre="garbanzos de prueba", dia=MARTES, cantidad=250, unidad="g",
                                               kcal=325)], hoy=HOY, texto="garbanzos de prueba", sin_cambios=True)[0]
        self.apuntado = fila["id"]
        self.ctx = Contexto(registro=self.registro, objetivos=self.objetivos, ajustes=self.ajustes, pesos=self.pesos,
                            recetario=self.recetario, hoy=lambda: HOY)

    def receta(self, nombre):
        return next(r for r in self.recetario.recetas() if r["nombre"] == nombre)

    def kcal_del_martes(self):
        return sum(a["kcal"] for a in self.registro.apuntes_del_dia(MARTES))

    def filas(self, tabla):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]  # noqa: S608 - fixed names


@pytest.fixture
def b(tmp_path):
    return Base(tmp_path)


def _uno(b, frase="texto de prueba", **campos):
    (resultado,) = proponer([CambioInterpretado(**campos)], b.ctx, Peticion(texto=frase, fecha=HOY))
    return resultado


CREMA = dict(accion="nueva_receta", objeto="crema de calabaza de prueba",
             lineas=[_l("calabaza", 800), _l("aceite", 20), _l("cebolla", 150)])


# -- the catalogue ---------------------------------------------------------------------------------------------------

def test_las_acciones_de_la_cocina_estan_en_el_catalogo():
    ids = set(catalogo())
    assert set(ACCIONES_023) <= ids and "guardar_ingrediente" in ids
    texto = texto_acciones()
    for i in ACCIONES_023:
        assert f"- {i}:" in texto
    assert re.search(r"\d", texto) is None  # still no figure at all for Claude (G-38)


# -- R1 · R-82, C-108: a new recipe -----------------------------------------------------------------------------------

def test_receta_nueva_con_sus_lineas_y_sin_pesar(b):  # R1
    antes = b.filas("receta")
    t = _uno(b, "receta nueva crema de calabaza de prueba: 800 gramos de calabaza, 20 de aceite y 150 de cebolla",
             **CREMA)
    assert t["tipo"] == "propuesta" and t["accion"] == "nueva_receta" and t["error"] is None
    assert t["lineas"] == ["calabaza de prueba 800 g · 200 kcal", "aceite de prueba 20 g · 180 kcal",
                           "cebolla de prueba 150 g · 60 kcal"]
    assert "440 kcal la receta entera, sin pesar" in t["texto"]
    assert "crema de calabaza de prueba" in t["texto"]
    assert [c["tipo"] for c in t["campos"]] == ["numero"] * 3
    assert [c["valor"] for c in t["campos"]] == [800, 20, 150]
    assert b.filas("receta") == antes and b.filas("tarjeta_asistente") == 0  # proposing writes nothing


def test_receta_nueva_con_algo_que_no_entiende_no_propone(b):  # R1 · G-40
    r = _uno(b, accion="nueva_receta", objeto="crema de calabaza de prueba",
             lineas=[_l("calabaza", 800), _l("kfjd", 50)])
    assert r == {"tipo": "no", "texto": "No sé qué es «kfjd»"}


def test_receta_nueva_con_algo_no_guardado_lo_marca_estimado(b):  # R1 · R-82
    t = _uno(b, accion="nueva_receta", objeto="crema de calabaza de prueba",
             lineas=[_l("calabaza", 800), _l("nata de prueba", 100, kcal_estimadas=300)])
    assert t["lineas"] == ["calabaza de prueba 800 g · 200 kcal", "nata de prueba 100 g · 300 kcal (estimado)"]
    assert "500 kcal la receta entera, sin pesar" in t["texto"]


def test_corregir_los_gramos_en_la_tarjeta(b):  # R1 · R-71
    t = _uno(b, **CREMA)
    clave = t["campos"][0]["clave"]
    t2 = rehacer(t, b.ctx, valores={clave: 1000})
    assert t2["lineas"][0] == "calabaza de prueba 1.000 g · 250 kcal"
    assert "490 kcal la receta entera, sin pesar" in t2["texto"] and t2["corregida"] is True
    t3 = rehacer(t, b.ctx, valores={clave: 0})
    assert t3["tipo"] == "propuesta" and "mayor que 0" in t3["error"]


def test_receta_nueva_confirmada_queda_como_a_mano(b):  # R1
    t = _uno(b, **CREMA)
    hecho = confirmar(t, b.ctx)
    assert "crema de calabaza de prueba" in hecho["hecho"]
    crema = b.receta("crema de calabaza de prueba")
    v = crema["en_uso"]
    assert v["kcal_total"] == 440 and v["kcal_100g"] is None and v["estado"] == "provisional"
    assert [(i["nombre"], i["cantidad"], i["kcal"], i["estado"]) for i in v["ingredientes"]] == [
        ("calabaza de prueba", 800, 200, "guardado"), ("aceite de prueba", 20, 180, "guardado"),
        ("cebolla de prueba", 150, 60, "guardado")]
    assert b.filas("tarjeta_asistente") == 1


def test_receta_nueva_con_el_nombre_de_un_ingrediente_no(b):  # R1 · G-34
    r = _uno(b, accion="nueva_receta", objeto="Aceite de prueba", lineas=[_l("calabaza", 800)])
    assert r == {"tipo": "no", "texto": "«aceite de prueba» ya es tu ingrediente «aceite de prueba»"}


def test_receta_nueva_sin_ingredientes_no(b):  # R1
    r = _uno(b, accion="nueva_receta", objeto="crema de calabaza de prueba", lineas=[])
    assert r == {"tipo": "no", "texto": "añade al menos un ingrediente"}


# -- R2 · R-82, C-109: correct the batch in use, or another batch -----------------------------------------------------

def test_corregir_un_ingrediente_de_una_receta_pesada(b):  # R2
    t = _uno(b, "a los garbanzos de prueba les puse 40 de aceite, no 50", accion="corregir_receta",
             objeto="garbanzos de prueba", lineas=[_l("aceite", 40)], antes_dicho="50")
    assert t["tipo"] == "propuesta" and t["accion"] == "corregir_receta" and t["error"] is None
    assert "50 → 40 g, receta entera 2.600 → 2.510 kcal, 130 → 125,5 kcal/100 g" in t["texto"]
    assert "aceite de prueba 40 g · 360 kcal" in t["lineas"]
    assert b.receta("garbanzos de prueba")["en_uso"]["kcal_100g"] == 130  # nothing written


def test_corregir_confirmado_cambia_la_tanda_en_uso(b):  # R2
    t = _uno(b, accion="corregir_receta", objeto="garbanzos de prueba", lineas=[_l("aceite", 40)])
    confirmar(t, b.ctx)
    g = b.receta("garbanzos de prueba")
    assert len(g["versiones"]) == 1
    assert (g["en_uso"]["kcal_total"], g["en_uso"]["kcal_100g"], g["en_uso"]["peso_neto_g"]) == (2510, 125.5, 2000)


def test_otra_tanda_conserva_la_anterior(b):  # R2
    t = _uno(b, "otra tanda de garbanzos de prueba con 40 de aceite", accion="nueva_receta",
             objeto="garbanzos de prueba", campo="otra_tanda", lineas=[_l("aceite", 40)])
    assert t["tipo"] == "propuesta" and "otra tanda" in t["texto"] and t["error"] is None
    assert "2.510 kcal la receta entera, sin pesar" in t["texto"]
    confirmar(t, b.ctx)
    g = b.receta("garbanzos de prueba")
    assert [v["numero"] for v in g["versiones"]] == [1, 2] and g["version_en_uso"] == 2
    assert g["versiones"][0]["kcal_total"] == 2600 and g["versiones"][0]["kcal_100g"] == 130
    assert g["en_uso"]["kcal_total"] == 2510 and g["en_uso"]["kcal_100g"] is None


def test_receta_nueva_con_el_nombre_de_una_receta_la_propone_como_otra_tanda(b):  # R2 · R-82
    t = _uno(b, accion="nueva_receta", objeto="Lentejas de prueba", lineas=[_l("calabaza", 800)])
    assert t["tipo"] == "propuesta" and "otra tanda" in t["texto"]
    t = _uno(b, accion="nueva_receta", objeto="garbanzos de prueba", campo="corregir", lineas=[_l("aceite", 40)])
    assert t["accion"] == "corregir_receta" and "2.600 → 2.510 kcal" in t["texto"]


def test_corregir_una_receta_que_no_esta(b):  # R2
    r = _uno(b, accion="corregir_receta", objeto="paella de prueba", lineas=[_l("aceite", 40)])
    assert r["tipo"] == "no" and "paella de prueba" in r["texto"]


# -- R3 · R-83, C-110: weigh with its pot ---------------------------------------------------------------------------

def test_pesar_con_la_tara_de_la_base(b):  # R3
    t = _uno(b, "las lentejas de prueba pesan 3.500 en la olla grande", accion="pesar_receta",
             objeto="lentejas de prueba", valor=3500, texto="olla grande")
    assert t["tipo"] == "propuesta" and t["accion"] == "pesar_receta" and t["error"] is None
    assert t["texto"] == ("Voy a pesar lentejas de prueba: 3.500 − 1.500 (olla grande) = 2.000 g → 130 kcal/100 g, "
                          "definitiva")
    assert [c["tipo"] for c in t["campos"]] == ["numero"]
    assert b.receta("lentejas de prueba")["en_uso"]["kcal_100g"] is None


def test_pesar_confirmado_queda_definitiva_en_la_misma_tanda(b):  # R3
    t = _uno(b, accion="pesar_receta", objeto="lentejas de prueba", valor=3500, texto="olla grande")
    confirmar(t, b.ctx)
    le = b.receta("lentejas de prueba")
    assert len(le["versiones"]) == 1
    v = le["en_uso"]
    assert (v["olla"], v["tara_g"], v["peso_lleno_g"], v["peso_neto_g"], v["kcal_100g"], v["estado"]) == (
        "olla grande", 1500, 3500, 2000, 130, "definitiva")


def test_pesar_menos_que_la_tara_no(b):  # R3 · G-35
    r = _uno(b, accion="pesar_receta", objeto="lentejas de prueba", valor=1200, texto="olla grande")
    assert r == {"tipo": "no", "texto": "la olla llena no puede pesar menos que su tara"}


def test_pesar_con_el_peso_neto(b):  # R3
    t = _uno(b, accion="pesar_receta", objeto="lentejas de prueba", valor=2000, campo="neto")
    assert "2.000 g" in t["texto"] and "130 kcal/100 g, definitiva" in t["texto"]


# -- R4 · R-84, C-111: delete, the card is the confirmation -------------------------------------------------------------

def test_borrar_un_ingrediente(b):  # R4
    t = _uno(b, "borra el queso mozzarella de prueba", accion="borrar_ingrediente",
             objeto="queso mozzarella de prueba")
    assert t["texto"] == ("Voy a borrar queso mozzarella de prueba de Ingredientes; lo ya apuntado con él no "
                          "cambia")
    assert any(f["nombre"] == "queso mozzarella de prueba" for f in b.recetario.fijos())
    confirmar(t, b.ctx)
    assert not any(f["nombre"] == "queso mozzarella de prueba" for f in b.recetario.fijos())


def test_borrar_una_receta(b):  # R4
    confirmar(_uno(b, **CREMA), b.ctx)
    t = _uno(b, "borra la receta crema de calabaza de prueba", accion="borrar_receta",
             objeto="crema de calabaza de prueba")
    assert t["texto"].startswith("Voy a borrar crema de calabaza de prueba de Recetas")
    assert "lo ya apuntado" in t["texto"] and "no cambia" in t["texto"]
    confirmar(t, b.ctx)
    assert not any(r["nombre"] == "crema de calabaza de prueba" for r in b.recetario.recetas())


def test_borrar_lo_que_no_esta(b):  # R4
    r = _uno(b, accion="borrar_ingrediente", objeto="mantequilla de prueba")
    assert r["tipo"] == "no" and "mantequilla de prueba" in r["texto"]


# -- R5 · R-85, C-112: what was logged keeps its kcal (the bearing criterion) ---------------------------------------------

def test_lo_apuntado_no_cambia_al_corregir_ni_al_borrar(b):  # R5
    assert b.kcal_del_martes() == 325
    confirmar(_uno(b, accion="corregir_receta", objeto="garbanzos de prueba", lineas=[_l("aceite", 40)]), b.ctx)
    assert b.receta("garbanzos de prueba")["en_uso"]["kcal_100g"] == 125.5
    assert b.kcal_del_martes() == 325
    confirmar(_uno(b, accion="borrar_receta", objeto="garbanzos de prueba"), b.ctx)
    assert b.kcal_del_martes() == 325
    confirmar(_uno(b, accion="borrar_ingrediente", objeto="aceite de prueba"), b.ctx)
    assert b.kcal_del_martes() == 325


# -- R6 · G-43: pots and tares are changed in their tab ------------------------------------------------------------------

def test_cambiar_una_tara_no(b):  # R6
    for accion in ("ollas", "pesar_receta"):
        r = _uno(b, "la olla grande pesa 1.400", accion=accion, objeto="olla grande", valor=1400)
        assert r["tipo"] == "no" and "Recetas → Taras" in r["texto"]
    assert b.recetario.ollas()[0]["tara_g"] == 1500


# -- integration: through the routes of unit 021 ---------------------------------------------------------------------------

class Web:
    def __init__(self, tmp_path):
        self.base = Base(tmp_path)
        self.interprete = MagicMock()
        self.cliente = TestClient(crear_app(self.base.registro, self.interprete, self.base.ajustes, hoy=lambda: HOY,
                                            objetivos=self.base.objetivos, pesos=self.base.pesos,
                                            recetario=self.base.recetario))

    def tarjeta(self, frase, **campos):
        self.interprete.interpretar.side_effect = lambda t, hoy: ListaInterpretada(
            alimentos=[], cambios=[CambioInterpretado(**campos)])
        r = self.cliente.post("/api/interpretar", json={"texto": frase})
        assert r.status_code == 200, r.text
        (t,) = r.json()["cambios"]
        return t

    def confirmar(self, t):
        r = self.cliente.post("/api/asistente/confirmar", json={"tarjeta": t})
        assert r.status_code == 200, r.text
        return r.json()

    def total(self, dia):
        r = self.cliente.get(f"/api/dia?fecha={dia.isoformat()}")
        assert r.status_code == 200, r.text
        return r.json()["total_kcal"]


@pytest.fixture
def w(tmp_path):
    return Web(tmp_path)


def test_web_receta_nueva_y_pesar_como_a_mano(w):  # R1, R3
    t = w.tarjeta("receta nueva crema de calabaza de prueba…", **CREMA)
    assert t["tipo"] == "propuesta" and "440 kcal la receta entera, sin pesar" in t["texto"]
    assert w.confirmar(t)["hecho"].startswith("Hecho:")
    recetas = w.cliente.get("/api/recetas").json()["recetas"]
    crema = next(r for r in recetas if r["nombre"] == "crema de calabaza de prueba")
    assert crema["en_uso"]["kcal_total"] == 440
    t = w.tarjeta("las lentejas de prueba pesan 3.500 en la olla grande", accion="pesar_receta",
                  objeto="lentejas de prueba", valor=3500, texto="olla grande")
    w.confirmar(t)
    lentejas = next(r for r in w.cliente.get("/api/recetas").json()["recetas"] if r["nombre"] == "lentejas de prueba")
    assert lentejas["en_uso"]["estado"] == "definitiva" and lentejas["en_uso"]["kcal_100g"] == 130


def test_web_borrar_sin_otra_confirmacion(w):  # R4
    t = w.tarjeta("borra el queso mozzarella de prueba", accion="borrar_ingrediente",
                  objeto="queso mozzarella de prueba")
    w.confirmar(t)
    fijos = w.cliente.get("/api/recetas").json()["fijos"]
    assert not any(f["nombre"] == "queso mozzarella de prueba" for f in fijos)


def test_web_el_martes_sigue_en_325(w):  # R5 · C-112
    assert w.total(MARTES) == 325
    w.confirmar(w.tarjeta("a los garbanzos de prueba les puse 40 de aceite, no 50", accion="corregir_receta",
                          objeto="garbanzos de prueba", lineas=[_l("aceite", 40)]))
    assert w.total(MARTES) == 325
    w.confirmar(w.tarjeta("borra la receta garbanzos de prueba", accion="borrar_receta", objeto="garbanzos de prueba"))
    assert w.total(MARTES) == 325


def test_web_si_cambio_la_receta_no_aplica(w):  # R2 · G-41
    t = w.tarjeta("a los garbanzos de prueba les puse 40 de aceite", accion="corregir_receta",
                  objeto="garbanzos de prueba", lineas=[_l("aceite", 40)])
    g = w.base.receta("garbanzos de prueba")
    w.base.recetario.corregir(g["id"], {"peso_neto_g": 2600})  # by hand, meanwhile
    r = w.cliente.post("/api/asistente/confirmar", json={"tarjeta": t})
    assert r.status_code == 409
    assert w.base.receta("garbanzos de prueba")["en_uso"]["kcal_total"] == 2600
