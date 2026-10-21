"""Recipes, fixed foods and pots (unit 011, `guardar-receta`): the rules over a temporary SQLite.

`Recetario` and its pure functions, without the web. Every value is invented (the C-n of the
plans): fixed foods garbanzo seco 350, aceite de oliva 900 and cebolla 40 kcal/100 g; the «olla
grande» weighs 1.500 g empty. Claude is never called: what it «said» is written by hand.
"""
from datetime import date, timedelta
from pathlib import Path

import pytest

from tridata.nutricion import Alimento, RegistroComidas
from tridata.nutricion.importar_deficit import filas_de_recetas
from tridata.nutricion.recetas import (
    Recetario, YaEsReceta, estado_version, kcal_100g, leer_cargas, normalizar,
)

HOY = date(2026, 10, 21)  # a Wednesday
MARTES = HOY - timedelta(days=1)
JUEVES = HOY + timedelta(days=1)
VIERNES = HOY + timedelta(days=2)
FIXTURE = Path(__file__).parent / "fixtures" / "recetas_inventado.md"


def ing(nombre, cantidad, kcal=None, unidad="g", **extra):
    return {"nombre": nombre, "cantidad": cantidad, "unidad": unidad, "kcal": kcal, "estimado": True,
            "dudoso": False, **extra}


GARBANZOS = [ing("garbanzo seco", 600, 2000), ing("aceite de oliva", 50, 400), ing("cebolla", 125, 60)]


def dicho(nombre, cantidad, unidad="g", kcal=999.0, guardado=None, coincidencia=None, dia=HOY):
    """An interpreted food as the web passes it to `resolver` (the JSON of AlimentoInterpretado)."""
    return {"nombre": nombre, "cantidad": cantidad, "unidad": unidad, "dia": dia.isoformat(), "kcal": kcal,
            "proteina_g": None, "hidratos_g": None, "grasa_g": None, "estimado": True, "dudoso": False,
            "supuestos": ["supuesto de Claude"], "guardado": guardado, "coincidencia": coincidencia}


@pytest.fixture
def rec(tmp_path):
    r = Recetario(tmp_path / "data.sqlite3")
    r.crear_fijo({"nombre": "garbanzo seco", "kcal": 350, "por": "100g"})
    r.crear_fijo({"nombre": "aceite de oliva", "kcal": 900, "por": "100g"})
    r.crear_fijo({"nombre": "cebolla", "kcal": 40, "por": "100g"})
    r.crear_olla({"nombre": "olla grande", "tara_g": 1500})
    return r


def _olla(rec, nombre):
    return next(o for o in rec.ollas() if o["nombre"] == nombre)["id"]


def _fijo(rec, nombre):
    return next(f for f in rec.fijos() if f["nombre"] == nombre)["id"]


def _garbanzos(rec, lleno=3500, **extra):
    return rec.crear_receta({"nombre": "garbanzos de la abuela", "ingredientes": GARBANZOS,
                             "olla_id": _olla(rec, "olla grande"), "peso_lleno_g": lleno, **extra}, hoy=MARTES)


def _lasana(rec, **extra):
    return rec.crear_receta({"nombre": "lasaña de la tía", "ingredientes": [
        ing("pasta de lasaña", 400, 1400), ing("carne picada", 800, 2000), ing("tomate frito", 500, 600)],
        **extra}, hoy=MARTES)


# -- pure functions ---------------------------------------------------------------------------

def test_normalizar_sin_mayusculas_tildes_ni_espacios_de_mas():
    assert normalizar("  Lasaña  de la TÍA ") == "lasana de la tia"
    assert normalizar("Guayaba") == normalizar("guayaba")


def test_kcal_100g_con_olla_o_neto_y_un_decimal():  # R2, Q-9
    assert kcal_100g(2600, lleno=3500, tara=1500) == 130.0
    assert kcal_100g(2600, neto=2000) == 130.0
    assert kcal_100g(1000, neto=3000) == 33.3
    with pytest.raises(ValueError, match="la olla llena no puede pesar menos que su tara"):
        kcal_100g(2600, lleno=1400, tara=1500)
    with pytest.raises(ValueError, match="la olla llena no puede pesar menos que su tara"):
        kcal_100g(2600, lleno=1500, tara=1500)


