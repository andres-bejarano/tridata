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


# -- unit 059: the result of a race comes from Garmin, its name, date, target and time are edited, and its strategy
# goes with the plan (R1-R5 · R-127, C-169). Every race, time and distance is invented. ---------------------------

DIA_CARRERA = date(2026, 10, 18)  # a Sunday before HOY


def _una(ident: str, tipo: str, metros: float, segundos: float, dia: date = DIA_CARRERA) -> Activity:
    return Activity(activity_id=ident, activity_date=dia, activity_type=tipo, name="Actividad de prueba",
                    duration_seconds=segundos, distance_meters=metros, moving_duration_seconds=segundos - 30)


def _plan_con(*carreras: dict) -> dict:
    return plan_de_prueba(carreras=[{"apartado": 0, "objetivo": "", "estado": "pendiente", "principal": False, **c}
                                    for c in carreras])


def _estado(store, *carreras: dict, hoy: date = HOY) -> list[dict]:
    return semanas.vista(_plan_con(*carreras), store, hoy)["carreras"]


@pytest.fixture
def dia_de_carrera(tmp_path) -> DataStore:
    """The race day: a warm-up, the race and a cool-down (all running), plus a ride to get there."""
    s = DataStore(tmp_path / "carrera.sqlite3")
    s.save_activities([
        _una("calentar", "running", 2000, 720),
        _una("la-carrera", "running", 15000, 3900),
        _una("soltar", "running", 1500, 600),
    ])
    return s


def test_una_carrera_corrida_sale_hecha_con_la_mas_larga_de_ese_dia(dia_de_carrera):  # R1 · R-127, C-169
    (c,) = _estado(dia_de_carrera, {"nombre": "Carrera de ejemplo", "fecha": DIA_CARRERA.isoformat()})
    assert c["resultado"] == {"tiempo": "1:05:00", "km": 15.0, "origen": "garmin"}
    assert c["estado"].startswith("✅") and "1:05:00" in c["estado"] and "15,0 km" in c["estado"]


def test_una_carrera_de_menos_de_una_hora_se_dice_en_minutos(tmp_path):  # R1
    s = DataStore(tmp_path / "corta.sqlite3")
    s.save_activities([_una("diez", "running", 10000, 2290)])
    (c,) = _estado(s, {"nombre": "10K de ejemplo", "fecha": DIA_CARRERA.isoformat()})
    assert c["resultado"] == {"tiempo": "38:10", "km": 10.0, "origen": "garmin"}


def test_lo_escrito_a_mano_gana_a_garmin(dia_de_carrera):  # R2 · R-127
    importada, a_mano = _estado(
        dia_de_carrera,
        {"nombre": "Carrera de ejemplo", "fecha": DIA_CARRERA.isoformat(), "estado": "✅ 1:04:30"},
        {"nombre": "Otra de ejemplo", "fecha": DIA_CARRERA.isoformat(), "resultado": "1:04:45"})
    assert importada["estado"] == "✅ 1:04:30"
    assert importada["resultado"] == {"tiempo": "1:04:30", "km": None, "origen": "a mano"}
    assert a_mano["estado"] == "✅ 1:04:45"
    assert a_mano["resultado"] == {"tiempo": "1:04:45", "km": None, "origen": "a mano"}


def test_sin_actividad_ese_dia_sigue_pendiente_u_hoy(store, dia_de_carrera):  # R1: nothing invented
    pasada, hoy, futura = _estado(
        store,
        {"nombre": "Pasada sin nada", "fecha": "2026-10-15"},  # a Thursday without activity in `store`
        {"nombre": "La de hoy", "fecha": HOY.isoformat(), "estado": ""},
        {"nombre": "Futura de ejemplo", "fecha": "2026-11-08"})
    assert (pasada["estado"], pasada["resultado"]) == ("pendiente", None)
    assert futura["estado"] == "pendiente" and futura["resultado"] is None
    # today, before the race is synced: «hoy»; with the activity of today, done
    sin_sincronizar = _estado(dia_de_carrera, {"nombre": "La de hoy", "fecha": HOY.isoformat(), "estado": ""})
    assert sin_sincronizar[0]["estado"] == "hoy"
    assert hoy["resultado"]["origen"] == "garmin" and hoy["resultado"]["km"] == 8.0  # `store` has a run today


