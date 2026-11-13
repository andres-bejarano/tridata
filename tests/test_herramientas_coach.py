"""Unit 081 (P-20261105-cf32bea5, mapa v29): what the app did with the paid AI, now done by the coach of the PC.

Through the local MCP server (`mcp_server.py`, `conversaciones/desde_pc.py`) the coach:

- R1 (R-131, R-132, load-bearing): presents a plan .md already ordered in its parts; `preparar_plan` says what would
  be saved (summary, the differences of the done weeks with Garmin, what fits nowhere) and its fingerprint, and saves
  nothing; `importar_plan`, with Andrés's «sí» and that fingerprint, saves it as the import of the app does.
- R2 (R-221, R-222): sees which PDF of the medical folder have no transcription and where they are, and saves the
  transcription it made with the «sí»; outside the folder or without values nothing is saved, and neither the answer
  nor the logs carry a value.
- R3 (S-72, R-42, R-43): sees the saved recipes and fixed foods and logs with them.
- R4 (S-73, R-151..R-154): the routes of Garmin and the profile of a route, never a coordinate.
- R5, R6: every save leaves its message in «Desde el PC»; nothing calls a paid AI.

Every datum is invented; Garmin is a double; nothing talks to Anthropic.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from test_plan_importar import FIXTURE, HOY, OrdenadorFalso, sembrar_garmin

from tridata.conversaciones import Conversaciones
from tridata.conversaciones.desde_pc import TITULO, montar_equipo
from tridata.mcp_server import build_server
from tridata.models import Activity
from tridata.nutricion import Recetario, RegistroComidas
from tridata.plan import Planes, importar
from tridata.plan.rutas import SIN_ALTITUD, puntos_gpx
from tridata.storage import DataStore

AHORA = datetime(2026, 10, 21, 10, 0)
HUELLA = re.compile(r"Huella: ([0-9a-f]{12})")
GPX = Path(__file__).parent / "fixtures" / "ruta-inventada.gpx"
COORDENADAS = ("10.5", "10,5", "-30.2", "-30,2", "30.2", "30,2")  # as in test_rutas.py
NUEVAS = ("preparar_plan", "importar_plan", "get_analiticas_pendientes", "preparar_transcripcion",
          "guardar_transcripcion", "get_recetas", "get_rutas", "get_perfil_ruta")


def plan_del_coach(texto: str) -> dict:
    """What the coach presents: each section of the .md with its part and its rows, as the AI of the app gave them
    (the double of `test_plan_importar.py`), and its text as it is."""
    apartados = []
    for t in importar.trocear(texto):
        if not (t.cuerpo or t.titulo):
            continue
        parte, filas = "nota", []
        if t.cuerpo:
            ordenado, _, _ = OrdenadorFalso().ordenar(t.titulo, t.cuerpo, HOY)
            parte, filas = ordenado.parte, list(ordenado.filas)
        apartados.append({"titulo": t.titulo, "parte": parte, "filas": filas, "texto": t.cuerpo})
    return {"nombre": importar.nombre_del_plan(texto), "apartados": apartados}


class GarminRutas:
    """What `plan/rutas.py` uses of `GarminClient`, in memory: one saved route and the GPX of any activity."""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.sin_altitud = False
        self.llamadas: list[tuple] = []

    def _llamada(self, *que) -> None:
        self.llamadas.append(que)
        if self.error is not None:
            raise self.error

    def rutas_guardadas(self) -> list[dict]:
        self._llamada("rutas_guardadas")
        return [{"id": 77, "nombre": "Ruta inventada de Garmin", "distancia_m": 20000.0, "sube_m": 115.0,
                 "baja_m": 115.0}]

    def ruta(self, course_id: int) -> list[dict]:
        self._llamada("ruta", course_id)
        return [{"distancia_m": p.distancia_m, "altitud_m": None if self.sin_altitud else p.altitud_m,
                 "lat": p.lat, "lon": p.lon} for p in puntos_gpx(GPX.read_bytes())]

    def gpx_actividad(self, activity_id: str) -> bytes:
        self._llamada("gpx_actividad", activity_id)
        return GPX.read_bytes()


TRANSCRIPCION = {
    "tipo": "analitica", "titulo": "Analítica inventada", "fecha": "2026-10-01",
    "valores": [{"prueba": "Ferritina inventada", "valor": "123,4", "unidad": "ng/mL", "rango": "30-400",
                 "fecha": None},
                {"prueba": "Hemoglobina inventada", "valor": "15,7", "unidad": "g/dL", "rango": "13-17", "fecha": None},
                {"prueba": "Glucosa inventada", "valor": "88", "unidad": "mg/dL", "rango": None, "fecha": None}],
    "notas": ["Nota inventada del informe."],
}
VALORES = ("123,4", "15,7", "Ferritina", "Hemoglobina", "Glucosa", "Nota inventada")


class Mundo:
    """A temporary SQLite with the invented Garmin runs of `test_plan_importar.py`, no plan, a folder with a new
    medical PDF (its transcriber a double that must never be called) and Garmin's routes as a double."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.db = tmp_path / "data.sqlite3"
        self.store = DataStore(self.db)
        sembrar_garmin(self.store)
        self.carpeta = tmp_path / "medicos"
        self.carpeta.mkdir()
        self.pdf = self.carpeta / "analitica-nueva-inventada.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 analitica inventada 1")
        self.transcriptor = MagicMock()
        self.equipo = montar_equipo(self.db, store=self.store, medicos_dir=self.carpeta,
                                    transcriptor_medico=self.transcriptor, token=tmp_path / "google-token.json",
                                    hoy=lambda: HOY, ahora=lambda: AHORA)
        self.garmin = GarminRutas()
        self.equipo.asistente.planes.garmin = lambda: self.garmin  # never the real Garmin Connect
        self.server = build_server(self.store, equipo=self.equipo)

    def llamar(self, nombre: str, argumentos: dict | None = None) -> str:
        resultado = asyncio.run(self.server.call_tool(nombre, argumentos or {}))
        assert not resultado.is_error, resultado
        return "".join(c.text for c in resultado.content)

    def huella(self, texto: str) -> str:
        encontrada = HUELLA.search(texto)
        assert encontrada, texto
        return encontrada.group(1)

    def volcado(self) -> list[str]:
        conn = sqlite3.connect(self.db)
        try:
            return list(conn.iterdump())
        finally:
            conn.close()

    def planes(self) -> list[dict]:
        return Planes(self.db).lista()

    def mensajes_pc(self) -> list[dict]:
        conversaciones = Conversaciones(self.db)
        return [m for c in conversaciones.lista() if c["titulo"] == TITULO for m in conversaciones.mensajes(c["id"])]