@pytest.mark.parametrize("version, estado", [
    ({"kcal_total": 2600, "kcal_100g": 130.0, "dudosos": 0}, "definitiva"),
    ({"kcal_total": 4000, "kcal_100g": None, "dudosos": 0}, "provisional"),
    ({"kcal_total": 80, "kcal_100g": None, "dudosos": 1}, "sin_kcal"),
])
def test_estado_de_la_receta_g32(version, estado):  # R6, R9
    assert estado_version(version) == estado


# -- R1, R2, R8, R9: saving a recipe ----------------------------------------------------------

def test_suma_con_los_fijos_aunque_la_pagina_diga_otras_kcal(rec):  # R1
    receta = _garbanzos(rec)
    v = receta["en_uso"]
    assert v["kcal_total"] == 2600
    assert [(i["nombre"], i["kcal"], i["estado"]) for i in v["ingredientes"]] == [
        ("garbanzo seco", 2100, "guardado"), ("aceite de oliva", 450, "guardado"), ("cebolla", 50, "guardado")]


def test_pesada_con_la_olla_grande_queda_definitiva(rec):  # R2
    v = _garbanzos(rec)["en_uso"]
    assert (v["peso_neto_g"], v["kcal_100g"], v["estado"], v["olla"]) == (2000, 130.0, "definitiva", "olla grande")


def test_olla_pequena_y_peso_neto_directo(rec):  # R2, C-55
    rec.crear_olla({"nombre": "olla pequeña", "tara_g": 1200})
    lentejas = rec.crear_receta({"nombre": "lentejas de prueba", "ingredientes": [ing("lentejas", 400, 1400)],
                                 "olla_id": _olla(rec, "olla pequeña"), "peso_lleno_g": 3000}, hoy=HOY)
    assert lentejas["en_uso"]["peso_neto_g"] == 1800
    sin_olla = rec.crear_receta({"nombre": "arroz de prueba", "ingredientes": [ing("arroz", 300, 1000)],
                                 "peso_neto_g": 900}, hoy=HOY)
    assert sin_olla["en_uso"]["kcal_100g"] == 111.1
    assert sin_olla["en_uso"]["olla"] is None
    assert {o["nombre"] for o in rec.ollas()} == {"olla grande", "olla pequeña"}  # R-50: every pot to choose


def test_un_ingrediente_estimado_sigue_pudiendo_ser_definitiva(rec):  # R8
    receta = _garbanzos(rec, ingredientes=GARBANZOS + [ing("pimentón de la Vera", 5, 15)])
    v = receta["en_uso"]
    pimenton = v["ingredientes"][-1]
    assert (pimenton["kcal"], pimenton["estado"]) == (15, "estimado")
    assert v["estimados"] == 1 and v["estado"] == "definitiva"
    assert v["kcal_total"] == 2615


def test_un_ingrediente_dudoso_deja_la_receta_sin_kcal(rec):  # R9
    receta = rec.crear_receta({"nombre": "sofrito de prueba", "ingredientes": [
        ing("cebolla", 200, 70), ing("kfjd", 30, None, dudoso=True)], "peso_neto_g": 180}, hoy=HOY)
    v = receta["en_uso"]
    assert [(i["nombre"], i["kcal"], i["estado"]) for i in v["ingredientes"]] == [
        ("cebolla", 80, "guardado"), ("kfjd", None, "dudoso")]
    assert v["estado"] == "sin_kcal" and v["kcal_100g"] is None and v["dudosos"] == 1
    r = rec.resolver(dicho("sofrito de prueba", 100, guardado="sofrito de prueba", coincidencia="exacta"))
    assert r["dudoso"] is True and r["kcal"] is None
    assert any("corrige la receta" in s for s in r["supuestos"])
    # corrected (kfjd removed) it is usable
    rec.corregir(receta["id"], {"ingredientes": [ing("cebolla", 200, 70)], "peso_neto_g": 180})
    assert rec.resolver(dicho("sofrito de prueba", 90, guardado="sofrito de prueba", coincidencia="exacta"))["kcal"] == 40


