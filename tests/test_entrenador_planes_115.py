"""Unit 115 (P-20261030-a51b8846 rev. 3, P-20261026-073af351 rev. 3 (c); REC-56 of `llevar-plan`, REC-57 of
`preparar-carrera`): the coach designs, reads and reviews plans, and sends the strategy of a race to the watch.

- R1 (R-345, R-350): `get_plan` reads the list of plans and any plan whole; reading changes nothing.
- R2 (R-346, R-347, load-bearing): a plan the coach designs is shown whole without saving; with the «sí» it is a new
  plan, edited as any other; the plan in course is still Valencia's and no other plan changes.
- R3 (R-351): a race of the proposal already in another plan the same day is said before saving.
- R4 (R-352): a week that ends before it starts, or two weeks that overlap, are not saved, naming the week.
- R5: the weeks keep their planned km (base, proposal, .md and Fitness › Plan); the old ones stay without km.
- R6 (R-348): weeks and phases of any plan with their card and the «sí».
- R7 (R-349): sessions, the target of a race, a note and the strategy of any plan; without a plan, the one in course.
- R8..R10 (R-353..R-357): the strategy of a race to the watch, by stretches, every x km or every x minutes.

Every datum is invented; Garmin is a double; nothing talks to a paid service.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import date, datetime

import pytest
from starlette.testclient import TestClient
from test_plan import plan_de_prueba
from test_plan_reloj import GarminDoble

from tridata.conversaciones.desde_pc import montar_equipo
from tridata.mcp_server import build_server
from tridata.nutricion import Ajustes, RegistroComidas
from tridata.nutricion.asistente import catalogo, texto_acciones
from tridata.plan import Planes
from tridata.plan import estrategia_reloj
from tridata.plan.estrategia_reloj import MAX_PASOS, EstrategiaMal, pasos
from tridata.plan.importar import Apartado, ErrorPlan, proponer_ordenado
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 10, 22)
AHORA = datetime(2026, 10, 22, 9, 0)
HUELLA = re.compile(r"Huella: ([0-9a-f]{12})")
BEHOBIA_DIA = date(2026, 11, 8)

# the strategy of the Behobia (20 km): four stretches with their km, one «por esfuerzo» with its time of passage
ESTRATEGIA = ["km 0-5 · llano · a 4:10 · paso 20:50", "km 5-8 · subida · por esfuerzo · paso 33:30", "gel: km 8",
              "km 8-14 · a 4:05", "km 14-20 · bajada y llano · a 4:00"]

VALENCIA = plan_de_prueba(
    nombre="Road to Valencia 26",
    carreras=[{"apartado": 0, "nombre": "Behobia", "fecha": "2026-11-08", "objetivo": "1:25", "estado": "pendiente",
               "principal": False},
              {"apartado": 0, "nombre": "Maratón de Valencia", "fecha": "2026-12-06", "objetivo": "3:05",
               "estado": "pendiente", "principal": True}])


def _zarautz() -> dict:
    semanas = [{"apartado": 1, "n": n, "desde": desde, "hasta": hasta, "previsto_s": 4 * 3600 + n * 600,
                "clave": f"clave inventada {n}", "hecho_plan_s": None, "hecho_plan_km": None}
               for n, desde, hasta in ((1, "2027-03-01", "2027-03-07"), (2, "2027-03-08", "2027-03-14"),
                                       (3, "2027-03-15", "2027-03-21"), (4, "2027-03-22", "2027-03-28"),
                                       (5, "2027-03-29", "2027-04-04"))]
    semanas[2]["previsto_km"] = 40.0  # C-442: the week 3 with 40 km
    return {
        "nombre": "Zarautz 27",
        "apartados": [{"titulo": "Objetivos", "parte": "objetivos"}, {"titulo": "Previsto", "parte": "previsto"}],
        "carreras": [{"apartado": 0, "nombre": "Triatlón de Zarautz", "fecha": "2027-07-11", "objetivo": "2:30",
                      "estado": "pendiente", "principal": True}],
        "semanas": semanas,
        "sesiones": [{"apartado": 1, "fecha": "2027-03-09", "titulo": "Rodaje de prueba", "bloques": ["45' a 5:30"],
                      "clave": False}],
        "nutricion": [{"apartado": 0, "producto": "Gel inventado", "pauta": "cada 40'"}],
        "incidencias": [{"apartado": 0, "fecha": "2027-01-10", "texto": "Molestia inventada en el gemelo"}],
        "notas": [{"apartado": 0, "tipo": "nota", "texto": "Nota inventada de Zarautz"},
                  {"apartado": 0, "tipo": "comparativa", "texto": "Comparativa inventada con 2026"}],
    }


def _propuesto(*filas_semanas: str, carreras: tuple[str, ...] = ()) -> dict:
    """The plan of the half ironman as the coach gives it to preparar_plan: its race, two weeks with km, a phase and
    two sessions with blocks."""
    semanas = filas_semanas or ("semana|1|2027-01-04|2027-01-10|6:00|base aeróbica|||40",
                                "semana|2|2027-01-11|2027-01-17|6:30|tirada larga|||45")
    return {"nombre": "Road to Medio Ironman 27", "apartados": [
        {"titulo": "Objetivos", "parte": "objetivos", "texto": "El medio ironman de junio, inventado.",
         "filas": ["carrera|Medio Ironman de prueba|2027-06-06|5:15|pendiente|sí", *carreras]},
        {"titulo": "Previsto", "parte": "previsto", "texto": "Las semanas, inventadas.",
         "filas": [*semanas, "fase|Base|1|2"]},
        {"titulo": "Sesiones clave", "parte": "sesiones_clave", "texto": "Las sesiones, inventadas.",
         "filas": ["sesion|2027-01-06|Series de prueba|15' suave;4×1 km a 4:10 (2' rec);10' suave|sí",
                   "sesion|2027-01-10|Tirada de prueba|1h30 a 5:20|sí"]}]}


class Mundo:
    """Valencia in course (with the Behobia and its strategy), Zarautz 27 saved, the team of the PC through the MCP
    server and Garmin as a double that counts how many times it is connected to."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.store = DataStore(self.db)
        self.planes = Planes(self.db)
        self.valencia = self.planes.crear(VALENCIA, AHORA)
        self.planes.guardar_estrategia(self.valencia, "Behobia", ESTRATEGIA)
        self.zarautz = self.planes.crear(_zarautz(), AHORA)
        self.equipo = montar_equipo(self.db, store=self.store, token=tmp_path / "google-token.json",
                                    hoy=lambda: HOY, ahora=lambda: AHORA)
        self.garmin = GarminDoble()
        self.conexiones = 0

        def conectar():
            self.conexiones += 1
            return self.garmin

        self.equipo.asistente.planes.garmin = conectar  # never the real Garmin Connect
        self.server = build_server(self.store, equipo=self.equipo)

    def llamar(self, nombre: str, argumentos: dict | None = None) -> str:
        resultado = asyncio.run(self.server.call_tool(nombre, argumentos or {}))
        assert not resultado.is_error, resultado
        return "".join(c.text for c in resultado.content)

    def preparar(self, cambio: dict) -> str:
        return self.llamar("preparar_cambio", {"cambio": cambio})

    def hacer(self, cambio: dict) -> str:
        """What the coach does: prepare, show it to Andrés, and with his «sí» do it with that fingerprint."""
        texto = self.preparar(cambio)
        encontrada = HUELLA.search(texto)
        assert encontrada, texto
        return self.llamar("hacer_cambio", {"papel": "entrenador", "cambio": cambio, "huella": encontrada.group(1)})

    def volcado(self) -> list[str]:
        conn = sqlite3.connect(self.db)
        try:
            return [x for x in conn.iterdump() if "tarjeta_asistente" not in x]
        finally:
            conn.close()

    def semana(self, plan_id: int, n: int) -> dict:
        return next(s for s in self.planes.leer(plan_id)["semanas"] if s["n"] == n)


