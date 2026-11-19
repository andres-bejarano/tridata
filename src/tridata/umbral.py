"""Unit 096 (REC-41 of `ver-fitness`, R-255..R-262, G-64): the heart-rate threshold of each sport and the proposals of
a new one. The threshold is the start of Z5; the zones come out of it with Friel's percentages.

- `zonas_desde_umbral`: the five zones of a sport from its threshold (R-257): run and swim Z2 85 %, Z3 90 %, Z4 95 %,
  Z5 100 %; ride Z2 81 %, Z3 90 %, Z4 94 %, Z5 100 %. Z1 starts at the resting HR and Z5 ends at the max, as every
  zone saved in Ajustes. Each start rounded half up; each zone ends one ppm before the next.
- `deporte_de`: the sport whose zones a type of activity uses — ride, swim (open water too), and the run's for
  everything else (walks, hikes, others). It reuses the sports of «Tus semanas» (`fitness.deporte_semana`).
- `detectar` (R-259): the effort of one session that estimates a threshold — a race (Garmin's mark) of 20 to 65 min
  (its mean HR), or a continuous stretch of 20 min or more at Z4 or above of that sport (the mean HR of the stretch,
  without its first 10 min if it lasts 30 or more). Second by second where there is detail, by laps where not.
- `Propuestas`: the table `propuesta_umbral` of the local database (created if missing). Only a difference of 3 ppm
  or more proposes (R-260); a newer session replaces the pending one of the same sport; «no» never shows it again,
  and only «sí» saves the new zones of that sport from that day (R-261, G-64) through `Ajustes.cambiar`.
- `revisar_sesion`: what the sync calls after bringing a session's laps or detail. Only sessions of the last
  `DIAS_PROPUESTA` days, and not older than the zones in force of that sport: old history is never looked through.

Nothing here touches the HR max or rest: the load, form, fatigue and freshness do not change (R-262).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .fitness import deporte_semana
from .nutricion.ajustes import CLAVE_ZONAS, Ajustes, HistorialFC, historial_fc_de
from .storage import DataStore

DEPORTES = ("carrera", "bici", "natacion")
NOMBRE = {"carrera": "carrera", "bici": "bici", "natacion": "natación"}
_DE_SEMANA = {"bici": "bici", "nadar": "natacion"}  # the sports of «Tus semanas» with zones of their own
PORCENTAJES = {"carrera": (85, 90, 95, 100), "natacion": (85, 90, 95, 100), "bici": (81, 90, 94, 100)}
DIFERENCIA_MINIMA = 3  # R-260: ppm
COMPETICION_S = (20 * 60, 65 * 60)  # R-259: a race of 20 to 65 min
TRAMO_MINIMO_S = 20 * 60
TRAMO_LARGO_S = 30 * 60  # from 30 min, without its first 10
SIN_INICIO_S = 10 * 60
BACHE_S = 30  # a dip under Z4 (or without HR) shorter than this does not cut the stretch
DIAS_PROPUESTA = 14  # only sessions of the last two weeks propose: the history is not looked through
PENDIENTE, ACEPTADA, RECHAZADA, SUSTITUIDA = "pendiente", "aceptada", "rechazada", "sustituida"
RESPUESTAS = ("si", "no")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS propuesta_umbral (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deporte TEXT NOT NULL,
    actual INTEGER NOT NULL,
    estimado INTEGER NOT NULL,
    activity_id TEXT NOT NULL,
    sesion TEXT NOT NULL,
    fecha TEXT NOT NULL,
    estado TEXT NOT NULL,
    creada TEXT NOT NULL,
    respondida TEXT
);
"""

Zonas = Sequence[Sequence[int]]


def _redondo(valor: float) -> int:
    """Half up (171.5 → 172), never Python's half to even."""
    return int(valor + 0.5)