# -- R3, R4, R6, D1, D6: resolving what was dictated -------------------------------------------

def test_exacta_usa_la_receta_sin_estimar(rec):  # R3 (criterio portante)
    _garbanzos(rec)
    r = rec.resolver(dicho("garbanzos de la abuela", 250, kcal=400, guardado="garbanzos de la abuela",
                           coincidencia="exacta"))
    assert (r["nombre"], r["cantidad"], r["unidad"], r["kcal"]) == ("garbanzos de la abuela", 250, "g", 325)
    assert r["estimado"] is False and r["dudoso"] is False and r["provisional"] is False
    assert r["origen"].startswith("de tu receta «garbanzos de la abuela»")
    assert r["supuestos"] == [] and r["propuesta"] is None


def test_el_nombre_tal_cual_es_exacta_aunque_claude_no_lo_marque(rec):  # D1
    _garbanzos(rec)
    r = rec.resolver(dicho("Garbanzos de la Abuela", 100))
    assert r["kcal"] == 130 and r["origen"].startswith("de tu receta")


def test_alimento_fijo_por_unidad_con_alias(rec):  # R3, R-49, C-54
    rec.crear_fijo({"nombre": "dulce de guayaba de ciclismo", "kcal": 90, "por": "unidad", "alias": ["guayaba"],
                    "nota": "no la fruta"})
    r = rec.resolver(dicho("guayaba", 2, unidad="unidad", kcal=120, guardado="guayaba", coincidencia="exacta"))
    assert (r["nombre"], r["cantidad"], r["kcal"], r["estimado"]) == ("dulce de guayaba de ciclismo", 2, 180, False)
    assert r["origen"] == "de tu alimento fijo «dulce de guayaba de ciclismo»"


def test_lo_desconocido_se_estima_como_siempre(rec):  # R4, C-45
    _garbanzos(rec)
    entrada = dicho("lentejas de la vecina", 300, kcal=350)
    r = rec.resolver(entrada)
    assert (r["kcal"], r["estimado"], r["origen"], r["propuesta"]) == (350, True, None, None)
    assert r["supuestos"] == ["supuesto de Claude"]


def test_lo_parecido_se_propone_a_la_vista(rec):  # R4, C-46
    _garbanzos(rec)
    r = rec.resolver(dicho("garbanzos", 200, kcal=250, guardado="garbanzos de la abuela", coincidencia="parecida"))
    assert (r["nombre"], r["kcal"], r["estimado"]) == ("garbanzos de la abuela", 260, False)
    assert r["origen"].startswith("de tu receta «garbanzos de la abuela»")
    assert r["propuesta"] == {"dicho": "garbanzos", "guardado": "garbanzos de la abuela"}
    assert r["kcal_estimada"] == 250
    assert any("garbanzos" in s and "garbanzos de la abuela" in s for s in r["supuestos"])


def test_un_guardado_que_no_esta_en_la_lista_se_ignora(rec):  # R4
    r = rec.resolver(dicho("tortilla", 150, kcal=300, guardado="tortilla de la luna", coincidencia="exacta"))
    assert (r["nombre"], r["kcal"], r["estimado"], r["origen"]) == ("tortilla", 300, True, None)


