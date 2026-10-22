"""The Recetas tab over the web (unit 011): its routes, interpreting and dictating with what is saved,
and `tridata importar-recetas`. Starlette TestClient over a temporary SQLite; the Claude interpreter
and the Whisper transcriber are unittest.mock doubles (no API, no GPU). Every value is invented.
"""
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata import cli
from tridata.ia import AlimentoInterpretado, ListaInterpretada
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, RegistroComidas
from tridata.nutricion.recetas import Recetario
from tridata.web import crear_app

HOY = date(2026, 10, 21)
AYER = HOY - timedelta(days=1)
AUDIO = b"\x00\x00\x00\x18ftypmp42-audio-inventado"
FIXTURE = Path(__file__).parent / "fixtures" / "recetas_inventado.md"


def _item(nombre, cantidad, unidad="g", kcal=100.0, guardado=None, coincidencia=None, dia=HOY, **extra):
    return AlimentoInterpretado(nombre=nombre, cantidad=cantidad, unidad=unidad, dia=dia, kcal=kcal, proteina_g=None,
                                hidratos_g=None, grasa_g=None, estimado=True, dudoso=extra.pop("dudoso", False),
                                supuestos=["supuesto de Claude"], guardado=guardado, coincidencia=coincidencia)


# What the double «understands», by text (Claude is never called).
RESPUESTAS = {
    "garbanzo seco 600 g, aceite de oliva 50 g y cebolla 125 g": [
        _item("garbanzo seco", 600, kcal=2000, guardado="garbanzo seco", coincidencia="exacta"),
        _item("aceite de oliva", 50, kcal=400, guardado="aceite de oliva", coincidencia="exacta"),
        _item("cebolla", 125, kcal=60, guardado="cebolla", coincidencia="exacta")],
    "garbanzos de la abuela 250 g": [
        _item("garbanzos de la abuela", 250, kcal=400, guardado="garbanzos de la abuela", coincidencia="exacta")],
    "200 g de garbanzos": [
        _item("garbanzos", 200, kcal=250, guardado="garbanzos de la abuela", coincidencia="parecida")],
    "300 g de lentejas de la vecina": [_item("lentejas de la vecina", 300, kcal=350)],
    "2 guayabas": [_item("guayaba", 2, unidad="unidad", kcal=120, guardado="guayaba", coincidencia="exacta")],
}


class Entorno:
    def __init__(self, tmp_path):
        self.db = tmp_path / "data.sqlite3"
        ajustes = Ajustes(self.db)
        ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "objetivo_pct": -10}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.interprete = MagicMock()
        self.interprete.interpretar.side_effect = lambda texto, hoy: ListaInterpretada(
            alimentos=[a.model_copy(update={"dia": hoy}) for a in RESPUESTAS[texto]])
        self.transcriptor = MagicMock(spec=["transcribir"])
        self.cliente = TestClient(crear_app(self.registro, self.interprete, ajustes, hoy=lambda: HOY,
                                            transcriptor=self.transcriptor))

    def post(self, ruta, cuerpo=None):
        return self.cliente.post(ruta, json=cuerpo if cuerpo is not None else {})

    def ok(self, ruta, cuerpo=None):
        r = self.post(ruta, cuerpo)
        assert r.status_code == 200, r.text
        return r.json()

    def fijos_de_prueba(self):
        for nombre, kcal in (("garbanzo seco", 350), ("aceite de oliva", 900), ("cebolla", 40)):
            self.ok("/api/fijos", {"nombre": nombre, "kcal": kcal, "por": "100g"})
        return self.ok("/api/ollas", {"nombre": "olla grande", "tara_g": 1500})["olla"]

    def garbanzos(self):
        olla = self.fijos_de_prueba()
        ingredientes = self.ok("/api/interpretar", {"texto": "garbanzo seco 600 g, aceite de oliva 50 g y cebolla 125 g"})
        return self.ok("/api/recetas", {"nombre": "garbanzos de la abuela", "ingredientes": ingredientes["alimentos"],
                                        "olla_id": olla["id"], "peso_lleno_g": 3500})["receta"]

    def tablas(self):
        with sqlite3.connect(self.db) as conn:
            return {t: conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()  # noqa: S608 - fixed names
                    for t in ("receta", "receta_version", "alimento_fijo", "olla")}


