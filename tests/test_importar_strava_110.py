"""Unit 110 (REC-53 of `recuperar-historico`): the old activities from the zip of Strava's export.

R1 (R-324, R-329, G-71, G-73) the cut on 18/1/2026 by the day of Madrid, the fields, `strava-<id>`, «de Strava», and
only `activities.csv` and the files of those activities are opened; R2 (R-325) laps of 1 km (10 km by bike) computed
from a GPX track, with their HR, and the track as the session's detail; R3 the real laps of a TCX (`.tcx.gz`); R4
(R-326) without a track, the summary and no laps; R10 a zip that is not an export, a strange path or an XML with
DOCTYPE. Every zip, track and value is invented here, in `tmp_path`: no file of a real export enters the repo.
"""
from __future__ import annotations

import csv
import gzip
import io
import math
import zipfile
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tridata.importar_strava import ZipNoValido, importar
from tridata.storage import DataStore

# The header of the export in Spanish, repeated names included: the second «Tiempo transcurrido» and «Distancia» are
# the raw ones (seconds, metres); the first «Distancia» is in km with a decimal comma.
CABECERAS = ["ID de actividad", "Fecha de la actividad", "Nombre de la actividad", "Tipo de actividad",
             "Descripción de la actividad", "Tiempo transcurrido", "Distancia", "Frecuencia cardiaca máx.",
             "Esfuerzo relativo", "Desplazamiento", "Nota privada de la actividad", "Equipo de la actividad",
             "Nombre del archivo", "Peso del atleta", "Peso de la bicicleta", "Tiempo transcurrido",
             "Tiempo en movimiento", "Distancia", "Velocidad máxima", "Velocidad promedio", "Desnivel positivo",
             "Desnivel negativo", "Elevación mínima", "Elevación máxima", "Frecuencia cardiaca máx.",
             "Frecuencia cardíaca promedio", "Calorías"]
_RADIO_M = 6371008.8
_M_POR_GRADO = _RADIO_M * math.pi / 180  # along a meridian


def fila(ident: int, fecha: str, nombre: str, tipo: str, *, segundos: float, metros: float, archivo: str = "",
         fc_max: str = "", fc_media: str = "", desnivel: str = "", calorias: str = "") -> dict[int, str]:
    """One row of activities.csv by column position (the header has repeated names)."""
    valores = {0: str(ident), 1: fecha, 2: nombre, 3: tipo, 5: str(int(segundos)),
               6: f"{metros / 1000:.2f}".replace(".", ","), 12: archivo, 15: f"{segundos:.1f}",
               16: f"{segundos:.1f}", 17: f"{metros:.1f}", 20: desnivel, 24: fc_max, 25: fc_media, 26: calorias}
    return valores


def csv_de(filas: list[dict[int, str]]) -> bytes:
    salida = io.StringIO()
    escritor = csv.writer(salida)
    escritor.writerow(CABECERAS)
    for f in filas:
        escritor.writerow([f.get(i, "") for i in range(len(CABECERAS))])
    return salida.getvalue().encode("utf-8")


def gpx(inicio: datetime, metros: float, segundos: float, *, paso_m: float = 10.0,
        pulso: Callable[[int], float | None] | None = None, doctype: bool = False, con_hora: bool = True) -> bytes:
    """An invented track along the meridian 0 from the equator (in the sea): `metros` in `segundos`, one point every
    `paso_m`, altitude going up and down, and the HR of `pulso(i)` (none if None)."""
    n = math.ceil(metros / paso_m)
    trozos = []
    for i in range(n + 1):
        d = min(i * paso_m, metros)
        t = inicio + timedelta(seconds=segundos * d / metros)
        hora = f"<time>{t.strftime('%Y-%m-%dT%H:%M:%SZ')}</time>" if con_hora else ""
        hr = pulso(i) if pulso is not None else None
        ext = (f"<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>{int(hr)}</gpxtpx:hr>"
               f"</gpxtpx:TrackPointExtension></extensions>") if hr is not None else ""
        ele = 100 + 20 * math.sin(i / 25)
        trozos.append(f'<trkpt lat="{d / _M_POR_GRADO:.9f}" lon="0.0"><ele>{ele:.1f}</ele>{hora}{ext}</trkpt>')
    cabeza = '<?xml version="1.0" encoding="UTF-8"?>\n'
    if doctype:
        cabeza += '<!DOCTYPE gpx [<!ENTITY x "y">]>\n'
    return (cabeza + '<gpx version="1.1" creator="prueba" xmlns="http://www.topografix.com/GPX/1/1" '
            'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
            f'<trk><name>Inventada</name><trkseg>{"".join(trozos)}</trkseg></trk></gpx>').encode()


