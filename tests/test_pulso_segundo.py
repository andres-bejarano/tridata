"""Unit 094 (REC-39 of `traer-vueltas`, REC-40 of `ver-fitness`): the HR (and speed, altitude and power) second by
second of each session — brought from Garmin with the same queue as the laps, saved compressed apart, and seen in the
session of Fitness as a curve over the zone bands, with the real time in each zone; «Tus semanas» counts the zones
second by second where there is detail.

Plans v38: R-246..R-254 · C-309..C-318 · G-63. R1-R8 of the unit. Garmin is always a fake client: nothing here talks
to Garmin nor touches ~/.tridata. Every value is invented (`tests/fixtures/garmin_detalle_sintetico.json` has the
shape of Garmin's answer, invented numbers and no coordinates).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from garminconnect import GarminConnectTooManyRequestsError

from tridata import cli, fitness
from tridata.garmin_client import GarminAuthError, GarminClient
from tridata.models import Activity, ActivityLap
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroComidas
from tridata.storage import DataStore
from tridata.sync import SyncService

FIXTURE = Path(__file__).parent / "fixtures" / "garmin_detalle_sintetico.json"
SERIES = ("t", "distancia", "pulso", "velocidad", "altura", "potencia")
HOY = date.today()  # the queue and the copy look at the real today (sessions older than 2 days)
ZONAS = [[55, 120], [121, 140], [141, 155], [156, 168], [169, 180]]
PULSO = {"fc_max": 180, "fc_reposo": 55, "zonas_fc": ZONAS, "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}


@pytest.fixture(autouse=True)
def _entorno(monkeypatch):
    monkeypatch.setenv("TRIDATA_HR_MAX", "190")
    monkeypatch.setenv("TRIDATA_HR_REST", "60")


@pytest.fixture
def store(tmp_path) -> DataStore:
    return DataStore(tmp_path / "data.sqlite3")


def _cliente_con(respuesta: dict) -> GarminClient:
    client = GarminClient.__new__(GarminClient)
    client._api = MagicMock()
    client._api.get_activity_details.return_value = respuesta
    return client


def _barajado(original: dict) -> dict:
    """The same answer with the metrics in another order (Garmin changes it between calls)."""
    copia = json.loads(json.dumps(original))
    n = len(copia["metricDescriptors"])
    nuevo = list(reversed(range(n)))  # old index i goes to nuevo[i]
    for d in copia["metricDescriptors"]:
        d["metricsIndex"] = nuevo[d["metricsIndex"]]
    for fila in copia["activityDetailMetrics"]:
        valores = fila["metrics"]
        fila["metrics"] = [valores[nuevo.index(j)] for j in range(n)]
    return copia


def _detalle(segundos: int, pulso=lambda i: 130.0, *, desde: float = 0.0) -> dict:
    """An invented detail of `segundos` points, one per second, at the resolution it is saved with."""
    return {"t": [desde + i for i in range(segundos)],
            "distancia": [round(i * 2.9, 1) for i in range(segundos)],
            "pulso": [pulso(i) for i in range(segundos)],
            "velocidad": [round(2.5 + (i % 17) * 0.031, 3) for i in range(segundos)],
            "altura": [round(80.0 + (i % 250) * 0.3 - 20, 1) for i in range(segundos)],
            "potencia": [None if i % 50 == 0 else float(200 + i % 90) for i in range(segundos)]}


def _actividad(store: DataStore, id_: str, dia: date, tipo: str = "running", segundos: float = 1200, **campos):
    store.save_activities([Activity(activity_id=id_, activity_date=dia, activity_type=tipo, name=id_,
                                    duration_seconds=segundos, **campos)])


# -- R1 · R-246, C-309: Garmin's answer read by key, whatever the order ----------------------------------------------

def test_lee_las_seis_series_por_key_con_el_orden_barajado():  # R1 · R-246, C-309
    original = json.loads(FIXTURE.read_text())
    assert not {"directLatitude", "directLongitude"} & {d["key"] for d in original["metricDescriptors"]}
    assert "geoPolylineDTO" not in original  # the repo is public: no coordinates, ever
    a = _cliente_con(original).get_activity_detail("900000001", 39)
    b = _cliente_con(_barajado(original)).get_activity_detail("900000001", 39)
    assert a == b and set(a) == set(SERIES)
    assert all(len(a[s]) == 40 for s in SERIES)
    assert a["pulso"][:3] == [118.0, 119.0, 120.0] and a["pulso"][7] is None
    assert a["t"][19:22] == [19.0, 19.0, 20.0]
    assert a["potencia"][:3] == [None, None, 242.0]
    assert a["velocidad"][1] == 2.85 and a["altura"][2] == 101.0 and a["distancia"][0] == 0.0


def test_pide_un_punto_por_segundo_y_sin_coordenadas():  # R1 · C-309 (with maxchart ≥ seconds Garmin gives 1/s)
    client = _cliente_con(json.loads(FIXTURE.read_text()))
    client.get_activity_detail("900000001", 4499.4)
    llamada = client._api.get_activity_details.call_args
    args = dict(zip(("activity_id", "maxchart", "maxpoly"), llamada.args, strict=False)) | llamada.kwargs
    assert args["maxchart"] >= 4500 and args["maxpoly"] == 0


def test_sin_metricas_o_sin_pulso_es_none_y_sin_potencia_es_vacia():  # R3 · R-248
    original = json.loads(FIXTURE.read_text())
    assert _cliente_con({"activityDetailMetrics": [], "metricDescriptors": []}).get_activity_detail("1", 60) is None
    assert _cliente_con({"detailsAvailable": False}).get_activity_detail("1", 60) is None
    sin_pulso = json.loads(json.dumps(original))
    i = next(d["metricsIndex"] for d in sin_pulso["metricDescriptors"] if d["key"] == "directHeartRate")
    for fila in sin_pulso["activityDetailMetrics"]:
        fila["metrics"][i] = None
    assert _cliente_con(sin_pulso).get_activity_detail("1", 60) is None
    sin_potencia = json.loads(json.dumps(original))
    sin_potencia["metricDescriptors"] = [d for d in sin_potencia["metricDescriptors"] if d["key"] != "directPower"]
    detalle = _cliente_con(sin_potencia).get_activity_detail("1", 60)
    assert detalle is not None and detalle["potencia"] == [None] * 40


# -- R1 · R-246, C-309: saved compressed, apart from the laps (criterio portante) -------------------------------------

def test_guarda_el_detalle_comprimido_y_lo_lee_igual(store):  # R1 · R-246, C-309 (criterio portante)
    _actividad(store, "tirada", HOY - timedelta(days=1), segundos=4500)
    detalle = _detalle(4500, lambda i: float(120 + i % 45))
    detalle["pulso"][10] = None
    store.guardar_detalle("tirada", detalle)
    leido = store.leer_detalle("tirada")
    assert leido is not None
    for serie in SERIES:
        assert leido[serie] == pytest.approx(detalle[serie], abs=1e-9, nan_ok=False), serie
    assert leido["pulso"][10] is None and leido["potencia"][0] is None
    assert store.estado_detalle("tirada") == "traido"
    with sqlite3.connect(store.db_path) as conn:
        puntos, bytes_ = conn.execute("SELECT puntos, length(datos) FROM activity_detail WHERE activity_id = ?",
                                      ("tirada",)).fetchone()
        tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert puntos == 4500 and bytes_ < 100_000  # a 75-min run: ~4,500 points, under 100 KB
    assert "activity_detail" in tablas and store.get_laps("tirada") == []  # apart from the laps


# -- R2 · R-247, C-310: the queue — up to 20, newest first, only the chosen sports -----------------------------------

TIPOS = ("running", "road_biking", "lap_swimming", "open_water_swimming", "strength_training", "hiking", "walking",
         "trail_running", "cycling", "yoga")


def _sembrar_cola(store: DataStore, n: int = 25) -> list[str]:
    """`n` sessions of mixed types, one per day from yesterday back; returns the eligible ids, newest first."""
    elegibles = []
    for k in range(n):
        tipo = TIPOS[k % len(TIPOS)]
        _actividad(store, f"s{k:02d}", HOY - timedelta(days=1 + k), tipo, 1800, avg_hr=140)
        if tipo not in ("lap_swimming", "strength_training", "yoga"):
            elegibles.append(f"s{k:02d}")
    return elegibles


def _garmin(fallos: dict | None = None, sin: tuple[str, ...] = ()) -> MagicMock:
    """A fake Garmin: a short detail for every session; `fallos` {id: exception}; `sin` ids it has no detail for."""
    client = MagicMock()

    def detalle(activity_id, duracion_s):
        if activity_id in (fallos or {}):
            raise fallos[activity_id]
        return None if activity_id in sin else _detalle(60)

    client.get_activity_detail.side_effect = detalle
    return client


def _pedidas(client: MagicMock) -> list[str]:
    return [c.args[0] for c in client.get_activity_detail.call_args_list]


def test_cola_de_20_de_la_mas_nueva_a_la_mas_vieja_sin_piscina_ni_fuerza(store):  # R2 · R-247, C-310
    elegibles = _sembrar_cola(store, 30)
    assert len(elegibles) > 20
    client = _garmin()
    assert SyncService(client, store).sync_detalle(limit=20) == 20
    assert _pedidas(client) == elegibles[:20]
    assert client.get_activity_detail.call_args_list[0].args[1] == 1800  # its duration, for one point per second
    client2 = _garmin()
    SyncService(client2, store).sync_detalle(limit=20)
    assert _pedidas(client2) == elegibles[20:]  # the next pass goes on where it was
    with sqlite3.connect(store.db_path) as conn:
        tipos = {r[0] for r in conn.execute(
            "SELECT json_extract(a.payload, '$.activity_type') FROM activity_detail d "
            "JOIN activities a USING (activity_id)")}
    assert tipos.isdisjoint({"lap_swimming", "strength_training", "yoga"})
    assert {"open_water_swimming", "hiking", "walking"} <= tipos


def test_un_429_a_la_octava_para_la_pasada_y_la_siguiente_sigue(store):  # R2 · R-247, C-311, G-63
    elegibles = _sembrar_cola(store, 14)
    octava = elegibles[7]
    with pytest.raises(GarminConnectTooManyRequestsError):
        SyncService(_garmin({octava: GarminConnectTooManyRequestsError("Too many requests")}), store).sync_detalle()
    assert [store.estado_detalle(i) for i in elegibles[:7]] == ["traido"] * 7
    assert store.estado_detalle(octava) is None  # still pending
    client = _garmin()
    SyncService(client, store).sync_detalle()
    assert _pedidas(client)[0] == octava


@pytest.mark.parametrize("exc", [RuntimeError("Garmin Connect answered 429"), GarminAuthError("sesión")])
def test_un_429_escondido_o_la_sesion_tambien_paran(store, exc):  # R2 · bug 079
    elegibles = _sembrar_cola(store, 5)
    with pytest.raises(type(exc)):
        SyncService(_garmin({elegibles[1]: exc}), store).sync_detalle()
    assert store.estado_detalle(elegibles[0]) == "traido" and store.estado_detalle(elegibles[2]) is None


def test_otro_fallo_de_una_sesion_sigue_con_las_demas_y_la_deja_pendiente(store, caplog):  # R2 · G-63
    elegibles = _sembrar_cola(store, 5)
    with caplog.at_level("WARNING"):
        n = SyncService(_garmin({elegibles[1]: RuntimeError("500 inventado")}), store).sync_detalle()
    assert n == len(elegibles)
    assert store.estado_detalle(elegibles[1]) is None and store.estado_detalle(elegibles[2]) == "traido"
    assert any(elegibles[1] in r.getMessage() for r in caplog.records)


# -- R2 · R-247, G-63: `sync --laps` brings the detail after the laps and never fails because of it -------------------

@pytest.fixture
def sync_cli(store, tmp_path, monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "nadie@example.invalid")
    monkeypatch.setenv("GARMIN_PASSWORD", "inventada")
    monkeypatch.setattr(cli, "DataStore", lambda *a, **k: store)
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", tmp_path / "data.sqlite3")
    monkeypatch.setattr(cli, "DEFAULT_BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    _actividad(store, "nueva", HOY - timedelta(days=1), "running", 1800, avg_hr=140)
    return store


def _garmin_con_vueltas(detalle_falla: BaseException | None) -> MagicMock:
    client = _garmin({"nueva": detalle_falla} if detalle_falla else None)
    client.get_activity_laps.return_value = [ActivityLap("nueva", i, duration_seconds=600, avg_hr=140)
                                             for i in (1, 2, 3)]
    return client


@pytest.mark.parametrize("falla", [None, GarminConnectTooManyRequestsError("Too many requests"),
                                   GarminAuthError("sesión"), RuntimeError("roto")])
def test_sync_laps_trae_el_detalle_y_su_fallo_no_cambia_la_salida(sync_cli, monkeypatch, capsys, falla):  # R2 · G-63
    client = _garmin_con_vueltas(falla)
    monkeypatch.setattr(cli, "GarminClient", lambda email, password: client)
    assert cli.main(["sync", "--laps"]) == 0  # the same exit as without detail
    assert len(sync_cli.get_laps("nueva")) == 3  # the laps stay
    assert "Lap sync complete: 1 activities processed." in capsys.readouterr().out
    assert sync_cli.estado_detalle("nueva") == (None if falla else "traido")
    assert sync_cli.estado_sync() is None  # not the normal sync
    nombres = [c[0] for c in client.method_calls if c[0].startswith("get_")]
    assert nombres == ["get_activity_laps", "get_activity_detail"]  # the laps first, then the detail


def test_si_fallan_las_vueltas_no_se_pide_el_detalle(sync_cli, monkeypatch):  # R2: as before (exit 1)
    client = _garmin_con_vueltas(None)
    client.get_activity_laps.side_effect = GarminAuthError("sesión")
    monkeypatch.setattr(cli, "GarminClient", lambda email, password: client)
    assert cli.main(["sync", "--laps"]) == 1
    client.get_activity_detail.assert_not_called()


def test_el_boton_trae_tambien_el_detalle_y_su_fallo_no_lo_rompe(monkeypatch, caplog):  # R2 · R-247
    monkeypatch.setattr(cli, "desde_para_actualizar", lambda estado, dia: dia)
    servicio = MagicMock()
    servicio.sync_detalle.side_effect = RuntimeError("Garmin Connect is limiting the requests (429)")
    with patch("tridata.cli.GarminClient"), patch("tridata.cli.SyncService", return_value=servicio), \
            caplog.at_level("WARNING"):
        cli._actualizador_garmin(MagicMock(), {"GARMIN_EMAIL": "persona@example.invalid",
                                               "GARMIN_PASSWORD": "inventada"})(date(2026, 11, 5))
    nombres = [c[0] for c in servicio.method_calls]
    assert nombres[:3] == ["sync", "sync_laps", "sync_weather"] and nombres[3] == "sync_detalle"
    assert servicio.sync_detalle.call_args.kwargs["limit"] <= 20
    assert any("429" in r.getMessage() for r in caplog.records)


def test_la_rutina_no_cambia():  # R2: `sync --laps` of daily_update.sh already brings it; no new order
    rutina = (Path(__file__).resolve().parents[1] / "daily_update.sh").read_text()
    assert "detalle" not in rutina and '"$TRIDATA" sync --laps' in rutina


# -- R3 · R-248, C-312: no detail or no HR → tried, never asked again --------------------------------------------------

def test_sin_detalle_o_sin_pulso_queda_intentada_y_no_se_repite(store):  # R3 · R-248, C-312, G-63
    elegibles = _sembrar_cola(store, 4)
    SyncService(_garmin(sin=(elegibles[0],)), store).sync_detalle()
    assert store.estado_detalle(elegibles[0]) == "intentado" and store.leer_detalle(elegibles[0]) is None
    client = _garmin()
    SyncService(client, store).sync_detalle()
    client.get_activity_detail.assert_not_called()
    assert store.ids_sin_detalle(("running",)) == []


# -- R4 · R-249, C-313: a verified copy before the first pass with old sessions; nothing else changes -----------------

def _volcado(store: DataStore) -> list:
    with sqlite3.connect(store.db_path) as conn:
        return [conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()  # noqa: S608 - fixed names
                for t in ("activities", "activity_laps", "activity_lap_sync_state")]


def test_copia_verificada_una_sola_vez_y_nada_mas_cambia(store, tmp_path):  # R4 · R-249, C-313
    elegibles = _sembrar_cola(store, 30)
    store.save_activity_laps([ActivityLap(i, 1, duration_seconds=900, avg_hr=140) for i in elegibles])
    for i in elegibles:
        store.mark_laps_synced(i, 1)
    antes = _volcado(store)
    SyncService(_garmin(), store).sync_detalle(limit=5)
    copias = sorted((tmp_path / "backups").glob("*/data.sqlite3"))
    assert len(copias) == 1
    with sqlite3.connect(f"file:{copias[0]}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM activity_detail").fetchone()[0] == 0  # taken BEFORE
    assert store.copia_detalle() == str(copias[0])
    SyncService(_garmin(), store).sync_detalle(limit=5)
    assert len(list((tmp_path / "backups").glob("*/data.sqlite3"))) == 1  # once
    assert _volcado(store) == antes  # activities and laps byte to byte


def test_sin_sesiones_antiguas_no_hace_copia(store, tmp_path):  # R4: only the new ones (≤ 2 days) → no copy yet
    _actividad(store, "ayer", HOY - timedelta(days=1), "running", 1800, avg_hr=140)
    SyncService(_garmin(), store).sync_detalle()
    assert store.estado_detalle("ayer") == "traido"
    assert not (tmp_path / "backups").exists() and store.copia_detalle() is None


# -- R5, R6 · R-250..R-252, C-314..C-316: the curve, the lap segments and the time in each zone ------------------------

def _pulso_por_tramos(i: int) -> float | None:
    """600 s in Z1 (110), 300 in Z2 (130), 200 in Z3 (150), 50 without HR, 50 in Z4 (160)."""
    if i < 600:
        return 110.0
    if i < 900:
        return 130.0
    if i < 1100:
        return 150.0
    return None if i < 1150 else 160.0


def _sesion_con_detalle(tmp_path) -> tuple[DataStore, Ajustes]:
    db = tmp_path / "data.sqlite3"
    store, ajustes = DataStore(db), Ajustes(db)
    ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)
    _actividad(store, "series", date(2026, 11, 15), "running", 1200, avg_hr=128, distance_meters=3500)
    store.save_activity_laps([ActivityLap("series", 1, duration_seconds=600, avg_hr=110),
                              ActivityLap("series", 2, duration_seconds=300, avg_hr=130),
                              ActivityLap("series", 3, duration_seconds=300, avg_hr=150)])
    store.guardar_detalle("series", _detalle(1200, _pulso_por_tramos))
    return store, ajustes


def test_curva_tramos_y_tiempo_en_zona(tmp_path):  # R5, R6 · R-250..R-252, C-314..C-316
    store, ajustes = _sesion_con_detalle(tmp_path)
    d = fitness.detalle_actividad(store, ajustes.historial_fc(), "series")
    curva = d["curva"]
    assert 0 < len(curva["t"]) == len(curva["pulso"]) <= fitness.MAX_PUNTOS_CURVA  # reduced only to draw
    assert curva["t"] == sorted(curva["t"]) and curva["t"][0] >= 0 and curva["t"][-1] <= 1199
    assert max(p for p in curva["pulso"] if p is not None) == pytest.approx(160, abs=0.5)
    # every second counts, not the reduced curve
    assert curva["zonas_s"] == {"z1": 600, "z2": 300, "z3": 200, "z4": 50, "z5": 0, "sin_pulso": 50}
    assert curva["limites"] == ZONAS
    assert [(v["n"], v["inicio_s"], v["fin_s"], v["zona"]) for v in curva["tramos"]] == [
        (1, 0, 600, 1), (2, 600, 900, 2), (3, 900, 1200, 3)]
    assert len(d["vueltas"]) == 3  # the laps as before


def test_un_dia_sin_zonas_la_curva_sale_sin_bandas(tmp_path):  # R5: the zones of THAT day (G-49)
    db = tmp_path / "data.sqlite3"
    store, ajustes = DataStore(db), Ajustes(db)
    ajustes.cambiar(PULSO, desde=date(2026, 12, 1))  # confirmed after the session
    _actividad(store, "antes", date(2026, 11, 15), "running", 600, avg_hr=128)
    store.guardar_detalle("antes", _detalle(600))
    curva = fitness.detalle_actividad(store, ajustes.historial_fc(), "antes")["curva"]
    assert curva["limites"] is None and curva["zonas_s"]["sin_pulso"] == 600


# -- R7 · R-253, C-317: «Tus semanas» by second where there is detail, by laps where not, and «aproximado» --------------

def test_zonas_de_semanas_mixtas_y_aproximado(tmp_path):  # R7 · R-253, C-317, S-85
    db = tmp_path / "data.sqlite3"
    store, ajustes = DataStore(db), Ajustes(db)
    ajustes.cambiar(PULSO, desde=DESDE_SIEMPRE)
    lunes = date(2026, 11, 9)
    hoy = lunes + timedelta(days=9)  # Wednesday of the week after
    # this week: two sessions with detail (their laps would say otherwise) and one without (by laps)
    _actividad(store, "d1", lunes, "running", 1200, avg_hr=128)
    store.save_activity_laps([ActivityLap("d1", 1, duration_seconds=1200, avg_hr=128)])  # by laps: all Z2
    store.guardar_detalle("d1", _detalle(1200, _pulso_por_tramos))
    _actividad(store, "d2", lunes + timedelta(days=2), "road_biking", 600, avg_hr=170)
    store.guardar_detalle("d2", _detalle(600, lambda i: 175.0))  # all Z5
    _actividad(store, "v1", lunes + timedelta(days=4), "running", 900, avg_hr=150)
    store.save_activity_laps([ActivityLap("v1", 1, duration_seconds=900, avg_hr=150)])  # Z3 by laps
    # the week after: only sessions with detail
    _actividad(store, "d3", lunes + timedelta(days=7), "running", 300, avg_hr=130)
    store.guardar_detalle("d3", _detalle(300, lambda i: 130.0))

    semanas = fitness.semanas(store, ajustes.historial_fc(), hoy, 3)
    vacia, mixta, completa = semanas
    assert mixta["total"]["zonas"] == {"z1": 600, "z2": 300, "z3": 200 + 900, "z4": 50, "z5": 600, "sin_pulso": 50}
    assert mixta["aproximado"] is True  # a session of the range still by laps
    assert completa["aproximado"] is False and completa["total"]["zonas"]["z2"] == 300
    assert vacia["aproximado"] is False
    assert {"horas", "km", "carga", "zonas"} == set(mixta["total"])  # nothing else changes in the figures
    f = fitness.fitness(store, ajustes.historial_fc(), hoy, dias=14)
    assert [s["aproximado"] for s in f["semanas"]] == [True, False]


# -- R8 · R-254, C-318: a session without detail is seen as today ------------------------------------------------------

class Web:
    def __init__(self, tmp_path) -> None:
        from starlette.testclient import TestClient

        from tridata.web import crear_app

        db = tmp_path / "data.sqlite3"
        self.store, self.ajustes = _sesion_con_detalle(tmp_path)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                             desde=DESDE_SIEMPRE)
        self.cliente = TestClient(crear_app(RegistroComidas(db), MagicMock(), self.ajustes,
                                            hoy=lambda: date(2026, 11, 18), objetivos=ObjetivosDia(db),
                                            store=self.store, actualizar_garmin=MagicMock(),
                                            ahora=lambda: datetime(2026, 11, 18, 10)))


def test_api_sesion_con_y_sin_detalle(tmp_path):  # R5, R8 · R-250, R-254, C-318
    web = Web(tmp_path)
    _actividad(web.store, "marzo", date(2026, 3, 10), "running", 1800, avg_hr=140)
    web.store.save_activity_laps([ActivityLap("marzo", 1, duration_seconds=1800, avg_hr=140)])
    _actividad(web.store, "intentada", date(2026, 3, 11), "running", 1800, avg_hr=140)
    web.store.marcar_detalle_intentado("intentada")
    con = web.cliente.get("/api/actividad/series")
    assert con.status_code == 200 and con.json()["curva"]["zonas_s"]["z1"] == 600
    for id_ in ("marzo", "intentada"):
        r = web.cliente.get(f"/api/actividad/{id_}")
        assert r.status_code == 200, id_
        assert r.json()["curva"] is None
    assert len(web.cliente.get("/api/actividad/marzo").json()["vueltas"]) == 1  # its laps, as today
