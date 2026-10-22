"""Unit 019 · add with what is saved: a recipe whole or by fractions without weighing, the kcal of what is chosen
from the search box always worked out on the server, and «Ingredientes» on screen.

Recetario over a temporary SQLite and the Starlette TestClient; the Claude interpreter is a unittest.mock
double (no API). Every value is invented (§Criterios de aceptación of the unit).

Plans: `apuntar-comida` R-93 (C-131, C-132), R-55 · `guardar-receta` R-94 (C-133), R-57 (C-134), R-58, R-45,
R-40, R-51 · `pedir-al-asistente` S-21. R1-R8 of the unit (R1 and R7 are on screen: tests/e2e).
"""
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas, RegistroPeso
from tridata.nutricion.recetas import Recetario
from tridata.web import crear_app

HOY = date(2026, 10, 21)
MANANA = HOY + timedelta(days=1)
ESTATICOS = Path(__file__).parent.parent / "src" / "tridata" / "web" / "static"

FIJOS = (("lenteja seca de prueba", 340, "100g"), ("aceite de prueba", 900, "100g"), ("cebolla de prueba", 40, "100g"),
         ("avena de prueba", 370, "100g"), ("leche de prueba", 46, "100g"), ("plátano de prueba", 89, "100g"),
         ("gel de prueba", 90, "unidad"))


def _linea(nombre: str, gramos: float) -> dict:
    return {"nombre": nombre, "cantidad": gramos, "unidad": "g", "kcal": 1, "guardado": nombre}


def sembrar(db: Path) -> tuple[Recetario, dict]:
    """The saved things of §Criterios: the ingredients, the bote with its alias, and the three recipes."""
    rec = Recetario(db)
    ids = {}
    for nombre, kcal, por in FIJOS:
        ids[nombre] = rec.crear_fijo({"nombre": nombre, "kcal": kcal, "por": por})["id"]
    ids["Lentejas cocidas bote"] = rec.crear_fijo({"nombre": "Lentejas cocidas bote", "kcal": 90, "por": "100g",
                                                   "alias": ["bote de lentejas"]})["id"]
    ids["Lentejas de la abuela"] = rec.crear_receta({"nombre": "Lentejas de la abuela", "peso_neto_g": 2500, "ingredientes": [
        _linea("lenteja seca de prueba", 500), _linea("aceite de prueba", 30), _linea("cebolla de prueba", 75)]}, HOY)["id"]
    ids["Porridge de prueba"] = rec.crear_receta({"nombre": "Porridge de prueba", "ingredientes": [
        _linea("avena de prueba", 80), _linea("leche de prueba", 250), _linea("plátano de prueba", 100)]}, HOY)["id"]
    ids["Lentejas con kfjd"] = rec.crear_receta({"nombre": "Lentejas con kfjd", "peso_neto_g": 1000, "ingredientes": [
        _linea("lenteja seca de prueba", 200), {"nombre": "kfjd", "cantidad": 50, "unidad": "g", "kcal": None,
                                                "dudoso": True}]}, HOY)["id"]
    return rec, ids


@pytest.fixture
def guardado(tmp_path):
    return sembrar(tmp_path / "data.sqlite3")


def test_lo_sembrado_es_lo_de_los_criterios(guardado):
    rec, ids = guardado
    abuela = rec.receta(ids["Lentejas de la abuela"])["en_uso"]
    assert (abuela["kcal_total"], abuela["kcal_100g"], abuela["estado"]) == (2000, 80, "definitiva")
    porridge = rec.receta(ids["Porridge de prueba"])["en_uso"]
    assert (porridge["kcal_total"], porridge["estado"], porridge["kcal_100g_aprox"]) == (500, "provisional", None)
    assert rec.receta(ids["Lentejas con kfjd"])["en_uso"]["estado"] == "sin_kcal"


# -- R4: whole or by fractions (para_apuntar on top of _calcular) · R-45, R-57, R-58, G-32 --------------------