@pytest.fixture
def mundo(tmp_path) -> Mundo:
    return Mundo(tmp_path)


def _huella(texto: str) -> str:
    encontrada = HUELLA.search(texto)
    assert encontrada, texto
    return encontrada.group(1)


# -- R1 · R-345, R-350, C-439, C-452: read any plan --------------------------------------------------------------------

def test_get_plan_sin_plan_da_la_lista_de_planes(mundo):  # R1
    antes = mundo.volcado()
    texto = mundo.llamar("get_plan")
    valencia, zarautz = (next(x for x in texto.splitlines() if nombre in x)
                         for nombre in ("Road to Valencia 26", "Zarautz 27"))
    assert f"id {mundo.valencia}" in valencia and "en curso" in valencia
    assert "Maratón de Valencia" in valencia and "2026-12-06" in valencia
    assert "2026-10-12" in valencia and "2026-11-01" in valencia  # its first and last week
    assert f"id {mundo.zarautz}" in zarautz and "en curso" not in zarautz
    assert "Triatlón de Zarautz" in zarautz and "2027-03-01" in zarautz and "2027-04-04" in zarautz
    assert mundo.volcado() == antes


@pytest.mark.parametrize("cual", ["Zarautz 27", "zarautz", "id"])
def test_get_plan_lee_entero_un_plan_que_no_esta_en_curso(mundo, cual):  # R1 · C-439
    antes = mundo.volcado()
    texto = mundo.llamar("get_plan", {"plan": str(mundo.zarautz) if cual == "id" else cual})
    assert "Zarautz 27" in texto
    datos = json.loads(texto[texto.index("{"):])
    assert [c["nombre"] for c in datos["carreras"]] == ["Triatlón de Zarautz"]
    assert [s["n"] for s in datos["semanas"]] == [1, 2, 3, 4, 5]
    s3 = datos["semanas"][2]
    assert s3["previsto_s"] == 4 * 3600 + 1800 and s3["previsto_km"] == 40 and "hecho" in s3
    assert "previsto_km" not in datos["semanas"][0]
    (sesion,) = datos["sesiones"]
    assert sesion["titulo"] == "Rodaje de prueba" and sesion["bloques"] == ["45' a 5:30"] and "id" in sesion
    assert datos["nutricion"] == [{"producto": "Gel inventado", "pauta": "cada 40'"}]
    assert datos["notas"] == ["Nota inventada de Zarautz"]
    assert datos["comparativa"] == ["Comparativa inventada con 2026"]
    assert [i["texto"] for i in datos["incidencias"]] == ["Molestia inventada en el gemelo"]
    assert mundo.volcado() == antes  # reading never changes anything (C-452)