@pytest.fixture
def e(tmp_path):
    return Entorno(tmp_path)


# -- R11: the listing ------------------------------------------------------------------------------

def test_listado_vacio_y_lleno(e):  # R11
    assert e.cliente.get("/api/recetas").json() == {"recetas": [], "fijos": [], "ollas": []}
    e.garbanzos()
    e.ok("/api/fijos", {"nombre": "dulce de guayaba de ciclismo", "kcal": 90, "por": "unidad", "alias": "guayaba",
                        "nota": "no la fruta"})
    datos = e.cliente.get("/api/recetas").json()
    receta = datos["recetas"][0]
    assert (receta["nombre"], receta["en_uso"]["kcal_100g"], receta["en_uso"]["estado"]) == (
        "garbanzos de la abuela", 130.0, "definitiva")
    assert [v["numero"] for v in receta["versiones"]] == [1]
    guayaba = next(f for f in datos["fijos"] if f["nombre"] == "dulce de guayaba de ciclismo")
    assert (guayaba["kcal"], guayaba["por"], guayaba["alias"], guayaba["nota"]) == (90, "unidad", ["guayaba"], "no la fruta")
    assert [(o["nombre"], o["tara_g"]) for o in datos["ollas"]] == [("olla grande", 1500)]


# -- R1: the server does not trust the kcal of the page ---------------------------------------------

def test_el_servidor_recalcula_los_fijos(e):  # R1
    olla = e.fijos_de_prueba()
    falsos = [{"nombre": "garbanzo seco", "cantidad": 600, "unidad": "g", "kcal": 1, "estimado": False},
              {"nombre": "aceite de oliva", "cantidad": 50, "unidad": "g", "kcal": 1},
              {"nombre": "cebolla", "cantidad": 125, "unidad": "g", "kcal": 1}]
    receta = e.ok("/api/recetas", {"nombre": "garbanzos de la abuela", "ingredientes": falsos, "olla_id": olla["id"],
                                   "peso_lleno_g": 3500})["receta"]
    assert receta["en_uso"]["kcal_total"] == 2600 and receta["en_uso"]["kcal_100g"] == 130.0


# -- R3, R4: interpreting and dictating with what is saved -------------------------------------------

def test_interpretar_usa_la_receta_y_al_confirmar_guarda_el_origen(e):  # R3
    e.garbanzos()
    r = e.ok("/api/interpretar", {"texto": "garbanzos de la abuela 250 g"})
    a = r["alimentos"][0]
    assert (a["nombre"], a["kcal"], a["estimado"], a["provisional"], a["propuesta"]) == (
        "garbanzos de la abuela", 325, False, False, None)
    assert a["origen"].startswith("de tu receta «garbanzos de la abuela»") and a["supuestos"] == []
    confirmado = e.ok("/api/confirmar", {"texto": r["texto"], "alimentos": r["alimentos"]})
    assert confirmado["guardados"][0]["origen"] == a["origen"]
    fila = e.cliente.get("/api/dia").json()["alimentos"][0]
    assert (fila["kcal"], fila["origen"], fila["provisional"]) == (325, a["origen"], False)


def test_dictar_usa_la_receta_igual(e):  # R3
    e.garbanzos()
    e.transcriptor.transcribir.return_value = "garbanzos de la abuela 250 g"
    r = e.cliente.post(f"/api/dictado?id=11111111-2222-4333-8444-555555555555&fecha={HOY}", content=AUDIO,
                       headers={"Content-Type": "audio/mp4"})
    assert r.status_code == 200, r.text
    a = r.json()["interpretacion"]["alimentos"][0]
    assert (a["kcal"], a["estimado"]) == (325, False) and a["origen"].startswith("de tu receta")


