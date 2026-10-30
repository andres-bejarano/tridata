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
"""
from __future__ import annotations

import json
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
ABIERTA, CERRADA = "abierta", "cerrada"


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
                                    ("plan_carrera", _RESULTADO), ("plan_sesion", _DESDE_GARMIN)):
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
            extra = conn.execute("SELECT estrategia, resultado FROM plan_carrera WHERE plan_id = ? ORDER BY orden",
                                 (ident,)).fetchall()
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
                "bloques": json.loads(f["bloques"]), "clave": bool(f["clave"]),
                **{columna: f[columna] for columna, _ in _RELOJ}}

    def sesiones_reloj(self, plan_id: int) -> list[dict[str, Any]]:
        """`[{id, fecha, titulo, estado, enviada, aviso}]` of the sessions of a plan, in the order of `leer`: `estado`
        is "al_dia" (in the watch as it is now), "cambiada" (changed since it was sent) or None (never sent);
        `aviso`, the warning of the last pass with Garmin (unit 061) or None."""
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
                           "enviada": f["garmin_enviada"] if estado else None, "aviso": f["garmin_aviso"]})
        return salida

    def marcar_garmin(self, ident: int, workout_id: int | None, schedule_id: int | None,
                      enviada: datetime | None = None, texto: str | None = None) -> None:
        """What is in Garmin of that session now (all None: nothing)."""
        with self._connect() as conn:
            conn.execute("UPDATE plan_sesion SET garmin_workout_id = ?, garmin_schedule_id = ?, garmin_enviada = ?, "
                         "garmin_texto = ? WHERE rowid = ?",
                         (workout_id, schedule_id, enviada.isoformat(timespec="seconds") if enviada else None,
                          texto, ident))

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
                    conn.execute(
                        f"INSERT INTO plan_sesion (plan_id, orden, apartado, clave, {', '.join(columnas)}) "  # noqa: S608
                        f"VALUES (?, ?, ?, 0, {', '.join('?' * len(columnas))})",
                        (plan_id, orden + i, apartado, *(_a_la_base("plan_sesion", c, nueva[c]) for c in columnas)))
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


def titulo_incidencia(zona: str | None, lado: str | None) -> str:
    """«tobillo derecho, lateral»: how an incident is named."""
    return " ".join(x for x in (zona, lado) if x)


__all__ = ["ABIERTA", "CERRADA", "HECHA", "Planes", "texto_sesion", "titulo_incidencia"]
