"""Unit 082 (REC-34): what the coach of the PC does with the supplements — `apuntar_toma`, `apuntar_leche_cafes`, the
`hora` of `apuntar_comida` — and what `get_contexto` gives him of each day (R1, R4, R5, R6, R7).

Through the MCP server (`mcp_server.py`, `conversaciones/desde_pc.py`) over a temporary SQLite with the clock fixed.
Garmin is a temporary `DataStore` with invented sleep; nothing talks to Anthropic, Garmin or Google.
Plans: `apuntar-comida` R-225, R-228, R-229, R-230, R-231, C-280, C-284, C-287, C-288.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from tridata.conversaciones import Conversaciones
from tridata.conversaciones.desde_pc import TITULO, contexto, montar_equipo
from tridata.mcp_server import build_server
from tridata.models import SleepRecord
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas
from tridata.nutricion.suplementacion import Suplementacion
from tridata.storage import DataStore

HOY = date(2026, 10, 26)
AYER = HOY - timedelta(days=1)
AHORA = datetime(2026, 10, 26, 16, 40)
DOBLE = {"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130}


class Mundo:
    """Today 1.200 kcal of an invented rice; yesterday a night of 6 h 10 min."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        Ajustes(self.db).cambiar({"basal_kcal": 1650, "kcal_por_paso": 0.04, "pasos_por_defecto": 9000,
                                  "objetivo_pct": -10}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.registro.guardar([Alimento(nombre="arroz inventado", dia=HOY, cantidad=300, unidad="g", kcal=1200)],
                              hoy=HOY, texto="arroz", sin_cambios=True)
        self.store = DataStore(self.db)
        self.store.save_sleep(SleepRecord(sleep_date=AYER, total_sleep_seconds=22200, sleep_score=60))
        self.equipo = montar_equipo(self.db, store=self.store, token=tmp_path / "google-token.json",
                                    hoy=lambda: HOY, ahora=lambda: AHORA)
        self.server = build_server(self.store, equipo=self.equipo)

    def llamar(self, nombre: str, argumentos: dict) -> str:
        resultado = asyncio.run(self.server.call_tool(nombre, argumentos))
        assert not resultado.is_error, resultado
        return "".join(c.text for c in resultado.content)

    def sup(self) -> Suplementacion:
        return Suplementacion(self.db)

    def kcal(self, dia: date = HOY) -> float:
        return sum(a["kcal"] or 0 for a in self.registro.apuntes_del_dia(dia))

    def volcado(self) -> list[str]:
        conn = sqlite3.connect(self.db)
        try:
            return list(conn.iterdump())
        finally:
            conn.close()

    def mensajes_pc(self) -> list[dict]:
        conversaciones = Conversaciones(self.db)
        return [m for c in conversaciones.lista() if c["titulo"] == TITULO for m in conversaciones.mensajes(c["id"])]

    def dia_del_contexto(self, dia: date) -> dict:
        datos = json.loads(contexto(self.equipo).bloques[-1])
        return next(d for d in datos["dias"] if d["fecha"] == dia.isoformat())


@pytest.fixture
def mundo(tmp_path) -> Mundo:
    return Mundo(tmp_path)


# -- R1: a dose with the «sí» -------------------------------------------------------------------------------------------

def test_apuntar_toma_la_deja_aparte_sin_kcal(mundo):  # R1 · R-225, G-60, C-280
    alimentos_antes = mundo.registro.apuntes_del_dia(HOY)
    respuesta = mundo.llamar("apuntar_toma", {"papel": "nutricionista", "dia": HOY.isoformat(),
                                              "tomas": [DOBLE, {"que": "b12", "hora": "09:00"}],
                                              "texto": "A las 10 un doble y la B12."})
    assert respuesta.startswith("Hecho"), respuesta
    assert "10:00" in respuesta and "130 mg" in respuesta
    tomas = mundo.sup().tomas_del_dia(HOY)
    assert [(t["que"], t["forma"], t["hora"], t["mg"], t["mg_estimado"]) for t in tomas] == [
        ("cafe", "doble", "10:00", 130, True), ("b12", None, "09:00", None, False)]
    assert mundo.registro.apuntes_del_dia(HOY) == alimentos_antes and mundo.kcal() == 1200
    assert len(mundo.mensajes_pc()) == 1 and mundo.mensajes_pc()[0]["papel"] == "nutricionista"


def test_toma_de_hoy_sin_hora_queda_a_la_de_ahora(mundo):  # R7 · R-231, C-288
    respuesta = mundo.llamar("apuntar_toma", {"papel": "nutricionista", "dia": HOY.isoformat(), "tomas": [
        {"que": "cafe", "forma": "soluble", "cucharadas": 2, "gramos": 2, "mg": 60}]})
    assert respuesta.startswith("Hecho")
    assert mundo.sup().tomas_del_dia(HOY)[0]["hora"] == "16:40"