@pytest.fixture
def mundo(tmp_path) -> Mundo:
    return Mundo(tmp_path)


@pytest.fixture
def texto() -> str:
    return FIXTURE.read_text(encoding="utf-8")


# -- R1 · R-131, R-132, C-173, C-174 (criterio portante) -------------------------------------------------------------

def test_preparar_plan_dice_lo_que_guardaria_y_no_guarda_nada(mundo, texto):  # R1 · C-173
    antes = mundo.volcado()
    respuesta = mundo.llamar("preparar_plan", {"plan": plan_del_coach(texto)})
    assert "Road to Ejemplo 26" in respuesta and "no está guardado" in respuesta
    assert "4 carreras" in respuesta and "8 semanas hechas" in respuesta
    assert "Semana 4" in respuesta  # Garmin has 6 minutes more: the difference, proposing the plan's
    assert "Correr una carrera inventada por montaña." in respuesta  # what fits nowhere, as a note (C-174)
    huella = mundo.huella(respuesta)
    assert mundo.huella(mundo.llamar("preparar_plan", {"plan": plan_del_coach(texto)})) == huella
    assert mundo.volcado() == antes and mundo.planes() == [] and mundo.mensajes_pc() == []


def test_importar_plan_con_el_si_lo_guarda_como_la_app(mundo, texto, tmp_path):  # R1 · R-131 (portante)
    huella = mundo.huella(mundo.llamar("preparar_plan", {"plan": plan_del_coach(texto)}))
    respuesta = mundo.llamar("importar_plan", {"papel": "entrenador", "plan": plan_del_coach(texto), "huella": huella,
                                               "texto": "Te importo el plan."})
    assert respuesta.startswith("Hecho"), respuesta
    (guardado,) = mundo.planes()
    # the same .md through the app's old path (the AI double orders it): the same plan in the base
    viejo = DataStore(tmp_path / "viejo.sqlite3")
    sembrar_garmin(viejo)
    propuesta = importar.proponer(texto, OrdenadorFalso(), viejo, HOY)
    ident = importar.confirmar(propuesta, {}, Planes(tmp_path / "viejo.sqlite3"), AHORA)
    esperado = Planes(tmp_path / "viejo.sqlite3").leer(ident)
    leido = Planes(mundo.db).leer(guardado["id"])
    assert esperado is not None and leido is not None
    assert {k: v for k, v in leido.items() if k != "id"} == {k: v for k, v in esperado.items() if k != "id"}
    s4 = next(s for s in leido["semanas"] if s["n"] == 4)
    assert s4["hecho_plan_s"] == 3 * 3600 + 16 * 60  # the plan's, proposed by default
    notas = [n["texto"] for n in leido["notas"] if n["tipo"] == "nota"]
    assert any("Correr una carrera inventada por montaña." in n for n in notas)  # R-132: as it was