def test_get_plan_lee_el_en_curso_con_su_estrategia(mundo):  # R1
    texto = mundo.llamar("get_plan", {"plan": "Valencia"})
    datos = json.loads(texto[texto.index("{"):])
    behobia = next(c for c in datos["carreras"] if c["nombre"] == "Behobia")
    assert behobia["estrategia"] == ESTRATEGIA
    assert "en curso" in texto[:texto.index("{")]


def test_get_plan_de_un_plan_que_no_existe_dice_cuales_hay(mundo):  # R1
    texto = mundo.llamar("get_plan", {"plan": "Plan inexistente"})
    assert "No hay ningún plan" in texto and "Road to Valencia 26" in texto and "Zarautz 27" in texto


# -- R2 · R-346, R-347, C-440, C-441, C-451 (criterio portante) --------------------------------------------------------

def test_el_plan_disenado_se_ensena_entero_sin_guardar(mundo):  # R2 · C-441
    antes = mundo.volcado()
    texto = mundo.llamar("preparar_plan", {"plan": _propuesto()})
    assert "no está guardado" in texto and "Road to Medio Ironman 27" in texto
    for visto in ("Medio Ironman de prueba", "2027-06-06", "5:15", "Base", "semanas 1-2", "40 km", "45 km",
                  "2027-01-04", "Series de prueba", "4×1 km a 4:10 (2' rec)", "Tirada de prueba", "1h30 a 5:20"):
        assert visto in texto, visto
    assert _huella(texto) == _huella(mundo.llamar("preparar_plan", {"plan": _propuesto()}))
    assert mundo.volcado() == antes and len(mundo.planes.lista()) == 2  # without the «sí», no new plan


def test_con_el_si_es_un_plan_nuevo_y_el_en_curso_sigue_siendo_valencia(mundo):  # R2 (portante) · C-440, C-451
    valencia, zarautz = mundo.planes.leer(mundo.valencia), mundo.planes.leer(mundo.zarautz)
    huella = _huella(mundo.llamar("preparar_plan", {"plan": _propuesto()}))
    hecho = mundo.llamar("importar_plan", {"papel": "entrenador", "plan": _propuesto(), "huella": huella})
    assert hecho.startswith("Hecho"), hecho
    nuevo_id = next(p["id"] for p in mundo.planes.lista() if p["nombre"] == "Road to Medio Ironman 27")
    nuevo = mundo.planes.leer(nuevo_id)
    assert [(c["nombre"], c["fecha"], c["objetivo"], c["principal"]) for c in nuevo["carreras"]] == [
        ("Medio Ironman de prueba", "2027-06-06", "5:15", True)]
    assert [(s["n"], s["previsto_s"], s["previsto_km"]) for s in nuevo["semanas"]] == [(1, 21600, 40.0),
                                                                                      (2, 23400, 45.0)]
    assert [(f["nombre"], f["desde_n"], f["hasta_n"]) for f in nuevo["fases"]] == [("Base", 1, 2)]
    assert [(s["fecha"], s["titulo"], s["bloques"]) for s in nuevo["sesiones"]] == [
        ("2027-01-06", "Series de prueba", ["15' suave", "4×1 km a 4:10 (2' rec)", "10' suave"]),
        ("2027-01-10", "Tirada de prueba", ["1h30 a 5:20"])]
    assert mundo.planes.en_curso(HOY) == mundo.valencia  # the plan in course is still Valencia's
    assert mundo.planes.leer(mundo.valencia) == valencia and mundo.planes.leer(mundo.zarautz) == zarautz
    # edited as any other (C-451): a session moved by hand, and Valencia is still the one in course
    (sesion, *_) = mundo.planes.sesiones_reloj(nuevo_id)
    mundo.planes.mover_sesion(sesion["id"], "2027-01-07", HOY)
    assert mundo.planes.leer(nuevo_id)["sesiones"][0]["fecha"] == "2027-01-07"
    assert mundo.planes.en_curso(HOY) == mundo.valencia


def test_el_plan_nuevo_sale_en_fitness_plan(mundo):  # R2 · R-347: it is seen in Fitness › Plan
    huella = _huella(mundo.llamar("preparar_plan", {"plan": _propuesto()}))
    mundo.llamar("importar_plan", {"papel": "entrenador", "plan": _propuesto(), "huella": huella})
    app = crear_app(RegistroComidas(mundo.db), None, Ajustes(mundo.db), hoy=lambda: HOY, store=mundo.store)
    with TestClient(app) as cliente:
        vista = cliente.get("/api/plan").json()
        assert vista["plan"]["nombre"] == "Road to Valencia 26"  # the one that opens
        nuevo = next(p for p in vista["planes"] if p["nombre"] == "Road to Medio Ironman 27")
        assert nuevo["en_curso"] is False
        suyo = cliente.get(f"/api/plan?id={nuevo['id']}").json()["plan"]
        assert [(s["n"], s.get("previsto_km")) for s in suyo["semanas"]] == [(1, 40.0), (2, 45.0)]


