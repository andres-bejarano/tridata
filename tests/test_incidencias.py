"""Unit 044 (REC-24 of `seguir-molestia`): the incidents of the plan come alive — open one, add the visits to the
physio or the doctor, close it with its lesson — and the notes of a day show in what was done that day.

R1-R4, R6, R7 · R-145..R-148, R-150, R-162 · C-187..C-190, C-192, C-204, C-205. Unit level over `plan.Planes` with a
temporary SQLite and, through the web, Fitness › Plan › Incidencias (`GET /api/plan/incidencias`, closing by hand)
and the notes in a week. The AI is always doubled. Every value is invented.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from test_plan import plan_de_prueba
from tridata.plan import Planes
from tridata.plan import semanas
from tridata.plan.markdown import escribir

AHORA = datetime(2026, 10, 22, 9, 0)
HOY = AHORA.date()
IMPORTADA = {"apartado": 1, "fecha": "2026-09-23", "texto": "rampas inventadas en los gemelos (piscina)"}


@pytest.fixture
def planes(tmp_path) -> Planes:
    return Planes(tmp_path / "data.sqlite3")


@pytest.fixture
def plan_id(planes) -> int:
    return planes.crear(plan_de_prueba(incidencias=[IMPORTADA]), AHORA)


def _abrir(planes: Planes, plan_id: int, **cambios) -> int:
    datos = {"zona": "tobillo", "lado": "derecho, lateral", "desde": date(2026, 10, 20),
             "que_paso": "molestia inventada en el lateral al acabar el rodaje", **cambios}
    return planes.abrir_incidencia(plan_id, datos["zona"], datos["lado"], datos["desde"], datos["que_paso"], AHORA)


# -- R1-R4 · the life of an incident --------------------------------------------------------------------------------

def test_una_incidencia_se_abre_con_zona_lado_desde_y_que_paso(planes, plan_id):  # R1 · C-187
    ident = _abrir(planes, plan_id)
    i = planes.incidencia(ident)
    assert i is not None and i["plan_id"] == plan_id
    assert (i["zona"], i["lado"], i["desde"], i["que_paso"]) == (
        "tobillo", "derecho, lateral", "2026-10-20", "molestia inventada en el lateral al acabar el rodaje")
    assert i["titulo"] == "tobillo derecho, lateral"
    assert (i["estado"], i["leccion"], i["visitas"], i["importada"]) == ("abierta", None, [], False)
    # it is in the plan: the .md of «Bajar .md» writes it among the incidents
    assert any("tobillo derecho, lateral" in x["texto"] for x in planes.leer(plan_id)["incidencias"])
    assert "tobillo derecho, lateral" in escribir(planes.leer(plan_id), None, HOY)


def test_las_visitas_van_a_su_incidencia_en_orden(planes, plan_id):  # R2 · C-188
    ident = _abrir(planes, plan_id)
    planes.anadir_visita(ident, date(2026, 10, 22), "fisio", "sin lesión inventada, alta para correr", AHORA)
    planes.anadir_visita(ident, date(2026, 10, 21), "médico", "reposo relativo inventado", AHORA)
    assert planes.incidencia(ident)["visitas"] == [
        {"fecha": "2026-10-21", "quien": "médico", "que_dijo": "reposo relativo inventado"},
        {"fecha": "2026-10-22", "quien": "fisio", "que_dijo": "sin lesión inventada, alta para correr"}]


def test_cerrada_con_su_leccion_deja_de_estar_abierta(planes, plan_id):  # R4 · C-190
    ident = _abrir(planes, plan_id)
    assert [i["id"] for i in planes.abiertas()] == [ident]
    planes.cerrar_incidencia(ident, "bici nunca tras la tirada larga", HOY)
    i = planes.incidencia(ident)
    assert (i["estado"], i["leccion"], i["cerrada"]) == ("cerrada", "bici nunca tras la tirada larga", "2026-10-22")
    assert planes.abiertas() == []


def test_abiertas_primero_y_las_del_md_como_cerradas(planes, plan_id):  # R3 · C-189
    vieja = _abrir(planes, plan_id, zona="tobillo", desde=date(2026, 9, 24))
    planes.cerrar_incidencia(vieja, "bici nunca tras la tirada larga", date(2026, 9, 30))
    abierta = _abrir(planes, plan_id, zona="gemelo", lado="izquierdo, parte baja", desde=date(2026, 10, 20),
                     que_paso="presión inventada en reposo que se va al correr")
    lista = planes.incidencias(plan_id)
    assert [(i["titulo"], i["estado"]) for i in lista] == [
        ("gemelo izquierdo, parte baja", "abierta"), ("tobillo derecho, lateral", "cerrada"),
        ("rampas inventadas en los gemelos (piscina)", "cerrada")]
    assert lista[0]["id"] == abierta
    del_md = lista[2]
    assert (del_md["importada"], del_md["desde"], del_md["leccion"]) == (True, "2026-09-23", None)


def test_el_plan_se_lee_igual_que_antes(planes, plan_id):  # the 040 keeps its promise (R7 of the 040)
    leido = planes.leer(plan_id)
    assert leido["incidencias"] == [IMPORTADA]


# -- R6 · C-192: the notes of a day ------------------------------------------------------------------------------

def test_la_nota_del_dia_sale_en_lo_hecho_de_ese_dia(planes, plan_id):  # R6 · C-192
    planes.nota_del_dia(plan_id, date(2026, 10, 21), "FC alta para el ritmo, piernas del domingo", AHORA)
    notas = planes.notas_del_dia(plan_id)
    assert notas == {"2026-10-21": ["FC alta para el ritmo, piernas del domingo"]}
    vista = semanas.semana(planes.leer(plan_id), 9, None, HOY, notas=notas)
    por_dia = {d["fecha"]: d["notas"] for d in vista["dias"]}
    assert por_dia["2026-10-21"] == ["FC alta para el ritmo, piernas del domingo"]
    assert por_dia["2026-10-20"] == []
    assert semanas.semana(planes.leer(plan_id), 9, None, HOY)["dias"][2]["notas"] == []  # without notes given


# -- through the web: Fitness › Plan › Incidencias -------------------------------------------------------------------

def _web(tmp_path):
    from test_conversaciones_web import Web

    web = Web(tmp_path)
    planes = Planes(web.db)
    plan_id = planes.crear(plan_de_prueba(incidencias=[IMPORTADA]), AHORA)
    return web, planes, plan_id


def test_la_pantalla_de_incidencias(tmp_path):  # R3 · C-189
    web, planes, plan_id = _web(tmp_path)
    ident = _abrir(planes, plan_id, zona="gemelo", lado="izquierdo")
    planes.anadir_visita(ident, date(2026, 10, 22), "fisio", "sin lesión inventada", AHORA)
    r = web.cliente.get("/api/plan/incidencias")
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["plan"] == {"id": plan_id, "nombre": "Road to Ejemplo 26"}
    primera, segunda = datos["incidencias"]
    assert (primera["titulo"], primera["estado"], primera["desde"]) == ("gemelo izquierdo", "abierta", "2026-10-20")
    assert primera["visitas"] == [{"fecha": "2026-10-22", "quien": "fisio", "que_dijo": "sin lesión inventada"}]
    assert (segunda["estado"], segunda["importada"]) == ("cerrada", True)
    vista = web.cliente.get("/api/plan").json()["plan"]
    assert vista["abiertas"] == 1  # «⚠️ Incidencias (1)»


def test_sin_plan_la_pantalla_esta_vacia(tmp_path):  # R3
    from test_conversaciones_web import Web

    r = Web(tmp_path).cliente.get("/api/plan/incidencias")
    assert r.status_code == 200 and r.json() == {"plan": None, "incidencias": []}


def test_cerrar_a_mano_con_su_leccion(tmp_path):  # R4 · C-190
    web, planes, plan_id = _web(tmp_path)
    ident = _abrir(planes, plan_id)
    r = web.cliente.post(f"/api/plan/incidencia/{ident}/cerrar", json={"leccion": "  "})
    assert r.status_code == 400 and "lección" in r.json()["error"]
    assert planes.incidencia(ident)["estado"] == "abierta"  # nothing without its lesson
    r = web.cliente.post(f"/api/plan/incidencia/{ident}/cerrar", json={"leccion": "bici nunca tras la tirada larga"})
    assert r.status_code == 200, r.text
    cerrada = next(i for i in r.json()["incidencias"] if i["id"] == ident)
    assert (cerrada["estado"], cerrada["leccion"], cerrada["cerrada"]) == (
        "cerrada", "bici nunca tras la tirada larga", "2026-10-24")  # the day of the web (`Web`)
    assert web.cliente.post(f"/api/plan/incidencia/{ident}/cerrar", json={"leccion": "otra"}).status_code == 409
    assert web.cliente.post("/api/plan/incidencia/999999/cerrar", json={"leccion": "x"}).status_code == 404


def test_la_semana_trae_las_notas_de_cada_dia(tmp_path):  # R6 · C-192
    web, planes, plan_id = _web(tmp_path)
    planes.nota_del_dia(plan_id, date(2026, 10, 21), "FC alta para el ritmo, piernas del domingo", AHORA)
    dias = web.cliente.get("/api/plan/semana/9").json()["dias"]
    assert next(d for d in dias if d["fecha"] == "2026-10-21")["notas"] == ["FC alta para el ritmo, piernas del domingo"]


# -- R7 · C-205: the alarm, with the Fisio --------------------------------------------------------------------------

def test_ante_una_alarma_el_fisio_manda_al_medico_sin_diagnosticar(tmp_path):  # R7 · R-162, C-205
    from test_conversaciones_web import Web

    web = Web(tmp_path)
    web.claude.responder("Con hinchazón y calor, ve al médico antes de correr.")
    web.nueva("la pantorrilla está hinchada y caliente", papel="fisio")
    papel = web.claude.peticiones[0]["system"][-1]["text"]  # the role goes after the cache mark (041)
    assert "Fisio" in papel
    for regla in ("hinchazón", "calor", "rojez", "va a más en reposo", "ir al médico antes de entrenar",
                  "sin diagnosticar"):
        assert regla in papel, regla