def test_importar_plan_con_otra_huella_no_guarda_y_devuelve_lo_nuevo(mundo, texto):  # R1
    antes = mundo.volcado()
    respuesta = mundo.llamar("importar_plan", {"papel": "entrenador", "plan": plan_del_coach(texto),
                                               "huella": "0" * 12})
    assert not respuesta.startswith("Hecho") and "No se ha hecho nada" in respuesta
    assert mundo.huella(respuesta)  # the version of now, to show again
    assert mundo.volcado() == antes and mundo.planes() == []


def test_importar_plan_si_garmin_cambio_desde_que_se_preparo_no_guarda(mundo, texto):  # R1
    huella = mundo.huella(mundo.llamar("preparar_plan", {"plan": plan_del_coach(texto)}))
    mundo.store.save_activities([Activity(  # one more run in week 4 (2026-09-14..20): Garmin's numbers change
        activity_id="s4-extra", activity_date=HOY - timedelta(days=35), activity_type="running", name="Rodaje",
        duration_seconds=1800, distance_meters=5000, moving_duration_seconds=1700)])
    antes = mundo.volcado()
    respuesta = mundo.llamar("importar_plan", {"papel": "entrenador", "plan": plan_del_coach(texto), "huella": huella})
    assert "cambiado" in respuesta.lower() and "No se ha hecho nada" in respuesta
    assert mundo.huella(respuesta) != huella
    assert mundo.volcado() == antes and mundo.planes() == []


def test_importar_plan_eligiendo_garmin_en_una_diferencia(mundo, texto):  # R1: what Andrés chose
    elecciones = {"4": "garmin"}
    huella = mundo.huella(mundo.llamar("preparar_plan", {"plan": plan_del_coach(texto), "elecciones": elecciones}))
    respuesta = mundo.llamar("importar_plan", {"papel": "entrenador", "plan": plan_del_coach(texto), "huella": huella,
                                               "elecciones": elecciones})
    assert respuesta.startswith("Hecho"), respuesta
    (guardado,) = mundo.planes()
    s4 = next(s for s in Planes(mundo.db).leer(guardado["id"])["semanas"] if s["n"] == 4)
    assert s4["hecho_plan_s"] is None and s4["hecho_plan_km"] is None  # Garmin's stays


