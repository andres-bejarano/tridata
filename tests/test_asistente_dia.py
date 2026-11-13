"""The assistant changes what was logged and the plan of a day (unit 022, REC-13 of `pedir-al-asistente`).

Substitute a food or correct its quantity (one action, one write, G-40), move a food to another day, the levels,
the % and the planned training of a day, and the «you had X, not Y» warning (R-77). Unit level over the catalogue
with a seeded temporary SQLite, and confirmation with Starlette's TestClient against the routes of unit 021.
Every `CambioInterpretado` is written by hand (what a double of Claude would return); Claude is never called.
Plans: R1-R8, R12 of the unit · R-74..R-77, R-79, G-39, G-40. Every value is invented.

Today is Thursday 2026-10-22. Logged today: «yogur natural de prueba» 125 g (75 kcal), «pasta de prueba» 100 g
(360 kcal), «plátano de prueba» 120 g (108 kcal) and «arroz de prueba» 200 g (457 kcal): 1,000 kcal. Ingredient
«manzana de prueba» 80 kcal per unit. Plan −15 %, weight target 72 kg for 2027-02-01. Sunday 2026-10-18: 60 min of
«Bici suave», 480 kcal.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from tridata.conversaciones.cambios import CambioInterpretado
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.nutricion.asistente import (
    Cambiado, Contexto, Peticion, catalogo, confirmar, proponer, rehacer, texto_acciones,
)
from tridata.nutricion.recetas import Recetario

HOY = date(2026, 10, 22)  # a Thursday
AYER = HOY - timedelta(days=1)
MANANA = HOY + timedelta(days=1)
MARTES = HOY - timedelta(days=2)
DOMINGO = date(2026, 10, 18)
BICI = {"activity_id": "prueba-bici", "activity_date": DOMINGO.isoformat(), "activity_type": "cycling",
        "name": "Bici suave", "duration_seconds": 3600, "calories": 480}


class Base:
    def __init__(self, tmp_path):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1980, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000,
                              "objetivo_pct": -15, "limite_saludable_pct": 25, "altura_cm": 175,
                              "objetivo_peso": {"kg": 72, "fecha": "2027-02-01"}}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.pesos = RegistroPeso(self.db)
        self.recetario = Recetario(self.db)
        self.recetario.crear_fijo({"nombre": "manzana de prueba", "kcal": 80, "por": "unidad"})
        self.recientes = [BICI]
        self.ctx = Contexto(registro=self.registro, objetivos=self.objetivos, ajustes=self.ajustes, pesos=self.pesos,
                            recetario=self.recetario, hoy=lambda: HOY, recientes=lambda: self.recientes)
        self.yogur = self.apuntar("yogur natural de prueba", HOY, 125, 75, "10:00")
        self.pasta = self.apuntar("pasta de prueba", HOY, 100, 360, "14:00")
        self.platano = self.apuntar("plátano de prueba", HOY, 120, 108, "17:30")
        self.apuntar("arroz de prueba", HOY, 200, 457, "21:00")

    def apuntar(self, nombre, dia, cantidad, kcal, hora="12:00", unidad="g"):
        fila = self.registro.guardar([Alimento(nombre=nombre, dia=dia, cantidad=cantidad, unidad=unidad, kcal=kcal)],
                                     hoy=HOY, texto=nombre, sin_cambios=True)[0]
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE alimento_apuntado SET creado = ? WHERE id = ?", (f"{dia.isoformat()}T{hora}:00", fila["id"]))
        return fila["id"]

    def total(self, dia=HOY):
        return round(sum(a["kcal"] or 0 for a in self.registro.apuntes_del_dia(dia)))

    def tarjetas(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT COUNT(*) FROM tarjeta_asistente").fetchone()[0]


@pytest.fixture
def b(tmp_path):
    return Base(tmp_path)


def _uno(b, dicho="texto de prueba", pedido=HOY, **campos):
    (resultado,) = proponer([CambioInterpretado(**campos)], b.ctx, Peticion(texto=dicho, fecha=pedido))
    return resultado


# -- the catalogue ------------------------------------------------------------------------------------------------

def test_el_catalogo_gana_las_acciones_del_dia():
    ids = set(catalogo())
    assert {"cambiar_alimento", "mover_alimento", "niveles_dia", "objetivo_dia", "entreno_previsto"} <= ids
    assert {"pasos_dia", "quitar_alimento"} <= ids  # unit 021 stays
    texto = texto_acciones()
    for i in ("cambiar_alimento", "mover_alimento", "niveles_dia", "objetivo_dia", "entreno_previsto"):
        assert f"- {i}:" in texto


# -- R1 · R-74, C-94: substitute, one action ----------------------------------------------------------------------

def test_sustituir_por_algo_guardado_una_sola_accion(b):  # R1 (the bearing criterion)
    t = _uno(b, "quita el yogur, al final fue una manzana", accion="cambiar_alimento", objeto="yogur",
             texto="manzana de prueba", cantidad=1, unidad="unidad", kcal_estimadas=95)
    assert t["tipo"] == "propuesta" and t["accion"] == "cambiar_alimento"
    assert t["texto"] == ("Voy a cambiar en el 22-10: yogur natural de prueba 125 g (75 kcal) → "
                          "manzana de prueba 1 unidad (80 kcal), de tu ingrediente")
    assert (t["antes"], t["despues"], t["dia"]) == (
        "yogur natural de prueba 125 g (75 kcal)", "manzana de prueba 1 unidad (80 kcal)", "2026-10-22")
    assert b.registro.alimento(b.yogur)["nombre"] == "yogur natural de prueba" and b.tarjetas() == 0  # nothing yet
    hecho = confirmar(t, b.ctx)
    assert "manzana de prueba" in hecho["hecho"]
    fila = b.registro.alimento(b.yogur)  # the same row, changed: never removed and added in two writes
    assert (fila["nombre"], fila["cantidad"], fila["unidad"], fila["kcal"]) == ("manzana de prueba", 1, "unidad", 80)
    assert fila["origen"].startswith("de tu ingrediente") and fila["estimado"] is False and fila["estado"] == "comido"
    assert [a["nombre"] for a in b.registro.apuntes_del_dia(HOY)].count("yogur natural de prueba") == 0
    assert b.total() == 1005


def test_sustituir_por_algo_no_guardado_es_estimado(b):  # R1
    t = _uno(b, "quita el yogur, al final fue una pera de prueba", accion="cambiar_alimento", objeto="yogur",
             texto="pera de prueba", cantidad=1, unidad="unidad", kcal_estimadas=85)
    assert t["texto"] == ("Voy a cambiar en el 22-10: yogur natural de prueba 125 g (75 kcal) → "
                          "pera de prueba 1 unidad (85 kcal), estimado")
    confirmar(t, b.ctx)
    fila = b.registro.alimento(b.yogur)
    assert (fila["nombre"], fila["kcal"], fila["estimado"], fila["origen"]) == ("pera de prueba", 85, True, None)
    assert b.total() == 1010


def test_sustituir_por_algo_no_guardado_sin_kcal_no_propone(b):  # G-40
    r = _uno(b, accion="cambiar_alimento", objeto="yogur", texto="pera de prueba", cantidad=1, unidad="unidad")
    assert r["tipo"] == "no" and "pera de prueba" in r["texto"]


def test_cambiar_lo_que_no_esta(b):
    r = _uno(b, accion="cambiar_alimento", objeto="lentejas", cantidad=200, unidad="g")
    assert r == {"tipo": "no", "texto": "Hoy no tienes ningún lentejas apuntado"}


# -- R2 · R-74, C-95: the quantity ----------------------------------------------------------------------------------

def test_cambiar_la_cantidad(b):  # R2
    t = _uno(b, "la pasta eran 120 gramos, no 100", accion="cambiar_alimento", objeto="pasta", cantidad=120,
             unidad="g", antes_dicho="100")
    assert t["texto"] == "Voy a cambiar pasta de prueba del 22-10: 100 g (360 kcal) → 120 g (432 kcal)"
    assert t["avisos"] == []  # what he said of before matches
    assert [c["clave"] for c in t["campos"]] == ["cantidad"]
    confirmar(t, b.ctx)
    fila = b.registro.alimento(b.pasta)
    assert (fila["cantidad"], fila["kcal"]) == (120, 432)
    assert b.total() == 1072


def test_corregir_la_cantidad_en_la_tarjeta(b):  # R2 · R-71
    t = _uno(b, accion="cambiar_alimento", objeto="pasta", cantidad=120, unidad="g")
    otra = rehacer(t, b.ctx, valores={"cantidad": 150})
    assert otra["texto"].endswith("→ 150 g (540 kcal)") and otra["corregida"] is True
    mala = rehacer(t, b.ctx, valores={"cantidad": -3})
    assert mala["error"] and "mayor que 0" in mala["error"]


# -- R3 · R-75, C-96: move ------------------------------------------------------------------------------------------

def test_mover_a_manana_queda_planeado(b):  # R3
    t = _uno(b, "el plátano pásalo a mañana", accion="mover_alimento", objeto="plátano", fecha=MANANA)
    assert t["texto"] == "Voy a mover plátano de prueba 120 g (108 kcal) del 22-10 al 23-10, como planeado"
    assert t["dia"] == "2026-10-22"
    confirmar(t, b.ctx)
    assert b.total() == 892
    (movido,) = b.registro.apuntes_del_dia(MANANA)
    assert (movido["id"], movido["estado"]) == (b.platano, "planeado")


def test_mover_a_ayer_queda_comido(b):  # R3
    t = _uno(b, accion="mover_alimento", objeto="plátano", fecha=AYER)
    assert t["texto"].endswith("del 22-10 al 21-10, como comido")
    confirmar(t, b.ctx)
    assert b.registro.alimento(b.platano)["estado"] == "comido"
    assert b.registro.alimento(b.platano)["dia"] == "2026-10-21"


def test_mover_al_mismo_dia_no(b):
    r = _uno(b, accion="mover_alimento", objeto="plátano", fecha=HOY)
    assert r["tipo"] == "no" and "ya está" in r["texto"]


# -- unit 075 · R-215, C-265, C-266: move a logged food to another section of Nutrición › día -------------------------

def test_seccion_pasa_el_mismo_alimento_a_carrera(b):  # 075 R1 (C-265)
    antes = b.registro.alimento(b.platano)
    t = _uno(b, "el plátano ponlo en nutrición en carrera", accion="seccion_alimento", objeto="plátano",
             texto="carrera")
    assert t["tipo"] == "propuesta" and t["accion"] == "seccion_alimento"
    assert t["texto"] == "Voy a pasar plátano de prueba 120 g (108 kcal) del 22-10 a Nutrición en carrera"
    confirmar(t, b.ctx)
    despues = b.registro.alimento(b.platano)
    assert despues["seccion"] == "carrera"
    assert {k: despues[k] for k in ("id", "dia", "kcal", "cantidad", "unidad", "estado")} == \
        {k: antes[k] for k in ("id", "dia", "kcal", "cantidad", "unidad", "estado")}
    assert b.total() == 1000  # nothing removed nor logged again


@pytest.mark.parametrize(("dicho", "clave", "nombre"), [
    ("Nutrición en carrera", "carrera", "Nutrición en carrera"),
    ("media mañana", "media_manana", "Media mañana"),
    ("media_manana", "media_manana", "Media mañana"),
    ("Desayuno", "desayuno", "Desayuno"),
])
def test_seccion_acepta_el_nombre_que_se_ve(b, dicho, clave, nombre):  # 075 R1
    t = _uno(b, accion="seccion_alimento", objeto="yogur", texto=dicho)
    assert t["texto"].endswith(f"del 22-10 a {nombre}")
    confirmar(t, b.ctx)
    assert b.registro.alimento(b.yogur)["seccion"] == clave


def test_seccion_de_vuelta_a_sin_seccion(b):  # 075 R1
    b.registro.mover_seccion(b.pasta, "comida")
    t = _uno(b, accion="seccion_alimento", objeto="pasta", texto="sin sección")
    assert t["texto"].endswith("del 22-10 a Sin sección")
    confirmar(t, b.ctx)
    assert b.registro.alimento(b.pasta)["seccion"] is None


def test_seccion_de_otro_dia_dicho(b):  # 075 R1: the day it is logged on
    gel = b.apuntar("gel de prueba", DOMINGO, 1, 90, unidad="unidad")
    t = _uno(b, accion="seccion_alimento", objeto="gel", dia=DOMINGO, texto="carrera")
    assert t["texto"] == "Voy a pasar gel de prueba 1 unidad (90 kcal) del 18-10 a Nutrición en carrera"
    confirmar(t, b.ctx)
    assert b.registro.alimento(gel)["seccion"] == "carrera"


def test_seccion_que_no_es_de_las_seis_no(b):  # 075 R2 (C-266)
    r = _uno(b, accion="seccion_alimento", objeto="plátano", texto="estrategia de geles")
    assert r["tipo"] == "no"
    for nombre in ("Desayuno", "Media mañana", "Comida", "Merienda", "Cena", "Nutrición en carrera", "Sin sección"):
        assert nombre in r["texto"]
    assert b.tarjetas() == 0
    assert b.registro.alimento(b.platano)["seccion"] is None


def test_seccion_sin_decirla_no(b):  # 075 R2
    r = _uno(b, accion="seccion_alimento", objeto="plátano")
    assert r["tipo"] == "no" and b.tarjetas() == 0


def test_seccion_ya_esta_en_esa_no(b):  # 075 R2
    b.registro.mover_seccion(b.platano, "merienda")
    r = _uno(b, accion="seccion_alimento", objeto="plátano", texto="merienda")
    assert r["tipo"] == "no" and "ya está en Merienda" in r["texto"]
    assert b.tarjetas() == 0


def test_seccion_ya_no_esta_al_aplicar_no_mueve_nada(b):  # 075 R2
    t = _uno(b, accion="seccion_alimento", objeto="plátano", texto="carrera")
    b.registro.borrar(b.platano)
    with pytest.raises(Cambiado) as e:
        confirmar(t, b.ctx)
    assert e.value.tarjeta["texto"] == "Ese alimento ya no está apuntado."


def test_seccion_varios_con_ese_nombre_pregunta_cual(b):  # 075 R1: `elegir`, as the other actions
    otro = b.apuntar("plátano de prueba", HOY, 100, 90, "19:00")
    e = _uno(b, accion="seccion_alimento", objeto="plátano", texto="carrera")
    assert e["tipo"] == "elegir"
    t = rehacer(e, b.ctx, eleccion=otro)
    confirmar(t, b.ctx)
    assert b.registro.alimento(otro)["seccion"] == "carrera"
    assert b.registro.alimento(b.platano)["seccion"] is None


def test_seccion_nombra_las_seis_y_sin_seccion():  # 075 R1: one visible name per section of `registro`
    from tridata.nutricion.asistente.dia import NOMBRE_SECCION
    from tridata.nutricion.registro import SECCIONES
    assert set(NOMBRE_SECCION) == {*SECCIONES, None}


def test_seccion_su_descripcion_dice_nutricion_dia(b):  # 075 R3
    texto = texto_acciones()
    (linea,) = [x for x in texto.splitlines() if x.startswith("- seccion_alimento:")]
    assert "Nutrición › día" in linea and "Nutrición en carrera" in linea and "carrera" in linea
    assert "estrategia" in linea  # it is not the nutrition strategy of the plan


# -- R4 · R-76, C-97: the levels --------------------------------------------------------------------------------------

def test_niveles_de_hoy(b):  # R4
    t = _uno(b, "hoy niveles 20, 15 y 10", accion="niveles_dia", valores=[-20, -15, -10], dia=HOY)
    assert t["texto"] == "Voy a cambiar los niveles del 22-10: −10 / 0 / +10 % → −20 / −15 / −10 % (déficit)"
    assert any(c["clave"] == "niveles" for c in t["campos"])
    assert b.objetivos.del_dia(HOY) is None
    confirmar(t, b.ctx)
    assert b.objetivos.del_dia(HOY)["niveles"] == [-20, -15, -10]
    assert [m["nivel"] for m in b.ctx.resumen(HOY)["margenes"]] == [-20, -15, -10]


def test_mas_de_cinco_niveles_no(b):  # R4
    r = _uno(b, accion="niveles_dia", valores=[1, 2, 3, 4, 5, 6], dia=HOY)
    assert r == {"tipo": "no", "texto": "los niveles del día son como mucho 5"}


def test_corregir_los_niveles_en_la_tarjeta(b):  # R4 · corrigible
    t = _uno(b, accion="niveles_dia", valores=[-20, -15, -10], dia=HOY)
    otra = rehacer(t, b.ctx, valores={"niveles": "-25, -10"})
    assert otra["texto"].endswith("→ −25 / −10 % (déficit)") and otra["error"] is None


# -- R5 · R-76, C-98: the % and the delay ---------------------------------------------------------------------------

def test_hoy_mantenimiento_con_el_retraso(b):  # R5
    t = _uno(b, "hoy mantenimiento", accion="objetivo_dia", valor=0, dia=HOY)
    assert t["texto"] == "Voy a cambiar el objetivo del 22-10: −15 % → 0 %"
    assert any("1 día más tarde" in a for a in t["avisos"])
    confirmar(t, b.ctx)
    assert b.objetivos.del_dia(HOY)["porcentaje"] == 0


def test_objetivo_igual_no_propone(b):
    r = _uno(b, accion="objetivo_dia", valor=-15, dia=HOY)
    assert r["tipo"] == "no" and "ya es" in r["texto"]


def test_objetivo_fuera_de_rango_como_a_mano(b):  # G-39
    r = _uno(b, accion="objetivo_dia", valor=-70, dia=HOY)
    assert r == {"tipo": "no", "texto": "el % del objetivo del día tiene que estar entre -50 y 50 %"}


# -- R6 · R-76, C-99: the planned training -------------------------------------------------------------------------

def test_entreno_previsto_como_el_del_domingo(b):  # R6
    t = _uno(b, "hoy además 45 minutos de bici suave como el del domingo", accion="entreno_previsto",
             objeto="bici suave", cantidad=45, fecha=DOMINGO, dia=HOY)
    assert t["tipo"] == "propuesta" and t["dia"] == "2026-10-22"
    assert "45 min" in t["texto"] and "360 kcal" in t["texto"] and "18-10" in t["texto"]
    assert t["parametros"]["previsto"]["kcal"] == 360
    confirmar(t, b.ctx)
    previsto = b.objetivos.del_dia(HOY)["previsto"]
    assert (previsto["actividad_id"], previsto["minutos"], previsto["kcal"]) == ("prueba-bici", 45, 360)


def test_entreno_que_no_esta_en_los_ultimos_14_dias(b):  # R6
    b.recientes = []
    r = _uno(b, accion="entreno_previsto", objeto="bici suave", cantidad=45, fecha=DOMINGO, dia=HOY)
    assert r == {"tipo": "no", "texto": "no encuentro ese entreno en los últimos 14 días"}


# -- R7 · R-77, C-101: what he says of before does not match -------------------------------------------------------

def test_los_pasos_que_dice_no_cuadran(b):  # R7
    t = _uno(b, "hoy en vez de 8 mil pasos son 15 mil", accion="pasos_dia", valor=15000, dia=HOY, antes_dicho="8 mil")
    assert t["texto"] == "Voy a cambiar los pasos previstos del 22-10: 10.000 → 15.000"
    assert t["avisos"] == ["tenías 10.000, no 8.000"]


def test_la_cantidad_que_dice_no_cuadra(b):  # R7
    t = _uno(b, accion="cambiar_alimento", objeto="pasta", cantidad=120, unidad="g", antes_dicho="90")
    assert t["avisos"] == ["tenías 100 g, no 90 g"]


# -- R12: a past day for the plan of the day ---------------------------------------------------------------------------

@pytest.mark.parametrize("campos", [
    {"accion": "niveles_dia", "valores": [-20, -10]},
    {"accion": "objetivo_dia", "valor": 0},
    {"accion": "entreno_previsto", "objeto": "bici suave", "cantidad": 45},
])
def test_un_dia_pasado_no(b, campos):  # R12
    r = _uno(b, "el martes niveles 20 y 10", dia=MARTES, **campos)
    assert r["tipo"] == "no" and r["texto"].startswith("ese día ya pasó")
    assert b.objetivos.del_dia(MARTES) is None


# Unit 080 (S-5): the tests through the web (/api/interpretar + /api/asistente/*) went with those routes; the
# cards are made by the coach from the PC with these same functions (`nutricion.asistente`).