def test_la_bici_de_ir_no_cuenta_en_una_carrera_a_pie(tmp_path):  # R5
    s = DataStore(tmp_path / "bici.sqlite3")
    s.save_activities([_una("ir", "cycling", 22000, 3000), _una("la-media", "running", 21100, 5400)])
    for nombre in ("Media de ejemplo", "Behobia de ejemplo", "12K de ejemplo", "Maratón de ejemplo",
                   "Trail de ejemplo"):
        (c,) = _estado(s, {"nombre": nombre, "fecha": DIA_CARRERA.isoformat()})
        assert (c["resultado"]["tiempo"], c["resultado"]["km"]) == ("1:30:00", 21.1), nombre
    # a name that does not say the sport: the longest of the day, whatever it is
    (c,) = _estado(s, {"nombre": "Prueba de ejemplo", "fecha": DIA_CARRERA.isoformat()})
    assert c["resultado"]["km"] == 22.0
    # a run race and only a ride that day: not done
    solo_bici = DataStore(tmp_path / "solo-bici.sqlite3")
    solo_bici.save_activities([_una("ir", "cycling", 22000, 3000)])
    (c,) = _estado(solo_bici, {"nombre": "10K de ejemplo", "fecha": DIA_CARRERA.isoformat()})
    assert c["resultado"] is None and c["estado"] == "pendiente"


def test_un_triatlon_es_el_multideporte_o_la_suma_del_dia(tmp_path):  # R5
    suelto = DataStore(tmp_path / "suelto.sqlite3")
    suelto.save_activities([_una("nadar", "open_water_swimming", 1900, 2100), _una("bici", "cycling", 90000, 9000),
                            _una("correr", "running", 21100, 5700)])
    for nombre in ("Half de ejemplo", "Triatlón de ejemplo", "Ironman de ejemplo"):
        (c,) = _estado(suelto, {"nombre": nombre, "fecha": DIA_CARRERA.isoformat()})
        assert c["resultado"] == {"tiempo": "4:40:00", "km": 113.0, "origen": "garmin"}, nombre
    junto = DataStore(tmp_path / "junto.sqlite3")
    junto.save_activities([_una("tri", "multi_sport", 113000, 17100), _una("soltar", "running", 2000, 700)])
    (c,) = _estado(junto, {"nombre": "Half de ejemplo", "fecha": DIA_CARRERA.isoformat()})
    assert c["resultado"] == {"tiempo": "4:45:00", "km": 113.0, "origen": "garmin"}


def _guardado(tmp_path) -> tuple[Planes, int]:
    planes = Planes(tmp_path / "data.sqlite3")
    datos = plan_de_prueba()
    datos.pop("id")
    datos["carreras"].append({"apartado": 0, "nombre": "Half de ejemplo", "fecha": "2027-06-05", "objetivo": "5:00",
                              "estado": "pendiente", "principal": False})
    return planes, planes.crear(datos, datetime(2026, 10, 21, 10))


def test_editar_una_carrera_lleva_su_estrategia_con_ella(tmp_path):  # R3
    planes, ident = _guardado(tmp_path)
    planes.guardar_estrategia(ident, "Half de ejemplo", ["natación · por sensaciones", "gel: km 30 de bici"])
    planes.editar_carrera(ident, "Half de ejemplo",
                          {"nombre": "Half de otro sitio", "fecha": "2027-06-12", "objetivo": "4:55"})
    carrera = planes.leer(ident)["carreras"][2]
    assert (carrera["nombre"], carrera["fecha"], carrera["objetivo"]) == ("Half de otro sitio", "2027-06-12", "4:55")
    assert carrera["estrategia"] == ["natación · por sensaciones", "gel: km 30 de bici"]
    assert planes.estrategia(ident, "Half de otro sitio") == ["natación · por sensaciones", "gel: km 30 de bici"]
    assert planes.estrategia(ident, "Half de ejemplo") == []
    assert [c["nombre"] for c in planes.leer(ident)["carreras"]] == [
        "10K de ejemplo", "Maratón de ejemplo", "Half de otro sitio"]  # same place, the others untouched