def test_importar_plan_con_fases_y_hora(mundo):  # R1: phases and the hour of a session
    plan = {"nombre": "Plan inventado con fases", "apartados": [
        {"titulo": "Previsto", "parte": "previsto", "texto": "las semanas inventadas", "filas": [
            "semana|1|2026-11-02|2026-11-08|4:00|tirada|| ", "semana|2|2026-11-09|2026-11-15|4:30|series||",
            "fase|Base inventada|1|2", "sesion|2026-11-04|Rodaje inventado|45' a 5:40|no|20:00"]}]}
    huella = mundo.huella(mundo.llamar("preparar_plan", {"plan": plan}))
    assert mundo.llamar("importar_plan", {"papel": "entrenador", "plan": plan, "huella": huella}).startswith("Hecho")
    (guardado,) = mundo.planes()
    leido = Planes(mundo.db).leer(guardado["id"])
    assert [(f["nombre"], f["desde_n"], f["hasta_n"]) for f in leido["fases"]] == [("Base inventada", 1, 2)]
    assert [(s["titulo"], s.get("hora")) for s in leido["sesiones"]] == [("Rodaje inventado", "20:00")]


@pytest.mark.parametrize("plan", [{"apartados": "no es una lista"}, {"apartados": [{"titulo": "x", "parte": "otra"}]},
                                  {"apartados": []}, "un texto"])
def test_un_plan_mal_formado_dice_que_corregir_y_no_guarda(mundo, plan):  # R1
    antes = mundo.volcado()
    for nombre, argumentos in (("preparar_plan", {"plan": plan}),
                               ("importar_plan", {"papel": "entrenador", "plan": plan, "huella": "0" * 12})):
        respuesta = mundo.llamar(nombre, argumentos)
        assert "No se ha hecho nada" in respuesta and "Huella" not in respuesta
    assert mundo.volcado() == antes and mundo.planes() == []


# -- R2 · R-221, R-222, C-274, C-275 ---------------------------------------------------------------------------------

def test_analiticas_pendientes_dice_cuales_y_donde(mundo):  # R2 · R-221
    antes = mundo.volcado()
    respuesta = mundo.llamar("get_analiticas_pendientes")
    assert "analitica-nueva-inventada.pdf" in respuesta and str(mundo.pdf) in respuesta and "nuevo" in respuesta
    assert mundo.volcado() == antes
    mundo.transcriptor.transcribir.assert_not_called()


def test_guardar_transcripcion_con_el_si_y_el_contexto_la_lleva(mundo):  # R2 · C-274
    antes = mundo.volcado()
    preparada = mundo.llamar("preparar_transcripcion", {"fichero": "analitica-nueva-inventada.pdf",
                                                        "transcripcion": TRANSCRIPCION})
    assert "3 valores" in preparada and "2026-10-01" in preparada and "no está guardad" in preparada
    assert mundo.volcado() == antes
    respuesta = mundo.llamar("guardar_transcripcion", {"papel": "fisio", "fichero": str(mundo.pdf),
                                                       "transcripcion": TRANSCRIPCION,
                                                       "huella": mundo.huella(preparada)})
    assert respuesta.startswith("Hecho"), respuesta
    conn = sqlite3.connect(mundo.db)
    try:
        (fila,) = conn.execute("SELECT fichero, huella FROM transcripcion_medica").fetchall()
    finally:
        conn.close()
    assert fila == ("analitica-nueva-inventada.pdf", hashlib.sha256(mundo.pdf.read_bytes()).hexdigest())
    contexto = mundo.llamar("get_contexto")
    assert "Ferritina inventada" in contexto and "123,4" in contexto
    assert "analitica-nueva-inventada.pdf" not in mundo.llamar("get_analiticas_pendientes")
    mundo.pdf.write_bytes(b"%PDF-1.4 analitica inventada 2")  # changed: pending again
    assert "cambiado" in mundo.llamar("get_analiticas_pendientes")
    mundo.transcriptor.transcribir.assert_not_called()


