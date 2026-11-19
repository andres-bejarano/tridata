"""Unit 098 (REC-43, mapa v42, P-20261112-03238263): protein, carbs and fat in what is saved and when logging.

- R1 (R-269, C-348): a fixed food keeps P · H · G per 100 g or per unit (as its kcal) and whether they are
  «estimado» or «de etiqueta»; a recipe adds up its ingredients' and spreads them by its weight, as its kcal. One
  ingredient with kcal and without macros leaves the recipe «sin macros» (never half a total).
- R2, R3 (R-270, C-343): `preparar_macros` lists what is missing and, with the team's values, a table and its
  fingerprint, writing nothing; `guardar_macros` with that fingerprint saves only those rows as «estimado» (minus the
  ones Andrés says no to) and recomputes the recipes in use. More than 100 g per 100 g or negative is refused; macros
  that do not match the kcal by more than 20 % are warned, not blocked.
- R4 (R-271, C-342, load-bearing): logging from a saved thing (app or team, by name or id) carries its macros for the
  quantity: 300 g of «Lentejas de la abuela» at 9 g of protein per 100 g → 27 g.
- R5 (R-271): changing the quantity (app or `preparar_cambio`/`hacer_cambio`) scales the macros as the kcal; another
  food takes the new one's.
- R6 (R-272, C-349): `apuntar_comida` asks the team to estimate the macros of what is not saved.

Every datum is invented; every database is a temporary SQLite; nothing talks to an outside service.
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from tridata.conversaciones import Conversaciones
from tridata.conversaciones.desde_pc import TITULO, montar_equipo
from tridata.mcp_server import build_server
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas
from tridata.nutricion.recetas import Recetario
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 11, 19)
AYER = date(2026, 11, 18)
AHORA = datetime(2026, 11, 19, 10, 0)
HUELLA = re.compile(r"Huella: ([0-9a-f]{12})")
MACROS = ("proteina_g", "hidratos_g", "grasa_g")

AVENA = {"nombre": "avena inventada", "kcal": 370, "por": "100g", "proteina": 13, "hidratos": 60, "grasa": 7}
MANZANA = {"nombre": "manzana inventada", "kcal": 52, "por": "100g", "proteina": 0.3, "hidratos": 14, "grasa": 0.2}
HUEVO = {"nombre": "huevo inventado", "kcal": 70, "por": "unidad", "proteina": 6, "hidratos": 0.5, "grasa": 5}
LENTEJA = {"nombre": "lenteja seca inventada", "kcal": 340, "por": "100g", "proteina": 24, "hidratos": 48,
           "grasa": 1.6}


def _linea(nombre: str, gramos: float, unidad: str = "g") -> dict:
    return {"nombre": nombre, "cantidad": gramos, "unidad": unidad, "kcal": 1, "guardado": nombre}


def _tablas(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _apuntados(db: Path) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT * FROM alimento_apuntado ORDER BY id").fetchall()
    finally:
        conn.close()


@pytest.fixture
def rec(tmp_path) -> Recetario:
    return Recetario(tmp_path / "data.sqlite3")


def _porridge(rec: Recetario, **peso) -> dict:
    rec.crear_fijo(AVENA)
    rec.crear_fijo(MANZANA)
    return rec.crear_receta({"nombre": "Porridge", "ingredientes": [_linea("avena inventada", 40),
                                                                    _linea("manzana inventada", 100)], **peso}, HOY)


def _lentejas(rec: Recetario) -> dict:
    """300 g of dry lentils (72 g of protein) in 800 g cooked: 9 g of protein per 100 g."""
    rec.crear_fijo(LENTEJA)
    return rec.crear_receta({"nombre": "Lentejas de la abuela", "peso_neto_g": 800,
                             "ingredientes": [_linea("lenteja seca inventada", 300)]}, HOY)


# -- R1 · R-269, C-348: what is saved ------------------------------------------------------------------------------

def test_ingrediente_guarda_macros_por_100g_y_por_unidad_y_su_origen(rec):  # R1
    avena = rec.crear_fijo(AVENA)
    huevo = rec.crear_fijo(HUEVO)
    assert (avena["proteina"], avena["hidratos"], avena["grasa"]) == (13, 60, 7)
    assert (huevo["proteina"], huevo["hidratos"], huevo["grasa"], huevo["por"]) == (6, 0.5, 5, "unidad")
    assert avena["macros_origen"] == "etiqueta" and huevo["macros_origen"] == "etiqueta"  # written by hand
    sin = rec.crear_fijo({"nombre": "sal inventada", "kcal": 0, "por": "100g"})
    assert (sin["proteina"], sin["hidratos"], sin["grasa"], sin["macros_origen"]) == (None, None, None, None)
    leido = {f["nombre"]: f for f in rec.fijos()}
    assert leido["avena inventada"]["proteina"] == 13 and leido["huevo inventado"]["grasa"] == 5


def test_cambiar_un_fijo_sin_mandar_macros_los_conserva_y_mandarlos_los_deja_de_etiqueta(rec):  # R1, R7
    avena = rec.crear_fijo({"nombre": "avena inventada", "kcal": 370, "por": "100g"})
    rec.guardar_macros([{"clave": f"fijo:{avena['id']}", "proteina": 12, "hidratos": 61, "grasa": 7}])
    assert rec.fijos()[-1]["macros_origen"] == "estimado"
    cambiado = rec.cambiar_fijo(avena["id"], {"nombre": "avena inventada", "kcal": 370, "por": "100g",
                                              "nota": "de la marca inventada"})
    assert (cambiado["proteina"], cambiado["macros_origen"]) == (12, "estimado")  # not sent: kept as they were
    cambiado = rec.cambiar_fijo(avena["id"], {"nombre": "avena inventada", "kcal": 370, "por": "100g",
                                              "proteina": 13.5, "hidratos": 61, "grasa": 7})
    assert (cambiado["proteina"], cambiado["macros_origen"]) == (13.5, "etiqueta")


@pytest.mark.parametrize("macros, motivo", [
    ({"proteina": 50, "hidratos": 40, "grasa": 20}, "100 g"),
    ({"proteina": -1, "hidratos": 40, "grasa": 2}, "negativ"),
    ({"proteina": 10, "hidratos": None, "grasa": 2}, "los tres"),
])
def test_macros_a_mano_imposibles_no_se_guardan(rec, macros, motivo):  # R1, R3
    with pytest.raises(ValueError, match=motivo):
        rec.crear_fijo({"nombre": "cosa inventada", "kcal": 300, "por": "100g", **macros})
    assert [f for f in rec.fijos() if f["nombre"] == "cosa inventada"] == []


def test_base_vieja_sin_columnas_de_macros_se_migra(tmp_path):  # R1: columns added on an old base (ALTER)
    db = tmp_path / "data.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE alimento_fijo (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
            alias TEXT NOT NULL DEFAULT '[]', kcal REAL NOT NULL, por TEXT NOT NULL, nota TEXT,
            comun INTEGER NOT NULL DEFAULT 0);
        INSERT INTO alimento_fijo (nombre, kcal, por) VALUES ('pan inventado', 250, '100g');""")
    conn.commit()
    conn.close()
    (pan,) = Recetario(db).fijos()
    assert pan["kcal"] == 250 and (pan["proteina"], pan["macros_origen"]) == (None, None)
    Recetario(db)  # twice: nothing to add, no error


