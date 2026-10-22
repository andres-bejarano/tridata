"""Unit 013 · comfortable recipes: work out what was taken and log it today, weigh later, hand-written kcal
that count, and the new icon. Recetario over a temporary SQLite and the Starlette TestClient; the Claude
interpreter is a unittest.mock double (no API). Every value is invented.

Plans (version 3): `guardar-receta` REC-10 (R-57, R-58, R-59; C-67…C-72) and `apuntar-comida` R-8 as
rewritten, R-56 (C-8, C-63…C-66).
"""
import hashlib
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.ia import AlimentoInterpretado, ListaInterpretada
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas
from tridata.nutricion.recetas import Recetario
from tridata.web import crear_app

HOY = date(2026, 10, 21)  # a Wednesday
LUNES = date(2026, 10, 19)
MARTES = date(2026, 10, 20)
MANANA = HOY + timedelta(days=1)
ESTATICOS = Path(__file__).parent.parent / "src" / "tridata" / "web" / "static"

INGREDIENTES = [{"nombre": "garbanzo seco", "cantidad": 600, "unidad": "g", "kcal": 1},
                {"nombre": "aceite", "cantidad": 50, "unidad": "g", "kcal": 1},
                {"nombre": "cebolla", "cantidad": 125, "unidad": "g", "kcal": 1}]  # 2.100 + 450 + 50 = 2.600


def _recetario(db: Path, hoy: date = HOY) -> tuple[Recetario, dict]:
    """The invented saved things of §Criterios: fixed foods, the big pot and the two recipes."""
    rec = Recetario(db)
    ids = {}
    for nombre, kcal, por in (("dátiles de prueba", 280, "100g"), ("gel de prueba", 90, "unidad"),
                              ("garbanzo seco", 350, "100g"), ("aceite", 900, "100g"), ("cebolla", 40, "100g")):
        ids[nombre] = rec.crear_fijo({"nombre": nombre, "kcal": kcal, "por": por})["id"]
    ids["olla grande"] = rec.crear_olla({"nombre": "olla grande", "tara_g": 1500})["id"]
    ids["garbanzos de la abuela"] = rec.crear_receta(
        {"nombre": "garbanzos de la abuela", "ingredientes": INGREDIENTES, "olla_id": ids["olla grande"],
         "peso_lleno_g": 3500}, hoy)["id"]
    ids["lentejas de prueba"] = rec.crear_receta({"nombre": "lentejas de prueba", "ingredientes": INGREDIENTES}, hoy)["id"]
    return rec, ids


@pytest.fixture
def recetario(tmp_path):
    return _recetario(tmp_path / "data.sqlite3")


# -- R1, R3: the sum to log (para_apuntar on top of _calcular) · R-57, R-58, G-31, G-32, R-45 ----------------

def test_cuenta_para_apuntar_receta_pesada(recetario):  # R1 · R-57, C-67
    rec, ids = recetario
    a = rec.para_apuntar("receta", ids["garbanzos de la abuela"], 250)
    assert a == {"nombre": "garbanzos de la abuela", "cantidad": 250, "unidad": "g", "kcal": 325,
                 "origen": "de tu receta «garbanzos de la abuela»", "provisional": False}


def test_cuenta_para_apuntar_fijos(recetario):  # R1 · R-57, C-68
    rec, ids = recetario
    datiles = rec.para_apuntar("fijo", ids["dátiles de prueba"], 6)
    assert (datiles["kcal"], datiles["unidad"], datiles["origen"], datiles["provisional"]) == (
        16.8, "g", "de tu ingrediente «dátiles de prueba»", False)
    gel = rec.para_apuntar("fijo", ids["gel de prueba"], 2)
    assert (gel["nombre"], gel["cantidad"], gel["unidad"], gel["kcal"]) == ("gel de prueba", 2, "unidad", 180)


def test_cuenta_para_apuntar_receta_con_kcal_aproximadas(recetario):  # R1 · R-57
    rec, ids = recetario
    rec.corregir(ids["lentejas de prueba"], {"kcal_100g_aprox": 200})
    a = rec.para_apuntar("receta", ids["lentejas de prueba"], 300)
    assert (a["kcal"], a["provisional"], a["unidad"]) == (600, True, "g")