def test_toma_de_otro_dia_sin_hora_falta_la_hora(mundo):  # R7 · S-80
    antes = mundo.volcado()
    respuesta = mundo.llamar("apuntar_toma", {"papel": "nutricionista", "dia": AYER.isoformat(),
                                              "tomas": [{"que": "b12", "hora": "09:00"},
                                                        {"que": "cafe", "forma": "doble", "mg": 130}]})
    assert "falta la hora" in respuesta and not respuesta.startswith("Hecho")
    assert mundo.volcado() == antes and mundo.mensajes_pc() == []


@pytest.mark.parametrize("toma, motivo", [
    ({"que": "cafe", "forma": "capuchino", "hora": "10:00", "mg": 80}, "forma"),
    ({"que": "cafe", "forma": "doble", "hora": "25:00", "mg": 80}, "hora"),
    ({"que": "cafe", "forma": "doble", "hora": "10:00", "mg": -1}, "negativ"),
    ({"que": "cafe", "forma": "doble", "hora": "10:00"}, "mg"),  # the coach always estimates them (S-81)
])
def test_toma_no_valida_no_apunta_nada(mundo, toma, motivo):  # R8
    antes = mundo.volcado()
    respuesta = mundo.llamar("apuntar_toma", {"papel": "nutricionista", "dia": HOY.isoformat(), "tomas": [toma]})
    assert respuesta.startswith("No se ha apuntado nada") and motivo in respuesta
    assert mundo.volcado() == antes


def test_toma_con_papel_que_no_es(mundo):
    antes = mundo.volcado()
    respuesta = mundo.llamar("apuntar_toma", {"papel": "cocinero", "dia": HOY.isoformat(), "tomas": [DOBLE]})
    assert "No se ha hecho nada" in respuesta and mundo.volcado() == antes


# -- R4: the milk of the coffees ----------------------------------------------------------------------------------------

def _tres_cafes(mundo) -> None:
    mundo.llamar("apuntar_toma", {"papel": "nutricionista", "dia": HOY.isoformat(), "tomas": [
        DOBLE, {**DOBLE, "hora": "12:00"}, {**DOBLE, "hora": "15:00", "solo": True}]})


def test_leche_una_vez_y_repartida(mundo):  # R4 · R-228, C-284
    _tres_cafes(mundo)
    respuesta = mundo.llamar("apuntar_leche_cafes", {"papel": "nutricionista", "dia": HOY.isoformat(), "ml": 250,
                                                     "kcal": 115, "texto": "Hoy 250 ml de leche en los cafés."})
    assert respuesta.startswith("Hecho"), respuesta
    assert mundo.kcal() == 1315
    r = mundo.sup().resumen(HOY)
    assert [t["leche_ml"] for t in r["tomas"]] == [125, 125, None]
    assert r["leche"]["ml"] == 250 and r["leche"]["repartida"] is True


def test_leche_otra_vez_cambia_la_misma(mundo):  # R4: «se apunta UNA vez»
    _tres_cafes(mundo)
    mundo.llamar("apuntar_leche_cafes", {"papel": "nutricionista", "dia": HOY.isoformat(), "ml": 250, "kcal": 115})
    respuesta = mundo.llamar("apuntar_leche_cafes", {"papel": "nutricionista", "dia": HOY.isoformat(), "ml": 300,
                                                     "kcal": 138})
    assert respuesta.startswith("Hecho")
    assert mundo.kcal() == 1338
    assert len(mundo.registro.apuntes_del_dia(HOY)) == 2  # the rice and ONE milk
    assert [t["leche_ml"] for t in mundo.sup().resumen(HOY)["tomas"]] == [150, 150, None]


@pytest.mark.parametrize("ml", [0, -50])
def test_leche_no_valida(mundo, ml):  # R8
    antes = mundo.volcado()
    respuesta = mundo.llamar("apuntar_leche_cafes", {"papel": "nutricionista", "dia": HOY.isoformat(), "ml": ml,
                                                     "kcal": 10})
    assert respuesta.startswith("No se ha apuntado") and "ml" in respuesta
    assert mundo.volcado() == antes


def test_leche_sin_cafes_a_lo_largo_del_dia(mundo):  # R4 · G-61
    respuesta = mundo.llamar("apuntar_leche_cafes", {"papel": "nutricionista", "dia": HOY.isoformat(), "ml": 200,
                                                     "kcal": 92})
    assert respuesta.startswith("Hecho") and "a lo largo del día" in respuesta
    assert mundo.sup().resumen(HOY)["leche"]["repartida"] is False


# -- R5: the hour of a section when logging a meal ----------------------------------------------------------------------

def test_apuntar_comida_con_hora(mundo):  # R5 · R-229
    respuesta = mundo.llamar("apuntar_comida", {"papel": "nutricionista", "dia": HOY.isoformat(), "seccion": "cena",
                                                "hora": "22:15", "alimentos": [
                                                    {"nombre": "sopa inventada", "cantidad": 300, "unidad": "g",
                                                     "kcal": 120}]})
    assert respuesta.startswith("Hecho")
    assert mundo.sup().horas_del_dia(HOY)["cena"] == {"hora": "22:15", "aproximada": False}
    assert mundo.kcal() == 1320