def test_lasana_sin_pesar_por_fracciones_o_aproximadas(rec):  # R6, C-48, C-49, C-50
    lasana = _lasana(rec)
    assert lasana["en_uso"]["kcal_total"] == 4000 and lasana["en_uso"]["estado"] == "provisional"
    cuarto = rec.resolver(dicho("lasaña de la tía", 0.25, unidad="receta", guardado="lasaña de la tía",
                                coincidencia="exacta"))
    assert (cuarto["kcal"], cuarto["provisional"], cuarto["estimado"]) == (1000, True, False)
    sin_aprox = rec.resolver(dicho("lasaña de la tía", 300, guardado="lasaña de la tía", coincidencia="exacta"))
    assert sin_aprox["dudoso"] is True and sin_aprox["kcal"] is None
    assert any("fracción" in s and "kcal/100 g" in s for s in sin_aprox["supuestos"])
    rec.corregir(lasana["id"], {"kcal_100g_aprox": 200})
    gramos = rec.resolver(dicho("lasaña de la tía", 300, guardado="lasaña de la tía", coincidencia="exacta"))
    assert (gramos["kcal"], gramos["provisional"], gramos["dudoso"]) == (600, True, False)


def test_una_fraccion_de_una_definitiva_no_es_provisional(rec):  # R6
    _garbanzos(rec)
    r = rec.resolver(dicho("garbanzos de la abuela", 0.5, unidad="receta", guardado="garbanzos de la abuela",
                           coincidencia="exacta"))
    assert (r["kcal"], r["provisional"]) == (1300, False)


def test_unidad_que_no_encaja_se_queda_estimada(rec):  # D6
    rec.crear_fijo({"nombre": "dulce de guayaba de ciclismo", "kcal": 90, "por": "unidad", "alias": ["guayaba"]})
    en_gramos = rec.resolver(dicho("guayaba", 50, kcal=130, guardado="guayaba", coincidencia="exacta"))
    assert (en_gramos["kcal"], en_gramos["estimado"], en_gramos["origen"]) == (130, True, None)
    assert any("por unidad" in s for s in en_gramos["supuestos"])
    en_unidades = rec.resolver(dicho("garbanzo seco", 2, unidad="unidad", kcal=10, guardado="garbanzo seco",
                                     coincidencia="exacta"))
    assert (en_unidades["kcal"], en_unidades["estimado"]) == (10, True)
    assert any("por 100 g: dilo en gramos" in s for s in en_unidades["supuestos"])


def test_nombres_solo_de_recetas_y_fijos_que_se_pueden_usar(rec):  # R5, D7
    rec.crear_fijo({"nombre": "dulce de guayaba de ciclismo", "kcal": 90, "por": "unidad", "alias": ["guayaba"]})
    _garbanzos(rec)
    nombres = rec.nombres()
    assert set(nombres) == {"garbanzo seco", "aceite de oliva", "cebolla", "dulce de guayaba de ciclismo", "guayaba",
                            "garbanzos de la abuela"}
    assert "olla grande" not in nombres


# -- R7, R10: versions and the past untouched ----------------------------------------------------

def _apuntar(tmp_path, rec, texto_dia, nombre, cantidad, unidad="g"):
    """What /api/confirmar saves: the resolved food, with its origin."""
    registro = RegistroComidas(tmp_path / "data.sqlite3")
    r = rec.resolver(dicho(nombre, cantidad, unidad=unidad, guardado=nombre, coincidencia="exacta", dia=texto_dia))
    alimento = Alimento.desde_dict(r)
    return registro, registro.guardar([alimento], hoy=texto_dia, texto=nombre, sin_cambios=True)[0]