def gpx_sin_puntos() -> bytes:
    return (b'<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
            b'<trk><name>Piscina</name><trkseg></trkseg></trk></gpx>')


def gpx_un_punto() -> bytes:
    return (b'<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
            b'<trk><trkseg><trkpt lat="0.0" lon="0.0"><ele>10</ele><time>2024-02-03T09:00:00Z</time></trkpt>'
            b'</trkseg></trk></gpx>')


def tcx(inicio: datetime, vueltas: list[tuple[float, float, int, int]]) -> bytes:
    """An invented TCX with these laps (metres, seconds, mean HR, max HR), each with 4 points of track."""
    laps, recorrido, t = [], 0.0, inicio
    for metros, segundos, media, maxima in vueltas:
        puntos = []
        for k in range(4):
            d = recorrido + metros * k / 3
            puntos.append(f"<Trackpoint><Time>{(t + timedelta(seconds=segundos * k / 3)).strftime('%Y-%m-%dT%H:%M:%SZ')}"
                          f"</Time><Position><LatitudeDegrees>{d / _M_POR_GRADO:.9f}</LatitudeDegrees>"
                          f"<LongitudeDegrees>0.0</LongitudeDegrees></Position><AltitudeMeters>{50 + k}"
                          f"</AltitudeMeters><DistanceMeters>{d:.1f}</DistanceMeters><HeartRateBpm><Value>{media}"
                          f"</Value></HeartRateBpm></Trackpoint>")
        laps.append(f'<Lap StartTime="{t.strftime("%Y-%m-%dT%H:%M:%SZ")}"><TotalTimeSeconds>{segundos}'
                    f"</TotalTimeSeconds><DistanceMeters>{metros}</DistanceMeters><Calories>50</Calories>"
                    f"<AverageHeartRateBpm><Value>{media}</Value></AverageHeartRateBpm><MaximumHeartRateBpm>"
                    f"<Value>{maxima}</Value></MaximumHeartRateBpm><Intensity>Active</Intensity>"
                    f"<TriggerMethod>Manual</TriggerMethod><Track>{''.join(puntos)}</Track></Lap>")
        recorrido += metros
        t += timedelta(seconds=segundos)
    # Strava's TCX begin with blanks before the XML declaration: they must be read anyway
    return ('          <?xml version="1.0" encoding="UTF-8"?><TrainingCenterDatabase '
            'xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"><Activities><Activity Sport="Running">'
            f'<Id>{inicio.strftime("%Y-%m-%dT%H:%M:%SZ")}</Id>{"".join(laps)}</Activity></Activities>'
            '</TrainingCenterDatabase>').encode()


def hacer_zip(ruta: Path, filas: list[dict[int, str]], ficheros: dict[str, bytes] | None = None,
              extra: dict[str, bytes] | None = None, sin_csv: bool = False) -> Path:
    with zipfile.ZipFile(ruta, "w") as z:
        if not sin_csv:
            z.writestr("activities.csv", csv_de(filas))
        for nombre, datos in {**(ficheros or {}), **(extra or {})}.items():
            z.writestr(nombre, datos)
    return ruta


UTC = timezone.utc
MARATON = datetime(2025, 11, 23, 8, 11, 36, tzinfo=UTC)
BICI = datetime(2025, 6, 1, 6, 0, 0, tzinfo=UTC)


