"""Unit 030: «Hoy de un vistazo» — what «Cómo llegas» and «Entreno» read, and one sync with Garmin at a time.

Plans `ver-dia-movil` REC-18: R-97, R-100, R-102, R-103 · G-47 (never an invented 0), G-48 (one sync at a time,
also between the app and the daily routine). R1, R4, R6 and R7 of the unit.
Garmin is NEVER called (`GarminClient` and `SyncService` patched); every row is invented.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from test_rutina_reintentos import Rutina, _syncs

from tridata import cli
from tridata.hoy import como_llegas, entreno, texto_sync
from tridata.models import Activity, DailyStats, HRVRecord, SleepRecord
from tridata.storage import DataStore
from tridata.sync import SyncEnMarcha, cerrojo_sync

HOY = date(2026, 10, 21)
REPO = Path(__file__).resolve().parents[1]
EMAIL = "persona@example.invalid"
HRV_7_NOCHES = [58, 60, 61, 59, 63, 57, 62]  # the 7 nights up to last night (62)


def _store(tmp_path: Path) -> DataStore:
    return DataStore(tmp_path / "data.sqlite3")


def _semana_completa(store: DataStore, *, hrv_de_anoche: bool = True) -> None:
    """7 nights of sleep and HRV, today's resting HR with a whole day, and a run of 45 min today."""
    for i, valor in enumerate(HRV_7_NOCHES):
        dia = HOY - timedelta(days=6 - i)
        store.save_sleep(SleepRecord(sleep_date=dia, total_sleep_seconds=25920, sleep_score=82))
        if dia != HOY or hrv_de_anoche:
            store.save_hrv(HRVRecord(hrv_date=dia, last_night_avg=valor))
    store.save_daily_stats(DailyStats(stat_date=HOY, steps=12000, resting_hr=48, total_calories=2600))
    store.save_activities([
        Activity(activity_id="r0", activity_date=HOY - timedelta(days=10), activity_type="running", name="Rodaje",
                 duration_seconds=3600, avg_hr=150, calories=600),
        Activity(activity_id="r1", activity_date=HOY, activity_type="running", name="Carrera suave",
                 duration_seconds=2700, avg_hr=142, calories=520),
    ])


# -- R1 · R-97: «Cómo llegas» with everything ---------------------------------------------------------------

def test_como_llegas_con_todo(tmp_path):  # R1 · R-97
    store = _store(tmp_path)
    _semana_completa(store)

    c = como_llegas(store, HOY)

    assert c["sueno"] == {"estado": "ok", "segundos": 25920, "puntuacion": 82}
    assert c["pulso_reposo"] == {"estado": "ok", "valor": 48}
    assert c["hrv"]["estado"] == "ok" and c["hrv"]["valor"] == 62
    assert c["hrv"]["media_7d"] == pytest.approx(sum(HRV_7_NOCHES) / 7, abs=0.05)
    assert c["frescura"]["estado"] == "ok"
    assert isinstance(c["frescura"]["valor"], float) and c["frescura"]["valor"] != 0


def test_la_frescura_es_la_de_hoy_aunque_el_ultimo_entreno_sea_viejo(tmp_path):  # R1 · R-97
    store = _store(tmp_path)
    store.save_activities([Activity(activity_id="r0", activity_date=HOY - timedelta(days=10), activity_type="running",
                                    name="Rodaje", duration_seconds=3600, avg_hr=150, calories=600)])
    tsb_hoy = como_llegas(store, HOY)["frescura"]["valor"]
    tsb_ayer = como_llegas(store, HOY - timedelta(days=1))["frescura"]["valor"]
    assert tsb_hoy is not None and tsb_ayer is not None
    assert tsb_hoy != tsb_ayer  # the load keeps decaying on rest days up to today


# -- R6 · R-102, G-47, C-143: what has not arrived, and what came half-done --------------------------------

def test_hrv_de_anoche_que_no_ha_llegado(tmp_path):  # R6 · C-143
    store = _store(tmp_path)
    _semana_completa(store, hrv_de_anoche=False)

    hrv = como_llegas(store, HOY)["hrv"]

    assert hrv["estado"] == "no_ha_llegado"
    assert hrv["valor"] is None
    assert hrv["media_7d"] == pytest.approx(sum(HRV_7_NOCHES[:-1]) / 6, abs=0.05)