def test_otra_tanda_se_usa_desde_ya_y_el_martes_no_cambia(tmp_path, rec):  # R7, C-51
    receta = _garbanzos(rec)
    registro, martes = _apuntar(tmp_path, rec, MARTES, "garbanzos de la abuela", 250)
    assert martes["kcal"] == 325 and martes["origen"].startswith("de tu receta")
    tanda = rec.otra_tanda(receta["id"], {"ingredientes": [ing("garbanzo seco", 500, 1), ing("aceite de oliva", 50, 1)],
                                          "olla_id": _olla(rec, "olla grande"), "peso_lleno_g": 3500}, hoy=JUEVES)
    assert tanda["en_uso"]["numero"] == 2 and tanda["en_uso"]["kcal_total"] == 2200
    assert tanda["en_uso"]["kcal_100g"] == 110.0
    assert [v["numero"] for v in tanda["versiones"]] == [1, 2]
    viernes = rec.resolver(dicho("garbanzos de la abuela", 250, guardado="garbanzos de la abuela", coincidencia="exacta",
                                 dia=VIERNES))
    assert viernes["kcal"] == 275 and "(tanda 2)" in viernes["origen"]
    assert registro.apuntes_del_dia(MARTES)[0]["kcal"] == 325
    rec.usar_version(receta["id"], 1)
    otra_vez = rec.resolver(dicho("garbanzos de la abuela", 250, guardado="garbanzos de la abuela",
                                  coincidencia="exacta"))
    assert otra_vez["kcal"] == 325 and "(tanda 1)" in otra_vez["origen"]
    with pytest.raises(KeyError):
        rec.usar_version(receta["id"], 7)


def test_corregir_y_borrar_no_tocan_lo_apuntado(tmp_path, rec):  # R10, C-62
    receta = _garbanzos(rec)
    registro, martes = _apuntar(tmp_path, rec, MARTES, "garbanzos de la abuela", 250)
    corregida = rec.corregir(receta["id"], {"ingredientes": [ing("garbanzo seco", 600), ing("aceite de oliva", 72.2),
                                                             ing("cebolla", 125)],
                                            "olla_id": _olla(rec, "olla grande"), "peso_lleno_g": 3500})
    assert corregida["en_uso"]["numero"] == 1 and corregida["en_uso"]["kcal_100g"] == 140.0
    with pytest.raises(ValueError):
        rec.borrar_receta(receta["id"])  # without confirming, nothing is deleted (R14)
    assert rec.receta(receta["id"])["nombre"] == "garbanzos de la abuela"
    rec.borrar_receta(receta["id"], confirmado=True)
    assert rec.recetas() == [] and "garbanzos de la abuela" not in rec.nombres()
    fila = registro.apuntes_del_dia(MARTES)[0]
    assert (fila["kcal"], fila["origen"]) == (325, martes["origen"])
    with pytest.raises(KeyError):
        rec.receta(receta["id"])


def test_borrar_fijo_y_olla_pide_confirmar(rec):  # R14
    fijo, olla = _fijo(rec, "cebolla"), _olla(rec, "olla grande")
    with pytest.raises(ValueError):
        rec.borrar_fijo(fijo)
    with pytest.raises(ValueError):
        rec.borrar_olla(olla)
    assert len(rec.fijos()) == 3 and len(rec.ollas()) == 1
    rec.borrar_fijo(fijo, confirmado=True)
    rec.borrar_olla(olla, confirmado=True)
    assert len(rec.fijos()) == 2 and rec.ollas() == []


# -- R12, R13: one name one thing, sensible values ------------------------------------------------

def test_un_alias_ya_usado_no_se_guarda(rec):  # R12, C-56
    rec.crear_fijo({"nombre": "dulce de guayaba de ciclismo", "kcal": 90, "por": "unidad", "alias": ["guayaba"]})
    with pytest.raises(ValueError) as error:
        rec.crear_fijo({"nombre": "Guayaba", "kcal": 68, "por": "100g"})
    assert "«guayaba» ya es" in str(error.value).lower() and "dulce de guayaba de ciclismo" in str(error.value)
    assert len(rec.fijos()) == 4
    with pytest.raises(ValueError, match="ya es"):
        rec.crear_olla({"nombre": "Cebolla", "tara_g": 500})
    with pytest.raises(ValueError, match="ya es"):
        rec.crear_fijo({"nombre": "pan de prueba", "kcal": 250, "por": "100g", "alias": ["OLLA GRANDE"]})