def test_editar_una_carrera_no_guarda_un_nombre_repetido_ni_una_fecha_mala(tmp_path):  # R3
    planes, ident = _guardado(tmp_path)
    antes = planes.leer(ident)
    with pytest.raises(ValueError, match="Maratón de ejemplo"):
        planes.editar_carrera(ident, "Half de ejemplo", {"nombre": "Maratón de ejemplo"})
    for mala in ("2027-02-30", "5/6/2027", "mañana"):
        with pytest.raises(ValueError, match="fecha"):
            planes.editar_carrera(ident, "Half de ejemplo", {"fecha": mala})
    with pytest.raises(ValueError, match="nombre"):
        planes.editar_carrera(ident, "Half de ejemplo", {"nombre": "   "})
    with pytest.raises(LookupError):
        planes.editar_carrera(ident, "No existe de ejemplo", {"objetivo": "4:50"})
    assert planes.leer(ident) == antes  # nothing saved


def test_el_tiempo_a_mano_se_escribe_y_se_borra(tmp_path, dia_de_carrera):  # R2, R3
    planes, ident = _guardado(tmp_path)
    planes.editar_carrera(ident, "10K de ejemplo", {"fecha": DIA_CARRERA.isoformat()})
    assert planes.leer(ident)["carreras"][0]["estado"] == "✅ 38:10"  # what the .md said is kept
    planes.editar_carrera(ident, "10K de ejemplo", {"resultado": ""})  # erased: Garmin's
    (c, *_) = semanas.vista(planes.leer(ident), dia_de_carrera, HOY)["carreras"]
    assert c["resultado"] == {"tiempo": "1:05:00", "km": 15.0, "origen": "garmin"}
    planes.editar_carrera(ident, "10K de ejemplo", {"resultado": " 38:05 "})  # the official one, again his
    (c, *_) = semanas.vista(planes.leer(ident), dia_de_carrera, HOY)["carreras"]
    assert (c["estado"], c["resultado"]["origen"]) == ("✅ 38:05", "a mano")


def test_la_estrategia_va_en_la_vista_del_plan(tmp_path):  # R4
    planes, ident = _guardado(tmp_path)
    planes.guardar_estrategia(ident, "Maratón de ejemplo", ["km 0-10 · 4:25/km", "gel: km 10"])
    carreras = semanas.vista(planes.leer(ident), None, HOY)["carreras"]
    assert [c["estrategia"] for c in carreras] == [[], ["km 0-10 · 4:25/km", "gel: km 10"], []]


def test_la_estrategia_y_el_tiempo_a_mano_van_en_el_md(tmp_path):  # R4
    from tridata.plan.markdown import escribir

    planes, ident = _guardado(tmp_path)
    planes.guardar_estrategia(ident, "Maratón de ejemplo", ["km 0-10 · 4:25/km", "gel: km 10"])
    planes.editar_carrera(ident, "Half de ejemplo", {"resultado": "4:58:00"})
    md = escribir(planes.leer(ident), None, HOY)
    assert "| Half de ejemplo | 2027-06-05 | 5:00 | ✅ 4:58:00 | no |" in md
    tabla = md.index("| Maratón de ejemplo |")
    estrategia = md.index("Estrategia de Maratón de ejemplo")
    assert tabla < estrategia < md.index("## Semanas")
    assert "- km 0-10 · 4:25/km\n- gel: km 10" in md[estrategia:]


# -- unit 063: plans by hand — a new plan, renamed or deleted, and its races added, removed, moved to another plan or
# made the main one (R1-R8 · R-127, R-133, R-134, R-135). Every plan, race and date is invented. -------------------

AHORA_063 = datetime(2026, 10, 30, 9)


def _dos_planes(tmp_path) -> tuple[Planes, int, int]:
    """«Road to Ejemplo 26» (two races, the main one in December) and a new «Medio de ejemplo 27» with its own."""
    planes, ejemplo = _guardado(tmp_path)
    medio = planes.nuevo("Medio de ejemplo 27", {"nombre": "Provisional de ejemplo", "fecha": "2027-09-12",
                                                 "objetivo": "acabar"}, AHORA_063)
    return planes, ejemplo, medio


