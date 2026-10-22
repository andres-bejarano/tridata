"""Unit 018: the last good sync in the base, the warning of today and «Actualizar Garmin» bringing what is missing.

Plans: `sincronizar-dia` R-90, G-44, G-2; `cerrar-dia` R-91; `rutina-diaria` R-60, Q-3 (no health value in
sync.log). Garmin is NEVER called: `GarminClient` is patched (a small fake with invented values) and
`SyncService` is the real one over a temporary SQLite, or patched. «Today» is Wednesday 2026-10-21. Every
value is invented: 2,437 kcal, 12,345 steps, resting HR 47, 27,000 s of sleep and a weight of 71.3.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from garminconnect import GarminConnectTooManyRequestsError
from starlette.testclient import TestClient

from tridata import cli
from tridata.garmin_client import GarminAuthError, GarminClient, GarminRateLimitError
from tridata.models import Activity, DailyStats, HRVRecord, SleepRecord
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, RegistroComidas
from tridata.storage import DataStore
from tridata.sync import HORAS_AVISO, MAX_DIAS_ATRAS, REINTENTO_HASTA, aviso_sync, desde_para_actualizar
from tridata.web import crear_app

HOY = date(2026, 10, 21)  # a Wednesday
AHORA = datetime(2026, 10, 21, 10, 0)
REPO = Path(__file__).resolve().parents[1]
EMAIL = "persona.inventada@ejemplo.invalid"
LIMITE = "Garmin está limitando las peticiones: espera unos minutos y vuelve a probar."
DIA_JSON = json.dumps({"calendarDate": "2026-10-21", "totalKilocalories": 2437, "totalSteps": 12345,
                       "restingHeartRate": 47, "sleepTimeSeconds": 27000, "weight": 71.3})


class ClienteFalso:
    """A `GarminClient` double with invented values; `falla` = where it breaks (None: nowhere)."""

    def __init__(self, falla: str | None = None) -> None:
        self.falla = falla

    def login(self) -> None:
        if self.falla == "sesion":
            raise GarminAuthError(f"Could not authenticate with Garmin Connect: {EMAIL}: Failed to retrieve social "
                                  "profile")
        if self.falla == "limite_login":
            raise GarminRateLimitError("Garmin is limiting the requests (429)")

    def get_activities(self, start, end):
        return [Activity(activity_id="a-inventada", activity_date=HOY - timedelta(days=2), activity_type="running",
                         name="Carrera inventada", duration_seconds=1800, calories=2437)]

    def get_daily_stats(self, day):
        if self.falla == "limite_mitad":
            raise GarminConnectTooManyRequestsError("Too many requests: 429")
        return DailyStats(stat_date=day, steps=12345, resting_hr=47, total_calories=2437, raw={"weight": 71.3})

    def get_sleep(self, day):
        if self.falla == "conexion":
            logging.getLogger("garminconnect").exception("Error fetching sleep: %s", DIA_JSON)
            raise ConnectionError(f"504 Server Error for {EMAIL}: Gateway Timeout {DIA_JSON}")
        return SleepRecord(sleep_date=day, total_sleep_seconds=27000)

    def get_hrv(self, day):
        return HRVRecord(hrv_date=day, last_night_avg=47)


# -- R1 · R-90, G-44, G-2: what `tridata sync` leaves in the base --------------------------------------------

@pytest.fixture
def sync(tmp_path, monkeypatch):
    """`cli.main(["sync"])` over a temporary SQLite, the clock at 2026-10-21 08:01:30, invented credentials."""
    db = tmp_path / "data.sqlite3"
    store = DataStore(db)
    monkeypatch.setenv("GARMIN_EMAIL", EMAIL)
    monkeypatch.setenv("GARMIN_PASSWORD", "inventada")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(cli, "DataStore", lambda *a, **k: store)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_ahora", lambda: datetime(2026, 10, 21, 8, 1, 30))
    monkeypatch.setattr(cli, "_cerrar_dias", lambda store: None)
    return db, store


def _filas(db) -> dict[str, int]:
    with sqlite3.connect(db) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - fixed names
                for t in ("activities", "daily_stats", "sleep")}


def test_sync_bien_apunta_ok_en(sync):  # R1 · R-90, G-44
    db, store = sync
    with patch("tridata.cli.GarminClient", return_value=ClienteFalso()):
        assert cli.main(["sync"]) == 0
    assert store.estado_sync() == {"ok_en": "2026-10-21T08:01:30", "fallo_en": None, "fallo_motivo": None}


@pytest.mark.parametrize(("falla", "motivo", "codigo"), [
    ("sesion", "sesion", 1),
    ("limite_login", "limite", 75),
    ("limite_mitad", "limite", 75),
    ("conexion", "conexion", 1),
])
def test_sync_que_falla_apunta_el_motivo(sync, falla, motivo, codigo):  # R1 · R-90, G-2
    db, store = sync
    store.marcar_sync_ok(datetime(2026, 10, 20, 8, 1))
    with patch("tridata.cli.GarminClient", return_value=ClienteFalso(falla)):
        assert cli.main(["sync"]) == codigo
    assert store.estado_sync() == {"ok_en": "2026-10-20T08:01:00", "fallo_en": "2026-10-21T08:01:30",
                                   "fallo_motivo": motivo}


def test_un_corte_a_mitad_deja_lo_ya_guardado(sync):  # R1 · G-2
    db, store = sync
    with patch("tridata.cli.GarminClient", return_value=ClienteFalso("conexion")):
        assert cli.main(["sync"]) == 1
    filas = _filas(db)
    assert filas["activities"] == 1 and filas["daily_stats"] > 0 and filas["sleep"] == 0
    assert store.estado_sync()["ok_en"] is None


def test_un_bien_despues_de_un_fallo_deja_los_dos(sync):  # R1: the warning compares both
    db, store = sync
    store.marcar_sync_fallo(datetime(2026, 10, 21, 8, 0, 5), "sesion")
    with patch("tridata.cli.GarminClient", return_value=ClienteFalso()):
        assert cli.main(["sync"]) == 0
    estado = store.estado_sync()
    assert estado["ok_en"] == "2026-10-21T08:01:30" and estado["fallo_en"] == "2026-10-21T08:00:05"
    assert aviso_sync(estado, AHORA)["aviso"] is False


@pytest.mark.parametrize("opcion", ["--laps", "--weather"])
@pytest.mark.parametrize("falla", [None, "sesion"])
def test_laps_y_weather_no_tocan_el_registro(sync, opcion, falla):  # R1
    db, store = sync
    with patch("tridata.cli.GarminClient"), patch("tridata.cli.SyncService") as servicio:
        if falla:
            servicio.return_value.sync_laps.side_effect = GarminAuthError("inventado")
            servicio.return_value.sync_weather.side_effect = GarminAuthError("inventado")
        assert cli.main(["sync", opcion]) == (1 if falla else 0)
    assert store.estado_sync() is None


def test_storage_sync_estado_una_fila(tmp_path):  # R1 · G-44
    store = DataStore(tmp_path / "d.sqlite3")
    assert store.estado_sync() is None
    store.marcar_sync_fallo(datetime(2026, 10, 21, 8, 3, 7, 999), "sesion")
    store.marcar_sync_ok(datetime(2026, 10, 21, 9, 0))
    store.marcar_sync_ok(datetime(2026, 10, 21, 10, 0))
    assert store.estado_sync() == {"ok_en": "2026-10-21T10:00:00", "fallo_en": "2026-10-21T08:03:07",
                                   "fallo_motivo": "sesion"}
    with sqlite3.connect(tmp_path / "d.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM sync_estado").fetchone()[0] == 1
    with pytest.raises(ValueError):
        store.marcar_sync_fallo(datetime(2026, 10, 21, 8, 0), "otro")


def test_login_429_es_limite(tmp_path):  # R1: GarminClient.login tells a 429 apart
    api = MagicMock()
    api.login.side_effect = GarminConnectTooManyRequestsError("429 Too Many Requests")
    with patch("tridata.garmin_client.Garmin", return_value=api):
        with pytest.raises(GarminRateLimitError):
            GarminClient(EMAIL, "inventada", token_dir=tmp_path).login()
    api.login.side_effect = RuntimeError("Failed to retrieve social profile")
    with patch("tridata.garmin_client.Garmin", return_value=api):
        with pytest.raises(GarminAuthError):
            GarminClient(EMAIL, "inventada", token_dir=tmp_path).login()
    causa = RuntimeError("wrapped")
    causa.__cause__ = GarminConnectTooManyRequestsError("429")
    api.login.side_effect = causa
    with patch("tridata.garmin_client.Garmin", return_value=api):
        with pytest.raises(GarminRateLimitError):
            GarminClient(EMAIL, "inventada", token_dir=tmp_path).login()


# -- R2 · R-91: the warning and its text --------------------------------------------------------------

def _estado(ok_en=None, fallo_en=None, motivo=None):
    return {"ok_en": ok_en, "fallo_en": fallo_en, "fallo_motivo": motivo}


@pytest.mark.parametrize(("estado", "ahora", "aviso", "texto"), [
    (_estado("2026-10-20T08:01:00", "2026-10-21T08:03:00", "sesion"), AHORA, True,
     "Garmin no se ha sincronizado desde el martes 20 a las 08:01. Se vuelve a intentar sola cada hora hasta las "
     "14:00."),
    (_estado("2026-10-20T08:01:00", "2026-10-21T08:03:00", "sesion"), datetime(2026, 10, 21, 15, 0), True,
     "Garmin no se ha sincronizado desde el martes 20 a las 08:01. Se vuelve a intentar mañana a las 08:00."),
    (_estado("2026-10-19T08:01:00"), AHORA, True,
     "Garmin no se ha sincronizado desde el lunes 19 a las 08:01. Se vuelve a intentar sola cada hora hasta las "
     "14:00."),
    (_estado("2026-10-12T08:01:00"), AHORA, True,
     "Garmin no se ha sincronizado desde el 12 de octubre a las 08:01. Se vuelve a intentar sola cada hora hasta "
     "las 14:00."),
    (_estado("2026-10-21T08:01:00"), AHORA, False, None),
    (None, AHORA, False, None),
])
def test_aviso_sync(estado, ahora, aviso, texto):  # R2 · R-91
    r = aviso_sync(estado, ahora)
    assert (r["aviso"], r["texto"]) == (aviso, texto)
    assert set(r) == {"ok_en", "fallo_en", "fallo_motivo", "aviso", "texto"}


def test_aviso_a_las_26_horas():  # R2: one day plus the routine's margin
    assert HORAS_AVISO == 26
    ok = datetime(2026, 10, 20, 8, 1)
    assert aviso_sync(_estado(ok.isoformat()), ok + timedelta(hours=26))["aviso"] is False
    assert aviso_sync(_estado(ok.isoformat()), ok + timedelta(hours=26, minutes=1))["aviso"] is True


def test_el_timer_dice_hasta_las_catorce():  # R4 · R-92: the timer and REINTENTO_HASTA agree
    timer = (REPO / ".config/systemd/user/tridata-sync.timer").read_text(encoding="utf-8")
    assert f"OnCalendar=*-*-* 08..{REINTENTO_HASTA}:00:00" in timer
    assert "Persistent=true" in timer


def _web(tmp_path, actualizar=None, ahora=AHORA):
    db = tmp_path / "data.sqlite3"
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000}, desde=DESDE_SIEMPRE)
    store = DataStore(db)
    reloj = {"ahora": ahora}
    cliente = TestClient(crear_app(RegistroComidas(db), MagicMock(), ajustes, hoy=lambda: reloj["ahora"].date(),
                                   store=store, actualizar_garmin=actualizar, ahora=lambda: reloj["ahora"]))
    return cliente, store, reloj


def test_garmin_sync_en_el_dia_de_hoy(tmp_path):  # R2 · R-91
    cliente, store, _ = _web(tmp_path)
    antes = cliente.get("/api/dia").json()
    assert antes["garmin_sync"] == {"ok_en": None, "fallo_en": None, "fallo_motivo": None, "aviso": False,
                                    "texto": None}
    store.marcar_sync_ok(datetime(2026, 10, 20, 8, 1))
    store.marcar_sync_fallo(datetime(2026, 10, 21, 8, 3), "sesion")
    hoy = cliente.get("/api/dia").json()
    assert hoy["garmin_sync"]["aviso"] is True
    assert hoy["garmin_sync"]["texto"].startswith("Garmin no se ha sincronizado desde el martes 20 a las 08:01.")
    assert cliente.get("/api/dia?fecha=2026-10-20").json()["garmin_sync"] is None
    assert cliente.get("/api/dia?fecha=2026-10-22").json()["garmin_sync"] is None
    sin = {k: v for k, v in hoy.items() if k != "garmin_sync"}
    assert sin == {k: v for k, v in antes.items() if k != "garmin_sync"}


# -- R3 · R-90: «Actualizar Garmin» brings what is missing ----------------------------------------------------

@pytest.mark.parametrize(("ok_en", "desde"), [
    (None, HOY),
    ("2026-10-19T08:01:00", date(2026, 10, 19)),
    ("2026-10-21T08:01:00", HOY),
    ((AHORA - timedelta(days=60)).isoformat(), HOY - timedelta(days=MAX_DIAS_ATRAS - 1)),
])
def test_desde_para_actualizar(ok_en, desde):  # R3
    estado = None if ok_en is None else _estado(ok_en)
    assert desde_para_actualizar(estado, HOY) == desde


@pytest.mark.parametrize(("ok_en", "since", "dias"), [
    (datetime(2026, 10, 19, 8, 1), date(2026, 10, 19), 3),
    (None, HOY, 1),
    (datetime(2026, 8, 22, 8, 1), HOY - timedelta(days=44), 45),
])
def test_el_boton_pide_desde_el_ultimo_bueno(tmp_path, ok_en, since, dias):  # R3 · R-90
    store = DataStore(tmp_path / "d.sqlite3")
    if ok_en is not None:
        store.marcar_sync_ok(ok_en)
    with patch("tridata.cli.GarminClient") as cliente_garmin, patch("tridata.cli.SyncService") as servicio:
        actualizar = cli._actualizador_garmin(store, {"GARMIN_EMAIL": EMAIL, "GARMIN_PASSWORD": "inventada"})
        actualizar(HOY)
    cliente_garmin.assert_called_once_with(EMAIL, "inventada")
    servicio.return_value.sync.assert_called_once_with(since=since, until=HOY, revalidate_days=dias)


def test_boton_bien_apunta_ok_en_y_quita_el_aviso(tmp_path):  # R3 · R-90, R-91
    actualizar = MagicMock()
    cliente, store, reloj = _web(tmp_path, actualizar)
    store.marcar_sync_ok(datetime(2026, 10, 19, 8, 1))
    assert cliente.get("/api/dia").json()["garmin_sync"]["aviso"] is True
    reloj["ahora"] = datetime(2026, 10, 21, 10, 5, 9)
    r = cliente.post("/api/garmin/actualizar")
    assert r.status_code == 200
    actualizar.assert_called_once_with(HOY)
    assert store.estado_sync()["ok_en"] == "2026-10-21T10:05:09"
    assert r.json()["dia"]["garmin_sync"]["aviso"] is False


@pytest.mark.parametrize("error", [GarminRateLimitError("429"), GarminConnectTooManyRequestsError("429")])
def test_boton_con_429(tmp_path, error):  # R3
    cliente, store, _ = _web(tmp_path, MagicMock(side_effect=error))
    store.marcar_sync_ok(datetime(2026, 10, 19, 8, 1))
    antes = store.estado_sync()
    r = cliente.post("/api/garmin/actualizar")
    assert r.status_code == 502
    assert r.json() == {"error": LIMITE, "motivo": "limite"}
    assert store.estado_sync() == antes


def test_boton_que_falla_no_apunta_nada(tmp_path):  # R3: the failures are written by the routine only
    cliente, store, _ = _web(tmp_path, MagicMock(side_effect=GarminAuthError("inventado")))
    assert cliente.post("/api/garmin/actualizar").status_code == 502
    assert store.estado_sync() is None


# -- R6 · R-60, Q-3: no health value, no trace, no e-mail in sync.log ------------------------------------------

_PROGRAMA = """
import sys
from unittest.mock import patch
sys.path.insert(0, {tests!r})
from test_sync_estado import ClienteFalso
from tridata import cli
falla = sys.argv[1] if sys.argv[1] != "-" else None
with patch("tridata.cli.GarminClient", return_value=ClienteFalso(falla)), patch.object(cli, "load_dotenv", lambda *a, **k: None):
    sys.exit(cli.main(["sync"]))
