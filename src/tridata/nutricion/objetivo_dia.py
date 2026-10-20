"""Objective of the day (unit 007, R-31): what Andrés says when he opens the day.

Its own table `objetivo_dia` in the TriData SQLite store, one per day, the last one wins: type of
day, % with its sign (G-29, NULL when not said), margin levels, planned training (text; it does
NOT add kcal) and planned steps.

Unit 008 extends this table, not another one: column `previsto` (JSON: the activity chosen on the
screen as planned training, its minutes and its scaled kcal, which DO add to the expenditure), and
`cambiar(dia, **campos)` to change the %, the levels or the planned training of one day without
losing the rest. Levels: 1 to 5, each −50..+50 %; every change replaces the one before that day.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..storage import DEFAULT_DB_PATH

PORCENTAJE = (-50, 50)  # the same range as the plan target in Ajustes
PASOS = (0, 50_000)
NIVELES_MAX = 5
CAMPOS = ("tipo", "porcentaje", "niveles", "entreno", "pasos")
CAMBIABLES = ("porcentaje", "niveles", "previsto")
_COLUMNAS = "dia, tipo, porcentaje, niveles, entreno, pasos, previsto, creado"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS objetivo_dia (
    dia TEXT PRIMARY KEY,
    tipo TEXT,
    porcentaje REAL,
    niveles TEXT,
    entreno TEXT,
    pasos INTEGER,
    creado TEXT NOT NULL
);
"""


def _numero(valor: Any, que: str) -> float | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, bool):
        raise ValueError(f"{que}: {valor!r} no es un número")
    try:
        numero = float(valor.strip().replace(",", ".").replace("−", "-")) if isinstance(valor, str) else float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{que}: {valor!r} no es un número") from None
    if not math.isfinite(numero):
        raise ValueError(f"{que}: {valor!r} no es un número")
    return numero


def _entero_si_puede(n: float) -> float | int:
    return int(n) if n.is_integer() else n


def _porcentaje(valor: Any, que: str) -> float | int | None:
    n = _numero(valor, que)
    if n is None:
        return None
    if not PORCENTAJE[0] <= n <= PORCENTAJE[1]:
        raise ValueError(f"{que} tiene que estar entre {PORCENTAJE[0]} y {PORCENTAJE[1]} %")
    return _entero_si_puede(n)


def _texto(valor: Any, que: str, largo: int) -> str | None:
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ValueError(f"{que} no es un texto")
    limpio = valor.strip()
    if len(limpio) > largo:
        raise ValueError(f"{que} es demasiado largo (máximo {largo} caracteres)")
    return limpio or None


def objetivo_valido(datos: Mapping[str, Any]) -> dict | None:
    """The checked, normalized objective; None if it says nothing. `ValueError` says what is wrong."""
    if not isinstance(datos, Mapping):
        raise ValueError("el objetivo del día tiene que ser un objeto")
    tipo = _texto(datos.get("tipo"), "el tipo de día", 40)
    porcentaje = _porcentaje(datos.get("porcentaje"), "el % del objetivo del día")
    crudos = datos.get("niveles")
    if crudos is None or crudos == []:
        niveles = None
    elif not isinstance(crudos, list):
        raise ValueError("los niveles del día tienen que ser una lista de %")
    else:
        niveles = [_porcentaje(n, "los niveles del día (%)") for n in crudos]
        niveles = [n for n in niveles if n is not None] or None
        if niveles is not None and len(niveles) > NIVELES_MAX:
            raise ValueError(f"los niveles del día son como mucho {NIVELES_MAX}")
    entreno = _texto(datos.get("entreno"), "el entreno previsto", 200)
    pasos_n = _numero(datos.get("pasos"), "los pasos previstos")
    if pasos_n is not None and (not pasos_n.is_integer() or not PASOS[0] <= pasos_n <= PASOS[1]):
        raise ValueError(f"los pasos previstos tienen que ser un número entero entre {PASOS[0]} y {PASOS[1]}")
    limpio = {"tipo": tipo.lower() if tipo else None, "porcentaje": porcentaje, "niveles": niveles,
              "entreno": entreno, "pasos": int(pasos_n) if pasos_n is not None else None}
    return limpio if any(v is not None for v in limpio.values()) else None