def zonas_desde_umbral(umbral: int, deporte: str, fc_reposo: int, fc_max: int) -> list[list[int]]:
    """R-257: the five [from, to] of `deporte` with its threshold `umbral` (start of Z5)."""
    inicios = [fc_reposo] + [(pct * umbral + 50) // 100 for pct in PORCENTAJES[deporte]]
    return [[inicios[n], inicios[n + 1] - 1] for n in range(4)] + [[inicios[4], fc_max]]


def deporte_de(tipo: str | None) -> str:
    """carrera · bici · natacion: the zones a type of activity uses (R-255)."""
    return _DE_SEMANA.get(deporte_semana({"activity_type": tipo}), "carrera")


# -- R-259: the effort -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Esfuerzo:
    umbral: int
    como: str  # "competicion" | "tramo"
    segundos: float


def _segmentos_detalle(detalle: Mapping[str, list[float | None]]) -> list[tuple[float, float | None]]:
    t, pulso = detalle["t"], detalle["pulso"]
    puntos = [(ti, p) for ti, p in zip(t, pulso, strict=True) if ti is not None]
    segmentos = []
    for i, (ti, p) in enumerate(puntos):
        paso = puntos[i + 1][0] - ti if i + 1 < len(puntos) else 1.0
        if paso > 0:
            segmentos.append((paso, p or None))
    return segmentos


def _segmentos_vueltas(laps: list[dict]) -> list[tuple[float, float | None]]:
    return [(float(v.get("duration_seconds") or 0), v.get("avg_hr") or None) for v in laps]


def _tramos(segmentos: list[tuple[float, float | None]], z4: int) -> list[list[tuple[float, float | None]]]:
    """The continuous stretches at Z4 or above; a dip shorter than `BACHE_S` stays inside the stretch."""
    tramos: list[list[tuple[float, float | None]]] = []
    actual: list[tuple[float, float | None]] = []
    bache: list[tuple[float, float | None]] = []
    bache_s = 0.0
    for segundos, pulso in segmentos:
        if segundos <= 0:
            continue
        if pulso is not None and pulso >= z4:
            actual.extend(bache)
            bache, bache_s = [], 0.0
            actual.append((segundos, pulso))
        elif actual:
            bache.append((segundos, pulso))
            bache_s += segundos
            if bache_s >= BACHE_S:
                tramos.append(actual)
                actual, bache, bache_s = [], [], 0.0
    if actual:
        tramos.append(actual)
    return tramos


def _sin_inicio(tramo: list[tuple[float, float | None]], quitar: float) -> list[tuple[float, float | None]]:
    resto = []
    for segundos, pulso in tramo:
        if quitar >= segundos:
            quitar -= segundos
            continue
        resto.append((segundos - quitar, pulso))
        quitar = 0.0
    return resto


def _media(tramo: list[tuple[float, float | None]]) -> float | None:
    con = [(s, p) for s, p in tramo if p is not None]
    total = sum(s for s, _ in con)
    return sum(s * p for s, p in con) / total if total else None


def detectar(actividad: Mapping[str, Any], laps: list[dict], detalle: Mapping[str, list[float | None]] | None,
             zonas: Zonas | None) -> Esfuerzo | None:
    """R-259: the effort of a session (with the zones of its sport that day), or None."""
    duracion = float(actividad.get("duration_seconds") or 0)
    pulso = actividad.get("avg_hr") or None
    if actividad.get("event_type") == "race" and pulso and COMPETICION_S[0] <= duracion <= COMPETICION_S[1]:
        return Esfuerzo(umbral=_redondo(pulso), como="competicion", segundos=duracion)
    if not zonas:
        return None
    hay_detalle = detalle is not None and any(detalle.get("pulso") or [])
    segmentos = _segmentos_detalle(detalle) if hay_detalle and detalle is not None else _segmentos_vueltas(laps)
    tramos = _tramos(segmentos, zonas[3][0])
    if not tramos:
        return None
    tramo = max(tramos, key=lambda t: sum(s for s, _ in t))
    segundos = sum(s for s, _ in tramo)
    if segundos < TRAMO_MINIMO_S:
        return None
    media = _media(_sin_inicio(tramo, SIN_INICIO_S) if segundos >= TRAMO_LARGO_S else tramo)
    return None if media is None else Esfuerzo(umbral=_redondo(media), como="tramo", segundos=segundos)


# -- R-260, R-261: the proposals ---------------------------------------------------------------------------------

class Propuestas:
    """The proposals of a new threshold, in the local database (`propuesta_umbral`)."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def registrar(self, deporte: str, actual: int, estimado: int, actividad_id: str, sesion: str,
                  fecha: date) -> dict | None:
        """A proposal if `estimado` differs `DIFERENCIA_MINIMA` or more from `actual`; None otherwise, if that session
        was already answered, or if a newer session of that sport is already pending."""
        if abs(estimado - actual) < DIFERENCIA_MINIMA:
            return None
        ahora = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            previa = conn.execute("SELECT id, estado FROM propuesta_umbral WHERE activity_id = ? AND deporte = ?",
                                  (actividad_id, deporte)).fetchone()
            if previa is not None:
                if previa["estado"] != PENDIENTE:
                    return None  # «no» (or «sí») is not asked again for the same session
                conn.execute("UPDATE propuesta_umbral SET actual = ?, estimado = ? WHERE id = ?",
                             (actual, estimado, previa["id"]))
                nueva = previa["id"]
            else:
                pendiente = conn.execute("SELECT id, fecha FROM propuesta_umbral WHERE deporte = ? AND estado = ?",
                                         (deporte, PENDIENTE)).fetchone()
                if pendiente is not None and pendiente["fecha"] > fecha.isoformat():
                    return None  # an older session never replaces a newer one
                if pendiente is not None:
                    conn.execute("UPDATE propuesta_umbral SET estado = ?, respondida = ? WHERE id = ?",
                                 (SUSTITUIDA, ahora, pendiente["id"]))
                cursor = conn.execute(
                    "INSERT INTO propuesta_umbral (deporte, actual, estimado, activity_id, sesion, fecha, estado, "
                    "creada) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (deporte, actual, estimado, actividad_id, sesion, fecha.isoformat(), PENDIENTE, ahora))
                nueva = cursor.lastrowid
        return self._una(nueva)

    def _una(self, id_propuesta: int | None) -> dict | None:
        with self._connect() as conn:
            fila = conn.execute("SELECT * FROM propuesta_umbral WHERE id = ?", (id_propuesta,)).fetchone()
        return _como_dict(fila) if fila else None

    def pendientes(self) -> list[dict]:
        with self._connect() as conn:
            filas = conn.execute("SELECT * FROM propuesta_umbral WHERE estado = ? ORDER BY fecha, id",
                                 (PENDIENTE,)).fetchall()
        return [_como_dict(f) for f in filas]

    def responder(self, id_propuesta: int, respuesta: Any, ajustes: Ajustes, hoy: date) -> dict:
        """R-261: «si» saves the zones of that sport from `hoy` out of the estimated threshold (with the HR max and
        rest of `hoy`); «no» only marks it. `ValueError`: another answer, or no HR confirmed; `KeyError`: no such
        pending proposal. Nothing changes on an error."""
        if respuesta not in RESPUESTAS:
            raise ValueError("La respuesta es «si» o «no»")
        propuesta = next((p for p in self.pendientes() if p["id"] == id_propuesta), None)
        if propuesta is None:
            raise KeyError(id_propuesta)
        if respuesta == "si":
            pulso = ajustes.historial_fc().vigente(hoy)
            if pulso is None:
                raise ValueError("Confirma antes tu FC máxima y en reposo en Ajustes")
            zonas = zonas_desde_umbral(propuesta["estimado"], propuesta["deporte"], pulso.fc_reposo, pulso.fc_max)
            ajustes.cambiar({CLAVE_ZONAS[propuesta["deporte"]]: zonas}, desde=hoy)
        with self._connect() as conn:
            conn.execute("UPDATE propuesta_umbral SET estado = ?, respondida = ? WHERE id = ?",
                         (ACEPTADA if respuesta == "si" else RECHAZADA, datetime.now().isoformat(timespec="seconds"),
                          id_propuesta))
        return {**propuesta, "estado": ACEPTADA if respuesta == "si" else RECHAZADA}


def _como_dict(fila: sqlite3.Row) -> dict:
    return {"id": fila["id"], "deporte": fila["deporte"], "actual": fila["actual"], "estimado": fila["estimado"],
            "actividad_id": fila["activity_id"], "sesion": fila["sesion"], "fecha": fila["fecha"],
            "estado": fila["estado"]}


def para_ver(propuestas: list[dict], historial: HistorialFC, hoy: date) -> list[dict]:
    """What Fitness and Ajustes show of the pending ones (R-260): «tu umbral de <deporte> pasa de X a Y (<sesión,
    fecha>), ¿actualizo desde hoy?» and the zones that would come out. X is the threshold in force today; one that
    no longer differs 3 ppm (the zones were changed by hand meanwhile) is not shown."""
    pulso = historial.vigente(hoy)
    if pulso is None:
        return []
    vistas = []
    for p in propuestas:
        zonas, _ = pulso.zonas_de(p["deporte"])
        if not zonas:
            continue
        actual = zonas[-1][0]
        if abs(p["estimado"] - actual) < DIFERENCIA_MINIMA:
            continue
        dia = date.fromisoformat(p["fecha"])
        texto = (f"tu umbral de {NOMBRE[p['deporte']]} pasa de {actual} a {p['estimado']} "
                 f"({p['sesion'] or 'sesión'}, {dia.day}/{dia.month}), ¿actualizo desde hoy?")
        vistas.append({**p, "actual": actual, "nombre": NOMBRE[p["deporte"]], "texto": texto,
                       "zonas": zonas_desde_umbral(p["estimado"], p["deporte"], pulso.fc_reposo, pulso.fc_max)})
    return vistas


def revisar_sesion(store: DataStore, actividad_id: str, hoy: date) -> dict | None:
    """R-259, R-260: looks for an effort in one session just brought and, if it moves the threshold of its sport
    `DIFERENCIA_MINIMA` or more, leaves a pending proposal. None if nothing is proposed."""
    a = store.get_activity(actividad_id)
    if a is None:
        return None
    dia = date.fromisoformat(a["activity_date"])
    if not hoy - timedelta(days=DIAS_PROPUESTA) <= dia <= hoy:
        return None
    historial = historial_fc_de(store.db_path)
    del_dia, de_hoy = historial.vigente(dia), historial.vigente(hoy)
    if del_dia is None or de_hoy is None:
        return None
    deporte = deporte_de(a.get("activity_type"))
    zonas_hoy, estimadas = de_hoy.zonas_de(deporte)
    if not zonas_hoy:
        return None
    desde = de_hoy.desde_zonas.get("carrera" if estimadas else deporte)
    if desde is not None and dia < desde:
        return None  # an effort before the zones in force says nothing new about them
    esfuerzo = detectar(a, store.get_laps(actividad_id), store.leer_detalle(actividad_id), del_dia.zonas_de(deporte)[0])
    if esfuerzo is None:
        return None
    return Propuestas(store.db_path).registrar(deporte, zonas_hoy[-1][0], esfuerzo.umbral, actividad_id,
                                               a.get("name") or "", dia)