def test_sin_nada_todo_no_ha_llegado_y_nunca_un_cero(tmp_path):  # R6 · G-47
    c = como_llegas(_store(tmp_path), HOY)
    for clave in ("sueno", "pulso_reposo", "hrv", "frescura"):
        assert c[clave]["estado"] == "no_ha_llegado", clave
        for campo, valor in c[clave].items():
            assert valor != 0 or isinstance(valor, bool), f"{clave}.{campo} is an invented 0"
            if campo != "estado":
                assert valor is None, f"{clave}.{campo}"


def test_fila_sin_valor_no_ha_llegado(tmp_path):  # R6 · G-47: a row whose value Garmin has not filled yet
    store = _store(tmp_path)
    store.save_sleep(SleepRecord(sleep_date=HOY, total_sleep_seconds=None, sleep_score=None))
    store.save_hrv(HRVRecord(hrv_date=HOY, last_night_avg=None))
    store.save_daily_stats(DailyStats(stat_date=HOY, steps=0, resting_hr=None, total_calories=None))
    c = como_llegas(store, HOY)
    assert c["sueno"]["estado"] == c["hrv"]["estado"] == c["pulso_reposo"]["estado"] == "no_ha_llegado"
    assert c["sueno"]["segundos"] is None and c["pulso_reposo"]["valor"] is None


@pytest.mark.parametrize("stats", [
    DailyStats(stat_date=HOY, steps=12000, resting_hr=48, total_calories=300),  # less than the run alone
    DailyStats(stat_date=HOY, steps=500, resting_hr=48, total_calories=2600),  # fewer steps than the run alone
], ids=["kcal", "pasos"])
def test_dia_a_medias_es_incompleto(tmp_path, stats):  # R6 · R-102 (the rule of exporters.py)
    store = _store(tmp_path)
    store.save_activities([Activity(activity_id="r1", activity_date=HOY, activity_type="running", name="Carrera",
                                    duration_seconds=2700, avg_hr=142, avg_cadence=170, calories=520)])
    store.save_daily_stats(stats)
    pulso = como_llegas(store, HOY)["pulso_reposo"]
    assert pulso == {"estado": "incompleto", "valor": 48}


# -- R1: «Entreno», today's activities -----------------------------------------------------------------------

def test_entreno_de_hoy(tmp_path):  # R1 · R-97
    store = _store(tmp_path)
    _semana_completa(store)
    assert entreno(store, HOY) == [{"actividad_id": "r1", "nombre": "Carrera suave", "deporte": "Carrera",
                                    "minutos": 45, "kcal": 520}]
    assert entreno(store, HOY - timedelta(days=1)) == []


def test_entreno_sin_kcal_no_inventa_un_cero(tmp_path):  # R6 · G-47
    store = _store(tmp_path)
    store.save_activities([Activity(activity_id="n1", activity_date=HOY, activity_type="lap_swimming",
                                    name="Natación", duration_seconds=1800, calories=None)])
    (a,) = entreno(store, HOY)
    assert a["deporte"] == "Natación" and a["minutos"] == 30 and a["kcal"] is None


# -- R3 · C-140: the hour of the last good sync -------------------------------------------------------------

@pytest.mark.parametrize(("ok_en", "texto"), [
    ("2026-10-21T09:05:00", "Garmin: sincronizado a las 9:05"),
    ("2026-10-20T08:01:00", "Garmin: sincronizado el martes 20 a las 8:01"),
    (None, "Garmin: aún no se ha sincronizado"),
])
def test_texto_de_la_hora(ok_en, texto):  # R3 · C-140
    assert texto_sync(ok_en, datetime(2026, 10, 21, 10, 0)) == texto


# -- R7 · G-48, C-144: one sync at a time, between processes ------------------------------------------------

def _hijo_con_el_cerrojo(carpeta: Path) -> subprocess.Popen:
    codigo = ("import sys, time\nfrom pathlib import Path\nfrom tridata.sync import cerrojo_sync\n"
              "with cerrojo_sync(Path(sys.argv[1])):\n    print('dentro', flush=True)\n    time.sleep(60)\n")
    entorno = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    hijo = subprocess.Popen([sys.executable, "-c", codigo, str(carpeta)], stdout=subprocess.PIPE, text=True,  # noqa: S603
                            env=entorno)
    assert hijo.stdout is not None and hijo.stdout.readline().strip() == "dentro"
    return hijo