@pytest.mark.parametrize("cantidad", [None, 0, -5])
def test_sin_cantidad_no_se_calcula(recetario, cantidad):  # R3 · R-58, C-69
    rec, ids = recetario
    with pytest.raises(ValueError, match="escribe cuánto has cogido"):
        rec.para_apuntar("receta", ids["garbanzos de la abuela"], cantidad)
    with pytest.raises(ValueError, match="escribe cuánto has cogido"):
        rec.para_apuntar("fijo", ids["dátiles de prueba"], cantidad)


def test_sin_pesar_ni_aproximadas_no_se_calcula(recetario):  # R3 · R-58, C-70
    rec, ids = recetario
    with pytest.raises(ValueError, match="pésala o pon sus kcal/100 g aproximadas"):
        rec.para_apuntar("receta", ids["lentejas de prueba"], 300)


def test_ingrediente_dudoso_no_se_calcula(recetario):  # R3 · R-58, G-32
    rec, _ = recetario
    dudosa = rec.crear_receta({"nombre": "guiso raro", "ingredientes": [
        {"nombre": "kfjd", "cantidad": 100, "unidad": "g", "kcal": None, "dudoso": True}], "peso_neto_g": 500}, HOY)
    with pytest.raises(ValueError, match="corrige la receta"):
        rec.para_apuntar("receta", dudosa["id"], 100)


def test_lo_que_no_existe_es_keyerror(recetario):  # R3
    rec, _ = recetario
    with pytest.raises(KeyError):
        rec.para_apuntar("receta", 999, 100)
    with pytest.raises(KeyError):
        rec.para_apuntar("fijo", 999, 100)


# -- the web: an app over a temporary SQLite --------------------------------------------------------------

class Entorno:
    def __init__(self, db: Path, hoy: date = HOY, preparar: bool = True):
        self.db = db
        self.ajustes = Ajustes(db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "objetivo_pct": -10}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(db)
        if preparar:
            self.recetario, self.ids = _recetario(db, hoy)
        self.interprete = MagicMock()
        self.cliente = TestClient(crear_app(self.registro, self.interprete, self.ajustes, hoy=lambda: hoy))

    def post(self, ruta, cuerpo):
        return self.cliente.post(ruta, json=cuerpo)

    def ok(self, ruta, cuerpo):
        r = self.post(ruta, cuerpo)
        assert r.status_code == 200, r.text
        return r.json()

    def dia(self, fecha: date = HOY) -> dict:
        return self.cliente.get(f"/api/dia?fecha={fecha.isoformat()}").json()


@pytest.fixture
def e(tmp_path):
    return Entorno(tmp_path / "data.sqlite3")


# -- R2: «Apuntar en hoy» · R-57, R-42, R-55 (the bearing criterion) ----------------------------------------

def test_apuntar_receta_en_hoy_con_lo_guardado(e):  # R2 · R-57, C-67 — criterio portante
    e.registro.anadir(Alimento(nombre="comida de antes", dia=HOY, cantidad=500, unidad="g", kcal=1000), HOY)
    r = e.ok(f"/api/recetas/{e.ids['garbanzos de la abuela']}/apuntar", {"cantidad": 250, "kcal": 999})
    a = r["alimento"]
    assert (a["dia"], a["estado"], a["nombre"], a["cantidad"], a["unidad"], a["kcal"], a["estimado"]) == (
        HOY.isoformat(), "comido", "garbanzos de la abuela", 250, "g", 325, False)
    assert a["origen"] == "de tu receta «garbanzos de la abuela»" and a["provisional"] is False
    dia = e.dia()
    assert dia["total_kcal"] == 1325
    assert [x["kcal"] for x in dia["alimentos"] if x["nombre"] == "garbanzos de la abuela"] == [325]


def test_apuntar_fijos_en_hoy(e):  # R2 · R-57, C-68
    datiles = e.ok(f"/api/fijos/{e.ids['dátiles de prueba']}/apuntar", {"cantidad": 6})["alimento"]
    assert (datiles["cantidad"], datiles["unidad"], datiles["kcal"], datiles["origen"]) == (
        6, "g", 16.8, "de tu ingrediente «dátiles de prueba»")
    gel = e.ok(f"/api/fijos/{e.ids['gel de prueba']}/apuntar", {"cantidad": 2, "kcal": 1})["alimento"]
    assert (gel["cantidad"], gel["unidad"], gel["kcal"], gel["estado"]) == (2, "unidad", 180, "comido")
    assert e.dia()["total_kcal"] == 196.8


