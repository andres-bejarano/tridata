"""The training plans of Fitness › Plan (unit 040, REC-22 of `llevar-plan`), kept only in the local SQLite (G-53).

One plan per main objective («Road to Valencia 26», «Road to Zarautz 27»…), with the sections («apartados») of the
.md it came from, in its order, and every row pointing at its section, so the plan is written back with the same
sections (`plan/markdown.py`):

- `plan_carrera`: the races and objectives, with their date, target, state or result and whether it is the main one.
- `plan_semana`: each week (number, from, to, planned time, key sessions). What was done is NOT copied here: it is
  computed from Garmin when it is shown (`plan/semanas.py`, G-54). Only what Andrés accepted from the plan (Strava)
  when importing is kept, «hecho según el plan» (`hecho_plan_s`, `hecho_plan_km`), and it wins.
- `plan_sesion`: a planned session of one day, its blocks as lines of text («15' a 5:45»).
- `plan_nutricion`: the race nutrition (product and its schedule).
- `plan_incidencia`: the incidents of the .md, read only (REC-24 will make them live).
- `plan_nota`: what fits nowhere, with its text as it was (`tipo` "nota"), and the lines of the comparison with
  another year (`tipo` "comparativa").

The plan of the current objective is the one of the next main race; with all of them over, the one of the last.
Nothing here logs a text of the plan (R9).

Unit 042 (REC-26): a session sent to the watch keeps its `garmin_workout_id` and `garmin_schedule_id` (to replace it,
R-160), when it was sent (`garmin_enviada`) and what was sent (`garmin_texto`, «título: bloque · bloque»), so the plan
says «en tu Garmin» or that it changed since. A session is addressed by its `rowid` (`sesion`, `sesiones_reloj`).
`garmin()` is the logged-in Garmin client (`plan.reloj.cliente_garmin`, looked up when it is called).
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    apartados TEXT NOT NULL,
    creado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_carrera (
    plan_id INTEGER NOT NULL REFERENCES plan(id),
    orden INTEGER NOT NULL,
    apartado INTEGER NOT NULL,
    nombre TEXT NOT NULL,
    fecha TEXT,
    objetivo TEXT NOT NULL,
    estado TEXT NOT NULL,
    principal INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_semana (
    plan_id INTEGER NOT NULL REFERENCES plan(id),
    orden INTEGER NOT NULL,
    apartado INTEGER NOT NULL,
    n INTEGER NOT NULL,
    desde TEXT NOT NULL,
    hasta TEXT NOT NULL,
    previsto_s INTEGER,
    clave TEXT NOT NULL,
    hecho_plan_s INTEGER,
    hecho_plan_km REAL
);
CREATE TABLE IF NOT EXISTS plan_sesion (
    plan_id INTEGER NOT NULL REFERENCES plan(id),
    orden INTEGER NOT NULL,
    apartado INTEGER NOT NULL,
    fecha TEXT NOT NULL,
    titulo TEXT NOT NULL,
    bloques TEXT NOT NULL,
    clave INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_nutricion (
    plan_id INTEGER NOT NULL REFERENCES plan(id),
    orden INTEGER NOT NULL,
    apartado INTEGER NOT NULL,
    producto TEXT NOT NULL,
    pauta TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_incidencia (
    plan_id INTEGER NOT NULL REFERENCES plan(id),
    orden INTEGER NOT NULL,
    apartado INTEGER NOT NULL,
    fecha TEXT,
    texto TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_nota (
    plan_id INTEGER NOT NULL REFERENCES plan(id),
    orden INTEGER NOT NULL,
    apartado INTEGER NOT NULL,
    tipo TEXT NOT NULL,
    texto TEXT NOT NULL
);
"""

# table -> its columns after (plan_id, orden), in the order of the dicts `leer` gives
_FILAS: dict[str, tuple[str, tuple[str, ...]]] = {
    "carreras": ("plan_carrera", ("apartado", "nombre", "fecha", "objetivo", "estado", "principal")),
    "semanas": ("plan_semana", ("apartado", "n", "desde", "hasta", "previsto_s", "clave", "hecho_plan_s",
                                "hecho_plan_km")),
    "sesiones": ("plan_sesion", ("apartado", "fecha", "titulo", "bloques", "clave")),
    "nutricion": ("plan_nutricion", ("apartado", "producto", "pauta")),
    "incidencias": ("plan_incidencia", ("apartado", "fecha", "texto")),
    "notas": ("plan_nota", ("apartado", "tipo", "texto")),
}
_BOOLEANOS = {("plan_carrera", "principal"), ("plan_sesion", "clave")}
# unit 042: the columns of a session sent to the watch, added to an older base without them
_RELOJ = (("garmin_workout_id", "INTEGER"), ("garmin_schedule_id", "INTEGER"), ("garmin_enviada", "TEXT"),
          ("garmin_texto", "TEXT"))


def texto_sesion(titulo: str, bloques: list[str]) -> str:
    """«título: bloque · bloque», how a session is said (and what was sent to the watch)."""
    return f"{titulo}: {' · '.join(bloques)}" if bloques else titulo


def _a_la_base(tabla: str, columna: str, valor: Any) -> Any:
    if columna == "bloques":
        return json.dumps(list(valor), ensure_ascii=False)
    if (tabla, columna) in _BOOLEANOS:
        return int(bool(valor))
    return valor


def _de_la_base(tabla: str, columna: str, valor: Any) -> Any:
    if columna == "bloques":
        return json.loads(valor)
    if (tabla, columna) in _BOOLEANOS:
        return bool(valor)
    return valor