def test_fijo_por_unidad_y_lo_desconocido(e):  # R3, R4
    e.ok("/api/fijos", {"nombre": "dulce de guayaba de ciclismo", "kcal": 90, "por": "unidad", "alias": ["guayaba"]})
    guayaba = e.ok("/api/interpretar", {"texto": "2 guayabas"})["alimentos"][0]
    assert (guayaba["nombre"], guayaba["kcal"], guayaba["estimado"]) == ("dulce de guayaba de ciclismo", 180, False)
    lentejas = e.ok("/api/interpretar", {"texto": "300 g de lentejas de la vecina"})["alimentos"][0]
    assert (lentejas["kcal"], lentejas["estimado"], lentejas.get("origen"), lentejas.get("propuesta")) == (
        350, True, None, None)  # untouched: it goes out as before unit 011, without the new keys


def test_lo_parecido_llega_como_propuesta(e):  # R4
    e.garbanzos()
    a = e.ok("/api/interpretar", {"texto": "200 g de garbanzos"})["alimentos"][0]
    assert (a["nombre"], a["kcal"], a["kcal_estimada"]) == ("garbanzos de la abuela", 260, 250)
    assert a["propuesta"] == {"dicho": "garbanzos", "guardado": "garbanzos de la abuela"}


def test_confirmar_lleva_provisional(e):  # R6, D8
    e.ok("/api/recetas", {"nombre": "lasaña de la tía", "ingredientes": [
        {"nombre": "pasta de lasaña", "cantidad": 400, "unidad": "g", "kcal": 1400},
        {"nombre": "carne picada", "cantidad": 800, "unidad": "g", "kcal": 2000},
        {"nombre": "tomate frito", "cantidad": 500, "unidad": "g", "kcal": 600}]})
    cuarto = {"nombre": "lasaña de la tía", "cantidad": 0.25, "unidad": "receta", "dia": HOY.isoformat(), "kcal": 1000,
              "estimado": False, "dudoso": False, "supuestos": [], "origen": "de tu receta «lasaña de la tía»",
              "provisional": True}
    e.ok("/api/confirmar", {"texto": "un cuarto de lasaña de la tía", "alimentos": [cuarto]})
    fila = e.cliente.get("/api/dia").json()["alimentos"][0]
    assert (fila["kcal"], fila["provisional"], fila["origen"]) == (1000, True, "de tu receta «lasaña de la tía»")


# -- R12, R13, R14: protected entries ------------------------------------------------------------------

def test_repetir_el_nombre_de_una_receta_es_otra_tanda(e):  # R12
    receta = e.garbanzos()
    r = e.post("/api/recetas", {"nombre": "Garbanzos de la abuela", "ingredientes": [
        {"nombre": "garbanzo seco", "cantidad": 500, "unidad": "g", "kcal": 1}], "peso_neto_g": 2000})
    assert r.status_code == 409
    assert r.json()["otra_tanda"] is True and r.json()["receta_id"] == receta["id"]
    assert "otra tanda" in r.json()["error"]
    tanda = e.ok("/api/recetas", {"nombre": "Garbanzos de la abuela", "otra_tanda": True, "ingredientes": [
        {"nombre": "garbanzo seco", "cantidad": 500, "unidad": "g", "kcal": 1},
        {"nombre": "aceite de oliva", "cantidad": 50, "unidad": "g", "kcal": 1}], "peso_neto_g": 2000})["receta"]
    assert tanda["id"] == receta["id"] and tanda["en_uso"]["numero"] == 2 and tanda["en_uso"]["kcal_100g"] == 110.0
    vuelta = e.ok(f"/api/recetas/{receta['id']}/version", {"numero": 1})["receta"]
    assert vuelta["en_uso"]["numero"] == 1


