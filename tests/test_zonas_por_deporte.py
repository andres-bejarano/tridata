"""Unit 096 (REC-41 of `ver-fitness`): heart-rate zones by sport — run, ride and swim, each from its date — and the
threshold the app proposes from a race or a long hard stretch, saved only with Andrés's «sí».

Plans v40: R-255..R-262 · C-320..C-330 · G-49, G-64. R1-R9 of the unit. Garmin is always doubled: nothing here talks
to Garmin nor touches ~/.tridata. Every value is invented.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata import fitness, umbral
from tridata.garmin_client import ActividadGarmin, GarminClient
from tridata.models import Activity, ActivityLap
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroComidas
from tridata.storage import DataStore
from tridata.sync import SyncService
from tridata.web import crear_app

HOY = date(2026, 10, 21)
AYER = HOY - timedelta(days=1)
FC_MAX, FC_REPOSO = 192, 48
# run threshold (start of Z5) 175: Z3 158-165, Z4 166-174
ZONAS_C = [[48, 148], [149, 157], [158, 165], [166, 174], [175, 192]]
PULSO = {"fc_max": FC_MAX, "fc_reposo": FC_REPOSO, "zonas_fc": ZONAS_C,
         "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}
# ride and swim without their own: 175 − 7 = 168, with Friel's percentages of each sport
ZONAS_BICI_168 = [[48, 135], [136, 150], [151, 157], [158, 167], [168, 192]]
ZONAS_NATACION_168 = [[48, 142], [143, 150], [151, 159], [160, 167], [168, 192]]


@pytest.fixture(autouse=True)
def _entorno(monkeypatch):
    monkeypatch.setenv("TRIDATA_HR_MAX", "190")
    monkeypatch.setenv("TRIDATA_HR_REST", "60")


@pytest.fixture
def db(tmp_path):
    return tmp_path / "data.sqlite3"


@pytest.fixture
def ajustes(db) -> Ajustes:
    a = Ajustes(db)
    a.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000}, desde=DESDE_SIEMPRE)
    a.cambiar(PULSO, desde=DESDE_SIEMPRE)
    return a


@pytest.fixture
def store(db) -> DataStore:
    return DataStore(db)


def _detalle(tramos: list[tuple[int, float | None]]) -> dict[str, list[float | None]]:
    """Second by second: each (seconds, HR) after the other."""
    pulso: list[float | None] = []
    for segundos, valor in tramos:
        pulso += [valor] * segundos
    n = len(pulso)
    return {"t": [float(i) for i in range(n)], "distancia": [float(i * 3) for i in range(n)], "pulso": pulso,
            "velocidad": [3.0] * n, "altura": [100.0] * n, "potencia": [None] * n}


def _competicion(aid: str, dia: date, pulso: float, minutos: int = 38, tipo: str = "running",
                 nombre: str = "10K de la ciudad") -> ActividadGarmin:
    return ActividadGarmin(activity_id=aid, activity_date=dia, activity_type=tipo, name=nombre,
                           duration_seconds=minutos * 60, distance_meters=10000, avg_hr=pulso, event_type="race")


# -- R3 (R-257, C-330) · R2 (R-256, C-329): zones out of a threshold, and ride/swim estimated from the run -----------

def test_zonas_de_friel_de_carrera_y_natacion():  # R3
    assert umbral.zonas_desde_umbral(168, "natacion", FC_REPOSO, FC_MAX) == ZONAS_NATACION_168
    assert umbral.zonas_desde_umbral(175, "carrera", FC_REPOSO, FC_MAX) == ZONAS_C


def test_zonas_de_friel_de_bici_tienen_su_propia_tabla():  # R3 · C-330
    assert umbral.zonas_desde_umbral(168, "bici", FC_REPOSO, FC_MAX) == ZONAS_BICI_168
    assert umbral.zonas_desde_umbral(172, "bici", FC_REPOSO, FC_MAX) == [
        [48, 138], [139, 154], [155, 161], [162, 171], [172, 192]]


def test_bici_y_natacion_sin_las_suyas_salen_de_carrera_menos_7(ajustes):  # R2 · C-329
    p = ajustes.historial_fc().vigente(HOY)
    assert p.zonas_de("carrera") == (tuple(tuple(z) for z in ZONAS_C), False)
    assert p.zonas_de("bici") == (tuple(tuple(z) for z in ZONAS_BICI_168), True)
    assert p.zonas_de("natacion") == (tuple(tuple(z) for z in ZONAS_NATACION_168), True)


def test_sin_zonas_de_carrera_no_hay_estimadas(db):
    a = Ajustes(db)
    a.cambiar({"fc_max": FC_MAX, "fc_reposo": FC_REPOSO}, desde=DESDE_SIEMPRE)
    assert a.historial_fc().vigente(HOY).zonas_de("bici") == (None, False)


@pytest.mark.parametrize(("tipo", "deporte"), [
    ("running", "carrera"), ("trail_running", "carrera"), ("walking", "carrera"), ("hiking", "carrera"),
    ("yoga", "carrera"), ("strength_training", "carrera"), (None, "carrera"),
    ("road_biking", "bici"), ("indoor_cycling", "bici"),
    ("lap_swimming", "natacion"), ("open_water_swimming", "natacion"),
])
def test_cada_tipo_usa_las_zonas_de_su_deporte(tipo, deporte):  # R4: walks and others, run; open water, swim
    assert umbral.deporte_de(tipo) == deporte


# -- R1 (R-255, C-320) · R9 (G-49): zones by sport and date, what was before stays; the private values -----------

def test_zonas_propias_de_bici_por_fecha_y_lo_anterior_intacto(ajustes):  # R1
    propias = [[48, 130], [131, 148], [149, 156], [157, 165], [166, 192]]
    ajustes.cambiar({"zonas_fc_bici": propias}, desde=date(2026, 10, 10))
    historial = ajustes.historial_fc()
    assert historial.vigente(date(2026, 10, 9)).zonas_de("bici") == (tuple(tuple(z) for z in ZONAS_BICI_168), True)
    assert historial.vigente(date(2026, 10, 10)).zonas_de("bici") == (tuple(tuple(z) for z in propias), False)
    assert historial.vigente(date(2026, 10, 10)).zonas_de("natacion")[1] is True  # swim still estimated
    vista = ajustes.valores_con_fecha(date(2026, 10, 10))
    assert vista["zonas_fc_bici"] == {"valor": propias, "desde": "2026-10-10"}
    assert vista["zonas_fc_natacion"] == {"valor": None, "desde": None}
    assert vista["zonas_fc"]["valor"] == ZONAS_C  # what was saved as `zonas_fc` is the run's, nothing migrated


def test_zonas_de_bici_que_no_cuadran_con_la_fc_no_se_guardan(ajustes):  # R1 · the same check as `zonas_fc`
    with pytest.raises(ValueError, match="FC máxima"):
        ajustes.cambiar({"zonas_fc_bici": [[48, 130], [131, 148], [149, 156], [157, 165], [166, 190]]}, desde=HOY)
    with pytest.raises(ValueError, match="cinco zonas"):
        ajustes.cambiar({"zonas_fc_natacion": [[48, 130]]}, desde=HOY)
    assert ajustes.historial_fc().vigente(HOY).zonas_de("bici")[1] is True


def test_valores_de_prueba_incluyen_las_zonas_de_bici_y_natacion(ajustes):  # R9 · G-49
    propias = [[48, 129], [130, 147], [148, 155], [156, 163], [164, 192]]
    ajustes.cambiar({"zonas_fc_bici": propias, "zonas_fc_natacion": propias}, desde=HOY)
    valores = ajustes.historial_fc().valores_de_prueba()
    assert {129, 147, 155, 163, 164} <= valores


def test_api_ajustes_ensena_los_tres_bloques(web):  # R1, R2 · C-320
    r = web.cliente.get("/api/ajustes")
    assert r.status_code == 200
    bloques = {b["deporte"]: b for b in r.json()["zonas_deporte"]}
    assert list(bloques) == ["carrera", "bici", "natacion"]
    assert (bloques["carrera"]["umbral"], bloques["carrera"]["estimadas"]) == (175, False)
    assert (bloques["bici"]["umbral"], bloques["bici"]["estimadas"]) == (168, True)
    assert bloques["bici"]["zonas"] == ZONAS_BICI_168
    assert (bloques["natacion"]["umbral"], bloques["natacion"]["estimadas"]) == (168, True)
    assert bloques["natacion"]["zonas"] == ZONAS_NATACION_168


# -- R4 (R-258, C-321): each session with the zones of ITS sport that day — the load-bearing criterion -----------

def _salida_en_bici(store: DataStore) -> None:
    store.save_activities([Activity(activity_id="bici-1", activity_date=AYER, activity_type="road_biking",
                                    name="Salida en bici", duration_seconds=1200, distance_meters=10000, avg_hr=162)])
    store.save_activity_laps([ActivityLap("bici-1", 1, distance_meters=10000, duration_seconds=1200, avg_hr=162)])


def test_vuelta_de_bici_a_162_es_z4_de_bici_no_z3_de_carrera(ajustes, store):  # R4 · C-321 (portante)
    _salida_en_bici(store)
    d = fitness.detalle_actividad(store, ajustes.historial_fc(), "bici-1")
    assert d["vueltas"][0]["zona"] == 4  # ride Z4 158-167; with the run's it would be Z3 (158-165)
    assert d["zonas_deporte"] == {"deporte": "bici", "estimadas": True, "umbral": 168,
                                  "texto": "zonas de bici estimadas desde carrera"}


def test_curva_de_bici_con_las_bandas_de_bici(ajustes, store):  # R4 · C-321
    _salida_en_bici(store)
    store.guardar_detalle("bici-1", _detalle([(1200, 162.0)]))
    curva = fitness.detalle_actividad(store, ajustes.historial_fc(), "bici-1")["curva"]
    assert curva["limites"] == ZONAS_BICI_168
    assert curva["zonas_s"]["z4"] == 1200 and curva["zonas_s"]["z3"] == 0


def test_tus_semanas_cuentan_la_bici_con_sus_zonas(ajustes, store):  # R4 · C-321
    _salida_en_bici(store)
    semana = fitness.semanas(store, ajustes.historial_fc(), HOY, 1)[0]
    assert semana["deportes"]["bici"]["zonas"]["z4"] == 1200
    assert semana["deportes"]["bici"]["zonas"]["z3"] == 0


def test_una_carrera_sigue_con_las_zonas_de_carrera(ajustes, store):  # R4: the runs look as today
    store.save_activities([Activity(activity_id="c-1", activity_date=AYER, activity_type="running",
                                    name="Rodaje", duration_seconds=600, distance_meters=2000, avg_hr=162)])
    store.save_activity_laps([ActivityLap("c-1", 1, distance_meters=2000, duration_seconds=600, avg_hr=162)])
    d = fitness.detalle_actividad(store, ajustes.historial_fc(), "c-1")
    assert d["vueltas"][0]["zona"] == 3
    assert d["zonas_deporte"]["estimadas"] is False and d["zonas_deporte"]["texto"] == "zonas de carrera"


# -- R5 (R-259, C-322, C-324, C-327): the effort that estimates a threshold ----------------------------------------

ZONAS_T = tuple(tuple(z) for z in ZONAS_C)  # Z4 from 166


def test_competicion_de_38_min_su_pulso_medio_es_el_umbral():  # R5 · C-322
    a = {"activity_type": "running", "event_type": "race", "duration_seconds": 38 * 60, "avg_hr": 171.4}
    esfuerzo = umbral.detectar(a, [], None, ZONAS_T)
    assert (esfuerzo.umbral, esfuerzo.como) == (171, "competicion")


def test_tirada_suave_no_propone_nada():  # R5 · C-324
    a = {"activity_type": "running", "duration_seconds": 7200, "avg_hr": 140}
    laps = [{"duration_seconds": 600, "avg_hr": 140} for _ in range(12)]
    assert umbral.detectar(a, laps, _detalle([(7200, 140.0)]), ZONAS_T) is None


def test_media_maraton_de_1h40_sin_tramo_fuerte_no_propone():  # R5 · C-327
    a = {"activity_type": "running", "event_type": "race", "duration_seconds": 6000, "avg_hr": 160}
    laps = [{"duration_seconds": 300, "avg_hr": 160} for _ in range(20)]
    assert umbral.detectar(a, laps, None, ZONAS_T) is None
    assert umbral.detectar(a, laps, _detalle([(6000, 160.0)]), ZONAS_T) is None


def test_media_maraton_con_tramo_fuerte_si_propone_por_el_tramo():  # R5 · C-327
    a = {"activity_type": "running", "event_type": "race", "duration_seconds": 6000, "avg_hr": 165}
    detalle = _detalle([(3600, 160.0), (1500, 170.0), (900, 160.0)])  # 25 min at Z4
    esfuerzo = umbral.detectar(a, [], detalle, ZONAS_T)
    assert (esfuerzo.umbral, esfuerzo.como) == (170, "tramo")


def test_tramo_de_30_min_o_mas_sin_sus_primeros_10():  # R5
    a = {"activity_type": "running", "duration_seconds": 4200, "avg_hr": 160}
    detalle = _detalle([(600, 130.0), (600, 168.0), (1800, 172.0), (600, 130.0)])  # 40 min at Z4
    esfuerzo = umbral.detectar(a, [], detalle, ZONAS_T)
    assert (esfuerzo.umbral, esfuerzo.como) == (172, "tramo")  # with its first 10 it would be 171


def test_tramo_de_menos_de_20_min_no_propone():  # R5
    a = {"activity_type": "running", "duration_seconds": 3000, "avg_hr": 160}
    assert umbral.detectar(a, [], _detalle([(1200, 130.0), (1080, 172.0), (720, 130.0)]), ZONAS_T) is None


def test_tramo_por_vueltas_seguidas_sin_detalle():  # R5: laps when there is no detail
    a = {"activity_type": "running", "duration_seconds": 3120, "avg_hr": 160}
    laps = [{"duration_seconds": 600, "avg_hr": 130}, {"duration_seconds": 480, "avg_hr": 168},
            {"duration_seconds": 480, "avg_hr": 171}, {"duration_seconds": 480, "avg_hr": 173},
            {"duration_seconds": 480, "avg_hr": 172}, {"duration_seconds": 600, "avg_hr": 130}]
    esfuerzo = umbral.detectar(a, laps, None, ZONAS_T)
    assert (esfuerzo.umbral, esfuerzo.como) == (172, "tramo")  # 32 min: without its first 10 (with them, 171)


def test_sin_zonas_no_hay_tramo():
    a = {"activity_type": "running", "duration_seconds": 3000, "avg_hr": 172}
    assert umbral.detectar(a, [], _detalle([(3000, 172.0)]), None) is None


# -- R6 (R-260, C-325): only from 3 ppm --------------------------------------------------------------------------

@pytest.mark.parametrize(("pulso", "propuestas"), [(173, 0), (177, 0), (172, 1), (178, 1)])
def test_solo_se_propone_con_3_ppm_o_mas(ajustes, store, db, pulso, propuestas):  # R6 · C-325
    store.save_activities([_competicion("r-1", AYER, pulso)])
    umbral.revisar_sesion(store, "r-1", HOY)
    assert len(umbral.Propuestas(db).pendientes()) == propuestas


def test_una_sesion_vieja_no_propone(ajustes, store, db):  # out of scope: only the sessions brought from now on
    store.save_activities([_competicion("r-viejo", HOY - timedelta(days=60), 168)])
    assert umbral.revisar_sesion(store, "r-viejo", HOY) is None
    assert umbral.Propuestas(db).pendientes() == []


def test_la_bici_se_compara_con_su_umbral_estimado(ajustes, store, db):  # R6 with R2: ride 168 estimated
    store.save_activities([ActividadGarmin(activity_id="b-1", activity_date=AYER, activity_type="road_biking",
                                           name="Contrarreloj", duration_seconds=1800, avg_hr=170)])
    store.guardar_detalle("b-1", _detalle([(1500, 172.0), (300, 130.0)]))  # 25 min at ride Z4 or more
    umbral.revisar_sesion(store, "b-1", HOY)
    (p,) = umbral.Propuestas(db).pendientes()
    assert (p["deporte"], p["actual"], p["estimado"]) == ("bici", 168, 172)


def test_propuesta_dice_de_x_a_y_con_las_zonas_que_saldrian(web):  # R6 · C-322
    web.store.save_activities([_competicion("r-1", AYER, 171)])
    umbral.revisar_sesion(web.store, "r-1", HOY)
    r = web.cliente.get("/api/umbral/propuestas")
    assert r.status_code == 200
    (p,) = r.json()["propuestas"]
    assert (p["deporte"], p["actual"], p["estimado"], p["actividad_id"]) == ("carrera", 175, 171, "r-1")
    assert p["texto"] == "tu umbral de carrera pasa de 175 a 171 (10K de la ciudad, 20/10), ¿actualizo desde hoy?"
    assert p["zonas"] == umbral.zonas_desde_umbral(171, "carrera", FC_REPOSO, FC_MAX)
    assert web.cliente.get("/api/ajustes").json()["propuestas"] == [p]


# -- R7 (R-261, C-323, C-326): «sí» saves from today, «no» never again, a newer one replaces --------------------

class Web:
    def __init__(self, tmp_path) -> None:
        db = tmp_path / "data.sqlite3"
        self.db = db
        self.ajustes = Ajustes(db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                             desde=DESDE_SIEMPRE)
        self.ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)
        self.store = DataStore(db)
        self.cliente = TestClient(crear_app(RegistroComidas(db), MagicMock(), self.ajustes, hoy=lambda: HOY,
                                            objetivos=ObjetivosDia(db), store=self.store,
                                            actualizar_garmin=MagicMock(), ahora=lambda: datetime(2026, 10, 21, 10)))


@pytest.fixture
def web(tmp_path) -> Web:
    return Web(tmp_path)


def _una_propuesta(web: Web, pulso: float = 171) -> int:
    web.store.save_activities([_competicion("r-1", AYER, pulso)])
    web.store.save_activity_laps([ActivityLap("r-1", 1, distance_meters=10000, duration_seconds=2280, avg_hr=167)])
    umbral.revisar_sesion(web.store, "r-1", HOY)
    return web.cliente.get("/api/umbral/propuestas").json()["propuestas"][0]["id"]


def test_si_guarda_desde_hoy_y_lo_anterior_sigue_igual(web):  # R7 · C-323
    pid = _una_propuesta(web)
    r = web.cliente.post(f"/api/umbral/propuestas/{pid}", json={"respuesta": "si"})
    assert r.status_code == 200
    historial = web.ajustes.historial_fc()
    nuevas = umbral.zonas_desde_umbral(171, "carrera", FC_REPOSO, FC_MAX)
    assert historial.vigente(HOY).zonas_de("carrera") == (tuple(tuple(z) for z in nuevas), False)
    assert historial.vigente(AYER).zonas_de("carrera")[0] == tuple(tuple(z) for z in ZONAS_C)
    assert web.ajustes.valores_con_fecha(HOY)["zonas_fc"]["desde"] == HOY.isoformat()
    # yesterday's session keeps yesterday's zones: lap at 167 is Z4 with 175 (166-174)
    vuelta = web.cliente.get("/api/actividad/r-1").json()["vueltas"][0]
    assert vuelta["zona"] == 4
    assert web.cliente.get("/api/umbral/propuestas").json()["propuestas"] == []
    # the ride, still estimated, now comes from 171 − 7
    assert historial.vigente(HOY).zonas_de("bici")[0][4][0] == 164


def test_no_no_vuelve_a_salir(web):  # R7 · C-326
    pid = _una_propuesta(web)
    assert web.cliente.post(f"/api/umbral/propuestas/{pid}", json={"respuesta": "no"}).status_code == 200
    assert web.cliente.get("/api/umbral/propuestas").json()["propuestas"] == []
    umbral.revisar_sesion(web.store, "r-1", HOY)  # the same session again (a second pass)
    assert web.cliente.get("/api/umbral/propuestas").json()["propuestas"] == []
    assert web.ajustes.historial_fc().vigente(HOY).zonas_de("carrera")[0][4][0] == 175


def test_una_propuesta_mas_nueva_sustituye_a_la_pendiente(web):  # R7
    _una_propuesta(web)
    web.store.save_activities([_competicion("r-2", HOY, 179, nombre="5K del barrio", minutos=21)])
    umbral.revisar_sesion(web.store, "r-2", HOY)
    (p,) = web.cliente.get("/api/umbral/propuestas").json()["propuestas"]
    assert (p["actividad_id"], p["estimado"]) == ("r-2", 179)


def test_responder_mal_no_cambia_nada(web):
    pid = _una_propuesta(web)
    assert web.cliente.post(f"/api/umbral/propuestas/{pid}", json={"respuesta": "quizá"}).status_code == 400
    assert web.cliente.post("/api/umbral/propuestas/999", json={"respuesta": "si"}).status_code == 404
    assert len(web.cliente.get("/api/umbral/propuestas").json()["propuestas"]) == 1
    web.cliente.post(f"/api/umbral/propuestas/{pid}", json={"respuesta": "no"})
    assert web.cliente.post(f"/api/umbral/propuestas/{pid}", json={"respuesta": "si"}).status_code == 404


# -- R8 (R-262, C-328): load, form, fatigue and freshness do not move ---------------------------------------------

def test_la_carga_no_cambia_al_aceptar_un_umbral(web):  # R8 · C-328
    pid = _una_propuesta(web)
    web.store.save_activities([Activity(activity_id="b-2", activity_date=HOY, activity_type="road_biking",
                                        name="Rodillo", duration_seconds=3600, avg_hr=150)])
    antes = web.cliente.get("/api/fitness").json()
    web.cliente.post(f"/api/umbral/propuestas/{pid}", json={"respuesta": "si"})
    despues = web.cliente.get("/api/fitness").json()
    assert despues["carga"]["serie"] == antes["carga"]["serie"]
    assert despues["carga"]["hoy"] == antes["carga"]["hoy"]
    assert [s["total"]["carga"] for s in despues["semanas"]] == [s["total"]["carga"] for s in antes["semanas"]]


# -- the race mark from Garmin and the sync that looks for an effort -----------------------------------------------

def test_garmin_trae_la_marca_de_competicion():
    client = GarminClient.__new__(GarminClient)
    client._api = MagicMock()
    base = {"activityId": 1, "startTimeLocal": "2026-10-20 09:00:00", "activityType": {"typeKey": "running"},
            "activityName": "10K", "duration": 2280.0}
    client._api.get_activities_by_date.return_value = [
        {**base, "eventType": {"typeId": 1, "typeKey": "race"}},
        {**base, "activityId": 2, "eventType": {"typeKey": "uncategorized"}},
        {**base, "activityId": 3},
        {**base, "activityId": 4, "eventType": None},
    ]
    actividades = client.get_activities(AYER, AYER)
    assert [a.to_dict()["event_type"] for a in actividades] == ["race", "uncategorized", None, None]


class _Garmin:
    def __init__(self, laps: dict[str, list[ActivityLap]]) -> None:
        self._laps = laps

    def login(self) -> None:
        pass

    def get_activity_laps(self, activity_id: str) -> list[ActivityLap]:
        return self._laps.get(activity_id, [])


def test_traer_vueltas_busca_el_esfuerzo(ajustes, store, db):  # R5: after the sync of a session
    hoy = date.today()
    store.save_activities([_competicion("r-s", hoy - timedelta(days=1), 170)])
    SyncService(_Garmin({"r-s": [ActivityLap("r-s", 1, duration_seconds=2280, avg_hr=170)]}), store).sync_laps()
    (p,) = umbral.Propuestas(db).pendientes()
    assert (p["actividad_id"], p["estimado"]) == ("r-s", 170)


def test_un_fallo_buscando_el_esfuerzo_no_rompe_la_sincronizacion(ajustes, store, monkeypatch):
    hoy = date.today()
    store.save_activities([_competicion("r-s", hoy - timedelta(days=1), 170)])

    def roto(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(umbral, "revisar_sesion", roto)
    hechas = SyncService(_Garmin({"r-s": [ActivityLap("r-s", 1, duration_seconds=2280, avg_hr=170)]}),
                         store).sync_laps()
    assert hechas == 1
    assert store.get_laps("r-s")


def test_la_tabla_de_propuestas_vive_en_la_base_local(ajustes, store, db):
    store.save_activities([_competicion("r-1", AYER, 171)])
    umbral.revisar_sesion(store, "r-1", HOY)
    with sqlite3.connect(db) as conn:
        filas = conn.execute("SELECT deporte, actual, estimado, activity_id, fecha, estado "
                             "FROM propuesta_umbral").fetchall()
    assert filas == [("carrera", 175, 171, "r-1", AYER.isoformat(), "pendiente")]
