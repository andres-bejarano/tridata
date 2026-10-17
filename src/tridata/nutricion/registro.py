"""Saved foods and interpretations, in their own tables of the TriData SQLite store."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..storage import DEFAULT_DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interpretacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hora TEXT NOT NULL,
    texto TEXT NOT NULL,
    confirmada_sin_cambios INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alimento_apuntado (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dia TEXT NOT NULL,
    nombre TEXT NOT NULL,
    cantidad REAL,
    unidad TEXT,
    kcal REAL,
    proteina_g REAL,
    hidratos_g REAL,
    grasa_g REAL,
    estado TEXT NOT NULL CHECK (estado IN ('comido', 'planeado')),
    estimado INTEGER NOT NULL,
    supuestos TEXT NOT NULL,
    pendiente_cantidad INTEGER NOT NULL,
    interpretacion_id INTEGER REFERENCES interpretacion(id),
    creado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alimento_apuntado_dia ON alimento_apuntado(dia);
"""

_COLUMNAS = (
    "id, dia, nombre, cantidad, unidad, kcal, proteina_g, hidratos_g, grasa_g, estado, estimado, "
    "supuestos, pendiente_cantidad, creado"
)


def _numero(datos: dict, clave: str) -> float | None:
    valor = datos.get(clave)
    if valor is None or valor == "":
        return None
    if isinstance(valor, bool):
        raise ValueError(f"{clave}: {valor!r} no es un número")
    try:
        return float(str(valor).replace(",", ".")) if isinstance(valor, str) else float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{clave}: {valor!r} no es un número") from None


@dataclass
class Alimento:
    """One food as reviewed by Andrés, before it is saved."""

    nombre: str
    dia: date
    cantidad: float | None = None
    unidad: str | None = None
    kcal: float | None = None
    proteina_g: float | None = None
    hidratos_g: float | None = None
    grasa_g: float | None = None
    estimado: bool = False
    dudoso: bool = False
    supuestos: list[str] = field(default_factory=list)

    @property
    def pendiente_cantidad(self) -> bool:
        return self.cantidad is None

    @classmethod
    def desde_dict(cls, datos: dict) -> Alimento:
        """Validate what the page sends back; ValueError says what is wrong."""
        if not isinstance(datos, dict):
            raise ValueError("cada alimento tiene que ser un objeto")
        nombre = datos.get("nombre")
        if not isinstance(nombre, str) or not nombre.strip():
            raise ValueError("un alimento no tiene nombre")
        try:
            dia = date.fromisoformat(str(datos.get("dia")))
        except ValueError:
            raise ValueError(f"{nombre}: día {datos.get('dia')!r} no válido") from None
        numeros = {c: _numero(datos, c) for c in ("cantidad", "kcal", "proteina_g", "hidratos_g", "grasa_g")}
        if numeros["cantidad"] is not None and numeros["cantidad"] <= 0:
            raise ValueError(f"{nombre}: la cantidad tiene que ser mayor que 0")
        if any(v is not None and v < 0 for v in numeros.values()):
            raise ValueError(f"{nombre}: hay un número negativo")
        unidad = datos.get("unidad")
        if unidad is not None and not isinstance(unidad, str):
            raise ValueError(f"{nombre}: unidad no válida")
        supuestos = datos.get("supuestos") or []
        if not isinstance(supuestos, list) or not all(isinstance(s, str) for s in supuestos):
            raise ValueError(f"{nombre}: supuestos no válidos")
        return cls(
            nombre=nombre.strip(), dia=dia, unidad=(unidad or "").strip() or None,
            estimado=bool(datos.get("estimado", False)), dudoso=bool(datos.get("dudoso", False)),
            supuestos=supuestos, **numeros,
        )


class RegistroComidas:
    """Repository of the food log over the TriData SQLite file."""

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

    def guardar(self, alimentos: list[Alimento], hoy: date, texto: str, sin_cambios: bool) -> list[dict]:
        """Save a confirmed list, food by food, plus its interpretation. All or nothing.

        A food for a later day is `planeado` there; today (or a past day) it is `comido`.
        """
        if not alimentos:
            raise ValueError("la lista está vacía")
        for a in alimentos:
            if a.dudoso:
                raise ValueError(f"«{a.nombre}» está marcado como dudoso: corrígelo o quítalo antes de confirmar")
            if a.cantidad is not None and a.kcal is None:
                raise ValueError(f"«{a.nombre}» tiene cantidad pero no kcal")
        ahora = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO interpretacion (hora, texto, confirmada_sin_cambios) VALUES (?, ?, ?)",
                (ahora, texto, int(sin_cambios)),
            )
            interpretacion_id = cur.lastrowid
            for a in alimentos:
                conn.execute(
                    "INSERT INTO alimento_apuntado (dia, nombre, cantidad, unidad, kcal, proteina_g, hidratos_g, "
                    "grasa_g, estado, estimado, supuestos, pendiente_cantidad, interpretacion_id, creado) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        a.dia.isoformat(), a.nombre, a.cantidad, a.unidad, a.kcal, a.proteina_g, a.hidratos_g,
                        a.grasa_g, "planeado" if a.dia > hoy else "comido", int(a.estimado),
                        json.dumps(a.supuestos, ensure_ascii=False), int(a.pendiente_cantidad),
                        interpretacion_id, ahora,
                    ),
                )
            filas = conn.execute(
                f"SELECT {_COLUMNAS} FROM alimento_apuntado WHERE interpretacion_id = ? ORDER BY id",  # noqa: S608
                (interpretacion_id,),
            ).fetchall()
        return [self._fila(f) for f in filas]

    def apuntes_del_dia(self, dia: date) -> list[dict]:
        with self._connect() as conn:
            filas = conn.execute(
                f"SELECT {_COLUMNAS} FROM alimento_apuntado WHERE dia = ? ORDER BY id",  # noqa: S608
                (dia.isoformat(),),
            ).fetchall()
        return [self._fila(f) for f in filas]

    def interpretaciones(self) -> list[dict]:
        with self._connect() as conn:
            filas = conn.execute(
                "SELECT id, hora, texto, confirmada_sin_cambios FROM interpretacion ORDER BY id"
            ).fetchall()
        return [{**dict(f), "confirmada_sin_cambios": bool(f["confirmada_sin_cambios"])} for f in filas]

    @staticmethod
    def _fila(fila: sqlite3.Row) -> dict:
        datos = dict(fila)
        datos["estimado"] = bool(datos["estimado"])
        datos["pendiente_cantidad"] = bool(datos["pendiente_cantidad"])
        datos["supuestos"] = json.loads(datos["supuestos"])
        return datos
