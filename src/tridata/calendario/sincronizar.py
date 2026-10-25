"""The planned sessions of the plan in course, made events of «TriData» (unit 043: R2, R3, R5 · C-207..C-211, G-59).

`sincronizar` compares the sessions of the plan of the current objective from today on (`Planes.en_curso`) with the
events of «TriData», each one tied to its session by `extendedProperties.private.tridata_sesion` (`<plan>-<orden>`,
the row of `plan_sesion`, which the cards of the 041 change in place):

- a session without its event: an all-day event is created («🏃 Series 4×12' a 4:00», the blocks one per line);
- a session whose title, blocks or day changed: its event changes title, description and day. If Andrés put it at an
  hour (`dateTime`), the hour and the duration stay and only the day moves;
- an event whose session left the plan: deleted, unless its day is over (that is history);
- two events of the same session: the extra ones are deleted. Never two events of one session.

Idempotent: a second pass in a row writes nothing. Only the type, the day and the blocks go to Google: no plan name,
no incidents, no figure of heart rate (G-59). It does not touch the code of the plan: it reads its SQLite.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any

from ..plan import Planes
from . import SIN_CONECTAR, Calendario, ErrorCalendario

CLAVE = "tridata_sesion"
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


def _previstas(planes: Planes, hoy: date) -> dict[str, dict[str, Any]]:
    """`{session id: its event}` of the plan in course, today and the days to come."""
    ident = planes.en_curso(hoy)
    if ident is None:
        return {}
    with planes._connect() as conn:  # the same SQLite of `Planes`, as the cards of the 041 do
        filas = conn.execute("SELECT orden, fecha, titulo, bloques FROM plan_sesion WHERE plan_id = ? AND fecha >= ? "
                             "ORDER BY fecha, orden", (ident, hoy.isoformat())).fetchall()
    return {f"{ident}-{f['orden']}": cuerpo_del_evento(f"{ident}-{f['orden']}", {
        "fecha": f["fecha"], "titulo": f["titulo"], "bloques": json.loads(f["bloques"])}) for f in filas}


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


def sincronizar(planes: Planes, calendario: Calendario, hoy: date) -> dict[str, int]:
    """One pass: `{creados, cambiados, borrados}`. `ErrorCalendario` if Google fails (the next pass tries again) or
    without connecting (then nothing talks to Google, R6)."""
    if not calendario.conectado:
        raise ErrorCalendario(SIN_CONECTAR)
    deseadas = _previstas(planes, hoy)
    por_sesion: dict[str, list[dict]] = {}
    for evento in calendario.eventos():
        sesion = ((evento.get("extendedProperties") or {}).get("private") or {}).get(CLAVE)
        if sesion:  # an event of Andrés's own in «TriData» is not touched
            por_sesion.setdefault(sesion, []).append(evento)

    resumen = {"creados": 0, "cambiados": 0, "borrados": 0}
    actuales: dict[str, dict] = {}
    for sesion, eventos in por_sesion.items():
        if sesion in deseadas:
            actuales[sesion], sobran = eventos[0], eventos[1:]
        else:
            sobran = [e for e in eventos if _dia(e) >= hoy]
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
    return resumen


__all__ = ["CLAVE", "cuerpo_del_evento", "sincronizar"]
