"""«Actualizar Garmin» button (unit 008, R13): the web route and its wiring with `tridata sync`.

Garmin is NEVER called: `actualizar_garmin` is a `MagicMock`, and in the CLI wiring
`GarminClient` and `SyncService` are patched. Credentials and rows are invented.
"""
import sqlite3
import threading
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from tridata import cli
from tridata.garmin_client import GarminAuthError
from tridata.models import Activity, DailyStats
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, RegistroComidas
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 3, 10)
# unit 016 (R6): a fixed text; nothing of the library's exception reaches the screen
SESION_FALLIDA = ("La sesión de Garmin ha fallado. Prueba otra vez y, si sigue, revisa GARMIN_EMAIL y "
                  "GARMIN_PASSWORD en .env.")


def _app(db, actualizar):
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000}, desde=DESDE_SIEMPRE)
    store = DataStore(db)
    return TestClient(crear_app(RegistroComidas(db), MagicMock(), ajustes, hoy=lambda: HOY, store=store,
                                actualizar_garmin=actualizar)), store


def _filas(db):
    with sqlite3.connect(db) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - fixed names
                for t in ("activities", "daily_stats")}


def test_actualizar_trae_la_natacion_y_recalcula(tmp_path):  # R13
    db = tmp_path / "data.sqlite3"
    actualizar = MagicMock()
    cliente, store = _app(db, actualizar)

    def traer(dia):
        store.save_daily_stats(DailyStats(stat_date=dia, steps=4000))
        store.save_activities([Activity(activity_id="n1", activity_date=dia, activity_type="lap_swimming",
                                        name="Natación", duration_seconds=2700, calories=300)])

    actualizar.side_effect = traer
    assert cliente.get("/api/dia").json()["gasto"]["kcal"] == 2000

    r = cliente.post("/api/garmin/actualizar")

    assert r.status_code == 200
    actualizar.assert_called_once_with(HOY)
    dia = r.json()["dia"]
    assert dia["gasto"]["kcal"] == 2300
    assert dia["barra"]["objetivo_kcal"] == 2300
    assert [a["nombre"] for a in dia["gasto"]["desglose"]["entreno_hecho"]] == ["Natación"]


@pytest.mark.parametrize(("error", "estado", "motivo", "texto"), [
    (GarminAuthError("token caducado"), 502, "sesion", SESION_FALLIDA),
    (ConnectionError("sin red"), 502, "red", "No se ha podido conectar con Garmin"),
])
def test_fallo_de_garmin_lo_dice_y_no_toca_nada(tmp_path, error, estado, motivo, texto):  # R13
    db = tmp_path / "data.sqlite3"
    actualizar = MagicMock(side_effect=error)
    cliente, store = _app(db, actualizar)
    store.save_daily_stats(DailyStats(stat_date=HOY, steps=4000))
    antes = _filas(db)

    r = cliente.post("/api/garmin/actualizar")

    assert r.status_code == estado
    assert r.json()["motivo"] == motivo and texto in r.json()["error"]
    assert _filas(db) == antes
    assert cliente.get("/api/dia").json()["gasto"]["desglose"]["pasos_hechos"] == 4000


def test_sesion_fallida_no_ensena_la_excepcion(tmp_path):  # unit 016 R6 (008 R13, security)
    db = tmp_path / "data.sqlite3"
    actualizar = MagicMock(side_effect=GarminAuthError("Could not authenticate usuario.inventado@ejemplo.com: 401"))
    cliente, store = _app(db, actualizar)
    store.save_daily_stats(DailyStats(stat_date=HOY, steps=4000))
    antes = _filas(db)

    r = cliente.post("/api/garmin/actualizar")

    assert r.status_code == 502
    assert r.json() == {"error": SESION_FALLIDA, "motivo": "sesion"}
    for dentro in ("usuario.inventado@ejemplo.com", "Could not", "401", "authenticate"):
        assert dentro not in r.text
    assert _filas(db) == antes
    assert cliente.get("/api/dia").json()["gasto"]["desglose"]["pasos_hechos"] == 4000


def test_sin_credenciales_lo_dice(tmp_path):  # R13
    db = tmp_path / "data.sqlite3"
    cliente, _ = _app(db, None)
    antes = _filas(db)
    r = cliente.post("/api/garmin/actualizar")
    assert r.status_code == 503
    assert "GARMIN_EMAIL" in r.json()["error"] and "GARMIN_PASSWORD" in r.json()["error"]
    assert _filas(db) == antes


