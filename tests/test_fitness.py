"""Unit 031 (REC-19 of `ver-fitness`): the load of 14 days, the last activity with its laps, zones and weather, and
the recovery — with the HR max, HR rest and zones confirmed in Ajustes with the date from which they apply.

Plans v6: R-105..R-111 · C-146..C-152 · G-47, G-49. R1-R6 and R8 of the unit. Every value is invented: the HR, the
zones, the activities, the nights. Nothing here reads `.private/`.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from tridata import fitness
from tridata.hoy import como_llegas
from tridata.metrics import compute_pmc, daily_tss, trimp_for_activity
from tridata.models import Activity, ActivityLap, DailyStats, HRVRecord, SleepRecord
from tridata.nutricion import DESDE_SIEMPRE, Ajustes
from tridata.storage import DataStore
from tridata.sync import ALL_SPORT_TYPES

HOY = date(2026, 10, 21)
AYER = HOY - timedelta(days=1)
ENTORNO = (190, 60)  # the .env of the tests (invented)
ZONAS_A = [[55, 120], [121, 140], [141, 155], [156, 168], [169, 180]]
PULSO_A = {"fc_max": 180, "fc_reposo": 55, "zonas_fc": ZONAS_A, "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}
ZONAS_B = [[50, 118], [119, 139], [140, 156], [157, 170], [171, 186]]
PULSO_B = {"fc_max": 186, "fc_reposo": 50, "zonas_fc": ZONAS_B}


@pytest.fixture(autouse=True)
def _entorno(monkeypatch):
    monkeypatch.setenv("TRIDATA_HR_MAX", str(ENTORNO[0]))
    monkeypatch.setenv("TRIDATA_HR_REST", str(ENTORNO[1]))


def sembrar(store: DataStore, hoy: date = HOY, *, historia: int = 60) -> None:
    """`historia` days of invented sessions; yesterday a run with 10 laps and weather; 7 nights of sleep and HRV
    (today's sleep half-done: no score yet); resting HR of the 6 days before today (today's has not arrived)."""
    ayer = hoy - timedelta(days=1)
    store.save_activities([
        Activity(activity_id=f"h{i:03d}", activity_date=hoy - timedelta(days=i), activity_type="cycling",
                 name="Rodaje de prueba", duration_seconds=3600, distance_meters=30000, avg_hr=130 + i % 9)
        for i in range(2, historia, 2)
    ] + [Activity(activity_id="c-ayer", activity_date=ayer, activity_type="running", name="Carrera de prueba",
                  duration_seconds=3000, distance_meters=10000, avg_hr=147, calories=610)])
    store.save_activity_laps([
        ActivityLap(activity_id="c-ayer", lap_index=i, distance_meters=1000, duration_seconds=300,
                    avg_pace_seconds_per_km=300, avg_hr=125 + 5 * i) for i in range(10)])
    store.save_activity_weather("c-ayer", {"temp_c": 14.0, "apparent_temp_c": 13.0, "humidity_pct": 70.0,
                                           "wind_speed_kmh": 8.0, "wind_direction_deg": 200.0,
                                           "condition": "Despejado", "station_name": "Estación de prueba"})
    for i in range(1, 7):
        store.save_sleep(SleepRecord(sleep_date=hoy - timedelta(days=i), total_sleep_seconds=25200, sleep_score=80))
        store.save_daily_stats(DailyStats(stat_date=hoy - timedelta(days=i), steps=9000, resting_hr=48 + i % 2,
                                          total_calories=2500))
    store.save_sleep(SleepRecord(sleep_date=hoy, total_sleep_seconds=18000, sleep_score=None))
    for i in range(7):
        store.save_hrv(HRVRecord(hrv_date=hoy - timedelta(days=i), last_night_avg=60 + i % 3))


@pytest.fixture
def base(tmp_path):
    db = tmp_path / "data.sqlite3"
    store, ajustes = DataStore(db), Ajustes(db)
    sembrar(store)
    return store, ajustes


# -- R6 · R-108, C-149, G-49: each day with the HR in force that day (criterio portante) -------------------------

def test_carga_con_la_fc_de_cada_dia(base):  # R6, R8 · G-49
    store, ajustes = base
    x = HOY - timedelta(days=10)
    ajustes.cambiar(PULSO_A, desde=DESDE_SIEMPRE)
    ajustes.cambiar(PULSO_B, desde=x)
    carga = fitness.carga(store, ajustes.historial_fc(), HOY)

    actividades = {a["activity_date"]: a for a in store.get_activities_with_laps(ALL_SPORT_TYPES)}
    vistos = 0
    for punto in carga["serie"]:
        a = actividades.get(punto["fecha"])
        if a is None:
            assert punto["tss"] == 0
            continue
        limites = (186, 50) if date.fromisoformat(punto["fecha"]) >= x else (180, 55)
        assert punto["tss"] == round(trimp_for_activity(a, a["laps"], limites), 1), punto["fecha"]
        assert punto["tss"] != round(trimp_for_activity(a, a["laps"], ENTORNO), 1)
        vistos += 1
    assert vistos >= 5
    assert carga["pulso"] == {"confirmada": True, "texto": "de tu prueba de esfuerzo de 2025-11, desde el 11/10"}


def test_una_fc_nueva_desde_un_dia_deja_el_pasado_como_estaba(base):  # R6 · G-49
    store, ajustes = base
    ajustes.cambiar(PULSO_A, desde=DESDE_SIEMPRE)
    antes = fitness.carga(store, ajustes.historial_fc(), HOY)["serie"]
    ajustes.cambiar(PULSO_B, desde=HOY - timedelta(days=3))
    despues = fitness.carga(store, ajustes.historial_fc(), HOY)["serie"]
    corte = (HOY - timedelta(days=3)).isoformat()
    assert [p["tss"] for p in antes if p["fecha"] < corte] == [p["tss"] for p in despues if p["fecha"] < corte]
    assert [p["tss"] for p in antes if p["fecha"] >= corte] != [p["tss"] for p in despues if p["fecha"] >= corte]


def test_sin_fc_confirmada_usa_la_configuracion_y_lo_dice(base):  # R6 · R-108
    store, ajustes = base
    carga = fitness.carga(store, ajustes.historial_fc(), HOY)
    a = store.get_activity("c-ayer")
    (ayer,) = [p for p in carga["serie"] if p["fecha"] == AYER.isoformat()]
    assert ayer["tss"] == round(trimp_for_activity(a, store.get_laps("c-ayer"), ENTORNO), 1)
    assert carga["pulso"] == {"confirmada": False, "texto": "de la configuración: confírmala en Ajustes"}
    assert fitness.carga(store, None, HOY)["pulso"]["confirmada"] is False


# -- R1 · R-105, C-146: form, fatigue and freshness of today and 14 days -----------------------------------------

def test_carga_de_hoy_y_14_dias_como_export_pmc(base):  # R1 · C-146 (unit 090: 14 days is now one of the ranges)
    store, ajustes = base
    carga = fitness.carga(store, ajustes.historial_fc(), HOY, dias=14)
    assert len(carga["serie"]) == 14
    assert carga["serie"][-1]["fecha"] == HOY.isoformat()
    assert carga["serie"][0]["fecha"] == (HOY - timedelta(days=13)).isoformat()
    serie = compute_pmc(daily_tss(store.get_activities_with_laps(ALL_SPORT_TYPES)))
    (ayer,) = [p for p in serie if p["date"] == AYER]  # export-pmc stops at the last activity: yesterday
    (mia,) = [p for p in carga["serie"] if p["fecha"] == AYER.isoformat()]
    assert (mia["ctl"], mia["atl"], mia["tsb"]) == (round(ayer["ctl"], 1), round(ayer["atl"], 1), round(ayer["tsb"], 1))
    h = carga["hoy"]
    assert h["fecha"] == HOY.isoformat()
    assert h["tsb"] == round(ayer["ctl"] - ayer["atl"], 1)  # today, a rest day: yesterday's fitness − fatigue


def test_sin_actividades_no_hay_carga_y_no_es_un_cero(tmp_path):  # R5 · G-47
    db = tmp_path / "data.sqlite3"
    carga = fitness.carga(DataStore(db), Ajustes(db).historial_fc(), HOY)
    assert carga["hoy"] is None and carga["serie"] == []


# -- R2 · R-106, C-147: the last activity ------------------------------------------------------------------------

def test_ultima_actividad(base):  # R2 · C-147
    store, ajustes = base
    ajustes.cambiar(PULSO_A, desde=DESDE_SIEMPRE)
    u = fitness.ultima_actividad(store, ajustes.historial_fc(), HOY)
    a = store.get_activity("c-ayer")
    assert u == {"actividad_id": "c-ayer", "nombre": "Carrera de prueba", "deporte": "Carrera",
                 "fecha": AYER.isoformat(), "distancia_m": 10000, "duracion_s": 3000, "pulso_medio": 147,
                 "carga": round(trimp_for_activity(a, store.get_laps("c-ayer"), (180, 55)), 1),
                 "vueltas": 10, "clima": True}


def test_ultima_actividad_sin_pulso_no_inventa_carga(tmp_path):  # R5 · G-47
    store = DataStore(tmp_path / "data.sqlite3")
    store.save_activities([Activity(activity_id="n1", activity_date=AYER, activity_type="lap_swimming",
                                    name="Natación de prueba", duration_seconds=2400)])
    u = fitness.ultima_actividad(store, None, HOY)
    assert (u["distancia_m"], u["pulso_medio"], u["carga"], u["vueltas"], u["clima"]) == (None, None, None, 0, False)
    assert fitness.ultima_actividad(DataStore(tmp_path / "otra.sqlite3"), None, HOY) is None


def test_una_actividad_de_manana_no_es_la_ultima(base):
    store, ajustes = base
    store.save_activities([Activity(activity_id="futura", activity_date=HOY + timedelta(days=1),
                                    activity_type="running", name="Mañana", duration_seconds=600)])
    assert fitness.ultima_actividad(store, ajustes.historial_fc(), HOY)["actividad_id"] == "c-ayer"


# -- R3 · R-111, C-152: the detail with its laps, the zone of each lap and the weather --------------------------

def test_detalle_con_vueltas_zonas_y_clima(base):  # R3 · C-152
    store, ajustes = base
    ajustes.cambiar(PULSO_A, desde=DESDE_SIEMPRE)
    d = fitness.detalle_actividad(store, ajustes.historial_fc(), "c-ayer")
    assert d["actividad"]["actividad_id"] == "c-ayer"
    assert len(d["vueltas"]) == 10
    assert [v["zona"] for v in d["vueltas"]] == [2, 2, 2, 2, 3, 3, 3, 4, 4, 5]
    v = d["vueltas"][0]
    assert (v["n"], v["distancia_m"], v["duracion_s"], v["ritmo_s_km"], v["pulso_medio"]) == (1, 1000, 300, 300, 125)
    assert d["zonas"] is True
    assert d["clima"]["temp_c"] == 14.0 and d["clima"]["condition"] == "Despejado"


def test_detalle_sin_zonas_confirmadas_no_inventa_zona(base):  # R3, R6
    store, ajustes = base
    d = fitness.detalle_actividad(store, ajustes.historial_fc(), "c-ayer")
    assert d["zonas"] is False
    assert {v["zona"] for v in d["vueltas"]} == {None}


def test_detalle_sin_vueltas_ni_clima_lo_dice(base):  # R3
    store, ajustes = base
    d = fitness.detalle_actividad(store, ajustes.historial_fc(), "h002")
    assert d["vueltas"] == [] and d["clima"] is None
    assert d["actividad"]["vueltas"] == 0 and d["actividad"]["clima"] is False
    assert fitness.detalle_actividad(store, ajustes.historial_fc(), "no-existe") is None


def test_la_bici_va_en_velocidad(tmp_path):  # R3
    store = DataStore(tmp_path / "data.sqlite3")
    store.save_activities([Activity(activity_id="b1", activity_date=AYER, activity_type="cycling",
                                    name="Bici de prueba", duration_seconds=1800, distance_meters=15000)])
    store.save_activity_laps([ActivityLap(activity_id="b1", lap_index=0, distance_meters=15000,
                                          duration_seconds=1800, avg_hr=None)])
    (v,) = fitness.detalle_actividad(store, None, "b1")["vueltas"]
    assert v["velocidad_kmh"] == 30.0 and v["ritmo_s_km"] is None and v["pulso_medio"] is None and v["zona"] is None


@pytest.mark.parametrize(("pulso", "zona"), [(54, None), (55, 1), (120, 1), (120.5, 1), (121, 2), (168.7, 4),
                                             (169, 5), (180, 5), (195, 5)])
def test_zona_de_un_pulso(pulso, zona):  # R3
    assert fitness.zona(pulso, tuple(tuple(z) for z in ZONAS_A)) == zona


# -- R4, R5 · R-107, R-109, C-148, C-150, G-47: the recovery -----------------------------------------------------

def test_recuperacion_con_sus_medias_y_estados(base):  # R4, R5 · C-148, C-150
    store, _ = base
    r = fitness.recuperacion(store, HOY)
    assert r["sueno"] == {"estado": "incompleto", "segundos": 18000, "puntuacion": None,
                          "media_7d": round((6 * 25200 + 18000) / 7)}
    assert r["pulso_reposo"]["estado"] == "no_ha_llegado" and r["pulso_reposo"]["valor"] is None
    assert r["pulso_reposo"]["media_7d"] == round(sum(48 + i % 2 for i in range(1, 7)) / 6, 1)
    assert r["hrv"] == {"estado": "ok", "valor": 60, "media_7d": round(sum(60 + i % 3 for i in range(7)) / 7, 1)}


def test_recuperacion_sin_nada_nunca_es_cero(tmp_path):  # R5 · G-47
    r = fitness.recuperacion(DataStore(tmp_path / "data.sqlite3"), HOY)
    for dato in r.values():
        assert dato["estado"] == "no_ha_llegado"
        assert all(v is None for k, v in dato.items() if k != "estado")


def test_pulso_de_hoy_a_medias(base):  # R5 · C-150
    store, _ = base
    store.save_activities([Activity(activity_id="hoy1", activity_date=HOY, activity_type="running",
                                    name="Carrera de hoy", duration_seconds=3600, distance_meters=12000,
                                    calories=1200)])
    store.save_daily_stats(DailyStats(stat_date=HOY, steps=500, resting_hr=47, total_calories=900))
    assert fitness.recuperacion(store, HOY)["pulso_reposo"] == {"estado": "incompleto", "valor": 47,
                                                               "media_7d": pytest.approx(48.3, abs=0.05)}


# -- R8: Hoy, export-pmc, the MCP and Fitness give the same freshness -------------------------------------------

def test_la_misma_frescura_en_todas_partes(tmp_path, monkeypatch):  # R8
    from tridata import cli
    from tridata.mcp_server import get_training_load

    hoy = date.today()  # the MCP extends up to the real today
    db = tmp_path / "data.sqlite3"
    store, ajustes = DataStore(db), Ajustes(db)
    sembrar(store, hoy)
    store.save_activities([Activity(activity_id="hoy1", activity_date=hoy, activity_type="running",
                                    name="Carrera de hoy", duration_seconds=1800, avg_hr=150)])
    ajustes.cambiar(PULSO_A, desde=DESDE_SIEMPRE)
    ajustes.cambiar(PULSO_B, desde=hoy - timedelta(days=5))
    historial = ajustes.historial_fc()

    tsb_fitness = fitness.carga(store, historial, hoy)["hoy"]["tsb"]
    tsb_hoy = como_llegas(store, hoy, limites=historial.limites)["frescura"]["valor"]
    mcp = get_training_load(store, days=3, limites=historial.limites)
    tsb_mcp = float(mcp.split("TSB ", 1)[1].split()[0].rstrip(","))

    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(cli, "DataStore", lambda *a, **k: DataStore(db))
    salida = tmp_path / "pmc.md"
    assert cli.main(["export-pmc", "--out", str(salida)]) == 0
    texto = salida.read_text()
    (fila,) = [linea for linea in texto.splitlines() if linea.startswith(f"| {hoy.isoformat()} ")]
    tsb_export = float(fila.strip("|").split("|")[-1].strip())

    assert tsb_fitness == tsb_hoy == tsb_mcp == tsb_export
    sin_fecha = fitness.carga(store, None, hoy)["hoy"]["tsb"]
    assert sin_fecha != tsb_fitness  # the confirmed HR really changes it


# -- unit 090 (REC-35): 14 days · 6 weeks · 3 months · 1 year, the weeks by sport and the zones by laps ------------
# Plans v34: R-232..R-236 · C-289..C-294 · G-49. R1-R6 of the unit. HOY is a Wednesday: its Monday is 19/10.

LUNES = HOY - timedelta(days=HOY.weekday())
ZONAS_TUPLAS = tuple(tuple(z) for z in ZONAS_A)


def _sesion(store: DataStore, id_: str, dia: date, tipo: str, segundos: float, *, metros: float | None = None,
            pulso: float | None = None, vueltas: tuple[tuple[float, float | None], ...] = ()) -> None:
    """One invented session; `vueltas` as (seconds, mean HR or None)."""
    store.save_activities([Activity(activity_id=id_, activity_date=dia, activity_type=tipo, name=id_,
                                    duration_seconds=segundos, distance_meters=metros, avg_hr=pulso)])
    store.save_activity_laps([ActivityLap(activity_id=id_, lap_index=i, duration_seconds=s, avg_hr=h)
                              for i, (s, h) in enumerate(vueltas)])


def _semana(semanas: list[dict], lunes: date) -> dict:
    (s,) = [s for s in semanas if s["lunes"] == lunes.isoformat()]
    return s


def test_zonas_por_vueltas_con_las_zonas_de_ese_dia(tmp_path):  # R4 · R-235, C-292, C-293 (criterio portante)
    db = tmp_path / "data.sqlite3"
    store, ajustes = DataStore(db), Ajustes(db)
    ajustes.cambiar(PULSO_A, desde=LUNES)  # nothing confirmed before this Monday
    # C-292: 20 min in Z2, 30 in Z4 and 10 in Z1 (and a lap below Z1: it has HR, so it is Z1, the lowest)
    _sesion(store, "c3", LUNES, "running", 3900, metros=12000, pulso=140,
            vueltas=((1200, 130), (1800, 160), (600, 100), (300, 50)))
    # C-293: a swim whose laps carry no HR → «sin pulso»
    _sesion(store, "n1", LUNES + timedelta(days=1), "lap_swimming", 1500, metros=2000,
            vueltas=((900, None), (600, None)))
    # a session without laps: one lap with its duration and its mean HR (Z3)
    _sesion(store, "b1", LUNES + timedelta(days=2), "cycling", 3600, metros=30000, pulso=150)
    # last week, before the zones were confirmed: its laps with HR still go to «sin pulso»
    _sesion(store, "c0", LUNES - timedelta(days=1), "running", 1800, metros=6000, pulso=150, vueltas=((1800, 150),))

    semanas = fitness.semanas(store, ajustes.historial_fc(), HOY, 2)
    esta = _semana(semanas, LUNES)
    assert esta["deportes"]["correr"]["zonas"] == {"z1": 900, "z2": 1200, "z3": 0, "z4": 1800, "z5": 0,
                                                   "sin_pulso": 0}
    assert esta["deportes"]["nadar"]["zonas"] == {"z1": 0, "z2": 0, "z3": 0, "z4": 0, "z5": 0, "sin_pulso": 1500}
    assert esta["deportes"]["bici"]["zonas"]["z3"] == 3600
    assert esta["total"]["zonas"] == {"z1": 900, "z2": 1200, "z3": 3600, "z4": 1800, "z5": 0, "sin_pulso": 1500}
    pasada = _semana(semanas, LUNES - timedelta(days=7))
    assert pasada["total"]["zonas"] == {"z1": 0, "z2": 0, "z3": 0, "z4": 0, "z5": 0, "sin_pulso": 1800}
    # the zone of each lap is the one `zona()` gives with the zones of THAT day
    assert fitness.zona(160, ZONAS_TUPLAS) == 4 and fitness.zona(50, ZONAS_TUPLAS) is None


def test_semanas_de_lunes_a_domingo_apiladas_por_deporte(tmp_path):  # R3, R5, R6 · R-234, R-236, C-291, C-294
    store = DataStore(tmp_path / "data.sqlite3")
    previa = LUNES - timedelta(days=7)
    # a week like the Behobia one: run, ride and swim; the Sunday run belongs to that week, not to this one
    _sesion(store, "behobia", previa + timedelta(days=6), "running", 4800, metros=20000, pulso=160)
    _sesion(store, "tirada", previa + timedelta(days=4), "trail_running", 12600, metros=34000, pulso=140)
    _sesion(store, "bici", previa, "road_biking", 5400, metros=45000, pulso=130)
    _sesion(store, "nado", previa + timedelta(days=2), "open_water_swimming", 2700, metros=2500)
    # this week: strength on Monday (with a distance Garmin may give: never km) and a walk («otros»)
    _sesion(store, "fuerza", LUNES, "strength_training", 2700, metros=500, pulso=110)
    _sesion(store, "paseo", LUNES + timedelta(days=1), "walking", 3600, metros=5000, pulso=95)
    _sesion(store, "manana", HOY + timedelta(days=1), "running", 3600, metros=10000, pulso=150)  # not done yet

    semanas = fitness.semanas(store, None, HOY, 6)
    assert [s["lunes"] for s in semanas] == [(LUNES - timedelta(weeks=5 - i)).isoformat() for i in range(6)]
    assert [s["domingo"] for s in semanas] == [(LUNES - timedelta(weeks=5 - i) + timedelta(days=6)).isoformat()
                                               for i in range(6)]
    assert [s["en_curso"] for s in semanas] == [False] * 5 + [True]
    for s in semanas[:4]:  # R6: an empty week is there, at zero
        assert s["total"]["horas"] == 0 and s["total"]["km"] == 0 and s["total"]["carga"] == 0
        assert set(s["deportes"]) == {"correr", "bici", "nadar", "fuerza", "otros"}

    b = _semana(semanas, previa)
    assert b["deportes"]["correr"]["km"] == 54.0 and b["deportes"]["correr"]["horas"] == 4.83
    assert (b["deportes"]["bici"]["km"], b["deportes"]["nadar"]["km"]) == (45.0, 2.5)
    assert b["total"]["km"] == 101.5 and b["total"]["horas"] == round((4800 + 12600 + 5400 + 2700) / 3600, 2)

    e = semanas[-1]
    assert e["deportes"]["fuerza"]["km"] is None and e["deportes"]["fuerza"]["horas"] == 0.75
    assert e["deportes"]["fuerza"]["carga"] > 0
    assert e["deportes"]["otros"]["horas"] == 1.0 and e["deportes"]["otros"]["km"] == 5.0
    assert e["deportes"]["otros"]["carga"] == 0  # the load counts what form and fatigue count (ALL_SPORT_TYPES)
    assert e["deportes"]["correr"]["horas"] == 0  # tomorrow's session is not in
    assert e["total"]["km"] == 5.0 and e["total"]["horas"] == 1.75


@pytest.mark.parametrize("dias", [14, 42, 91, 365])
def test_cada_rango_da_los_mismos_numeros_dia_a_dia(tmp_path, dias):  # R1 · R-232, C-289, G-49
    db = tmp_path / "data.sqlite3"
    store, ajustes = DataStore(db), Ajustes(db)
    sembrar(store, historia=400)
    ajustes.cambiar(PULSO_A, desde=DESDE_SIEMPRE)
    ajustes.cambiar(PULSO_B, desde=HOY - timedelta(days=20))
    historial = ajustes.historial_fc()
    entera = fitness.carga(store, historial, HOY, dias=365)["serie"]
    carga = fitness.carga(store, historial, HOY, dias=dias)
    assert len(carga["serie"]) == dias
    assert carga["serie"] == entera[-dias:]
    assert carga["serie"][-1]["fecha"] == HOY.isoformat() and carga["hoy"] == carga["serie"][-1]
    assert carga["serie"][-14:] == fitness.carga(store, historial, HOY, dias=14)["serie"]
    assert fitness.carga(store, historial, HOY)["serie"] == entera[-42:]  # 6 weeks by default


def test_un_rango_que_no_es_de_los_cuatro_no_vale(base):  # R1
    store, ajustes = base
    with pytest.raises(ValueError):
        fitness.carga(store, ajustes.historial_fc(), HOY, dias=30)
    assert fitness.RANGOS_DIAS == (14, 42, 91, 365) and fitness.DIAS_POR_DEFECTO == 42


def test_la_carga_semanal_cuadra_con_la_diaria(tmp_path):  # R3 · R-234, G-49
    db = tmp_path / "data.sqlite3"
    store, ajustes = DataStore(db), Ajustes(db)
    sembrar(store)
    _sesion(store, "paseo", AYER, "walking", 3600, metros=5000, pulso=100)
    ajustes.cambiar(PULSO_A, desde=DESDE_SIEMPRE)
    ajustes.cambiar(PULSO_B, desde=HOY - timedelta(days=10))  # two HRs inside the 6 weeks
    historial = ajustes.historial_fc()
    por_dia = daily_tss(store.get_activities_with_laps(ALL_SPORT_TYPES), historial.limites)
    for s in fitness.semanas(store, historial, HOY, 6):
        lunes = date.fromisoformat(s["lunes"])
        esperada = sum(por_dia.get(lunes + timedelta(days=i), 0.0) for i in range(7))
        assert s["total"]["carga"] == pytest.approx(round(esperada, 1), abs=0.051), s["lunes"]
        assert s["total"]["carga"] == pytest.approx(sum(d["carga"] for d in s["deportes"].values()), abs=0.11)


def test_semanas_por_rango_y_sin_actividades(tmp_path):  # R3, R6
    db = tmp_path / "data.sqlite3"
    store, ajustes = DataStore(db), Ajustes(db)
    assert [fitness.SEMANAS_DE[d] for d in fitness.RANGOS_DIAS] == [2, 6, 13, 52]
    f = fitness.fitness(store, ajustes.historial_fc(), HOY, dias=365)
    assert len(f["semanas"]) == 52 and f["carga"]["serie"] == []
    assert all(s["total"] == {"horas": 0, "km": 0, "carga": 0,
                              "zonas": {"z1": 0, "z2": 0, "z3": 0, "z4": 0, "z5": 0, "sin_pulso": 0}}
               for s in f["semanas"])
    assert f["semanas"][-1]["en_curso"] is True and f["semanas"][-1]["lunes"] == LUNES.isoformat()
    assert len(fitness.fitness(store, ajustes.historial_fc(), HOY)["semanas"]) == 6