def test_apuntar_receta_aproximada_queda_provisional(e):  # R2 · R-57
    e.recetario.corregir(e.ids["lentejas de prueba"], {"kcal_100g_aprox": 200})
    a = e.ok(f"/api/recetas/{e.ids['lentejas de prueba']}/apuntar", {"cantidad": "300"})["alimento"]
    assert (a["kcal"], a["provisional"]) == (600, True)


@pytest.mark.parametrize("ruta, cuerpo, estado, texto", [
    ("receta:garbanzos de la abuela", {"cantidad": 0}, 422, "escribe cuánto has cogido"),
    ("receta:garbanzos de la abuela", {"cantidad": -3}, 422, "escribe cuánto has cogido"),
    ("receta:garbanzos de la abuela", {"cantidad": None}, 422, "escribe cuánto has cogido"),
    ("receta:garbanzos de la abuela", {}, 422, "escribe cuánto has cogido"),
    ("receta:garbanzos de la abuela", {"cantidad": "mucho"}, 400, "no es un número"),
    ("receta:garbanzos de la abuela", {"cantidad": True}, 400, "no es un número"),
    ("receta:lentejas de prueba", {"cantidad": 300}, 422, "pésala o pon sus kcal/100 g aproximadas"),
    ("fijo:dátiles de prueba", {"cantidad": 0}, 422, "escribe cuánto has cogido"),
    ("receta:999", {"cantidad": 100}, 404, "ya no está guardada"),
    ("fijo:999", {"cantidad": 100}, 404, "ya no está guardado"),
])
def test_cuando_no_se_puede_no_se_apunta_nada(e, ruta, cuerpo, estado, texto):  # R3 · R-58, C-69, C-70
    tipo, cual = ruta.split(":")
    ident = e.ids.get(cual, cual)
    r = e.post(f"/api/{'recetas' if tipo == 'receta' else 'fijos'}/{ident}/apuntar", cuerpo)
    assert r.status_code == estado, r.text
    assert texto in r.json()["error"]
    assert "no se ha guardado nada" in r.json()["error"].lower() or estado == 404
    assert e.registro.apuntes_del_dia(HOY) == []


def test_apuntar_con_un_ingrediente_dudoso_es_422(e):  # R3 · R-58
    dudosa = e.recetario.crear_receta({"nombre": "guiso raro", "ingredientes": [
        {"nombre": "kfjd", "cantidad": 100, "unidad": "g", "kcal": None, "dudoso": True}], "peso_neto_g": 500}, HOY)
    r = e.post(f"/api/recetas/{dudosa['id']}/apuntar", {"cantidad": 100})
    assert r.status_code == 422 and "corrige la receta" in r.json()["error"]
    assert e.registro.apuntes_del_dia(HOY) == []