# -- R3 · R-351, C-444 ------------------------------------------------------------------------------------------------

def test_una_carrera_que_ya_esta_en_otro_plan_se_dice_antes_de_guardar(mundo):  # R3
    plan = _propuesto(carreras=("carrera|Behobia|2026-11-08|1:25|pendiente|no",))
    texto = mundo.llamar("preparar_plan", {"plan": plan})
    assert "no está guardado" in texto
    assert "«Behobia»" in texto and "ya está en el plan «Road to Valencia 26»" in texto
    # the «sí» seeing the warning saves it (Andrés decides); it is never left out in silence
    hecho = mundo.llamar("importar_plan", {"papel": "entrenador", "plan": plan, "huella": _huella(texto)})
    assert hecho.startswith("Hecho"), hecho


def test_otra_carrera_otro_dia_no_avisa(mundo):  # R3
    texto = mundo.llamar("preparar_plan", {"plan": _propuesto(carreras=("carrera|Behobia|2027-11-14|1:25||no",))})
    assert "ya está en el plan" not in texto


# -- R4 · R-352, C-445 ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("semanas", "dice"), [
    (("semana|1|2027-01-04|2027-01-10|6:00|base|||40", "semana|2|2027-01-17|2027-01-11|6:30|tirada|||45"),
     "semana 2"),
    (("semana|1|2027-01-04|2027-01-10|6:00|base|||40", "semana|2|2027-01-08|2027-01-14|6:30|tirada|||45"),
     "semana 2"),
], ids=["acaba-antes-de-empezar", "se-pisan"])
def test_semanas_imposibles_no_se_guardan_y_dice_cual(mundo, semanas, dice):  # R4
    antes = mundo.volcado()
    texto = mundo.llamar("preparar_plan", {"plan": _propuesto(*semanas)})
    assert "No se puede" in texto and dice in texto.lower(), texto
    assert "Huella" not in texto
    hecho = mundo.llamar("importar_plan", {"papel": "entrenador", "plan": _propuesto(*semanas), "huella": "0" * 12})
    assert not hecho.startswith("Hecho") and dice in hecho.lower()
    assert mundo.volcado() == antes


def test_las_semanas_que_se_pisan_nombran_las_dos():  # R4: the check itself, in the import
    apartados = [Apartado("Previsto", "previsto", ("semana|1|2027-01-04|2027-01-10|6:00|base",
                                                    "semana|2|2027-01-08|2027-01-14|6:30|tirada"), "x")]
    with pytest.raises(ErrorPlan, match=r"[Ss]emana 2.*semana 1"):
        proponer_ordenado(apartados, None, HOY, "Plan inventado")


# -- R5: the planned km of a week -------------------------------------------------------------------------------------

def test_los_km_previstos_en_la_base(tmp_path):  # R5
    planes = Planes(tmp_path / "data.sqlite3")
    ident = planes.crear(plan_de_prueba(), AHORA)
    assert all("previsto_km" not in s for s in planes.leer(ident)["semanas"])  # the ones of before, without km
    planes.anadir_semana(ident, {"n": 11, "desde": "2026-11-02", "hasta": "2026-11-08", "previsto": "4:30",
                                 "clave": "tirada", "km": "45,5"})
    assert next(s for s in planes.leer(ident)["semanas"] if s["n"] == 11)["previsto_km"] == 45.5
    planes.cambiar_semana(ident, 11, {"km": 50})
    assert next(s for s in planes.leer(ident)["semanas"] if s["n"] == 11)["previsto_km"] == 50.0
    planes.cambiar_semana(ident, 11, {"clave": "series"})  # not said, the km stay
    assert next(s for s in planes.leer(ident)["semanas"] if s["n"] == 11)["previsto_km"] == 50.0
    planes.cambiar_semana(ident, 11, {"km": ""})  # empty, none
    assert "previsto_km" not in next(s for s in planes.leer(ident)["semanas"] if s["n"] == 11)
    with pytest.raises(ValueError, match="km"):
        planes.cambiar_semana(ident, 11, {"km": "muchos"})
    assert all("previsto_km" not in s for s in planes.leer(ident)["semanas"] if s["n"] != 11)


def test_los_km_previstos_en_una_base_de_antes(tmp_path):  # R5: an older base gets the column, empty
    db = tmp_path / "data.sqlite3"
    ident = Planes(db).crear(plan_de_prueba(), AHORA)
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE plan_semana DROP COLUMN previsto_km")
    conn.commit()
    conn.close()
    planes = Planes(db)
    assert all("previsto_km" not in s for s in planes.leer(ident)["semanas"])
    planes.cambiar_semana(ident, 9, {"km": 42})
    assert next(s for s in planes.leer(ident)["semanas"] if s["n"] == 9)["previsto_km"] == 42.0


def test_los_km_previstos_desde_el_md(tmp_path):  # R5: the row of the .md with its km at the end
    apartados = [Apartado("Previsto", "previsto", ("semana|1|2027-01-04|2027-01-10|6:00|base|||40,5",
                                                    "semana|2|2027-01-11|2027-01-17|6:30|tirada"), "x")]
    propuesta = proponer_ordenado(apartados, None, HOY, "Plan inventado")
    assert [s.get("previsto_km") for s in propuesta["semanas"]] == [40.5, None]


