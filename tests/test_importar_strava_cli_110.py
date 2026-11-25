"""Unit 110 (REC-53 of `recuperar-historico`): `tridata importar-strava --zip <ruta> [--simular]`.

R8 (R-331, C-422) the summary (how many entered, without track, skipped and why, and which could not be read, with
their reason), a broken file does not stop the rest, and `--simular` says the same writing nothing; R9 (R-332, C-423)
a verified copy of the base before writing anything, and without copy nothing is imported; R10 the rejections with a
clear message. Every zip and value is invented (see `test_importar_strava_110.py`).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

from test_importar_strava_110 import UTC, fila, gpx, hacer_zip

from tridata import cli
from tridata.models import Activity
from tridata.storage import DataStore


def _base(tmp_path):
    db = tmp_path / "base" / "data.sqlite3"
    DataStore(db).save_activities([Activity(activity_id="1", activity_date=date(2026, 3, 1), activity_type="running",
                                            name="De Garmin", duration_seconds=1800, distance_meters=5000)])
    return db


def _ids(db) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {f[0] for f in conn.execute("SELECT activity_id FROM activities")}


def _zip_con_roto(tmp_path):
    return hacer_zip(
        tmp_path / "roto.zip",
        [fila(31, "2 feb 2024, 9:00:00", "Rota de prueba", "Carrera", segundos=3600, metros=10000,
              archivo="activities/31.gpx"),
         fila(32, "3 feb 2024, 9:00:00", "Buena de prueba", "Carrera", segundos=1800, metros=5000,
              archivo="activities/32.gpx"),
         fila(33, "20 ene 2026, 9:00:00", "Del reloj", "Carrera", segundos=1800, metros=5000),
         fila(34, "4 feb 2024, 9:00:00", "Piscina de prueba", "Natación", segundos=1800, metros=1500)],
        {"activities/31.gpx": b"<gpx><trk><trkseg><trkpt lat=",
         "activities/32.gpx": gpx(datetime(2024, 2, 3, 9, tzinfo=UTC), 5000, 1800)})


def test_el_comando_existe():  # R8
    args = cli._build_parser().parse_args(["importar-strava", "--zip", "x.zip", "--simular"])
    assert (args.command, str(args.zip), args.simular) == ("importar-strava", "x.zip", True)


def test_informe_con_un_fichero_roto(tmp_path, capsys):  # R8 · R-331, C-422
    db = _base(tmp_path)
    roto = _zip_con_roto(tmp_path)
    codigo = cli._cmd_importar_strava(roto, False, db_path=db, backup_dir=tmp_path / "copias")
    salida = capsys.readouterr().out
    assert codigo == 0
    assert _ids(db) == {"1", "strava-32", "strava-34"}
    assert "Importadas: 2 (1 sin traza)" in salida
    assert "época del reloj Garmin" in salida and "1" in salida
    assert "Ya estaban: 0" in salida
    assert "No se pudieron leer: 1" in salida
    assert "Rota de prueba (2024-02-02)" in salida


def test_simular_no_escribe_nada(tmp_path, capsys):  # R8 · R-331
    db = _base(tmp_path)
    antes = db.read_bytes()
    roto = _zip_con_roto(tmp_path)
    codigo = cli._cmd_importar_strava(roto, True, db_path=db, backup_dir=tmp_path / "copias")
    salida = capsys.readouterr().out
    assert codigo == 0
    assert db.read_bytes() == antes
    assert not (tmp_path / "copias").exists()
    assert "Simulación" in salida
    assert "Importadas: 2 (1 sin traza)" in salida and "Rota de prueba (2024-02-02)" in salida


def test_simular_sin_base_no_la_crea(tmp_path, capsys):  # R8
    roto = _zip_con_roto(tmp_path)
    db = tmp_path / "nada" / "data.sqlite3"
    assert cli._cmd_importar_strava(roto, True, db_path=db, backup_dir=tmp_path / "copias") == 0
    assert not db.exists()


def test_copia_verificada_antes_de_escribir(tmp_path, capsys):  # R9 · R-332, C-423
    db = _base(tmp_path)
    roto = _zip_con_roto(tmp_path)
    assert cli._cmd_importar_strava(roto, False, db_path=db, backup_dir=tmp_path / "copias") == 0
    (copia,) = (tmp_path / "copias").glob("*/data.sqlite3")
    assert _ids(copia) == {"1"}  # taken before the first activity was written
    assert "Backup OK" in capsys.readouterr().out


def test_sin_copia_no_se_importa_nada(tmp_path, capsys, monkeypatch):  # R9 · R-332, C-423
    db = _base(tmp_path)
    antes = db.read_bytes()
    monkeypatch.setattr(cli, "_cmd_backup", lambda *a, **k: 1)
    roto = _zip_con_roto(tmp_path)
    assert cli._cmd_importar_strava(roto, False, db_path=db, backup_dir=tmp_path / "copias") == 1
    assert db.read_bytes() == antes
    assert "no se ha importado nada" in capsys.readouterr().err


def test_copia_que_revienta_tampoco(tmp_path, capsys, monkeypatch):  # R9
    db = _base(tmp_path)

    def revienta(*_a, **_k):
        raise RuntimeError("integrity check failed")

    monkeypatch.setattr(cli, "_cmd_backup", revienta)
    roto = _zip_con_roto(tmp_path)
    assert cli._cmd_importar_strava(roto, False, db_path=db, backup_dir=tmp_path / "copias") == 1
    assert _ids(db) == {"1"}
    assert "no se ha importado nada" in capsys.readouterr().err


def test_rechazos_con_mensaje_claro(tmp_path, capsys):  # R10
    db = _base(tmp_path)
    antes = db.read_bytes()
    sin_csv = hacer_zip(tmp_path / "otro.zip", [], {"profile.jpg": b"\xff"}, sin_csv=True)
    raro = hacer_zip(tmp_path / "raro.zip", [fila(1, "2 feb 2024, 9:00:00", "Rara", "Carrera", segundos=60, metros=100,
                                                  archivo="../fuera.gpx")], {"../fuera.gpx": b"<gpx/>"})
    for zip_, texto in ((tmp_path / "no-esta.zip", "No encuentro"), (sin_csv, "activities.csv"), (raro, "ruta")):
        assert cli._cmd_importar_strava(zip_, False, db_path=db, backup_dir=tmp_path / "copias") == 1
        err = capsys.readouterr().err
        assert texto in err and "No se importa nada" in err
    assert db.read_bytes() == antes
    assert not (tmp_path / "copias").exists()