@pytest.mark.parametrize("ruta, cuerpo, texto", [
    ("/api/fijos", {"nombre": "manteca de prueba", "kcal": 1500, "por": "100g"}, "nada pasa de 900 kcal/100 g"),
    ("/api/fijos", {"nombre": "gel de prueba", "kcal": -5, "por": "unidad"}, "negativ"),
    ("/api/fijos", {"nombre": "Guayaba", "kcal": 68, "por": "100g"}, "dulce de guayaba de ciclismo"),
    ("/api/ollas", {"nombre": "táper de prueba", "tara_g": 0}, "tara"),
])
def test_reglas_de_negocio_son_422_con_su_texto(e, ruta, cuerpo, texto):  # R12, R13
    e.ok("/api/fijos", {"nombre": "dulce de guayaba de ciclismo", "kcal": 90, "por": "unidad", "alias": ["guayaba"]})
    antes = e.tablas()
    r = e.post(ruta, cuerpo)
    assert r.status_code == 422 and texto in r.json()["error"]
    assert e.tablas() == antes


def test_olla_llena_menor_que_su_tara(e):  # R13, C-57
    olla = e.fijos_de_prueba()
    antes = e.tablas()
    r = e.post("/api/recetas", {"nombre": "garbanzos de la abuela", "olla_id": olla["id"], "peso_lleno_g": 1400,
                                "ingredientes": [{"nombre": "garbanzo seco", "cantidad": 600, "unidad": "g", "kcal": 1}]})
    assert r.status_code == 422 and "la olla llena no puede pesar menos que su tara" in r.json()["error"]
    assert e.tablas() == antes


@pytest.mark.parametrize("ruta, cuerpo", [
    ("/api/fijos", {"nombre": "x de prueba", "kcal": "mucho", "por": "100g"}),
    ("/api/fijos", {"nombre": "x de prueba", "kcal": 10, "por": "kilo"}),
    ("/api/fijos", {"kcal": 10, "por": "100g"}),
    ("/api/ollas", {"nombre": "x de prueba", "tara_g": "pesada"}),
    ("/api/recetas", {"nombre": "x de prueba", "ingredientes": "no es una lista"}),
    ("/api/recetas", {"nombre": "x de prueba", "ingredientes": [{"cantidad": 3}]}),
])
def test_datos_mal_formados_son_400(e, ruta, cuerpo):
    antes = e.tablas()
    assert e.post(ruta, cuerpo).status_code == 400
    assert e.cliente.post(ruta, content=b"no es json", headers={"Content-Type": "application/json"}).status_code == 400
    assert e.tablas() == antes


def test_borrar_solo_con_confirmado(e):  # R14, C-59, C-61
    receta = e.garbanzos()
    e.ok("/api/recetas", {"nombre": "garbanzos de la abuela", "otra_tanda": True, "ingredientes": [
        {"nombre": "garbanzo seco", "cantidad": 500, "unidad": "g", "kcal": 1}], "peso_neto_g": 2000})
    fijo = e.cliente.get("/api/recetas").json()["fijos"][0]
    olla = e.cliente.get("/api/recetas").json()["ollas"][0]
    antes = e.tablas()
    for ruta in (f"/api/recetas/{receta['id']}/borrar", f"/api/fijos/{fijo['id']}/borrar",
                 f"/api/ollas/{olla['id']}/borrar"):
        assert e.post(ruta, {}).status_code == 400
        assert e.post(ruta, {"confirmado": "sí"}).status_code == 400
    assert e.tablas() == antes
    assert len(e.cliente.get("/api/recetas").json()["recetas"][0]["versiones"]) == 2
    datos = e.ok(f"/api/recetas/{receta['id']}/borrar", {"confirmado": True})
    assert datos["recetas"] == []
    e.ok(f"/api/fijos/{fijo['id']}/borrar", {"confirmado": True})
    e.ok(f"/api/ollas/{olla['id']}/borrar", {"confirmado": True})
    assert e.post(f"/api/recetas/{receta['id']}/borrar", {"confirmado": True}).status_code == 404
    assert e.post(f"/api/fijos/{fijo['id']}/borrar", {"confirmado": True}).status_code == 404
    assert e.post("/api/recetas/999", {"kcal_100g_aprox": 100}).status_code == 404