def _fuera(mundo) -> str:
    otra = mundo.tmp / "otra-inventada.pdf"
    otra.write_bytes(b"%PDF-1.4 fuera")
    return str(otra)


@pytest.mark.parametrize("caso", ["fuera", "subir", "no_existe", "sin_valores", "mal_formada", "huella"])
def test_transcripcion_que_no_vale_no_se_guarda_ni_lleva_valores(mundo, caso, caplog):  # R2 · R-222, C-275
    fichero, transcripcion, huella = "analitica-nueva-inventada.pdf", dict(TRANSCRIPCION), None
    if caso == "fuera":
        fichero = _fuera(mundo)
    elif caso == "subir":
        _fuera(mundo)
        fichero = "../otra-inventada.pdf"
    elif caso == "no_existe":
        fichero = "no-esta-inventada.pdf"
    elif caso == "sin_valores":
        transcripcion["valores"] = []
    elif caso == "mal_formada":
        transcripcion["valores"] = [{"prueba": "Ferritina inventada", "valor": {"123,4": "ng"}}]
    elif caso == "huella":
        huella = "0" * 12
    antes = mundo.volcado()
    caplog.set_level(logging.DEBUG)
    respuestas = [mundo.llamar("preparar_transcripcion", {"fichero": fichero, "transcripcion": transcripcion})]
    respuestas.append(mundo.llamar("guardar_transcripcion", {"papel": "fisio", "fichero": fichero,
                                                             "transcripcion": transcripcion,
                                                             "huella": huella or "0" * 12}))
    if caso == "huella":
        respuestas = respuestas[1:]  # preparing it is fine: only the save with another fingerprint is refused
    for respuesta in respuestas:
        assert "No se ha hecho nada" in respuesta, respuesta
        assert not any(v in respuesta for v in VALORES), respuesta
    if caso in ("fuera", "subir", "no_existe"):
        assert "no está en la carpeta" in respuestas[-1]
    if caso == "sin_valores":
        assert "ningún valor" in respuestas[-1]
    assert not any(v in caplog.text for v in VALORES)
    assert mundo.volcado() == antes and mundo.mensajes_pc() == []


# -- R3 · S-72, R-42, R-43 -------------------------------------------------------------------------------------------

@pytest.fixture
def recetas(mundo) -> dict:
    recetario = Recetario(mundo.db)
    lentejas = recetario.crear_receta({"nombre": "lentejas inventadas", "alias": ["lentejas de la semana"],
                                       "ingredientes": [{"nombre": "lenteja inventada", "cantidad": 400, "unidad": "g",
                                                         "kcal": 1400}], "peso_neto_g": 1000}, HOY)
    guiso = recetario.crear_receta({"nombre": "guiso inventado", "kcal_100g_aprox": 120,
                                    "ingredientes": [{"nombre": "patata inventada", "cantidad": 500, "unidad": "g",
                                                      "kcal": 400}]}, HOY)
    galleta = recetario.crear_fijo({"nombre": "galleta inventada", "alias": ["galletita"], "kcal": 45,
                                    "por": "unidad"})
    return {"lentejas": lentejas, "guiso": guiso, "galleta": galleta}


def test_get_recetas_ve_nombre_alias_kcal_y_estado(mundo, recetas):  # R3 · S-72
    antes = mundo.volcado()
    datos = json.loads(mundo.llamar("get_recetas"))
    por_nombre = {r["nombre"]: r for r in datos["recetas"]}
    lentejas, guiso = por_nombre["lentejas inventadas"], por_nombre["guiso inventado"]
    assert lentejas["alias"] == ["lentejas de la semana"] and lentejas["kcal_100g"] == 140
    assert lentejas["estado"] == "definitiva" and guiso["estado"] == "provisional"
    assert lentejas["id"] == recetas["lentejas"]["id"]
    (galleta,) = [f for f in datos["ingredientes"] if f["nombre"] == "galleta inventada"]
    assert (galleta["kcal"], galleta["por"], galleta["alias"]) == (45, "unidad", ["galletita"])
    assert mundo.volcado() == antes