def test_un_plan_nuevo_con_su_principal_se_baja_y_se_reimporta_igual(tmp_path):  # R1 · R-133, R-134
    from test_plan_importar import OrdenadorFalso

    from tridata.plan import importar
    from tridata.plan.markdown import escribir

    planes = Planes(tmp_path / "data.sqlite3")
    ident = planes.nuevo("  Medio de ejemplo   27 ", {"nombre": "Medio de ejemplo", "fecha": "2027-09-12",
                                                      "objetivo": "5:30"}, AHORA_063)
    assert [(p["id"], p["nombre"], p["principal"]) for p in planes.lista()] == [
        (ident, "Medio de ejemplo 27", "2027-09-12")]
    plan = planes.leer(ident)
    assert plan["carreras"] == [{"apartado": 0, "nombre": "Medio de ejemplo", "fecha": "2027-09-12",
                                 "objetivo": "5:30", "estado": "pendiente", "principal": True}]
    assert plan["semanas"] == [] and plan["sesiones"] == []
    vista = semanas.vista(plan, None, HOY)
    assert vista["semanas"] == [] and vista["carreras"][0]["principal"] is True

    md = escribir(plan, None, HOY)
    otra = importar.proponer(md, OrdenadorFalso(), None, HOY)
    assert otra["nombre"] == plan["nombre"]
    assert [a["titulo"] for a in otra["apartados"]] == [a["titulo"] for a in plan["apartados"]]
    for parte in ("carreras", "semanas", "sesiones", "nutricion", "incidencias", "notas"):
        assert otra[parte] == plan[parte], parte


def test_un_plan_nuevo_sin_nombre_o_con_fecha_mala_no_se_guarda(tmp_path):  # R1
    planes = Planes(tmp_path / "data.sqlite3")
    carrera = {"nombre": "Medio de ejemplo", "fecha": "2027-09-12", "objetivo": ""}
    with pytest.raises(ValueError, match="nombre"):
        planes.nuevo("   ", carrera, AHORA_063)
    with pytest.raises(ValueError, match="nombre"):
        planes.nuevo("Medio de ejemplo 27", {**carrera, "nombre": " "}, AHORA_063)
    for mala in ("2027-02-30", "12/9/2027", "", None):
        with pytest.raises(ValueError, match="fecha"):
            planes.nuevo("Medio de ejemplo 27", {**carrera, "fecha": mala}, AHORA_063)
    assert planes.lista() == []


def test_renombrar_un_plan(tmp_path):  # R2
    planes, ejemplo, medio = _dos_planes(tmp_path)
    planes.renombrar(medio, "  Medio de otro sitio 27 ")
    assert [p["nombre"] for p in planes.lista()] == ["Road to Ejemplo 26", "Medio de otro sitio 27"]
    with pytest.raises(ValueError, match="nombre"):
        planes.renombrar(medio, "  ")
    with pytest.raises(LookupError):
        planes.renombrar(999, "Otro de ejemplo")
    assert planes.leer(medio)["nombre"] == "Medio de otro sitio 27"


def test_pasar_una_carrera_a_otro_plan_se_lleva_su_objetivo_resultado_y_estrategia(tmp_path):  # R5 (portante)
    planes, ejemplo, medio = _dos_planes(tmp_path)
    planes.guardar_estrategia(ejemplo, "Half de ejemplo", ["natación · por sensaciones", "gel: km 30 de bici"])
    planes.editar_carrera(ejemplo, "Half de ejemplo", {"resultado": "4:58:00"})
    planes.mover_carrera(ejemplo, "Half de ejemplo", medio)

    assert [c["nombre"] for c in planes.leer(ejemplo)["carreras"]] == ["10K de ejemplo", "Maratón de ejemplo"]
    assert planes.estrategia(ejemplo, "Half de ejemplo") == []
    destino = planes.leer(medio)
    assert [c["nombre"] for c in destino["carreras"]] == ["Provisional de ejemplo", "Half de ejemplo"]
    half = destino["carreras"][1]
    assert (half["fecha"], half["objetivo"], half["resultado"]) == ("2027-06-05", "5:00", "4:58:00")
    assert half["estrategia"] == ["natación · por sensaciones", "gel: km 30 de bici"]
    assert half["apartado"] == destino["carreras"][0]["apartado"]  # among the races of the destination
    assert half["principal"] is False
    # in the .md of the destination, with its strategy and its time
    from tridata.plan.markdown import escribir

    md = escribir(destino, None, HOY)
    assert "| Half de ejemplo | 2027-06-05 | 5:00 | ✅ 4:58:00 | no |" in md
    assert "Estrategia de Half de ejemplo:" in md


