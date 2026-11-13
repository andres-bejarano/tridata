"""The conversations of the team (unit 032, REC-20 of `preguntar-equipo`): what stays on this PC (S-38).

Own tables in the same SQLite (as `nutricion/ajustes.py`):

- `conversacion`: its title, when it was created and when it was last used, and its role (unit 041);
- `mensaje`: who (`andres` | `asistente`), the text, the hour and the role that wrote it.

Unit 083 (P-20261113-33e057c3): without the paid AI, the only writer is the coach of the PC, which leaves each change
it makes in the conversation «Desde el PC» (`desde_pc.py`), and the app only reads it («Lo que ha hecho el coach»,
`web/conversaciones.py`). What the Asistente of the app kept (its other conversations, the model and the token use of
each answer, its cards, the model of each role in `modelo_papel`, the route attached in `conversacion_ruta` and what
each call to the API cost in `gasto_asistente`) stays in the base as it was: the tables are still created if missing
(a base of before opens as it is, a new one has the same tables) and nothing here deletes a row of them.

Nothing here talks to an AI. The logs of this package carry ids and counts, never a text (R12).
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .papeles import PAPELES, POR_DEFECTO

LARGO_AVANCE = 140  # how much of a message a list shows

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    titulo_auto INTEGER NOT NULL DEFAULT 1,
    creada TEXT NOT NULL,
    usada TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mensaje (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversacion_id INTEGER NOT NULL REFERENCES conversacion(id) ON DELETE CASCADE,
    quien TEXT NOT NULL CHECK (quien IN ('andres', 'asistente')),
    texto TEXT NOT NULL,
    hora TEXT NOT NULL,
    contexto_hasta TEXT,
    sin_respuesta INTEGER NOT NULL DEFAULT 0,
    modelo TEXT,
    tokens_entrada INTEGER,
    tokens_salida INTEGER,
    tokens_cache_escritura INTEGER,
    tokens_cache_lectura INTEGER,
    tarjetas TEXT NOT NULL DEFAULT '[]',
    propuesto TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS mensaje_de_conversacion ON mensaje (conversacion_id, id);
CREATE TABLE IF NOT EXISTS modelo_papel (
    papel TEXT PRIMARY KEY,
    modelo TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversacion_ruta (
    conversacion_id INTEGER PRIMARY KEY REFERENCES conversacion(id) ON DELETE CASCADE,
    perfil TEXT NOT NULL,
    adjuntada TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gasto_asistente (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hora TEXT NOT NULL,
    mes TEXT NOT NULL,
    que TEXT NOT NULL,
    modelo TEXT NOT NULL,
    tokens_entrada INTEGER NOT NULL,
    tokens_salida INTEGER NOT NULL,
    tokens_cache_escritura INTEGER NOT NULL,
    tokens_cache_lectura INTEGER NOT NULL,
    dolares REAL NOT NULL
);
"""


class NoEsta(LookupError):
    """That conversation (or message) does not exist: a 404."""


def avance(texto: str, largo: int = LARGO_AVANCE) -> str:
    """The beginning of an answer, in one line, cut at a word with «…»."""
    plano = " ".join(texto.split())
    if len(plano) <= largo:
        return plano
    corte = plano[:largo].rsplit(" ", 1)[0]
    return corte.rstrip(" ,.;:") + "…"


def _hora(cuando: datetime) -> str:
    return cuando.isoformat(timespec="seconds")


# unit 041: columns added to the tables of unit 032 when they are missing (table, column, its definition)
_COLUMNAS_NUEVAS = (
    ("conversacion", "papel", f"TEXT NOT NULL DEFAULT '{POR_DEFECTO}'"),
    ("mensaje", "papel", "TEXT"),
)


def _conversacion(fila: sqlite3.Row) -> dict[str, Any]:
    return {"id": fila["id"], "titulo": fila["titulo"], "creada": fila["creada"], "usada": fila["usada"],
            "papel": fila["papel"]}


def _papel_valido(papel: str) -> str:
    if papel not in PAPELES:
        raise ValueError(f"No hay nadie en el equipo que se llame {papel!r}.")
    return papel


def _mensaje(fila: sqlite3.Row) -> dict[str, Any]:
    uso = None
    if fila["tokens_entrada"] is not None:
        uso = {"entrada": fila["tokens_entrada"], "salida": fila["tokens_salida"],
               "cache_escritura": fila["tokens_cache_escritura"], "cache_lectura": fila["tokens_cache_lectura"]}
    return {"id": fila["id"], "conversacion_id": fila["conversacion_id"], "quien": fila["quien"],
            "texto": fila["texto"], "hora": fila["hora"], "contexto_hasta": fila["contexto_hasta"],
            "sin_respuesta": bool(fila["sin_respuesta"]), "modelo": fila["modelo"], "uso": uso,
            # an answer of before unit 041 is the Asistente's; Andrés's messages have no role
            "papel": fila["papel"] or (POR_DEFECTO if fila["quien"] == "asistente" else None),
            "tarjetas": json.loads(fila["tarjetas"]), "propuesto": json.loads(fila["propuesto"])}