@pytest.mark.parametrize("alimento, nombre, kcal, origen", [
    ({"nombre": "lentejas de la semana", "cantidad": 150, "unidad": "g", "kcal": 999}, "lentejas inventadas", 210,
     "de tu receta «lentejas inventadas»"),
    ({"nombre": "Galletita", "cantidad": 3, "unidad": "unidad", "kcal": 999}, "galleta inventada", 135,
     "de tu ingrediente «galleta inventada»"),
])
def test_apuntar_por_nombre_o_alias_sale_de_lo_guardado(mundo, recetas, alimento, nombre, kcal, origen):  # R3 · R-42
    respuesta = mundo.llamar("apuntar_comida", {"papel": "nutricionista", "dia": HOY.isoformat(),
                                                "alimentos": [alimento]})
    assert respuesta.startswith("Hecho") and str(kcal) in respuesta, respuesta
    (apunte,) = [a for a in RegistroComidas(mundo.db).apuntes_del_dia(HOY) if a["nombre"] == nombre]
    assert (apunte["kcal"], apunte["origen"], apunte["estimado"]) == (kcal, origen, False)


def test_apuntar_con_guardado_por_id_liga_y_calcula(mundo, recetas):  # R3: the mechanism of the app (unit 019)
    guiso = {"nombre": "guiso", "cantidad": 200, "unidad": "g", "kcal": 1,
             "guardado": {"tipo": "receta", "id": recetas["guiso"]["id"]}}
    assert mundo.llamar("apuntar_comida", {"papel": "nutricionista", "dia": HOY.isoformat(),
                                           "alimentos": [guiso]}).startswith("Hecho")
    (apunte,) = [a for a in RegistroComidas(mundo.db).apuntes_del_dia(HOY) if a["nombre"] == "guiso inventado"]
    assert (apunte["kcal"], apunte["origen"], apunte["provisional"]) == (240, "de tu receta «guiso inventado»", True)


def test_apuntar_lo_que_no_esta_guardado_va_estimado_como_hoy(mundo, recetas):  # R3 · R-43, R6
    paella = {"nombre": "paella inventada", "cantidad": 350, "unidad": "g", "kcal": 650, "estimado": True}
    assert mundo.llamar("apuntar_comida", {"papel": "nutricionista", "dia": HOY.isoformat(),
                                           "alimentos": [paella]}).startswith("Hecho")
    (apunte,) = [a for a in RegistroComidas(mundo.db).apuntes_del_dia(HOY) if a["nombre"] == "paella inventada"]
    assert (apunte["kcal"], apunte["origen"], apunte["estimado"]) == (650, None, True)


def test_apuntar_un_guardado_que_no_sale_no_apunta_nada(mundo, recetas):  # R3: never another number in silence
    antes = mundo.volcado()
    galletas = {"nombre": "galleta inventada", "cantidad": 30, "unidad": "g", "kcal": 140}  # saved per unit
    respuesta = mundo.llamar("apuntar_comida", {"papel": "nutricionista", "dia": HOY.isoformat(),
                                                "alimentos": [galletas]})
    assert "No se ha apuntado nada" in respuesta and "unidad" in respuesta
    assert mundo.volcado() == antes


# -- R4 · S-73, R-151..R-154 -----------------------------------------------------------------------------------------

def test_get_rutas_da_las_de_garmin_y_las_carreras_hechas(mundo):  # R4 · R-151
    respuesta = mundo.llamar("get_rutas")
    assert "Ruta inventada de Garmin" in respuesta and "77" in respuesta and "20" in respuesta
    assert "r-dom" in respuesta  # a run already done, by its id


