"""The old activities from the zip of Strava's export (unit 110, REC-53 of `recuperar-historico`, R-324..R-332).

Run once by the team on the PC (`tridata importar-strava --zip <ruta> [--simular]`): Strava is never called.

- The zip is read WITHOUT extracting it: only `activities.csv` and the file of each activity that enters are opened
  (G-73); messages, logins, photos and the rest are never opened, and the zip is not copied. Every name of the zip
  must be relative and without `..`, and every file the CSV cites must be under `activities/`; otherwise the whole zip
  is refused before anything is read (`ZipNoValido`).
- Only what started before `CORTE` (18/1/2026, the day the Garmin watch starts) in Madrid enters (G-71); the date of
  the CSV is UTC. Id `strava-<id of Strava>`, `fuente: "strava"` in the payload.
- A GPX (or `.gpx.gz`) with a track: laps of `VUELTA_BICI_M` by bike and `VUELTA_M` in the rest, the last one with what
  is left, each with its distance, time, pace, climb and HR; a TCX (or `.tcx.gz`): its real laps. The track becomes the
  detail of the session (`DataStore.guardar_detalle`), as Garmin's. Without a track (no points with time), the summary
  of the CSV and no laps (R-326).
- An activity already there (the same `strava-` id, or one of Garmin of the same sport started in the same minute) is
  skipped untouched (R-327). The laps, the detail and the weather of every imported activity are written down as
  asked, so the routine never asks Garmin for a `strava-` id.
- XML with DOCTYPE or entities is never parsed (as `plan/rutas.py`); a member bigger than `MAX_BYTES` (or a gzip that
  inflates beyond it) is not read. A broken file is reported with its reason and does not stop the others (R-331).
"""
from __future__ import annotations

import csv
import gzip
import io
import math
import posixpath
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET  # noqa: S405 - a DOCTYPE or an ENTITY is refused before parsing (`_raiz`)
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .models import Activity, ActivityLap
from .storage import DataStore
from .sync import CYCLING_TYPES, GYM_TYPES, RUNNING_TYPES, SWIMMING_TYPES

CORTE = date(2026, 1, 18)  # G-71: from this day (in Madrid) Garmin brings them
MADRID = ZoneInfo("Europe/Madrid")
PREFIJO = "strava-"
FUENTE = "strava"
VUELTA_M = 1000.0  # S-111: laps of 1 km...
VUELTA_BICI_M = 10000.0  # ...and of 10 km by bike
RESTO_MINIMO_M = 1.0  # a remainder shorter than this is the noise of the sum, not a lap
CSV = "activities.csv"
CARPETA = "activities/"
MAX_BYTES = 64 * 1024 * 1024  # one file of an activity, inflated
_RADIO_M = 6371008.8

# The type of Strava (Spanish or English export) -> the type of Garmin
_TIPOS = {"carrera": "running", "run": "running", "bicicleta": "cycling", "ride": "cycling",
          "natacion": "lap_swimming", "swim": "lap_swimming", "caminata": "walking", "walk": "walking",
          "senderismo": "hiking", "hike": "hiking", "entrenamiento con pesas": "strength_training",
          "weight training": "strength_training", "entrenamiento": "other", "workout": "other"}
_MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "sept": 9,
          "oct": 10, "nov": 11, "dic": 12, "jan": 1, "apr": 4, "aug": 8, "dec": 12}


class ZipNoValido(ValueError):
    """The zip cannot be imported at all (its text says why, for Andrés): nothing has been written."""


class CopiaFallida(RuntimeError):
    """The copy of the base before writing failed: nothing has been imported (R-332)."""


class _Ilegible(ValueError):
    """The file of one activity cannot be read: its text is the reason in the summary."""


@dataclass
class ActividadStrava(Activity):
    """An activity of Garmin's shape that says where it comes from (R-324): `fuente` goes into the payload."""

    fuente: str = FUENTE


