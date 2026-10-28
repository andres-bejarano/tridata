"""Objective of the day (unit 007, R-31): what Andrés says when he opens the day.

Its own table `objetivo_dia` in the TriData SQLite store, one per day, the last one wins: type of
day, % with its sign (G-29, NULL when not said), margin levels, planned training (text; it does
NOT add kcal) and planned steps.

Unit 008 extends this table, not another one: column `previsto` (JSON: the activity chosen on the
screen as planned training, its minutes and its scaled kcal, which DO add to the expenditure), and
`cambiar(dia, **campos)` to change the %, the levels or the planned training of one day without
losing the rest. Levels: 1 to 5, each −50..+50 %; every change replaces the one before that day.

Unit 018 extends it again (no new table): column `repartido`, set by «Aplicar» of the Balance spread
(`cambiar(dia, porcentaje=…, repartido=True)`); any other change of the % of that day, or `guardar`, clears it.

Unit 021: `cambiar(dia, pasos=…)` changes only the planned steps of that day (`None`: back to the default steps
of Ajustes), checked as `objetivo_valido` does (`pasos_validos`); the assistant applies its card with it.

Unit 047: `previsto` keeps a LIST of planned trainings (natación, bici y carrera the same day), at most
`PREVISTOS_MAX`. A row saved before keeps ONE object and is read as a list of one, with no migration. `del_dia`
gives `previstos` (the list, `[]` without any) and `previsto` (the first or None, as before). `cambiar(dia,
previsto=obj|None)` leaves only that one or none, as always; `cambiar(dia, previstos=[…])` saves the whole list.
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
PREVISTOS_MAX = 10  # unit 047: planned trainings of one day
CAMPOS = ("tipo", "porcentaje", "niveles", "entreno", "pasos")
CAMBIABLES = ("porcentaje", "niveles", "previsto", "previstos", "pasos")
_COLUMNAS = "dia, tipo, porcentaje, niveles, entreno, pasos, previsto, repartido, creado"

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
    limpio = {"tipo": tipo.lower() if tipo else None, "porcentaje": porcentaje, "niveles": niveles,
              "entreno": entreno, "pasos": pasos_validos(datos.get("pasos"))}
    return limpio if any(v is not None for v in limpio.values()) else None


def pasos_validos(valor: Any) -> int | None:
    """The planned steps of a day: a whole number from 0 to 50,000, or None. `ValueError` says what is wrong.

    The one check of the screen of the day, of «nuevo día…» and of the assistant (unit 021, G-39)."""
    pasos_n = _numero(valor, "los pasos previstos")
    if pasos_n is not None and (not pasos_n.is_integer() or not PASOS[0] <= pasos_n <= PASOS[1]):
        raise ValueError(f"los pasos previstos tienen que ser un número entero entre {PASOS[0]} y {PASOS[1]}")
    return int(pasos_n) if pasos_n is not None else None


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
            if "repartido" not in columnas:  # unit 018: the % comes from the spread (idempotent)
                conn.execute("ALTER TABLE objetivo_dia ADD COLUMN repartido INTEGER")

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
                "pasos = excluded.pasos, repartido = NULL, creado = excluded.creado",
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
        datos["previstos"] = _lista_de_previstos(datos.pop("previsto"))
        datos["previsto"] = datos["previstos"][0] if datos["previstos"] else None
        if datos["porcentaje"] is not None:
            datos["porcentaje"] = _entero_si_puede(float(datos["porcentaje"]))
        datos["repartido"] = bool(datos["repartido"])
        return datos

    def cambiar(self, dia: date, *, repartido: bool = False, **campos: Any) -> dict:
        """Change the %, the levels, the planned training and/or the planned steps of `dia`, keeping the rest (unit 008;
        the steps, unit 021).

        Each one given replaces the one before for that day (`None` clears it: the plan's %, the
        default levels, no planned training). Everything is checked first: a `ValueError` changes nothing.
        Unit 018: a % given with `repartido=True` comes from the Balance spread; a % given without it does not.
        """
        if repartido and "porcentaje" not in campos:
            raise ValueError("solo un % del objetivo del día puede venir del reparto")
        if not campos:
            raise ValueError("no hay nada que cambiar en el objetivo del día")
        desconocidos = sorted(set(campos) - set(CAMBIABLES))
        if desconocidos:
            raise ValueError(f"no se puede cambiar {', '.join(desconocidos)} del objetivo del día")
        valores: dict[str, Any] = {}
        if "porcentaje" in campos:
            valores["porcentaje"] = _porcentaje(campos["porcentaje"], "el % del objetivo del día")
            valores["repartido"] = 1 if repartido and valores["porcentaje"] is not None else None
        if "niveles" in campos:
            valores["niveles"] = None if campos["niveles"] is None else json.dumps(niveles_validos(campos["niveles"]))
        if "previsto" in campos and "previstos" in campos:
            raise ValueError("el entreno previsto se cambia de una forma o de la otra, no de las dos")
        if "previsto" in campos:  # the old shape: only that one, or none
            valores["previsto"] = None if campos["previsto"] is None else json.dumps([previsto_valido(campos["previsto"])])
        if "previstos" in campos:
            lista = previstos_validos(campos["previstos"])
            valores["previsto"] = json.dumps(lista) if lista else None
        if "pasos" in campos:
            valores["pasos"] = pasos_validos(campos["pasos"])
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


def _lista_de_previstos(crudo: str | None) -> list[dict]:
    """The planned trainings of the column: a list, or the ONE object a row saved before unit 047 keeps."""
    if crudo is None:
        return []
    datos = json.loads(crudo)
    if isinstance(datos, dict):
        return [datos]
    return list(datos) if isinstance(datos, list) else []


def previstos_validos(crudos: Any) -> list[dict]:
    """The planned trainings of a day (unit 047): a list of 0 to `PREVISTOS_MAX`, each checked by `previsto_valido`."""
    if not isinstance(crudos, list):
        raise ValueError("los entrenos previstos tienen que ser una lista")
    if len(crudos) > PREVISTOS_MAX:
        raise ValueError(f"como mucho {PREVISTOS_MAX} entrenos previstos en un día")
    return [previsto_valido(p) for p in crudos]


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