# -- R18: editing a fixed food and a pot --------------------------------------------------------------

def test_editar_fijo_y_olla(e):  # R18
    guayaba = e.ok("/api/fijos", {"nombre": "dulce de guayaba de ciclismo", "kcal": 90, "por": "unidad",
                                  "alias": ["guayaba"]})["fijo"]
    ayer = {"nombre": "dulce de guayaba de ciclismo", "cantidad": 2, "unidad": "unidad", "dia": AYER.isoformat(),
            "kcal": 180, "estimado": False, "dudoso": False, "supuestos": [],
            "origen": "de tu ingrediente «dulce de guayaba de ciclismo»"}
    e.ok("/api/confirmar", {"texto": "2 guayabas", "alimentos": [ayer]})
    cambiado = e.ok(f"/api/fijos/{guayaba['id']}", {"nombre": "dulce de guayaba de ciclismo", "kcal": 95,
                                                    "por": "unidad", "alias": ["guayaba"]})["fijo"]
    assert cambiado["kcal"] == 95
    assert e.ok("/api/interpretar", {"texto": "2 guayabas"})["alimentos"][0]["kcal"] == 190
    assert e.cliente.get(f"/api/dia?fecha={AYER}").json()["alimentos"][0]["kcal"] == 180
    e.ok("/api/ollas", {"nombre": "olla grande", "tara_g": 1500})
    for malo in ({"nombre": "dulce de guayaba de ciclismo", "kcal": 1500, "por": "100g"},
                 {"nombre": "dulce de guayaba de ciclismo", "kcal": 95, "por": "unidad", "alias": ["olla grande"]}):
        assert e.post(f"/api/fijos/{guayaba['id']}", malo).status_code == 422
    assert e.cliente.get("/api/recetas").json()["fijos"][0]["kcal"] == 95
    olla = e.cliente.get("/api/recetas").json()["ollas"][0]
    assert e.post(f"/api/ollas/{olla['id']}", {"nombre": "olla grande", "tara_g": 0}).status_code == 422
    assert e.ok(f"/api/ollas/{olla['id']}", {"nombre": "olla grande", "tara_g": 1600})["olla"]["tara_g"] == 1600
    assert e.post("/api/ollas/999", {"nombre": "olla de prueba", "tara_g": 10}).status_code == 404


# -- R16 without a browser: E2E-1 end to end with TestClient --------------------------------------------

def test_e2e_1_sin_navegador(e):  # R16, E2E-1
    receta = e.garbanzos()
    assert (receta["en_uso"]["kcal_total"], receta["en_uso"]["kcal_100g"], receta["en_uso"]["estado"]) == (
        2600, 130.0, "definitiva")
    lista = e.ok("/api/interpretar", {"texto": "garbanzos de la abuela 250 g"})
    assert lista["alimentos"][0]["kcal"] == 325 and lista["alimentos"][0]["estimado"] is False
    dia = e.ok("/api/confirmar", {"texto": lista["texto"], "alimentos": lista["alimentos"]})["dia"]
    assert dia["total_kcal"] == 325
    assert dia["alimentos"][0]["origen"].startswith("de tu receta")
    assert e.cliente.get("/api/dia").json()["total_kcal"] == 325


# -- `tridata web` gives Claude the names, `tridata importar-recetas` loads deficit.md ---------------------

def test_tridata_web_pasa_los_nombres_al_interprete(tmp_path, monkeypatch):  # R5
    monkeypatch.setenv("TRIDATA_BASAL_KCAL", "1600")
    monkeypatch.setenv("TRIDATA_KCAL_POR_PASO", "0.04")
    monkeypatch.setattr("uvicorn.run", MagicMock())
    creados = []
    monkeypatch.setattr("tridata.ia.InterpreteClaude.__init__",
                        lambda self, cliente=None, modelo=None, guardados=None: creados.append(guardados))
    db = tmp_path / "data.sqlite3"
    Recetario(db).crear_fijo({"nombre": "gel de prueba", "kcal": 100, "por": "unidad", "alias": ["gel"]})
    assert cli._cmd_web("127.0.0.1", 8765, db_path=db) == 0
    assert creados and creados[0] is not None and sorted(creados[0]()) == ["gel", "gel de prueba"]