def test_pasar_una_carrera_que_ya_esta_en_el_destino_no_se_hace(tmp_path):  # R5
    planes, ejemplo, medio = _dos_planes(tmp_path)
    planes.anadir_carrera(medio, {"nombre": "Half de ejemplo", "fecha": "2027-05-01", "objetivo": ""})
    antes = (planes.leer(ejemplo), planes.leer(medio))
    with pytest.raises(ValueError, match="Half de ejemplo"):
        planes.mover_carrera(ejemplo, "Half de ejemplo", medio)
    with pytest.raises(ValueError):
        planes.mover_carrera(ejemplo, "Half de ejemplo", ejemplo)  # the same plan
    with pytest.raises(LookupError):
        planes.mover_carrera(ejemplo, "No existe de ejemplo", medio)
    with pytest.raises(LookupError):
        planes.mover_carrera(ejemplo, "Half de ejemplo", 999)
    assert (planes.leer(ejemplo), planes.leer(medio)) == antes


def test_anadir_y_quitar_una_carrera(tmp_path):  # R4, R5
    planes, ejemplo, medio = _dos_planes(tmp_path)
    planes.anadir_carrera(medio, {"nombre": " Sprint de ejemplo ", "fecha": "2027-04-18", "objetivo": "1:10"})
    carreras = planes.leer(medio)["carreras"]
    assert [(c["nombre"], c["fecha"], c["objetivo"], c["estado"], c["principal"]) for c in carreras] == [
        ("Provisional de ejemplo", "2027-09-12", "acabar", "pendiente", True),
        ("Sprint de ejemplo", "2027-04-18", "1:10", "pendiente", False)]
    with pytest.raises(ValueError, match="Sprint de ejemplo"):
        planes.anadir_carrera(medio, {"nombre": "Sprint de ejemplo", "fecha": "2027-05-01", "objetivo": ""})
    with pytest.raises(ValueError, match="nombre"):
        planes.anadir_carrera(medio, {"nombre": "  ", "fecha": "2027-05-01", "objetivo": ""})
    with pytest.raises(ValueError, match="fecha"):
        planes.anadir_carrera(medio, {"nombre": "Otra de ejemplo", "fecha": "2027-13-01", "objetivo": ""})
    with pytest.raises(LookupError):
        planes.anadir_carrera(999, {"nombre": "Otra de ejemplo", "fecha": "2027-05-01", "objetivo": ""})
    # the same name in ANOTHER plan is fine
    planes.anadir_carrera(ejemplo, {"nombre": "Sprint de ejemplo", "fecha": "2027-04-18", "objetivo": ""})

    planes.quitar_carrera(medio, "Sprint de ejemplo")
    assert [c["nombre"] for c in planes.leer(medio)["carreras"]] == ["Provisional de ejemplo"]
    assert "Sprint de ejemplo" in [c["nombre"] for c in planes.leer(ejemplo)["carreras"]]
    with pytest.raises(LookupError):
        planes.quitar_carrera(medio, "Sprint de ejemplo")


def test_hacer_principal_deja_una_sola_y_sin_principal_no_cuenta(tmp_path):  # R6, R8 · R-134
    planes, ejemplo, medio = _dos_planes(tmp_path)
    planes.mover_carrera(ejemplo, "Half de ejemplo", medio)
    planes.hacer_principal(medio, "Half de ejemplo")
    assert [(c["nombre"], c["principal"]) for c in planes.leer(medio)["carreras"]] == [
        ("Provisional de ejemplo", False), ("Half de ejemplo", True)]
    planes.quitar_carrera(medio, "Provisional de ejemplo")
    assert [p["principal"] for p in planes.lista()] == ["2026-12-06", "2027-06-05"]
    with pytest.raises(LookupError):
        planes.hacer_principal(medio, "No existe de ejemplo")

    # R8: the main race moved away leaves its plan without one; none is picked by itself
    planes.mover_carrera(ejemplo, "Maratón de ejemplo", medio)
    assert all(not c["principal"] for c in planes.leer(ejemplo)["carreras"])
    assert [(c["nombre"], c["principal"]) for c in planes.leer(medio)["carreras"]] == [
        ("Half de ejemplo", True), ("Maratón de ejemplo", False)]  # never two in the destination
    assert planes.lista()[0]["principal"] is None
    # a plan without a main race does not count for the current one, even if it is the only one with races to come
    assert planes.en_curso(HOY) == medio
    planes.quitar_carrera(medio, "Half de ejemplo")  # R8: removing the main one, the same
    assert all(not c["principal"] for c in planes.leer(medio)["carreras"])