def test_lo_malformado_es_400(e):  # R3
    r = e.cliente.post(f"/api/recetas/{e.ids['garbanzos de la abuela']}/apuntar", content=b"no es json",
                       headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert e.registro.apuntes_del_dia(HOY) == []


# -- R4: weigh later, with the correct route that already exists · R-59, R-41, G-32, C-43, C-57, C-71, C-72 --

def _lentejas_del_lunes(tmp_path) -> tuple[Path, int, int, dict]:
    db = tmp_path / "data.sqlite3"
    lunes = Entorno(db, hoy=LUNES)
    receta = lunes.recetario.receta(lunes.ids["lentejas de prueba"])
    assert receta["en_uso"]["estado"] == "provisional" and receta["en_uso"]["kcal_total"] == 2600
    return db, lunes.ids["lentejas de prueba"], lunes.ids["olla grande"], receta


@pytest.mark.parametrize("pesos", [{"olla": True, "peso_lleno_g": 3500}, {"peso_neto_g": 2000}],
                         ids=["olla-llena", "peso-neto"])
def test_pesar_despues_con_la_app_reiniciada(tmp_path, pesos):  # R4 · R-59, C-71, C-72
    db, lentejas, olla, antes = _lentejas_del_lunes(tmp_path)
    martes = Entorno(db, hoy=MARTES, preparar=False)  # a new app, the next day
    cuerpo = {"olla_id": olla, "peso_lleno_g": pesos["peso_lleno_g"]} if pesos.get("olla") else {
        "olla_id": None, "peso_neto_g": pesos["peso_neto_g"]}
    receta = martes.ok(f"/api/recetas/{lentejas}", cuerpo)["receta"]
    v = receta["en_uso"]
    assert (v["kcal_100g"], v["estado"], v["numero"], len(receta["versiones"])) == (130, "definitiva", 1, 1)
    assert v["fecha"] == LUNES.isoformat()
    assert v["ingredientes"] == antes["en_uso"]["ingredientes"] and v["kcal_total"] == 2600
    # after weighing it, the calculator works per gram
    a = martes.ok(f"/api/recetas/{lentejas}/apuntar", {"cantidad": 100})["alimento"]
    assert (a["kcal"], a["provisional"], a["dia"]) == (130, False, MARTES.isoformat())


def test_pesar_con_la_olla_llena_menor_que_su_tara_no_cambia_nada(tmp_path):  # R4 · C-57
    db, lentejas, olla, antes = _lentejas_del_lunes(tmp_path)
    martes = Entorno(db, hoy=MARTES, preparar=False)
    r = martes.post(f"/api/recetas/{lentejas}", {"olla_id": olla, "peso_lleno_g": 1400})
    assert r.status_code == 422 and "la olla llena no puede pesar menos que su tara" in r.json()["error"]
    assert Recetario(db).receta(lentejas) == antes


# -- R5: kcal without a quantity count (the map changes) · R-8 (rewritten), C-8, C-63 -------------------------

def test_kcal_sin_cantidad_suman_al_anadir(e):  # R5 · R-8, C-63
    e.registro.anadir(Alimento(nombre="comida de antes", dia=HOY, cantidad=500, unidad="g", kcal=1000), HOY)
    a = e.ok("/api/alimento", {"nombre": "dátiles de prueba", "dia": HOY.isoformat(), "kcal": 17})["alimento"]
    assert (a["cantidad"], a["kcal"], a["pendiente_cantidad"]) == (None, 17, False)
    dia = e.dia()
    assert dia["total_kcal"] == 1017
    assert dia["pendientes"] == []


def test_kcal_sin_cantidad_suman_al_cambiar(e):  # R5 · R-8
    a = e.ok("/api/alimento", {"nombre": "dátiles de prueba", "dia": HOY.isoformat()})["alimento"]
    assert a["pendiente_cantidad"] is True
    assert e.dia()["total_kcal"] == 0
    cambiado = e.ok(f"/api/alimento/{a['id']}", {"nombre": "dátiles de prueba", "cantidad": None, "kcal": 17})["alimento"]
    assert cambiado["pendiente_cantidad"] is False
    dia = e.dia()
    assert (dia["total_kcal"], dia["pendientes"]) == (17, [])


def test_kcal_sin_cantidad_suman_al_confirmar_y_lo_vacio_sigue_pendiente(e):  # R5 · R-8, C-8, C-63
    lista = [{"nombre": "dátiles de prueba", "dia": HOY.isoformat(), "cantidad": None, "unidad": None, "kcal": 17,
              "estimado": False, "dudoso": False, "supuestos": []},
             {"nombre": "leche en el café", "dia": HOY.isoformat(), "cantidad": None, "unidad": None, "kcal": None,
              "estimado": True, "dudoso": False, "supuestos": []}]
    guardados = e.ok("/api/confirmar", {"texto": "dátiles y leche en el café", "alimentos": lista})["guardados"]
    assert [g["pendiente_cantidad"] for g in guardados] == [False, True]
    dia = e.dia()
    assert dia["total_kcal"] == 17
    assert [p["nombre"] for p in dia["pendientes"]] == ["leche en el café"]


def test_una_fila_guardada_antes_con_kcal_y_sin_cantidad_pasa_a_sumar(e):  # R5 · R-8
    with sqlite3.connect(e.db) as conn:  # as unit 011 left it: flag 1, kcal written, no quantity
        conn.execute("INSERT INTO alimento_apuntado (dia, nombre, cantidad, unidad, kcal, estado, estimado, supuestos, "
                     "pendiente_cantidad, creado) VALUES (?, 'dátiles de antes', NULL, NULL, 17, 'comido', 0, '[]', 1, "
                     "'2026-10-21T09:00:00')", (HOY.isoformat(),))
        conn.execute("INSERT INTO alimento_apuntado (dia, nombre, cantidad, unidad, kcal, estado, estimado, supuestos, "
                     "pendiente_cantidad, creado) VALUES (?, 'leche de antes', NULL, NULL, NULL, 'comido', 0, '[]', 1, "
                     "'2026-10-21T09:00:00')", (HOY.isoformat(),))
    dia = e.dia()
    assert dia["total_kcal"] == 17
    assert [p["nombre"] for p in dia["pendientes"]] == ["leche de antes"]
    assert [a["pendiente_cantidad"] for a in e.registro.apuntes_del_dia(HOY)] == [False, True]


def test_kcal_sin_cantidad_de_un_dia_planeado_cuentan_con_lo_planeado(e):  # R5 · R-8
    e.ok("/api/alimento", {"nombre": "dátiles de prueba", "dia": MANANA.isoformat(), "kcal": 17})
    dia = e.dia(MANANA)
    assert dia["planeado_kcal"] == 17
    assert dia["pendientes"] == []


def test_la_regla_vive_en_alimento():  # R5 · R-8: «pendiente de cantidad» = sin cantidad y sin kcal
    assert Alimento(nombre="x", dia=HOY).pendiente_cantidad is True
    assert Alimento(nombre="x", dia=HOY, kcal=17).pendiente_cantidad is False
    assert Alimento(nombre="x", dia=HOY, cantidad=6, unidad="g", kcal=16.8).pendiente_cantidad is False


def test_simular_cuenta_las_kcal_a_mano(e):  # R5 · R-8
    r = e.ok("/api/simular", {"alimentos": [{"nombre": "dátiles de prueba", "dia": HOY.isoformat(), "kcal": 17}]})
    assert r["kcal_lista"] == 17


# -- R6: an estimate without a quantity does not count · R-8, C-64 ---------------------------------------------

def test_una_estimacion_sin_cantidad_llega_sin_kcal(e):  # R6 · R-8, C-64
    e.interprete.interpretar.side_effect = lambda texto, hoy: ListaInterpretada(alimentos=[AlimentoInterpretado(
        nombre="nueces de prueba", cantidad=None, unidad=None, dia=hoy, kcal=40, proteina_g=None, hidratos_g=None,
        grasa_g=None, estimado=True, dudoso=False, supuestos=["un puñado"])])
    pieza = e.ok("/api/interpretar", {"texto": "unas nueces"})["alimentos"][0]
    assert (pieza["cantidad"], pieza["kcal"]) == (None, None)
    guardado = e.ok("/api/confirmar", {"texto": "unas nueces", "alimentos": [pieza]})["guardados"][0]
    assert guardado["pendiente_cantidad"] is True
    dia = e.dia()
    assert dia["total_kcal"] == 0 and [p["nombre"] for p in dia["pendientes"]] == ["nueces de prueba"]


def test_una_estimacion_con_cantidad_sigue_igual(e):  # R6: only the quantity-less estimate loses its kcal
    e.interprete.interpretar.side_effect = lambda texto, hoy: ListaInterpretada(alimentos=[AlimentoInterpretado(
        nombre="nueces de prueba", cantidad=30, unidad="g", dia=hoy, kcal=196, proteina_g=None, hidratos_g=None,
        grasa_g=None, estimado=True, dudoso=False, supuestos=[])])
    assert e.ok("/api/interpretar", {"texto": "30 g de nueces"})["alimentos"][0]["kcal"] == 196


# -- R8: the icon · (010 R13) -------------------------------------------------------------------------------------

def test_sw_sube_la_version():  # R8
    version = re.search(r'const VERSION = "([^"]+)"', (ESTATICOS / "sw.js").read_text(encoding="utf-8")).group(1)
    assert version != "tridata-cascaron-v3"


# sha256 of `.runtime/icono-pro/icono-E*` (variant E · «Neón en la Concha», chosen by Andrés on 2026-10-21)
ICONO_E = {"icono.svg": "3e282f2a6e1c297309baa5fa581033cab7ef03436dc69fb9731c92a770ed4dab",
           "icono-32.png": "a010abc8399975c3a271aa4691f6e4427cf49289e4632efccda70d133b9f8ddb",
           "icono-180.png": "2b8fb47474118dd7811c9cb6359f3e1b897c51dc2d42db42df79137e4481f271",
           "icono-512.png": "534cfce2b92fac56a099e87e1064e0e581d681639172f6d93392679a26bd33e8"}


@pytest.mark.parametrize("fichero", sorted(ICONO_E))
def test_el_icono_es_la_variante_elegida(fichero):  # R8
    assert hashlib.sha256((ESTATICOS / fichero).read_bytes()).hexdigest() == ICONO_E[fichero]


def test_el_icono_no_lleva_la_foto():  # R8: only the finished vectors, never a pixel of the photo
    svg = (ESTATICOS / "icono.svg").read_text(encoding="utf-8")
    assert "<image" not in svg and "base64" not in svg