@pytest.mark.parametrize("argumentos", [{"garmin": 77}, {"actividad": "r-dom"}, {"gpx": str(GPX)}])
def test_perfil_de_una_ruta_sin_coordenadas(mundo, argumentos):  # R4 · R-152, R-154
    respuesta = mundo.llamar("get_perfil_ruta", argumentos)
    assert re.search(r"\b(19,\d|20(,\d)?) km", respuesta) and "subida" in respuesta and "bajada" in respuesta and "llano" in respuesta
    assert "Altitud cada 100 m" in respuesta
    for coordenada in COORDENADAS:
        assert coordenada not in respuesta, coordenada


def test_perfil_sin_altitud_o_garmin_caido_lo_dice_y_pide_un_gpx(mundo, tmp_path):  # R4 · R-153
    mundo.garmin.sin_altitud = True
    assert SIN_ALTITUD in mundo.llamar("get_perfil_ruta", {"garmin": 77})
    mundo.garmin.error = RuntimeError("Garmin Connect did not answer")
    for argumentos in ({"garmin": 77}, {"actividad": "r-dom"}):
        respuesta = mundo.llamar("get_perfil_ruta", argumentos)
        assert "Garmin no responde" in respuesta and "GPX" in respuesta
    rutas = mundo.llamar("get_rutas")
    assert "Garmin no responde" in rutas and "r-dom" in rutas  # the runs done are still offered
    sin_ele = tmp_path / "sin-altitud-inventada.gpx"
    sin_ele.write_text(re.sub(r"<ele>[^<]*</ele>", "", GPX.read_text(encoding="utf-8")), encoding="utf-8")
    assert SIN_ALTITUD in mundo.llamar("get_perfil_ruta", {"gpx": str(sin_ele)})
    assert "no encuentro" in mundo.llamar("get_perfil_ruta", {"gpx": str(tmp_path / "no-esta.gpx")}).lower()


# -- R5 · R-196 ------------------------------------------------------------------------------------------------------

def test_lo_guardado_queda_en_desde_el_pc(mundo, texto):  # R5
    huella = mundo.huella(mundo.llamar("preparar_plan", {"plan": plan_del_coach(texto)}))
    mundo.llamar("importar_plan", {"papel": "entrenador", "plan": plan_del_coach(texto), "huella": huella})
    preparada = mundo.llamar("preparar_transcripcion", {"fichero": "analitica-nueva-inventada.pdf",
                                                        "transcripcion": TRANSCRIPCION})
    mundo.llamar("guardar_transcripcion", {"papel": "fisio", "fichero": "analitica-nueva-inventada.pdf",
                                           "transcripcion": TRANSCRIPCION, "huella": mundo.huella(preparada)})
    plan, analitica = mundo.mensajes_pc()
    assert (plan["papel"], analitica["papel"]) == ("entrenador", "fisio")
    assert "Road to Ejemplo 26" in plan["texto"] and "Hecho" in plan["texto"]
    assert "analitica-nueva-inventada.pdf" in analitica["texto"] and "Hecho" in analitica["texto"]
    assert not any(v in analitica["texto"] for v in VALORES)  # what was done, never a value
    assert plan["hora"].startswith("2026-10-21T10:00") and analitica["hora"].startswith("2026-10-21T10:00")


def test_las_herramientas_no_hablan_de_la_pestana_asistente(mundo):  # R5
    herramientas = asyncio.run(mundo.server.list_tools())
    assert set(NUEVAS) <= {t.name for t in herramientas}
    for t in herramientas:
        assert "Asistente" not in (t.description or "") and "pestaña" not in (t.description or ""), t.name


# -- R6 --------------------------------------------------------------------------------------------------------------