@dataclass
class Informe:
    """What the import did (or would do, `simulado`), for the summary of R-331."""

    importadas: int = 0
    sin_traza: int = 0
    epoca_garmin: int = 0
    ya_estaban: int = 0
    fallidas: list[tuple[str, str]] = field(default_factory=list)  # (name (day), reason)
    columnas_que_faltan: list[str] = field(default_factory=list)
    simulado: bool = False

    def texto(self) -> str:
        lineas = ["Simulación: no se ha escrito nada." if self.simulado else "Importación de Strava hecha.",
                  f"Importadas: {self.importadas} ({self.sin_traza} sin traza)",
                  f"Saltadas por ser de la época del reloj Garmin (desde el {CORTE.day}/{CORTE.month}/"
                  f"{CORTE.year}): {self.epoca_garmin}",
                  f"Ya estaban: {self.ya_estaban}",
                  f"No se pudieron leer: {len(self.fallidas)}"]
        lineas += [f"  - {nombre}: {motivo}" for nombre, motivo in self.fallidas]
        if self.columnas_que_faltan:
            lineas.append(f"Columnas de {CSV} que no encontré (esos datos no entran): "
                          f"{', '.join(self.columnas_que_faltan)}")
        return "\n".join(lineas)


# -- activities.csv ---------------------------------------------------------------------------------------------------

def _normal(texto: str) -> str:
    """Lower case, without accents nor punctuation: «Frecuencia cardíaca máx.» -> «frecuencia cardiaca max»."""
    sin = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)).lower()
    return " ".join("".join(c if c.isalnum() else " " for c in sin).split())


# column -> (names it may have, which appearance: 0 the first, -1 the last, None any with a value)
_COLUMNAS: dict[str, tuple[tuple[str, ...], int | None]] = {
    "id": (("id de actividad", "activity id"), 0),
    "fecha": (("fecha de la actividad", "activity date"), 0),
    "nombre": (("nombre de la actividad", "activity name"), 0),
    "tipo": (("tipo de actividad", "activity type"), 0),
    "tiempo": (("tiempo transcurrido", "elapsed time"), -1),  # the second one: plain seconds
    "movimiento": (("tiempo en movimiento", "moving time"), -1),
    "distancia": (("distancia", "distance"), -1),  # the second one: metres (the first, km)
    "fc_max": (("frecuencia cardiaca max", "frecuencia cardiaca maxima", "max heart rate"), None),
    "fc_media": (("frecuencia cardiaca promedio", "frecuencia cardiaca media", "frecuencia cardiaca medio",
                  "average heart rate"), None),
    "desnivel": (("desnivel positivo", "ganancia de elevacion", "elevation gain"), None),
    "calorias": (("calorias", "calories"), None),
    "archivo": (("nombre del archivo", "filename"), 0),
}
_OBLIGATORIAS = ("id", "fecha", "tipo")
_NOMBRES = {"tiempo": "tiempo transcurrido", "movimiento": "tiempo en movimiento", "distancia": "distancia",
            "fc_max": "pulso máximo", "fc_media": "pulso medio", "desnivel": "desnivel positivo",
            "calorias": "calorías", "archivo": "nombre del archivo", "nombre": "nombre de la actividad"}


def _numero(texto: str | None) -> float | None:
    t = (texto or "").strip()
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".") if "." in t else t.replace(",", ".")
    try:
        valor = float(t)
    except ValueError:
        return None
    return valor if math.isfinite(valor) else None