def test_cerrojo_entre_procesos_y_se_suelta_si_el_otro_muere(tmp_path):  # R7 · G-48
    hijo = _hijo_con_el_cerrojo(tmp_path)
    try:
        with pytest.raises(SyncEnMarcha), cerrojo_sync(tmp_path):
            pass
    finally:
        hijo.send_signal(signal.SIGKILL)
        hijo.wait(10)
    with cerrojo_sync(tmp_path):  # the lock of a dead process is released by itself
        pass
    assert (tmp_path / "sync.lock").exists()


def test_cerrojo_en_el_mismo_proceso_y_se_suelta_al_salir(tmp_path):  # R7 · G-48
    with cerrojo_sync(tmp_path):
        with pytest.raises(SyncEnMarcha), cerrojo_sync(tmp_path):
            pass
    with cerrojo_sync(tmp_path):
        pass


# -- R7: `tridata sync` with the button's sync running -------------------------------------------------------

@pytest.fixture
def sync_cli(tmp_path, monkeypatch):
    db = tmp_path / "data.sqlite3"
    store = DataStore(db)
    monkeypatch.setenv("GARMIN_EMAIL", EMAIL)
    monkeypatch.setenv("GARMIN_PASSWORD", "inventada")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(cli, "DataStore", lambda *a, **k: store)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    return store


@pytest.mark.parametrize("args", [["sync"], ["sync", "--laps"], ["sync", "--weather"]])
def test_tridata_sync_con_el_cerrojo_cogido(sync_cli, tmp_path, capsys, args):  # R7 · C-144
    with patch("tridata.cli.GarminClient") as cliente, patch("tridata.cli.SyncService") as servicio:
        with cerrojo_sync(tmp_path):
            codigo = cli.main(args)
    assert codigo == cli.CODIGO_EN_MARCHA != 0
    cliente.assert_not_called()
    servicio.assert_not_called()
    err = capsys.readouterr().err
    assert "ya hay una sincronización con Garmin en marcha" in err
    assert EMAIL not in err
    assert sync_cli.estado_sync() is None  # not a failure of Garmin: nothing written down


def test_tridata_sync_toma_el_cerrojo_mientras_sincroniza(sync_cli, tmp_path):  # R7 · G-48
    def mientras(*a, **k):
        with pytest.raises(SyncEnMarcha), cerrojo_sync(tmp_path):
            pass

    with patch("tridata.cli.GarminClient"), patch("tridata.cli.SyncService") as servicio:
        servicio.return_value.sync.side_effect = mientras
        assert cli.main(["sync"]) == 0
    servicio.return_value.sync.assert_called_once()
    with cerrojo_sync(tmp_path):  # released after
        pass


# -- R7: the daily routine treats it as the usual retry ------------------------------------------------------

def test_la_rutina_reintenta_si_el_boton_esta_en_marcha(tmp_path):  # R7 · G-48
    rutina = Rutina(tmp_path)
    assert rutina.correr(cli.CODIGO_EN_MARCHA, 0) == 0
    log = rutina.texto_log()
    assert "sync: ya hay una sincronización con Garmin en marcha (el botón de la app), se reintenta" in log
    assert "sync OK (intento 2/3)" in log
    assert len(_syncs(rutina.ordenes())) == 2
    assert rutina.esperas_s() == [60]  # the usual wait, not the one of a 429
    assert log.rstrip().endswith("Done.")


# -- R4 · R-100: the button brings at least yesterday and today ---------------------------------------------

@pytest.mark.parametrize(("ok_en", "since", "dias"), [
    (None, HOY - timedelta(days=1), 2),
    (datetime(2026, 10, 21, 8, 1), HOY - timedelta(days=1), 2),
    (datetime(2026, 10, 18, 8, 1), date(2026, 10, 18), 4),
])
def test_el_boton_trae_ayer_y_hoy(tmp_path, ok_en, since, dias):  # R4 · R-100
    store = _store(tmp_path)
    if ok_en is not None:
        store.marcar_sync_ok(ok_en)
    with patch("tridata.cli.GarminClient"), patch("tridata.cli.SyncService") as servicio:
        cli._actualizador_garmin(store, {"GARMIN_EMAIL": EMAIL, "GARMIN_PASSWORD": "inventada"})(HOY)
    servicio.return_value.sync.assert_called_once_with(since=since, until=HOY, revalidate_days=dias)