def _pulso_carrera(i: int) -> float:
    return 150 + (i % 20)


def zip_variado(tmp_path: Path, extra: dict[str, bytes] | None = None) -> Path:
    """A marathon of 43.19 km with HR, a ride of 45.3 km without HR, a 2022 TCX with 3 laps, a pool swim without
    track, a strength session without file, and two of the Garmin era (one the 18/1 in Madrid but the 17/1 in UTC)."""
    filas = [
        fila(1001, "23 nov 2025, 8:11:36", "Maratón de prueba", "Carrera", segundos=12012, metros=43190,
             archivo="activities/1001.gpx", fc_max="181", fc_media="166", desnivel="120", calorias="2900"),
        fila(1002, "1 jun 2025, 6:00:00", "Salida en bici de prueba", "Bicicleta", segundos=5400, metros=45300,
             archivo="activities/1002.gpx.gz", desnivel="400"),
        fila(1003, "15 mar 2022, 18:00:00", "Series de 2022", "Carrera", segundos=950, metros=3700,
             archivo="activities/1003.tcx.gz", fc_media="155"),
        fila(1004, "16 ene 2026, 17:00:00", "Natación de prueba", "Natación", segundos=2735, metros=2530,
             archivo="activities/1004.gpx"),
        fila(1005, "10 ene 2026, 9:00:00", "Pesas de prueba", "Entrenamiento con pesas", segundos=3000, metros=0),
        fila(1006, "17 ene 2026, 23:30:00", "Ya es del reloj", "Carrera", segundos=1800, metros=5000,
             archivo="activities/1006.gpx"),
        fila(1007, "20 ene 2026, 9:00:00", "Del reloj", "Carrera", segundos=1800, metros=5000,
             archivo="activities/1007.fit.gz"),
    ]
    ficheros = {
        "activities/1001.gpx": gpx(MARATON, 43190, 12012, pulso=_pulso_carrera),
        "activities/1002.gpx.gz": gzip.compress(gpx(BICI, 45300, 5400, paso_m=50)),
        "activities/1003.tcx.gz": gzip.compress(tcx(datetime(2022, 3, 15, 18, 0, tzinfo=UTC),
                                                    [(1500, 380, 150, 160), (1500, 370, 156, 165),
                                                     (700, 200, 160, 170)])),
        "activities/1004.gpx": gpx_sin_puntos(),
        "activities/1006.gpx": gpx(datetime(2026, 1, 17, 23, 30, tzinfo=UTC), 5000, 1800),
        "activities/1007.fit.gz": b"no es un fit",
    }
    return hacer_zip(tmp_path / "export_prueba.zip", filas, ficheros, extra)


@pytest.fixture
def importado(tmp_path):
    db = tmp_path / "data.sqlite3"
    informe = importar(zip_variado(tmp_path), db)
    return DataStore(db), informe


# -- R1 (R-324, G-71, C-413): the cut, the fields, the id and the day of Madrid ----------------------------------------

def test_entran_solo_las_anteriores_al_18_de_enero_en_madrid(importado):  # R1 · R-324, G-71, C-413
    store, informe = importado
    ids = {a["activity_id"] for a in store.activities_between(date(2000, 1, 1), date(2030, 1, 1))}
    assert ids == {"strava-1001", "strava-1002", "strava-1003", "strava-1004", "strava-1005"}
    assert informe.importadas == 5
    assert informe.epoca_garmin == 2  # the 17/1 23:30 UTC is the 18/1 in Madrid, and the 20/1
    assert informe.ya_estaban == 0 and informe.fallidas == []