def _fecha_utc(texto: str) -> datetime | None:
    """«23 nov 2025, 8:11:36» (or «Nov 23, 2025, 8:11:36 AM»), in UTC."""
    partes = _normal(texto).split()
    try:
        if len(partes) >= 6 and partes[0].isdigit():
            dia, mes, anio, h, m, s = partes[:6]
        else:
            mes, dia, anio, h, m, s = partes[:6]
        hora = int(h)
        if len(partes) > 6 and partes[6] in ("am", "pm"):
            hora = hora % 12 + (12 if partes[6] == "pm" else 0)
        return datetime(int(anio), _MESES[mes[:4] if mes[:4] in _MESES else mes[:3]], int(dia), hora, int(m), int(s),
                        tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None


@dataclass
class _Fila:
    numero: int
    valores: dict[str, str | None]

    def get(self, clave: str) -> str | None:
        return self.valores.get(clave)

    def num(self, clave: str) -> float | None:
        return _numero(self.valores.get(clave))


def _leer_csv(datos: bytes) -> tuple[list[_Fila], list[str]]:
    try:
        texto = datos.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ZipNoValido(f"{CSV} no está en UTF-8: no es una exportación de Strava que pueda leer.") from None
    filas = list(csv.reader(io.StringIO(texto)))
    if not filas:
        raise ZipNoValido(f"{CSV} está vacío.")
    cabecera = [_normal(c) for c in filas[0]]
    posiciones: dict[str, list[int]] = {}
    for clave, (nombres, _) in _COLUMNAS.items():
        posiciones[clave] = [i for i, c in enumerate(cabecera) if c in nombres]
    faltan = [c for c in _OBLIGATORIAS if not posiciones[c]]
    if faltan:
        raise ZipNoValido(f"{CSV} no tiene las columnas de una exportación de Strava (id, fecha y tipo de actividad).")
    leidas = []
    for n, fila in enumerate(filas[1:], start=2):
        if not any(c.strip() for c in fila):
            continue
        valores: dict[str, str | None] = {}
        for clave, (_, cual) in _COLUMNAS.items():
            celdas = [fila[i].strip() for i in posiciones[clave] if i < len(fila)]
            if cual is None:
                valores[clave] = next((c for c in reversed(celdas) if c), None)
            else:
                valores[clave] = celdas[cual] if celdas else None
        # one «Distancia» only: it is in km
        if len(posiciones["distancia"]) == 1 and valores["distancia"]:
            km = _numero(valores["distancia"])
            valores["distancia"] = None if km is None else str(km * 1000)
        leidas.append(_Fila(n, valores))
    return leidas, [_NOMBRES[c] for c in _NOMBRES if not posiciones[c]]


# -- the zip ----------------------------------------------------------------------------------------------------------

def _ruta_rara(nombre: str) -> bool:
    if not nombre or "\\" in nombre or nombre.startswith("/") or (len(nombre) > 1 and nombre[1] == ":"):
        return True
    return ".." in nombre.split("/")


def _archivo(fila: _Fila) -> str | None:
    """The file the CSV cites for this activity, checked: under `activities/`, relative, without `..`."""
    nombre = (fila.get("archivo") or "").strip()
    if not nombre:
        return None
    if _ruta_rara(nombre) or not posixpath.normpath(nombre).startswith(CARPETA) or posixpath.normpath(nombre) != nombre:
        raise ZipNoValido(f"La fila {fila.numero} de {CSV} cita un fichero con una ruta rara ({nombre!r}): "
                          "no es una exportación de Strava fiable.")
    return nombre


def _abrir(zip_path: Path) -> zipfile.ZipFile:
    if not zip_path.is_file():
        raise ZipNoValido(f"No encuentro el zip: {zip_path}")
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise ZipNoValido(f"{zip_path.name} no es un zip que pueda leer.") from None
    raros = [n for n in z.namelist() if _ruta_rara(n)]
    if raros:
        z.close()
        raise ZipNoValido(f"El zip tiene ficheros con una ruta rara ({raros[0]!r}): no es una exportación de Strava "
                          "fiable.")
    if CSV not in z.namelist():
        z.close()
        raise ZipNoValido(f"El zip no tiene {CSV}: no es una exportación de Strava.")
    return z


def _leer(z: zipfile.ZipFile, nombre: str) -> bytes:
    try:
        info = z.getinfo(nombre)
    except KeyError:
        raise _Ilegible(f"el zip no trae {nombre}") from None
    if info.file_size > MAX_BYTES:
        raise _Ilegible("el fichero es demasiado grande")
    try:
        with z.open(info) as f:
            datos = f.read(MAX_BYTES + 1)
    except (zipfile.BadZipFile, OSError, EOFError, ValueError) as exc:
        raise _Ilegible(f"el zip no deja leerlo ({type(exc).__name__})") from None
    if nombre.endswith(".gz"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(datos)) as g:
                datos = g.read(MAX_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise _Ilegible(f"el .gz está roto ({type(exc).__name__})") from None
    if len(datos) > MAX_BYTES:
        raise _Ilegible("el fichero es demasiado grande")
    return datos


# -- the track: GPX and TCX -------------------------------------------------------------------------------------------

@dataclass
class _Punto:
    t: float  # seconds since the first point
    d: float  # metres since the first point
    ele: float | None
    hr: float | None


@dataclass
class _Lectura:
    puntos: list[_Punto]  # [] or 1: no track
    vueltas: list[dict[str, Any]] | None = None  # the real laps of a TCX


def _raiz(datos: bytes) -> ET.Element:
    texto = datos.lstrip()
    bajo = texto.lower()
    if b"<!doctype" in bajo or b"<!entity" in bajo:
        raise _Ilegible("XML con DOCTYPE o entidades: no se procesa")
    try:
        return ET.fromstring(texto)  # noqa: S314 - no DOCTYPE, so no entity can be declared
    except ET.ParseError:
        raise _Ilegible("XML roto: no se puede leer") from None


def _local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _hijo(nodo: ET.Element, *nombres: str) -> ET.Element | None:
    actual: ET.Element | None = nodo
    for nombre in nombres:
        if actual is None:
            return None
        actual = next((c for c in actual if _local(c.tag) == nombre), None)
    return actual


def _texto_num(nodo: ET.Element | None) -> float | None:
    return _numero(nodo.text) if nodo is not None else None


def _texto(nodo: ET.Element | None) -> str | None:
    return nodo.text if nodo is not None else None


def _instante(texto: str | None) -> datetime | None:
    t = (texto or "").strip()
    if not t:
        return None
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    if "." in t:  # «.123» or «.123456»: fromisoformat of 3.10 wants 3 or 6 digits
        cabeza, _, cola = t.partition(".")
        cifras = "".join(c for c in cola if c.isdigit())
        t = f"{cabeza}.{(cifras + '000000')[:6]}{cola[len(cifras):]}"
    try:
        instante = datetime.fromisoformat(t)
    except ValueError:
        return None
    return instante if instante.tzinfo else instante.replace(tzinfo=timezone.utc)


def _metros(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * _RADIO_M * math.asin(min(1.0, math.sqrt(h)))


def _hr_de(nodo: ET.Element) -> float | None:
    for c in nodo.iter():
        if _local(c.tag) == "hr":
            return _texto_num(c)
    return None


_Crudo = tuple[datetime, "tuple[float, float] | None", "float | None", "float | None", "float | None"]


def _puntos(crudos: list[_Crudo]) -> list[_Punto]:
    """(time, position, own distance, altitude, HR) -> points from the first one, in time order, the distance
    measured here when the file does not give it."""
    crudos = sorted(crudos, key=lambda p: p[0])
    if not crudos:
        return []
    inicio = crudos[0][0]
    puntos: list[_Punto] = []
    recorrido, anterior = 0.0, None
    for instante, donde, propia, ele, hr in crudos:
        if propia is not None:
            recorrido = max(recorrido, propia)
        elif donde is not None and anterior is not None:
            recorrido += _metros(anterior, donde)
        if donde is not None:
            anterior = donde
        puntos.append(_Punto((instante - inicio).total_seconds(), recorrido, ele, hr if hr else None))
    return puntos


def _leer_gpx(datos: bytes) -> _Lectura:
    raiz = _raiz(datos)
    if _local(raiz.tag) != "gpx":
        raise _Ilegible("no es un GPX")
    crudos: list[_Crudo] = []
    for n in raiz.iter():
        if _local(n.tag) != "trkpt":
            continue
        instante = _instante(_texto(_hijo(n, "time")))
        try:
            donde = (float(n.attrib["lat"]), float(n.attrib["lon"]))
        except (KeyError, ValueError):
            continue
        if instante is None:
            continue
        crudos.append((instante, donde, None, _texto_num(_hijo(n, "ele")), _hr_de(n)))
    return _Lectura(_puntos(crudos))


def _leer_tcx(datos: bytes) -> _Lectura:
    raiz = _raiz(datos)
    if _local(raiz.tag) != "TrainingCenterDatabase":
        raise _Ilegible("no es un TCX")
    crudos: list[_Crudo] = []
    vueltas = []
    for lap in (n for n in raiz.iter() if _local(n.tag) == "Lap"):
        altitudes = []
        for tp in (n for n in lap.iter() if _local(n.tag) == "Trackpoint"):
            instante = _instante(_texto(_hijo(tp, "Time")))
            if instante is None:
                continue
            lat, lon = _texto_num(_hijo(tp, "Position", "LatitudeDegrees")), _texto_num(
                _hijo(tp, "Position", "LongitudeDegrees"))
            ele = _texto_num(_hijo(tp, "AltitudeMeters"))
            altitudes.append(ele)
            crudos.append((instante, (lat, lon) if lat is not None and lon is not None else None,
                           _texto_num(_hijo(tp, "DistanceMeters")), ele, _texto_num(_hijo(tp, "HeartRateBpm", "Value"))))
        subida, bajada = _desnivel(altitudes)
        vueltas.append({"distancia": _texto_num(_hijo(lap, "DistanceMeters")),
                        "segundos": _texto_num(_hijo(lap, "TotalTimeSeconds")),
                        "fc_media": _texto_num(_hijo(lap, "AverageHeartRateBpm", "Value")),
                        "fc_max": _texto_num(_hijo(lap, "MaximumHeartRateBpm", "Value")),
                        "subida": subida, "bajada": bajada})
    return _Lectura(_puntos(crudos), vueltas or None)


def _leer_fichero(nombre: str, datos: bytes) -> _Lectura:
    base = nombre[:-3] if nombre.endswith(".gz") else nombre
    if base.endswith(".gpx"):
        return _leer_gpx(datos)
    if base.endswith(".tcx"):
        return _leer_tcx(datos)
    raise _Ilegible(f"formato {posixpath.splitext(base)[1] or 'sin extensión'} no soportado")


def _desnivel(altitudes: list[float | None]) -> tuple[float | None, float | None]:
    hay = [a for a in altitudes if a is not None]
    if len(hay) < 2:
        return None, None
    subida = sum(max(0.0, b - a) for a, b in zip(hay, hay[1:], strict=False))
    bajada = sum(max(0.0, a - b) for a, b in zip(hay, hay[1:], strict=False))
    return round(subida, 1), round(bajada, 1)


def _tramo(aid: str, n: int, puntos: list[_Punto], d0: float, d1: float, t0: float, t1: float) -> ActivityLap:
    """One lap between the distances d0 and d1 (times t0 and t1): its HR and climb from the points inside it."""
    dentro = [p for p in puntos if t0 <= p.t <= t1]
    pulsos = [p.hr for p in dentro if p.hr]
    subida, bajada = _desnivel([p.ele for p in dentro])
    distancia, segundos = d1 - d0, t1 - t0
    return ActivityLap(activity_id=aid, lap_index=n, distance_meters=round(distancia, 1),
                       duration_seconds=round(segundos, 1),
                       avg_pace_seconds_per_km=round(segundos / distancia * 1000, 1) if distancia > 0 else None,
                       avg_hr=round(sum(pulsos) / len(pulsos)) if pulsos else None,
                       max_hr=max(pulsos) if pulsos else None, elevation_gain_m=subida, elevation_loss_m=bajada)


def vueltas_calculadas(aid: str, puntos: list[_Punto], largo: float) -> list[ActivityLap]:
    """R-325 (S-111): laps of `largo` metres by the accumulated distance of the track, each boundary interpolated in
    time between the two points around it; the last one with what is left."""
    fronteras = [(0.0, 0.0)]  # (distance, time)
    objetivo = largo
    for a, b in zip(puntos, puntos[1:], strict=False):
        while a.d < objetivo <= b.d:
            f = (objetivo - a.d) / (b.d - a.d)
            fronteras.append((objetivo, a.t + f * (b.t - a.t)))
            objetivo += largo
    final = (puntos[-1].d, puntos[-1].t)
    if final[0] - fronteras[-1][0] >= RESTO_MINIMO_M:
        fronteras.append(final)
    return [_tramo(aid, n, puntos, d0, d1, t0, t1)
            for n, ((d0, t0), (d1, t1)) in enumerate(zip(fronteras, fronteras[1:], strict=False), start=1)]


def _vueltas_tcx(aid: str, vueltas: list[dict[str, Any]]) -> list[ActivityLap]:
    """R3: the real laps of the TCX, as they are."""
    laps = []
    for n, v in enumerate(vueltas, start=1):
        distancia, segundos = v["distancia"], v["segundos"]
        laps.append(ActivityLap(activity_id=aid, lap_index=n, distance_meters=distancia, duration_seconds=segundos,
                                avg_pace_seconds_per_km=round(segundos / distancia * 1000, 1)
                                if distancia and segundos else None,
                                avg_hr=v["fc_media"], max_hr=v["fc_max"], elevation_gain_m=v["subida"],
                                elevation_loss_m=v["bajada"]))
    return laps


def _detalle(puntos: list[_Punto]) -> dict[str, list[float | None]]:
    """The track as the detail of the session (`storage._SERIES_DETALLE`): time, distance, HR, speed, altitude."""
    velocidad: list[float | None] = [None]
    for a, b in zip(puntos, puntos[1:], strict=False):
        velocidad.append((b.d - a.d) / (b.t - a.t) if b.t > a.t else None)
    return {"t": [p.t for p in puntos], "distancia": [p.d for p in puntos], "pulso": [p.hr for p in puntos],
            "velocidad": velocidad, "altura": [p.ele for p in puntos], "potencia": [None] * len(puntos)}


# -- what is there already --------------------------------------------------------------------------------------------

def familia(tipo: str | None) -> str:
    """The sport, to compare with Garmin (a trail run and a run are both a run)."""
    for nombre, tipos in (("correr", RUNNING_TYPES), ("bici", CYCLING_TYPES), ("nadar", SWIMMING_TYPES),
                          ("fuerza", GYM_TYPES)):
        if tipo in tipos:
            return nombre
    return str(tipo)


def _ya_hay(db_path: Path) -> tuple[set[str], set[tuple[str, str]]]:
    """The ids in the base and the (sport, minute of start) of each activity, read only: a dry run writes nothing."""
    if not db_path.is_file():
        return set(), set()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'activities'").fetchone() is None:
            return set(), set()
        filas = conn.execute("SELECT activity_id, json_extract(payload, '$.activity_type'), "
                             "json_extract(payload, '$.start_time_local') FROM activities").fetchall()
    finally:
        conn.close()
    ids = {f[0] for f in filas}
    minutos = {(familia(f[1]), str(f[2]).replace("T", " ")[:16]) for f in filas if f[2]}
    return ids, minutos


# -- the import -------------------------------------------------------------------------------------------------------

def _tipo(texto: str | None, con_traza: bool) -> str:
    normal = _normal(texto or "")
    tipo = _TIPOS.get(normal)
    if tipo is None:
        tipo = next((t for clave, t in _TIPOS.items() if normal.startswith(clave)), "other")
    if tipo == "lap_swimming" and con_traza:
        return "open_water_swimming"
    return tipo


@dataclass
class _Plan:
    fila: _Fila
    aid: str
    local: datetime
    archivo: str | None

    @property
    def nombre(self) -> str:
        return f"{self.fila.get('nombre') or 'Actividad sin nombre'} ({self.local.date().isoformat()})"


def _actividad(p: _Plan, lectura: _Lectura | None) -> tuple[ActividadStrava, list[ActivityLap]]:
    f, puntos = p.fila, (lectura.puntos if lectura is not None else [])
    con_traza = len(puntos) >= 2
    tipo = _tipo(f.get("tipo"), con_traza)
    distancia = f.num("distancia") or None
    segundos = f.num("tiempo") or f.num("movimiento") or (puntos[-1].t if con_traza else 0.0)
    pulsos = [q.hr for q in puntos if q.hr]
    fc_media = f.num("fc_media") or (round(sum(pulsos) / len(pulsos)) if pulsos else None)
    fc_max = f.num("fc_max") or (max(pulsos) if pulsos else None)
    subida = f.num("desnivel")
    if subida is None and con_traza:
        subida = _desnivel([q.ele for q in puntos])[0]
    actividad = ActividadStrava(
        activity_id=p.aid, activity_date=p.local.date(), activity_type=tipo,
        name=f.get("nombre") or "Actividad de Strava", duration_seconds=segundos, distance_meters=distancia,
        avg_hr=fc_media, max_hr=fc_max, calories=f.num("calorias") or None,
        avg_pace_seconds_per_km=round(segundos / distancia * 1000, 1) if distancia and segundos else None,
        elevation_gain_m=subida, moving_duration_seconds=f.num("movimiento"),
        start_time_local=p.local.strftime("%Y-%m-%d %H:%M:%S"))
    if lectura is not None and lectura.vueltas:
        laps = _vueltas_tcx(p.aid, lectura.vueltas)
    elif con_traza:
        laps = vueltas_calculadas(p.aid, puntos, VUELTA_BICI_M if tipo in CYCLING_TYPES else VUELTA_M)
    else:
        laps = []
    return actividad, laps


def _guardar(store: DataStore, actividad: ActividadStrava, laps: list[ActivityLap], puntos: list[_Punto]) -> None:
    """Laps, detail and weather first, the activity last: a cut halfway leaves it absent, so the next pass redoes it.
    Each one written down as asked, so the routine never asks Garmin for a `strava-` id."""
    aid = actividad.activity_id
    if laps:
        store.save_activity_laps(laps)
    store.mark_laps_synced(aid, len(laps))
    if len(puntos) >= 2:
        store.guardar_detalle(aid, _detalle(puntos))
    else:
        store.marcar_detalle_intentado(aid)
    store.save_activity_weather(aid, None)
    store.save_activities([actividad])


def _planear(z: zipfile.ZipFile, db_path: Path, informe: Informe) -> list[_Plan]:
    """Everything that can refuse the zip, checked before reading any activity: the CSV and every path it cites."""
    filas, informe.columnas_que_faltan = _leer_csv(_leer_csv_del_zip(z))
    archivos = {f.numero: _archivo(f) for f in filas}
    ids, minutos = _ya_hay(db_path)
    planes = []
    for f in filas:
        ident, instante = (f.get("id") or "").strip(), _fecha_utc(f.get("fecha") or "")
        if not ident.isdigit() or instante is None:
            informe.fallidas.append((f"Fila {f.numero} de {CSV}", "sin id o sin fecha que se pueda leer"))
            continue
        local = instante.astimezone(MADRID).replace(tzinfo=None)
        if local.date() >= CORTE:
            informe.epoca_garmin += 1
            continue
        aid = PREFIJO + ident
        minuto = local.strftime("%Y-%m-%d %H:%M")
        if aid in ids or (familia(_tipo(f.get("tipo"), False)), minuto) in minutos:
            informe.ya_estaban += 1
            continue
        ids.add(aid)  # the same id twice in the CSV enters once
        planes.append(_Plan(f, aid, local, archivos[f.numero]))
    return planes


def _leer_csv_del_zip(z: zipfile.ZipFile) -> bytes:
    try:
        return _leer(z, CSV)
    except _Ilegible as exc:
        raise ZipNoValido(f"No puedo leer {CSV}: {exc}.") from None


def importar(zip_path: Path | str, db_path: Path | str, *, simular: bool = False,
             copia: Callable[[], None] | None = None) -> Informe:
    """R-324..R-332: imports the activities of the export before `CORTE` into the base `db_path` and says what it did.
    With `simular`, the same summary writing nothing (the base is only read). `copia` runs once before the first write
    (R-332): if it raises, `CopiaFallida` and nothing is written. `ZipNoValido` if the zip cannot be imported at all."""
    zip_path, db_path = Path(zip_path), Path(db_path)
    informe = Informe(simulado=simular)
    with _abrir(zip_path) as z:
        planes = _planear(z, db_path, informe)
        store: DataStore | None = None
        for actividad, laps, puntos in _leidas(z, planes, informe):
            if not simular:
                if store is None:
                    if copia is not None:
                        try:
                            copia()
                        except Exception as exc:  # noqa: BLE001 - any failure of the copy stops before writing
                            raise CopiaFallida(f"{type(exc).__name__}: {exc}") from exc
                    store = DataStore(db_path)
                _guardar(store, actividad, laps, puntos)
            informe.importadas += 1
            informe.sin_traza += 0 if len(puntos) >= 2 else 1
    return informe


def _leidas(z: zipfile.ZipFile, planes: list[_Plan], informe: Informe,
            ) -> Iterator[tuple[ActividadStrava, list[ActivityLap], list[_Punto]]]:
    """Each planned activity read from its own file (only then is it opened), one by one; a broken one is reported."""
    for p in planes:
        lectura = None
        if p.archivo is not None:
            try:
                lectura = _leer_fichero(p.archivo, _leer(z, p.archivo))
            except _Ilegible as exc:
                informe.fallidas.append((p.nombre, f"{p.archivo}: {exc}"))
                continue
        try:
            actividad, laps = _actividad(p, lectura)
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            informe.fallidas.append((p.nombre, f"{p.archivo or 'sin fichero'}: datos raros ({type(exc).__name__})"))
            continue
        yield actividad, laps, (lectura.puntos if lectura is not None else [])