def test_receta_suma_sus_ingredientes_y_reparte_por_su_peso(rec):  # R1 · C-348
    porridge = _porridge(rec, peso_neto_g=300)
    v = porridge["en_uso"]
    total = v["macros_total"]
    assert total == {"proteina_g": 5.5, "hidratos_g": 38.0, "grasa_g": 3.0}  # 40 g avena + 100 g manzana
    assert v["macros_100g"] == {"proteina_g": round(5.5 / 3, 1), "hidratos_g": round(38 / 3, 1),
                                "grasa_g": round(3 / 3, 1)}
    assert v["kcal_100g"] == round(200 / 3, 1) and v["sin_macros"] == 0
    linea = v["ingredientes"][0]
    assert (linea["proteina_g"], linea["hidratos_g"], linea["grasa_g"]) == (5.2, 24, 2.8)


def test_receta_con_un_ingrediente_sin_macros_queda_sin_macros(rec):  # R1: never half a total
    rec.crear_fijo(AVENA)
    rec.crear_fijo({"nombre": "miel inventada", "kcal": 300, "por": "100g"})
    r = rec.crear_receta({"nombre": "Porridge con miel", "peso_neto_g": 300, "ingredientes": [
        _linea("avena inventada", 40), _linea("miel inventada", 20)]}, HOY)
    v = r["en_uso"]
    assert v["macros_total"] is None and v["macros_100g"] is None and v["sin_macros"] == 1
    estimada = rec.crear_receta({"nombre": "Tostada inventada", "ingredientes": [
        _linea("avena inventada", 40), {"nombre": "mermelada casera", "cantidad": 30, "unidad": "g", "kcal": 60}]},
        HOY)
    assert estimada["en_uso"]["macros_total"] is None and estimada["en_uso"]["sin_macros"] == 1


