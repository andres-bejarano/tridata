"""Unit 040 (REC-22 of `llevar-plan`): the routes of Fitness › Plan (`web/plan.py`).

    GET  /api/plan                 the plan of the current objective (or ?id=) and the list of plans
    GET  /api/plan/semana/{n}      the days of one week: the planned session and what was done
    POST /api/plan/importar        the proposal of an .md, with its differences with Garmin; saves nothing
    POST /api/plan/confirmar       saves the proposal with what Andrés chose
    GET  /api/plan/{id}.md         the plan as a document

R1, R4, R5, R7, R8, R9 · C-169, C-172, C-173, C-175, C-176, C-177. The AI is the double of test_plan_importar.py;
every value is invented.
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient
from test_plan_importar import FIXTURE, OrdenadorFalso, sembrar_garmin

from tridata.conversaciones import Conversaciones
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroComidas
from tridata.plan import Planes
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 10, 21)
AHORA = datetime(2026, 10, 21, 10)


class Web:
    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        ajustes = Ajustes(self.db)
        ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000}, desde=DESDE_SIEMPRE)
        self.store = DataStore(self.db)
        sembrar_garmin(self.store)
        self.ordenador = OrdenadorFalso()
        self.cliente = TestClient(crear_app(RegistroComidas(self.db), MagicMock(), ajustes, hoy=lambda: HOY,
                                            objetivos=ObjetivosDia(self.db), store=self.store,
                                            actualizar_garmin=MagicMock(), ahora=lambda: AHORA,
                                            ordenador_plan=self.ordenador))

    def importar(self, texto: str | None = None) -> dict:
        r = self.cliente.post("/api/plan/importar", json={"texto": texto or FIXTURE.read_text(encoding="utf-8"),
                                                          "fichero": "plan-inventado.md"})
        assert r.status_code == 200, r.text
        return r.json()

    def confirmar(self, propuesta: dict, elecciones: dict | None = None) -> dict:
        r = self.cliente.post("/api/plan/confirmar", json={"token": propuesta["token"],
                                                           "elecciones": elecciones or {}})
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def web(tmp_path) -> Web:
    return Web(tmp_path)


def test_sin_plan_todavia(web):  # R1
    r = web.cliente.get("/api/plan")
    assert r.status_code == 200
    assert r.json() == {"hoy": HOY.isoformat(), "plan": None, "planes": []}


def test_importar_propone_y_no_guarda_hasta_confirmar(web):  # R5 · C-173
    p = web.importar()
    assert p["nombre"] == "Road to Ejemplo 26" and p["fichero"] == "plan-inventado.md"
    assert [d["semana"] for d in p["diferencias"]] == [4]
    assert p["resumen"]["carreras"] == 4
    assert web.cliente.get("/api/plan").json()["plan"] is None  # nothing saved yet
    assert Conversaciones(web.db).gasto_del_mes(AHORA) > 0  # the spending of «importar plan» counts for the cap

    vista = web.confirmar(p, {"4": "plan"})
    plan = vista["plan"]
    assert plan["nombre"] == "Road to Ejemplo 26"
    assert [(c["nombre"], c["estado"]) for c in plan["carreras"]][0] == ("10K de ejemplo", "✅ 38:10")
    assert [s["n"] for s in plan["semanas"] if s["hoy"]] == [9]
    s4 = next(s for s in plan["semanas"] if s["n"] == 4)
    assert s4["hecho"]["fuente"] == "plan" and s4["hecho"]["carrera"]["segundos"] == 3 * 3600 + 16 * 60
    assert web.cliente.get("/api/plan").json()["plan"]["id"] == plan["id"]
    # a proposal is confirmed once
    r = web.cliente.post("/api/plan/confirmar", json={"token": p["token"], "elecciones": {}})
    assert r.status_code == 404


def test_lo_enviado_mal_no_importa_nada(web):  # R5
    assert web.cliente.post("/api/plan/importar", json={"texto": "  "}).status_code == 400
    assert web.cliente.post("/api/plan/confirmar", json={"token": "no-existe"}).status_code == 404
    assert web.cliente.post("/api/plan/confirmar", content=b"no es json").status_code == 400


def test_si_la_ia_falla_no_hay_plan_y_se_puede_reintentar(web):  # R5
    web.ordenador.fallar_en = "Previsto"
    r = web.cliente.post("/api/plan/importar", json={"texto": FIXTURE.read_text(encoding="utf-8")})
    assert r.status_code == 502
    assert "Inténtalo de nuevo" in r.json()["error"]
    assert web.cliente.get("/api/plan").json()["plan"] is None
    web.ordenador.fallar_en = None
    assert web.importar()["diferencias"]


def test_al_tope_del_mes_no_se_importa(web):  # the cap of unit 032 (R9) and bug 038
    Conversaciones(web.db).apuntar_gasto(AHORA, "claude-sonnet-5-5", {"entrada": 7_500_000, "salida": 0}, "mensaje")  # 15 $ (unit 041)
    r = web.cliente.post("/api/plan/importar", json={"texto": FIXTURE.read_text(encoding="utf-8")})
    assert r.status_code == 429 and r.json()["tope"] is True
    assert web.ordenador.llamadas == []


def test_la_semana_y_el_detalle_de_lo_hecho(web):  # R3, R4 · C-171, C-172
    web.confirmar(web.importar())
    r = web.cliente.get("/api/plan/semana/8")
    assert r.status_code == 200
    semana = r.json()
    assert (semana["n"], semana["desde"], semana["hasta"]) == (8, "2026-10-12", "2026-10-18")
    martes, miercoles = semana["dias"][1], semana["dias"][2]
    assert martes["sesiones"][0]["titulo"] == "40' suaves" and martes["estado"] == "no_hecho"
    assert miercoles["sesiones"][0]["titulo"] == "Series 4×1000"
    hecha = miercoles["actividades"][0]
    assert hecha["actividad_id"] == "r-mie"
    # the same detail as Fitness: its 12 laps, with pace and HR…
    detalle = web.cliente.get(f"/api/actividad/{hecha['actividad_id']}").json()
    assert len(detalle["vueltas"]) == 12 and detalle["vueltas"][0]["ritmo_s_km"] == 310
    # …and, for the plan, the cadence and the climb of each lap too
    plan = web.cliente.get(f"/api/plan/actividad/{hecha['actividad_id']}").json()
    assert len(plan["vueltas"]) == 12
    assert plan["vueltas"][0]["cadencia"] == 171 and plan["vueltas"][0]["desnivel_m"] == 3
    assert plan["vueltas"][0]["pulso_medio"] == 141
    assert (plan["actividad"]["cadencia"], plan["actividad"]["desnivel_m"]) == (176, 40)
    assert web.cliente.get("/api/plan/semana/99").status_code == 404
    assert web.cliente.get("/api/plan/actividad/no-existe").status_code == 404


def test_varios_planes_y_cambiar_de_uno_a_otro(web):  # R8 · C-176
    ejemplo = web.confirmar(web.importar())["plan"]["id"]
    otro_texto = (FIXTURE.read_text(encoding="utf-8").replace("Road to Ejemplo 26", "Road to Otro 27")
                  .replace("Maratón de ejemplo | 2026-12-06", "Maratón de otro | 2027-06-06"))
    otro = web.confirmar(web.importar(otro_texto))["plan"]["id"]
    datos = web.cliente.get("/api/plan").json()
    assert datos["plan"]["id"] == ejemplo  # the next main race is Ejemplo's
    assert [(p["id"], p["nombre"], p["en_curso"]) for p in datos["planes"]] == [
        (ejemplo, "Road to Ejemplo 26", True), (otro, "Road to Otro 27", False)]
    assert web.cliente.get(f"/api/plan?id={otro}").json()["plan"]["nombre"] == "Road to Otro 27"
    assert web.cliente.get(f"/api/plan/semana/8?id={otro}").json()["n"] == 8
    assert web.cliente.get("/api/plan?id=999").status_code == 404


def _sin(filas: list[dict], *claves: str) -> list[dict]:
    return [{k: v for k, v in f.items() if k not in claves} for f in filas]


def test_bajar_el_md_y_volver_a_importarlo_igual(web):  # R7 · C-175
    ident = web.confirmar(web.importar(), {"4": "plan"})["plan"]["id"]
    r = web.cliente.get(f"/api/plan/{ident}.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"] and ".md" in r.headers["content-disposition"]
    md = r.text
    assert md.startswith("# Road to Ejemplo 26\n")
    titulos = [linea[3:] for linea in md.splitlines() if linea.startswith("## ")]
    assert titulos == ["Objetivos", "Semanas hechas", "Previsto", "Sesiones clave", "Nutrición en carrera",
                       "Incidencias", "Comparativa", "Ideas para 2028"]
    assert "Correr una carrera inventada por montaña." in md
    assert "| 4 | 2026-09-14 | 2026-09-20 | 3:30 | tempo 30' | 3:16 | 39,1 |" in md

    from tridata.plan import Planes
    guardado = Planes(web.db).leer(ident)
    otra = web.importar(md)
    assert otra["nombre"] == guardado["nombre"]
    assert otra["apartados"] == guardado["apartados"]
    for parte in ("carreras", "sesiones", "nutricion", "incidencias", "notas"):
        assert otra[parte] == guardado[parte], parte
    campos = ("n", "desde", "hasta", "previsto_s", "clave")
    assert [{k: s[k] for k in campos} for s in otra["semanas"]] == [{k: s[k] for k in campos}
                                                                      for s in guardado["semanas"]]
    assert [d["semana"] for d in otra["diferencias"]] == [4]  # what was accepted from the plan, again
    assert web.cliente.get("/api/plan/999.md").status_code == 404


def test_nada_del_plan_en_los_registros(web, caplog):  # R9 · C-177
    marcador = "MARCADOR-INVENTADO-W9K"
    caplog.set_level("DEBUG")
    texto = FIXTURE.read_text(encoding="utf-8").replace("resfriado inventado", f"resfriado {marcador}")
    ident = web.confirmar(web.importar(texto))["plan"]["id"]
    web.cliente.get("/api/plan")
    web.cliente.get("/api/plan/semana/8")
    assert marcador in web.cliente.get(f"/api/plan/{ident}.md").text
    web.ordenador.fallar_en = "Incidencias"
    web.cliente.post("/api/plan/importar", json={"texto": texto})
    assert marcador not in caplog.text


def test_la_estrategia_de_una_carrera_se_ve_en_fitness_plan(web):  # unit 045 · R6 (R-156, C-198)
    vista = web.confirmar(web.importar(), {"4": "plan"})
    plan_id = vista["plan"]["id"]
    carrera = vista["plan"]["carreras"][-1]["nombre"]
    assert all(c["estrategia"] == [] for c in vista["plan"]["carreras"])
    Planes(web.db).guardar_estrategia(plan_id, carrera, ["km 0-4 · llano · 4:15/km · paso 17:00", "gel: km 6,5"])
    carreras = web.cliente.get("/api/plan").json()["plan"]["carreras"]
    assert carreras[-1]["estrategia"] == ["km 0-4 · llano · 4:15/km · paso 17:00", "gel: km 6,5"]
    assert all(c["estrategia"] == [] for c in carreras[:-1])