def test_plan_en_curso_con_dos_planes_que_no_se_solapan(tmp_path):  # R7 · R-134
    planes, ejemplo, medio = _dos_planes(tmp_path)
    planes.mover_carrera(ejemplo, "Half de ejemplo", medio)
    planes.hacer_principal(medio, "Half de ejemplo")
    assert planes.en_curso(HOY) == ejemplo  # until its race (2026-12-06)
    assert planes.en_curso(date(2026, 12, 6)) == ejemplo
    assert planes.en_curso(date(2026, 12, 7)) == medio  # then the one of the half
    assert planes.en_curso(date(2027, 10, 1)) == medio  # all over: the one whose race was last


def test_borrar_un_plan_no_deja_ninguna_fila_suya(tmp_path):  # R3 · R-135
    import sqlite3

    planes, ejemplo, medio = _dos_planes(tmp_path)
    datos = plan_de_prueba()
    datos.pop("id")
    datos["nutricion"] = [{"apartado": 1, "producto": "Gel de ejemplo", "pauta": "min 30"}]
    datos["incidencias"] = [{"apartado": 1, "fecha": "2026-10-02", "texto": "resfriado inventado"}]
    datos["notas"] = [{"apartado": 1, "tipo": "nota", "texto": "algo inventado"}]
    completo = planes.crear(datos, AHORA_063)
    incidencia = planes.abrir_incidencia(completo, "tobillo", "derecho", date(2026, 10, 20), "torcedura inventada",
                                         AHORA_063)
    planes.anadir_visita(incidencia, date(2026, 10, 22), "fisio", "reposo inventado", AHORA_063)
    planes.nota_del_dia(completo, date(2026, 10, 20), "nota inventada", AHORA_063)
    otra = planes.abrir_incidencia(medio, "rodilla", None, date(2026, 10, 21), "molestia inventada", AHORA_063)
    planes.anadir_visita(otra, date(2026, 10, 23), "médico", "nada inventado", AHORA_063)
    planes.nota_del_dia(medio, date(2026, 10, 21), "otra nota inventada", AHORA_063)
    antes = {i: planes.leer(i) for i in (ejemplo, medio)}

    planes.borrar(completo)
    assert planes.leer(completo) is None
    assert [p["id"] for p in planes.lista()] == [ejemplo, medio]
    conn = sqlite3.connect(tmp_path / "data.sqlite3")
    for tabla in ("plan_carrera", "plan_semana", "plan_sesion", "plan_nutricion", "plan_incidencia", "plan_nota",
                  "plan_nota_dia"):
        assert conn.execute(f"SELECT COUNT(*) FROM {tabla} WHERE plan_id = ?", (completo,)).fetchone() == (0,), tabla  # noqa: S608
    assert conn.execute("SELECT COUNT(*) FROM plan_visita WHERE incidencia = ?", (incidencia,)).fetchone() == (0,)
    conn.close()
    # the other plans, untouched
    assert {i: planes.leer(i) for i in (ejemplo, medio)} == antes
    assert [x["que_paso"] for x in planes.incidencias(medio)] == ["molestia inventada"]
    assert planes.incidencias(medio)[0]["visitas"] == [{"fecha": "2026-10-23", "quien": "médico",
                                                        "que_dijo": "nada inventado"}]
    assert planes.notas_del_dia(medio) == {"2026-10-21": ["otra nota inventada"]}
    with pytest.raises(LookupError):
        planes.borrar(completo)