@pytest.mark.parametrize("fraccion, kcal", [(1, 2000), (0.5, 1000), (0.25, 500)])
def test_receta_pesada_por_fraccion(guardado, fraccion, kcal):  # R4 · R-57, R-45
    rec, ids = guardado
    a = rec.para_apuntar("receta", ids["Lentejas de la abuela"], fraccion, "receta")
    assert a == {"nombre": "Lentejas de la abuela", "cantidad": fraccion, "unidad": "receta", "kcal": kcal,
                 "origen": "de tu receta «Lentejas de la abuela»", "provisional": False}


def test_un_tercio(guardado):  # R4: ⅓ goes as 1/3
    rec, ids = guardado
    assert rec.para_apuntar("receta", ids["Lentejas de la abuela"], 1 / 3, "receta")["kcal"] == pytest.approx(666.7)


@pytest.mark.parametrize("fraccion, kcal", [(1, 500), (0.5, 250)])
def test_receta_sin_pesar_por_fraccion_es_provisional(guardado, fraccion, kcal):  # R4 · R-58 (rewritten), C-134
    rec, ids = guardado
    a = rec.para_apuntar("receta", ids["Porridge de prueba"], fraccion, "receta")
    assert (a["kcal"], a["provisional"], a["unidad"], a["origen"]) == (
        kcal, True, "receta", "de tu receta «Porridge de prueba»")


@pytest.mark.parametrize("unidad", [None, "g"])
def test_receta_sin_pesar_por_gramos_sigue_sin_poderse(guardado, unidad):  # R4 · R-58, C-70
    rec, ids = guardado
    with pytest.raises(ValueError, match="pésala o pon sus kcal/100 g aproximadas"):
        rec.para_apuntar("receta", ids["Porridge de prueba"], 200, unidad)


def test_por_gramos_sigue_como_antes(guardado):  # R2 · R-57
    rec, ids = guardado
    a = rec.para_apuntar("receta", ids["Lentejas de la abuela"], 300, "g")
    assert (a["kcal"], a["unidad"], a["provisional"]) == (240, "g", False)
    assert rec.para_apuntar("receta", ids["Lentejas de la abuela"], 300) == a


@pytest.mark.parametrize("unidad, cantidad", [("receta", 1), ("g", 100), (None, 100)])
def test_un_ingrediente_dudoso_no_se_apunta(guardado, unidad, cantidad):  # R4 · G-32
    rec, ids = guardado
    with pytest.raises(ValueError, match="corrige la receta"):
        rec.para_apuntar("receta", ids["Lentejas con kfjd"], cantidad, unidad)


def test_una_receta_por_revisar_no_se_apunta(guardado, tmp_path):  # R1, R4: «corrígela en Recetas»
    rec, ids = guardado
    with sqlite3.connect(tmp_path / "data.sqlite3") as conn:
        conn.execute("UPDATE receta SET por_revisar = 1 WHERE id = ?", (ids["Lentejas de la abuela"],))
    with pytest.raises(ValueError, match="corrige la receta"):
        rec.para_apuntar("receta", ids["Lentejas de la abuela"], 1, "receta")


@pytest.mark.parametrize("fraccion", [0, -0.5, None])
def test_fraccion_vacia_o_cero_no_se_apunta(guardado, fraccion):  # R3
    rec, ids = guardado
    with pytest.raises(ValueError, match="escribe cuánto has cogido"):
        rec.para_apuntar("receta", ids["Porridge de prueba"], fraccion, "receta")


def test_ingrediente_en_su_unidad(guardado):  # R2 · R-57
    rec, ids = guardado
    bote = rec.para_apuntar("fijo", ids["Lentejas cocidas bote"], 400, "g")
    assert bote == {"nombre": "Lentejas cocidas bote", "cantidad": 400, "unidad": "g", "kcal": 360,
                    "origen": "de tu ingrediente «Lentejas cocidas bote»", "provisional": False}
    gel = rec.para_apuntar("fijo", ids["gel de prueba"], 2, "unidad")
    assert (gel["kcal"], gel["unidad"]) == (180, "unidad")
    assert rec.para_apuntar("fijo", ids["gel de prueba"], 2) == gel


@pytest.mark.parametrize("cual, unidad", [("gel de prueba", "g"), ("Lentejas cocidas bote", "unidad"),
                                          ("Lentejas cocidas bote", "receta")])