def test_los_km_previstos_en_fitness_plan(mundo):  # R5: the web, its forms and its view
    app = crear_app(RegistroComidas(mundo.db), None, Ajustes(mundo.db), hoy=lambda: HOY, store=mundo.store)
    with TestClient(app) as cliente:
        r = cliente.post("/api/plan/semana/anadir", json={"id": mundo.zarautz, "n": 6, "desde": "2027-04-05",
                                                          "hasta": "2027-04-11", "previsto": "5:00", "clave": "",
                                                          "km": "48"})
        assert r.status_code == 200, r.text
        semanas = {s["n"]: s for s in r.json()["plan"]["semanas"]}
        assert semanas[6]["previsto_km"] == 48.0 and semanas[3]["previsto_km"] == 40.0
        assert "previsto_km" not in semanas[1]
        r = cliente.post("/api/plan/semana/cambiar", json={"id": mundo.zarautz, "semana": 6, "km": "52,5"})
        assert r.status_code == 200, r.text
        assert cliente.get(f"/api/plan/semana/6?id={mundo.zarautz}").json()["previsto_km"] == 52.5
        r = cliente.post("/api/plan/semana/cambiar", json={"id": mundo.zarautz, "semana": 6, "km": "mucho"})
        assert r.status_code == 400 and "km" in r.json()["error"]


# -- R6 · R-348, C-442: weeks and phases with their card ------------------------------------------------------------

def test_sube_la_semana_3_y_llama_base_a_las_cuatro_primeras(mundo):  # R6 · C-442
    valencia = mundo.planes.leer(mundo.valencia)
    cambio_km = {"accion": "plan_cambiar_semana", "plan": "Zarautz 27", "valor": 3, "cantidad": 45}
    texto = mundo.preparar(cambio_km)
    assert "semana 3" in texto and "40 km" in texto and "45 km" in texto and "Zarautz 27" in texto
    assert mundo.semana(mundo.zarautz, 3)["previsto_km"] == 40.0  # without the «sí», nothing
    assert mundo.hacer(cambio_km).startswith("Hecho")
    assert mundo.semana(mundo.zarautz, 3)["previsto_km"] == 45.0
    fase = {"accion": "plan_anadir_fase", "plan": "Zarautz 27", "objeto": "Base", "valores": [1, 4]}
    assert mundo.hacer(fase).startswith("Hecho")
    assert mundo.planes.leer(mundo.zarautz)["fases"] == [
        {"id": mundo.planes.leer(mundo.zarautz)["fases"][0]["id"], "nombre": "Base", "desde_n": 1, "hasta_n": 4}]
    assert mundo.planes.leer(mundo.valencia) == valencia  # the plan in course is not touched


def test_sin_el_si_nada_cambia(mundo):  # R6 · C-452
    antes = mundo.volcado()
    for cambio in ({"accion": "plan_cambiar_semana", "plan": "Zarautz 27", "valor": 5, "cantidad": 30},
                   {"accion": "plan_anadir_fase", "plan": "Zarautz 27", "objeto": "Base", "valores": [1, 4]},
                   {"accion": "plan_quitar_semana", "plan": "Zarautz 27", "valor": 2},
                   {"accion": "plan_anadir_semana", "plan": "Zarautz 27", "valor": 6, "desde": "2027-04-05",
                    "hasta": "2027-04-11"}):
        assert "Huella" in mundo.preparar(cambio)
    assert mundo.volcado() == antes


@pytest.mark.parametrize(("cambio", "dice"), [
    ({"valor": 2, "hasta": "2027-03-01"}, "antes de empezar"),
    ({"valor": 2, "hasta": "2027-03-16"}, "pisa la semana 3"),
    ({"valor": 9, "cantidad": 30}, "semana 9"),
], ids=["al-reves", "se-pisan", "no-esta"])
def test_fechas_imposibles_se_rechazan_diciendo_por_que(mundo, cambio, dice):  # R6
    antes = mundo.volcado()
    texto = mundo.preparar({"accion": "plan_cambiar_semana", "plan": "Zarautz 27", **cambio})
    assert texto.startswith("No se puede") and dice in texto, texto
    assert mundo.volcado() == antes


