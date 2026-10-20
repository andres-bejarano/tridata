"""Objective of the day (unit 007, R-31): what Andrés says when he opens the day.

Its own table `objetivo_dia` in the TriData SQLite store, one per day, the last one wins: type of
day, % with its sign (G-29, NULL when not said), margin levels, planned training (text; it does
NOT add kcal yet, R-15 is unit 008) and planned steps. Unit 008 extends this table, not another one.
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
CAMPOS = ("tipo", "porcentaje", "niveles", "entreno", "pasos")

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
        """Save the objective of `dia`, replacing the one there was (the last one wins)."""
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
            fila = conn.execute(
                "SELECT dia, tipo, porcentaje, niveles, entreno, pasos, creado FROM objetivo_dia WHERE dia = ?",
                (dia.isoformat(),),
            ).fetchone()
        if fila is None:
            return None
        datos = dict(fila)
        datos["niveles"] = None if datos["niveles"] is None else json.loads(datos["niveles"])
        if datos["porcentaje"] is not None:
            datos["porcentaje"] = _entero_si_puede(float(datos["porcentaje"]))
        return datos