def test_ingrediente_en_otra_unidad_no_se_apunta(guardado, cual, unidad):  # R4: gel 2 g → ValueError
    rec, ids = guardado
    with pytest.raises(ValueError, match="está guardado por"):
        rec.para_apuntar("fijo", ids[cual], 2, unidad)


def test_receta_en_unidades_no_se_apunta(guardado):
    rec, ids = guardado
    with pytest.raises(ValueError, match="por gramos o por fracción"):
        rec.para_apuntar("receta", ids["Lentejas de la abuela"], 2, "unidad")


# -- the web: an app over a temporary SQLite with 1.000 kcal today ----------------------------------------------

class Entorno:
    def __init__(self, db: Path):
        self.db = db
        self.ajustes = Ajustes(db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "objetivo_pct": -10}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(db)
        self.recetario, self.ids = sembrar(db)
        self.registro.anadir(Alimento(nombre="comida de antes", dia=HOY, cantidad=500, unidad="g", kcal=1000), HOY)
        self.interprete = MagicMock()
        self.cliente = TestClient(crear_app(self.registro, self.interprete, self.ajustes, hoy=lambda: HOY))

    def post(self, ruta, cuerpo):
        return self.cliente.post(ruta, json=cuerpo)

    def ok(self, ruta, cuerpo):
        r = self.post(ruta, cuerpo)
        assert r.status_code == 200, r.text
        return r.json()

    def dia(self, fecha: date = HOY) -> dict:
        return self.cliente.get(f"/api/dia?fecha={fecha.isoformat()}").json()

    def nombres_de_hoy(self) -> list[str]:
        return [a["nombre"] for a in self.registro.apuntes_del_dia(HOY)]


@pytest.fixture
def e(tmp_path):
    return Entorno(tmp_path / "data.sqlite3")


# -- R4: the /apuntar routes read the unit · R-57, C-134 --------------------------------------------------------

def test_apuntar_el_porridge_entero_en_hoy(e):  # R4 · R-57, C-134
    a = e.ok(f"/api/recetas/{e.ids['Porridge de prueba']}/apuntar", {"cantidad": 1, "unidad": "receta",
                                                                    "kcal": 999})["alimento"]
    assert (a["dia"], a["estado"], a["nombre"], a["cantidad"], a["unidad"], a["kcal"], a["provisional"]) == (
        HOY.isoformat(), "comido", "Porridge de prueba", 1, "receta", 500, True)
    assert a["origen"] == "de tu receta «Porridge de prueba»"
    assert e.dia()["total_kcal"] == 1500


def test_apuntar_media_receta_pesada(e):  # R4
    a = e.ok(f"/api/recetas/{e.ids['Lentejas de la abuela']}/apuntar", {"cantidad": 0.5, "unidad": "receta"})["alimento"]
    assert (a["kcal"], a["provisional"], a["unidad"]) == (1000, False, "receta")


def test_apuntar_por_gramos_sin_unidad_sigue_igual(e):  # lo de antes: {"cantidad": n} son gramos
    a = e.ok(f"/api/recetas/{e.ids['Lentejas de la abuela']}/apuntar", {"cantidad": 300})["alimento"]
    assert (a["kcal"], a["unidad"]) == (240, "g")
    r = e.post(f"/api/recetas/{e.ids['Porridge de prueba']}/apuntar", {"cantidad": 300, "unidad": "g"})
    assert r.status_code == 422 and "pésala" in r.json()["error"]


@pytest.mark.parametrize("cuerpo, estado", [({"cantidad": 0, "unidad": "receta"}, 422),
                                            ({"cantidad": -1, "unidad": "receta"}, 422),
                                            ({"cantidad": 1, "unidad": 3}, 400)])
def test_apuntar_por_fraccion_que_no_vale(e, cuerpo, estado):  # R3, R4
    antes = e.nombres_de_hoy()
    r = e.post(f"/api/recetas/{e.ids['Porridge de prueba']}/apuntar", cuerpo)
    assert r.status_code == estado, r.text
    assert "no se ha guardado nada" in r.json()["error"].lower()
    assert e.nombres_de_hoy() == antes


# -- R2, R3: «Añadir a mano» with what was chosen · R-93, C-131, R-57 (the bearing criterion) ---------------------