class ObjetivosDia:
    """Repository of the objective of each day over the TriData SQLite file."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            columnas = {fila["name"] for fila in conn.execute("PRAGMA table_info(objetivo_dia)")}
            if "previsto" not in columnas:  # the table as unit 007 created it (idempotent)
                conn.execute("ALTER TABLE objetivo_dia ADD COLUMN previsto TEXT")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def guardar(self, dia: date, objetivo: Mapping[str, Any]) -> dict:
        """Save the objective of `dia`, replacing the one there was (the last one wins).

        The planned training chosen on the screen (`previsto`, unit 008) is kept."""
        limpio = objetivo_valido(objetivo)
        if limpio is None:
            raise ValueError("el objetivo del día está vacío")
        ahora = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO objetivo_dia (dia, tipo, porcentaje, niveles, entreno, pasos, creado) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(dia) DO UPDATE SET tipo = excluded.tipo, "
                "porcentaje = excluded.porcentaje, niveles = excluded.niveles, entreno = excluded.entreno, "
                "pasos = excluded.pasos, creado = excluded.creado",
                (dia.isoformat(), limpio["tipo"], limpio["porcentaje"],
                 None if limpio["niveles"] is None else json.dumps(limpio["niveles"]),
                 limpio["entreno"], limpio["pasos"], ahora),
            )
        return {"dia": dia.isoformat(), **limpio, "creado": ahora}

    def del_dia(self, dia: date) -> dict | None:
        with self._connect() as conn:
            fila = conn.execute(f"SELECT {_COLUMNAS} FROM objetivo_dia WHERE dia = ?",  # noqa: S608 - fixed
                                (dia.isoformat(),)).fetchone()
        if fila is None:
            return None
        datos = dict(fila)
        datos["niveles"] = None if datos["niveles"] is None else json.loads(datos["niveles"])
        datos["previsto"] = None if datos["previsto"] is None else json.loads(datos["previsto"])
        if datos["porcentaje"] is not None:
            datos["porcentaje"] = _entero_si_puede(float(datos["porcentaje"]))
        return datos

    def cambiar(self, dia: date, **campos: Any) -> dict:
        """Change the %, the levels and/or the planned training of `dia`, keeping the rest (unit 008).

        Each one given replaces the one before for that day (`None` clears it: the plan's %, the
        default levels, no planned training). Everything is checked first: a `ValueError` changes nothing.
        """
        if not campos:
            raise ValueError("no hay nada que cambiar en el objetivo del día")
        desconocidos = sorted(set(campos) - set(CAMBIABLES))
        if desconocidos:
            raise ValueError(f"no se puede cambiar {', '.join(desconocidos)} del objetivo del día")
        valores: dict[str, Any] = {}
        if "porcentaje" in campos:
            valores["porcentaje"] = _porcentaje(campos["porcentaje"], "el % del objetivo del día")
        if "niveles" in campos:
            valores["niveles"] = None if campos["niveles"] is None else json.dumps(niveles_validos(campos["niveles"]))
        if "previsto" in campos:
            valores["previsto"] = None if campos["previsto"] is None else json.dumps(previsto_valido(campos["previsto"]))
        ahora = datetime.now().isoformat(timespec="seconds")
        nombres = list(valores)
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO objetivo_dia (dia, creado) VALUES (?, ?)", (dia.isoformat(), ahora))
            conn.execute(
                f"UPDATE objetivo_dia SET {', '.join(f'{n} = ?' for n in nombres)}, creado = ? "  # noqa: S608 - fixed
                "WHERE dia = ?",
                (*[valores[n] for n in nombres], ahora, dia.isoformat()),
            )
        resultado = self.del_dia(dia)
        if resultado is None:  # the row was just written
            raise RuntimeError("SQLite did not keep the objective of the day")
        return resultado


def niveles_validos(crudos: Any) -> list[float | int]:
    """The levels chosen for a day: 1 to 5, each −50..+50 %, without repeats. `ValueError` otherwise."""
    if not isinstance(crudos, list) or not crudos:
        raise ValueError(f"los niveles del día son una lista de 1 a {NIVELES_MAX} %")
    niveles = [_porcentaje(n, "los niveles del día (%)") for n in crudos]
    if any(n is None for n in niveles):
        raise ValueError("algún nivel del día está vacío")
    limpios = sorted({n for n in niveles if n is not None})
    if len(limpios) > NIVELES_MAX:
        raise ValueError(f"los niveles del día son como mucho {NIVELES_MAX}")
    return limpios


def previsto_valido(datos: Any) -> dict:
    """The planned training as saved: activity, minutes and scaled kcal (computed by the caller)."""
    if not isinstance(datos, Mapping):
        raise ValueError("el entreno previsto tiene que ser un objeto")
    actividad = datos.get("actividad_id")
    if not isinstance(actividad, str) or not actividad.strip():
        raise ValueError("falta la actividad del entreno previsto")
    minutos = _numero(datos.get("minutos"), "los minutos del entreno previsto")
    kcal = _numero(datos.get("kcal"), "las kcal del entreno previsto")
    if minutos is None or not 0 < minutos <= 600:
        raise ValueError("los minutos del entreno previsto tienen que estar entre 1 y 600")
    if kcal is None or kcal < 0:
        raise ValueError("las kcal del entreno previsto no valen")
    limpio = {"actividad_id": actividad.strip(), "minutos": _entero_si_puede(minutos), "kcal": kcal}
    for clave in ("nombre", "tipo", "fecha"):
        texto = _texto(datos.get(clave), f"el campo {clave} del entreno previsto", 200)
        if texto is not None:
            limpio[clave] = texto
    return limpio
