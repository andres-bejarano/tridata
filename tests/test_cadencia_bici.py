"""Unit 093 (REC-38 of `traer-vueltas`): the laps of a ride keep their cadence (R-244), and a one-off refill asks
Garmin again for the laps of the rides already brought without it and ONLY adds that field (R-245, G-4 exception).

Plans v37: R-244, R-245 · C-305..C-307 · G-4. R6-R7 of the unit. Garmin is always a fake client: nothing here talks
to Garmin nor touches ~/.tridata. Every value is invented.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from garminconnect import GarminConnectTooManyRequestsError

from tridata import cli
from tridata.garmin_client import GarminAuthError, GarminClient
from tridata.models import Activity, ActivityLap
from tridata.storage import DataStore
from tridata.sync import SyncService

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture
def store(tmp_path) -> DataStore:
    return DataStore(tmp_path / "data.sqlite3")


def _cliente_con(respuesta: dict) -> GarminClient:
    client = GarminClient.__new__(GarminClient)
    client._api = MagicMock()
    client._api.get_activity_splits.return_value = respuesta
    return client


# -- R6 · R-244, C-305: a new ride keeps the cadence of each lap ------------------------------------------------------

def test_garmin_client_lee_la_cadencia_de_bici():  # R6 · C-305
    laps = _cliente_con({"lapDTOs": [
        {"lapIndex": 1, "distance": 5000.0, "duration": 600.0, "averageBikeCadence": 86.0},
        {"lapIndex": 2, "distance": 5000.0, "duration": 620.0},
    ]}).get_activity_laps("b-nueva")
    assert [v.avg_bike_cadence for v in laps] == [86.0, None]
    assert laps[0].avg_cadence is None  # the run cadence stays the run's


def test_garmin_client_lee_el_otro_nombre_de_la_cadencia_de_bici():  # R6 (the name is not verified on Garmin yet)
    (lap,) = _cliente_con({"lapDTOs": [{"lapIndex": 1, "averageBikingCadenceInRevPerMinute": 84.0}]}
                          ).get_activity_laps("b-nueva")
    assert lap.avg_bike_cadence == 84.0


def test_traer_las_vueltas_de_una_bici_nueva_guarda_su_cadencia(store):  # R6 · C-305
    store.save_activities([Activity("b-nueva", date(2026, 11, 17), "road_biking", "Salida", 3600.0)])
    client = MagicMock()
    client.get_activity_laps.return_value = [ActivityLap("b-nueva", i, distance_meters=5000.0, avg_bike_cadence=c)
                                             for i, c in ((1, 86.0), (2, 88.0), (3, None))]
    SyncService(client, store).sync_laps(limit=20)
    assert [v["avg_bike_cadence"] for v in store.get_laps("b-nueva")] == [86.0, 88.0, None]


# -- R7 · R-245, C-306, C-307, G-4: the one-off refill ------------------------------------------------------------------

def _vuelta_antigua(store: DataStore, id_: str, lap_index: int, **campos) -> dict:
    """A lap stored before this unit: its payload has no `avg_bike_cadence` key at all."""
    payload = {"activity_id": id_, "lap_index": lap_index, "distance_meters": 5000.0, "duration_seconds": 600.0,
               "avg_pace_seconds_per_km": 120.0, "avg_hr": 131.0, "max_hr": 150.0, "avg_cadence": None,
               "avg_stride_length_cm": None, "elevation_gain_m": 21.0, "elevation_loss_m": 19.0,
               "intensity_type": "ACTIVE", "avg_swolf": None, "avg_swim_cadence": None, "total_strokes": None,
               "swim_stroke": None, **campos}
    with store._connect() as conn:
        conn.execute("INSERT INTO activity_laps (activity_id, lap_index, payload) VALUES (?, ?, ?)",
                     (id_, lap_index, json.dumps(payload)))
    return payload


def _sembrar_antiguas(store: DataStore) -> dict[str, list[dict]]:
    """Three rides already brought (b1 oldest … b3 newest), 2 laps each, without cadence; a run with laps and a ride
    whose laps already have the key: neither is asked for."""
    store.save_activities([Activity(f"b{i}", date(2026, 3, i), "cycling", f"Bici {i}", 1200.0) for i in (1, 2, 3)]
                          + [Activity("c1", date(2026, 3, 4), "running", "Carrera", 1800.0),
                             Activity("b-nueva", date(2026, 3, 5), "road_biking", "Nueva", 1800.0)])
    antes = {f"b{i}": [_vuelta_antigua(store, f"b{i}", n, avg_hr=120.0 + n) for n in (1, 2)] for i in (1, 2, 3)}
    _vuelta_antigua(store, "c1", 1, avg_cadence=170.0)
    store.save_activity_laps([ActivityLap("b-nueva", 1, distance_meters=5000.0, avg_bike_cadence=90.0)])
    store.mark_laps_synced("b-nueva", 1)
    for i in (1, 2, 3):
        store.mark_laps_synced(f"b{i}", 2)
    store.mark_laps_synced("c1", 1)
    return antes


def _garmin(fallan: dict[str, BaseException] | None = None) -> MagicMock:
    """Garmin answers each lap with ANOTHER distance, time and HR (to see they are not taken) and a cadence."""
    fallan = fallan or {}

    def laps(activity_id: str) -> list[ActivityLap]:
        if activity_id in fallan:
            raise fallan[activity_id]
        return [ActivityLap(activity_id, n, distance_meters=9999.0, duration_seconds=1.0, avg_hr=199.0,
                            avg_bike_cadence=80.0 + n) for n in (1, 2)]

    client = MagicMock()
    client.get_activity_laps.side_effect = laps
    return client


def test_el_relleno_solo_anade_la_cadencia(store):  # R7 · C-306 (pisar datos guardados es el riesgo)
    antes = _sembrar_antiguas(store)
    client = _garmin()
    resultado = SyncService(client, store).rellenar_cadencia_bici()

    assert sorted(c.args[0] for c in client.get_activity_laps.call_args_list) == ["b1", "b2", "b3"]
    assert resultado["faltan"] == [] and resultado["salidas"] == 3 and resultado["vueltas_con_cadencia"] == 6
    for id_, vueltas in antes.items():
        guardadas = store.get_laps(id_)
        assert guardadas == [{**v, "avg_bike_cadence": 80.0 + v["lap_index"]} for v in vueltas]
    assert store.get_laps("c1")[0].get("avg_bike_cadence", "sin clave") == "sin clave"  # a run is not touched
    assert store.get_laps("b-nueva")[0]["avg_bike_cadence"] == 90.0


def test_si_garmin_falla_con_una_sigue_dice_cual_y_relanzar_pide_solo_esa(store):  # R7 · C-307
    antes = _sembrar_antiguas(store)
    client = _garmin({"b2": RuntimeError("Garmin Connect answered 500")})
    resultado = SyncService(client, store).rellenar_cadencia_bici()

    assert resultado["faltan"] == ["b2"]
    assert store.get_laps("b1")[0]["avg_bike_cadence"] == 81.0
    assert store.get_laps("b3")[0]["avg_bike_cadence"] == 81.0
    assert store.get_laps("b2") == antes["b2"]  # untouched

    otra = _garmin()
    resultado = SyncService(otra, store).rellenar_cadencia_bici()
    otra.get_activity_laps.assert_called_once_with("b2")
    assert resultado["faltan"] == [] and store.get_laps("b2")[1]["avg_bike_cadence"] == 82.0


def test_una_vuelta_sin_cadencia_en_garmin_no_se_vuelve_a_pedir(store):  # R7 · G-4
    _sembrar_antiguas(store)
    client = MagicMock()
    client.get_activity_laps.side_effect = lambda id_: [ActivityLap(id_, 1, avg_bike_cadence=None)]
    SyncService(client, store).rellenar_cadencia_bici()
    assert [v["avg_bike_cadence"] for v in store.get_laps("b1")] == [None, None]  # asked: Garmin had none

    client.get_activity_laps.reset_mock()
    SyncService(client, store).rellenar_cadencia_bici()
    client.get_activity_laps.assert_not_called()


@pytest.mark.parametrize("exc", [GarminConnectTooManyRequestsError("Too many requests"),
                                 RuntimeError("Garmin Connect answered 429"), GarminAuthError("sesión")])
def test_un_429_o_la_sesion_lo_paran_como_sync_laps(store, exc):  # R7 · bug 079
    _sembrar_antiguas(store)
    with pytest.raises(type(exc)):
        SyncService(_garmin({"b3": exc}), store).rellenar_cadencia_bici()
    assert "avg_bike_cadence" not in store.get_laps("b1")[0]


def test_la_rutina_diaria_no_lo_lanza(store):  # R7
    _sembrar_antiguas(store)
    client = _garmin()
    SyncService(client, store).sync_laps(limit=20)
    client.get_activity_laps.assert_not_called()  # every ride was already tried (G-4)
    assert "laps-cadencia-bici" not in (RAIZ / "daily_update.sh").read_text()


def test_la_orden_copia_la_base_y_luego_rellena(store, tmp_path, monkeypatch, capsys):  # R7 · R-245
    _sembrar_antiguas(store)
    monkeypatch.setenv("GARMIN_EMAIL", "nadie@example.invalid")
    monkeypatch.setenv("GARMIN_PASSWORD", "inventada")
    monkeypatch.setattr(cli, "DataStore", lambda: store)
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", tmp_path / "data.sqlite3")
    monkeypatch.setattr(cli, "DEFAULT_BACKUP_DIR", tmp_path / "backups")
    garmin = _garmin({"b2": RuntimeError("500")})
    monkeypatch.setattr(cli, "GarminClient", lambda email, password: garmin)

    codigo = cli.main(["sync", "--laps-cadencia-bici"])

    copias = list((tmp_path / "backups").glob("*/data.sqlite3"))
    assert len(copias) == 1  # the copy of the database before (R-245)
    salida = capsys.readouterr()
    assert "b2" in salida.out + salida.err  # it says which are missing
    assert codigo == 1
    assert store.get_laps("b1")[0]["avg_bike_cadence"] == 81.0
    assert store.estado_sync() is None  # not the normal sync: it writes no «last sync»


def test_la_orden_existe_y_no_es_la_de_por_defecto():  # R7
    assert cli._build_parser().parse_args(["sync", "--laps-cadencia-bici"]).laps_cadencia_bici is True
    assert cli._build_parser().parse_args(["sync"]).laps_cadencia_bici is False