def _elegido(tipo: str, ident: int, cantidad, unidad, **extra) -> dict:
    return {"nombre": "lo que escribió", "dia": HOY.isoformat(), "cantidad": cantidad, "unidad": unidad,
            "kcal": 999, "guardado": {"tipo": tipo, "id": ident}, **extra}


def test_anadir_el_bote_elegido_guarda_las_kcal_del_servidor(e):  # R2, R3 · R-93, C-131 — criterio portante
    a = e.ok("/api/alimento", _elegido("fijo", e.ids["Lentejas cocidas bote"], 400, "g"))["alimento"]
    assert (a["nombre"], a["cantidad"], a["unidad"], a["kcal"], a["estimado"], a["dia"]) == (
        "Lentejas cocidas bote", 400, "g", 360, False, HOY.isoformat())
    assert a["origen"] == "de tu ingrediente «Lentejas cocidas bote»" and a["provisional"] is False
    assert e.dia()["total_kcal"] == 1360


def test_anadir_gel_por_unidades_y_receta_por_gramos(e):  # R2
    gel = e.ok("/api/alimento", _elegido("fijo", e.ids["gel de prueba"], 2, "unidad"))["alimento"]
    assert (gel["nombre"], gel["kcal"], gel["origen"]) == ("gel de prueba", 180, "de tu ingrediente «gel de prueba»")
    abuela = e.ok("/api/alimento", _elegido("receta", e.ids["Lentejas de la abuela"], 300, "g"))["alimento"]
    assert (abuela["kcal"], abuela["origen"]) == (240, "de tu receta «Lentejas de la abuela»")


def test_anadir_el_porridge_entero(e):  # R4
    a = e.ok("/api/alimento", _elegido("receta", e.ids["Porridge de prueba"], 1, "receta"))["alimento"]
    assert (a["kcal"], a["provisional"], a["unidad"], a["cantidad"]) == (500, True, "receta", 1)


def test_anadir_elegido_en_un_dia_planeado_conserva_el_dia(e):  # R2: «en ese día»
    a = e.ok("/api/alimento", {**_elegido("fijo", e.ids["Lentejas cocidas bote"], 400, "g"),
                               "dia": MANANA.isoformat()})["alimento"]
    assert (a["dia"], a["estado"], a["kcal"]) == (MANANA.isoformat(), "planeado", 360)


@pytest.mark.parametrize("cambio, estado, texto", [
    ({"guardado": {"tipo": "fijo", "id": 999}}, 422, "ya no está guardado: no se ha guardado nada"),
    ({"guardado": {"tipo": "receta", "id": 999}}, 422, "ya no está guardado: no se ha guardado nada"),
    ({"cantidad": None}, 422, "escribe cuánto has cogido"),
    ({"cantidad": 0}, 422, "escribe cuánto has cogido"),
    ({"cantidad": -2}, 422, "escribe cuánto has cogido"),
    ({"unidad": "unidad"}, 422, "está guardado por 100 g"),
    ({"cantidad": "mucho"}, 400, "no es un número"),
    ({"guardado": {"tipo": "olla", "id": 1}}, 400, ""),
    ({"guardado": {"tipo": "fijo", "id": "1"}}, 400, ""),
])
def test_anadir_elegido_que_no_se_puede_no_guarda_nada(e, cambio, estado, texto):  # R3
    antes = e.nombres_de_hoy()
    r = e.post("/api/alimento", {**_elegido("fijo", e.ids["Lentejas cocidas bote"], 400, "g"), **cambio})
    assert r.status_code == estado, r.text
    assert texto in r.json()["error"]
    assert e.nombres_de_hoy() == antes


def test_un_ingrediente_borrado_dice_su_nombre_no_el_id(e):  # R3
    e.ok(f"/api/fijos/{e.ids['Lentejas cocidas bote']}/borrar", {"confirmado": True})
    r = e.post("/api/alimento", _elegido("fijo", e.ids["Lentejas cocidas bote"], 400, "g"))
    assert r.status_code == 422
    assert r.json()["error"] == "«lo que escribió» ya no está guardado: no se ha guardado nada."