def test_el_nombre_de_una_receta_ofrece_otra_tanda(rec):  # R12
    receta = _garbanzos(rec)
    with pytest.raises(YaEsReceta) as error:
        rec.crear_receta({"nombre": "GARBANZOS de la Abuéla", "ingredientes": GARBANZOS}, hoy=HOY)
    assert error.value.receta_id == receta["id"]
    assert "otra tanda" in str(error.value)
    assert len(rec.recetas()) == 1 and len(rec.receta(receta["id"])["versiones"]) == 1


@pytest.mark.parametrize("hacer, motivo", [
    (lambda r: r.crear_fijo({"nombre": "manteca de prueba", "kcal": 1500, "por": "100g"}),
     "nada pasa de 900 kcal/100 g"),
    (lambda r: r.crear_fijo({"nombre": "gel de prueba", "kcal": -5, "por": "unidad"}), "negativ"),
    (lambda r: r.crear_olla({"nombre": "táper de prueba", "tara_g": 0}), "tara"),
    (lambda r: r.crear_olla({"nombre": "táper de prueba", "tara_g": -20}), "tara"),
    (lambda r: _garbanzos(r, lleno=1400), "la olla llena no puede pesar menos que su tara"),
    (lambda r: _garbanzos(r, kcal_100g_aprox=1200), "900"),
])
def test_valores_no_razonables_no_se_guardan(rec, hacer, motivo):  # R13, C-57, C-58
    antes = (rec.recetas(), rec.fijos(), rec.ollas())
    with pytest.raises(ValueError, match=motivo):
        hacer(rec)
    assert (rec.recetas(), rec.fijos(), rec.ollas()) == antes


# -- R18: editing a fixed food or a pot -------------------------------------------------------------

def test_editar_fijo_vale_desde_ya_y_no_cambia_lo_apuntado(tmp_path, rec):  # R18
    rec.crear_fijo({"nombre": "dulce de guayaba de ciclismo", "kcal": 90, "por": "unidad", "alias": ["guayaba"]})
    registro, ayer = _apuntar(tmp_path, rec, MARTES, "dulce de guayaba de ciclismo", 2, unidad="unidad")
    assert ayer["kcal"] == 180
    guayaba = _fijo(rec, "dulce de guayaba de ciclismo")
    cambiado = rec.cambiar_fijo(guayaba, {"nombre": "dulce de guayaba de ciclismo", "kcal": 95, "por": "unidad",
                                          "alias": ["guayaba"], "nota": "no la fruta"})
    assert (cambiado["kcal"], cambiado["nota"]) == (95, "no la fruta")
    hoy = rec.resolver(dicho("guayaba", 2, unidad="unidad", guardado="guayaba", coincidencia="exacta"))
    assert hoy["kcal"] == 190
    assert registro.apuntes_del_dia(MARTES)[0]["kcal"] == 180


def test_editar_fijo_con_valores_malos_no_cambia_nada(rec):  # R18
    rec.crear_fijo({"nombre": "dulce de guayaba de ciclismo", "kcal": 90, "por": "unidad", "alias": ["guayaba"]})
    guayaba = _fijo(rec, "dulce de guayaba de ciclismo")
    antes = rec.fijos()
    for datos in ({"nombre": "dulce de guayaba de ciclismo", "kcal": 1500, "por": "100g"},
                  {"nombre": "dulce de guayaba de ciclismo", "kcal": 90, "por": "unidad", "alias": ["cebolla"]},
                  {"nombre": "aceite de oliva", "kcal": 90, "por": "unidad"}):
        with pytest.raises(ValueError):
            rec.cambiar_fijo(guayaba, datos)
    assert rec.fijos() == antes
    with pytest.raises(KeyError):
        rec.cambiar_fijo(999, {"nombre": "x", "kcal": 1, "por": "unidad"})


