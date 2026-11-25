"""Unit 112 (REC-55 of `apuntar-comida`, flow `apuntar-variante-y-postre`, R-340..R-344, G-75, S-115, S-116,
C-432..C-438): the variants of a saved recipe («Garbanzos de la abuela · sin chorizo») and the dessert inside its meal.

A variant keeps its own name, kcal/100 g and, if given, its macros per 100 g; it has no ingredients nor weighing. The
coach saves, changes or deletes it with Andrés's «sí» (`preparar_cambio` / `hacer_cambio`, actions `variante_nueva`,
`variante_cambiar`, `variante_borrar`) and logs it with `apuntar_comida` by its name or by `guardado: {tipo:
"variante", id}`: its kcal/100 g, never the recipe's, which does not change (G-75). A food marked as dessert stays in
its section, is shown apart at the end of it and adds up as always (S-116).

Integration through the functions of the coach over a temporary SQLite with the day fixed; unit tests of `Recetario`
and `RegistroComidas`; a store with the schema of main 24830b7 for the migration. Nothing talks to a paid service.
Every datum is invented.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from tridata.conversaciones.desde_pc import (apuntar_comida_desde_pc, contexto, hacer_desde_pc, montar_equipo,
                                             preparar_desde_pc, recetario_texto)
from tridata.nutricion import DESDE_SIEMPRE, Alimento, RegistroComidas
from tridata.nutricion.recetas import Recetario

HOY = date(2026, 11, 25)
AYER = HOY - timedelta(days=1)
AHORA = datetime(2026, 11, 25, 14, 0)
HUELLA = re.compile(r"Huella: ([0-9a-f]{12})")
RECETA = "Garbanzos de la abuela"
VARIANTE = "Garbanzos de la abuela · sin chorizo"


class Mundo:
    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.equipo = montar_equipo(self.db, store=None, token=tmp_path / "google-token.json", hoy=lambda: HOY,
                                    ahora=lambda: AHORA)
        a = self.equipo.asistente
        self.registro, self.recetario = a.registro, a.recetario
        a.ajustes.cambiar({"basal_kcal": 1900, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                          desde=DESDE_SIEMPRE)
        # 1.260 kcal in 1.000 g: 126 kcal/100 g, weighed (definitive)
        self.receta = self.recetario.crear_receta({"nombre": RECETA, "peso_neto_g": 1000, "ingredientes": [
            {"nombre": "garbanzo cocido de prueba", "cantidad": 1000, "unidad": "g", "kcal": 1260}]}, HOY)

    def preparar(self, cambio: dict) -> str:
        return preparar_desde_pc(self.equipo, cambio, "texto de prueba")

    def hacer(self, cambio: dict) -> str:
        texto = self.preparar(cambio)
        encontrada = HUELLA.search(texto)
        assert encontrada, texto
        return hacer_desde_pc(self.equipo, "nutricionista", cambio, encontrada.group(1), "texto de prueba")

    def apuntar(self, alimentos: list[dict], dia: date = HOY, seccion: str | None = "comida") -> str:
        return apuntar_comida_desde_pc(self.equipo, "nutricionista", dia.isoformat(), alimentos, "texto de prueba",
                                       seccion)

    def kcal_receta(self) -> float:
        return self.recetario.receta(self.receta["id"])["en_uso"]["kcal_100g"]

    def variantes(self) -> list[dict]:
        return self.recetario.receta(self.receta["id"])["variantes"]

    def volcado(self) -> list[str]:
        conn = sqlite3.connect(self.db)
        try:
            return [x for x in conn.iterdump() if "tarjeta_asistente" not in x and "conversacion" not in x
                    and "mensaje" not in x]
        finally:
            conn.close()

    def datos_contexto(self) -> dict:
        return json.loads(contexto(self.equipo).bloques[2])


@pytest.fixture
def mundo(tmp_path) -> Mundo:
    return Mundo(tmp_path)


NUEVA = {"accion": "variante_nueva", "objeto": "garbanzos de la abuela", "texto": "sin chorizo", "valor": 105,
         "valores": [7, 15, 2]}


def _con_variante(mundo: Mundo) -> dict:
    hecho = mundo.hacer(NUEVA)
    assert hecho.startswith("Hecho"), hecho
    (variante,) = mundo.variantes()
    return variante


# -- R1 · R-340, R-341, G-75, C-432, C-433 (portante): saved with the «sí», logged with ITS kcal ----------------

def test_variante_con_el_si_se_guarda_y_la_receta_sigue_igual(mundo):  # R1
    antes = mundo.volcado()
    texto = mundo.preparar(NUEVA)
    assert "sin chorizo" in texto and "105 kcal/100 g" in texto, texto
    assert "126 kcal/100 g" in texto, texto  # the card says the recipe stays as it is
    assert mundo.volcado() == antes  # preparing writes nothing
    assert mundo.variantes() == []
    hecho = mundo.hacer(NUEVA)
    assert hecho.startswith("Hecho"), hecho
    (variante,) = mundo.variantes()
    assert (variante["nombre"], variante["kcal_100g"]) == ("sin chorizo", 105)
    assert variante["macros_100g"] == {"proteina_g": 7, "hidratos_g": 15, "grasa_g": 2}
    assert mundo.kcal_receta() == 126  # G-75: the recipe does not change


def test_apuntar_la_variante_por_su_nombre_usa_sus_kcal(mundo):  # R1
    _con_variante(mundo)
    hecho = mundo.apuntar([{"nombre": VARIANTE, "cantidad": 350, "unidad": "g", "kcal": 999, "estimado": True}])
    assert hecho.startswith("Hecho"), hecho
    assert "368 kcal" in hecho and "de tu receta «Garbanzos de la abuela» · sin chorizo" in hecho, hecho
    (a,) = mundo.registro.apuntes_del_dia(HOY)
    assert a["nombre"] == VARIANTE and round(a["kcal"]) == 368 and a["estimado"] is False
    assert a["origen"] == "de tu receta «Garbanzos de la abuela» · sin chorizo"
    assert (a["proteina_g"], a["hidratos_g"], a["grasa_g"]) == (24.5, 52.5, 7)  # its macros, for 350 g
    assert mundo.kcal_receta() == 126


def test_apuntar_la_variante_por_guardado_usa_sus_kcal(mundo):  # R1
    variante = _con_variante(mundo)
    hecho = mundo.apuntar([{"nombre": "lo que diga el coach", "cantidad": 350, "unidad": "g",
                            "guardado": {"tipo": "variante", "id": variante["id"]}}])
    assert hecho.startswith("Hecho"), hecho
    (a,) = mundo.registro.apuntes_del_dia(HOY)
    assert a["nombre"] == VARIANTE and round(a["kcal"]) == 368
    assert a["origen"] == "de tu receta «Garbanzos de la abuela» · sin chorizo"


def test_apuntar_la_variante_sin_el_punto_tambien_la_encuentra(mundo):  # R1: «garbanzos de la abuela sin chorizo»
    _con_variante(mundo)
    mundo.apuntar([{"nombre": "garbanzos de la abuela sin chorizo", "cantidad": 350, "unidad": "g", "kcal": 1}])
    (a,) = mundo.registro.apuntes_del_dia(HOY)
    assert a["nombre"] == VARIANTE and round(a["kcal"]) == 368


def test_la_receta_sigue_apuntandose_con_las_suyas(mundo):  # R1 · G-75
    _con_variante(mundo)
    mundo.apuntar([{"nombre": RECETA, "cantidad": 350, "unidad": "g", "kcal": 1}])
    (a,) = mundo.registro.apuntes_del_dia(HOY)
    assert a["nombre"] == RECETA and a["kcal"] == 441


def test_variante_sin_macros_se_apunta_sin_macros(mundo):  # R1: «con macros de la variante si los tiene»
    mundo.hacer({**NUEVA, "valores": None})
    mundo.apuntar([{"nombre": VARIANTE, "cantidad": 350, "unidad": "g", "kcal": 1, "proteina_g": 30,
                    "hidratos_g": 40, "grasa_g": 10}])
    (a,) = mundo.registro.apuntes_del_dia(HOY)
    assert round(a["kcal"]) == 368 and (a["proteina_g"], a["hidratos_g"], a["grasa_g"]) == (None, None, None)


def test_variante_en_otra_unidad_no_se_apunta(mundo):  # R1: it is per 100 g, nothing logged with another number
    variante = _con_variante(mundo)
    hecho = mundo.apuntar([{"nombre": VARIANTE, "cantidad": 0.5, "unidad": "receta",
                            "guardado": {"tipo": "variante", "id": variante["id"]}}])
    assert hecho.startswith("No se ha apuntado nada"), hecho
    assert mundo.registro.apuntes_del_dia(HOY) == []


# -- R3 · R-342, G-75, S-115, C-435: change, delete, delete the recipe; what was logged stays ---------------------

def test_cambiar_la_variante_se_ensena_y_lo_apuntado_sigue(mundo):  # R3
    _con_variante(mundo)
    mundo.apuntar([{"nombre": VARIANTE, "cantidad": 350, "unidad": "g", "kcal": 1}], dia=AYER)
    cambio = {"accion": "variante_cambiar", "objeto": RECETA, "texto": "sin chorizo", "valor": 100}
    antes = mundo.volcado()
    texto = mundo.preparar(cambio)
    assert "105" in texto and "100 kcal/100 g" in texto, texto
    assert mundo.volcado() == antes
    assert mundo.hacer(cambio).startswith("Hecho")
    (variante,) = mundo.variantes()
    assert variante["kcal_100g"] == 100
    assert variante["macros_100g"] == {"proteina_g": 7, "hidratos_g": 15, "grasa_g": 2}  # not said: as they were
    (a,) = mundo.registro.apuntes_del_dia(AYER)
    assert round(a["kcal"]) == 368  # what was logged is history
    assert mundo.kcal_receta() == 126


def test_borrar_la_variante_se_ensena_y_lo_apuntado_sigue(mundo):  # R3
    _con_variante(mundo)
    mundo.apuntar([{"nombre": VARIANTE, "cantidad": 350, "unidad": "g", "kcal": 1}], dia=AYER)
    cambio = {"accion": "variante_borrar", "objeto": RECETA, "texto": "sin chorizo"}
    antes = mundo.volcado()
    texto = mundo.preparar(cambio)
    assert "sin chorizo" in texto, texto
    assert mundo.volcado() == antes
    assert mundo.hacer(cambio).startswith("Hecho")
    assert mundo.variantes() == []
    (a,) = mundo.registro.apuntes_del_dia(AYER)
    assert round(a["kcal"]) == 368 and a["nombre"] == VARIANTE
    assert mundo.kcal_receta() == 126


def test_borrar_la_receta_se_lleva_sus_variantes_y_lo_dice(mundo):  # R3 · S-115
    _con_variante(mundo)
    mundo.apuntar([{"nombre": VARIANTE, "cantidad": 350, "unidad": "g", "kcal": 1}], dia=AYER)
    cambio = {"accion": "borrar_receta", "objeto": RECETA}
    texto = mundo.preparar(cambio)
    assert "sin chorizo" in texto, texto  # it says the variants go with it
    assert mundo.hacer(cambio).startswith("Hecho")
    with sqlite3.connect(mundo.db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM receta_variante").fetchone()[0] == 0
    (a,) = mundo.registro.apuntes_del_dia(AYER)
    assert round(a["kcal"]) == 368


def test_dos_variantes_con_el_mismo_nombre_no_se_guardan(mundo):  # R3
    _con_variante(mundo)
    antes = mundo.volcado()
    texto = mundo.preparar({**NUEVA, "texto": "SIN CHORIZO", "valor": 110})
    assert texto.startswith("No se puede"), texto
    assert mundo.volcado() == antes
    with pytest.raises(ValueError):
        mundo.recetario.crear_variante(mundo.receta["id"], {"nombre": "Sin chorízo", "kcal_100g": 110})
    assert len(mundo.variantes()) == 1


def test_el_mismo_nombre_en_otra_receta_si_se_guarda(mundo):  # R3: unique WITHIN its recipe
    otra = mundo.recetario.crear_receta({"nombre": "Lentejas de prueba", "peso_neto_g": 1000, "ingredientes": [
        {"nombre": "lenteja cocida de prueba", "cantidad": 1000, "unidad": "g", "kcal": 1100}]}, HOY)
    _con_variante(mundo)
    v = mundo.recetario.crear_variante(otra["id"], {"nombre": "sin chorizo", "kcal_100g": 95})
    assert v["nombre"] == "sin chorizo"


# -- R4 · R-343, S-116, C-436, C-437: the dessert, in its section ------------------------------------------------

def test_postre_del_coach_queda_en_su_seccion_y_suma_igual(mundo):  # R4
    _con_variante(mundo)
    hecho = mundo.apuntar([{"nombre": VARIANTE, "cantidad": 350, "unidad": "g", "kcal": 1},
                           {"nombre": "yogur natural de prueba", "cantidad": 125, "unidad": "g", "kcal": 77,
                            "postre": True}])
    assert hecho.startswith("Hecho"), hecho
    garbanzos, yogur = mundo.registro.apuntes_del_dia(HOY)
    assert (garbanzos["seccion"], garbanzos["postre"]) == ("comida", False)
    assert (yogur["seccion"], yogur["postre"]) == ("comida", True)
    resumen = mundo.equipo.asistente.resumen(HOY)
    assert resumen["total_kcal"] == pytest.approx(garbanzos["kcal"] + 77)


def test_marca_de_postre_no_cambia_el_total_ni_el_margen(mundo):  # R4
    mundo.apuntar([{"nombre": "merluza de prueba", "cantidad": 200, "unidad": "g", "kcal": 180},
                   {"nombre": "yogur natural de prueba", "cantidad": 125, "unidad": "g", "kcal": 77}], seccion="cena")
    _, yogur = mundo.registro.apuntes_del_dia(HOY)
    antes = mundo.equipo.asistente.resumen(HOY)
    marcado = mundo.registro.marcar_postre(yogur["id"], True)
    assert marcado["postre"] is True and marcado["seccion"] == "cena" and marcado["kcal"] == 77
    despues = mundo.equipo.asistente.resumen(HOY)
    assert despues["total_kcal"] == antes["total_kcal"]
    assert despues["margenes"] == antes["margenes"]
    movido = mundo.registro.mover_seccion(yogur["id"], "comida")
    assert (movido["seccion"], movido["postre"]) == ("comida", True)  # S-116: it stays dessert there
    quitado = mundo.registro.marcar_postre(yogur["id"], False)
    assert (quitado["seccion"], quitado["postre"]) == ("comida", False)


@pytest.mark.parametrize("malo", ["sí", 1, None, [True]])
def test_marca_de_postre_que_no_es_si_o_no_no_cambia_nada(mundo, malo):  # R4
    a = mundo.registro.anadir(Alimento(nombre="yogur de prueba", dia=HOY, cantidad=125, unidad="g", kcal=77), HOY)
    with pytest.raises(ValueError):
        mundo.registro.marcar_postre(a["id"], malo)
    assert mundo.registro.alimento(a["id"])["postre"] is False


def test_postre_que_no_es_si_o_no_no_apunta_nada(mundo):  # R4
    hecho = mundo.apuntar([{"nombre": "yogur de prueba", "cantidad": 125, "unidad": "g", "kcal": 77,
                            "postre": "quizá"}])
    assert hecho.startswith("No se ha apuntado nada"), hecho
    assert mundo.registro.apuntes_del_dia(HOY) == []


# -- R5 · R-344, C-438: what the coach sees ----------------------------------------------------------------------

def test_get_recetas_da_las_variantes(mundo):  # R5
    _con_variante(mundo)
    datos = json.loads(recetario_texto(mundo.equipo))
    (receta,) = [r for r in datos["recetas"] if r["nombre"] == RECETA]
    (v,) = receta["variantes"]
    assert (v["nombre"], v["kcal_100g"]) == ("sin chorizo", 105)
    assert v["macros_100g"] == {"proteina_g": 7, "hidratos_g": 15, "grasa_g": 2}
    assert isinstance(v["id"], int)


def test_get_contexto_da_las_variantes_y_el_postre(mundo):  # R5
    _con_variante(mundo)
    mundo.apuntar([{"nombre": VARIANTE, "cantidad": 350, "unidad": "g", "kcal": 1},
                   {"nombre": "yogur natural de prueba", "cantidad": 125, "unidad": "g", "kcal": 77,
                    "postre": True}])
    datos = mundo.datos_contexto()
    (con,) = datos["variantes_de_recetas"]
    assert con["receta"] == RECETA
    (v,) = con["variantes"]
    assert (v["nombre"], v["kcal_100g"]) == ("sin chorizo", 105)
    assert v["macros_100g"] == {"proteina_g": 7, "hidratos_g": 15, "grasa_g": 2}
    (hoy,) = [d for d in datos["dias"] if d["fecha"] == HOY.isoformat()]
    garbanzos, yogur = hoy["comidas"]
    assert yogur["nombre"] == "yogur natural de prueba" and yogur["postre"] is True
    assert "postre" not in garbanzos  # only the dessert carries the mark


def test_contexto_sin_variantes_ni_postre_no_lleva_nada_nuevo(mundo):  # R5: nothing invented
    mundo.apuntar([{"nombre": "pera de prueba", "cantidad": 150, "unidad": "g", "kcal": 80}])
    datos = mundo.datos_contexto()
    assert "variantes_de_recetas" not in datos
    (hoy,) = [d for d in datos["dias"] if d["fecha"] == HOY.isoformat()]
    assert "postre" not in hoy["comidas"][0]


def test_el_catalogo_lleva_las_acciones_de_variantes_sin_cifras():  # R1, R3 · G-38
    from tridata.nutricion.asistente import catalogo, texto_acciones

    texto = texto_acciones()
    for accion in ("variante_nueva", "variante_cambiar", "variante_borrar"):
        assert accion in catalogo() and f"- {accion}:" in texto
    assert re.search(r"\d", texto) is None


# -- R6: a store of today opens as it was; impossible kcal are not saved ----------------------------------------

# the schema of main 24830b7 (what Andrés's store has today), as SQLite keeps it after the ALTER TABLEs
_ESQUEMA_24830B7 = """
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
    origen TEXT, provisional INTEGER NOT NULL DEFAULT 0, seccion TEXT, macros_origen TEXT
);
CREATE INDEX idx_alimento_apuntado_dia ON alimento_apuntado(dia);
CREATE TABLE receta (
    id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, alias TEXT NOT NULL DEFAULT '[]', nota TEXT,
    version_en_uso INTEGER NOT NULL DEFAULT 1, por_revisar INTEGER NOT NULL DEFAULT 0, creado TEXT NOT NULL
);
CREATE TABLE receta_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT, receta_id INTEGER NOT NULL REFERENCES receta(id), numero INTEGER NOT NULL,
    fecha TEXT NOT NULL, ingredientes TEXT NOT NULL, kcal_total REAL, olla_id INTEGER, olla TEXT, tara_g REAL,
    peso_lleno_g REAL, peso_neto_g REAL, kcal_100g REAL, kcal_100g_aprox REAL, UNIQUE (receta_id, numero)
);
CREATE TABLE alimento_fijo (
    id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, alias TEXT NOT NULL DEFAULT '[]', kcal REAL NOT NULL,
    por TEXT NOT NULL CHECK (por IN ('100g', 'unidad')), nota TEXT, comun INTEGER NOT NULL DEFAULT 0, proteina REAL,
    hidratos REAL, grasa REAL, macros_origen TEXT
);
CREATE TABLE comun_quitado (nombre TEXT PRIMARY KEY);
CREATE TABLE olla (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, tara_g REAL NOT NULL);
"""


def _base_de_hoy(db) -> None:
    with sqlite3.connect(db) as conn:
        conn.executescript(_ESQUEMA_24830B7)
        for dia, nombre, kcal, seccion in ((AYER, "avena de prueba", 228, "desayuno"),
                                           (AYER, "garbanzos de prueba", 441, "comida"),
                                           (HOY, "yogur de prueba", 77, None)):
            conn.execute("INSERT INTO alimento_apuntado (dia, nombre, cantidad, unidad, kcal, estado, estimado, "
                         "supuestos, pendiente_cantidad, creado, origen, provisional, seccion) VALUES (?, ?, 100, "
                         "'g', ?, 'comido', 0, '[]', 0, '2026-11-24T10:00:00', NULL, 0, ?)",
                         (dia.isoformat(), nombre, kcal, seccion))
        conn.execute("INSERT INTO receta (nombre, alias, version_en_uso, por_revisar, creado) VALUES "
                     "('garbanzos de prueba', '[]', 1, 0, '2026-11-01T10:00:00')")
        conn.execute("INSERT INTO receta_version (receta_id, numero, fecha, ingredientes, kcal_total, peso_neto_g, "
                     "kcal_100g) VALUES (1, 1, '2026-11-01', '[]', 1260, 1000, 126)")


def _tablas(db, *tablas: str) -> dict[str, list[dict]]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return {t: [dict(f) for f in conn.execute(f"SELECT * FROM {t} ORDER BY id")] for t in tablas}  # noqa: S608
    finally:
        conn.close()


def _esquema(db) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()


def test_base_de_hoy_abre_igual_sin_migrar_a_mano(tmp_path):  # R6
    db = tmp_path / "data.sqlite3"
    _base_de_hoy(db)
    antes = _tablas(db, "alimento_apuntado", "receta", "receta_version")
    registro = RegistroComidas(db)
    recetario = Recetario(db)
    despues = _tablas(db, "alimento_apuntado", "receta", "receta_version")
    assert [{k: v for k, v in f.items() if k != "postre"} for f in despues["alimento_apuntado"]] \
        == antes["alimento_apuntado"]
    assert [f["postre"] for f in despues["alimento_apuntado"]] == [0, 0, 0]  # nothing is dessert
    assert despues["receta"] == antes["receta"] and despues["receta_version"] == antes["receta_version"]
    assert [a["postre"] for a in registro.apuntes_del_dia(AYER)] == [False, False]
    assert sum(a["kcal"] for a in registro.apuntes_del_dia(AYER)) == 669
    assert [a["seccion"] for a in registro.apuntes_del_dia(AYER)] == ["desayuno", "comida"]
    (receta,) = recetario.recetas()
    assert receta["en_uso"]["kcal_100g"] == 126 and receta["variantes"] == []


def test_abrir_la_base_dos_veces_no_rehace_nada(tmp_path):  # R6
    db = tmp_path / "data.sqlite3"
    _base_de_hoy(db)
    RegistroComidas(db)
    Recetario(db)
    esquema, datos = _esquema(db), _tablas(db, "alimento_apuntado", "receta", "receta_version")
    RegistroComidas(db)
    Recetario(db)
    assert _esquema(db) == esquema
    assert _tablas(db, "alimento_apuntado", "receta", "receta_version") == datos


@pytest.mark.parametrize("kcal", [float("nan"), float("inf"), float("-inf"), -1, None, "", "1e400"])
def test_variante_con_kcal_imposibles_no_se_guarda_y_dice_por_que(mundo, kcal):  # R6
    with pytest.raises(ValueError) as exc:
        mundo.recetario.crear_variante(mundo.receta["id"], {"nombre": "sin chorizo", "kcal_100g": kcal})
    assert "kcal" in str(exc.value)
    assert mundo.variantes() == []


@pytest.mark.parametrize("valor", [None, -1])
def test_el_coach_con_kcal_imposibles_no_guarda_nada(mundo, valor):  # R6
    antes = mundo.volcado()
    texto = mundo.preparar({**NUEVA, "valor": valor})
    assert texto.startswith("No se puede"), texto
    assert "kcal" in texto
    assert mundo.volcado() == antes