@pytest.mark.parametrize("extra, motivo", [({"hora": "22:15"}, "sección"),
                                           ({"hora": "22:75", "seccion": "cena"}, "hora")])
def test_apuntar_comida_con_hora_mal_no_apunta(mundo, extra, motivo):  # R5, R8
    antes = mundo.volcado()
    respuesta = mundo.llamar("apuntar_comida", {"papel": "nutricionista", "dia": HOY.isoformat(), **extra,
                                                "alimentos": [{"nombre": "sopa inventada", "cantidad": 300,
                                                               "unidad": "g", "kcal": 120}]})
    assert respuesta.startswith("No se ha apuntado nada") and motivo in respuesta
    assert mundo.volcado() == antes


# -- R6: the context ----------------------------------------------------------------------------------------------------

def test_el_contexto_lleva_tomas_horas_y_sueno(mundo):  # R6 · R-230, C-287
    sup = mundo.sup()
    sup.apuntar(AYER, {"que": "cafe", "forma": "italiana", "gramos": 20, "fraccion": 0.5, "hora": "19:00", "mg": 90},
                hoy=HOY, ahora=AHORA)
    mundo.registro.guardar([Alimento(nombre="tortilla inventada", dia=AYER, cantidad=200, unidad="g", kcal=300)],
                           hoy=HOY, texto="cena", sin_cambios=True, seccion="cena")
    mundo.registro.guardar([Alimento(nombre="tostada inventada", dia=AYER, cantidad=60, unidad="g", kcal=150)],
                           hoy=HOY, texto="desayuno", sin_cambios=True, seccion="desayuno")
    (leche,) = mundo.registro.guardar([Alimento(nombre="leche de prueba", dia=AYER, cantidad=100, unidad="ml",
                                                kcal=46)], hoy=HOY, texto="leche", sin_cambios=False)
    sup.marcar_leche(AYER, leche["id"])
    sup.poner_hora_seccion(AYER, "cena", "22:15")
    sup.poner_hora_seccion(AYER, "merienda", "18:30")  # no food there: not in the context
    dia = mundo.dia_del_contexto(AYER)
    s = dia["suplementacion"]
    assert s["tomas"] == [{"hora": "19:00", "que": "café de italiana 20 g (½)", "mg": 90, "mg_estimado": True,
                           "leche_ml": 100}]
    assert (s["cafeina_mg"], s["ultima"]) == (90, "19:00")
    assert s["leche"] == {"ml": 100, "kcal": 46, "repartida": True}
    assert dia["horas_comidas"] == {"desayuno": "~08:00", "cena": "22:15"}
    assert dia["sueno_h"] == pytest.approx(6.17)
    # the same context the get_contexto tool gives
    assert mundo.llamar("get_contexto", {}) == "\n\n".join(contexto(mundo.equipo).bloques)


def test_un_dia_sin_nada_no_lleva_suplementacion(mundo):  # R6: what is not there is not sent
    dia = mundo.dia_del_contexto(HOY)
    assert "suplementacion" not in dia and "horas_comidas" not in dia


def test_el_contexto_no_escribe(tmp_path):  # R6: montar is read only, also over a store of before
    from tridata.conversaciones.contexto import montar
    from tridata.nutricion import ObjetivosDia, RegistroPeso

    db = tmp_path / "data.sqlite3"
    registro = RegistroComidas(db)
    ObjetivosDia(db)  # montar opens it (unit 071): already there, as in any store of today
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1650, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
    pesos = RegistroPeso(db)
    conn = sqlite3.connect(db)
    antes = list(conn.iterdump())
    conn.close()
    montar(registro=registro, pesos=pesos, ajustes=ajustes, store=None, transcripciones=[], aviso_medico=None,
           hoy=HOY)
    conn = sqlite3.connect(db)
    assert list(conn.iterdump()) == antes
    conn.close()


# -- the tools ----------------------------------------------------------------------------------------------------------

def test_las_herramientas_piden_el_si(mundo):  # R1: the same rule of the «sí» and «Hecho» as apuntar_comida
    tools = {t.name: t for t in asyncio.run(mundo.server.list_tools())}
    for nombre in ("apuntar_toma", "apuntar_leche_cafes"):
        t = tools[nombre]
        assert not t.annotations.read_only_hint
        assert "«sí»" in t.description and "Andrés" in t.description and "Hecho" in t.description
    assert set(tools["apuntar_toma"].input_schema["required"]) == {"papel", "dia", "tomas"}
    assert set(tools["apuntar_leche_cafes"].input_schema["required"]) == {"papel", "dia", "ml"}
    comida = tools["apuntar_comida"]
    assert "hora" in comida.input_schema["properties"]
    assert "hora" not in comida.input_schema.get("required", [])
    for forma in ("expreso", "doble", "ingles", "italiana", "soluble"):
        assert forma in tools["apuntar_toma"].description