def test_campos_de_una_importada(importado):  # R1 · R-324, C-413
    store, _ = importado
    a = store.get_activity("strava-1001")
    assert a["activity_date"] == "2025-11-23"
    assert a["start_time_local"] == "2025-11-23 09:11:36"  # 8:11 UTC is 9:11 in Madrid in November
    assert a["activity_type"] == "running"
    assert a["name"] == "Maratón de prueba"
    assert a["duration_seconds"] == 12012
    assert a["distance_meters"] == 43190  # the second «Distancia» (metres), not the first (km, «43,19»)
    assert (a["avg_hr"], a["max_hr"]) == (166, 181)
    assert a["elevation_gain_m"] == 120
    assert a["calories"] == 2900
    assert a["fuente"] == "strava"
    assert a["avg_pace_seconds_per_km"] == pytest.approx(12012 / 43.19, abs=0.1)


def test_hora_de_verano_y_tipos(importado):  # R1 · C-413
    store, _ = importado
    bici = store.get_activity("strava-1002")
    assert bici["start_time_local"] == "2025-06-01 08:00:00"  # CEST: UTC+2
    assert bici["activity_type"] == "cycling"
    assert store.get_activity("strava-1004")["activity_type"] == "lap_swimming"  # no track: the pool
    assert store.get_activity("strava-1005")["activity_type"] == "strength_training"
    assert store.get_activity("strava-1004")["activity_date"] == "2026-01-16"


def test_la_del_17_en_madrid_entra(tmp_path):  # R1 · G-71: 22:30 UTC of the 17/1 is 23:30 in Madrid, still the 17
    zip_ = hacer_zip(tmp_path / "e.zip", [fila(7, "17 ene 2026, 22:30:00", "Última de Strava", "Carrera",
                                               segundos=1800, metros=5000)])
    informe = importar(zip_, tmp_path / "data.sqlite3")
    assert informe.importadas == 1
    assert DataStore(tmp_path / "data.sqlite3").get_activity("strava-7")["activity_date"] == "2026-01-17"


# -- R1 (R-329, G-73, C-419): only the activities are opened ----------------------------------------------------------