def test_racion_de_receta_en_proporcion_a_sus_kcal(rec):  # R1, R4: weighed, not weighed and by fraction
    pesado = _porridge(rec, peso_neto_g=300)
    por_gramos = rec.para_apuntar("receta", pesado["id"], 150)
    assert por_gramos["kcal"] == 100 and por_gramos["proteina_g"] == round(5.5 / 2, 1)
    assert por_gramos["hidratos_g"] == 19 and por_gramos["grasa_g"] == 1.5
    cuarto = rec.para_apuntar("receta", pesado["id"], 0.25, "receta")
    assert cuarto["kcal"] == 50 and (cuarto["proteina_g"], cuarto["hidratos_g"]) == (round(5.5 / 4, 1), 9.5)
    sin_pesar = rec.crear_receta({"nombre": "Porridge sin pesar", "kcal_100g_aprox": 80, "ingredientes": [
        _linea("avena inventada", 40), _linea("manzana inventada", 100)]}, HOY)
    aprox = rec.para_apuntar("receta", sin_pesar["id"], 150)  # 120 kcal of 200: 0.6 of the recipe
    assert aprox["kcal"] == 120 and aprox["provisional"] is True
    assert (aprox["proteina_g"], aprox["hidratos_g"], aprox["grasa_g"]) == (3.3, 22.8, 1.8)
    assert sin_pesar["en_uso"]["macros_100g"] == {"proteina_g": 2.2, "hidratos_g": 15.2, "grasa_g": 1.2}


def test_ingrediente_guardado_por_gramos_y_por_unidad(rec):  # R1, R4
    avena = rec.crear_fijo(AVENA)
    huevo = rec.crear_fijo(HUEVO)
    gramos = rec.para_apuntar("fijo", avena["id"], 50)
    assert (gramos["kcal"], gramos["proteina_g"], gramos["hidratos_g"], gramos["grasa_g"]) == (185, 6.5, 30, 3.5)
    dos = rec.para_apuntar("fijo", huevo["id"], 2)
    assert (dos["kcal"], dos["proteina_g"], dos["hidratos_g"], dos["grasa_g"]) == (140, 12, 1, 10)


def test_guardado_sin_macros_se_apunta_con_macros_vacios(rec):  # R4: as today
    pan = rec.crear_fijo({"nombre": "pan inventado", "kcal": 250, "por": "100g"})
    datos = rec.para_apuntar("fijo", pan["id"], 80)
    assert datos["kcal"] == 200 and all(datos[k] is None for k in MACROS)


# -- R2, R3 · R-270, C-343: filling them in once -------------------------------------------------------------------

