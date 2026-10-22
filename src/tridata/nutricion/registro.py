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
    "supuestos, pendiente_cantidad, creado, origen, provisional"
)
# unit 011 (D8): where the kcal came from («de tu receta …») and whether they were provisional;
# added to an existing store with ALTER TABLE, saved on confirming and never recomputed.
_COLUMNAS_NUEVAS = (("origen", "TEXT"), ("provisional", "INTEGER NOT NULL DEFAULT 0"))


def _estado(dia: date, hoy: date) -> str:
    """A food for a later day is `planeado` there; today or a past day it is `comido`."""
    return "planeado" if dia > hoy else "comido"


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
    origen: str | None = None
    provisional: bool = False

    @property
    def pendiente_cantidad(self) -> bool:
        """R-8 (unit 013): pending only without a quantity AND without kcal; kcal by hand count."""
        return self.cantidad is None and self.kcal is None

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
        origen = datos.get("origen")
        if origen is not None and not isinstance(origen, str):
            raise ValueError(f"{nombre}: origen no válido")
        return cls(
            nombre=nombre.strip(), dia=dia, unidad=(unidad or "").strip() or None,
            estimado=bool(datos.get("estimado", False)), dudoso=bool(datos.get("dudoso", False)),
            supuestos=supuestos, origen=(origen or "").strip() or None,
            provisional=bool(datos.get("provisional", False)), **numeros,
        )