def test_anadir_y_quitar_semanas_y_fases(mundo):  # R6
    anadir = {"accion": "plan_anadir_semana", "plan": "Zarautz 27", "valor": 6, "desde": "2027-04-05",
              "hasta": "2027-04-11", "campo": "5:00", "cantidad": 50, "texto": "tirada larga"}
    assert mundo.hacer(anadir).startswith("Hecho")
    s6 = mundo.semana(mundo.zarautz, 6)
    assert (s6["desde"], s6["hasta"], s6["previsto_s"], s6["previsto_km"], s6["clave"]) == (
        "2027-04-05", "2027-04-11", 18000, 50.0, "tirada larga")
    pisa = mundo.preparar({**anadir, "valor": 7, "desde": "2027-04-10", "hasta": "2027-04-16"})
    assert pisa.startswith("No se puede") and "pisa la semana 6" in pisa
    # the Tuesday of week 2 goes to the watch; removing the week says it takes it and removes it from Garmin too
    assert mundo.hacer({"accion": "mandar_a_garmin", "plan": "Zarautz 27", "dia": "2027-03-09"}).startswith("Hecho")
    assert mundo.garmin.entrenos
    quitar = {"accion": "plan_quitar_semana", "plan": "Zarautz 27", "valor": 2}
    assert "1 sesión" in mundo.preparar(quitar)
    assert mundo.hacer(quitar).startswith("Hecho")
    assert [s["n"] for s in mundo.planes.leer(mundo.zarautz)["semanas"]] == [1, 3, 4, 5, 6]
    assert mundo.planes.leer(mundo.zarautz)["sesiones"] == [] and mundo.garmin.entrenos == {}
    assert mundo.hacer({"accion": "plan_anadir_fase", "plan": "Zarautz 27", "objeto": "Base", "valores": [1, 3]}
                       ).startswith("Hecho")
    cambiar = {"accion": "plan_cambiar_fase", "plan": "Zarautz 27", "objeto": "base", "texto": "Base aeróbica",
               "valores": [1, 4]}
    assert mundo.hacer(cambiar).startswith("Hecho")
    assert [(f["nombre"], f["desde_n"], f["hasta_n"]) for f in mundo.planes.leer(mundo.zarautz)["fases"]] == [
        ("Base aeróbica", 1, 4)]
    assert mundo.hacer({"accion": "plan_quitar_fase", "plan": "Zarautz 27", "objeto": "Base aeróbica"}
                       ).startswith("Hecho")
    assert "fases" not in mundo.planes.leer(mundo.zarautz)
    assert [s["n"] for s in mundo.planes.leer(mundo.zarautz)["semanas"]] == [1, 3, 4, 5, 6]  # the weeks stay


# -- R7 · R-349, C-443: sessions and the rest, of any plan -----------------------------------------------------------

def test_cambiar_una_sesion_de_zarautz_no_toca_valencia(mundo):  # R7 · C-443
    valencia = mundo.planes.leer(mundo.valencia)
    cambio = {"accion": "plan_cambiar_sesion", "plan": "Zarautz 27", "dia": "2027-03-09",
              "texto": "Series de prueba | 15' suave | 6×1 km a 4:05 (2' rec) | 10' suave"}
    assert "Zarautz 27" in mundo.preparar(cambio)
    assert mundo.hacer(cambio).startswith("Hecho")
    (sesion,) = mundo.planes.leer(mundo.zarautz)["sesiones"]
    assert sesion["titulo"] == "Series de prueba" and sesion["bloques"][1] == "6×1 km a 4:05 (2' rec)"
    assert mundo.planes.leer(mundo.valencia) == valencia


def test_las_demas_acciones_del_plan_aceptan_el_plan(mundo):  # R7
    valencia = mundo.planes.leer(mundo.valencia)
    plan = f"#{mundo.zarautz}"  # by its id too
    for cambio in ({"accion": "plan_anadir_sesion", "plan": plan, "dia": "2027-03-10", "texto": "Rodaje | 40' suave"},
                   {"accion": "plan_quitar_sesion", "plan": "Zarautz 27", "dia": "2027-03-09"},
                   {"accion": "plan_objetivo_carrera", "plan": "Zarautz 27", "objeto": "Zarautz", "texto": "2:20"},
                   {"accion": "plan_nota", "plan": "Zarautz 27", "texto": "Nota nueva inventada"},
                   {"accion": "guardar_estrategia", "plan": "Zarautz 27", "objeto": "Zarautz",
                    "texto": "km 0-10 · a 4:20 | km 10-21 · a 4:15"}):
        hecho = mundo.hacer(cambio)
        assert hecho.startswith("Hecho"), (cambio, hecho)
    zarautz = mundo.planes.leer(mundo.zarautz)
    assert [(s["fecha"], s["titulo"]) for s in zarautz["sesiones"]] == [("2027-03-10", "Rodaje")]
    assert zarautz["carreras"][0]["objetivo"] == "2:20"
    assert zarautz["carreras"][0]["estrategia"] == ["km 0-10 · a 4:20", "km 10-21 · a 4:15"]
    assert "Nota nueva inventada" in [n["texto"] for n in zarautz["notas"]]
    assert mundo.planes.leer(mundo.valencia) == valencia


def test_sin_plan_dicho_el_en_curso_como_hoy(mundo):  # R7
    assert mundo.hacer({"accion": "plan_nota", "texto": "Nota de Valencia inventada"}).startswith("Hecho")
    assert "Nota de Valencia inventada" in [n["texto"] for n in mundo.planes.leer(mundo.valencia)["notas"]]
    assert "Nota de Valencia inventada" not in [n["texto"] for n in mundo.planes.leer(mundo.zarautz)["notas"]]


def test_un_plan_que_no_existe_no_cambia_nada(mundo):  # R7
    antes = mundo.volcado()
    texto = mundo.preparar({"accion": "plan_nota", "plan": "Plan inexistente", "texto": "x"})
    assert texto.startswith("No se puede") and "Zarautz 27" in texto
    assert mundo.volcado() == antes


