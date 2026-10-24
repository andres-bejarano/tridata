"""The assistant that acts (unit 021): proposing each action of the catalogue over a seeded temporary SQLite.

The code decides, Claude only understands: every `CambioInterpretado` is written here by hand (what a double of
Claude would return) and the card's «before», checks and candidates come from the base, with the same functions
and texts as the screens by hand. Plans `pedir-al-asistente` (REC-12): R1, R4-R7, R13, R14 of the unit ·
R-64, R-67..R-69, R-80, R-81, R-89, G-39, G-40. Every value is invented; Claude is never called.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta

import pytest

from tridata.ia import CambioInterpretado
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.nutricion.asistente import (
    Contexto, Peticion, buscar_apuntado, catalogo, confirmar, proponer, rehacer, texto_acciones,
)
from tridata.nutricion.recetas import Recetario

HOY = date(2026, 10, 22)  # a Thursday
AYER = HOY - timedelta(days=1)
MANANA = HOY + timedelta(days=1)


class Base:
    """The data of the contract: 8,000 default steps, 0.04 kcal per step, 2,300 kcal estimated today."""

    def __init__(self, tmp_path):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1980, "kcal_por_paso": 0.04, "pasos_por_defecto": 8000},
                             desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.pesos = RegistroPeso(self.db)
        self.recetario = Recetario(self.db)
        self.queso = self.recetario.crear_fijo({"nombre": "queso mozzarella de prueba", "kcal": 280, "por": "100g"})
        self.recetario.crear_fijo({"nombre": "manzana de prueba", "kcal": 80, "por": "unidad",
                                   "alias": ["guayaba de prueba"]})
        self.recetario.crear_olla({"nombre": "olla grande", "tara_g": 1500})
        for nombre in ("lentejas de prueba", "crema de prueba", "garbanzos de prueba"):
            self.recetario.crear_receta({"nombre": nombre, "ingredientes": [
                {"nombre": "harina de prueba", "cantidad": 400, "unidad": "g", "kcal": 1400}], "peso_neto_g": 1800},
                hoy=HOY)
        self.ctx = Contexto(registro=self.registro, objetivos=self.objetivos, ajustes=self.ajustes, pesos=self.pesos,
                            recetario=self.recetario, hoy=lambda: HOY)

    def apuntar(self, nombre, dia, cantidad, kcal, hora="12:00", unidad="g"):
        fila = self.registro.guardar([Alimento(nombre=nombre, dia=dia, cantidad=cantidad, unidad=unidad, kcal=kcal)],
                                     hoy=HOY, texto=nombre, sin_cambios=True)[0]
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE alimento_apuntado SET creado = ? WHERE id = ?", (f"{dia.isoformat()}T{hora}:00", fila["id"]))
        return fila["id"]

    def dia_de_prueba(self):
        self.yogur = self.apuntar("yogur natural de prueba", HOY, 125, 75, "10:00")
        self.pasta = self.apuntar("pasta de prueba", HOY, 100, 360, "14:00")
        self.apuntar("manzana de prueba", AYER, 1, 80, "11:00", unidad="unidad")

    def filas(self, tabla):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]  # noqa: S608 - fixed names


@pytest.fixture
def b(tmp_path):
    base = Base(tmp_path)
    base.dia_de_prueba()
    return base


def _pet(texto="texto de prueba", fecha=HOY):
    return Peticion(texto=texto, fecha=fecha)


def _uno(b, texto="texto de prueba", fecha=HOY, **campos):
    (resultado,) = proponer([CambioInterpretado(**campos)], b.ctx, _pet(texto, fecha))
    return resultado


# -- the catalogue and its lines for Claude ---------------------------------------------------------------------

def test_el_catalogo_de_esta_entrega():
    ids = set(catalogo())
    assert {"pasos_dia", "quitar_alimento", "guardar_ingrediente"} <= ids
    assert {"borrar_dia", "borrar_peso", "ollas", "datos_garmin", "entrenos_hechos", "cerrar_dia"} <= ids  # R-89
    texto = texto_acciones()
    for i in ids:
        assert f"- {i}:" in texto
    assert re.search(r"\d", texto) is None  # the fixed list carries no figure at all (G-38)


# -- R1 · R-64, C-82: the steps of a day -------------------------------------------------------------------------

def test_pasos_de_hoy_tarjeta_con_el_antes_de_la_base(b):  # R1
    t = _uno(b, "hoy en vez de 8 mil pasos son 15 mil", accion="pasos_dia", valor=15000, dia=HOY)
    assert t["tipo"] == "propuesta" and t["accion"] == "pasos_dia"
    assert t["texto"] == "Voy a cambiar los pasos previstos del 22-10: 8.000 → 15.000"
    assert t["donde"] == "solo ese día, los pasos por defecto siguen en 8.000"
    assert (t["antes"], t["despues"], t["dia"], t["error"]) == ("8.000", "15.000", "2026-10-22", None)
    assert "Gasto estimado del 22-10: 2.300 → 2.580 kcal" in t["lineas"]
    assert "En mantenimiento te quedan 280 kcal más" in t["lineas"]
    assert t["peticion"] == {"texto": "hoy en vez de 8 mil pasos son 15 mil", "fecha": "2026-10-22"}
    assert [c["clave"] for c in t["campos"]] == ["pasos", "dia"]
    assert b.objetivos.del_dia(HOY) is None and b.filas("tarjeta_asistente") == 0  # nothing written


def test_pasos_sin_dia_es_el_de_la_peticion_y_el_antes_es_el_del_dia(b):  # R1 · G-41
    b.objetivos.cambiar(MANANA, pasos=12000)
    t = _uno(b, accion="pasos_dia", valor=15000, dia=MANANA)
    assert t["texto"] == "Voy a cambiar los pasos previstos del 23-10: 12.000 → 15.000"
    t = _uno(b, fecha=AYER, accion="pasos_dia", valor=9000)  # a recording of yesterday, no day said
    assert t["dia"] == "2026-10-21" and t["texto"].startswith("Voy a cambiar los pasos previstos del 21-10:")


def test_pasos_de_un_dia_pasado_no(b):  # R13 · R-89
    r = _uno(b, accion="pasos_dia", valor=15000, dia=AYER)
    assert r == {"tipo": "no", "texto": "ese día ya pasó: sus pasos los trae Garmin"}


def test_pasos_fuera_de_rango_como_a_mano(b):  # R14 · G-39, C-100
    r = _uno(b, accion="pasos_dia", valor=80000, dia=HOY)
    assert r == {"tipo": "no", "texto": "los pasos previstos tienen que ser un número entero entre 0 y 50000"}


def test_pasos_iguales_no_propone_nada(b):
    r = _uno(b, accion="pasos_dia", valor=8000, dia=HOY)
    assert r["tipo"] == "no" and "ya son 8.000" in r["texto"]


# -- R6 · R-68, G-40: nothing half done ---------------------------------------------------------------------------

def test_lo_que_falta_no_propone_nada(b):  # R6 · C-86
    r = _uno(b, "cámbialo a 74", accion="otra", valor=74, falta="No sé qué quieres cambiar a 74")
    assert r == {"tipo": "no", "texto": "No sé qué quieres cambiar a 74"}


def test_una_sustitucion_a_medias_tampoco_quita(b):  # R6 · C-87, S-22
    r = _uno(b, "quita el yogur y pon lo otro", accion="quitar_alimento", objeto="yogur",
             falta="No sé qué poner en lugar del yogur")
    assert r == {"tipo": "no", "texto": "No sé qué poner en lugar del yogur"}
    assert len(b.registro.apuntes_del_dia(HOY)) == 2


# -- R7 · R-69: what is named is not there, or is there twice --------------------------------------------------------

def test_quitar_un_alimento_apuntado(b):  # R7, R1
    t = _uno(b, "quita el yogur", accion="quitar_alimento", objeto="yogur")
    assert t["tipo"] == "propuesta"
    assert t["texto"] == "Voy a quitar del 22-10: yogur natural de prueba 125 g (75 kcal), 10:00"
    assert t["parametros"]["id"] == b.yogur and t["dia"] == "2026-10-22"
    assert len(b.registro.apuntes_del_dia(HOY)) == 2


def test_quitar_lo_que_no_esta(b, tmp_path):  # R7 · C-89
    b.registro.borrar(b.yogur)
    r = _uno(b, "quita el yogur", accion="quitar_alimento", objeto="yogur")
    assert r == {"tipo": "no", "texto": "Hoy no tienes ningún yogur apuntado"}
    r = _uno(b, "quita la pasta de ayer", accion="quitar_alimento", objeto="pasta", dia=AYER)
    assert r == {"tipo": "no", "texto": "El 21-10 no tienes ningún pasta apuntado"}


def test_quitar_lo_de_otro_dia_lo_busca_en_ese_dia(b):  # R3 · the code looks for it in yesterday
    t = _uno(b, "quita la manzana de ayer", accion="quitar_alimento", objeto="manzana", dia=AYER)
    assert t["texto"] == "Voy a quitar del 21-10: manzana de prueba 1 unidad (80 kcal), 11:00"


def test_dos_candidatos_deja_elegir_con_su_hora(b):  # R7 · C-88
    otro = b.apuntar("yogur natural de prueba", HOY, 125, 75, "17:30")
    r = _uno(b, "quita el yogur", accion="quitar_alimento", objeto="yogur")
    assert r["tipo"] == "elegir" and r["accion"] == "quitar_alimento"
    assert [(c["eleccion"], c["texto"]) for c in r["candidatos"]] == [
        (b.yogur, "yogur natural de prueba 125 g (75 kcal), 10:00"),
        (otro, "yogur natural de prueba 125 g (75 kcal), 17:30")]
    t = rehacer(r, b.ctx, eleccion=otro)
    assert t["tipo"] == "propuesta" and t["parametros"]["id"] == otro
    assert t["texto"] == "Voy a quitar del 22-10: yogur natural de prueba 125 g (75 kcal), 17:30"
    assert rehacer(r, b.ctx, eleccion=b.pasta)["tipo"] == "no"  # not one of the candidates


def test_buscar_apuntado_sin_mayusculas_ni_tildes_y_por_trozo(b):
    assert [a["id"] for a in buscar_apuntado(b.ctx, HOY, "YOGÚR")] == [b.yogur]
    assert [a["id"] for a in buscar_apuntado(b.ctx, HOY, "pasta de prueba")] == [b.pasta]
    assert buscar_apuntado(b.ctx, HOY, "manzana") == []


# -- R4, R5 · R-67, R-80: an ingredient, new or corrected ------------------------------------------------------------

def test_corregir_un_ingrediente_con_el_antes_de_la_base(b):  # R4 · C-85
    t = _uno(b, "el queso mozzarella de prueba son 290 por cada 100", accion="guardar_ingrediente",
             objeto="queso mozzarella de prueba", valor=290, unidad="g")
    assert t["texto"] == "Voy a corregir queso mozzarella de prueba en Ingredientes: 280 → 290 kcal/100 g"
    assert (t["antes"], t["despues"]) == ("280 kcal/100 g", "290 kcal/100 g")
    assert t["parametros"]["id"] == b.queso["id"]
    assert next(f for f in b.recetario.fijos() if f["id"] == b.queso["id"])["kcal"] == 280


def test_corregir_por_alias(b):  # R4
    t = _uno(b, accion="guardar_ingrediente", objeto="Guayaba de prueba", valor=95, unidad="unidad")
    assert t["texto"] == "Voy a corregir manzana de prueba en Ingredientes: 80 → 95 kcal por unidad"


def test_ingrediente_nuevo(b):  # R5 · C-104, C-105
    t = _uno(b, accion="guardar_ingrediente", objeto="queso de prueba nuevo", valor=300, unidad="g")
    assert t["texto"] == "Voy a guardar: queso de prueba nuevo 300 kcal/100 g en Ingredientes"
    assert t["antes"] is None and t["despues"] == "300 kcal/100 g"
    t = _uno(b, accion="guardar_ingrediente", objeto="gel de prueba de naranja", valor=95, unidad="unidad")
    assert t["texto"] == "Voy a guardar: gel de prueba de naranja 95 kcal por unidad en Ingredientes"
    assert len(b.recetario.fijos()) == 2


def test_ingrediente_fuera_de_rango_o_con_nombre_de_otra_cosa(b):  # R14 · R-81, C-106, C-107
    r = _uno(b, accion="guardar_ingrediente", objeto="aceite de prueba", valor=9000, unidad="g")
    assert r == {"tipo": "no", "texto": "nada pasa de 900 kcal/100 g"}
    r = _uno(b, accion="guardar_ingrediente", objeto="olla grande", valor=50, unidad="g")
    assert r == {"tipo": "no", "texto": "«olla grande» ya es tu olla «olla grande»"}
    r = _uno(b, accion="guardar_ingrediente", objeto="Lentejas de prueba", valor=120, unidad="g")
    assert r == {"tipo": "no", "texto": "«lentejas de prueba» ya es tu receta «lentejas de prueba»"}
    assert len(b.recetario.ollas()) == 1 and len(b.recetario.fijos()) == 2


def test_ingrediente_sin_por_que_unidad_no_propone(b):  # R6 · G-40
    r = _uno(b, accion="guardar_ingrediente", objeto="queso de prueba nuevo", valor=300, unidad=None)
    assert r["tipo"] == "no" and "por 100 g o por unidad" in r["texto"]


# -- R13 · R-89: what the assistant does not change, and where it is done -------------------------------------------

@pytest.mark.parametrize("accion, donde", [
    ("borrar_dia", "Eso no lo cambio yo: en Nutrición › Día, vete a ese día y quita lo que sobre"),
    ("borrar_peso", "Balance"),
    ("ollas", "Recetas → Taras"),
    ("datos_garmin", "«Actualizar Garmin»"),
    ("entrenos_hechos", "«Actualizar Garmin»"),
    ("cerrar_dia", "«Cerrar el día»"),
    ("otra", "Ajustes"),
    ("mover_alimento", "aún no lo sé hacer"),  # of unit 022: not in this catalogue
])
def test_lo_que_no_hace_dice_donde(b, accion, donde):  # R13 · C-119
    r = _uno(b, "borra el día de ayer", accion=accion, dia=AYER)
    assert r["tipo"] == "no" and donde in r["texto"]
    assert len(b.registro.apuntes_del_dia(AYER)) == 1


# -- several at once, and a failure of one never hides the others --------------------------------------------------

def test_varios_cambios_una_tarjeta_por_cambio(b):  # R10 · R-72
    r = proponer([CambioInterpretado(accion="quitar_alimento", objeto="yogur"),
                  CambioInterpretado(accion="pasos_dia", valor=15000, dia=HOY)], b.ctx, _pet())
    assert [x["accion"] for x in r] == ["quitar_alimento", "pasos_dia"]
    assert all(x["tipo"] == "propuesta" for x in r)


# -- the bearing criterion at core level (R12 · R-78): confirming redoes the card with what is there now -----------

def test_confirmar_rehace_y_si_cambio_no_aplica(b):  # R12
    t = _uno(b, accion="pasos_dia", valor=15000, dia=HOY)
    b.objetivos.cambiar(HOY, pasos=12000)  # by hand, meanwhile
    with pytest.raises(Exception) as exc:
        confirmar(t, b.ctx)
    rehecha = exc.value.tarjeta
    assert rehecha["texto"] == "Voy a cambiar los pasos previstos del 22-10: 12.000 → 15.000"
    assert b.objetivos.del_dia(HOY)["pasos"] == 12000 and b.filas("tarjeta_asistente") == 0
