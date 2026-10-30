"""The planned sessions of the plan in course, made events of «TriData» (unit 043: R2, R3, R5 · C-207..C-211, G-59),
and the trainings done in Garmin (unit 054: R1-R6, R8).

`sincronizar` compares every session of the plan of the current objective, the past days too (`Planes.en_curso`;
unit 054, R5), with the events of «TriData», each one tied to its session by `extendedProperties.private.tridata_sesion`
(`<plan>-<orden>`, the row of `plan_sesion`, which the cards of the 041 change in place):

- a session without its event: an all-day event is created («🏃 Series 4×12' a 4:00», the blocks one per line);
- a session whose title, blocks or day changed: its event changes title, description and day. If Andrés put it at an
  hour (`dateTime`), the hour and the duration stay and only the day moves;
- an event whose session left the plan in course: deleted, whatever its day; one of another plan: deleted only if
  its day is today or to come (a past one is history);
- two events of the same session: the extra ones are deleted. Never two events of one session.

Unit 054: each activity of the `DataStore` since `DESDE_HECHOS` is one event tied by `tridata_actividad` (its
`activity_id`): «✅ 🏃 Carrera · 52' · 10,2 km», at its hour and lasting what Garmin said (all day while it has no
hour), «Hecho según Garmin» as description. It changes when the activity changes and is deleted when it leaves the
base. The sessions and the trainings are crossed by their own key: one never changes or deletes the other (R6).

Idempotent: a second pass in a row writes nothing. Only the type, the day and the blocks go to Google: no plan name,
no incidents, no figure of heart rate (G-59); of a training, only its sport, hour, duration and distance: no heart
rate, kcal, load, pace, training effect, name of the activity or place (G-59 widened, R2). It does not touch the code
of the plan: it reads its SQLite.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..plan import Planes
from ..storage import DataStore
from ..sync import CYCLING_TYPES, GYM_TYPES, RUNNING_TYPES, SWIMMING_TYPES
from . import DESDE_HECHOS, SIN_CONECTAR, Calendario, ErrorCalendario

CLAVE = "tridata_sesion"
CLAVE_HECHO = "tridata_actividad"  # unit 054: the `activity_id` of a training done
MINIMO_BICI_S = 50 * 60  # unit 056: a shorter ride is a commute, not a training: it does not go to the calendar
HECHO = "Hecho según Garmin"
_DEPORTES = ((RUNNING_TYPES, "🏃", "Carrera"), (CYCLING_TYPES, "🚴", "Bici"), (SWIMMING_TYPES, "🏊", "Natación"),
             (GYM_TYPES, "🏋️", "Fuerza"))
_ICONOS = (
    (re.compile(r"\b(bici|rodillo|ciclismo|btt|mtb|carretera en bici)\b"), "🚴"),
    (re.compile(r"\b(natacion|nadar|piscina|aguas abiertas|crol)\b"), "🏊"),
    (re.compile(r"\b(fuerza|gimnasio|gym|pesas)\b"), "🏋️"),
)
# a heart-rate figure («a 135-145 ppm», «(FC < 172 lpm)»), with its «a» and its brackets: never goes to Google (G-59)
_PULSO = re.compile(r"\s*(?:\ba\s+)?\(?\s*(?:FC\s*)?(?:[<>≤≥]=?\s*)?\d{2,3}(?:\s*[-–]\s*\d{2,3})?\s*"
                    r"(?:ppm|lpm|bpm|pulsaciones)\b\s*\)?", re.IGNORECASE)
_SIN_FC = re.compile(r"\s*\(?\bFC\b[^,;)]*\)?", re.IGNORECASE)


def _plano(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sin_tildes.lower()


def _icono(titulo: str) -> str:
    plano = _plano(titulo)
    return next((icono for patron, icono in _ICONOS if patron.search(plano)), "🏃")


def _sin_pulso(texto: str) -> str:
    limpio = _SIN_FC.sub("", _PULSO.sub("", texto))
    return " ".join(limpio.split()).strip(" ,;·-")


def cuerpo_del_evento(sesion_id: str, sesion: dict[str, Any]) -> dict[str, Any]:
    """The event of one session: its type in the title, its day (all day) and its blocks. Nothing else (R5)."""
    dia = date.fromisoformat(sesion["fecha"])
    titulo = _sin_pulso(sesion["titulo"]) or sesion["titulo"]
    bloques = [b for b in (_sin_pulso(b) for b in sesion.get("bloques") or []) if b]
    return {"summary": f"{_icono(titulo)} {titulo}", "description": "\n".join(bloques),
            "start": {"date": dia.isoformat()}, "end": {"date": (dia + timedelta(days=1)).isoformat()},
            "extendedProperties": {"private": {CLAVE: sesion_id}}}


def _previstas(planes: Planes, ident: int | None) -> dict[str, dict[str, Any]]:
    """`{session id: its event}` of the plan in course, every day of it (unit 054, R5)."""
    if ident is None:
        return {}
    with planes._connect() as conn:  # the same SQLite of `Planes`, as the cards of the 041 do
        filas = conn.execute("SELECT orden, fecha, titulo, bloques FROM plan_sesion WHERE plan_id = ? "
                             "ORDER BY fecha, orden", (ident,)).fetchall()
    return {f"{ident}-{f['orden']}": cuerpo_del_evento(f"{ident}-{f['orden']}", {
        "fecha": f["fecha"], "titulo": f["titulo"], "bloques": json.loads(f["bloques"])}) for f in filas}


# -- unit 054: the trainings done ---------------------------------------------------------------------------------------

def zona_local() -> str | None:
    """The IANA zone of this PC (`TZ`, or where `/etc/localtime` points), or None if it cannot be told."""
    candidatas = [os.environ.get("TZ", "").lstrip(":")]
    try:
        destino = str(Path("/etc/localtime").resolve())
        candidatas.append(destino.split("zoneinfo/", 1)[1] if "zoneinfo/" in destino else "")
    except OSError:
        pass
    for nombre in candidatas:
        try:
            if nombre:
                ZoneInfo(nombre)
                return nombre
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return None


def _distancia(metros: float, natacion: bool) -> str:
    if natacion:
        return f"{round(metros)} m"
    return f"{metros / 1000:.1f}".replace(".", ",") + " km"


def cuerpo_del_hecho(actividad: dict[str, Any], zona: str | None) -> dict[str, Any]:
    """The event of one training done: «✅ 🏃 Carrera · 52' · 10,2 km» at its hour and lasting what it lasted, in
    `zona` (the one of this PC); all day while it has no hour. Nothing else of the activity (R1, R2)."""
    tipo = actividad.get("activity_type")
    icono, deporte = next(((i, d) for tipos, i, d in _DEPORTES if tipo in tipos), ("🏃", "Entreno"))
    segundos = float(actividad.get("duration_seconds") or 0)
    metros = float(actividad.get("distance_meters") or 0)
    partes = [f"✅ {icono} {deporte}", f"{round(segundos / 60)}'"]
    if metros > 0:
        partes.append(_distancia(metros, tipo in SWIMMING_TYPES))
    cuerpo: dict[str, Any] = {"summary": " · ".join(partes), "description": HECHO}
    hora = actividad.get("start_time_local")
    if hora:
        inicio = datetime.fromisoformat(str(hora))
        inicio = inicio.replace(tzinfo=ZoneInfo(zona)) if zona else inicio.astimezone()
        fin = inicio + timedelta(seconds=round(segundos))
        extra = {"timeZone": zona} if zona else {}
        cuerpo["start"] = {"dateTime": inicio.isoformat(), **extra}
        cuerpo["end"] = {"dateTime": fin.isoformat(), **extra}
    else:
        dia = date.fromisoformat(str(actividad["activity_date"]))
        cuerpo["start"] = {"date": dia.isoformat()}
        cuerpo["end"] = {"date": (dia + timedelta(days=1)).isoformat()}
    cuerpo["extendedProperties"] = {"private": {CLAVE_HECHO: str(actividad["activity_id"])}}
    return cuerpo


def _hechos(store: DataStore, hoy: date, zona: str | None) -> dict[str, dict[str, Any]]:
    """`{activity id: its event}` of every activity since `DESDE_HECHOS` (R1, R3), but the commutes: a ride of less
    than `MINIMO_BICI_S` (unit 056). One already sent leaves the calendar like any activity no longer wanted."""
    return {str(a["activity_id"]): cuerpo_del_hecho(a, zona) for a in store.activities_between(DESDE_HECHOS, hoy)
            if not _desplazamiento(a)}


def _dia_y_deporte(cuerpo: dict[str, Any]) -> tuple[str, str]:
    """The day and the sport (its icon, the second word of «✅ 🏃 …», the first of «🏃 Series …») of an event."""
    inicio = cuerpo["start"]
    palabras = cuerpo["summary"].split()
    return (inicio.get("date") or inicio["dateTime"][:10]), palabras[1 if palabras[0] == "✅" else 0]


def _sin_las_entrenadas(deseadas: dict[str, dict], hechos: dict[str, dict]) -> dict[str, dict]:
    """Unit 056 (R3): a session of a day that already has a training done of its sport is not wanted: that day
    shows only the training. A session of a sport not done that day stays."""
    entrenado = {_dia_y_deporte(c) for c in hechos.values()}
    return {sesion: c for sesion, c in deseadas.items() if _dia_y_deporte(c) not in entrenado}


def _desplazamiento(actividad: dict[str, Any]) -> bool:
    return (actividad.get("activity_type") in CYCLING_TYPES
            and float(actividad.get("duration_seconds") or 0) < MINIMO_BICI_S)


def _mismo_momento(actual: dict, deseado: dict) -> bool:
    """Whether a start (or an end) of Google says the same as the wanted one: the same day, or the same instant
    whatever the offset Google writes it with."""
    if "date" in deseado:
        return actual.get("date") == deseado["date"] and not actual.get("dateTime")
    if not actual.get("dateTime"):
        return False
    return _fecha_hora(actual["dateTime"]) == _fecha_hora(deseado["dateTime"])


def _cambio_hecho(evento: dict, cuerpo: dict) -> dict:
    """What to patch so the event says what the activity says now. When it goes from all day to its hour (or back),
    the other field is cleared (`None`), as a patch of Google needs."""
    cambio = {k: cuerpo[k] for k in ("summary", "description") if evento.get(k) != cuerpo[k]}
    for lado in ("start", "end"):
        actual = evento.get(lado) or {}
        if not _mismo_momento(actual, cuerpo[lado]):
            otro = {"dateTime": None, "timeZone": None} if "date" in cuerpo[lado] else {"date": None}
            cambio[lado] = {**{k: v for k, v in otro.items() if k in actual}, **cuerpo[lado]}
    return cambio


def _fecha_hora(texto: str) -> datetime:
    return datetime.fromisoformat(texto[:-1] + "+00:00" if texto.endswith("Z") else texto)


def _dia(evento: dict) -> date:
    inicio = evento.get("start") or {}
    if "date" in inicio:
        return date.fromisoformat(inicio["date"])
    return _fecha_hora(inicio["dateTime"]).date()


def _cambio(evento: dict, cuerpo: dict) -> dict:
    """What to patch so the event says what the session says now, keeping the hour Andrés gave it (R3)."""
    cambio = {k: cuerpo[k] for k in ("summary", "description") if evento.get(k) != cuerpo[k]}
    dia = date.fromisoformat(cuerpo["start"]["date"])
    inicio, fin = evento.get("start") or {}, evento.get("end") or {}
    if "dateTime" in inicio and "dateTime" in fin:
        desde, hasta = _fecha_hora(inicio["dateTime"]), _fecha_hora(fin["dateTime"])
        if desde.date() != dia:
            nuevo = datetime.combine(dia, desde.timetz())
            cambio["start"] = {**inicio, "dateTime": nuevo.isoformat()}
            cambio["end"] = {**fin, "dateTime": (nuevo + (hasta - desde)).isoformat()}
    elif inicio != cuerpo["start"] or fin != cuerpo["end"]:
        cambio["start"], cambio["end"] = cuerpo["start"], cuerpo["end"]
    return cambio


def _del_plan(sesion: str, ident: int | None) -> bool:
    return ident is not None and sesion.split("-", 1)[0] == str(ident)


def sincronizar(planes: Planes, calendario: Calendario, hoy: date, store: DataStore | None = None, *,
                zona: str | None = None) -> dict[str, int]:
    """One pass: `{creados, cambiados, borrados}`. `ErrorCalendario` if Google fails (the next pass tries again: what
    was already made stays and is found by its key, R8) or without connecting (then nothing talks to Google, R6).

    Unit 054: with `store`, the trainings done since `DESDE_HECHOS` too, at their hour in `zona` (by default the one
    of this PC); without it, the events of trainings are not touched."""
    if not calendario.conectado:
        raise ErrorCalendario(SIN_CONECTAR)
    ident = planes.en_curso(hoy)
    deseadas = _previstas(planes, ident)
    hechos = _hechos(store, hoy, zona or zona_local()) if store is not None else None
    if hechos:
        deseadas = _sin_las_entrenadas(deseadas, hechos)
    calendario.asegurar()  # «TriData» is still there (Andrés may have deleted it: then a new one)
    por_sesion: dict[str, list[dict]] = {}
    por_actividad: dict[str, list[dict]] = {}
    for evento in calendario.eventos():
        privado = (evento.get("extendedProperties") or {}).get("private") or {}
        if privado.get(CLAVE):  # an event of Andrés's own in «TriData» is not touched
            por_sesion.setdefault(privado[CLAVE], []).append(evento)
        elif privado.get(CLAVE_HECHO):
            por_actividad.setdefault(privado[CLAVE_HECHO], []).append(evento)

    resumen = {"creados": 0, "cambiados": 0, "borrados": 0}
    actuales: dict[str, dict] = {}
    for sesion, eventos in por_sesion.items():
        if sesion in deseadas:
            actuales[sesion], sobran = eventos[0], eventos[1:]
        elif _del_plan(sesion, ident):  # it left the plan in course: whatever its day (R5)
            sobran = eventos
        else:  # another plan: a past day is history
            sobran = [e for e in eventos if _dia(e) >= hoy]
        for evento in sobran:
            calendario.borrar(evento["id"])
            resumen["borrados"] += 1

    hechos_actuales: dict[str, dict] = {}
    if hechos is not None:
        for actividad, eventos in por_actividad.items():
            if actividad in hechos:
                hechos_actuales[actividad], sobran = eventos[0], eventos[1:]
            else:
                sobran = eventos
            for evento in sobran:
                calendario.borrar(evento["id"])
                resumen["borrados"] += 1

    ids: dict[str, str] = {}
    for sesion, cuerpo in deseadas.items():
        actual = actuales.get(sesion)
        if actual is None:
            ids[sesion] = str(calendario.crear(cuerpo)["id"])
            resumen["creados"] += 1
            continue
        ids[sesion] = str(actual["id"])
        cambio = _cambio(actual, cuerpo)
        if cambio:
            calendario.cambiar(actual["id"], cambio)
            resumen["cambiados"] += 1
    calendario.recordar(ids)

    for actividad, cuerpo in (hechos or {}).items():
        actual = hechos_actuales.get(actividad)
        if actual is None:
            calendario.crear(cuerpo)
            resumen["creados"] += 1
            continue
        cambio = _cambio_hecho(actual, cuerpo)
        if cambio:
            calendario.cambiar(actual["id"], cambio)
            resumen["cambiados"] += 1
    return resumen


__all__ = ["CLAVE", "CLAVE_HECHO", "cuerpo_del_evento", "cuerpo_del_hecho", "sincronizar", "zona_local"]