class Equipo:
    """The team of the PC over a temporary SQLite, through the MCP server as the coach calls it."""

    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.store = DataStore(self.db)
        self.equipo = montar_equipo(self.db, store=self.store, medicos_dir=None, token=tmp_path / "token.json",
                                    hoy=lambda: HOY, ahora=lambda: AHORA)
        self.recetario: Recetario = self.equipo.asistente.recetario
        self.registro: RegistroComidas = self.equipo.asistente.registro
        self.server = build_server(self.store, equipo=self.equipo)

    def llamar(self, nombre: str, argumentos: dict | None = None) -> str:
        resultado = asyncio.run(self.server.call_tool(nombre, argumentos or {}))
        assert not resultado.is_error, resultado
        return "".join(c.text for c in resultado.content)

    def huella(self, texto: str) -> str:
        encontrada = HUELLA.search(texto)
        assert encontrada, texto
        return encontrada.group(1)

    def mensajes_pc(self) -> list[dict]:
        conversaciones = Conversaciones(self.db)
        return [m for c in conversaciones.lista() if c["titulo"] == TITULO for m in conversaciones.mensajes(c["id"])]


@pytest.fixture
def eq(tmp_path) -> Equipo:
    return Equipo(tmp_path)


def _sin_macros(eq: Equipo) -> dict:
    """Three fixed foods without macros and a recipe with two of them and an estimated line, plus a food logged
    yesterday from the recipe."""
    rec = eq.recetario
    avena = rec.crear_fijo({"nombre": "avena inventada", "kcal": 370, "por": "100g"})
    manzana = rec.crear_fijo({"nombre": "manzana inventada", "kcal": 52, "por": "100g"})
    huevo = rec.crear_fijo({"nombre": "huevo inventado", "kcal": 70, "por": "unidad"})
    porridge = rec.crear_receta({"nombre": "Porridge", "peso_neto_g": 300, "ingredientes": [
        _linea("avena inventada", 40), _linea("manzana inventada", 100),
        {"nombre": "canela casera", "cantidad": 5, "unidad": "g", "kcal": 12}]}, HOY)
    datos = rec.para_apuntar("receta", porridge["id"], 150)
    eq.registro.guardar([Alimento.desde_dict({**datos, "dia": AYER.isoformat()})], hoy=HOY, texto="ayer", sin_cambios=False)
    return {"avena": avena, "manzana": manzana, "huevo": huevo, "porridge": porridge}


def _propuestas(cosas: dict) -> list[dict]:
    return [{"clave": f"fijo:{cosas['avena']['id']}", "proteina": 13, "hidratos": 60, "grasa": 7},
            {"clave": f"fijo:{cosas['manzana']['id']}", "proteina": 0.3, "hidratos": 14, "grasa": 0.2},
            {"clave": f"fijo:{cosas['huevo']['id']}", "proteina": 6, "hidratos": 0.5, "grasa": 5},
            {"clave": f"receta:{cosas['porridge']['id']}:2", "proteina": 0.2, "hidratos": 4, "grasa": 0.1}]


def test_preparar_macros_sin_valores_lista_lo_que_falta_y_no_escribe(eq):  # R2
    cosas = _sin_macros(eq)
    antes = _tablas(eq.db)
    texto = eq.llamar("preparar_macros")
    for clave in (f"fijo:{cosas['avena']['id']}", f"fijo:{cosas['huevo']['id']}", f"receta:{cosas['porridge']['id']}:2"):
        assert clave in texto, texto
    assert "canela casera" in texto and "Porridge" in texto and "por unidad" in texto
    assert not HUELLA.search(texto)
    assert _tablas(eq.db) == antes and eq.mensajes_pc() == []


def test_preparar_macros_con_valores_da_tabla_y_huella_sin_escribir(eq):  # R2 · C-343
    cosas = _sin_macros(eq)
    antes = _tablas(eq.db)
    texto = eq.llamar("preparar_macros", {"propuestas": _propuestas(cosas)})
    huella = eq.huella(texto)
    assert "avena inventada" in texto and "no está guardado" in texto
    assert eq.huella(eq.llamar("preparar_macros", {"propuestas": _propuestas(cosas)})) == huella
    assert _tablas(eq.db) == antes and eq.mensajes_pc() == []