def test_ninguna_herramienta_nueva_llama_a_una_ia_de_pago(mundo, texto, recetas):  # R6
    with patch("anthropic.Anthropic", side_effect=AssertionError("a paid AI was called")) as api:
        huella = mundo.huella(mundo.llamar("preparar_plan", {"plan": plan_del_coach(texto)}))
        mundo.llamar("importar_plan", {"papel": "entrenador", "plan": plan_del_coach(texto), "huella": huella})
        mundo.llamar("get_analiticas_pendientes")
        preparada = mundo.llamar("preparar_transcripcion", {"fichero": "analitica-nueva-inventada.pdf",
                                                            "transcripcion": TRANSCRIPCION})
        mundo.llamar("guardar_transcripcion", {"papel": "fisio", "fichero": "analitica-nueva-inventada.pdf",
                                               "transcripcion": TRANSCRIPCION, "huella": mundo.huella(preparada)})
        mundo.llamar("get_recetas")
        mundo.llamar("get_rutas")
        mundo.llamar("get_perfil_ruta", {"gpx": str(GPX)})
        mundo.llamar("get_contexto")
    api.assert_not_called()
    mundo.transcriptor.transcribir.assert_not_called()


# -- límites (paso 4) ------------------------------------------------------------------------------------------------

def test_los_que_guardan_rechazan_un_papel_desconocido(mundo, texto):  # R1, R2 as R-197
    antes = mundo.volcado()
    huella = mundo.huella(mundo.llamar("preparar_plan", {"plan": plan_del_coach(texto)}))
    preparada = mundo.llamar("preparar_transcripcion", {"fichero": "analitica-nueva-inventada.pdf",
                                                        "transcripcion": TRANSCRIPCION})
    for nombre, argumentos in (("importar_plan", {"plan": plan_del_coach(texto), "huella": huella}),
                               ("guardar_transcripcion", {"fichero": "analitica-nueva-inventada.pdf",
                                                          "transcripcion": TRANSCRIPCION,
                                                          "huella": mundo.huella(preparada)})):
        assert "No se ha hecho nada" in mundo.llamar(nombre, {"papel": "médico", **argumentos})
    assert mundo.volcado() == antes


@pytest.mark.parametrize("elecciones", [{"9": "garmin"}, {"4": "las dos"}])
def test_una_eleccion_que_no_es_de_una_diferencia_no_vale(mundo, texto, elecciones):  # R1
    respuesta = mundo.llamar("preparar_plan", {"plan": plan_del_coach(texto), "elecciones": elecciones})
    assert "elecciones" in respuesta and "No se ha hecho nada" in respuesta and "Huella" not in respuesta


def test_una_fase_que_no_cuadra_va_a_las_notas(mundo):  # R1 · R-132: nothing is lost, nothing fails when saving
    plan = {"apartados": [{"titulo": "Previsto", "parte": "previsto", "filas": [
        "semana|1|2026-11-02|2026-11-08|4:00|tirada||", "fase|Base inventada|1|3",
        "sesion|2026-11-04|Rodaje inventado|45' a 5:40|no|25:00"]}]}
    preparada = mundo.llamar("preparar_plan", {"plan": plan})
    assert "fase | Base inventada | 1 | 3" in preparada and "25:00" in preparada  # both as notes, as they were
    assert mundo.llamar("importar_plan", {"papel": "entrenador", "plan": plan,
                                          "huella": mundo.huella(preparada)}).startswith("Hecho")
    (guardado,) = mundo.planes()
    leido = Planes(mundo.db).leer(guardado["id"])
    assert "fases" not in leido and leido["sesiones"] == [] and leido["nombre"] == importar.NOMBRE_POR_DEFECTO
    assert len([n for n in leido["notas"] if n["tipo"] == "nota"]) == 2


def test_sin_carpeta_de_analiticas_lo_dice(tmp_path):  # R2
    store = DataStore(tmp_path / "data.sqlite3")
    equipo = montar_equipo(tmp_path / "data.sqlite3", store=store, token=tmp_path / "google-token.json",
                           hoy=lambda: HOY, ahora=lambda: AHORA)
    server = build_server(store, equipo=equipo)
    resultado = asyncio.run(server.call_tool("get_analiticas_pendientes", {}))
    assert "TRIDATA_MEDICOS_DIR" in "".join(c.text for c in resultado.content)