def test_solo_se_abren_las_actividades(tmp_path, monkeypatch):  # R1 · R-329, G-73, C-419
    zip_ = zip_variado(tmp_path, extra={"messaging.json": b'{"mensajes": []}', "logins.csv": b"fecha,ip\n",
                                        "profile.jpg": b"\xff\xd8", "media/foto.jpg": b"\xff\xd8"})
    abiertos: list[str] = []
    original = zipfile.ZipFile.open

    def espia(self, nombre, *args, **kwargs):
        abiertos.append(nombre.filename if isinstance(nombre, zipfile.ZipInfo) else nombre)
        return original(self, nombre, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", espia)
    carpeta = tmp_path / "base"
    carpeta.mkdir()
    importar(zip_, carpeta / "data.sqlite3")
    assert set(abiertos) == {"activities.csv", "activities/1001.gpx", "activities/1002.gpx.gz",
                             "activities/1003.tcx.gz", "activities/1004.gpx"}
    assert sorted(p.name for p in carpeta.iterdir()) == ["data.sqlite3"]  # the zip is not copied, nothing else


# -- R2 (R-325, C-414, C-425): laps computed from the GPX track -------------------------------------------------------

def test_vueltas_de_1_km_de_una_carrera_gpx(importado):  # R2 · R-325, C-414 (the criterion that bears the unit)
    store, _ = importado
    laps = store.get_laps("strava-1001")
    assert len(laps) == 44
    assert [v["lap_index"] for v in laps] == list(range(1, 45))
    for v in laps[:43]:
        assert v["distance_meters"] == pytest.approx(1000, abs=0.5)
        assert v["duration_seconds"] == pytest.approx(12012 / 43.19, abs=1)
        assert v["avg_pace_seconds_per_km"] == pytest.approx(12012 / 43.19, abs=1)
        assert 150 <= v["avg_hr"] <= 169
        assert v["max_hr"] >= v["avg_hr"]
        assert v["elevation_gain_m"] is not None and v["elevation_loss_m"] is not None
    assert laps[-1]["distance_meters"] == pytest.approx(190, abs=1)
    assert sum(v["duration_seconds"] for v in laps) == pytest.approx(12012, abs=1)


def test_vueltas_de_10_km_en_bici(importado):  # R2 · S-111, C-425
    store, _ = importado
    laps = store.get_laps("strava-1002")
    assert [round(v["distance_meters"]) for v in laps] == [10000, 10000, 10000, 10000, 5300]
    assert all(v["avg_hr"] is None and v["max_hr"] is None for v in laps)  # the track has no HR
    assert laps[0]["duration_seconds"] == pytest.approx(5400 * 10000 / 45300, abs=1)


def test_la_traza_queda_como_detalle(importado):  # R2 · R-325: as Garmin's detail of the session
    store, _ = importado
    detalle = store.leer_detalle("strava-1001")
    assert detalle is not None
    assert len(detalle["t"]) == 4320
    assert detalle["t"][0] == 0 and detalle["t"][-1] == pytest.approx(12012, abs=0.1)
    assert detalle["distancia"][-1] == pytest.approx(43190, abs=1)
    assert detalle["pulso"][:3] == [150, 151, 152]
    assert detalle["altura"][0] == pytest.approx(100, abs=0.1)
    assert store.estado_detalle("strava-1002") == "traido"


def test_ninguna_se_vuelve_a_pedir_a_garmin(importado):  # R2, R4: no lap, detail or weather is asked of Garmin
    store, _ = importado
    ids = {f"strava-{n}" for n in (1001, 1002, 1003, 1004, 1005)}
    assert ids <= store.activity_ids_lap_synced()
    assert ids <= store.activity_ids_weather_synced()
    assert not ids & set(store.ids_sin_detalle(("running", "cycling", "open_water_swimming", "lap_swimming",
                                                "strength_training", "hiking", "walking")))
    assert not ids & set(store.ids_sin_cadencia_bici(("cycling",)))


# -- R3 (C-415): the real laps of a TCX ------------------------------------------------------------------------------

def test_vueltas_reales_de_un_tcx_gz(importado):  # R3 · C-415
    store, _ = importado
    laps = store.get_laps("strava-1003")
    assert [(v["distance_meters"], v["duration_seconds"], v["avg_hr"], v["max_hr"]) for v in laps] == [
        (1500, 380, 150, 160), (1500, 370, 156, 165), (700, 200, 160, 170)]
    assert laps[0]["avg_pace_seconds_per_km"] == pytest.approx(380 / 1.5, abs=0.1)
    assert store.leer_detalle("strava-1003") is not None


# -- R4 (R-326, C-416): without a track ------------------------------------------------------------------------------

def test_sin_traza_entra_con_el_resumen_y_sin_vueltas(importado):  # R4 · R-326, C-416
    store, informe = importado
    piscina = store.get_activity("strava-1004")
    assert (piscina["distance_meters"], piscina["duration_seconds"]) == (2530, 2735)
    assert store.get_laps("strava-1004") == [] and store.get_laps("strava-1005") == []
    assert store.leer_detalle("strava-1004") is None
    assert store.get_activity("strava-1005")["distance_meters"] is None  # strength: never a 0 km
    assert informe.sin_traza == 2


def test_gpx_sin_hora_cuenta_como_sin_traza(tmp_path):  # R4: a GPX without <time> or with one point
    zip_ = hacer_zip(tmp_path / "e.zip", [
        fila(8, "2 feb 2024, 9:00:00", "Ruta sin hora", "Caminata", segundos=3600, metros=4000,
             archivo="activities/8.gpx"),
        fila(9, "3 feb 2024, 9:00:00", "Un punto", "Senderismo", segundos=3600, metros=4000,
             archivo="activities/9.gpx")],
        {"activities/8.gpx": gpx(datetime(2024, 2, 2, 9, tzinfo=UTC), 4000, 3600, con_hora=False),
         "activities/9.gpx": gpx_un_punto()})
    db = tmp_path / "data.sqlite3"
    informe = importar(zip_, db)
    store = DataStore(db)
    assert (informe.importadas, informe.sin_traza) == (2, 2)
    assert store.get_laps("strava-8") == [] and store.get_laps("strava-9") == []
    assert store.get_activity("strava-8")["activity_type"] == "walking"
    assert store.get_activity("strava-9")["activity_type"] == "hiking"


# -- R8: a broken file does not stop the rest --------------------------------------------------------------------------

def test_un_fichero_roto_no_para_las_demas(tmp_path):  # R8 · C-422
    zip_ = hacer_zip(tmp_path / "e.zip", [
        fila(11, "2 feb 2024, 9:00:00", "Rota", "Carrera", segundos=3600, metros=10000, archivo="activities/11.gpx"),
        fila(12, "3 feb 2024, 9:00:00", "Buena", "Carrera", segundos=1800, metros=5000, archivo="activities/12.gpx"),
        fila(13, "4 feb 2024, 9:00:00", "Sin fichero", "Carrera", segundos=1800, metros=5000,
             archivo="activities/13.gpx")],
        {"activities/11.gpx": b"<gpx><trk><trkseg><trkpt lat=", "activities/12.gpx":
         gpx(datetime(2024, 2, 3, 9, tzinfo=UTC), 5000, 1800)})
    db = tmp_path / "data.sqlite3"
    informe = importar(zip_, db)
    assert informe.importadas == 1
    assert [nombre for nombre, _ in informe.fallidas] == ["Rota (2024-02-02)", "Sin fichero (2024-02-04)"]
    assert all(motivo for _, motivo in informe.fallidas)
    assert DataStore(db).get_activity("strava-11") is None


# -- R10: what is rejected --------------------------------------------------------------------------------------------

def test_zip_sin_activities_csv(tmp_path):  # R10
    zip_ = hacer_zip(tmp_path / "e.zip", [], {"profile.jpg": b"\xff"}, sin_csv=True)
    with pytest.raises(ZipNoValido, match="activities.csv"):
        importar(zip_, tmp_path / "data.sqlite3")
    assert not (tmp_path / "data.sqlite3").exists()


def test_ruta_que_no_existe_o_no_es_zip(tmp_path):  # R10
    with pytest.raises(ZipNoValido, match="No encuentro"):
        importar(tmp_path / "no-esta.zip", tmp_path / "data.sqlite3")
    (tmp_path / "falso.zip").write_bytes(b"no soy un zip")
    with pytest.raises(ZipNoValido, match="zip"):
        importar(tmp_path / "falso.zip", tmp_path / "data.sqlite3")
    assert not (tmp_path / "data.sqlite3").exists()


@pytest.mark.parametrize("nombre", ["../x.gpx", "/etc/x.gpx", "activities/../../x.gpx"])
def test_ruta_rara_dentro_del_zip(tmp_path, nombre):  # R10
    zip_ = hacer_zip(tmp_path / "e.zip", [fila(1, "2 feb 2024, 9:00:00", "Rara", "Carrera", segundos=60,
                                                metros=100, archivo=nombre)],
                     {nombre: gpx(datetime(2024, 2, 2, 9, tzinfo=UTC), 100, 60)})
    with pytest.raises(ZipNoValido, match="ruta"):
        importar(zip_, tmp_path / "data.sqlite3")
    assert not (tmp_path / "data.sqlite3").exists()


def test_gpx_con_doctype_no_se_procesa(tmp_path):  # R10
    zip_ = hacer_zip(tmp_path / "e.zip", [fila(1, "2 feb 2024, 9:00:00", "Con entidades", "Carrera", segundos=600,
                                                metros=2000, archivo="activities/1.gpx")],
                     {"activities/1.gpx": gpx(datetime(2024, 2, 2, 9, tzinfo=UTC), 2000, 600, doctype=True)})
    db = tmp_path / "data.sqlite3"
    informe = importar(zip_, db)
    assert informe.importadas == 0
    assert len(informe.fallidas) == 1 and "DOCTYPE" in informe.fallidas[0][1]
    assert DataStore(db).activities_between(date(2000, 1, 1), date(2030, 1, 1)) == []
