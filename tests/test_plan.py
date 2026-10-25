"""Unit 040 (REC-22 of `llevar-plan`): lo previsto frente a lo hecho of each week of the plan (`plan/semanas.py`) and
the plans kept in the local SQLite (`plan.Planes`).

R1, R2, R3, R8 · C-169, C-170, C-171, C-176 · G-54. Every value is invented.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from tridata.models import Activity
from tridata.plan import Planes
from tridata.plan import semanas
from tridata.storage import DataStore

HOY = date(2026, 10, 21)  # Wednesday of S9
LUNES_S8, DOMINGO_S8 = date(2026, 10, 12), date(2026, 10, 18)


def plan_de_prueba(**cambios) -> dict:
    """A plan as `Planes.leer` gives it: two weeks (S8 past, S9 of today), its sessions by day and two races."""
    plan = {
        "id": 1, "nombre": "Road to Ejemplo 26",
        "apartados": [{"titulo": "Objetivos", "parte": "objetivos"}, {"titulo": "Semanas", "parte": "previsto"}],
        "carreras": [
            {"apartado": 0, "nombre": "10K de ejemplo", "fecha": "2026-10-11", "objetivo": "mejor marca",
             "estado": "✅ 38:10", "principal": False},
            {"apartado": 0, "nombre": "Maratón de ejemplo", "fecha": "2026-12-06", "objetivo": "3:05",
             "estado": "pendiente", "principal": True},
        ],
        "semanas": [
            {"apartado": 1, "n": 8, "desde": "2026-10-12", "hasta": "2026-10-18", "previsto_s": 13800,
             "clave": "4×1000 · tirada 1h30", "hecho_plan_s": None, "hecho_plan_km": None},
            {"apartado": 1, "n": 9, "desde": "2026-10-19", "hasta": "2026-10-25", "previsto_s": 16200,
             "clave": "4×1000 + 15K", "hecho_plan_s": None, "hecho_plan_km": None},
            {"apartado": 1, "n": 10, "desde": "2026-10-26", "hasta": "2026-11-01", "previsto_s": 21600,
             "clave": "cuestas", "hecho_plan_s": None, "hecho_plan_km": None},
        ],
        "sesiones": [
            {"apartado": 1, "fecha": "2026-10-12", "titulo": "easy 35'", "bloques": ["35' suave"], "clave": False},
            {"apartado": 1, "fecha": "2026-10-13", "titulo": "40' suaves", "bloques": ["40' a 5:40"], "clave": False},
            {"apartado": 1, "fecha": "2026-10-14", "titulo": "Series 4×1000",
             "bloques": ["20' a 5:30", "4×1000 a 4:00 (2' rec)", "20' a 5:30"], "clave": True},
        ],
        "nutricion": [], "incidencias": [], "notas": [],
    }
    plan.update(cambios)
    return plan


def _carrera(ident: str, dia: date, metros: float, movimiento: float | None, total: float, **extra) -> Activity:
    return Activity(activity_id=ident, activity_date=dia, activity_type="running", name="Carrera de prueba",
                    duration_seconds=total, distance_meters=metros, moving_duration_seconds=movimiento, **extra)


@pytest.fixture
def store(tmp_path) -> DataStore:
    s = DataStore(tmp_path / "data.sqlite3")
    s.save_activities([
        _carrera("r-lun", date(2026, 10, 12), 6000, 2100, 2160, avg_hr=131),
        _carrera("r-mie", date(2026, 10, 14), 12000, 3720, 3900, avg_hr=150),
        _carrera("r-vie", date(2026, 10, 16), 6200, 2100, 2200),
        _carrera("r-dom", date(2026, 10, 18), 17000, None, 5500),  # no moving time: the week uses the total
        Activity(activity_id="b-sab", activity_date=date(2026, 10, 17), activity_type="cycling", name="Bici de prueba",
                 duration_seconds=3600, distance_meters=30000, moving_duration_seconds=3500),
        _carrera("r-hoy", HOY, 8000, 2700, 2800),
        _carrera("r-fuera", date(2026, 10, 11), 10000, 2290, 2300),  # the Sunday before S8: not in it
    ])
    return s


def test_lo_hecho_de_la_semana_es_carrera_en_movimiento_y_la_bici_aparte(store):  # R2 · C-170
    hecho = semanas.hecho(store, LUNES_S8, DOMINGO_S8, HOY)
    carrera = hecho["carrera"]
    assert carrera["n"] == 4
    assert carrera["segundos"] == 2100 + 3720 + 2100 + 5500  # moving time; the total of the one without it
    assert carrera["km"] == pytest.approx(41.2)
    assert carrera["tiempo_total"] is True  # said: one activity had no moving time
    assert hecho["bici"] == {"n": 1, "segundos": 3500, "distancia_m": 30000}
    assert hecho["natacion"]["n"] == 0 and hecho["gimnasio"]["n"] == 0
    assert hecho["fuente"] == "garmin"


def test_sin_actividades_sin_tiempo_en_movimiento_no_dice_tiempo_total(store):  # R2
    hecho = semanas.hecho(store, date(2026, 10, 19), date(2026, 10, 25), HOY)
    assert hecho["carrera"] == {"n": 1, "segundos": 2700, "km": 8.0, "tiempo_total": False}
    assert hecho["hasta"] == HOY.isoformat()  # this week, done up to today


def test_una_semana_futura_no_tiene_hecho(store):  # R2: nothing invented, never a 0
    assert semanas.hecho(store, date(2026, 10, 26), date(2026, 11, 1), HOY) is None


def test_el_plan_con_sus_carreras_y_la_semana_de_hoy_marcada(store):  # R1 · C-169
    vista = semanas.vista(plan_de_prueba(), store, HOY)
    assert vista["nombre"] == "Road to Ejemplo 26"
    assert [(c["nombre"], c["estado"]) for c in vista["carreras"]] == [
        ("10K de ejemplo", "✅ 38:10"), ("Maratón de ejemplo", "pendiente")]
    assert [s["n"] for s in vista["semanas"] if s["hoy"]] == [9]
    s8, s9, s10 = vista["semanas"]
    assert s8["previsto_s"] == 13800 and s8["clave"] == "4×1000 · tirada 1h30"
    assert s8["hecho"]["carrera"]["n"] == 4
    assert s9["hecho"]["carrera"]["segundos"] == 2700
    assert s10["hecho"] is None


def test_lo_aceptado_del_plan_gana_a_garmin(store):  # G-54
    plan = plan_de_prueba()
    plan["semanas"][0].update(hecho_plan_s=13500, hecho_plan_km=41.0)
    s8 = semanas.vista(plan, store, HOY)["semanas"][0]
    assert s8["hecho"]["fuente"] == "plan"
    assert (s8["hecho"]["carrera"]["segundos"], s8["hecho"]["carrera"]["km"]) == (13500, 41.0)
    assert s8["hecho"]["bici"]["n"] == 1  # the other sports still come from Garmin


def test_los_dias_de_una_semana_con_su_sesion_y_lo_hecho(store):  # R3 · C-171
    vista = semanas.semana(plan_de_prueba(), 8, store, HOY)
    assert vista["n"] == 8 and len(vista["dias"]) == 7
    lunes, martes, miercoles, jueves = vista["dias"][:4]
    assert lunes["sesiones"][0]["titulo"] == "easy 35'"
    assert lunes["actividades"][0]["actividad_id"] == "r-lun"
    assert lunes["actividades"][0]["segundos"] == 2100 and lunes["actividades"][0]["pulso_medio"] == 131
    assert lunes["estado"] == "hecho"
    assert martes["sesiones"][0]["titulo"] == "40' suaves"
    assert martes["actividades"] == [] and martes["estado"] == "no_hecho"  # never a 0
    assert miercoles["sesiones"][0]["bloques"] == ["20' a 5:30", "4×1000 a 4:00 (2' rec)", "20' a 5:30"]
    assert miercoles["actividades"][0]["ritmo_s_km"] == 310
    assert jueves["sesiones"] == [] and jueves["estado"] == "no_hecho"


def test_los_dias_de_la_semana_de_hoy_y_los_que_faltan(store):  # R3
    vista = semanas.semana(plan_de_prueba(), 9, store, HOY)
    estados = [d["estado"] for d in vista["dias"]]
    assert estados == ["no_hecho", "no_hecho", "hecho", "pendiente", "pendiente", "pendiente", "pendiente"]
    assert semanas.semana(plan_de_prueba(), 99, store, HOY) is None


def _datos(nombre: str, principal: str) -> dict:
    return {"nombre": nombre, "apartados": [{"titulo": "Objetivos", "parte": "objetivos"}],
            "carreras": [{"apartado": 0, "nombre": f"Maratón de {nombre}", "fecha": principal, "objetivo": "3:05",
                          "estado": "pendiente", "principal": True}],
            "semanas": [], "sesiones": [], "nutricion": [], "incidencias": [], "notas": []}


def test_varios_planes_y_el_del_objetivo_en_curso(tmp_path):  # R8 · C-176
    planes = Planes(tmp_path / "data.sqlite3")
    assert planes.lista() == [] and planes.en_curso(HOY) is None
    ejemplo = planes.crear(_datos("Ejemplo 26", "2026-12-06"), datetime(2026, 10, 21, 10))
    otro = planes.crear(_datos("Otro 27", "2027-07-04"), datetime(2026, 10, 21, 11))
    assert [p["nombre"] for p in planes.lista()] == ["Ejemplo 26", "Otro 27"]
    assert planes.en_curso(date(2026, 11, 15)) == ejemplo  # the next main race is Ejemplo's
    assert planes.en_curso(date(2026, 12, 20)) == otro  # Ejemplo's race is over
    assert planes.en_curso(date(2027, 8, 1)) == otro  # all over: the last one
    leido = planes.leer(ejemplo)
    assert leido["carreras"][0]["nombre"] == "Maratón de Ejemplo 26" and leido["carreras"][0]["principal"] is True
    assert planes.leer(999) is None


def test_un_plan_guardado_se_lee_entero(tmp_path):  # R7: what is kept is what comes back
    planes = Planes(tmp_path / "data.sqlite3")
    datos = plan_de_prueba()
    datos.pop("id")
    datos["nutricion"] = [{"apartado": 1, "producto": "Gel de ejemplo", "pauta": "min 30"}]
    datos["incidencias"] = [{"apartado": 1, "fecha": "2026-10-02", "texto": "resfriado inventado"}]
    datos["notas"] = [{"apartado": 1, "tipo": "nota", "texto": "algo inventado"}]
    ident = planes.crear(datos, datetime(2026, 10, 21, 10))
    leido = planes.leer(ident)
    assert leido == {**datos, "id": ident}