def test_dos_a_la_vez_la_segunda_es_409(tmp_path):  # R13
    db = tmp_path / "data.sqlite3"
    dentro, soltar = threading.Event(), threading.Event()

    def lenta(dia):
        dentro.set()
        assert soltar.wait(10)

    actualizar = MagicMock(side_effect=lenta)
    cliente, _ = _app(db, actualizar)
    respuestas = {}
    primera = threading.Thread(target=lambda: respuestas.setdefault("primera", cliente.post("/api/garmin/actualizar")))
    primera.start()
    try:
        assert dentro.wait(10)
        segunda = cliente.post("/api/garmin/actualizar")
    finally:
        soltar.set()
        primera.join(10)

    assert segunda.status_code == 409
    assert respuestas["primera"].status_code == 200
    assert actualizar.call_count == 1
    assert cliente.post("/api/garmin/actualizar").status_code == 200  # the lock is released after


# -- wiring with `tridata sync`: the same GarminClient + SyncService, for today ---------------------

def test_cmd_web_cablea_la_misma_sincronizacion(tmp_path, monkeypatch):  # R13
    db = tmp_path / "data.sqlite3"
    monkeypatch.setenv("TRIDATA_BASAL_KCAL", "1600")
    monkeypatch.setenv("TRIDATA_KCAL_POR_PASO", "0.04")
    monkeypatch.setenv("GARMIN_EMAIL", "persona@example.invalid")
    monkeypatch.setenv("GARMIN_PASSWORD", "inventada")
    arrancado = MagicMock()
    monkeypatch.setattr("uvicorn.run", arrancado)
    with patch("tridata.cli.GarminClient") as cliente_garmin, patch("tridata.cli.SyncService") as servicio:
        assert cli._cmd_web("127.0.0.1", 8765, db_path=db) == 0
        cliente_garmin.assert_not_called()  # nothing talks to Garmin until the button is pressed
        app = TestClient(arrancado.call_args.args[0])
        r = app.post("/api/garmin/actualizar")

    assert r.status_code == 200
    hoy = date.today()
    cliente_garmin.assert_called_once_with("persona@example.invalid", "inventada")
    (store_usado,) = [c.args[1] for c in servicio.call_args_list]
    assert isinstance(store_usado, DataStore)
    servicio.return_value.sync.assert_called_once_with(since=hoy, until=hoy, revalidate_days=1)


def test_actualizador_sin_credenciales_es_none(tmp_path):  # R13
    assert cli._actualizador_garmin(DataStore(tmp_path / "d.sqlite3"), {}) is None
    assert cli._actualizador_garmin(DataStore(tmp_path / "d.sqlite3"), {"GARMIN_EMAIL": "x@example.invalid"}) is None


def test_cmd_web_sin_credenciales_arranca_y_el_boton_lo_dice(tmp_path, monkeypatch):  # R13
    db = tmp_path / "data.sqlite3"
    monkeypatch.setenv("TRIDATA_BASAL_KCAL", "1600")
    monkeypatch.setenv("TRIDATA_KCAL_POR_PASO", "0.04")
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
    arrancado = MagicMock()
    monkeypatch.setattr("uvicorn.run", arrancado)
    with patch("tridata.cli.GarminClient") as cliente_garmin:
        assert cli._cmd_web("127.0.0.1", 8765, db_path=db) == 0
        r = TestClient(arrancado.call_args.args[0]).post("/api/garmin/actualizar")
    assert r.status_code == 503
    cliente_garmin.assert_not_called()


def test_cmd_web_pasa_el_store_de_la_misma_base(tmp_path, monkeypatch):  # R1
    db = tmp_path / "data.sqlite3"
    monkeypatch.setenv("TRIDATA_BASAL_KCAL", "1600")
    monkeypatch.setenv("TRIDATA_KCAL_POR_PASO", "0.04")
    arrancado = MagicMock()
    monkeypatch.setattr("uvicorn.run", arrancado)
    assert cli._cmd_web("127.0.0.1", 8765, db_path=db) == 0
    DataStore(db).save_daily_stats(DailyStats(stat_date=date.today(), steps=12000))
    dia = TestClient(arrancado.call_args.args[0]).get("/api/dia").json()
    assert dia["gasto"]["desglose"]["pasos_hechos"] == 12000
