"""Unit 040 (REC-22 of `llevar-plan`): importing the plan's .md (`plan/importar.py` + `ia/plan.py`).

The code cuts the .md by its sections, the AI (always doubled here: `OrdenadorFalso`) orders each one into rows of
text, what fits nowhere goes to notes, and the weeks already done are crossed with Garmin. Nothing is saved until
Andrés confirms. R5, R6, R9 · C-173, C-174, C-177 · G-54. Every value is invented (`fixtures/plan-inventado.md`).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from tridata.ia.plan import ApartadoOrdenado, ErrorPlan, esquema_enviado
from tridata.models import Activity, ActivityLap
from tridata.plan import Planes
from tridata.plan import importar
from tridata.storage import DataStore

FIXTURE = Path(__file__).parent / "fixtures" / "plan-inventado.md"
HOY = date(2026, 10, 21)
AHORA = datetime(2026, 10, 21, 10)
USO = {"entrada": 900, "salida": 300, "cache_escritura": 0, "cache_lectura": 0}

_PARTES = (("objetivo", "objetivos"), ("semanas hechas", "semanas_hechas"), ("previsto", "previsto"),
           ("sesiones clave", "sesiones_clave"), ("nutrici", "nutricion"), ("incidencia", "incidencias"),
           ("comparativa", "comparativa"))
_TABLAS = {"carrera": "carrera", "semana": "semana", "producto": "nutricion"}


def ordenar_como_la_ia(titulo: str, texto: str) -> ApartadoOrdenado:
    """The double of the AI: it reads the invented .md (and the one `plan/markdown.py` writes) the way Claude is asked
    to — one row of text per thing, its kind first. A section it does not know is a «nota» without rows."""
    parte = next((p for clave, p in _PARTES if clave in titulo.lower()), "nota")
    if parte == "nota":
        return ApartadoOrdenado(parte="nota", filas=[])
    filas: list[str] = []
    tabla: str | None = None
    sesion: list[str] | None = None

    def cerrar() -> None:
        nonlocal sesion
        if sesion is not None:
            fecha, titulo_sesion, bloques = sesion[0], sesion[1], sesion[2:]
            clave = "sí" if parte == "sesiones_clave" else "no"
            filas.append(f"sesion|{fecha}|{titulo_sesion}|{'; '.join(bloques)}|{clave}")
            sesion = None

    for linea in texto.splitlines():
        linea = linea.strip()
        if linea.startswith("|"):
            celdas = [c.strip() for c in linea.strip("|").split("|")]
            if set("".join(celdas)) <= set("-: "):
                continue
            if tabla is None:
                tabla = _TABLAS.get(celdas[0].lower(), "nota")
                continue
            filas.append("|".join([tabla, *celdas]))
            continue
        tabla = None
        if linea.startswith("### "):
            cerrar()
            fecha, _, titulo_sesion = linea[4:].partition(" · ")
            sesion = [fecha, titulo_sesion]
        elif linea.startswith("- ") and sesion is not None:
            sesion.append(linea[2:])
        elif linea.startswith("- ") and parte == "incidencias":
            fecha, _, resto = linea[2:].partition(": ")
            filas.append(f"incidencia|{fecha}|{resto}")
        elif linea.startswith("- ") and parte == "comparativa":
            filas.append(f"comparativa|{linea[2:]}")
    cerrar()
    return ApartadoOrdenado(parte=parte, filas=filas)


class OrdenadorFalso:
    """`ordenar(titulo, texto, hoy) -> (ApartadoOrdenado, uso, modelo)`, as `ia.plan.OrdenadorPlan`; never the API."""

    modelo = "claude-haiku-5-5"

    def __init__(self, fallar_en: str | None = None) -> None:
        self.llamadas: list[str] = []
        self.fallar_en = fallar_en

    def ordenar(self, titulo: str, texto: str, hoy: date):
        self.llamadas.append(titulo)
        if self.fallar_en is not None and self.fallar_en in titulo:
            raise ErrorPlan("no hay conexión con la API")
        return ordenar_como_la_ia(titulo, texto), dict(USO), self.modelo


# the moving time (s) and km of each done week of the fixture, as Garmin has them: S4 has 6 minutes more
GARMIN = {1: (2 * 3600 + 50 * 60, 30.0), 2: (3 * 3600 + 5 * 60, 33.5), 3: (3 * 3600 + 10 * 60, 35.0),
          4: (3 * 3600 + 22 * 60, 39.1), 5: (3 * 3600 + 35 * 60 + 60, 40.2), 6: (3 * 3600 + 48 * 60, 42.2),
          7: (4 * 3600 + 5 * 60, 46.0)}
LUNES_S1 = date(2026, 8, 24)


def sembrar_garmin(store: DataStore) -> None:
    """One run on the Wednesday of S1-S7 (S5: 1 min more, S6: 0,2 km more — inside the tolerance) and the four runs
    of S8, the Wednesday one with 12 laps; plus a run today."""
    actividades = []
    for n, (segundos, km) in GARMIN.items():
        dia = LUNES_S1 + timedelta(weeks=n - 1, days=2)
        actividades.append(Activity(activity_id=f"s{n}", activity_date=dia, activity_type="running", name="Rodaje",
                                    duration_seconds=segundos + 60, distance_meters=km * 1000,
                                    moving_duration_seconds=segundos))
    for ident, dia, metros, segundos in (("r-lun", 12, 6000, 2100), ("r-mie", 14, 12000, 3720),
                                         ("r-vie", 16, 6200, 2100), ("r-dom", 18, 17000, 5400)):
        actividades.append(Activity(activity_id=ident, activity_date=date(2026, 10, dia), activity_type="running",
                                    name="Carrera de prueba", duration_seconds=segundos + 90,
                                    distance_meters=metros, moving_duration_seconds=segundos, avg_hr=148,
                                    avg_cadence=176, elevation_gain_m=40))
    actividades.append(Activity(activity_id="r-hoy", activity_date=HOY, activity_type="running", name="Series de hoy",
                                duration_seconds=3000, distance_meters=9000, moving_duration_seconds=2900))
    store.save_activities(actividades)
    store.save_activity_laps([ActivityLap(activity_id="r-mie", lap_index=i, distance_meters=1000, duration_seconds=310,
                                          avg_pace_seconds_per_km=310, avg_hr=140 + i, avg_cadence=170 + i,
                                          elevation_gain_m=3) for i in range(1, 13)])


@pytest.fixture
def store(tmp_path) -> DataStore:
    s = DataStore(tmp_path / "data.sqlite3")
    sembrar_garmin(s)
    return s


@pytest.fixture
def texto() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_trocear_no_pierde_ni_un_caracter(texto):  # R6 · C-174
    trozos = importar.trocear(texto)
    assert "".join(t.texto for t in trozos) == texto
    titulos = [t.titulo for t in trozos]
    assert titulos == ["", "Objetivos", "Semanas hechas", "Previsto", "Sesiones clave", "Nutrición en carrera",
                       "Incidencias", "Comparativa", "Ideas para 2028"]
    assert importar.nombre_del_plan(texto) == "Road to Ejemplo 26"
    assert "### 2026-10-14 · Series 4×1000" in trozos[4].cuerpo  # the ### stay inside their section


def test_cada_apartado_en_su_parte_y_el_raro_en_notas(texto, store):  # R5, R6 · C-174
    ordenador = OrdenadorFalso()
    gastos = []
    p = importar.proponer(texto, ordenador, store, HOY, al_gastar=lambda modelo, uso: gastos.append((modelo, uso)))
    assert len(ordenador.llamadas) == 9  # one call per section (the beginning too)
    assert len(gastos) == 9 and gastos[0] == ("claude-haiku-5-5", USO)  # the spending of each call is counted
    assert p["nombre"] == "Road to Ejemplo 26"
    assert [a["parte"] for a in p["apartados"]] == ["nota", "objetivos", "semanas_hechas", "previsto",
                                                    "sesiones_clave", "nutricion", "incidencias", "comparativa",
                                                    "nota"]
    assert [c["nombre"] for c in p["carreras"]] == ["10K de ejemplo", "15K de ejemplo", "Media de ejemplo",
                                                    "Maratón de ejemplo"]
    assert [c["principal"] for c in p["carreras"]] == [False, False, False, True]
    assert p["carreras"][0]["estado"] == "✅ 38:10" and p["carreras"][3]["fecha"] == "2026-12-06"
    assert [s["n"] for s in p["semanas"]] == list(range(1, 12))
    s4 = p["semanas"][3]
    assert (s4["desde"], s4["hasta"], s4["previsto_s"], s4["hecho_s"], s4["hecho_km"]) == (
        "2026-09-14", "2026-09-20", 3 * 3600 + 30 * 60, 3 * 3600 + 16 * 60, 39.1)
    assert p["semanas"][8]["hecho_s"] is None  # S9 is still to be done
    clave = [s for s in p["sesiones"] if s["clave"]]
    assert [s["titulo"] for s in clave] == ["Series 4×1000", "Tirada larga 1h30"]
    assert clave[0]["bloques"] == ["20' a 5:30", "4×1000 a 4:00 (2' rec)", "20' a 5:30"]
    assert len(p["sesiones"]) == 9
    assert [n["producto"] for n in p["nutricion"]] == ["Gel de ejemplo (25 g de hidratos)",
                                                       "Pastilla de sal de ejemplo"]
    assert [(i["fecha"], i["texto"]) for i in p["incidencias"]] == [
        ("2026-09-16", "molestia inventada en el gemelo, dos días sin correr"), ("2026-10-02", "resfriado inventado")]
    comparativa = [n for n in p["notas"] if n["tipo"] == "comparativa"]
    assert [n["texto"] for n in comparativa] == ["2025: semana tipo inventada de 3h40 y 40 km"]
    notas = [n for n in p["notas"] if n["tipo"] == "nota"]
    assert [p["apartados"][n["apartado"]]["titulo"] for n in notas] == ["", "Ideas para 2028"]
    assert notas[1]["texto"] == ("Correr una carrera inventada por montaña.\n"
                                 "Probar otra marca inventada de geles.")  # with its text, as it was
    assert p["resumen"] == {"carreras": 4, "semanas_hechas": 8, "previstas": 3, "sesiones_clave": 2,
                            "nutricion": 2, "incidencias": 2, "comparativa": 1, "notas": 2}


def test_lo_que_la_ia_no_sabe_leer_va_a_notas_tal_cual(store):  # R6: no piece of the .md is lost
    class Torpe(OrdenadorFalso):
        def ordenar(self, titulo, texto, hoy):
            self.llamadas.append(titulo)
            return ApartadoOrdenado(parte="objetivos", filas=["carrera|sin fecha ni nada", "esto no es una fila"]), USO, "m"

    texto = "# Plan raro\n\n## Objetivos\n\nAlgo que no es una tabla.\n"
    p = importar.proponer(texto, Torpe(), store, HOY)
    assert [c["nombre"] for c in p["carreras"]] == ["sin fecha ni nada"]
    assert [n["texto"] for n in p["notas"]] == ["esto no es una fila"]

    class Vacia(OrdenadorFalso):
        def ordenar(self, titulo, texto, hoy):
            return ApartadoOrdenado(parte="objetivos", filas=[]), USO, "m"

    p = importar.proponer(texto, Vacia(), store, HOY)
    assert p["carreras"] == [] and [n["texto"] for n in p["notas"]] == ["Algo que no es una tabla."]


def test_el_esquema_enviado_es_plano():  # R5: the 400 «grammar too large» of bug 036
    esquema = esquema_enviado()
    texto = repr(esquema)
    assert "anyOf" not in texto and "'null'" not in texto
    assert esquema["properties"]["filas"] == {**esquema["properties"]["filas"], "type": "array",
                                              "items": {"type": "string"}}
    assert esquema["properties"]["parte"]["type"] == "string"
    assert set(esquema["required"]) == {"parte", "filas"}

    def arrays(nodo):
        if isinstance(nodo, dict):
            if nodo.get("type") == "array":
                yield nodo
            for v in nodo.values():
                yield from arrays(v)
        elif isinstance(nodo, list):
            for v in nodo:
                yield from arrays(v)

    assert all(a["items"] == {"type": "string"} for a in arrays(esquema))  # no list of objects anywhere


def test_las_diferencias_con_garmin_proponen_lo_del_plan(texto, store):  # R5 · C-173 · G-54
    p = importar.proponer(texto, OrdenadorFalso(), store, HOY)
    # S4: 6 minutes more in Garmin; S5 (1 min) and S6 (0,2 km) are inside the tolerance (±2 min, ±0,3 km);
    # S8 matches (its four runs add up 3:42 and 41,2 km)
    assert [d["semana"] for d in p["diferencias"]] == [4]
    d = p["diferencias"][0]
    assert d["plan"] == {"segundos": 3 * 3600 + 16 * 60, "km": 39.1}
    assert d["garmin"] == {"segundos": 3 * 3600 + 22 * 60, "km": 39.1}
    assert d["propuesta"] == "plan"


def test_sin_garmin_la_semana_hecha_es_una_diferencia(texto, tmp_path):  # R5: «Garmin, nada»
    p = importar.proponer(texto, OrdenadorFalso(), DataStore(tmp_path / "vacio.sqlite3"), HOY)
    assert [d["semana"] for d in p["diferencias"]] == list(range(1, 9))
    assert p["diferencias"][0]["garmin"] == {"segundos": None, "km": None}


def test_hasta_confirmar_no_hay_plan_y_al_confirmar_lo_aceptado_gana(texto, store, tmp_path):  # R5 · C-173
    planes = Planes(tmp_path / "data.sqlite3")
    p = importar.proponer(texto, OrdenadorFalso(), store, HOY)
    assert planes.lista() == []  # nothing saved by proposing
    ident = importar.confirmar(p, {"4": "plan"}, planes, AHORA)
    plan = planes.leer(ident)
    s4 = next(s for s in plan["semanas"] if s["n"] == 4)
    assert (s4["hecho_plan_s"], s4["hecho_plan_km"]) == (3 * 3600 + 16 * 60, 39.1)
    s5 = next(s for s in plan["semanas"] if s["n"] == 5)
    assert (s5["hecho_plan_s"], s5["hecho_plan_km"]) == (None, None)  # no difference: Garmin's
    otro = importar.confirmar(p, {"4": "garmin"}, planes, AHORA)
    s4 = next(s for s in planes.leer(otro)["semanas"] if s["n"] == 4)
    assert (s4["hecho_plan_s"], s4["hecho_plan_km"]) == (None, None)
    assert len(planes.lista()) == 2


def test_si_la_ia_falla_no_se_guarda_nada(texto, store, tmp_path):  # R5: retry later
    planes = Planes(tmp_path / "data.sqlite3")
    gastos = []
    with pytest.raises(ErrorPlan):
        importar.proponer(texto, OrdenadorFalso(fallar_en="Nutrición"), store, HOY,
                          al_gastar=lambda modelo, uso: gastos.append(modelo))
    assert planes.lista() == []
    assert len(gastos) == 8  # what was answered is paid anyway


def test_los_registros_no_imprimen_el_plan(store, caplog):  # R9 · C-177
    marcador = "MARCADOR-INVENTADO-7Q2"
    texto = f"# Plan {marcador}\n\n## Objetivos\n\n| Carrera | Fecha |\n|---|---|\n| {marcador} | mal |\n\n## Raro\n\n{marcador}\n"
    caplog.set_level("DEBUG")
    importar.proponer(texto, OrdenadorFalso(), store, HOY)
    with pytest.raises(ErrorPlan):
        importar.proponer(texto, OrdenadorFalso(fallar_en="Raro"), store, HOY)
    assert marcador not in caplog.text
    assert not re.search(r"Ideas|gemelo", caplog.text)