def test_el_guardado_de_claude_no_es_una_eleccion(e):  # unit 011: `guardado` as a name (Claude's) is not chosen
    a = e.ok("/api/alimento", {"nombre": "lentejas", "dia": HOY.isoformat(), "cantidad": 300, "unidad": "g",
                               "kcal": 350, "guardado": "Lentejas de la abuela"})["alimento"]
    assert (a["nombre"], a["kcal"], a["origen"]) == ("lentejas", 350, None)


def test_anadir_a_mano_sin_elegir_sigue_igual(e):  # NO debe haber cambiado
    a = e.ok("/api/alimento", {"nombre": "tortilla", "dia": HOY.isoformat(), "cantidad": 150, "unidad": "g",
                               "kcal": 300})["alimento"]
    assert (a["nombre"], a["kcal"], a["origen"]) == ("tortilla", 300, None)


# -- R5: the review · R-93, C-132, R-55 --------------------------------------------------------------------------

def _de_la_revision(**extra) -> dict:
    return {"nombre": "unas lentejas", "dia": HOY.isoformat(), "cantidad": 300, "unidad": "g", "kcal": 350,
            "estimado": True, "dudoso": False, "supuestos": ["un plato"], **extra}


def test_confirmar_con_lo_elegido(e):  # R5 · C-132
    abuela = _de_la_revision(nombre="Lentejas de la abuela", cantidad=0.5, unidad="receta", kcal=999,
                             guardado={"tipo": "receta", "id": e.ids["Lentejas de la abuela"]})
    bote = _de_la_revision(nombre="bote", cantidad=400, unidad="g", kcal=1,
                           guardado={"tipo": "fijo", "id": e.ids["Lentejas cocidas bote"]})
    guardados = e.ok("/api/confirmar", {"texto": "unas lentejas y un bote", "alimentos": [abuela, bote]})["guardados"]
    assert [(g["nombre"], g["kcal"], g["estimado"], g["provisional"]) for g in guardados] == [
        ("Lentejas de la abuela", 1000, False, False), ("Lentejas cocidas bote", 360, False, False)]
    assert [g["origen"] for g in guardados] == ["de tu receta «Lentejas de la abuela»",
                                                "de tu ingrediente «Lentejas cocidas bote»"]
    assert e.dia()["total_kcal"] == 2360


def test_confirmar_con_uno_roto_no_guarda_nada(e):  # R3, R5: todo o nada
    bueno = _de_la_revision(guardado={"tipo": "fijo", "id": e.ids["Lentejas cocidas bote"]}, cantidad=400)
    roto = _de_la_revision(nombre="otra cosa", guardado={"tipo": "receta", "id": 999}, cantidad=1, unidad="receta")
    sin_cantidad = _de_la_revision(guardado={"tipo": "fijo", "id": e.ids["gel de prueba"]}, cantidad=0, unidad="unidad")
    antes = e.nombres_de_hoy()
    for lista in ([bueno, roto], [bueno, sin_cantidad]):
        r = e.post("/api/confirmar", {"texto": "x", "alimentos": lista, "peso": {"kg": 72.4, "dia": HOY.isoformat()}})
        assert r.status_code == 422, r.text
        assert "no se ha guardado nada" in r.json()["error"]
    assert e.nombres_de_hoy() == antes
    assert RegistroPeso(e.db).entre(HOY, HOY) == []


def test_confirmar_sin_elegir_sigue_igual(e):  # NO debe haber cambiado: la estimación de Claude se guarda tal cual
    g = e.ok("/api/confirmar", {"texto": "unas lentejas", "alimentos": [_de_la_revision()]})["guardados"][0]
    assert (g["nombre"], g["kcal"], g["estimado"]) == ("unas lentejas", 350, True)


# -- R6: an ingredient chosen in a recipe · R-94, C-133, R-40 ------------------------------------------------------