# -- R8, R9 · R-353..R-356, C-446..C-449: the strategy split in steps ------------------------------------------------

@pytest.mark.parametrize(("como", "cada", "esperado"), [
    ("tramos", None, ["5 km a 4:10", "3 km por esfuerzo", "6 km a 4:05", "6 km a 4:00"]),
    (None, None, ["5 km a 4:10", "3 km por esfuerzo", "6 km a 4:05", "6 km a 4:00"]),  # not said: its stretches
    ("km", 1, ["1 km a 4:10"] * 5 + ["1 km por esfuerzo"] * 3 + ["1 km a 4:05"] * 6 + ["1 km a 4:00"] * 6),
    ("km", 3, ["3 km a 4:10", "3 km por esfuerzo", "3 km por esfuerzo", "3 km a 4:05", "3 km a 4:03", "3 km a 4:00",
               "2 km a 4:00"]),
    ("km", 2.5, ["2,5 km a 4:10", "2,5 km a 4:10", "2,5 km por esfuerzo", "2,5 km por esfuerzo", "2,5 km a 4:05",
                 "2,5 km a 4:03", "2,5 km a 4:00", "2,5 km a 4:00"]),
    ("minutos", 15, ["15' a 4:10", "15' por esfuerzo", "15' por esfuerzo", "15' a 4:04", "15' a 4:00", "7' a 4:00"]),
], ids=["tramos", "sin-decir", "cada-km", "cada-3-km", "cada-2,5-km", "cada-15-minutos"])
def test_partir_la_estrategia(como, cada, esperado):  # R8 · C-446, C-447
    assert pasos(ESTRATEGIA, como, cada) == esperado


def test_un_paso_que_no_llega_al_km_va_en_metros():  # R8
    assert pasos(["km 0-1,4 · a 4:00"], "km", 1) == ["1 km a 4:00", "400 m a 4:00"]


@pytest.mark.parametrize(("lineas", "como", "cada", "dice"), [
    ([], "km", 1, "no tiene estrategia"),
    (["gel: km 8"], "km", 1, "no tiene estrategia"),
    (["km 0-5 · a 4:10", "subida de Miracruz · por esfuerzo", "km 8-20 · a 4:05"], "km", 1, "subida de Miracruz"),
    (["km 0-5 · a 4:10", "km 6-20 · a 4:05"], "km", 1, "entre el km 5 y el km 6"),
    (["km 0-5 · a 4:10", "km 5-8 · por esfuerzo", "km 8-20 · a 4:05"], "minutos", 15, "km 5-8"),
    (ESTRATEGIA, "minutos", 1, str(MAX_PASOS)),
    (ESTRATEGIA, "km", 0, "cada cuántos"),
], ids=["sin-estrategia", "solo-geles", "tramo-sin-km", "hueco", "esfuerzo-sin-tiempo-por-minutos",
        "demasiados-pasos", "cada-cero"])
def test_lo_que_falta_se_dice_y_no_se_parte(lineas, como, cada, dice):  # R9 · C-448, C-449
    with pytest.raises(EstrategiaMal) as fallo:
        pasos(lineas, como, cada)
    assert dice in str(fallo.value)


def test_demasiados_pasos_propone_juntar_tramos():  # R9 · R-356
    with pytest.raises(EstrategiaMal, match="junta"):
        pasos(ESTRATEGIA, "minutos", 1)
    assert MAX_PASOS == estrategia_reloj.MAX_PASOS == 50


@pytest.mark.parametrize(("como", "cada"), [("tramos", None), ("km", 1), ("km", 3), ("minutos", 15)])
def test_la_sesion_de_la_estrategia_vuelve_igual_de_garmin(como, cada):  # R8: «Garmin manda» never rewrites it
    from tridata.plan.reloj import _firma, a_garmin, de_garmin

    sesion = {"titulo": "Behobia · estrategia", "bloques": pasos(ESTRATEGIA, como, cada)}
    entreno = a_garmin(sesion)
    titulo, bloques = de_garmin(entreno)
    assert titulo == sesion["titulo"]
    assert _firma(a_garmin({"titulo": titulo, "bloques": bloques})) == _firma(entreno)


# -- R8, R10 · R-353, R-357, C-446, C-450: to the watch, and again -------------------------------------------------------

def _de_la_estrategia(mundo) -> list[dict]:
    return [s for s in mundo.planes.leer(mundo.valencia)["sesiones"] if s["fecha"] == "2026-11-08"]