def test_guardar_macros_con_la_huella_guarda_solo_lo_aceptado_como_estimado(eq):  # R2 · C-343
    cosas = _sin_macros(eq)
    apuntado_antes = _apuntados(eq.db)
    texto = eq.llamar("preparar_macros", {"propuestas": _propuestas(cosas)})
    hecho = eq.llamar("guardar_macros", {"papel": "nutricionista", "propuestas": _propuestas(cosas),
                                         "huella": eq.huella(texto), "rechazar": [f"fijo:{cosas['huevo']['id']}"]})
    assert hecho.startswith("Hecho"), hecho
    fijos = {f["nombre"]: f for f in eq.recetario.fijos()}
    assert (fijos["avena inventada"]["proteina"], fijos["avena inventada"]["macros_origen"]) == (13, "estimado")
    assert fijos["manzana inventada"]["macros_origen"] == "estimado"
    assert (fijos["huevo inventado"]["proteina"], fijos["huevo inventado"]["macros_origen"]) == (None, None)
    v = eq.recetario.receta(cosas["porridge"]["id"])["en_uso"]  # the recipe in use, recomputed
    assert v["macros_total"] == {"proteina_g": 5.7, "hidratos_g": 42.0, "grasa_g": 3.1}
    assert v["kcal_total"] == cosas["porridge"]["en_uso"]["kcal_total"]  # its kcal do not change
    assert _apuntados(eq.db) == apuntado_antes  # what was logged yesterday does not change
    (mensaje,) = eq.mensajes_pc()
    assert mensaje["papel"] == "nutricionista" and "Hecho" in mensaje["texto"]


def test_guardar_macros_con_otra_huella_no_guarda_nada(eq):  # R2
    cosas = _sin_macros(eq)
    texto = eq.llamar("preparar_macros", {"propuestas": _propuestas(cosas)})
    eq.recetario.cambiar_fijo(cosas["avena"]["id"], {"nombre": "avena inventada", "kcal": 380, "por": "100g"})
    antes = _tablas(eq.db)
    respuesta = eq.llamar("guardar_macros", {"papel": "nutricionista", "propuestas": _propuestas(cosas),
                                             "huella": eq.huella(texto)})
    assert not respuesta.startswith("Hecho") and "cambiado" in respuesta
    assert _tablas(eq.db) == antes and eq.mensajes_pc() == []
    otra = eq.llamar("guardar_macros", {"papel": "nutricionista", "propuestas": _propuestas(cosas),
                                        "huella": "0" * 12})
    assert not otra.startswith("Hecho") and _tablas(eq.db) == antes


@pytest.mark.parametrize("cambio, motivo", [
    ({"proteina": 50, "hidratos": 45, "grasa": 10}, "100 g"),
    ({"proteina": -2, "hidratos": 60, "grasa": 7}, "negativ"),
])
def test_propuesta_imposible_se_rechaza_con_el_porque(eq, cambio, motivo):  # R3
    cosas = _sin_macros(eq)
    propuestas = _propuestas(cosas)
    propuestas[0] = {**propuestas[0], **cambio}
    antes = _tablas(eq.db)
    texto = eq.llamar("preparar_macros", {"propuestas": propuestas})
    assert motivo in texto and "avena inventada" in texto and not HUELLA.search(texto)
    assert _tablas(eq.db) == antes


def test_propuesta_que_no_cuadra_con_sus_kcal_avisa_pero_no_bloquea(eq):  # R3
    cosas = _sin_macros(eq)
    propuestas = _propuestas(cosas)
    propuestas[1] = {**propuestas[1], "hidratos": 30}  # 4·0.3 + 4·30 + 9·0.2 = 123 kcal for 52: far off
    texto = eq.llamar("preparar_macros", {"propuestas": propuestas})
    linea = next(x for x in texto.splitlines() if "manzana inventada" in x)
    assert "no cuadra con sus kcal" in linea
    assert "no cuadra" not in next(x for x in texto.splitlines() if "avena inventada" in x)
    hecho = eq.llamar("guardar_macros", {"papel": "nutricionista", "propuestas": propuestas,
                                         "huella": eq.huella(texto)})
    assert hecho.startswith("Hecho")