"""

_NUMEROS = [r"2[.,]?437", r"12[.,]?345", r"27[.,]?000", r"71[.,]3", r"(?<![\d:,.\-])47(?![\d:])"]


def _sin_salud(texto: str) -> list[str]:
    return [p for p in _NUMEROS if re.search(p, texto)]


@pytest.mark.parametrize(("falla", "codigo", "motivo", "tipo"), [
    (None, 0, None, None),
    ("sesion", 1, "sesion", "GarminAuthError"),
    ("limite_mitad", 75, "limite", "GarminConnectTooManyRequestsError"),
    ("conexion", 1, "conexion", "ConnectionError"),
])
def test_salida_de_sync_sin_salud_ni_trazas(tmp_path, falla, codigo, motivo, tipo):  # R6 · R-60, Q-3
    entorno = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "GARMIN_EMAIL": EMAIL,
               "GARMIN_PASSWORD": "inventada", "PYTHONPATH": str(REPO / "src")}
    r = subprocess.run(  # noqa: S603 - our own interpreter and program
        [sys.executable, "-c", _PROGRAMA.format(tests=str(REPO / "tests")), falla or "-"],
        env=entorno, cwd=tmp_path, capture_output=True, text=True, check=False, timeout=120)
    salida = r.stdout + r.stderr
    (tmp_path / "sync.log").write_text(salida, encoding="utf-8")  # the test sync.log of the evidence
    assert r.returncode == codigo, salida
    assert _sin_salud(salida) == [], salida
    assert "Traceback" not in salida and EMAIL not in salida
    assert all(len(linea) <= 300 for linea in salida.splitlines()), salida
    fallos = [linea for linea in salida.splitlines() if linea.startswith("sync FAILED")]
    if motivo is None:
        assert fallos == [] and "Sync complete." in salida
    else:
        assert len(fallos) == 1, salida
        assert fallos[0].startswith(f"sync FAILED: {motivo} ({tipo}")
    if falla == "sesion":
        assert "<email>" in fallos[0]
