"""The Asistente's conversations (unit 032, REC-20 of `preguntar-equipo`): what stays on this PC (S-38).

Own tables in the same SQLite (as `nutricion/ajustes.py`):

- `conversacion`: its title (`titulo_auto` while the app put it: Claude may give a better one with the first answer;
  once Andrés changes it, nobody else does, R-120), when it was created and when it was last used;
- `mensaje`: who (`andres` | `asistente`), the text, the hour, until when the context reached (`contexto_hasta`,
  R-115), whether it was left without an answer (`sin_respuesta`, R-117), the model and the token use of an answer
  and the cards of change it brought (`tarjetas`: the ones still waiting; `propuesto`: what each one said, kept
  so the next requests tell Claude what it proposed, R-96);
- `gasto_asistente`: what each call to the API cost, with the use the API gave back (R9). Deleting a conversation
  never gives back what was spent: the cap of the month counts every call.

Nothing here talks to the API. The logs of this package carry ids, counts and token use, never a text (R12).
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

TOPE_DOLARES_MES = 5.0  # decided by Andrés on 2026-10-23: at 5 $ it warns and sends nothing more that month
LARGO_TITULO = 30
LARGO_AVANCE = 140  # how much of the last answer the list and the card of Hoy show (R-97)
# $ per million tokens: (input, output, cache write 5 min, cache read). Official prices of the SDK reference
# (2026-10-06): Haiku 5.5 0.10 / 0.50 up to 100K tokens, Sonnet 5.5 2 / 10; a write is 1.25x and a read 0.1x the input.
PRECIOS: dict[str, tuple[float, float, float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.20),
}
# Haiku 5.5 above 100K tokens in one call (input + cache write + cache read): 0.50 / 2.50, write 1.25x and read 0.1x
# the input (bug 038; SDK reference 2026-10-06). The tier goes per call, so each round is priced on its own.
LARGO_DESDE = 100_000
PRECIOS_LARGOS: dict[str, tuple[float, float, float, float]] = {
    "claude-haiku-5-5": (0.50, 2.50, 0.625, 0.05),
}
_MAS_CARO = "claude-sonnet-5-5"  # an unknown model is counted at the dearest price: the cap never falls short
USO_VACIO = {"entrada": 0, "salida": 0, "cache_escritura": 0, "cache_lectura": 0}

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


def dolares(modelo: str, uso: dict[str, int]) -> float:
    """What ONE call cost, with the use the API gave back (R9); above `LARGO_DESDE` tokens in, its long tier."""
    entrada, salida, escritura, lectura = PRECIOS.get(modelo, PRECIOS[_MAS_CARO])
    if uso.get("entrada", 0) + uso.get("cache_escritura", 0) + uso.get("cache_lectura", 0) > LARGO_DESDE:
        entrada, salida, escritura, lectura = PRECIOS_LARGOS.get(modelo, (entrada, salida, escritura, lectura))
    return (uso.get("entrada", 0) * entrada + uso.get("salida", 0) * salida
            + uso.get("cache_escritura", 0) * escritura + uso.get("cache_lectura", 0) * lectura) / 1_000_000


def titulo_provisional(texto: str) -> str:
    """The beginning of the first message, cut at a word (R-120): «Quiero hacer 10 km el 3 de…». Claude usually puts
    a better one with its first answer (`poner_titulo`)."""
    palabras = texto.split()
    if not palabras:
        return "Conversación"
    titulo = palabras[0]
    for palabra in palabras[1:]:
        if len(titulo) + 1 + len(palabra) > LARGO_TITULO:
            titulo += "…"
            break
        titulo += " " + palabra
    return titulo[0].upper() + titulo[1:]


def avance(texto: str, largo: int = LARGO_AVANCE) -> str:
    """The beginning of an answer, in one line, cut at a word with «…»."""
    plano = " ".join(texto.split())
    if len(plano) <= largo:
        return plano
    corte = plano[:largo].rsplit(" ", 1)[0]
    return corte.rstrip(" ,.;:") + "…"


def _mes(cuando: datetime) -> str:
    return cuando.strftime("%Y-%m")


def _hora(cuando: datetime) -> str:
    return cuando.isoformat(timespec="seconds")


def _conversacion(fila: sqlite3.Row) -> dict[str, Any]:
    return {"id": fila["id"], "titulo": fila["titulo"], "creada": fila["creada"], "usada": fila["usada"]}


def _mensaje(fila: sqlite3.Row) -> dict[str, Any]:
    uso = None
    if fila["tokens_entrada"] is not None:
        uso = {"entrada": fila["tokens_entrada"], "salida": fila["tokens_salida"],
               "cache_escritura": fila["tokens_cache_escritura"], "cache_lectura": fila["tokens_cache_lectura"]}
    return {"id": fila["id"], "conversacion_id": fila["conversacion_id"], "quien": fila["quien"],
            "texto": fila["texto"], "hora": fila["hora"], "contexto_hasta": fila["contexto_hasta"],
            "sin_respuesta": bool(fila["sin_respuesta"]), "modelo": fila["modelo"], "uso": uso,
            "tarjetas": json.loads(fila["tarjetas"]), "propuesto": json.loads(fila["propuesto"])}


class Conversaciones:
    """Repository of the conversations, their messages and what the API cost."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

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

    def crear(self, titulo: str, ahora: datetime) -> dict[str, Any]:
        with self._connect() as conn:
            cur = conn.execute("INSERT INTO conversacion (titulo, titulo_auto, creada, usada) VALUES (?, 1, ?, ?)",
                               (titulo, _hora(ahora), _hora(ahora)))
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

    def titular_sola(self, ident: int, titulo: str) -> bool:
        """Claude's title for a conversation whose title the app put; never over Andrés's."""
        titulo = " ".join(titulo.split())[:LARGO_TITULO * 2]
        if not titulo:
            return False
        with self._connect() as conn:
            return conn.execute("UPDATE conversacion SET titulo = ? WHERE id = ? AND titulo_auto = 1",
                                (titulo, ident)).rowcount == 1

    def usar(self, ident: int, ahora: datetime) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE conversacion SET usada = ? WHERE id = ?", (_hora(ahora), ident))

    def borrar(self, ident: int) -> None:
        """The whole conversation and its messages; nothing else (R-118)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM mensaje WHERE conversacion_id = ?", (ident,))
            if conn.execute("DELETE FROM conversacion WHERE id = ?", (ident,)).rowcount == 0:
                raise NoEsta(ident)

    # -- messages ----------------------------------------------------------------------------------------------------

    def mensajes(self, ident: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [_mensaje(f) for f in conn.execute(
                "SELECT * FROM mensaje WHERE conversacion_id = ? ORDER BY id", (ident,))]

    def mensaje(self, mensaje_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            fila = conn.execute("SELECT * FROM mensaje WHERE id = ?", (mensaje_id,)).fetchone()
        if fila is None:
            raise NoEsta(mensaje_id)
        return _mensaje(fila)

    def anadir_mensaje(self, ident: int, quien: str, texto: str, ahora: datetime, *, sin_respuesta: bool = False,
                       contexto_hasta: datetime | None = None, modelo: str | None = None,
                       uso: dict[str, int] | None = None, tarjetas: list[dict] | None = None) -> dict[str, Any]:
        uso = uso or {}
        propuesto = [t.get("texto", "") for t in tarjetas or [] if t.get("tipo") in ("propuesta", "elegir")]
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO mensaje (conversacion_id, quien, texto, hora, contexto_hasta, sin_respuesta, modelo, "
                "tokens_entrada, tokens_salida, tokens_cache_escritura, tokens_cache_lectura, tarjetas, propuesto) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ident, quien, texto, _hora(ahora), _hora(contexto_hasta) if contexto_hasta else None,
                 int(sin_respuesta), modelo, uso.get("entrada"), uso.get("salida"), uso.get("cache_escritura"),
                 uso.get("cache_lectura"), json.dumps(tarjetas or [], ensure_ascii=False),
                 json.dumps(propuesto, ensure_ascii=False)))
            conn.execute("UPDATE conversacion SET usada = ? WHERE id = ?", (_hora(ahora), ident))
            return _mensaje(conn.execute("SELECT * FROM mensaje WHERE id = ?", (cur.lastrowid,)).fetchone())

    def respondido(self, mensaje_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("UPDATE mensaje SET sin_respuesta = 0 WHERE id = ?", (mensaje_id,))
        return self.mensaje(mensaje_id)

    def quitar_mensaje(self, mensaje_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM mensaje WHERE id = ?", (mensaje_id,))

    def poner_tarjetas(self, mensaje_id: int, tarjetas: list[dict]) -> dict[str, Any]:
        """The cards still waiting on an answer (one confirmed or discarded goes, R-96)."""
        with self._connect() as conn:
            if conn.execute("UPDATE mensaje SET tarjetas = ? WHERE id = ? AND quien = 'asistente'",
                            (json.dumps(tarjetas, ensure_ascii=False), mensaje_id)).rowcount == 0:
                raise NoEsta(mensaje_id)
        return self.mensaje(mensaje_id)

    def ultima_respuesta(self) -> dict[str, Any] | None:
        """The last answer of the assistant, for the card of Hoy (R-97, R-98)."""
        with self._connect() as conn:
            fila = conn.execute(
                "SELECT m.texto, c.id, c.titulo FROM mensaje m JOIN conversacion c ON c.id = m.conversacion_id "
                "WHERE m.quien = 'asistente' ORDER BY m.id DESC LIMIT 1").fetchone()
        if fila is None:
            return None
        return {"texto": avance(fila["texto"]), "conversacion_id": fila["id"], "titulo": fila["titulo"]}

    # -- what it costs (R9) ------------------------------------------------------------------------------------------

    def apuntar_gasto(self, ahora: datetime, modelo: str, uso: dict[str, int], que: str) -> float:
        coste = dolares(modelo, uso)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO gasto_asistente (hora, mes, que, modelo, tokens_entrada, tokens_salida, "
                "tokens_cache_escritura, tokens_cache_lectura, dolares) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_hora(ahora), _mes(ahora), que, modelo, uso.get("entrada", 0), uso.get("salida", 0),
                 uso.get("cache_escritura", 0), uso.get("cache_lectura", 0), coste))
        return coste

    def gasto_del_mes(self, ahora: datetime) -> float:
        with self._connect() as conn:
            (total,) = conn.execute("SELECT COALESCE(SUM(dolares), 0) FROM gasto_asistente WHERE mes = ?",
                                    (_mes(ahora),)).fetchone()
        return float(total)

    def tope_alcanzado(self, ahora: datetime, tope: float = TOPE_DOLARES_MES) -> bool:
        return self.gasto_del_mes(ahora) >= tope


__all__ = ["LARGO_AVANCE", "PRECIOS", "TOPE_DOLARES_MES", "USO_VACIO", "Conversaciones", "NoEsta", "avance",
           "dolares", "titulo_provisional"]
