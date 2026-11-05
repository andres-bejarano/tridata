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
- `plan_incidencia`: the incidents, the ones of the .md (read only) and, since unit 044, the live ones.
- `plan_nota`: what fits nowhere, with its text as it was (`tipo` "nota"), and the lines of the comparison with
  another year (`tipo` "comparativa").

The plan of the current objective is the one of the next main race; with all of them over, the one of the last.
Nothing here logs a text of the plan (R9).

Unit 042 (REC-26): a session sent to the watch keeps its `garmin_workout_id` and `garmin_schedule_id` (to replace it,
R-160), when it was sent (`garmin_enviada`) and what was sent (`garmin_texto`, «título: bloque · bloque»), so the plan
says «en tu Garmin» or that it changed since. A session is addressed by its `rowid` (`sesion`, `sesiones_reloj`).
`garmin()` is the logged-in Garmin client (`plan.reloj.cliente_garmin`, looked up when it is called).

Unit 044 (REC-24, R-145..R-150): an incident comes alive. Opened (only from a confirmed card, `plan.acciones`) it
keeps its zone, side, since when and what happened (`zona`, `lado`, `que_paso`, `estado` "abierta"), and its row of
`plan_incidencia` gets the text «zona lado: qué pasó», so the .md writes it among the incidents as before; its
visits to the physio or the doctor go to `plan_visita` (date, who, what they said); closing it keeps its lesson and
the day (`leccion`, `cerrada`). The incidents of the .md have no state: they are history, and are shown as closed
without a lesson (`importada`). The notes of a day («FC alta para el ritmo») go to `plan_nota_dia` and are shown in
what was done that day (`plan/semanas.py`). `leer` gives the incidents as before (apartado, fecha, texto).

Unit 045 (REC-25, R-156): a race keeps its strategy by stretches (`plan_carrera.estrategia`, its lines as JSON:
«km 4-6,5 · subida · por esfuerzo · paso 28:40», «gel: km 6,5»), saved only from a confirmed card (`plan.acciones`,
`guardar_estrategia`) and read with `estrategia`.

Unit 059 (R1-R4): a race keeps the result Andrés wrote by hand (`plan_carrera.resultado`, «1:12:30»), which an
import never writes; without one, the result of a race that was run comes from Garmin when the plan is read
(`plan/semanas.py`). `editar_carrera` changes its name, date, target and time in one `UPDATE` of its row, so its
strategy goes with it. `leer` gives each race its `estrategia` and its `resultado` only when it has them, so a plan
without them reads as before.

Unit 061 («Garmin manda»): a session paired with a workout of Garmin keeps Garmin's `updatedDate`
(`garmin_actualizado`, so it is converted again only when it changes) and the warning of the last pass
(`garmin_aviso`: «Ya no está en tu Garmin», or several sessions that day and none with its title), which
`sesiones_reloj` gives as `aviso`. `aplicar_garmin` writes what `plan.desde_garmin.cambios` says, IN PLACE (an added
session goes at the end, so no `orden` moves).

Unit 063 (plans by hand): `nuevo` makes a plan with no weeks, only its main race, with the sections of
`APARTADOS_NUEVO` (so its .md goes and comes back the same); `renombrar` and `borrar` (everything of it, in one
transaction). A race is added (`anadir_carrera`), removed (`quitar_carrera`), moved to another plan (`mover_carrera`,
one `UPDATE` of its row, so its target, result and strategy go with it) or made the main one (`hacer_principal`, one
per plan). A plan can be left without a main race (removing or moving it never picks another one): it then does not
count for the current plan, which is still the one of the next main race (`en_curso`).

Unit 067 (REC-30, R-199..R-208): the plan by hand. A session is added (`anadir_sesion`, in the section of the session
closest before it, as the cards of `plan.acciones` do), changed (`cambiar_sesion`), moved to another day
(`mover_sesion`) and deleted (`borrar_sesion`), each one an `UPDATE` or `DELETE` of its row IN PLACE: its `orden`
never moves, so Google Calendar (`<plan>-<orden>`) and the team keep it as the same session. A day that is over is
history (`YA_PASO`): its session is not changed nor deleted, only moved to today or a day to come. A sent session
keeps the day Garmin has it on (`garmin_fecha`): moved by hand it says «cambiada» until it is sent again, and the
next pass of «Garmin manda» does not take it back to that day (`aplicar_garmin`). Weeks are added
(`anadir_semana`, to the section of the weeks, or the «previsto» one of a plan made by hand), changed
(`cambiar_semana`) and deleted with their sessions (`borrar_semana`). The phases (`plan_fase`: name and the weeks it
spans, `desde_n`..`hasta_n`) are added, changed and deleted without touching their weeks; two phases never share a
week. `leer` gives `fases` only when the plan has them, so a plan without them reads as before.