@pytest.fixture
def carga(tmp_path, monkeypatch):
    db = tmp_path / "data.sqlite3"
    RegistroComidas(db)  # the store already exists, as Andrés's does
    copias = []
    monkeypatch.setattr(cli, "_cmd_backup", lambda store, dest, keep, extras: copias.append((dest, extras)) or 0)
    return {"db": db, "copias": copias, "md": FIXTURE, "backups": tmp_path / "copias"}


def _importar(carga, simular=False):
    return cli._cmd_importar_recetas(carga["md"], simular, db_path=carga["db"], backup_dir=carga["backups"], hoy=HOY)


def test_importar_recetas_simular_no_escribe(carga, capsys):  # R15
    assert _importar(carga, simular=True) == 0
    salida = capsys.readouterr().out
    assert "Potaje de prueba" in salida and "Simulación" in salida
    assert Recetario(carga["db"]).listado() == {"recetas": [], "fijos": [], "ollas": []}
    assert carga["copias"] == []


def test_importar_recetas_de_verdad_y_dos_veces(carga, capsys):  # R15
    assert _importar(carga) == 0
    assert carga["copias"] and carga["md"] in carga["copias"][0][1]
    datos = Recetario(carga["db"]).listado()
    estados = sorted(r["en_uso"]["estado"] for r in datos["recetas"])
    assert estados == ["definitiva", "provisional"]
    assert len(datos["fijos"]) == 1 and len(datos["ollas"]) == 1
    assert _importar(carga) == 0
    assert Recetario(carga["db"]).listado() == datos
    assert "ya estaba" in capsys.readouterr().out


def test_importar_recetas_si_la_copia_falla_no_escribe(carga, monkeypatch, capsys):  # R15
    monkeypatch.setattr(cli, "_cmd_backup", lambda *a, **k: 1)
    assert _importar(carga) != 0
    assert "copia" in capsys.readouterr().err.lower()
    assert Recetario(carga["db"]).listado() == {"recetas": [], "fijos": [], "ollas": []}


def test_la_orden_importar_recetas(monkeypatch):
    llamadas = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "_cmd_importar_recetas", lambda md, simular: llamadas.append((md, simular)) or 0)
    assert cli.main(["importar-recetas", "--md", "a.md", "--simular"]) == 0
    assert cli.main(["importar-recetas", "--md", "a.md"]) == 0
    assert llamadas == [(Path("a.md"), True), (Path("a.md"), False)]


def test_importar_recetas_simular_formatos_de_deficit(carga, capsys):  # bug 012 · R6
    carga["md"] = Path(__file__).parent / "fixtures" / "recetas_formatos_inventado.md"
    assert _importar(carga, simular=True) == 0
    lineas = capsys.readouterr().out.splitlines()
    for esperada in ("alimento fijo «Mermelada de prueba» · 250 kcal/100 g",
                     "receta «Garbanzos de la tía» · definitiva · 123.4 kcal/100 g",
                     "receta «Flan de prueba» · provisional · 1000 kcal la receta entera",
                     "alimento fijo «Galletas de prueba rellenas» · 60 kcal/unidad"):
        assert any(esperada in linea for linea in lineas), esperada
    assert sum("sin kcal" in linea for linea in lineas) == 1
    assert any("«Guiso de prueba sin datos»" in linea and "sin kcal" in linea for linea in lineas)
    for nombre in re.findall(r"«([^»]*)»", "\n".join(lineas)):
        assert "(" not in nombre, nombre
    assert Recetario(carga["db"]).listado() == {"recetas": [], "fijos": [], "ollas": []}
    assert carga["copias"] == []