def test_receta_nueva_con_ingredientes_elegidos(e):  # R6 · C-133
    receta = e.ok("/api/recetas", {"nombre": "Lentejas rápidas", "ingredientes": [
        {"nombre": "Lentejas cocidas bote", "cantidad": 400, "unidad": "g", "kcal": 1, "guardado": "Lentejas cocidas bote"},
        {"nombre": "cebolla de prueba", "cantidad": 40, "unidad": "g", "kcal": 1, "guardado": "cebolla de prueba"},
        {"nombre": "sal a ojo", "cantidad": 2, "unidad": "g", "kcal": None},
    ]})["receta"]
    lineas = receta["en_uso"]["ingredientes"]
    assert [(i["nombre"], i["kcal"], i["estado"]) for i in lineas] == [
        ("Lentejas cocidas bote", 360, "guardado"), ("cebolla de prueba", 16, "guardado"), ("sal a ojo", None, "dudoso")]
    assert receta["en_uso"]["kcal_total"] == 376


# -- R8: «ingrediente» in what the screen shows · S-21, R-57, R-51, R-43, R-55 --------------------------------------

def test_el_origen_dice_ingrediente(e):  # R8
    a = e.ok(f"/api/fijos/{e.ids['gel de prueba']}/apuntar", {"cantidad": 2})["alimento"]
    assert a["origen"] == "de tu ingrediente «gel de prueba»"


def test_un_nombre_que_ya_es_ingrediente(e):  # R8 · R-51
    r = e.post("/api/recetas", {"nombre": "bote de lentejas", "ingredientes": [_linea("avena de prueba", 50)]})
    assert r.status_code == 422
    assert "ya es tu ingrediente «Lentejas cocidas bote»" in r.json()["error"]


def test_dictado_parecido_propone_tu_ingrediente(guardado):  # R8 · R-43
    rec, _ = guardado
    r = rec.resolver({"nombre": "gel raro", "cantidad": 1, "unidad": "unidad", "dia": HOY.isoformat(), "kcal": 80,
                      "estimado": True, "dudoso": False, "supuestos": [], "guardado": "gel de prueba",
                      "coincidencia": "parecida"})
    assert r["supuestos"] == ["has dicho «gel raro»: te propongo tu ingrediente «gel de prueba»"]
    assert r["origen"] == "de tu ingrediente «gel de prueba»"


def test_un_ingrediente_borrado(e):  # R8
    for r in (e.post("/api/fijos/999", {"nombre": "x", "kcal": 1, "por": "100g"}),
              e.post("/api/fijos/999/apuntar", {"cantidad": 1})):
        assert r.status_code == 404 and r.json()["error"] == "Ese ingrediente ya no está guardado."


def test_lo_apuntado_antes_no_se_reescribe(e):  # R8 · R-55: the screen reads it, the base keeps it
    e.ok("/api/confirmar", {"texto": "dátiles", "alimentos": [{
        "nombre": "dátiles de prueba", "dia": HOY.isoformat(), "cantidad": 6, "unidad": "g", "kcal": 16.8,
        "estimado": False, "dudoso": False, "supuestos": [], "origen": "de tu alimento fijo «dátiles de prueba»"}]})
    fila = next(a for a in e.dia()["alimentos"] if a["nombre"] == "dátiles de prueba")
    assert (fila["origen"], fila["kcal"]) == ("de tu alimento fijo «dátiles de prueba»", 16.8)


def test_en_la_pantalla_no_queda_alimento_fijo():  # R8: the static texts (the E2E looks at the drawn screens)
    for fichero in ("index.html", "recetas.js", "app.js", "buscador.js", "ajustes.html", "ajustes.js"):
        texto = (ESTATICOS / fichero).read_text(encoding="utf-8")
        visibles = [linea for linea in texto.splitlines() if not linea.lstrip().startswith("//")
                    and re.search(r"alimentos? fijos?", linea, re.I) and "de tu alimento fijo" not in linea]
        assert visibles == [], f"{fichero}: {visibles}"


# -- the shell: buscador.js offline, with another version ---------------------------------------------------------

def test_el_cascaron_lleva_el_buscador():  # punto 8 del Cómo
    sw = (ESTATICOS / "sw.js").read_text(encoding="utf-8")
    assert '"/static/buscador.js"' in re.search(r"const CASCARON = \[(.*?)\];", sw, re.S).group(1)
    assert re.search(r'const VERSION = "([^"]+)"', sw).group(1) not in {f"tridata-cascaron-v{n}" for n in range(1, 6)}