Unit 076 (REC-31, R-216..R-220): a session may have the hour it starts (`plan_sesion.hora`, «HH:MM», NULL: none, as
every session of before). It is set when a session is added (`anadir_sesion`) or changed (`cambiar_sesion`: not said,
it stays; empty, it goes) and checked with `hora_de` («25:00» is a ValueError saying how it goes). Moving a session
and «Garmin manda» (`aplicar_garmin`) never touch it, and it is not part of what goes to the watch, so changing only
the hour never marks a sent session «cambiada». `leer` gives `hora` only to a session that has one; `sesion` and
`sesiones_reloj` always (None without one).
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .desde_garmin import Cambios

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
CREATE TABLE IF NOT EXISTS plan_visita (
    incidencia INTEGER NOT NULL,
    fecha TEXT NOT NULL,
    quien TEXT NOT NULL,
    que_dijo TEXT NOT NULL,
    creada TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_nota_dia (
    plan_id INTEGER NOT NULL REFERENCES plan(id),
    fecha TEXT NOT NULL,
    texto TEXT NOT NULL,
    creada TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_fase (
    plan_id INTEGER NOT NULL REFERENCES plan(id),
    orden INTEGER NOT NULL,
    nombre TEXT NOT NULL,
    desde_n INTEGER NOT NULL,
    hasta_n INTEGER NOT NULL
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
# unit 061: Garmin's `updatedDate` of a paired session and the warning of the last pass
_DESDE_GARMIN = (("garmin_actualizado", "TEXT"), ("garmin_aviso", "TEXT"))
# unit 067: the day Garmin has a sent session on, to tell a move by hand from a move in Garmin
_EN_GARMIN = (("garmin_fecha", "TEXT"),)
# unit 076: the hour a session starts («HH:MM»), NULL without one
_HORA = (("hora", "TEXT"),)
_COLUMNAS_GARMIN = {"titulo", "bloques", "fecha", "garmin_workout_id", "garmin_schedule_id", "garmin_actualizado",
                    "garmin_texto"}
# unit 044: the columns of a live incident, added to an older base without them
_VIVA = (("zona", "TEXT"), ("lado", "TEXT"), ("que_paso", "TEXT"), ("estado", "TEXT"), ("leccion", "TEXT"),
         ("cerrada", "TEXT"), ("abierta", "TEXT"))
# unit 045: the strategy of a race, added to an older base without it
_ESTRATEGIA = (("estrategia", "TEXT"),)
# unit 059: the result of a race written by hand, added to an older base without it
_RESULTADO = (("resultado", "TEXT"),)
HECHA = "✅"
# unit 063: the sections of a plan made by hand (the races, then the weeks and the sessions to come), titled as the
# import reads them (`plan/importar.py`), so its .md goes and comes back the same (R1)
APARTADOS_NUEVO = ({"titulo": "Objetivos", "parte": "objetivos"}, {"titulo": "Previsto", "parte": "previsto"},
                   {"titulo": "Sesiones clave", "parte": "sesiones_clave"})
_TABLAS_DEL_PLAN = ("plan_carrera", "plan_semana", "plan_sesion", "plan_nutricion", "plan_incidencia", "plan_nota",
                    "plan_nota_dia", "plan_fase")
ABIERTA, CERRADA = "abierta", "cerrada"
CONSERVA = object()  # unit 076: `cambiar_sesion` without an hour keeps the one the session had


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
            for tabla, columnas in (("plan_sesion", _RELOJ), ("plan_incidencia", _VIVA), ("plan_carrera", _ESTRATEGIA),
                                    ("plan_carrera", _RESULTADO), ("plan_sesion", _DESDE_GARMIN),
                                    ("plan_sesion", _EN_GARMIN), ("plan_sesion", _HORA)):
                tiene = {f["name"] for f in conn.execute(f"PRAGMA table_info({tabla})")}
                for columna, tipo in columnas:
                    if columna not in tiene:
                        conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")

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
                if tabla == "plan_sesion":  # unit 077: its hour too, when a session brings one
                    columnas = (*columnas, "hora")
                marcas = ", ".join("?" * (len(columnas) + 2))
                conn.executemany(
                    f"INSERT INTO {tabla} (plan_id, orden, {', '.join(columnas)}) VALUES ({marcas})",  # noqa: S608
                    [(ident, orden, *(hora_de(fila.get(c)) if c == "hora" else _a_la_base(tabla, c, fila.get(c))
                                      for c in columnas))
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
            extra = conn.execute("SELECT estrategia, resultado FROM plan_carrera WHERE plan_id = ? ORDER BY orden",
                                 (ident,)).fetchall()
            horas = [f["hora"] for f in conn.execute("SELECT hora FROM plan_sesion WHERE plan_id = ? ORDER BY orden",
                                                     (ident,))]
            fases = self._fases(conn, ident)
        for sesion, hora in zip(plan["sesiones"], horas, strict=True):  # unit 076: only when it has one
            if hora:
                sesion["hora"] = hora
        if fases:  # unit 067: only when it has them
            plan["fases"] = fases
        for carrera, f in zip(plan["carreras"], extra, strict=True):  # unit 059: only when it has them
            if f["estrategia"] and json.loads(f["estrategia"]):
                carrera["estrategia"] = json.loads(f["estrategia"])
            if f["resultado"]:
                carrera["resultado"] = f["resultado"]
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
                "bloques": json.loads(f["bloques"]), "clave": bool(f["clave"]), "hora": f["hora"],
                **{columna: f[columna] for columna, _ in _RELOJ}}

    def sesiones_reloj(self, plan_id: int) -> list[dict[str, Any]]:
        """`[{id, fecha, titulo, estado, enviada, aviso, hora}]` of the sessions of a plan, in the order of `leer`:
        `estado` is "al_dia" (in the watch as it is now), "cambiada" (changed since it was sent) or None (never sent);
        `aviso`, the warning of the last pass with Garmin (unit 061) or None; `hora`, its hour (unit 076) or None. The
        hour does not go to the watch, so it never makes a session «cambiada»."""
        with self._connect() as conn:
            filas = conn.execute("SELECT rowid AS id, * FROM plan_sesion WHERE plan_id = ? ORDER BY orden",
                                 (plan_id,)).fetchall()
        salida = []
        for f in filas:
            estado = None
            if f["garmin_workout_id"] is not None:
                ahora = texto_sesion(f["titulo"], json.loads(f["bloques"]))
                movida = f["garmin_fecha"] is not None and f["garmin_fecha"] != f["fecha"]  # unit 067
                estado = "al_dia" if f["garmin_texto"] == ahora and not movida else "cambiada"
            salida.append({"id": f["id"], "fecha": f["fecha"], "titulo": f["titulo"], "estado": estado,
                           "enviada": f["garmin_enviada"] if estado else None, "aviso": f["garmin_aviso"],
                           "hora": f["hora"]})
        return salida

    def marcar_garmin(self, ident: int, workout_id: int | None, schedule_id: int | None,
                      enviada: datetime | None = None, texto: str | None = None) -> None:
        """What is in Garmin of that session now (all None: nothing)."""
        with self._connect() as conn:
            conn.execute("UPDATE plan_sesion SET garmin_workout_id = ?, garmin_schedule_id = ?, garmin_enviada = ?, "
                         "garmin_texto = ?, garmin_fecha = CASE WHEN ? IS NULL THEN NULL ELSE fecha END "
                         "WHERE rowid = ?",
                         (workout_id, schedule_id, enviada.isoformat(timespec="seconds") if enviada else None,
                          texto, workout_id, ident))

    # -- unit 061: «Garmin manda» --------------------------------------------------------------------------------

    def fin(self, plan_id: int) -> date | None:
        """The last day of a plan: its last week, session or race; None without that plan or without dates."""
        with self._connect() as conn:
            (fin,) = conn.execute(
                "SELECT MAX(d) FROM (SELECT MAX(hasta) AS d FROM plan_semana WHERE plan_id = ? "
                "UNION ALL SELECT MAX(fecha) FROM plan_sesion WHERE plan_id = ? "
                "UNION ALL SELECT MAX(fecha) FROM plan_carrera WHERE plan_id = ?)",
                (plan_id, plan_id, plan_id)).fetchone()
        return date.fromisoformat(fin) if fin else None

    def sesiones_garmin(self, plan_id: int, desde: date, hasta: date) -> list[dict[str, Any]]:
        """The sessions of a plan from `desde` to `hasta`, in its order, as `plan.desde_garmin.cambios` pairs them:
        `{id, fecha, titulo, bloques, garmin_workout_id, garmin_schedule_id, garmin_actualizado, garmin_texto}`."""
        with self._connect() as conn:
            filas = conn.execute("SELECT rowid AS id, * FROM plan_sesion WHERE plan_id = ? AND fecha BETWEEN ? AND ? "
                                 "ORDER BY orden", (plan_id, desde.isoformat(), hasta.isoformat())).fetchall()
        return [{"id": f["id"], "fecha": f["fecha"], "titulo": f["titulo"], "bloques": json.loads(f["bloques"]),
                 "garmin_workout_id": f["garmin_workout_id"], "garmin_schedule_id": f["garmin_schedule_id"],
                 "garmin_actualizado": f["garmin_actualizado"], "garmin_texto": f["garmin_texto"]} for f in filas]

    def aplicar_garmin(self, plan_id: int, cambios: Cambios) -> dict[str, int]:
        """Writes a pass of «Garmin manda» in one transaction: the changed sessions in place, the new ones at the end
        of the plan (in the section of its last session) and the warnings. `{cambiadas, anadidas, avisos}`."""
        with self._connect() as conn:
            for ident, campos in cambios.actualizar.items():
                valores = {c: _a_la_base("plan_sesion", c, v) for c, v in campos.items() if c in _COLUMNAS_GARMIN}
                if "fecha" in valores:  # unit 067: a session moved by hand, Garmin still on the day it was sent
                    fila = conn.execute("SELECT fecha, garmin_fecha FROM plan_sesion WHERE rowid = ? AND plan_id = ?",
                                        (ident, plan_id)).fetchone()
                    if fila is not None and fila["garmin_fecha"] is not None and fila["fecha"] != fila["garmin_fecha"] \
                            and valores["fecha"] == fila["garmin_fecha"]:
                        del valores["fecha"]
                    else:
                        valores["garmin_fecha"] = valores["fecha"]
                if valores:
                    conn.execute(f"UPDATE plan_sesion SET {', '.join(f'{c} = ?' for c in valores)} "  # noqa: S608
                                 "WHERE rowid = ? AND plan_id = ?", (*valores.values(), ident, plan_id))
            if cambios.anadir:
                (orden,) = conn.execute("SELECT COALESCE(MAX(orden), -1) + 1 FROM plan_sesion WHERE plan_id = ?",
                                        (plan_id,)).fetchone()
                fila = conn.execute("SELECT apartado FROM plan_sesion WHERE plan_id = ? ORDER BY orden DESC LIMIT 1",
                                    (plan_id,)).fetchone()
                if fila is not None:
                    apartado = fila[0]
                else:
                    (apartados,) = conn.execute("SELECT apartados FROM plan WHERE id = ?", (plan_id,)).fetchone()
                    apartado = max(len(json.loads(apartados)) - 1, 0)
                for i, nueva in enumerate(cambios.anadir):
                    columnas = [c for c in nueva if c in _COLUMNAS_GARMIN]
                    nueva_id = conn.execute(
                        f"INSERT INTO plan_sesion (plan_id, orden, apartado, clave, {', '.join(columnas)}) "  # noqa: S608
                        f"VALUES (?, ?, ?, 0, {', '.join('?' * len(columnas))})",
                        (plan_id, orden + i, apartado, *(_a_la_base("plan_sesion", c, nueva[c]) for c in columnas))
                    ).lastrowid
                    conn.execute("UPDATE plan_sesion SET garmin_fecha = fecha WHERE rowid = ?", (nueva_id,))
            for ident, aviso in cambios.avisos.items():
                conn.execute("UPDATE plan_sesion SET garmin_aviso = ? WHERE rowid = ? AND plan_id = ?",
                             (aviso, ident, plan_id))
        return {"cambiadas": len(cambios.actualizar), "anadidas": len(cambios.anadir),
                "avisos": sum(1 for a in cambios.avisos.values() if a)}

    def garmin(self) -> Any:
        """The Garmin client to send a session with (logged in only now, never when the app starts)."""
        from . import reloj

        return reloj.cliente_garmin()


    # -- unit 045: the strategy of a race -------------------------------------------------------------------------

    def estrategia(self, plan_id: int, carrera: str) -> list[str]:
        """The lines of the strategy of that race of the plan ([] without one)."""
        with self._connect() as conn:
            fila = conn.execute("SELECT estrategia FROM plan_carrera WHERE plan_id = ? AND nombre = ? ORDER BY orden "
                                "LIMIT 1", (plan_id, carrera)).fetchone()
        return json.loads(fila["estrategia"]) if fila is not None and fila["estrategia"] else []

    def guardar_estrategia(self, plan_id: int, carrera: str, lineas: list[str]) -> None:
        """That strategy in that race of the plan, instead of the one it had (only from a confirmed card, R-156)."""
        with self._connect() as conn:
            conn.execute("UPDATE plan_carrera SET estrategia = ? WHERE plan_id = ? AND nombre = ?",
                         (json.dumps(list(lineas), ensure_ascii=False), plan_id, carrera))

    # -- unit 059: a race edited from its row of Fitness › Plan ------------------------------------------------------

    def editar_carrera(self, plan_id: int, carrera: str, cambios: dict[str, Any]) -> None:
        """Changes `nombre`, `fecha` (ISO or empty), `objetivo` and `resultado` (the time by hand; empty: none, and a
        «✅ …» of the .md goes too, so Garmin's is seen) of that race of the plan, only the ones in `cambios`, in one
        `UPDATE` of its row (its strategy stays with it). ValueError, with why, for an empty or repeated name or a bad
        date, and nothing is saved; LookupError without that race."""
        with self._connect() as conn:
            fila = conn.execute("SELECT rowid AS id, estado FROM plan_carrera WHERE plan_id = ? AND nombre = ? "
                                "ORDER BY orden LIMIT 1", (plan_id, carrera)).fetchone()
            if fila is None:
                raise LookupError(carrera)
            valores: dict[str, Any] = {}
            if "nombre" in cambios:
                nombre = " ".join(str(cambios["nombre"] or "").split())
                if not nombre:
                    raise ValueError("Ponle un nombre a la carrera")
                otra = conn.execute("SELECT 1 FROM plan_carrera WHERE plan_id = ? AND nombre = ? AND rowid != ?",
                                    (plan_id, nombre, fila["id"])).fetchone()
                if otra is not None:
                    raise ValueError(f"Ya hay otra carrera en el plan que se llama «{nombre}»")
                valores["nombre"] = nombre
            if "fecha" in cambios:
                valores["fecha"] = _fecha(cambios["fecha"])
            if "objetivo" in cambios:
                valores["objetivo"] = " ".join(str(cambios["objetivo"] or "").split())
            if "resultado" in cambios:
                resultado = " ".join(str(cambios["resultado"] or "").split())
                valores["resultado"] = resultado or None
                if not resultado and str(fila["estado"] or "").startswith(HECHA):
                    valores["estado"] = "pendiente"
            if valores:
                conn.execute(f"UPDATE plan_carrera SET {', '.join(f'{c} = ?' for c in valores)} "  # noqa: S608
                             "WHERE rowid = ?", (*valores.values(), fila["id"]))

    # -- unit 063: plans by hand ---------------------------------------------------------------------------------------

    def nuevo(self, nombre: str, carrera: dict[str, Any], ahora: datetime) -> int:
        """A new plan with no weeks, only its main race (`{nombre, fecha, objetivo}`), with the sections of a plan
        (`APARTADOS_NUEVO`) so «Bajar .md» and importing it again give the same plan (R1). ValueError, with why, for
        an empty name of the plan or of the race, or a missing or bad date; nothing is saved."""
        nombre_plan = _limpio(nombre)
        if not nombre_plan:
            raise ValueError("Ponle un nombre al plan")
        principal = _carrera_nueva(carrera)
        if principal["fecha"] is None:
            raise ValueError("La carrera principal necesita su fecha (año-mes-día)")
        return self.crear({"nombre": nombre_plan, "apartados": [dict(a) for a in APARTADOS_NUEVO],
                           "carreras": [{**principal, "apartado": 0, "principal": True}]}, ahora)

    def renombrar(self, plan_id: int, nombre: str) -> None:
        """Another name for that plan (R2). ValueError if empty; LookupError without that plan."""
        limpio = _limpio(nombre)
        if not limpio:
            raise ValueError("Ponle un nombre al plan")
        with self._connect() as conn:
            if conn.execute("UPDATE plan SET nombre = ? WHERE id = ?", (limpio, plan_id)).rowcount == 0:
                raise LookupError(plan_id)

    def borrar(self, plan_id: int) -> None:
        """The whole plan and everything of it (races, weeks, sessions, nutrition, incidents with their visits, notes),
        in one transaction (R3). Garmin and what was done are not touched. LookupError without that plan."""
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM plan WHERE id = ?", (plan_id,)).fetchone() is None:
                raise LookupError(plan_id)
            conn.execute("DELETE FROM plan_visita WHERE incidencia IN "
                         "(SELECT rowid FROM plan_incidencia WHERE plan_id = ?)", (plan_id,))
            for tabla in _TABLAS_DEL_PLAN:
                conn.execute(f"DELETE FROM {tabla} WHERE plan_id = ?", (plan_id,))  # noqa: S608
            conn.execute("DELETE FROM plan WHERE id = ?", (plan_id,))

    @staticmethod
    def _apartado_carreras(conn: sqlite3.Connection, plan_id: int) -> int:
        """The section of the races of a plan: the one of its last race; without races, its «objetivos» section; else
        a new «Carreras» section at the end."""
        fila = conn.execute("SELECT apartado FROM plan_carrera WHERE plan_id = ? ORDER BY orden DESC LIMIT 1",
                            (plan_id,)).fetchone()
        if fila is not None:
            return int(fila[0])
        (texto,) = conn.execute("SELECT apartados FROM plan WHERE id = ?", (plan_id,)).fetchone()
        apartados = json.loads(texto)
        i = next((i for i, a in enumerate(apartados) if a.get("parte") == "objetivos"), None)
        if i is not None:
            return i
        apartados.append({"titulo": "Carreras", "parte": "objetivos"})
        conn.execute("UPDATE plan SET apartados = ? WHERE id = ?", (json.dumps(apartados, ensure_ascii=False), plan_id))
        return len(apartados) - 1

    @staticmethod
    def _repetida(conn: sqlite3.Connection, plan_id: int, nombre: str) -> None:
        if conn.execute("SELECT 1 FROM plan_carrera WHERE plan_id = ? AND nombre = ?", (plan_id, nombre)).fetchone():
            raise ValueError(f"Ya hay otra carrera en el plan que se llama «{nombre}»")

    @staticmethod
    def _fila_carrera(conn: sqlite3.Connection, plan_id: int, carrera: str) -> int:
        fila = conn.execute("SELECT rowid FROM plan_carrera WHERE plan_id = ? AND nombre = ? ORDER BY orden LIMIT 1",
                            (plan_id, carrera)).fetchone()
        if fila is None:
            raise LookupError(carrera)
        return int(fila[0])

    def anadir_carrera(self, plan_id: int, carrera: dict[str, Any]) -> None:
        """A race (`{nombre, fecha, objetivo}`) at the end of the races of that plan, not the main one (R4). ValueError
        for an empty name, a name of another race of the plan or a bad date; LookupError without that plan."""
        nueva = _carrera_nueva(carrera)
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM plan WHERE id = ?", (plan_id,)).fetchone() is None:
                raise LookupError(plan_id)
            self._repetida(conn, plan_id, nueva["nombre"])
            apartado = self._apartado_carreras(conn, plan_id)
            (orden,) = conn.execute("SELECT COALESCE(MAX(orden), -1) + 1 FROM plan_carrera WHERE plan_id = ?",
                                    (plan_id,)).fetchone()
            conn.execute("INSERT INTO plan_carrera (plan_id, orden, apartado, nombre, fecha, objetivo, estado, "
                         "principal) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                         (plan_id, orden, apartado, nueva["nombre"], nueva["fecha"], nueva["objetivo"],
                          nueva["estado"]))

    def quitar_carrera(self, plan_id: int, carrera: str) -> None:
        """That race out of the plan, with its strategy and result (R5); if it was the main one, the plan has none
        (R8). LookupError without it."""
        with self._connect() as conn:
            conn.execute("DELETE FROM plan_carrera WHERE rowid = ?", (self._fila_carrera(conn, plan_id, carrera),))

    def mover_carrera(self, plan_id: int, carrera: str, destino: int) -> None:
        """That race to the end of the races of plan `destino`, in one `UPDATE` of its row, so its target, its result
        by hand and its strategy go with it (R5); it is not the main one there, and its plan of origin is left without
        one if it was (R8). ValueError if `destino` is the same plan or already has a race with that name;
        LookupError without the race or the plan of destination."""
        if destino == plan_id:
            raise ValueError("La carrera ya está en ese plan")
        with self._connect() as conn:
            fila = self._fila_carrera(conn, plan_id, carrera)
            if conn.execute("SELECT 1 FROM plan WHERE id = ?", (destino,)).fetchone() is None:
                raise LookupError(destino)
            self._repetida(conn, destino, carrera)
            apartado = self._apartado_carreras(conn, destino)
            (orden,) = conn.execute("SELECT COALESCE(MAX(orden), -1) + 1 FROM plan_carrera WHERE plan_id = ?",
                                    (destino,)).fetchone()
            conn.execute("UPDATE plan_carrera SET plan_id = ?, orden = ?, apartado = ?, principal = 0 WHERE rowid = ?",
                         (destino, orden, apartado, fila))

    def hacer_principal(self, plan_id: int, carrera: str) -> None:
        """That race is the main one of its plan, and the one that was is not any more: one per plan (R6).
        LookupError without it."""
        with self._connect() as conn:
            fila = self._fila_carrera(conn, plan_id, carrera)
            conn.execute("UPDATE plan_carrera SET principal = (rowid = ?) WHERE plan_id = ?", (fila, plan_id))


    # -- unit 067: the plan by hand — sessions, weeks and phases ----------------------------------------------------

    @staticmethod
    def _plan_existe(conn: sqlite3.Connection, plan_id: int) -> None:
        if conn.execute("SELECT 1 FROM plan WHERE id = ?", (plan_id,)).fetchone() is None:
            raise LookupError(plan_id)

    @staticmethod
    def _fila_sesion(conn: sqlite3.Connection, ident: int) -> sqlite3.Row:
        fila = conn.execute("SELECT rowid AS id, * FROM plan_sesion WHERE rowid = ?", (ident,)).fetchone()
        if fila is None:
            raise LookupError(ident)
        return fila

    def anadir_sesion(self, plan_id: int, fecha: str, titulo: str, bloques: list[str] | str, hoy: date,
                      hora: str | None = None) -> int:
        """A session on that day (R-199), with its blocks (one line each, the empty ones left out) and its hour if it
        has one (unit 076, «HH:MM»), in the section of the session closest before it; its id. ValueError, with why,
        for a bad date, a day that is over (`YA_PASO`), an empty title or a bad hour; LookupError without that plan."""
        from .acciones import _sitio

        dia, limpio, lineas = _dia_que_viene(fecha, hoy), _titulo(titulo), _bloques_limpios(bloques)
        a_la = hora_de(hora)
        with self._connect() as conn:
            self._plan_existe(conn, plan_id)
            apartado, orden = _sitio(conn, "plan_sesion", plan_id, dia)
            cursor = conn.execute("INSERT INTO plan_sesion (plan_id, orden, apartado, fecha, titulo, bloques, clave, "
                                  "hora) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                                  (plan_id, orden, apartado, dia, limpio, json.dumps(lineas, ensure_ascii=False), a_la))
            return int(cursor.lastrowid or 0)

    def cambiar_sesion(self, ident: int, titulo: str, bloques: list[str] | str, hoy: date,
                       hora: str | None | object = CONSERVA) -> None:
        """Another title and blocks for that session, in its row (R-200), and its hour (unit 076): not given
        (`CONSERVA`), the one it had stays; «HH:MM», that one; empty or None, none. ValueError for an empty title, a
        bad hour or a day that is over (R-207); LookupError without it."""
        limpio, lineas = _titulo(titulo), _bloques_limpios(bloques)
        a_la = None if hora is CONSERVA else hora_de(hora)
        with self._connect() as conn:
            fila = self._fila_sesion(conn, ident)
            _no_pasado(fila["fecha"], hoy)
            conn.execute("UPDATE plan_sesion SET titulo = ?, bloques = ?, hora = ? WHERE rowid = ?",
                         (limpio, json.dumps(lineas, ensure_ascii=False),
                          fila["hora"] if hora is CONSERVA else a_la, ident))

    def mover_sesion(self, ident: int, fecha: str, hoy: date) -> None:
        """That session to another day, with its blocks, in ONE `UPDATE` of its row: its `orden` stays, so Google
        Calendar and the team keep it as the same session (R-201). A session of a day that is over can be moved to
        today or a day to come, never to a day that is over (R-207). Sent to Garmin, it keeps the day Garmin has it
        on, so it says «cambiada» (R-205). ValueError with why; LookupError without it."""
        dia = _dia_que_viene(fecha, hoy)
        with self._connect() as conn:
            fila = self._fila_sesion(conn, ident)
            conn.execute("UPDATE plan_sesion SET fecha = ?, garmin_fecha = CASE WHEN garmin_workout_id IS NULL "
                         "THEN garmin_fecha ELSE COALESCE(garmin_fecha, ?) END WHERE rowid = ?",
                         (dia, fila["fecha"], ident))

    def borrar_sesion(self, ident: int, hoy: date) -> None:
        """That session out of the plan (R-202); not one of a day that is over (R-207). Garmin is not touched here:
        `web/plan.py` removes it from Garmin first (`plan.reloj.quitar`, R-206). LookupError without it."""
        with self._connect() as conn:
            fila = self._fila_sesion(conn, ident)
            _no_pasado(fila["fecha"], hoy)
            conn.execute("DELETE FROM plan_sesion WHERE rowid = ?", (ident,))

    @staticmethod
    def _fila_semana(conn: sqlite3.Connection, plan_id: int, n: int) -> sqlite3.Row:
        fila = conn.execute("SELECT rowid AS id, * FROM plan_semana WHERE plan_id = ? AND n = ? ORDER BY orden LIMIT 1",
                            (plan_id, n)).fetchone()
        if fila is None:
            raise LookupError(n)
        return fila

    @staticmethod
    def _apartado_semanas(conn: sqlite3.Connection, plan_id: int) -> int:
        """The section of the weeks of a plan: the one of its last week; without weeks, its «previsto» section (a plan
        made by hand, `APARTADOS_NUEVO`); else a new «Semanas» section at the end."""
        fila = conn.execute("SELECT apartado FROM plan_semana WHERE plan_id = ? ORDER BY orden DESC LIMIT 1",
                            (plan_id,)).fetchone()
        if fila is not None:
            return int(fila[0])
        (texto,) = conn.execute("SELECT apartados FROM plan WHERE id = ?", (plan_id,)).fetchone()
        apartados = json.loads(texto)
        i = next((i for i, a in enumerate(apartados) if a.get("parte") == "previsto"), None)
        if i is not None:
            return i
        apartados.append({"titulo": "Semanas", "parte": "previsto"})
        conn.execute("UPDATE plan SET apartados = ? WHERE id = ?", (json.dumps(apartados, ensure_ascii=False), plan_id))
        return len(apartados) - 1

    @staticmethod
    def _semana_valida(conn: sqlite3.Connection, plan_id: int, semana: dict[str, Any], rowid: int | None) -> None:
        if semana["hasta"] < semana["desde"]:
            raise ValueError(f"La semana {semana['n']} acaba ({semana['hasta']}) antes de empezar ({semana['desde']}): "
                             "la fecha hasta va después de la fecha desde")
        otra = conn.execute("SELECT 1 FROM plan_semana WHERE plan_id = ? AND n = ? AND rowid != ?",
                            (plan_id, semana["n"], rowid or -1)).fetchone()
        if otra is not None:
            raise ValueError(f"Ya hay una semana {semana['n']} en el plan")
        pisada = conn.execute("SELECT n, desde, hasta FROM plan_semana WHERE plan_id = ? AND rowid != ? AND desde <= ? "
                              "AND hasta >= ? ORDER BY desde LIMIT 1",
                              (plan_id, rowid or -1, semana["hasta"], semana["desde"])).fetchone()
        if pisada is not None:  # unit 072: deleting one of two overlapping weeks would take sessions of the other
            raise ValueError(f"Esa semana pisa la semana {pisada[0]} (del {pisada[1]} al {pisada[2]}): una semana "
                             "no comparte días con otra del plan")

    @staticmethod
    def _ajustar_fases(conn: sqlite3.Connection, plan_id: int) -> None:
        """Unit 072: each phase keeps the weeks it still has inside its stretch (first and last number), or goes if it
        has none; called after a week is deleted or renumbered. The weeks themselves are not touched."""
        tiene = sorted(int(f[0]) for f in conn.execute("SELECT n FROM plan_semana WHERE plan_id = ?", (plan_id,)))
        for fase in conn.execute("SELECT rowid, desde_n, hasta_n FROM plan_fase WHERE plan_id = ?",
                                 (plan_id,)).fetchall():
            dentro = [n for n in tiene if fase[1] <= n <= fase[2]]
            if not dentro:
                conn.execute("DELETE FROM plan_fase WHERE rowid = ?", (fase[0],))
            elif (dentro[0], dentro[-1]) != (fase[1], fase[2]):
                conn.execute("UPDATE plan_fase SET desde_n = ?, hasta_n = ? WHERE rowid = ?",
                             (dentro[0], dentro[-1], fase[0]))

    def anadir_semana(self, plan_id: int, datos: dict[str, Any]) -> None:
        """A week (`{n, desde, hasta, previsto, clave}`; `previsto` as «4:30», «4h30» or «270'») of that plan, also of
        an empty one (R-203). ValueError, with why, for a bad number, dates or planned time, `hasta` before `desde` or
        a number the plan has; LookupError without that plan."""
        semana = _semana_nueva(datos)
        with self._connect() as conn:
            self._plan_existe(conn, plan_id)
            self._semana_valida(conn, plan_id, semana, None)
            apartado = self._apartado_semanas(conn, plan_id)
            (orden,) = conn.execute("SELECT COALESCE(MAX(orden), -1) + 1 FROM plan_semana WHERE plan_id = ?",
                                    (plan_id,)).fetchone()
            conn.execute("INSERT INTO plan_semana (plan_id, orden, apartado, n, desde, hasta, previsto_s, clave) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (plan_id, orden, apartado, semana["n"], semana["desde"], semana["hasta"],
                          semana["previsto_s"], semana["clave"]))

    def cambiar_semana(self, plan_id: int, n: int, cambios: dict[str, Any]) -> None:
        """Changes `n`, `desde`, `hasta`, `previsto` and `clave` of week `n` of that plan, only the ones in `cambios`, in
        its row (R-203). ValueError, with why, as `anadir_semana`; LookupError without that week."""
        with self._connect() as conn:
            fila = self._fila_semana(conn, plan_id, n)
            actual = {"n": fila["n"], "desde": fila["desde"], "hasta": fila["hasta"], "clave": fila["clave"],
                      "previsto": fila["previsto_s"]}
            semana = _semana_nueva({**actual, **cambios}, previsto_s="previsto" not in cambios)
            self._semana_valida(conn, plan_id, semana, fila["id"])
            conn.execute("UPDATE plan_semana SET n = ?, desde = ?, hasta = ?, previsto_s = ?, clave = ? WHERE rowid = ?",
                         (semana["n"], semana["desde"], semana["hasta"], semana["previsto_s"], semana["clave"],
                          fila["id"]))
            if semana["n"] != fila["n"]:
                self._ajustar_fases(conn, plan_id)

    def sesiones_de_semana(self, plan_id: int, n: int) -> int:
        """How many sessions week `n` of that plan has (what goes with it if it is deleted). LookupError without it."""
        with self._connect() as conn:
            fila = self._fila_semana(conn, plan_id, n)
            (cuantas,) = conn.execute("SELECT COUNT(*) FROM plan_sesion WHERE plan_id = ? AND fecha BETWEEN ? AND ?",
                                      (plan_id, fila["desde"], fila["hasta"])).fetchone()
        return int(cuantas)

    def borrar_semana(self, plan_id: int, n: int) -> int:
        """Week `n` out of that plan with its sessions, in one transaction; how many sessions went with it (R-203). Its
        phase stays, shrunk to the weeks it still has (unit 072). Garmin is not touched here (`web/plan.py` removes the sent ones first). LookupError without it."""
        with self._connect() as conn:
            fila = self._fila_semana(conn, plan_id, n)
            borradas = conn.execute("DELETE FROM plan_sesion WHERE plan_id = ? AND fecha BETWEEN ? AND ?",
                                    (plan_id, fila["desde"], fila["hasta"])).rowcount
            conn.execute("DELETE FROM plan_semana WHERE rowid = ?", (fila["id"],))
            self._ajustar_fases(conn, plan_id)
        return int(borradas)

    @staticmethod
    def _fases(conn: sqlite3.Connection, plan_id: int) -> list[dict[str, Any]]:
        filas = conn.execute("SELECT rowid AS id, nombre, desde_n, hasta_n FROM plan_fase WHERE plan_id = ? "
                             "ORDER BY desde_n, orden", (plan_id,)).fetchall()
        return [{"id": f["id"], "nombre": f["nombre"], "desde_n": f["desde_n"], "hasta_n": f["hasta_n"]} for f in filas]

    @staticmethod
    def _fase_valida(conn: sqlite3.Connection, plan_id: int, nombre: str, desde_n: Any, hasta_n: Any,
                     rowid: int | None) -> tuple[str, int, int]:
        limpio = _limpio(nombre)
        if not limpio:
            raise ValueError("Ponle un nombre a la fase")
        desde, hasta = _numero(desde_n, "la semana desde"), _numero(hasta_n, "la semana hasta")
        if hasta < desde:
            raise ValueError(f"La fase acaba en la semana {hasta}, antes de empezar (semana {desde}): la semana hasta "
                             "va después")
        tiene = {int(f[0]) for f in conn.execute("SELECT n FROM plan_semana WHERE plan_id = ?", (plan_id,))}
        for extremo in (desde, hasta):
            if extremo not in tiene:
                raise ValueError(f"El plan no tiene la semana {extremo}")
        pisada = conn.execute("SELECT nombre FROM plan_fase WHERE plan_id = ? AND rowid != ? AND desde_n <= ? "
                              "AND hasta_n >= ? ORDER BY desde_n LIMIT 1", (plan_id, rowid or -1, hasta, desde)).fetchone()
        if pisada is not None:
            raise ValueError(f"Esas semanas pisan la fase «{pisada[0]}»: una semana está en una fase o en ninguna")
        return limpio, desde, hasta

    def anadir_fase(self, plan_id: int, nombre: str, desde_n: Any, hasta_n: Any) -> int:
        """A phase («Base») over weeks `desde_n`..`hasta_n` of that plan; its id (R-204, S-70). ValueError, with why,
        for an empty name, `hasta_n` before `desde_n`, a week the plan has not or one in another phase; LookupError
        without that plan."""
        with self._connect() as conn:
            self._plan_existe(conn, plan_id)
            limpio, desde, hasta = self._fase_valida(conn, plan_id, nombre, desde_n, hasta_n, None)
            (orden,) = conn.execute("SELECT COALESCE(MAX(orden), -1) + 1 FROM plan_fase WHERE plan_id = ?",
                                    (plan_id,)).fetchone()
            cursor = conn.execute("INSERT INTO plan_fase (plan_id, orden, nombre, desde_n, hasta_n) VALUES (?, ?, ?, ?, ?)",
                                  (plan_id, orden, limpio, desde, hasta))
            return int(cursor.lastrowid or 0)

    def _fila_fase(self, conn: sqlite3.Connection, ident: int) -> sqlite3.Row:
        fila = conn.execute("SELECT rowid AS id, plan_id FROM plan_fase WHERE rowid = ?", (ident,)).fetchone()
        if fila is None:
            raise LookupError(ident)
        return fila

    def fase(self, ident: int) -> dict[str, Any] | None:
        """`{id, plan_id}` of a phase, or None."""
        with self._connect() as conn:
            fila = conn.execute("SELECT rowid AS id, plan_id FROM plan_fase WHERE rowid = ?", (ident,)).fetchone()
        return {"id": fila["id"], "plan_id": fila["plan_id"]} if fila is not None else None

    def cambiar_fase(self, ident: int, nombre: str, desde_n: Any, hasta_n: Any) -> None:
        """Another name and stretch for that phase (R-204); ValueError as `anadir_fase`, LookupError without it."""
        with self._connect() as conn:
            fila = self._fila_fase(conn, ident)
            limpio, desde, hasta = self._fase_valida(conn, fila["plan_id"], nombre, desde_n, hasta_n, ident)
            conn.execute("UPDATE plan_fase SET nombre = ?, desde_n = ?, hasta_n = ? WHERE rowid = ?",
                         (limpio, desde, hasta, ident))

    def borrar_fase(self, ident: int) -> None:
        """That phase out of the plan; its weeks stay (R-204). LookupError without it."""
        with self._connect() as conn:
            self._fila_fase(conn, ident)
            conn.execute("DELETE FROM plan_fase WHERE rowid = ?", (ident,))

    # -- unit 044: the live incidents and the notes of a day ---------------------------------------------------------

    def abrir_incidencia(self, plan_id: int, zona: str, lado: str | None, desde: date, que_paso: str,
                         ahora: datetime) -> int:
        """Opens an incident in that plan and returns its id (only from a confirmed card, R-145)."""
        texto = f"{titulo_incidencia(zona, lado)}: {que_paso}"
        with self._connect() as conn:
            fila = conn.execute("SELECT MAX(apartado) FROM plan_incidencia WHERE plan_id = ?", (plan_id,)).fetchone()
            apartado = fila[0]
            if apartado is None:
                (apartados,) = conn.execute("SELECT apartados FROM plan WHERE id = ?", (plan_id,)).fetchone()
                apartado = max(len(json.loads(apartados)) - 1, 0)
            (orden,) = conn.execute("SELECT COALESCE(MAX(orden), -1) + 1 FROM plan_incidencia WHERE plan_id = ?",
                                    (plan_id,)).fetchone()
            cursor = conn.execute(
                "INSERT INTO plan_incidencia (plan_id, orden, apartado, fecha, texto, zona, lado, que_paso, estado, "
                "abierta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (plan_id, orden, apartado, desde.isoformat(), texto, zona, lado, que_paso, ABIERTA,
                 ahora.isoformat(timespec="seconds")))
            return int(cursor.lastrowid or 0)

    def anadir_visita(self, ident: int, fecha: date, quien: str, que_dijo: str, ahora: datetime) -> None:
        """A visit to the physio or the doctor of that incident (R-146)."""
        with self._connect() as conn:
            conn.execute("INSERT INTO plan_visita (incidencia, fecha, quien, que_dijo, creada) VALUES (?, ?, ?, ?, ?)",
                         (ident, fecha.isoformat(), quien, que_dijo, ahora.isoformat(timespec="seconds")))

    def cerrar_incidencia(self, ident: int, leccion: str, dia: date) -> None:
        """Closed with its lesson on that day: it is not treated as open any more (R-148)."""
        with self._connect() as conn:
            conn.execute("UPDATE plan_incidencia SET estado = ?, leccion = ?, cerrada = ? WHERE rowid = ?",
                         (CERRADA, leccion, dia.isoformat(), ident))

    def _incidencias(self, donde: str, valores: tuple) -> list[dict[str, Any]]:
        with self._connect() as conn:
            filas = conn.execute(f"SELECT rowid AS id, * FROM plan_incidencia WHERE {donde}", valores).fetchall()  # noqa: S608
            visitas: dict[int, list[dict[str, Any]]] = {}
            for v in conn.execute("SELECT incidencia, fecha, quien, que_dijo FROM plan_visita ORDER BY fecha, rowid"):
                visitas.setdefault(v["incidencia"], []).append(
                    {"fecha": v["fecha"], "quien": v["quien"], "que_dijo": v["que_dijo"]})
        salida = []
        for f in filas:
            importada = f["estado"] is None
            salida.append({
                "id": f["id"], "plan_id": f["plan_id"], "zona": f["zona"], "lado": f["lado"],
                "titulo": f["texto"] if importada else titulo_incidencia(f["zona"], f["lado"]),
                "desde": f["fecha"], "que_paso": f["texto"] if importada else f["que_paso"], "texto": f["texto"],
                "estado": CERRADA if importada else f["estado"], "leccion": f["leccion"], "cerrada": f["cerrada"],
                "visitas": visitas.get(f["id"], []), "importada": importada})
        abiertas = sorted((i for i in salida if i["estado"] == ABIERTA), key=lambda i: i["desde"] or "", reverse=True)
        cerradas = sorted((i for i in salida if i["estado"] != ABIERTA),
                          key=lambda i: i["cerrada"] or i["desde"] or "", reverse=True)
        return abiertas + cerradas

    def incidencias(self, plan_id: int) -> list[dict[str, Any]]:
        """The incidents of a plan, the open ones first (R-147): `{id, plan_id, zona, lado, titulo, desde, que_paso,
        texto, estado, leccion, cerrada, visitas, importada}`."""
        return self._incidencias("plan_id = ?", (plan_id,))

    def incidencia(self, ident: int) -> dict[str, Any] | None:
        """One incident by its id, or None."""
        return next(iter(self._incidencias("rowid = ?", (ident,))), None)

    def abiertas(self) -> list[dict[str, Any]]:
        """The open incidents of every plan."""
        return self._incidencias("estado = ?", (ABIERTA,))

    def nota_del_dia(self, plan_id: int, fecha: date, texto: str, ahora: datetime) -> None:
        """A note of how a day went, in what was done that day (R-150)."""
        with self._connect() as conn:
            conn.execute("INSERT INTO plan_nota_dia (plan_id, fecha, texto, creada) VALUES (?, ?, ?, ?)",
                         (plan_id, fecha.isoformat(), texto, ahora.isoformat(timespec="seconds")))

    def notas_del_dia(self, plan_id: int) -> dict[str, list[str]]:
        """`{fecha: [texto, …]}` of a plan, each day in the order they were written."""
        with self._connect() as conn:
            filas = conn.execute("SELECT fecha, texto FROM plan_nota_dia WHERE plan_id = ? ORDER BY fecha, rowid",
                                 (plan_id,)).fetchall()
        notas: dict[str, list[str]] = {}
        for f in filas:
            notas.setdefault(f["fecha"], []).append(f["texto"])
        return notas


_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def hora_de(valor: Any) -> str | None:
    """Unit 076: the hour of a session as «HH:MM» («7:30» is «07:30»), or None if empty; else ValueError saying how it
    goes (00:00 to 23:59)."""
    texto = str(valor or "").strip()
    if not texto:
        return None
    encontrada = _HHMM.match(texto)
    if encontrada is None:
        raise ValueError(f"La hora «{texto}» no vale: va como HH:MM, de 00:00 a 23:59 (p. ej. 20:00)")
    return f"{int(encontrada.group(1)):02d}:{encontrada.group(2)}"


def con_hora(hora: str | None, texto: str) -> str:
    """«20:00 · texto» when there is an hour; the text alone without one (unit 076)."""
    return f"{hora} · {texto}" if hora else texto


def _fecha(valor: Any) -> str | None:
    """An ISO date (YYYY-MM-DD) that exists, or None if empty; else ValueError saying why."""
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        if len(texto) != 10:
            raise ValueError(texto)
        return date.fromisoformat(texto).isoformat()
    except ValueError:
        raise ValueError(f"La fecha «{texto}» no es una fecha válida (año-mes-día)") from None


def _limpio(valor: Any) -> str:
    return " ".join(str(valor or "").split())


def _titulo(valor: Any) -> str:
    limpio = _limpio(valor)
    if not limpio:
        raise ValueError("Ponle un título a la sesión")
    return limpio


def _bloques_limpios(bloques: Any) -> list[str]:
    """One block per line (a list, or a text with a line per block), tidied; the empty ones left out."""
    lineas = bloques.splitlines() if isinstance(bloques, str) else list(bloques or [])
    return [x for x in (_limpio(b) for b in lineas if isinstance(b, str)) if x]


def _no_pasado(fecha: str, hoy: date) -> None:
    from .acciones import YA_PASO

    if date.fromisoformat(fecha) < hoy:
        raise ValueError(YA_PASO)


def _dia_que_viene(fecha: Any, hoy: date) -> str:
    """The ISO date of today or a day to come; ValueError for a bad or missing one or a day that is over (R-207)."""
    dia = _fecha(fecha)
    if dia is None:
        raise ValueError("Elige el día de la sesión (año-mes-día)")
    _no_pasado(dia, hoy)
    return dia


def _numero(valor: Any, que: str) -> int:
    if isinstance(valor, bool):
        valor = None
    try:
        numero = int(str(valor).strip()) if isinstance(valor, (int, str)) else None
    except ValueError:
        numero = None
    if numero is None or numero < 1:
        raise ValueError(f"El número de {que} tiene que ser 1 o más")
    return numero


_PREVISTO = (re.compile(r"^(\d+):([0-5]\d)$"), re.compile(r"^(\d+)\s*h\s*(\d{1,2})?'?$"), re.compile(r"^(\d+)\s*'?$"))


def _previsto(valor: Any) -> int | None:
    """The planned time of a week in seconds: «4:30» or «4h30» (hours and minutes), «4h», «270'» or «270» (minutes);
    empty, None. ValueError saying why for anything else."""
    texto = "".join(str(valor or "").split()).replace("min", "'")
    if not texto:
        return None
    horas_minutos, horas, minutos = (p.match(texto) for p in _PREVISTO)
    if horas_minutos:
        return int(horas_minutos.group(1)) * 3600 + int(horas_minutos.group(2)) * 60
    if horas:
        return int(horas.group(1)) * 3600 + int(horas.group(2) or 0) * 60
    if minutos:
        return int(minutos.group(1)) * 60
    raise ValueError(f"El previsto «{texto}» no se entiende: escríbelo en horas y minutos, como 4:30")


def _semana_nueva(datos: dict[str, Any], previsto_s: bool = False) -> dict[str, Any]:
    """`{n, desde, hasta, previsto_s, clave}` of a week typed by hand (`previsto_s`: `previsto` is already seconds);
    ValueError, with why, for a bad number, a missing or bad date or a planned time it does not understand."""
    n = _numero(datos.get("n"), "la semana")
    desde, hasta = _fecha(datos.get("desde")), _fecha(datos.get("hasta"))
    if desde is None or hasta is None:
        raise ValueError(f"La semana {n} necesita su fecha desde y su fecha hasta (año-mes-día)")
    previsto = datos.get("previsto")
    return {"n": n, "desde": desde, "hasta": hasta,
            "previsto_s": (int(previsto) if previsto is not None else None) if previsto_s else _previsto(previsto),
            "clave": _limpio(datos.get("clave"))}


def _carrera_nueva(carrera: dict[str, Any]) -> dict[str, Any]:
    """`{nombre, fecha, objetivo, estado}` of a race typed by hand; ValueError, with why, for an empty name or a bad
    date."""
    nombre = _limpio(carrera.get("nombre"))
    if not nombre:
        raise ValueError("Ponle un nombre a la carrera")
    return {"nombre": nombre, "fecha": _fecha(carrera.get("fecha")), "objetivo": _limpio(carrera.get("objetivo")),
            "estado": "pendiente"}


def titulo_incidencia(zona: str | None, lado: str | None) -> str:
    """«tobillo derecho, lateral»: how an incident is named."""
    return " ".join(x for x in (zona, lado) if x)


__all__ = ["ABIERTA", "APARTADOS_NUEVO", "CERRADA", "CONSERVA", "HECHA", "Planes", "con_hora", "hora_de", "texto_sesion",
           "titulo_incidencia"]