def test_relleno_no_toca_lo_que_ya_tiene_macros(eq):  # R2: only what is missing; «de etiqueta» stays
    cosas = _sin_macros(eq)
    eq.recetario.cambiar_fijo(cosas["avena"]["id"], {"nombre": "avena inventada", "kcal": 370, "por": "100g",
                                                     "proteina": 13.5, "hidratos": 58, "grasa": 7})
    texto = eq.llamar("preparar_macros")
    assert f"fijo:{cosas['avena']['id']}" not in texto
    respuesta = eq.llamar("preparar_macros", {"propuestas": _propuestas(cosas)})
    assert not HUELLA.search(respuesta) and "avena inventada" in respuesta


# -- R4 · R-271, C-342 (load-bearing): logging from a saved thing --------------------------------------------------

class App:
    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "data.sqlite3"
        ajustes = Ajustes(self.db)
        ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "objetivo_pct": -10}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.cliente = TestClient(crear_app(self.registro, None, ajustes, hoy=lambda: HOY))
        self.recetario = Recetario(self.db)

    def ok(self, ruta: str, cuerpo: dict) -> dict:
        r = self.cliente.post(ruta, json=cuerpo)
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def app(tmp_path) -> App:
    return App(tmp_path)


def test_apuntar_desde_la_app_lleva_los_macros_de_la_receta(app):  # R4 · C-342 (portante)
    lentejas = _lentejas(app.recetario)
    alimento = app.ok("/api/alimento", {"nombre": "lo que diga la página", "cantidad": 300, "unidad": "g", "kcal": 1,
                                        "dia": HOY.isoformat(), "guardado": {"tipo": "receta", "id": lentejas["id"]}})
    a = alimento["alimento"]
    assert a["kcal"] == round(300 * 127.5 / 100, 1) and a["proteina_g"] == 27
    assert (a["hidratos_g"], a["grasa_g"]) == (54, 1.8)
    desde_recetas = app.ok(f"/api/recetas/{lentejas['id']}/apuntar", {"cantidad": 300})["alimento"]
    assert desde_recetas["proteina_g"] == 27


@pytest.mark.parametrize("como", ["nombre", "id"])
def test_apuntar_por_el_equipo_lleva_los_macros_de_la_receta(eq, como):  # R4 · C-342 (portante)
    lentejas = _lentejas(eq.recetario)
    alimento = {"nombre": "Lentejas de la abuela", "cantidad": 300, "unidad": "g", "kcal": 999}
    if como == "id":
        alimento = {**alimento, "nombre": "lentejas", "guardado": {"tipo": "receta", "id": lentejas["id"]}}
    hecho = eq.llamar("apuntar_comida", {"papel": "nutricionista", "dia": HOY.isoformat(), "alimentos": [alimento]})
    assert hecho.startswith("Hecho"), hecho
    (fila,) = eq.registro.apuntes_del_dia(HOY)
    assert (fila["proteina_g"], fila["hidratos_g"], fila["grasa_g"]) == (27, 54, 1.8)


def test_apuntar_por_el_equipo_algo_no_guardado_lleva_sus_macros_estimados(eq):  # R6 · C-349
    hecho = eq.llamar("apuntar_comida", {"papel": "nutricionista", "dia": HOY.isoformat(), "alimentos": [
        {"nombre": "bocadillo de tortilla", "cantidad": 1, "unidad": "unidad", "kcal": 520, "proteina_g": 20,
         "hidratos_g": 55, "grasa_g": 24, "estimado": True}]})
    assert hecho.startswith("Hecho"), hecho
    (fila,) = eq.registro.apuntes_del_dia(HOY)
    assert (fila["proteina_g"], fila["hidratos_g"], fila["grasa_g"], fila["estimado"]) == (20, 55, 24, True)