def test_la_estrategia_al_reloj_un_paso_por_km(mundo):  # R8 · C-446
    cambio = {"accion": "estrategia_al_reloj", "objeto": "Behobia", "campo": "km", "valor": 1}
    texto = mundo.preparar(cambio)
    assert "Behobia · estrategia" in texto and "dom 8/11" in texto
    assert mundo.conexiones == 0 and _de_la_estrategia(mundo) == []  # preparing never talks to Garmin nor saves
    hecho = mundo.hacer(cambio)
    assert hecho.startswith("Hecho"), hecho
    (sesion,) = _de_la_estrategia(mundo)
    assert sesion["titulo"] == "Behobia · estrategia" and len(sesion["bloques"]) == 20
    (entreno,) = mundo.garmin.del_dia(BEHOBIA_DIA)
    assert entreno["workoutName"] == "Behobia · estrategia"
    pasos_reloj = entreno["workoutSegments"][0]["workoutSteps"]
    assert len(pasos_reloj) == 20
    assert all(p["endCondition"]["conditionTypeKey"] == "distance" and p["endConditionValue"] == 1000
               for p in pasos_reloj)
    assert pasos_reloj[0]["targetValueOne"] == pytest.approx(1000 / 255)  # a 4:10 ± 5"
    assert pasos_reloj[0]["targetValueTwo"] == pytest.approx(1000 / 245)
    assert pasos_reloj[5]["targetType"]["workoutTargetTypeKey"] == "no.target"  # the stretch by effort
    assert mundo.planes.sesiones_reloj(mundo.valencia)[-1]["estado"] == "al_dia"


def test_cada_15_minutos_va_por_tiempo(mundo):  # R8 · C-447
    hecho = mundo.hacer({"accion": "estrategia_al_reloj", "objeto": "Behobia", "campo": "minutos", "valor": 15})
    assert hecho.startswith("Hecho"), hecho
    (entreno,) = mundo.garmin.del_dia(BEHOBIA_DIA)
    pasos_reloj = entreno["workoutSegments"][0]["workoutSteps"]
    assert [p["endCondition"]["conditionTypeKey"] for p in pasos_reloj] == ["time"] * 6
    assert [p["endConditionValue"] for p in pasos_reloj] == [900] * 5 + [420]


def test_volver_a_mandarla_deja_una_sola_la_nueva(mundo):  # R10 · C-450
    assert mundo.hacer({"accion": "estrategia_al_reloj", "objeto": "Behobia", "campo": "km", "valor": 1}
                       ).startswith("Hecho")
    nueva = ["km 0-5 · a 4:12", "km 5-8 · por esfuerzo", "km 8-20 · a 4:02"]
    mundo.planes.guardar_estrategia(mundo.valencia, "Behobia", nueva)
    texto = mundo.preparar({"accion": "estrategia_al_reloj", "objeto": "Behobia"})
    assert "sustituye" in texto
    assert mundo.hacer({"accion": "estrategia_al_reloj", "objeto": "Behobia"}).startswith("Hecho")
    (sesion,) = _de_la_estrategia(mundo)
    assert sesion["bloques"] == ["5 km a 4:12", "3 km por esfuerzo", "12 km a 4:02"]
    (entreno,) = mundo.garmin.del_dia(BEHOBIA_DIA)
    assert len(entreno["workoutSegments"][0]["workoutSteps"]) == 3
    assert len(mundo.garmin.entrenos) == 1 and len(mundo.garmin.programados) == 1


def test_sin_estrategia_no_se_manda_nada(mundo):  # R9 · C-448
    antes = mundo.volcado()
    texto = mundo.preparar({"accion": "estrategia_al_reloj", "objeto": "Maratón de Valencia", "campo": "km",
                            "valor": 1})
    assert texto.startswith("No se puede") and "no tiene estrategia" in texto
    assert mundo.conexiones == 0 and mundo.volcado() == antes


def test_demasiados_pasos_no_se_manda_nada(mundo):  # R9 · C-449
    texto = mundo.preparar({"accion": "estrategia_al_reloj", "objeto": "Behobia", "campo": "minutos", "valor": 1})
    assert texto.startswith("No se puede") and "junta" in texto
    assert mundo.conexiones == 0 and _de_la_estrategia(mundo) == []


def test_si_garmin_falla_no_se_guarda_nada(mundo):  # R8: nothing half done
    mundo.garmin.falla_en = "subir_entreno"
    hecho = mundo.hacer({"accion": "estrategia_al_reloj", "objeto": "Behobia"})
    assert not hecho.startswith("Hecho") and "No ha llegado a tu Garmin" in hecho
    assert _de_la_estrategia(mundo) == []


# -- the list the coach reads, and the tools --------------------------------------------------------------------------

def test_las_acciones_nuevas_estan_en_la_lista_sin_cifras():  # G-38
    nuevas = {"plan_anadir_semana", "plan_cambiar_semana", "plan_quitar_semana", "plan_anadir_fase",
              "plan_cambiar_fase", "plan_quitar_fase", "estrategia_al_reloj"}
    assert nuevas <= set(catalogo())
    texto = texto_acciones()
    for accion in nuevas:
        assert f"- {accion}:" in texto
    assert re.search(r"\d", texto) is None
    estrategia = next(x for x in texto.splitlines() if x.startswith("- guardar_estrategia:"))
    assert "km" in estrategia and "ritmo" in estrategia and "reloj" in estrategia


def test_las_herramientas_dicen_como_disenar_y_leer_planes(mundo):  # R1, R2
    tools = {t.name: t for t in asyncio.run(mundo.server.list_tools())}
    assert tools["get_plan"].annotations.read_only_hint is True
    assert "design" in tools["preparar_plan"].description and "km" in tools["preparar_plan"].description
    assert "get_plan" in tools["preparar_cambio"].description and "get_plan" in tools["get_contexto"].description
