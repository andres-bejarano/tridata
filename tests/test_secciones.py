"""Unit 035: the sections of the day (desayuno, media mañana, comida, merienda, cena).

`RegistroComidas` over a temporary SQLite and the Starlette TestClient; the Claude interpreter is a
`MagicMock` (no API). Every value is invented.
Plans v7: `apuntar-comida`, flow `ordenar-comida-secciones`, REC-21 (R-121..R-126, C-162..C-168), G-51, G-52,
S-42..S-45. R1, R3, R4, R7, R8 and R9 of the unit.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.ia import AlimentoInterpretado, Interpretacion
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas
from tridata.nutricion.recetas import Recetario
from tridata.web import crear_app

HOY = date(2026, 10, 21)
AYER = HOY - timedelta(days=1)
SECCIONES = ("desayuno", "media_manana", "comida", "merienda", "cena")

# the table as it was before this unit (units 002..034): no `seccion`
_ESQUEMA_ANTERIOR = """
CREATE TABLE interpretacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT, hora TEXT NOT NULL, texto TEXT NOT NULL,
    confirmada_sin_cambios INTEGER NOT NULL
);
CREATE TABLE alimento_apuntado (
    id INTEGER PRIMARY KEY AUTOINCREMENT, dia TEXT NOT NULL, nombre TEXT NOT NULL, cantidad REAL, unidad TEXT,
    kcal REAL, proteina_g REAL, hidratos_g REAL, grasa_g REAL,
    estado TEXT NOT NULL CHECK (estado IN ('comido', 'planeado')), estimado INTEGER NOT NULL,
    supuestos TEXT NOT NULL, pendiente_cantidad INTEGER NOT NULL,
    interpretacion_id INTEGER REFERENCES interpretacion(id), creado TEXT NOT NULL,
    origen TEXT, provisional INTEGER NOT NULL DEFAULT 0
);
"""


def _pera(dia: date = HOY, **campos) -> Alimento:
    return Alimento(**{"nombre": "pera de prueba", "dia": dia, "cantidad": 120, "unidad": "g", "kcal": 60, **campos})


@pytest.fixture
def registro(tmp_path) -> RegistroComidas:
    return RegistroComidas(tmp_path / "data.sqlite3")


def _filas(db: Path) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT * FROM alimento_apuntado ORDER BY id").fetchall()


# -- R7 · R-126, C-166: an old store gets the column, everything «Sin sección», same total -------

def test_base_vieja_recibe_la_columna_sin_seccion_y_el_total_igual(tmp_path):  # R7
    db = tmp_path / "data.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.executescript(_ESQUEMA_ANTERIOR)
        for nombre, kcal in (("avena de prueba", 228), ("arroz de prueba", 532), ("yogur de prueba", 77)):
            conn.execute("INSERT INTO alimento_apuntado (dia, nombre, cantidad, unidad, kcal, estado, estimado, "
                         "supuestos, pendiente_cantidad, creado) VALUES (?, ?, 100, 'g', ?, 'comido', 0, '[]', 0, "
                         "'2026-10-20T10:00:00')", (AYER.isoformat(), nombre, kcal))
    registro = RegistroComidas(db)
    with sqlite3.connect(db) as conn:
        columnas = {f[1] for f in conn.execute("PRAGMA table_info(alimento_apuntado)")}
    assert "seccion" in columnas
    apuntes = registro.apuntes_del_dia(AYER)
    assert [a["seccion"] for a in apuntes] == [None, None, None]
    assert sum(a["kcal"] for a in apuntes) == 837
    RegistroComidas(db)  # opening it again does not add it twice
    assert [a["seccion"] for a in registro.apuntes_del_dia(AYER)] == [None, None, None]


def test_dia_viejo_se_puede_ordenar(tmp_path):  # R7 · C-166
    db = tmp_path / "data.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.executescript(_ESQUEMA_ANTERIOR)
        conn.execute("INSERT INTO alimento_apuntado (dia, nombre, cantidad, unidad, kcal, estado, estimado, "
                     "supuestos, pendiente_cantidad, creado) VALUES (?, 'avena de prueba', 60, 'g', 228, 'comido', "
                     "0, '[]', 0, '2026-10-20T10:00:00')", (AYER.isoformat(),))
    registro = RegistroComidas(db)
    movido = registro.mover_seccion(1, "desayuno")
    assert movido["seccion"] == "desayuno"
    assert movido["kcal"] == 228


# -- R1 · R-121, G-51, C-162: every way of logging leaves it without a section --------------------

def test_guardar_entra_sin_seccion(registro):  # R1 (dictation and the assistant confirm with `guardar`)
    assert registro.guardar([_pera()], hoy=HOY, texto="una pera", sin_cambios=True)[0]["seccion"] is None


def test_anadir_a_mano_entra_sin_seccion(registro):  # R1
    assert registro.anadir(_pera(), hoy=HOY)["seccion"] is None


def test_importar_entra_sin_seccion(registro):  # R1
    registro.importar_dia(AYER, [_pera(AYER)], hoy=HOY)
    assert [a["seccion"] for a in registro.apuntes_del_dia(AYER)] == [None]


# -- R3, R4 · R-123, R-124, G-52: moving keeps every number --------------------------------------

@pytest.mark.parametrize("seccion", [*SECCIONES, None])
def test_mover_seccion_guarda_solo_la_seccion(registro, seccion):  # R3, R4
    antes = registro.anadir(_pera(), hoy=HOY)
    movido = registro.mover_seccion(antes["id"], "cena" if seccion is None else seccion)
    if seccion is None:
        movido = registro.mover_seccion(antes["id"], None)
    assert movido["seccion"] == seccion
    assert {k: v for k, v in movido.items() if k != "seccion"} == {k: v for k, v in antes.items() if k != "seccion"}
    assert registro.alimento(antes["id"])["seccion"] == seccion


def test_mover_un_planeado_sigue_planeado(registro):  # R4 · C-168
    planeado = registro.anadir(_pera(HOY + timedelta(days=1)), hoy=HOY)
    assert planeado["estado"] == "planeado"
    movido = registro.mover_seccion(planeado["id"], "merienda")
    assert movido["estado"] == "planeado"
    assert movido["kcal"] == 60


@pytest.mark.parametrize("mala", ["brunch", "", "Desayuno", 3, True, ["cena"]])
def test_seccion_que_no_es_una_de_las_cinco_no_cambia_nada(registro, tmp_path, mala):  # R9
    a = registro.anadir(_pera(), hoy=HOY)
    registro.mover_seccion(a["id"], "comida")
    antes = _filas(tmp_path / "data.sqlite3")
    with pytest.raises(ValueError, match="sección"):
        registro.mover_seccion(a["id"], mala)
    assert _filas(tmp_path / "data.sqlite3") == antes


def test_alimento_que_no_existe_no_cambia_nada(registro, tmp_path):  # R9
    registro.anadir(_pera(), hoy=HOY)
    antes = _filas(tmp_path / "data.sqlite3")
    with pytest.raises(KeyError):
        registro.mover_seccion(9999, "desayuno")
    assert _filas(tmp_path / "data.sqlite3") == antes


# -- R8: correcting a food in its card keeps its section -----------------------------------------

def test_cambiar_conserva_la_seccion(registro):  # R8
    a = registro.anadir(_pera(nombre="avena de prueba", cantidad=50, kcal=190), hoy=HOY)
    registro.mover_seccion(a["id"], "desayuno")
    cambiado = registro.cambiar(a["id"], {"nombre": "avena de prueba", "cantidad": 60, "unidad": "g", "kcal": 228},
                                hoy=HOY)
    assert cambiado["seccion"] == "desayuno"
    assert cambiado["cantidad"] == 60


def test_marcar_comido_conserva_la_seccion(registro):  # R8
    planeado = registro.guardar([_pera()], hoy=AYER, texto="pera", sin_cambios=True)[0]
    registro.mover_seccion(planeado["id"], "merienda")
    assert registro.marcar_comido(planeado["id"], hoy=HOY)["seccion"] == "merienda"


# -- the web route: POST /api/alimento/N/seccion and /api/dia -------------------------------------

class Entorno:
    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.registro = RegistroComidas(self.db)
        ajustes = Ajustes(self.db)
        ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)  # 2,000 kcal of expenditure
        self.recetario = Recetario(self.db)
        self.interprete = MagicMock()
        self.cliente = TestClient(crear_app(self.registro, self.interprete, ajustes, hoy=lambda: HOY,
                                            recetario=self.recetario))

    def dia(self, fecha: date = HOY) -> dict:
        r = self.cliente.get(f"/api/dia?fecha={fecha.isoformat()}")
        assert r.status_code == 200, r.text
        return r.json()

    def seccion(self, alimento_id: int, cuerpo):
        return self.cliente.post(f"/api/alimento/{alimento_id}/seccion", json=cuerpo)


@pytest.fixture
def entorno(tmp_path) -> Entorno:
    return Entorno(tmp_path)


def _dia_de_1240(entorno: Entorno) -> list[dict]:
    """1,240 kcal eaten today, and a dinner of 400 planned yesterday for today."""
    comidos = [entorno.registro.anadir(_pera(nombre=n, kcal=k), hoy=HOY)
               for n, k in (("avena de prueba", 228), ("arroz de prueba", 532), ("pollo de prueba", 480))]
    cena = entorno.registro.guardar([_pera(nombre="cena de prueba", kcal=400)], hoy=AYER, texto="cena",
                                    sin_cambios=True)[0]
    return [*comidos, cena]


def _cuentas(dia: dict) -> tuple:
    return (dia["total_kcal"], dia["margenes"], dia["planeado_kcal"], dia["con_planeado"], dia["barra"],
            [(a["id"], a["kcal"], a["cantidad"], a["estado"]) for a in dia["alimentos"]])


def test_ruta_de_seccion_guarda_y_devuelve_el_dia_con_las_mismas_cuentas(entorno):  # R3, R4 · C-164, C-168
    avena, _, _, cena = _dia_de_1240(entorno)
    antes = entorno.dia()
    assert antes["total_kcal"] == 1240
    r = entorno.seccion(avena["id"], {"seccion": "desayuno"})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["alimento"]["seccion"] == "desayuno"
    assert cuerpo["dia"]["fecha"] == HOY.isoformat()
    assert _cuentas(cuerpo["dia"]) == _cuentas(antes)
    assert entorno.seccion(cena["id"], {"seccion": "cena"}).status_code == 200
    despues = entorno.dia()  # a reload
    assert {a["nombre"]: a["seccion"] for a in despues["alimentos"]} == {
        "avena de prueba": "desayuno", "arroz de prueba": None, "pollo de prueba": None, "cena de prueba": "cena"}
    assert next(a for a in despues["alimentos"] if a["nombre"] == "cena de prueba")["estado"] == "planeado"
    assert _cuentas(despues) == _cuentas(antes)


def test_ruta_de_seccion_vuelve_a_sin_seccion(entorno):  # R3 · C-164
    avena = _dia_de_1240(entorno)[0]
    entorno.seccion(avena["id"], {"seccion": "comida"})
    r = entorno.seccion(avena["id"], {"seccion": None})
    assert r.status_code == 200
    assert next(a for a in entorno.dia()["alimentos"] if a["id"] == avena["id"])["seccion"] is None


@pytest.mark.parametrize("cuerpo", [{"seccion": "brunch"}, {"seccion": ""}, {"seccion": 1}, {}, {"otra": "cena"}])
def test_ruta_de_seccion_mala_es_422_y_no_cambia_nada(entorno, cuerpo):  # R9
    avena = _dia_de_1240(entorno)[0]
    entorno.seccion(avena["id"], {"seccion": "desayuno"})
    antes = _filas(entorno.db)
    r = entorno.seccion(avena["id"], cuerpo)
    assert r.status_code == 422
    assert r.json()["error"].startswith("No se ha cambiado nada: ")
    assert _filas(entorno.db) == antes


def test_ruta_de_seccion_de_un_alimento_que_no_existe(entorno):  # R9
    _dia_de_1240(entorno)
    antes = _filas(entorno.db)
    r = entorno.seccion(9999, {"seccion": "cena"})
    assert r.status_code == 404
    assert r.json()["error"].startswith("No se ha cambiado nada: ")
    assert _filas(entorno.db) == antes


def test_api_dia_trae_la_seccion_de_cada_alimento(entorno):  # R6, R7
    _dia_de_1240(entorno)
    assert [a["seccion"] for a in entorno.dia()["alimentos"]] == [None, None, None, None]


def test_apuntar_por_la_web_entra_sin_seccion_aunque_la_traiga(entorno):  # R1 · C-162, G-51
    # by hand: a `seccion` in the body is ignored
    r = entorno.cliente.post("/api/alimento", json={"nombre": "pera de prueba", "cantidad": 120, "unidad": "g",
                                                    "kcal": 60, "dia": HOY.isoformat(), "seccion": "cena"})
    assert r.status_code == 200, r.text
    assert r.json()["alimento"]["seccion"] is None
    # from Recetas: a saved ingredient chosen
    fijo = entorno.recetario.crear_fijo({"nombre": "plátano de prueba", "kcal": 89, "por": "100g"})
    r = entorno.cliente.post("/api/alimento", json={"nombre": "plátano de prueba", "cantidad": 100, "unidad": "g",
                                                    "dia": HOY.isoformat(),
                                                    "guardado": {"tipo": "fijo", "id": fijo["id"]}})
    assert r.status_code == 200, r.text
    assert r.json()["alimento"]["seccion"] is None
    # dictated / written: confirmed after the review
    entorno.interprete.interpretar.return_value = Interpretacion(alimentos=[AlimentoInterpretado(
        nombre="manzana de prueba", cantidad=150, unidad="g", dia=HOY, kcal=78, proteina_g=None, hidratos_g=None,
        grasa_g=None, estimado=True, dudoso=False, supuestos=[])], peso=None, objetivo_dia=None, dudosas=[])
    piezas = entorno.cliente.post("/api/interpretar", json={"texto": "una manzana"}).json()
    r = entorno.cliente.post("/api/confirmar", json={"texto": "una manzana", "alimentos": piezas["alimentos"]})
    assert r.status_code == 200, r.text
    assert {a["nombre"]: a["seccion"] for a in entorno.dia()["alimentos"]} == {
        "pera de prueba": None, "plátano de prueba": None, "manzana de prueba": None}


def test_corregir_en_la_ficha_no_cambia_la_seccion(entorno):  # R8
    avena = _dia_de_1240(entorno)[0]
    entorno.seccion(avena["id"], {"seccion": "desayuno"})
    r = entorno.cliente.post(f"/api/alimento/{avena['id']}", json={"nombre": "avena de prueba", "cantidad": 80,
                                                                   "unidad": "g", "kcal": 300})
    assert r.status_code == 200, r.text
    assert r.json()["alimento"]["seccion"] == "desayuno"


def test_comido_por_la_web_conserva_la_seccion(entorno):  # R8
    cena = _dia_de_1240(entorno)[3]
    entorno.seccion(cena["id"], {"seccion": "cena"})
    r = entorno.cliente.post(f"/api/alimento/{cena['id']}/comido", json={})
    assert r.status_code == 200, r.text
    assert r.json()["alimento"] == {**r.json()["alimento"], "estado": "comido", "seccion": "cena"}