class RegistroComidas:
    """Repository of the food log over the TriData SQLite file."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            tiene = {f["name"] for f in conn.execute("PRAGMA table_info(alimento_apuntado)")}
            for columna, tipo in _COLUMNAS_NUEVAS:
                if columna not in tiene:
                    conn.execute(f"ALTER TABLE alimento_apuntado ADD COLUMN {columna} {tipo}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @property
    def db_path(self) -> Path:
        """The SQLite file, so the weight and objective repositories open the same store."""
        return self._db_path

    @staticmethod
    def validar(alimentos: list[Alimento]) -> None:
        """The business rules of a confirmed list, checked before anything is written."""
        for a in alimentos:
            if a.dudoso:
                raise ValueError(f"«{a.nombre}» está marcado como dudoso: corrígelo o quítalo antes de confirmar")
            if a.cantidad is not None and a.kcal is None:
                raise ValueError(f"«{a.nombre}» tiene cantidad pero no kcal")

    def guardar(self, alimentos: list[Alimento], hoy: date, texto: str, sin_cambios: bool) -> list[dict]:
        """Save a confirmed list, food by food, plus its interpretation. All or nothing.

        A food for a later day is `planeado` there; today (or a past day) it is `comido`. The list
        may be empty (a confirmation with only a weight or an objective, unit 007): the
        interpretation is saved all the same.
        """
        self.validar(alimentos)
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
                    "grasa_g, estado, estimado, supuestos, pendiente_cantidad, interpretacion_id, creado, origen, "
                    "provisional) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        a.dia.isoformat(), a.nombre, a.cantidad, a.unidad, a.kcal, a.proteina_g, a.hidratos_g,
                        a.grasa_g, _estado(a.dia, hoy), int(a.estimado),
                        json.dumps(a.supuestos, ensure_ascii=False), int(a.pendiente_cantidad),
                        interpretacion_id, ahora, a.origen, int(a.provisional),
                    ),
                )
            filas = conn.execute(
                f"SELECT {_COLUMNAS} FROM alimento_apuntado WHERE interpretacion_id = ? ORDER BY id",  # noqa: S608
                (interpretacion_id,),
            ).fetchall()
        return [self._fila(f) for f in filas]

    def cambiar(self, alimento_id: int, datos: dict, hoy: date | None = None) -> dict:
        """Change name, quantity, unit and kcal of a saved food, and its day if `datos` brings one.

        Moving to another day sets the state by the same rule as `guardar` (needs `hoy`); without
        a day, or with the same one, day and state stay as they were. New kcal stop being
        `estimado`, and lose `origen` and `provisional` (unit 016). KeyError if the id does not exist, ValueError if invalid.
        """
        with self._connect() as conn:
            fila = self._leer(conn, alimento_id)
            dia_nuevo = datos.get("dia")
            nuevo = Alimento.desde_dict({**datos, "dia": dia_nuevo if dia_nuevo else fila["dia"]})
            if nuevo.cantidad is not None and nuevo.kcal is None:
                raise ValueError(f"«{nuevo.nombre}» tiene cantidad pero no kcal")
            estado = fila["estado"]
            if nuevo.dia.isoformat() != fila["dia"]:
                if hoy is None:
                    raise ValueError("para pasar un alimento a otro día hace falta saber qué día es hoy")
                estado = _estado(nuevo.dia, hoy)
            mismas_kcal = nuevo.kcal == fila["kcal"]
            estimado = bool(fila["estimado"]) and mismas_kcal
            # unit 016 (R7): kcal written by hand no longer come from the recipe, nor are provisional
            origen = fila["origen"] if mismas_kcal else None
            provisional = bool(fila["provisional"]) and mismas_kcal
            conn.execute(
                "UPDATE alimento_apuntado SET nombre = ?, cantidad = ?, unidad = ?, kcal = ?, estimado = ?, "
                "pendiente_cantidad = ?, dia = ?, estado = ?, origen = ?, provisional = ? WHERE id = ?",
                (nuevo.nombre, nuevo.cantidad, nuevo.unidad, nuevo.kcal, int(estimado),
                 int(nuevo.pendiente_cantidad), nuevo.dia.isoformat(), estado, origen, int(provisional), alimento_id),
            )
            return self._fila(self._leer(conn, alimento_id))

    def alimento(self, alimento_id: int) -> dict:
        """One saved food; KeyError if it does not exist."""
        with self._connect() as conn:
            return self._fila(self._leer(conn, alimento_id))

    def borrar(self, alimento_id: int) -> dict:
        """Delete one saved food and return it as it was; KeyError if it does not exist.

        Asking Andrés first (R-11) is the caller's job: the web route demands `confirmado`.
        """
        with self._connect() as conn:
            fila = self._fila(self._leer(conn, alimento_id))
            conn.execute("DELETE FROM alimento_apuntado WHERE id = ?", (alimento_id,))
        return fila

    def marcar_comido(self, alimento_id: int, hoy: date) -> dict:
        """Mark a planned food as eaten on its own day. ValueError if that day is after `hoy`."""
        with self._connect() as conn:
            fila = self._leer(conn, alimento_id)
            if date.fromisoformat(fila["dia"]) > hoy:
                raise ValueError(f"«{fila['nombre']}» es de un día que aún no ha llegado: no se puede marcar comido")
            conn.execute("UPDATE alimento_apuntado SET estado = 'comido' WHERE id = ?", (alimento_id,))
            return self._fila(self._leer(conn, alimento_id))

    def cerrar_planeados(self, dia: date, conn: sqlite3.Connection | None = None) -> int:
        """Unit 014 (R-24): what is still planned for `dia` counts as eaten when its food is closed.

        With `conn`, inside the caller's transaction (the close of the day, `Cierres.cerrar_comida`);
        without it, in one of its own. Returns how many foods changed.
        """
        if conn is None:
            with self._connect() as propia:
                return self.cerrar_planeados(dia, propia)
        cursor = conn.execute("UPDATE alimento_apuntado SET estado = 'comido' WHERE dia = ? AND estado = 'planeado'",
                              (dia.isoformat(),))
        return cursor.rowcount

    def anadir(self, alimento: Alimento, hoy: date) -> dict:
        """Add a food by hand, without the AI: no interpretation, not estimated, no assumptions."""
        if alimento.cantidad is not None and alimento.kcal is None:
            raise ValueError(f"«{alimento.nombre}» tiene cantidad pero no kcal")
        ahora = datetime.now().isoformat(timespec="seconds")
        a = alimento
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO alimento_apuntado (dia, nombre, cantidad, unidad, kcal, proteina_g, hidratos_g, "
                "grasa_g, estado, estimado, supuestos, pendiente_cantidad, interpretacion_id, creado, origen, "
                "provisional) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '[]', ?, NULL, ?, ?, ?)",
                (a.dia.isoformat(), a.nombre, a.cantidad, a.unidad, a.kcal, a.proteina_g, a.hidratos_g,
                 a.grasa_g, _estado(a.dia, hoy), int(a.pendiente_cantidad), ahora, a.origen, int(a.provisional)),
            )
            if cur.lastrowid is None:  # sqlite always sets it after an INSERT
                raise RuntimeError("no se ha podido apuntar el alimento")
            return self._fila(self._leer(conn, cur.lastrowid))

    def importar_dia(self, dia: date, alimentos: list[Alimento], hoy: date) -> int:
        """Save the food lines of an imported day (unit 009), only if that day has no foods yet.

        One connection, all or nothing: if `dia` already has foods it returns 0 and writes nothing
        (what the app has always wins, and importing twice does not duplicate); otherwise it
        inserts every line with its own `estimado` and `supuestos`, without an interpretation,
        and returns how many.
        """
        if not alimentos:
            return 0
        self.validar(alimentos)
        ahora = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM alimento_apuntado WHERE dia = ? LIMIT 1", (dia.isoformat(),)).fetchone():
                return 0
            for a in alimentos:
                conn.execute(
                    "INSERT INTO alimento_apuntado (dia, nombre, cantidad, unidad, kcal, proteina_g, hidratos_g, "
                    "grasa_g, estado, estimado, supuestos, pendiente_cantidad, interpretacion_id, creado, origen, "
                    "provisional) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                    (
                        dia.isoformat(), a.nombre, a.cantidad, a.unidad, a.kcal, a.proteina_g, a.hidratos_g,
                        a.grasa_g, _estado(dia, hoy), int(a.estimado),
                        json.dumps(a.supuestos, ensure_ascii=False), int(a.pendiente_cantidad), ahora,
                        a.origen, int(a.provisional),
                    ),
                )
        return len(alimentos)

    def apuntes_del_dia(self, dia: date) -> list[dict]:
        with self._connect() as conn:
            filas = conn.execute(
                f"SELECT {_COLUMNAS} FROM alimento_apuntado WHERE dia = ? ORDER BY id",  # noqa: S608
                (dia.isoformat(),),
            ).fetchall()
        return [self._fila(f) for f in filas]

    def primer_dia(self) -> date | None:
        """The oldest day with any food logged (unit 017: where «Todo» starts); None with nothing logged."""
        with self._connect() as conn:
            fila = conn.execute("SELECT MIN(dia) FROM alimento_apuntado").fetchone()
        return date.fromisoformat(fila[0]) if fila and fila[0] else None

    def interpretaciones(self) -> list[dict]:
        with self._connect() as conn:
            filas = conn.execute(
                "SELECT id, hora, texto, confirmada_sin_cambios FROM interpretacion ORDER BY id"
            ).fetchall()
        return [{**dict(f), "confirmada_sin_cambios": bool(f["confirmada_sin_cambios"])} for f in filas]

    @staticmethod
    def _leer(conn: sqlite3.Connection, alimento_id: int) -> sqlite3.Row:
        fila = conn.execute(
            f"SELECT {_COLUMNAS} FROM alimento_apuntado WHERE id = ?", (alimento_id,)  # noqa: S608
        ).fetchone()
        if fila is None:
            raise KeyError(alimento_id)
        return fila

    @staticmethod
    def _fila(fila: sqlite3.Row) -> dict:
        datos = dict(fila)
        datos["estimado"] = bool(datos["estimado"])
        # recomputed on reading (R-8, unit 013): a row saved before with kcal and no quantity counts too
        datos["pendiente_cantidad"] = datos["cantidad"] is None and datos["kcal"] is None
        datos["provisional"] = bool(datos["provisional"])
        datos["supuestos"] = json.loads(datos["supuestos"])
        return datos
