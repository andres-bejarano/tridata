"""«Garmin manda» (unit 061, `mandar-sesion-reloj`): what Andrés changes in the calendar of Garmin Connect reaches the
plan on the next sync. Pure: no SQLite, no Garmin; `Planes.aplicar_garmin` writes what `cambios` says and
`SyncService.plan_desde_garmin` reads Garmin.

Each workout scheduled in Garmin (from 3 days ago to the end of the plan in course) is paired with a session:

1. by its ids (`garmin_workout_id` or `garmin_schedule_id`: it was sent from TriData or paired before). If Garmin's
   `updatedDate` is another than the one kept, Garmin wins: title, blocks, day (R1). The first time a session sent
   from TriData is seen there is no `updatedDate` kept: if Garmin's says the same workout as what was sent (the
   same title and the same steps, whatever the words), only its `updatedDate` is kept, so what the coach wrote is not
   rewritten; if not, it was changed in Garmin and Garmin wins. Moved to another day, the session moves with it.
2. else, with the session of the same day and sport: one, it is replaced (R2); several (048), the one with the same
   title; none with it, nothing is touched and those sessions say why (R2).
3. else, on that day of the plan, a new session (R3).

A session with ids that no workout of Garmin has any more is not deleted: it says «Ya no está en tu Garmin» (R5).
Rows are changed IN PLACE, so `<plan>-<orden>` (the key of Google Calendar and of the team) stays.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..nutricion.recetas import normalizar
from . import texto_sesion
from .reloj import NoTraducible, _firma, a_garmin, de_garmin

YA_NO_ESTA = "Ya no está en tu Garmin"
_DEPORTES = ((re.compile(r"\b(bici\w*|rodillo|ciclismo|btt|mtb)\b"), "bici"),
             (re.compile(r"\b(natacion|nadar|piscina|aguas abiertas|crol)\b"), "natacion"),
             (re.compile(r"\b(fuerza|gimnasio|gym|pesas)\b"), "fuerza"))
_DE_GARMIN = {"running": "carrera", "trail_running": "carrera", "treadmill_running": "carrera",
              "indoor_running": "carrera", "cycling": "bici", "road_biking": "bici", "indoor_cycling": "bici",
              "mountain_biking": "bici", "gravel_cycling": "bici", "virtual_ride": "bici", "swimming": "natacion",
              "lap_swimming": "natacion", "open_water_swimming": "natacion", "strength_training": "fuerza"}


@dataclass(frozen=True)
class Programado:
    """A workout scheduled in Garmin, already as a session: its ids, day, sport, text and Garmin's `updatedDate`."""

    programado: int
    entreno: int
    fecha: str
    deporte: str
    titulo: str
    bloques: tuple[str, ...]
    actualizado: str | None


@dataclass
class Cambios:
    """What a pass changes: `actualizar` {rowid: columns}, `anadir` [new rows] and `avisos` {rowid: warning or None}
    for every session of the window (None clears an old one)."""

    actualizar: dict[int, dict[str, Any]] = field(default_factory=dict)
    anadir: list[dict[str, Any]] = field(default_factory=list)
    avisos: dict[int, str | None] = field(default_factory=dict)


def deporte_de_sesion(titulo: str) -> str:
    """`carrera`, `bici`, `natacion` or `fuerza`, from the title (as the icon of Google Calendar is chosen)."""
    plano = normalizar(titulo)
    return next((deporte for patron, deporte in _DEPORTES if patron.search(plano)), "carrera")


def programado(item: Mapping[str, Any], detalle: Mapping[str, Any]) -> Programado:
    """One item of `GarminClient.entrenos_programados` with the detail of its workout (`GarminClient.entreno`)."""
    titulo, bloques = de_garmin(detalle)
    return Programado(programado=int(item["programado"]), entreno=int(item["entreno"]), fecha=str(item["fecha"]),
                      deporte=_DE_GARMIN.get(str(item.get("deporte") or ""), "otro"), titulo=titulo,
                      bloques=tuple(bloques), actualizado=detalle.get("updatedDate"))


def _todo(p: Programado) -> dict[str, Any]:
    bloques = list(p.bloques)
    return {"titulo": p.titulo, "bloques": bloques, "fecha": p.fecha, "garmin_workout_id": p.entreno,
            "garmin_schedule_id": p.programado, "garmin_actualizado": p.actualizado,
            "garmin_texto": texto_sesion(p.titulo, bloques)}