def test_editar_olla_no_recalcula_lo_pesado(rec):  # R18
    receta = _garbanzos(rec)
    olla = _olla(rec, "olla grande")
    with pytest.raises(ValueError, match="tara"):
        rec.cambiar_olla(olla, {"nombre": "olla grande", "tara_g": 0})
    assert rec.cambiar_olla(olla, {"nombre": "olla grande", "tara_g": 1600})["tara_g"] == 1600
    assert rec.receta(receta["id"])["en_uso"]["kcal_100g"] == 130.0
    tanda = rec.otra_tanda(receta["id"], {"ingredientes": GARBANZOS, "olla_id": olla, "peso_lleno_g": 3600}, hoy=HOY)
    assert tanda["en_uso"]["peso_neto_g"] == 2000 and tanda["en_uso"]["tara_g"] == 1600
    assert tanda["versiones"][0]["tara_g"] == 1500


# -- R15: loading what deficit.md had -----------------------------------------------------------------

FILA_RARA = "| Algo de prueba que no encaja | media bandeja | ~300 kcal |"


def _cargas(texto):
    filas = filas_de_recetas(texto)
    return leer_cargas(filas.referencias, filas.taras)


def test_leer_las_filas_del_fixture():  # R15
    cargas = _cargas(FIXTURE.read_text(encoding="utf-8"))
    por_nombre = {c.nombre: c for c in cargas}
    potaje = por_nombre["Potaje de prueba"]
    assert (potaje.tipo, potaje.kcal_total, potaje.peso_neto_g, potaje.kcal_100g) == ("receta", 1320, 1200, 110.0)
    assert "garbanzo 300g" in potaje.nota
    bizcocho = por_nombre["Bizcocho de prueba"]
    assert (bizcocho.tipo, bizcocho.kcal_total, bizcocho.kcal_100g) == ("receta", 2400, None)
    barrita = por_nombre["Barrita de prueba"]
    assert (barrita.tipo, barrita.kcal, barrita.por) == ("fijo", 190, "unidad")
    olla = por_nombre["Olla de prueba roja"]
    assert (olla.tipo, olla.tara_g) == ("olla", 1300)


def test_cargar_de_deficit_una_vez(tmp_path):  # R15, C-60
    rec = Recetario(tmp_path / "data.sqlite3")
    rec.crear_olla({"nombre": "olla de prueba ROJA", "tara_g": 1111})  # what the app has wins
    texto = FIXTURE.read_text(encoding="utf-8").replace("| Barrita de prueba |", FILA_RARA + "\n| Barrita de prueba |")
    cargas = _cargas(texto)
    simulado = rec.cargar_de_deficit(cargas, hoy=HOY, simular=True)
    assert rec.recetas() == [] and rec.fijos() == []
    assert sorted(estado for _, estado in simulado) == ["nueva", "nueva", "nueva", "nueva", "ya estaba"]

    rec.cargar_de_deficit(cargas, hoy=HOY)
    recetas = {r["nombre"]: r for r in rec.recetas()}
    assert recetas["Potaje de prueba"]["en_uso"]["estado"] == "definitiva"
    assert recetas["Potaje de prueba"]["en_uso"]["kcal_100g"] == 110.0
    assert recetas["Bizcocho de prueba"]["en_uso"]["estado"] == "provisional"
    assert recetas["Algo de prueba que no encaja"]["por_revisar"] is True
    assert [f["nombre"] for f in rec.fijos()] == ["Barrita de prueba"]
    assert [(o["nombre"], o["tara_g"]) for o in rec.ollas()] == [("olla de prueba ROJA", 1111)]
    assert "Algo de prueba que no encaja" not in rec.nombres()  # por revisar: not used when dictating
    assert rec.resolver(dicho("algo de prueba que no encaja", 100, kcal=50))["kcal"] == 50

    segunda = rec.cargar_de_deficit(cargas, hoy=HOY)
    assert all(estado == "ya estaba" for _, estado in segunda)
    assert len(rec.recetas()) == 3 and len(rec.fijos()) == 1 and len(rec.ollas()) == 1
    # the one to review is used once Andrés opens it and saves it
    rara = recetas["Algo de prueba que no encaja"]
    rec.corregir(rara["id"], {"ingredientes": [ing("cosa de prueba", 300, 300)], "peso_neto_g": 300})
    assert "Algo de prueba que no encaja" in rec.nombres()
