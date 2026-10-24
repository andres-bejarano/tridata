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

def test_carga_de_hoy_y_14_dias_como_export_pmc(base):  # R1 · C-146
    store, ajustes = base
    carga = fitness.carga(store, ajustes.historial_fc(), HOY)
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