class Conversaciones:
    """Repository of the conversations and their messages."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            for tabla, columna, definicion in _COLUMNAS_NUEVAS:
                if columna not in {c["name"] for c in conn.execute(f"PRAGMA table_info({tabla})")}:
                    conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")

    @property
    def db_path(self) -> Path:
        return self._db_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- conversations -----------------------------------------------------------------------------------------------

    def crear(self, titulo: str, ahora: datetime, papel: str = POR_DEFECTO) -> dict[str, Any]:
        """A new conversation, of `papel` (R1); ValueError if that role does not exist."""
        _papel_valido(papel)
        with self._connect() as conn:
            cur = conn.execute("INSERT INTO conversacion (titulo, titulo_auto, creada, usada, papel) "
                               "VALUES (?, 1, ?, ?, ?)", (titulo, _hora(ahora), _hora(ahora), papel))
            return _conversacion(conn.execute("SELECT * FROM conversacion WHERE id = ?", (cur.lastrowid,)).fetchone())

    def conversacion(self, ident: int) -> dict[str, Any]:
        with self._connect() as conn:
            fila = conn.execute("SELECT * FROM conversacion WHERE id = ?", (ident,)).fetchone()
        if fila is None:
            raise NoEsta(ident)
        return _conversacion(fila)

    def lista(self) -> list[dict[str, Any]]:
        """Newest use first (R-112), each with the beginning of its last message (`ultima`)."""
        with self._connect() as conn:
            filas = conn.execute(
                "SELECT c.*, (SELECT m.texto FROM mensaje m WHERE m.conversacion_id = c.id "
                "ORDER BY m.id DESC LIMIT 1) AS ultima FROM conversacion c ORDER BY c.usada DESC, c.id DESC"
            ).fetchall()
        return [{**_conversacion(f), "ultima": avance(f["ultima"]) if f["ultima"] else ""} for f in filas]

    def renombrar(self, ident: int, titulo: str) -> dict[str, Any]:
        """Andrés's title: from now on the app never changes it (R-120)."""
        titulo = " ".join(titulo.split())
        if not titulo:
            raise ValueError("El título no puede quedar vacío.")
        with self._connect() as conn:
            if conn.execute("UPDATE conversacion SET titulo = ?, titulo_auto = 0 WHERE id = ?",
                            (titulo[:120], ident)).rowcount == 0:
                raise NoEsta(ident)
        return self.conversacion(ident)

    # -- messages ----------------------------------------------------------------------------------------------------

    def mensajes(self, ident: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [_mensaje(f) for f in conn.execute(
                "SELECT * FROM mensaje WHERE conversacion_id = ? ORDER BY id", (ident,))]

    def anadir_mensaje(self, ident: int, quien: str, texto: str, ahora: datetime, *, sin_respuesta: bool = False,
                       contexto_hasta: datetime | None = None, modelo: str | None = None,
                       uso: dict[str, int] | None = None, tarjetas: list[dict] | None = None,
                       papel: str | None = None) -> dict[str, Any]:
        """`papel`: the role that gave an answer (unit 041, R3); None for Andrés's messages."""
        uso = uso or {}
        propuesto = [t.get("texto", "") for t in tarjetas or [] if t.get("tipo") in ("propuesta", "elegir")]
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO mensaje (conversacion_id, quien, texto, hora, contexto_hasta, sin_respuesta, modelo, "
                "tokens_entrada, tokens_salida, tokens_cache_escritura, tokens_cache_lectura, tarjetas, propuesto, "
                "papel) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ident, quien, texto, _hora(ahora), _hora(contexto_hasta) if contexto_hasta else None,
                 int(sin_respuesta), modelo, uso.get("entrada"), uso.get("salida"), uso.get("cache_escritura"),
                 uso.get("cache_lectura"), json.dumps(tarjetas or [], ensure_ascii=False),
                 json.dumps(propuesto, ensure_ascii=False), papel))
            conn.execute("UPDATE conversacion SET usada = ? WHERE id = ?", (_hora(ahora), ident))
            return _mensaje(conn.execute("SELECT * FROM mensaje WHERE id = ?", (cur.lastrowid,)).fetchone())


__all__ = ["LARGO_AVANCE", "Conversaciones", "NoEsta", "avance"]