def test_descripciones_del_equipo_piden_y_ensenan_macros(eq):  # R6, R2
    herramientas = {t.name: t for t in asyncio.run(eq.server.list_tools())}
    comida = herramientas["apuntar_comida"].description
    assert "proteína" in comida.lower() or "protein" in comida.lower()
    assert re.search(r"estimate[^.]*(protein|macros)", comida, re.I), comida
    assert "macros" in herramientas["get_recetas"].description.lower()
    assert herramientas["preparar_macros"].annotations.read_only_hint is True
    assert herramientas["guardar_macros"].annotations.read_only_hint is False


def test_get_recetas_ensena_los_macros(eq):  # R2: the team sees them
    import json
    _lentejas(eq.recetario)
    datos = json.loads(eq.llamar("get_recetas"))
    (receta,) = datos["recetas"]
    assert receta["macros_100g"] == {"proteina_g": 9.0, "hidratos_g": 18.0, "grasa_g": 0.6}
    (lenteja,) = [f for f in datos["ingredientes"] if f["nombre"] == "lenteja seca inventada"]
    assert (lenteja["proteina"], lenteja["macros_origen"]) == (24, "etiqueta")


# -- R5 · R-271: changing the quantity -----------------------------------------------------------------------------

def test_cambiar_cantidad_en_la_app_reescala_los_macros(app):  # R5
    lentejas = _lentejas(app.recetario)
    a = app.ok("/api/alimento", {"nombre": "x", "cantidad": 200, "unidad": "g", "kcal": 1, "dia": HOY.isoformat(),
                                 "guardado": {"tipo": "receta", "id": lentejas["id"]}})["alimento"]
    assert a["proteina_g"] == 18
    # by hand, as the Día card sends it: the page scales the kcal, the server the macros
    cambiado = app.ok(f"/api/alimento/{a['id']}", {"nombre": a["nombre"], "cantidad": 300, "unidad": "g",
                                                   "kcal": round(a["kcal"] * 1.5, 1), "dia": HOY.isoformat()})
    c = cambiado["alimento"]
    assert (c["proteina_g"], c["hidratos_g"], c["grasa_g"]) == (27, 54, 1.8)
    # chosen again from the saved ones (unit 074): the saved one's
    otra = app.ok(f"/api/alimento/{a['id']}", {"nombre": "x", "cantidad": 100, "unidad": "g", "kcal": 1,
                                               "dia": HOY.isoformat(),
                                               "guardado": {"tipo": "receta", "id": lentejas["id"]}})["alimento"]
    assert otra["proteina_g"] == 9


def test_cambiar_por_otro_alimento_en_la_app_toma_los_del_nuevo(app):  # R5
    _lentejas(app.recetario)
    avena = app.recetario.crear_fijo(AVENA)
    (a,) = app.registro.guardar([Alimento.desde_dict({"nombre": "yogur inventado", "cantidad": 125, "unidad": "g",
                                                       "kcal": 80, "proteina_g": 5, "hidratos_g": 6, "grasa_g": 3,
                                                       "dia": HOY.isoformat()})], hoy=HOY, texto="", sin_cambios=False)
    c = app.ok(f"/api/alimento/{a['id']}", {"nombre": "x", "cantidad": 50, "unidad": "g", "kcal": 1,
                                            "dia": HOY.isoformat(), "guardado": {"tipo": "fijo", "id": avena["id"]}})
    assert (c["alimento"]["proteina_g"], c["alimento"]["hidratos_g"]) == (6.5, 30)
    a2 = app.registro.guardar([Alimento.desde_dict({"nombre": "yogur inventado", "cantidad": 125, "unidad": "g",
                                                     "kcal": 80, "proteina_g": 5, "hidratos_g": 6, "grasa_g": 3,
                                                     "dia": HOY.isoformat()})], hoy=HOY, texto="", sin_cambios=False)[0]
    a_mano = app.ok(f"/api/alimento/{a2['id']}", {"nombre": "kéfir inventado", "cantidad": 125, "unidad": "g",
                                                  "kcal": 90, "dia": HOY.isoformat()})["alimento"]
    assert all(a_mano[k] is None for k in MACROS)  # another food without macros: not the yogurt's


