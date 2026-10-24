"""The assistant changes a setting with its date, a race, the weight target and the weight of a day (unit 022, REC-15).

Unit level over the catalogue with a seeded temporary SQLite, and confirmation with Starlette's TestClient against
the routes of unit 021. Every `CambioInterpretado` is written by hand; Claude is never called. Plans: R9-R11 of the
unit · R-86..R-88, G-42. Every value is invented.

Today is Thursday 2026-10-22. Healthy limit 25 %, height 175 cm, weight target 72 kg for 2027-02-01, «carrera de
prueba» on 2026-11-08 (one maintenance day), weight of 2026-10-21: 71,8 kg.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.ia import CambioInterpretado, ListaInterpretada
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.nutricion.asistente import (Cambiado, Contexto, Peticion, catalogo, confirmar, proponer, rehacer,
                                         texto_acciones)
from tridata.nutricion.recetas import Recetario
from tridata.web import crear_app

HOY = date(2026, 10, 22)
AYER = HOY - timedelta(days=1)
MANANA = HOY + timedelta(days=1)
LUNES = date(2026, 10, 19)


class Base:
    def __init__(self, tmp_path):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1980, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000,
                              "objetivo_pct": -15, "limite_saludable_pct": 25, "altura_cm": 175,
                              "objetivo_peso": {"kg": 72, "fecha": "2027-02-01"}}, desde=DESDE_SIEMPRE)
        self.carrera = self.ajustes.anadir_carrera("carrera de prueba", "2026-11-08", 1)
        self.registro = RegistroComidas(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.pesos = RegistroPeso(self.db)
        self.pesos.apuntar(AYER, 71.8, hoy=HOY)
        self.recetario = Recetario(self.db)
        self.ctx = Contexto(registro=self.registro, objetivos=self.objetivos, ajustes=self.ajustes, pesos=self.pesos,
                            recetario=self.recetario, hoy=lambda: HOY)

    def pesos_de(self, dia):
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT kg FROM peso_dia WHERE dia = ?", (dia.isoformat(),)).fetchall()


@pytest.fixture
def b(tmp_path):
    return Base(tmp_path)


def _uno(b, dicho="texto de prueba", pedido=HOY, **campos):
    (resultado,) = proponer([CambioInterpretado(**campos)], b.ctx, Peticion(texto=dicho, fecha=pedido))
    return resultado


def test_el_catalogo_gana_ajustes_carreras_y_peso():
    assert {"cambiar_ajuste", "cambiar_carrera", "peso_dia"} <= set(catalogo())
    texto = texto_acciones()
    for i in ("cambiar_ajuste", "cambiar_carrera", "peso_dia"):
        assert f"- {i}:" in texto
    linea = next(x for x in texto.splitlines() if x.startswith("- peso_dia:"))
    assert "corregir" in linea  # «peso 72,4» stays the piece «Peso del día» (R-88)


# -- R9 · R-86, C-113..C-115: a setting with its date ----------------------------------------------------------------

def test_la_fecha_de_una_carrera(b):  # R9
    t = _uno(b, "la carrera de prueba es el 15 de noviembre, no el 8", accion="cambiar_carrera",
             objeto="carrera de prueba", fecha=date(2026, 11, 15), antes_dicho="8 de noviembre")
    assert t["tipo"] == "propuesta"
    assert "Carrera de prueba: 2026-11-08 → 2026-11-15 · vale desde hoy, 22-10" in t["texto"]
    confirmar(t, b.ctx)
    (carrera,) = b.ajustes.carreras()
    assert carrera.fecha == date(2026, 11, 15) and carrera.dias_mantenimiento == 1
    assert b.ajustes.vigentes(date(2026, 11, 15)).motivo_objetivo == "carrera carrera de prueba"
    assert b.ajustes.vigentes(date(2026, 11, 8)).motivo_objetivo == "ajuste"


def test_una_carrera_que_no_esta(b):
    r = _uno(b, accion="cambiar_carrera", objeto="maratón de prueba", fecha=date(2026, 11, 15))
    assert r["tipo"] == "no" and "maratón de prueba" in r["texto"]


def test_anadir_una_carrera(b):  # unit 032, R8: «apúntame esa carrera en Ajustes»
    t = _uno(b, "apúntame el 10K del río el 6 de diciembre", accion="anadir_carrera", texto="10K del río",
             fecha=date(2026, 12, 6))
    assert t["tipo"] == "propuesta"
    assert t["texto"] == "Voy a añadir la carrera 10K del río el 2026-12-06, con 2 días de mantenimiento antes"
    confirmar(t, b.ctx)
    with pytest.raises(Cambiado):  # the same card confirmed twice does not add it twice
        confirmar(t, b.ctx)
    iguales = [c for c in b.ajustes.carreras() if c.nombre == "10K del río"]
    assert len(iguales) == 1
    nueva = iguales[0]
    assert nueva.fecha == date(2026, 12, 6) and nueva.dias_mantenimiento == 2


def test_anadir_una_carrera_que_ya_esta(b):
    r = _uno(b, accion="anadir_carrera", texto="Carrera de prueba", fecha=date(2026, 12, 6))
    assert r["tipo"] == "no" and "Ya tienes" in r["texto"]
    assert len(b.ajustes.carreras()) == 1


def test_el_objetivo_de_peso_vale_desde_hoy_y_se_corrige(b):  # R9
    t = _uno(b, "mi objetivo es 70 kilos para el 1 de marzo", accion="cambiar_ajuste", campo="objetivo_peso",
             valor=70, fecha=date(2027, 3, 1))
    assert "Objetivo de peso: 72 kg (2027-02-01) → 70 kg (2027-03-01) · vale desde hoy, 22-10" in t["texto"]
    assert t["desde"] == "2026-10-22"
    assert {"clave": "desde", "etiqueta": "Vale desde", "tipo": "fecha", "valor": "2026-10-22"} in t["campos"]
    confirmar(t, b.ctx)
    meta = b.ajustes.vigentes(HOY).objetivo_peso
    assert (meta.kg, meta.fecha) == (70, date(2027, 3, 1))
    assert b.ajustes.vigentes(AYER).objetivo_peso.kg == 72


def test_la_altura_fuera_de_rango_con_el_texto_de_ajustes(b):  # R9
    r = _uno(b, "mi altura es 300", accion="cambiar_ajuste", campo="altura_cm", valor=300)
    assert r == {"tipo": "no", "texto": "La altura (cm) tiene que estar entre 100 y 230 (no 300)"}


def test_un_ajuste_desconocido_no(b):
    r = _uno(b, accion="cambiar_ajuste", campo="color", valor=3)
    assert r["tipo"] == "no"


# -- R10 · R-87, C-116: a past date ------------------------------------------------------------------------------------

def test_desde_el_lunes_avisa_de_los_dias_pasados(b):  # R10
    t = _uno(b, "desde el lunes el límite saludable es 20", accion="cambiar_ajuste", campo="limite_saludable_pct",
             valor=20, desde=LUNES)
    assert "Límite saludable: 25 → 20 % · desde 2026-10-19" in t["texto"]
    assert t["avisos"] == ["los días del 19 al 21-10 se evaluarán con el 20 %; lo comido no cambia"]
    assert t["desde"] == "2026-10-19"
    confirmar(t, b.ctx)
    assert b.ajustes.vigentes(date(2026, 10, 20)).limite_saludable_pct == 20
    assert b.ajustes.vigentes(date(2026, 10, 18)).limite_saludable_pct == 25


def test_corregir_la_fecha_de_vigencia(b):  # R9 · the date is a field
    t = _uno(b, accion="cambiar_ajuste", campo="limite_saludable_pct", valor=20)
    assert t["avisos"] == [] and "vale desde hoy, 22-10" in t["texto"]
    otra = rehacer(t, b.ctx, valores={"desde": "2026-10-21"})
    assert "desde 2026-10-21" in otra["texto"]
    assert otra["avisos"] == ["el día 21-10 se evaluará con el 20 %; lo comido no cambia"]


# -- R11 · R-88, C-117, C-118: the weight of a day ---------------------------------------------------------------------

def test_corregir_el_peso_de_ayer(b):  # R11
    t = _uno(b, "ayer pesé 71,3, no 71,8", accion="peso_dia", valor=71.3, dia=AYER, antes_dicho="71,8")
    assert t["texto"] == "Voy a corregir el peso del 21-10: 71,8 → 71,3 kg"
    assert (t["antes"], t["despues"], t["dia"], t["avisos"]) == ("71,8 kg", "71,3 kg", "2026-10-21", [])
    confirmar(t, b.ctx)
    assert b.pesos_de(AYER) == [(71.3,)]


def test_el_peso_que_dice_no_cuadra(b):  # R7 in the weight
    t = _uno(b, accion="peso_dia", valor=71.3, dia=AYER, antes_dicho="71,5")
    assert t["avisos"] == ["tenías 71,8 kg, no 71,5 kg"]


def test_el_peso_de_manana_no(b):  # R11
    r = _uno(b, "mañana peso 70", accion="peso_dia", valor=70, dia=MANANA)
    assert r == {"tipo": "no", "texto": "no se puede apuntar el peso de un día que aún no ha llegado"}


def test_el_peso_fuera_de_rango_no(b):
    r = _uno(b, accion="peso_dia", valor=20, dia=AYER)
    assert r == {"tipo": "no", "texto": "el peso tiene que estar entre 30 y 250 kg"}


# -- through the web -------------------------------------------------------------------------------------------------

class Web:
    def __init__(self, tmp_path):
        self.b = Base(tmp_path)
        self.interprete = MagicMock()
        self.hoy = HOY
        self.cliente = TestClient(crear_app(self.b.registro, self.interprete, self.b.ajustes, hoy=lambda: self.hoy,
                                            objetivos=self.b.objetivos, recetario=self.b.recetario,
                                            pesos=self.b.pesos))

    def tarjeta(self, dicho, **campos):
        self.interprete.interpretar.side_effect = lambda t, hoy: ListaInterpretada(
            alimentos=[], cambios=[CambioInterpretado(**campos)])
        r = self.cliente.post("/api/interpretar", json={"texto": dicho})
        assert r.status_code == 200, r.text
        (t,) = r.json()["cambios"]
        return t

    def confirmar(self, t):
        return self.cliente.post("/api/asistente/confirmar", json={"tarjeta": t})


@pytest.fixture
def w(tmp_path):
    return Web(tmp_path)


def test_web_limite_desde_el_lunes(w):  # R10
    t = w.tarjeta("desde el lunes el límite saludable es 20", accion="cambiar_ajuste", campo="limite_saludable_pct",
                  valor=20, desde=LUNES)
    r = w.confirmar(t)
    assert r.status_code == 200, r.text
    vista = w.cliente.get("/api/ajustes?fecha=2026-10-20").json()
    assert vista["vigentes"]["limite_saludable_pct"] == {"valor": 20, "desde": "2026-10-19"}


def test_web_carrera_ajustes_y_plan_dicen_el_15(w):  # R9
    t = w.tarjeta("la carrera de prueba es el 15", accion="cambiar_carrera", objeto="carrera de prueba",
                  fecha=date(2026, 11, 15))
    assert w.confirmar(t).status_code == 200
    vista = w.cliente.get("/api/ajustes").json()
    assert [c["fecha"] for c in vista["carreras"]] == ["2026-11-15"]
    plan = w.cliente.get("/api/ajustes?fecha=2026-11-15").json()["plan"]
    assert plan["objetivo_pct"] == 0 and plan["motivo"] == "carrera carrera de prueba"


def test_web_el_peso_de_ayer_un_solo_peso_y_su_media(w):  # R11
    t = w.tarjeta("ayer pesé 71,3, no 71,8", accion="peso_dia", valor=71.3, dia=AYER)
    r = w.confirmar(t)
    assert r.status_code == 200, r.text
    assert w.b.pesos_de(AYER) == [(71.3,)]
    dia = w.cliente.get("/api/dia?fecha=2026-10-21").json()
    assert dia["peso"]["media_7_dias"]["kg"] == 71.3


def test_web_el_peso_confirmado_despues_de_medianoche(w):  # R8 for the weight: the day of the card
    t = w.tarjeta("hoy peso 71,0, me equivoqué", accion="peso_dia", valor=71.0, dia=HOY)
    w.hoy = MANANA
    assert w.confirmar(t).status_code == 200
    assert w.b.pesos_de(HOY) == [(71.0,)] and w.b.pesos_de(MANANA) == []
