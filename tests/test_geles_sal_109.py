"""Unit 109 (REC-52 of `apuntar-comida`): gels with caffeine and salt tablets in «Suplementación», with saved
products — `nutricion/suplementacion.py`.

Unit level: the summary of the day (caffeine by origin, the last dose with the gels with an hour, «en carrera» by
minute, sodium), the saved products (their values copied × the number, never changed afterwards), the checks
(hour or minute, numbers that are not finite) and the one-off rebuild of the table of a store of the 082.
Every value is invented. Plans: `apuntar-comida` R-317..R-323, G-68..G-70, S-105..S-109, C-404..C-412.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from tridata.nutricion import Alimento, RegistroComidas
from tridata.nutricion.suplementacion import Suplementacion, describir, leer_todo, resumen_dia

HOY = date(2026, 11, 24)  # a Tuesday
DOMINGO = date(2026, 11, 29)  # a day that has not come yet
AHORA = datetime(2026, 11, 24, 16, 40)


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "data.sqlite3"


@pytest.fixture
def registro(db) -> RegistroComidas:
    registro = RegistroComidas(db)
    registro.guardar([Alimento(nombre="arroz inventado", dia=HOY, cantidad=300, unidad="g", kcal=1200)], hoy=HOY,
                     texto="arroz", sin_cambios=True)
    return registro


@pytest.fixture
def sup(db, registro) -> Suplementacion:
    return Suplementacion(db)


def _alimentos(db: Path) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT * FROM alimento_apuntado ORDER BY id").fetchall()


def _gel(**extra) -> dict:
    return {"que": "gel", "producto": "Gel cola de prueba", "mg": 100, **extra}


# -- R1 (the load-bearing one): a gel is a dose of its day, also a day to come, without kcal --------------------------

def test_geles_de_un_dia_futuro_sin_tocar_kcal(sup, registro, db):  # R1 · R-317, G-68, C-404
    antes = _alimentos(db)
    hechas = sup.apuntar_varias(DOMINGO, [_gel(minuto=60), _gel(minuto=90)], hoy=HOY, ahora=AHORA)
    assert [(t["que"], t["minuto"], t["mg"], t["cantidad"]) for t in hechas] == [("gel", 60, 100, 1),
                                                                                 ("gel", 90, 100, 1)]
    r = sup.resumen(DOMINGO)
    assert r["cafeina_mg"] == 200
    assert [t["minuto"] for t in r["en_carrera"]] == [60, 90]
    assert all("kcal" not in t for t in hechas)
    assert _alimentos(db) == antes  # the foods of every day untouched
    assert registro.apuntes_del_dia(DOMINGO) == []
    assert sum(a["kcal"] for a in registro.apuntes_del_dia(HOY)) == 1200


def test_el_gel_por_numero_multiplica_los_mg(sup):  # R1: «cafeína total (mg × número)»
    toma = sup.apuntar(DOMINGO, _gel(cantidad=2, hora="09:30", sodio_mg=50), hoy=HOY, ahora=AHORA)
    assert (toma["cantidad"], toma["mg"], toma["sodio_mg"], toma["hora"], toma["minuto"]) == (2, 200, 100, "09:30",
                                                                                             None)
    assert toma["mg_estimado"] is False
    assert describir(toma) == "2 × Gel cola de prueba"


# -- R2, R4: the summary of the day --------------------------------------------------------------------------------------

def test_resumen_cafeina_por_origen_ultima_carrera_y_sodio(sup):  # R2, R4 · G-69, C-405, C-408
    sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    sup.apuntar(HOY, _gel(hora="11:15"), hoy=HOY, ahora=AHORA)
    sup.apuntar(HOY, {"que": "sal", "producto": "Sal de prueba", "sodio_mg": 215, "minuto": 75}, hoy=HOY, ahora=AHORA)
    r = sup.resumen(HOY)
    assert (r["cafeina_mg"], r["cafeina_cafe_mg"], r["cafeina_geles_mg"]) == (230, 130, 100)
    assert r["ultima"] == "11:15"
    assert r["sodio_mg"] == 215
    assert [(t["hora"], t["que"]) for t in r["tomas"]] == [("10:00", "cafe"), ("11:15", "gel")]
    assert [(t["minuto"], t["que"], t["mg"], t["sodio_mg"]) for t in r["en_carrera"]] == [(75, "sal", None, 215)]


def test_un_gel_solo_con_minuto_no_cuenta_para_la_ultima(sup):  # R2 · G-69
    sup.apuntar(HOY, {"que": "cafe", "forma": "expreso", "hora": "08:00", "mg": 70}, hoy=HOY, ahora=AHORA)
    sup.apuntar(HOY, _gel(minuto=60), hoy=HOY, ahora=AHORA)
    r = sup.resumen(HOY)
    assert (r["cafeina_mg"], r["ultima"]) == (170, "08:00")


def test_en_carrera_ordenado_por_minuto(sup):  # R2 · S-105
    for minuto in (90, 30, 60):
        sup.apuntar(DOMINGO, _gel(minuto=minuto), hoy=HOY, ahora=AHORA)
    assert [t["minuto"] for t in sup.resumen(DOMINGO)["en_carrera"]] == [30, 60, 90]
    assert sup.resumen(DOMINGO)["tomas"] == []


def test_sin_geles_el_resumen_es_el_de_siempre(sup):  # R2: «sin geles, como hoy»
    sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    r = sup.resumen(HOY)
    assert set(r) == {"tomas", "cafeina_mg", "ultima", "leche"}
    assert sup.resumen(DOMINGO) == {"tomas": [], "cafeina_mg": 0, "ultima": None, "leche": None}


def test_la_sal_lleva_sodio_sin_cafeina(sup):  # R4 · R-320, C-408
    toma = sup.apuntar(HOY, {"que": "sal", "sodio_mg": 215, "cantidad": 2, "hora": "12:00"}, hoy=HOY, ahora=AHORA)
    assert (toma["mg"], toma["sodio_mg"], toma["cantidad"]) == (None, 430, 2)
    assert describir(toma) == "2 × pastilla de sal"
    r = sup.resumen(HOY)
    assert (r["cafeina_mg"], r["sodio_mg"]) == (0, 430)
    assert "cafeina_geles_mg" not in r


def test_resumen_dia_es_puro_con_geles():  # R2: the same summary from plain data
    tomas = [{"id": 1, "que": "gel", "hora": None, "minuto": 60, "mg": 100, "sodio_mg": 50, "cantidad": 1,
              "producto": "Gel"},
             {"id": 2, "que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130, "solo": False}]
    r = resumen_dia(tomas, None)
    assert (r["cafeina_mg"], r["cafeina_cafe_mg"], r["cafeina_geles_mg"], r["sodio_mg"]) == (230, 130, 100, 50)
    assert [t["id"] for t in r["tomas"]] == [2] and [t["id"] for t in r["en_carrera"]] == [1]


# -- R3: saved products ----------------------------------------------------------------------------------------------

def test_producto_guardado_da_sus_valores_por_el_numero(sup):  # R3 · R-319, G-70, C-406
    gel = sup.guardar_producto({"nombre": "Gel 226 cola", "tipo": "gel", "mg_cafeina": 100, "sodio_mg": 50})
    assert (gel["nombre"], gel["tipo"], gel["mg_cafeina"], gel["sodio_mg"]) == ("Gel 226 cola", "gel", 100, 50)
    toma = sup.apuntar(HOY, {"que": "gel", "producto_id": gel["id"], "cantidad": 2, "hora": "9:30"}, hoy=HOY,
                       ahora=AHORA)
    assert (toma["producto"], toma["mg"], toma["sodio_mg"], toma["hora"]) == ("Gel 226 cola", 200, 100, "09:30")
    assert sup.productos() == [gel]


def test_el_producto_elegido_da_el_tipo(sup):  # R3: choosing a product is enough
    sal = sup.guardar_producto({"nombre": "Pastilla de prueba", "tipo": "sal", "sodio_mg": 215})
    toma = sup.apuntar(HOY, {"producto_id": sal["id"], "minuto": 75}, hoy=HOY, ahora=AHORA)
    assert (toma["que"], toma["sodio_mg"], toma["mg"]) == ("sal", 215, None)


def test_producto_por_nombre_sin_mg_usa_los_suyos(sup):  # R3, R6: the coach names it
    sup.guardar_producto({"nombre": "Gel 226 cola", "tipo": "gel", "mg_cafeina": 100, "sodio_mg": 50})
    toma = sup.apuntar(DOMINGO, {"que": "gel", "producto": "gel 226 COLA", "minuto": 60}, hoy=HOY, ahora=AHORA)
    assert (toma["producto"], toma["mg"], toma["sodio_mg"]) == ("Gel 226 cola", 100, 50)
    dicho = sup.apuntar(DOMINGO, {"que": "gel", "producto": "Gel 226 cola", "mg": 80, "minuto": 90}, hoy=HOY,
                        ahora=AHORA)
    assert dicho["mg"] == 80  # what he says wins


def test_cambiar_o_borrar_el_producto_no_cambia_las_tomas(sup):  # R3 · G-70, C-407
    gel = sup.guardar_producto({"nombre": "Gel 226 cola", "tipo": "gel", "mg_cafeina": 100, "sodio_mg": 50})
    antes = sup.apuntar(HOY, {"producto_id": gel["id"], "cantidad": 2, "hora": "09:30"}, hoy=HOY, ahora=AHORA)
    cambiado = sup.cambiar_producto(gel["id"], {"mg_cafeina": 75})
    assert (cambiado["mg_cafeina"], cambiado["sodio_mg"]) == (75, 50)
    assert sup.toma(antes["id"]) == antes
    despues = sup.apuntar(HOY, {"producto_id": gel["id"], "cantidad": 2, "hora": "10:30"}, hoy=HOY, ahora=AHORA)
    assert despues["mg"] == 150
    assert sup.borrar_producto(gel["id"])["id"] == gel["id"]
    assert sup.productos() == []
    assert sup.toma(antes["id"]) == antes and sup.toma(despues["id"]) == despues
    with pytest.raises(KeyError):
        sup.borrar_producto(gel["id"])


def test_nombre_repetido_no_se_guarda_dos_veces(sup):  # R3: «sin distinguir mayúsculas»
    sup.guardar_producto({"nombre": "Gel 226 cola", "tipo": "gel", "mg_cafeina": 100})
    with pytest.raises(ValueError, match="ya"):
        sup.guardar_producto({"nombre": "  GEL 226 Cola ", "tipo": "gel", "mg_cafeina": 90})
    otro = sup.guardar_producto({"nombre": "Gel naranja", "tipo": "gel", "mg_cafeina": 0})
    with pytest.raises(ValueError, match="ya"):
        sup.cambiar_producto(otro["id"], {"nombre": "gel 226 cola"})
    assert [p["nombre"] for p in sup.productos()] == ["Gel 226 cola", "Gel naranja"]


@pytest.mark.parametrize("datos, motivo", [
    ({"nombre": "", "tipo": "gel", "mg_cafeina": 100}, "nombre"),
    ({"nombre": "X", "tipo": "barrita", "mg_cafeina": 100}, "gel o sal"),
    ({"nombre": "X", "tipo": "gel", "mg_cafeina": "nan"}, "finito"),
    ({"nombre": "X", "tipo": "gel", "mg_cafeina": -1}, "negativ"),
    ({"nombre": "X", "tipo": "sal", "sodio_mg": float("inf")}, "finito"),
])
def test_producto_no_valido(sup, datos, motivo):  # R3, R7
    with pytest.raises(ValueError, match=motivo):
        sup.guardar_producto(datos)
    assert sup.productos() == []


# -- R5: changing a gel or a salt --------------------------------------------------------------------------------------

def test_cambiar_numero_minuto_y_mg_de_un_gel(sup):  # R5 · R-321
    toma = sup.apuntar(DOMINGO, _gel(minuto=60, sodio_mg=50), hoy=HOY, ahora=AHORA)
    tres = sup.cambiar(toma["id"], {"cantidad": 3})
    assert (tres["cantidad"], tres["mg"], tres["sodio_mg"]) == (3, 300, 150)  # the values per gel kept
    otro = sup.cambiar(toma["id"], {"mg": 80, "minuto": 45, "producto": "Gel limón de prueba"})
    assert (otro["mg"], otro["minuto"], otro["producto"]) == (240, 45, "Gel limón de prueba")
    con_hora = sup.cambiar(toma["id"], {"hora": "10:15", "minuto": None})
    assert (con_hora["hora"], con_hora["minuto"]) == ("10:15", None)
    with pytest.raises(ValueError, match="falta la hora o el minuto"):
        sup.cambiar(toma["id"], {"hora": None})
    assert sup.toma(toma["id"]) == con_hora


def test_cambiar_el_producto_de_una_toma(sup):  # R5: «cambia el producto»
    a = sup.guardar_producto({"nombre": "Gel A", "tipo": "gel", "mg_cafeina": 100, "sodio_mg": 50})
    b = sup.guardar_producto({"nombre": "Gel B", "tipo": "gel", "mg_cafeina": 40, "sodio_mg": 20})
    toma = sup.apuntar(HOY, {"producto_id": a["id"], "cantidad": 2, "hora": "10:00"}, hoy=HOY, ahora=AHORA)
    cambiada = sup.cambiar(toma["id"], {"producto_id": b["id"]})
    assert (cambiada["producto"], cambiada["mg"], cambiada["sodio_mg"], cambiada["cantidad"]) == ("Gel B", 80, 40, 2)


# -- R7: without hour or minute, numbers that are not finite ----------------------------------------------------------

@pytest.mark.parametrize("dia", [HOY, DOMINGO])
def test_gel_o_sal_sin_hora_ni_minuto_no_se_apunta(sup, dia):  # R7 · R-323, C-411 (also today: R-231 is coffee's)
    for datos in (_gel(), {"que": "sal", "sodio_mg": 215}):
        with pytest.raises(ValueError, match="falta la hora o el minuto"):
            sup.apuntar(dia, datos, hoy=HOY, ahora=AHORA)
    assert sup.tomas_del_dia(dia) == []


@pytest.mark.parametrize("malo", ["nan", "inf", "-inf", "1e400", float("nan"), float("inf")])
@pytest.mark.parametrize("datos, clave", [
    ({"que": "cafe", "forma": "doble", "hora": "10:00"}, "mg"),
    ({"que": "cafe", "forma": "italiana", "hora": "10:00"}, "gramos"),
    ({"que": "cafe", "forma": "soluble", "hora": "10:00", "mg": 60}, "cucharadas"),
    ({"que": "cafe", "forma": "italiana", "hora": "10:00", "mg": 60}, "fraccion"),
    ({"que": "gel", "minuto": 60}, "mg"),
    ({"que": "gel", "minuto": 60, "mg": 100}, "sodio_mg"),
    ({"que": "sal", "minuto": 60}, "sodio_mg"),
    ({"que": "gel", "mg": 100}, "minuto"),
    ({"que": "gel", "mg": 100, "minuto": 60}, "cantidad"),
])
def test_numeros_no_finitos_no_se_guardan(sup, datos, clave, malo):  # R7 · S-109, C-412, P-20261113-5e585faf
    with pytest.raises(ValueError, match="finito"):
        sup.apuntar(HOY, {**datos, clave: malo}, hoy=HOY, ahora=AHORA)
    assert sup.tomas_del_dia(HOY) == []


@pytest.mark.parametrize("datos, motivo", [
    (_gel(minuto=60, cantidad=0), "mayor que 0"),
    (_gel(minuto=60, cantidad=-2), "negativ"),
    (_gel(minuto=-5), "negativ"),
    (_gel(minuto=60, mg=-1), "negativ"),
    (_gel(minuto=60, sodio_mg=-1), "negativ"),
    (_gel(minuto=60.5), "entero"),
    ({"que": "gel", "minuto": 60}, "mg"),  # a gel without its mg and no saved product
    ({"que": "sal", "minuto": 60}, "sodio"),  # a salt without its sodium
    ({"producto_id": 999, "minuto": 60}, "producto"),
    ({"que": "te", "hora": "10:00"}, "café o B12"),
])
def test_gel_o_sal_no_validos(sup, datos, motivo):  # R7 · R-323, C-412
    with pytest.raises(ValueError, match=motivo):
        sup.apuntar(HOY, datos, hoy=HOY, ahora=AHORA)
    assert sup.tomas_del_dia(HOY) == []


def test_cambio_no_finito_no_cambia_nada(sup):  # R7 in a change too
    cafe = sup.apuntar(HOY, {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}, hoy=HOY, ahora=AHORA)
    gel = sup.apuntar(HOY, _gel(minuto=60), hoy=HOY, ahora=AHORA)
    for toma, malo in ((cafe, {"mg": "nan"}), (gel, {"cantidad": "1e400"}), (gel, {"minuto": float("inf")})):
        with pytest.raises(ValueError, match="finito"):
            sup.cambiar(toma["id"], malo)
    assert sup.toma(cafe["id"]) == cafe and sup.toma(gel["id"]) == gel


# -- R8: a store of the 082 opens as it was, once ---------------------------------------------------------------------

_ESQUEMA_082 = """
CREATE TABLE IF NOT EXISTS toma_suplemento (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dia TEXT NOT NULL,
    que TEXT NOT NULL CHECK (que IN ('cafe', 'b12')),
    forma TEXT,
    gramos REAL,
    fraccion REAL,
    cucharadas REAL,
    hora TEXT,
    mg REAL,
    mg_estimado INTEGER NOT NULL DEFAULT 0,
    solo INTEGER NOT NULL DEFAULT 0,
    dosis TEXT,
    creado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_toma_suplemento_dia ON toma_suplemento(dia);
CREATE TABLE IF NOT EXISTS hora_seccion (
    dia TEXT NOT NULL,
    seccion TEXT NOT NULL,
    hora TEXT NOT NULL,
    PRIMARY KEY (dia, seccion)
);
CREATE TABLE IF NOT EXISTS leche_cafes (
    dia TEXT PRIMARY KEY,
    alimento_id INTEGER NOT NULL
);
"""
_VIEJAS = "id, dia, que, forma, gramos, fraccion, cucharadas, hora, mg, mg_estimado, solo, dosis, creado"


def _base_082(db: Path, registro: RegistroComidas) -> None:
    """A store as the 082 left it: three coffees and a B12 (one deleted, so the sequence is ahead), milk, an hour."""
    (leche,) = registro.guardar([Alimento(nombre="leche de prueba", dia=HOY, cantidad=200, unidad="ml", kcal=92)],
                                hoy=HOY, texto="leche", sin_cambios=False)
    with sqlite3.connect(db) as conn:
        conn.executescript(_ESQUEMA_082)
        filas = [(HOY.isoformat(), "cafe", "doble", None, None, None, "10:00", 130, 1, 0, None),
                 (HOY.isoformat(), "cafe", "italiana", 20, 0.5, None, "17:30", 90, 0, 1, None),
                 (HOY.isoformat(), "b12", None, None, None, None, "09:00", None, 0, 0, "1000 µg"),
                 ((HOY - timedelta(days=1)).isoformat(), "cafe", "soluble", 2, None, 2, "08:00", 60, 1, 0, None)]
        for f in filas:
            conn.execute("INSERT INTO toma_suplemento (dia, que, forma, gramos, fraccion, cucharadas, hora, mg, "
                         "mg_estimado, solo, dosis, creado) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (*f, "2026-11-20T10:00:00"))
        conn.execute("DELETE FROM toma_suplemento WHERE id = 4")  # the sequence stays at 4
        conn.execute("INSERT INTO leche_cafes (dia, alimento_id) VALUES (?, ?)", (HOY.isoformat(), leche["id"]))
        conn.execute("INSERT INTO hora_seccion (dia, seccion, hora) VALUES (?, 'cena', '22:15')", (HOY.isoformat(),))


def _tomas_viejas(db: Path) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT {_VIEJAS} FROM toma_suplemento ORDER BY id").fetchall()  # noqa: S608


def _dump(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def test_una_base_de_la_082_abre_igual_y_una_vez(db):  # R8
    registro = RegistroComidas(db)
    registro.guardar([Alimento(nombre="pan inventado", dia=HOY, cantidad=60, unidad="g", kcal=150)], hoy=HOY,
                     texto="pan", sin_cambios=True, seccion="desayuno")
    _base_082(db, registro)
    tomas, alimentos = _tomas_viejas(db), _alimentos(db)
    tomas_de, _horas, leches = leer_todo(db)
    resumen_antes = resumen_dia(tomas_de[HOY.isoformat()], leches.get(HOY.isoformat()))
    assert [t[0] for t in tomas] == [1, 2, 3]

    sup = Suplementacion(db)
    assert _tomas_viejas(db) == tomas  # ids and rows identical
    assert _alimentos(db) == alimentos
    assert sup.resumen(HOY) == {**resumen_antes,
                                "tomas": [{k: v for k, v in t.items() if k != "dia"} for t in resumen_antes["tomas"]]}
    assert sup.resumen(HOY)["cafeina_mg"] == 220 and sup.resumen(HOY)["leche"]["repartida"] is True
    assert sup.horas_del_dia(HOY)["cena"]["hora"] == "22:15"
    with sqlite3.connect(db) as conn:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'toma_suplemento'").fetchone()[0]
        indices = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' "
                                              "AND tbl_name = 'toma_suplemento'")}
    assert "'gel'" in sql and "'sal'" in sql
    assert "idx_toma_suplemento_dia" in indices
    nueva = sup.apuntar(HOY, _gel(minuto=60), hoy=HOY, ahora=AHORA)
    assert nueva["id"] == 5  # the sequence of the 082 kept: a deleted id is never reused

    despues = _dump(db)
    Suplementacion(db)  # a second opening rebuilds nothing
    assert _dump(db) == despues


def test_una_base_de_hoy_vacia_abre_dos_veces_igual(db):  # R8
    Suplementacion(db)
    antes = _dump(db)
    Suplementacion(db)
    assert _dump(db) == antes


def test_leer_todo_sobre_una_base_de_la_082_sin_abrir(db):  # R8, R6: the context reads it before the app migrates
    registro = RegistroComidas(db)
    _base_082(db, registro)
    antes = _dump(db)
    tomas, _horas, leches = leer_todo(db)
    assert [t["que"] for t in tomas[HOY.isoformat()]] == ["cafe", "cafe", "b12"]
    assert all(t["minuto"] is None and t["producto"] is None for t in tomas[HOY.isoformat()])
    assert _dump(db) == antes  # read only