def _mismo(a: str, b: str) -> bool:
    return " ".join(normalizar(a).split()) == " ".join(normalizar(b).split())


def _el_mismo(titulo: str, bloques: list[str], p: Programado) -> bool:
    """Whether Garmin's says the same workout as that title and blocks: the same title, and the same blocks or the
    same steps for the watch (`a_garmin`), as «10'» and «10' suave» are."""
    if titulo != p.titulo:
        return False
    if list(bloques) == list(p.bloques):
        return True
    try:
        return _firma(a_garmin({"titulo": titulo, "bloques": bloques})) == _firma(
            a_garmin({"titulo": p.titulo, "bloques": list(p.bloques)}))
    except NoTraducible:
        return False


def _como_se_mando(s: Mapping[str, Any]) -> tuple[str, list[str]]:
    """The title and blocks that were sent to Garmin (`garmin_texto`, «título: bloque · bloque»): the session as it is
    if it did not change since, else read back from that text (the first «: » that leaves the title)."""
    titulo, bloques = str(s["titulo"]), list(s.get("bloques") or [])
    enviado = s.get("garmin_texto")
    if enviado is None or enviado == texto_sesion(titulo, bloques):
        return titulo, bloques
    antes, _, despues = str(enviado).partition(": ")
    return antes, despues.split(" · ") if despues else []


def varias(titulo: str) -> str:
    """The warning of R2: several sessions that day and sport, none with the title of Garmin's."""
    return f"En tu Garmin hay «{titulo}» este día y no sé a cuál de estas sesiones sustituye"


def cambios(sesiones: Sequence[Mapping[str, Any]], programados: Sequence[Programado]) -> Cambios:
    """What changes in the sessions of the window (`Planes.sesiones_garmin`) for the workouts scheduled in Garmin."""
    salida = Cambios(avisos={int(s["id"]): None for s in sesiones})
    por_id = {int(s["id"]): s for s in sesiones}
    emparejadas: set[int] = set()
    sueltos: list[Programado] = []

    for p in programados:  # 1. by its ids
        s = next((s for s in sesiones if int(s["id"]) not in emparejadas
                  and (s.get("garmin_workout_id") == p.entreno or s.get("garmin_schedule_id") == p.programado)), None)
        if s is None:
            sueltos.append(p)
            continue
        emparejadas.add(int(s["id"]))
        campos: dict[str, Any] = {}
        if s.get("garmin_actualizado") is None and _el_mismo(*_como_se_mando(s), p):  # as it was sent
            campos["garmin_actualizado"] = p.actualizado
        elif s.get("garmin_actualizado") is None:  # sent from TriData and changed in Garmin since
            campos = _todo(p)
        elif s.get("garmin_actualizado") != p.actualizado:  # changed in Garmin: Garmin wins (R1)
            campos = _todo(p)
        if s["fecha"] != p.fecha:
            campos["fecha"] = p.fecha
        if (s.get("garmin_workout_id"), s.get("garmin_schedule_id")) != (p.entreno, p.programado):
            campos.update(garmin_workout_id=p.entreno, garmin_schedule_id=p.programado)
        if campos:
            salida.actualizar[int(s["id"])] = campos

    for p in sueltos:  # 2. the session of that day and sport; 3. a new one
        candidatas = [s for s in sesiones if int(s["id"]) not in emparejadas and s["fecha"] == p.fecha
                      and deporte_de_sesion(str(s["titulo"])) == p.deporte]
        if len(candidatas) > 1:
            iguales = [s for s in candidatas if _mismo(str(s["titulo"]), p.titulo)]
            if len(iguales) != 1:
                for s in candidatas:
                    salida.avisos[int(s["id"])] = varias(p.titulo)
                continue
            candidatas = iguales
        if candidatas:
            ident = int(candidatas[0]["id"])
            emparejadas.add(ident)
            salida.actualizar[ident] = _todo(p)
        else:
            salida.anadir.append(_todo(p))

    for ident, s in por_id.items():  # R5: it was in Garmin and it is not any more
        if ident not in emparejadas and salida.avisos[ident] is None and (
                s.get("garmin_workout_id") is not None or s.get("garmin_schedule_id") is not None):
            salida.avisos[ident] = YA_NO_ESTA
    return salida


__all__ = ["YA_NO_ESTA", "Cambios", "Programado", "cambios", "deporte_de_sesion", "programado", "varias"]
