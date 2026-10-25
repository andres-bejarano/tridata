"""The profile of a route (unit 045, REC-25 of `preparar-carrera`, R-152..R-154, G-57).

A route comes from three places, all of them read on this PC: a route saved in Garmin Connect (its points with
distance and altitude, `GarminClient.ruta` -> `puntos_garmin`), an activity already run (its GPX,
`GarminClient.gpx_actividad`) or a GPX uploaded (`puntos_gpx`, with `xml.etree`: no new dependency). Its profile
(`perfil`) is the altitude every 100 m, smoothed so the wobble of a GPS does not become climbs, and its stretches
(«tramos») of climb, descent and flat, each with its climb: a stretch is at least 500 m, and a climb or a descent of
less than 10 m is flat. A route without altitude raises `SinAltitud`: the profile is never invented (R-153).

The profile carries km and metres only: latitude and longitude are used to measure the distance of a GPX and are
dropped here, so they never reach a conversation nor the model (`perfil_para_modelo`, G-57).
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET  # noqa: S405 - Andrés's own GPX on his PC; a DOCTYPE is refused below
from dataclasses import dataclass
from typing import Any

PASO_M = 100  # the altitude every 100 m (R-152)
_VENTANA = 5  # the smoothing: the mean of 5 points of 100 m (±200 m)
_PENDIENTE = 1.5  # % from which 100 m climb or descend
_TRAMO_MINIMO = 5  # 500 m: a shorter stretch joins its neighbour
_DESNIVEL_MINIMO = 10  # m: a climb or a descent of less is flat
_RADIO_M = 6371008.8
FUENTES = {"garmin": "ruta guardada en Garmin", "actividad": "de una actividad hecha", "gpx": "GPX subido"}


class RutaMal(ValueError):
    """That is not a route that can be read (not a GPX, no points, too short): its text says why, for Andrés."""


class SinAltitud(RutaMal):
    """The route has no altitude: no profile without inventing it (R-153)."""


SIN_ALTITUD = "Esta ruta no trae altitud: no puedo sacar su perfil sin inventarlo. Sube un GPX con altitud."


@dataclass(frozen=True)
class Punto:
    distancia_m: float
    altitud_m: float | None
    lat: float | None = None
    lon: float | None = None


def _metros(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Haversine distance in metres between two (lat, lon)."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * _RADIO_M * math.asin(min(1.0, math.sqrt(h)))


def _raiz(datos: bytes | str) -> ET.Element:
    texto = datos.encode() if isinstance(datos, str) else bytes(datos)
    if not texto.strip():
        raise RutaMal("El fichero está vacío: sube el GPX de la ruta.")
    if b"<!doctype" in texto.lower():  # no entities: neither a bomb nor a file read
        raise RutaMal("Ese fichero no es un GPX que pueda leer.")
    try:
        return ET.fromstring(texto)  # noqa: S314 - no DOCTYPE, so no entity can be declared
    except ET.ParseError:
        raise RutaMal("Ese fichero no es un GPX que pueda leer.") from None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def nombre_gpx(datos: bytes | str) -> str | None:
    """The name the GPX gives itself (its metadata, its track or its route), or None."""
    try:
        raiz = _raiz(datos)
    except RutaMal:
        return None
    for nodo in raiz.iter():
        if _local(nodo.tag) == "name" and nodo.text and nodo.text.strip():
            return " ".join(nodo.text.split())
    return None


def puntos_gpx(datos: bytes | str) -> list[Punto]:
    """The points of the track (or the route) of a GPX, with their distance from the start measured here."""
    raiz = _raiz(datos)
    if _local(raiz.tag) != "gpx":
        raise RutaMal("Ese fichero no es un GPX que pueda leer.")
    nodos = [n for n in raiz.iter() if _local(n.tag) == "trkpt"] or [n for n in raiz.iter() if _local(n.tag) == "rtept"]
    puntos: list[Punto] = []
    anterior: tuple[float, float] | None = None
    recorrido = 0.0
    for n in nodos:
        try:
            donde = (float(n.attrib["lat"]), float(n.attrib["lon"]))
        except (KeyError, ValueError):
            continue
        ele = next((c.text for c in n if _local(c.tag) == "ele"), None)
        try:
            altitud = float(ele) if ele is not None and ele.strip() else None
        except ValueError:
            altitud = None
        if anterior is not None:
            recorrido += _metros(anterior, donde)
        anterior = donde
        puntos.append(Punto(recorrido, altitud, *donde))
    return puntos


def puntos_garmin(puntos: list[dict[str, Any]]) -> list[Punto]:
    """The points of a route of Garmin (`GarminClient.ruta`): its own distance, or measured here if it has none."""
    salida: list[Punto] = []
    recorrido = 0.0
    for p in puntos:
        lat, lon = p.get("lat"), p.get("lon")
        distancia = p.get("distancia_m")
        if distancia is None and salida and None not in (lat, lon, salida[-1].lat, salida[-1].lon):
            recorrido += _metros((salida[-1].lat, salida[-1].lon), (lat, lon))  # type: ignore[arg-type]
            distancia = recorrido
        if distancia is None:
            distancia = recorrido
        recorrido = float(distancia)
        altitud = p.get("altitud_m")
        salida.append(Punto(float(distancia), float(altitud) if altitud is not None else None, lat, lon))
    return salida


def _interpolar(puntos: list[Punto], d: float) -> float:
    """The altitude at `d` metres, between the two points with altitude around it."""
    bajo, alto = 0, len(puntos) - 1
    if d <= puntos[0].distancia_m:
        return float(puntos[0].altitud_m)  # type: ignore[arg-type]
    if d >= puntos[-1].distancia_m:
        return float(puntos[-1].altitud_m)  # type: ignore[arg-type]
    while alto - bajo > 1:
        medio = (bajo + alto) // 2
        if puntos[medio].distancia_m <= d:
            bajo = medio
        else:
            alto = medio
    a, b = puntos[bajo], puntos[alto]
    if b.distancia_m == a.distancia_m:
        return float(a.altitud_m)  # type: ignore[arg-type]
    t = (d - a.distancia_m) / (b.distancia_m - a.distancia_m)
    return float(a.altitud_m) + t * (float(b.altitud_m) - float(a.altitud_m))  # type: ignore[arg-type]


def _suavizar(valores: list[float]) -> list[float]:
    media = _VENTANA // 2
    return [sum(valores[max(0, i - media):i + media + 1]) / len(valores[max(0, i - media):i + media + 1])
            for i in range(len(valores))]


def _terreno(pendiente: float) -> str:
    return "subida" if pendiente >= _PENDIENTE else "bajada" if pendiente <= -_PENDIENTE else "llano"


def _juntar(tramos: list[list[Any]]) -> list[list[Any]]:
    """Neighbours of the same terrain become one: [desde, hasta, terreno]."""
    salida: list[list[Any]] = []
    for t in tramos:
        if salida and salida[-1][2] == t[2]:
            salida[-1][1] = t[1]
        else:
            salida.append(list(t))
    return salida


def _tramos(alturas: list[float]) -> list[list[Any]]:
    """The stretches over the smoothed altitudes (indices of 100 m): by slope, then without the short ones and
    without the climbs or descents that barely climb."""
    tramos = _juntar([[i, i + 1, _terreno((alturas[i + 1] - alturas[i]) / PASO_M * 100)]
                      for i in range(len(alturas) - 1)])
    while True:
        for t in tramos:  # a climb or a descent that barely climbs is flat
            if t[2] != "llano" and abs(alturas[t[1]] - alturas[t[0]]) < _DESNIVEL_MINIMO:
                t[2] = "llano"
        tramos = _juntar(tramos)
        cortos = [i for i, t in enumerate(tramos) if t[1] - t[0] < _TRAMO_MINIMO]
        if not cortos or len(tramos) == 1:
            return _cimas(tramos, alturas)
        i = min(cortos, key=lambda k: tramos[k][1] - tramos[k][0])
        vecinos = [k for k in (i - 1, i + 1) if 0 <= k < len(tramos)]
        tramos[i][2] = tramos[max(vecinos, key=lambda k: tramos[k][1] - tramos[k][0])][2]
        tramos = _juntar(tramos)


def _cimas(tramos: list[list[Any]], alturas: list[float]) -> list[list[Any]]:
    """A climb ends at its top and a descent at its bottom: the border between them goes to the highest (or the
    lowest) altitude around it, so each one climbs what it climbs."""
    for a, b in zip(tramos, tramos[1:], strict=False):
        par = (a[2], b[2])
        if par not in (("subida", "bajada"), ("bajada", "subida")):
            continue
        tramo = range(a[0] + 1, b[1])
        borde = (max if par[0] == "subida" else min)(tramo, key=lambda i: alturas[i])
        a[1] = b[0] = borde
    return tramos


def perfil(puntos: list[Punto], nombre: str, fuente: str) -> dict[str, Any]:
    """`{nombre, fuente, distancia_km, sube_m, baja_m, paso_m, altitudes, tramos}`: the altitude every 100 m from km 0
    (smoothed, in whole metres) and the stretches `{desde_km, hasta_km, terreno, desnivel_m, pendiente_pct}`.
    Never a coordinate (G-57). `RutaMal` without points, `SinAltitud` without altitude (R-153)."""
    puntos = sorted(puntos, key=lambda p: p.distancia_m)
    if len(puntos) < 2:
        raise RutaMal("Esa ruta no trae puntos: sube el GPX de la ruta.")
    con_altitud = [p for p in puntos if p.altitud_m is not None]
    if len(con_altitud) < 2 or len(con_altitud) < len(puntos) / 2:
        raise SinAltitud(SIN_ALTITUD)
    total = puntos[-1].distancia_m - puntos[0].distancia_m
    if total < 2 * PASO_M:
        raise RutaMal("Esa ruta es demasiado corta para sacar su perfil.")
    inicio = puntos[0].distancia_m
    alturas = _suavizar([_interpolar(con_altitud, inicio + i * PASO_M) for i in range(int(total // PASO_M) + 1)])
    distancia_km = round(total / 1000, 1)
    tramos = []
    for desde, hasta, terreno in _tramos(alturas):
        desnivel = alturas[hasta] - alturas[desde]
        tramos.append({"desde_km": round(desde * PASO_M / 1000, 1),
                       "hasta_km": round(hasta * PASO_M / 1000, 1) if hasta < len(alturas) - 1 else distancia_km,
                       "terreno": terreno, "desnivel_m": round(desnivel),
                       "pendiente_pct": round(desnivel / ((hasta - desde) * PASO_M) * 100, 1) or 0.0})
    diferencias = [b - a for a, b in zip(alturas, alturas[1:], strict=False)]
    return {"nombre": nombre, "fuente": fuente, "distancia_km": distancia_km,
            "sube_m": round(sum(d for d in diferencias if d > 0)), "baja_m": round(-sum(d for d in diferencias if d < 0)),
            "paso_m": PASO_M, "altitudes": [round(a) for a in alturas], "tramos": tramos}


def _km(x: float) -> str:
    return f"{x:g}".replace(".", ",") if float(x).is_integer() else f"{x:.1f}".replace(".", ",")


def resumen(p: dict[str, Any]) -> str:
    """«Ruta inventada (GPX subido) · 20 km · +115 m / −115 m»: how the route is said on the screen and in the line."""
    return (f"{p['nombre']} ({FUENTES.get(p['fuente'], p['fuente'])}) · {_km(p['distancia_km'])} km · "
            f"+{p['sube_m']} m / −{p['baja_m']} m")


def perfil_para_modelo(p: dict[str, Any]) -> str:
    """What the model is told of the route attached, in the message (R-154): km, altitude and stretches, and what to
    answer with it (R-155). Never a coordinate: the profile has none (G-57)."""
    tramos = "\n".join(
        f"- km {_km(t['desde_km'])}–{_km(t['hasta_km'])} · {t['terreno']} {t['desnivel_m']:+d} m"
        + (f" ({str(abs(t['pendiente_pct'])).replace('.', ',')} %)" if t["terreno"] != "llano" else "")
        for t in p["tramos"])
    return (f"[Ruta adjunta a esta conversación: {resumen(p)}. Va solo su perfil: sus coordenadas se quedan en el PC.\n"
            f"Tramos:\n{tramos}\n"
            f"Altitud cada {p['paso_m']} m desde el km 0 (m): {', '.join(str(a) for a in p['altitudes'])}\n"
            "Si Andrés te pide la estrategia de la carrera de esta ruta, respóndele con una línea por tramo: sus km, "
            "su terreno, el ritmo o el esfuerzo y el tiempo de paso acumulado al acabarlo, que sume su objetivo; y los "
            "geles, cada uno en su km. Después propónle guardarla en el plan con proponer_cambio (accion "
            "guardar_estrategia), en la carrera de su plan que sea.]")


__all__ = ["FUENTES", "PASO_M", "SIN_ALTITUD", "Punto", "RutaMal", "SinAltitud", "nombre_gpx", "perfil",
           "perfil_para_modelo", "puntos_garmin", "puntos_gpx", "resumen"]