def test_cambiar_cantidad_por_el_equipo_reescala_los_macros(eq):  # R5
    (a,) = eq.registro.guardar([Alimento.desde_dict({"nombre": "yogur inventado", "cantidad": 200, "unidad": "g",
                                                      "kcal": 120, "proteina_g": 8, "hidratos_g": 10, "grasa_g": 4,
                                                      "estimado": True, "dia": HOY.isoformat()})],
                               hoy=HOY, texto="", sin_cambios=False)
    cambio = {"accion": "cambiar_alimento", "objeto": "yogur inventado", "dia": HOY.isoformat(), "cantidad": 300,
              "unidad": "g"}
    texto = eq.llamar("preparar_cambio", {"cambio": cambio})
    hecho = eq.llamar("hacer_cambio", {"papel": "nutricionista", "cambio": cambio, "huella": eq.huella(texto)})
    assert hecho.startswith("Hecho"), hecho
    fila = eq.registro.alimento(a["id"])
    assert (fila["kcal"], fila["proteina_g"], fila["hidratos_g"], fila["grasa_g"]) == (180, 12, 15, 6)


def test_cambiar_por_otro_guardado_por_el_equipo_toma_los_del_nuevo(eq):  # R5
    _lentejas(eq.recetario)
    (a,) = eq.registro.guardar([Alimento.desde_dict({"nombre": "yogur inventado", "cantidad": 200, "unidad": "g",
                                                      "kcal": 120, "proteina_g": 8, "hidratos_g": 10, "grasa_g": 4,
                                                      "dia": HOY.isoformat()})], hoy=HOY, texto="", sin_cambios=False)
    cambio = {"accion": "cambiar_alimento", "objeto": "yogur inventado", "dia": HOY.isoformat(),
              "texto": "Lentejas de la abuela", "cantidad": 300, "unidad": "g"}
    texto = eq.llamar("preparar_cambio", {"cambio": cambio})
    hecho = eq.llamar("hacer_cambio", {"papel": "nutricionista", "cambio": cambio, "huella": eq.huella(texto)})
    assert hecho.startswith("Hecho"), hecho
    fila = eq.registro.alimento(a["id"])
    assert (fila["nombre"], fila["proteina_g"], fila["hidratos_g"]) == ("Lentejas de la abuela", 27, 54)


# -- R-270, R-271: what was logged before does not change -----------------------------------------------------------

def test_cambiar_un_fijo_no_cambia_lo_apuntado(app):  # R7 · C-345: «lo apuntado ayer no cambia»
    yogur = app.recetario.crear_fijo({"nombre": "Yougurt 0.0", "kcal": 50, "por": "100g"})
    app.recetario.guardar_macros([{"clave": f"fijo:{yogur['id']}", "proteina": 8, "hidratos": 4, "grasa": 0.1}])
    ayer = app.ok("/api/alimento", {"nombre": "x", "cantidad": 125, "unidad": "g", "kcal": 1,
                                    "dia": AYER.isoformat(), "guardado": {"tipo": "fijo", "id": yogur["id"]}})
    assert ayer["alimento"]["proteina_g"] == 10
    antes = _apuntados(app.db)
    app.ok(f"/api/fijos/{yogur['id']}", {"nombre": "Yougurt 0.0", "kcal": 50, "por": "100g", "proteina": 10,
                                         "hidratos": 4, "grasa": 0.1})
    assert _apuntados(app.db) == antes
    hoy = app.ok("/api/alimento", {"nombre": "x", "cantidad": 125, "unidad": "g", "kcal": 1, "dia": HOY.isoformat(),
                                   "guardado": {"tipo": "fijo", "id": yogur["id"]}})
    assert hoy["alimento"]["proteina_g"] == 12.5  # what is logged after uses the new value