class Planes:
    """The plans in the SQLite of `db_path` (the same one as the rest of the app)."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            tiene = {f["name"] for f in conn.execute("PRAGMA table_info(plan_sesion)")}
            for columna, tipo in _RELOJ:
                if columna not in tiene:
                    conn.execute(f"ALTER TABLE plan_sesion ADD COLUMN {columna} {tipo}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def crear(self, datos: dict[str, Any], ahora: datetime) -> int:
        """Saves a whole plan (`nombre`, `apartados` and its rows, as `leer` gives them) and returns its id."""
        with self._connect() as conn:
            cursor = conn.execute("INSERT INTO plan (nombre, apartados, creado) VALUES (?, ?, ?)",
                                  (datos["nombre"], json.dumps(datos["apartados"], ensure_ascii=False),
                                   ahora.isoformat(timespec="seconds")))
            ident = int(cursor.lastrowid or 0)
            for clave, (tabla, columnas) in _FILAS.items():
                marcas = ", ".join("?" * (len(columnas) + 2))
                conn.executemany(
                    f"INSERT INTO {tabla} (plan_id, orden, {', '.join(columnas)}) VALUES ({marcas})",  # noqa: S608
                    [(ident, orden, *(_a_la_base(tabla, c, fila.get(c)) for c in columnas))
                     for orden, fila in enumerate(datos.get(clave, []))])
        return ident

    def leer(self, ident: int) -> dict[str, Any] | None:
        """The whole plan, or None if there is no plan with that id."""
        with self._connect() as conn:
            fila = conn.execute("SELECT id, nombre, apartados FROM plan WHERE id = ?", (ident,)).fetchone()
            if fila is None:
                return None
            plan: dict[str, Any] = {"id": fila["id"], "nombre": fila["nombre"],
                                    "apartados": json.loads(fila["apartados"])}
            for clave, (tabla, columnas) in _FILAS.items():
                filas = conn.execute(f"SELECT {', '.join(columnas)} FROM {tabla} WHERE plan_id = ? "  # noqa: S608
                                     "ORDER BY orden", (ident,)).fetchall()
                plan[clave] = [{c: _de_la_base(tabla, c, f[c]) for c in columnas} for f in filas]
        return plan

    def lista(self) -> list[dict[str, Any]]:
        """`[{id, nombre, principal}]`, oldest first; `principal` the date (ISO) of its main race, or None."""
        with self._connect() as conn:
            filas = conn.execute(
                "SELECT p.id, p.nombre, (SELECT MAX(c.fecha) FROM plan_carrera c WHERE c.plan_id = p.id "
                "AND c.principal = 1) AS principal FROM plan p ORDER BY p.id").fetchall()
        return [{"id": f["id"], "nombre": f["nombre"], "principal": f["principal"]} for f in filas]

    def en_curso(self, hoy: date) -> int | None:
        """The plan of the next main race (R8); all of them over, the one whose race was last; None without plans."""
        planes = self.lista()
        if not planes:
            return None
        hoy_iso = hoy.isoformat()
        proximos = [p for p in planes if p["principal"] and p["principal"] >= hoy_iso]
        if proximos:
            return int(min(proximos, key=lambda p: p["principal"])["id"])
        return int(max(planes, key=lambda p: (p["principal"] or "", p["id"]))["id"])

    # -- unit 042: the sessions sent to the watch ----------------------------------------------------------------

    def sesion(self, ident: int) -> dict[str, Any] | None:
        """One session by its id, with its plan and what was sent to the watch; None if it is not there."""
        with self._connect() as conn:
            f = conn.execute("SELECT rowid AS id, * FROM plan_sesion WHERE rowid = ?", (ident,)).fetchone()
        if f is None:
            return None
        return {"id": f["id"], "plan_id": f["plan_id"], "fecha": f["fecha"], "titulo": f["titulo"],
                "bloques": json.loads(f["bloques"]), "clave": bool(f["clave"]),
                **{columna: f[columna] for columna, _ in _RELOJ}}

    def sesiones_reloj(self, plan_id: int) -> list[dict[str, Any]]:
        """`[{id, fecha, titulo, estado, enviada}]` of the sessions of a plan, in the order of `leer`: `estado` is
        "al_dia" (in the watch as it is now), "cambiada" (changed since it was sent) or None (never sent)."""
        with self._connect() as conn:
            filas = conn.execute("SELECT rowid AS id, * FROM plan_sesion WHERE plan_id = ? ORDER BY orden",
                                 (plan_id,)).fetchall()
        salida = []
        for f in filas:
            estado = None
            if f["garmin_workout_id"] is not None:
                ahora = texto_sesion(f["titulo"], json.loads(f["bloques"]))
                estado = "al_dia" if f["garmin_texto"] == ahora else "cambiada"
            salida.append({"id": f["id"], "fecha": f["fecha"], "titulo": f["titulo"], "estado": estado,
                           "enviada": f["garmin_enviada"] if estado else None})
        return salida

    def marcar_garmin(self, ident: int, workout_id: int | None, schedule_id: int | None,
                      enviada: datetime | None = None, texto: str | None = None) -> None:
        """What is in Garmin of that session now (all None: nothing)."""
        with self._connect() as conn:
            conn.execute("UPDATE plan_sesion SET garmin_workout_id = ?, garmin_schedule_id = ?, garmin_enviada = ?, "
                         "garmin_texto = ? WHERE rowid = ?",
                         (workout_id, schedule_id, enviada.isoformat(timespec="seconds") if enviada else None,
                          texto, ident))

    def garmin(self) -> Any:
        """The Garmin client to send a session with (logged in only now, never when the app starts)."""
        from . import reloj

        return reloj.cliente_garmin()


__all__ = ["Planes", "texto_sesion"]
