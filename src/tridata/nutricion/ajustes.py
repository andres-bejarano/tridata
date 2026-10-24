"""Profile and plan settings, each with the date from which it applies (unit 005, REC-5).

Two tables of their own in the TriData SQLite store:

- `ajuste`: a history. Every change is a NEW row (key, JSON value, `desde`); nothing is ever
  updated or deleted. What is in force on day `d` is, per key, the row with `desde` <= `d` and
  the highest (`desde`, `id`): a later save with the same date corrects an earlier one.
  "Since always" is `0001-01-01`.
- `carrera`: races (name, date, maintenance days before). The race day and its N days before
  are maintenance (plan 0 %).

`Ajustes.vigentes(dia)` is the ONE door to these values: the web, and later the weight (007)
and full-margin (008) units, read them only through it — never the tables, `.env` or a
`ConfigGasto` of their own (G-27).

Unit 031 (REC-19, G-49): «Pulso y zonas» — `fc_max`, `fc_reposo` (whole ppm), `zonas_fc` (five [from, to] in ppm,
one after the other with no gap, from the resting HR to the max) and `fc_origen` ({"tipo": "prueba", "mes":
"AAAA-MM"} for a stress test, or {"tipo": "configuracion"}). They are saved and dated like every other setting and
checked together: what is in force from `desde` once saved must hang together. `HistorialFC` reads their whole
history at once, so the load can ask the HR of each day without one query per day; `historial_fc_de(db)` does it
without creating anything (the MCP and `export-pmc` only read).
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..storage import DEFAULT_DB_PATH
from .margen import PASOS_POR_DEFECTO, ConfigGasto

DESDE_SIEMPRE = date(1, 1, 1)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ajuste (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clave TEXT NOT NULL,
    valor TEXT NOT NULL,
    desde TEXT NOT NULL,
    guardado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ajuste_clave_desde ON ajuste(clave, desde);
CREATE TABLE IF NOT EXISTS carrera (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    fecha TEXT NOT NULL,
    dias_mantenimiento INTEGER NOT NULL
);
"""

# key -> (what it is called in a message, whole number?, minimum, maximum)
_RANGOS: dict[str, tuple[str, bool, float, float]] = {
    "edad": ("la edad", True, 10, 100),
    "altura_cm": ("la altura (cm)", False, 100, 230),
    "pasos_por_defecto": ("los pasos por defecto", True, 0, 50_000),
    "basal_kcal": ("el basal (kcal)", False, 800, 4_000),
    "kcal_por_paso": ("las kcal por paso", False, 0.01, 0.2),
    "limite_saludable_pct": ("el límite saludable (%)", False, 1, 50),
    "objetivo_pct": ("el objetivo del plan (%)", False, -50, 50),
    "fc_max": ("la FC máxima", True, 120, 230),
    "fc_reposo": ("la FC en reposo", True, 30, 100),
}
PESO_KG = (30, 250)  # the same range unit 007 uses for the weight of a day
DIAS_MANTENIMIENTO = (0, 14)
CLAVES_FC = ("fc_max", "fc_reposo", "zonas_fc", "fc_origen")
CLAVES = (*_RANGOS, "objetivo_peso", "zonas_fc", "fc_origen")
N_ZONAS = 5
PPM_ZONA = (30, 230)
_MES = re.compile(r"^(\d{4})-(\d{2})$")


def _fecha(valor: Any, que: str) -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str):
        try:
            return date.fromisoformat(valor.strip())
        except ValueError:
            pass
    raise ValueError(f"{que}: {valor!r} no es una fecha válida (AAAA-MM-DD)")


def _mayus(texto: str) -> str:
    """The first letter in capitals, the rest as it is («la FC máxima» → «La FC máxima»)."""
    return texto[:1].upper() + texto[1:]


def _numero(valor: Any, nombre: str, entero: bool, minimo: float, maximo: float) -> float | int:
    if valor is None or valor == "" or isinstance(valor, bool):
        raise ValueError(f"Falta {nombre} o no es un número: {valor!r}")
    try:
        numero = float(valor.strip().replace(",", ".")) if isinstance(valor, str) else float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{_mayus(nombre)}: {valor!r} no es un número") from None
    if not math.isfinite(numero):
        raise ValueError(f"{_mayus(nombre)}: {valor!r} no es un número")
    if entero and not numero.is_integer():
        raise ValueError(f"{_mayus(nombre)} tiene que ser un número entero (no {valor!r})")
    if not minimo <= numero <= maximo:
        raise ValueError(f"{_mayus(nombre)} tiene que estar entre {minimo:g} y {maximo:g} (no {valor!r})")
    return int(numero) if entero else numero


def _validar(clave: str, valor: Any, desde: date) -> Any:
    """The checked, normalized value of one key (what is stored as JSON). `ValueError` says why not."""
    if clave in _RANGOS:
        return _numero(valor, *_RANGOS[clave])
    if clave == "objetivo_peso":
        if not isinstance(valor, Mapping):
            raise ValueError("El objetivo de peso son unos kg y una fecha objetivo")
        kg = _numero(valor.get("kg"), "el objetivo de peso (kg)", False, *PESO_KG)
        fecha = _fecha(valor.get("fecha"), "La fecha del objetivo de peso")
        if fecha < desde:
            raise ValueError("La fecha del objetivo de peso no puede ser anterior a la fecha desde la que vale")
        return {"kg": kg, "fecha": fecha.isoformat()}
    if clave == "zonas_fc":
        return _zonas(valor)
    if clave == "fc_origen":
        return _origen(valor)
    raise ValueError(f"{clave!r} no es un ajuste conocido")


def _zonas(valor: Any) -> list[list[int]]:
    """Five [from, to] in whole ppm, each going up (unit 031, R7). How they chain is checked with the HR."""
    if not isinstance(valor, (list, tuple)) or len(valor) != N_ZONAS:
        raise ValueError("Las zonas son cinco zonas, cada una con su «desde» y su «hasta» en ppm")
    zonas = []
    for n, zona in enumerate(valor, start=1):
        if isinstance(zona, Mapping):
            zona = [zona.get("desde"), zona.get("hasta")]
        if not isinstance(zona, (list, tuple)) or len(zona) != 2:
            raise ValueError(f"La zona {n} necesita su «desde» y su «hasta» en ppm")
        desde = int(_numero(zona[0], f"el «desde» de la zona {n}", True, *PPM_ZONA))
        hasta = int(_numero(zona[1], f"el «hasta» de la zona {n}", True, *PPM_ZONA))
        if hasta <= desde:
            raise ValueError(f"La zona {n} tiene que ir de menos a más ppm (no de {desde} a {hasta})")
        zonas.append([desde, hasta])
    return zonas


def _origen(valor: Any) -> dict:
    """Where the HR and the zones come from: a stress test of a month, or the configuration (unit 031)."""
    tipo = valor.get("tipo") if isinstance(valor, Mapping) else None
    if tipo == "configuracion":
        return {"tipo": "configuracion", "mes": None}
    if tipo != "prueba":
        raise ValueError("El origen de la FC es «prueba» (de esfuerzo) o «configuracion»")
    mes = valor.get("mes")
    hallado = _MES.match(mes.strip()) if isinstance(mes, str) else None
    if hallado is None or not 1 <= int(hallado.group(2)) <= 12:
        raise ValueError(f"El mes de la prueba de esfuerzo tiene que ser AAAA-MM (no {mes!r})")
    return {"tipo": "prueba", "mes": mes.strip()}


def _pulso_coherente(valores: Mapping[str, Any]) -> None:
    """What is in force for «Pulso y zonas» from a date, once saved, must hang together (unit 031, R7)."""
    fc_max, fc_reposo, zonas = valores.get("fc_max"), valores.get("fc_reposo"), valores.get("zonas_fc")
    if (fc_max is None) != (fc_reposo is None):
        raise ValueError("Guarda la FC máxima y la FC en reposo juntas")
    if fc_max is None or fc_reposo is None:
        if zonas is not None:
            raise ValueError("Las zonas van con la FC máxima y la FC en reposo: guárdalas juntas")
        return
    if fc_reposo >= fc_max:
        raise ValueError(f"La FC en reposo ({fc_reposo}) tiene que ser menor que la FC máxima ({fc_max})")
    if zonas is None:
        return
    if zonas[0][0] != fc_reposo:
        raise ValueError(f"La zona 1 tiene que empezar en tu FC en reposo ({fc_reposo}), no en {zonas[0][0]}")
    if zonas[-1][1] != fc_max:
        raise ValueError(f"La zona {N_ZONAS} tiene que acabar en tu FC máxima ({fc_max}), no en {zonas[-1][1]}")
    for n in range(1, N_ZONAS):
        (_, hasta), (desde, _) = zonas[n - 1], zonas[n]
        if desde > hasta + 1:
            raise ValueError(f"Entre la zona {n} (hasta {hasta}) y la zona {n + 1} (desde {desde}) hay un hueco")
        if desde < hasta:
            raise ValueError(f"La zona {n} (hasta {hasta}) y la zona {n + 1} (desde {desde}) se pisan")


def _carrera_valida(nombre: Any, fecha: Any, dias: Any) -> tuple[str, date, int]:
    if not isinstance(nombre, str) or not nombre.strip():
        raise ValueError("Ponle nombre a la carrera")
    if len(nombre.strip()) > 100:
        raise ValueError("El nombre de la carrera es demasiado largo (máximo 100 caracteres)")
    dia = _fecha(fecha, "La fecha de la carrera")
    n = int(_numero(dias, "los días de mantenimiento antes", True, *DIAS_MANTENIMIENTO))
    return nombre.strip(), dia, n


@dataclass(frozen=True)
class ObjetivoPeso:
    kg: float
    fecha: date


@dataclass(frozen=True)
class Carrera:
    id: int
    nombre: str
    fecha: date
    dias_mantenimiento: int

    def mantiene(self, dia: date) -> bool:
        """The race day and its N days before are maintenance days."""
        return self.fecha - timedelta(days=self.dias_mantenimiento) <= dia <= self.fecha

    def como_dict(self) -> dict:
        return {"id": self.id, "nombre": self.nombre, "fecha": self.fecha.isoformat(),
                "dias_mantenimiento": self.dias_mantenimiento}


@dataclass(frozen=True)
class AjustesDelDia:
    """Everything in force on `dia` (R11). `None` means "not set" — never an assumed value."""

    dia: date
    edad: int | None
    altura_cm: float | None
    pasos_por_defecto: int | None
    basal_kcal: float | None
    kcal_por_paso: float | None
    limite_saludable_pct: float | None  # the same limit for a deficit and a surplus
    objetivo_pct: float | None  # the plan of THAT day: 0 on a race's maintenance days
    motivo_objetivo: str | None  # "ajuste", "carrera <nombre>", or None without a plan
    objetivo_peso: ObjetivoPeso | None
    carreras: tuple[Carrera, ...]  # from `dia` onward, by date
    desde: Mapping[str, date] = field(default_factory=dict)  # since when each set key applies

    def config_gasto(self) -> ConfigGasto:
        """The expenditure config of that day, with the hand-written basal (unit 007 swaps it)."""
        basal, por_paso = self.basal_kcal, self.kcal_por_paso
        if basal is None or por_paso is None:
            faltan = [nombre for nombre, valor in (("el basal (TRIDATA_BASAL_KCAL)", basal),
                                                   ("las kcal por paso (TRIDATA_KCAL_POR_PASO)", por_paso))
                      if valor is None]
            raise ValueError(f"Faltan ajustes para el {self.dia.isoformat()}: {' y '.join(faltan)}.")
        pasos = PASOS_POR_DEFECTO if self.pasos_por_defecto is None else self.pasos_por_defecto
        return ConfigGasto(basal_kcal=basal, kcal_por_paso=por_paso, pasos=pasos)


class Ajustes:
    """Repository of the settings over the TriData SQLite file."""

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

    # -- reading --------------------------------------------------------------------------

    def _filas_vigentes(self, dia: date) -> dict[str, sqlite3.Row]:
        with self._connect() as conn:
            filas = conn.execute(
                "SELECT clave, valor, desde FROM ajuste WHERE desde <= ? ORDER BY desde, id", (dia.isoformat(),)
            ).fetchall()
        return {f["clave"]: f for f in filas}  # the last one per key wins

    def vigentes(self, dia: date) -> AjustesDelDia:
        filas = self._filas_vigentes(dia)
        valores = {clave: json.loads(f["valor"]) for clave, f in filas.items()}
        carreras = tuple(c for c in self.carreras() if c.fecha >= dia)
        de_carrera = next((c for c in carreras if c.mantiene(dia)), None)
        if de_carrera is not None:
            objetivo, motivo = 0.0, f"carrera {de_carrera.nombre}"
        elif "objetivo_pct" in valores:
            objetivo, motivo = valores["objetivo_pct"], "ajuste"
        else:
            objetivo, motivo = None, None
        peso = valores.get("objetivo_peso")
        return AjustesDelDia(
            dia=dia,
            edad=valores.get("edad"),
            altura_cm=valores.get("altura_cm"),
            pasos_por_defecto=valores.get("pasos_por_defecto"),
            basal_kcal=valores.get("basal_kcal"),
            kcal_por_paso=valores.get("kcal_por_paso"),
            limite_saludable_pct=valores.get("limite_saludable_pct"),
            objetivo_pct=objetivo,
            motivo_objetivo=motivo,
            objetivo_peso=ObjetivoPeso(kg=peso["kg"], fecha=date.fromisoformat(peso["fecha"])) if peso else None,
            carreras=carreras,
            desde={clave: date.fromisoformat(f["desde"]) for clave, f in filas.items()},
        )

    def valores_con_fecha(self, dia: date) -> dict[str, dict]:
        """Per key, the value in force on `dia` and since when (`None`, `None` if never set)."""
        filas = self._filas_vigentes(dia)
        return {clave: ({"valor": json.loads(filas[clave]["valor"]), "desde": filas[clave]["desde"]}
                        if clave in filas else {"valor": None, "desde": None}) for clave in CLAVES}

    def cambios_futuros(self, dia: date) -> list[dict]:
        """Changes saved to apply after `dia` (the effective one per key and date), by date."""
        with self._connect() as conn:
            filas = conn.execute(
                "SELECT clave, valor, desde FROM ajuste WHERE desde > ? ORDER BY desde, id", (dia.isoformat(),)
            ).fetchall()
        ultimos = {(f["clave"], f["desde"]): f for f in filas}
        return [{"clave": f["clave"], "valor": json.loads(f["valor"]), "desde": f["desde"]} for f in ultimos.values()]

    def historial(self, clave: str | None = None) -> list[dict]:
        """Every saved change, in the order saved (nothing is ever overwritten)."""
        with self._connect() as conn:
            filas = conn.execute(
                "SELECT id, clave, valor, desde, guardado FROM ajuste WHERE ? IS NULL OR clave = ? ORDER BY id",
                (clave, clave),
            ).fetchall()
        return [{**dict(f), "valor": json.loads(f["valor"])} for f in filas]

    def historial_fc(self) -> HistorialFC:
        """Unit 031: the whole history of «Pulso y zonas», read once."""
        with self._connect() as conn:
            return HistorialFC(_filas_fc(conn))

    def carreras(self) -> list[Carrera]:
        with self._connect() as conn:
            filas = conn.execute("SELECT id, nombre, fecha, dias_mantenimiento FROM carrera ORDER BY fecha, id").fetchall()
        return [Carrera(id=f["id"], nombre=f["nombre"], fecha=date.fromisoformat(f["fecha"]),
                        dias_mantenimiento=f["dias_mantenimiento"]) for f in filas]

    # -- writing --------------------------------------------------------------------------

    def cambiar(self, datos: Mapping[str, Any], desde: Any) -> dict:
        """Save one or more keys, all from `desde`, in one transaction (all or nothing).

        `preguntar_limite` is true when a non-zero plan target is saved and no healthy limit is
        in force from that date: the screen must ask, and nothing is assumed meanwhile (R6).
        """
        dia = _fecha(desde, "La fecha desde la que vale")
        if not isinstance(datos, Mapping) or not datos:
            raise ValueError("No hay nada que guardar")
        limpios = {clave: _validar(clave, valor, dia) for clave, valor in datos.items()}
        if any(clave in CLAVES_FC for clave in limpios):  # unit 031: the HR and its zones hang together
            en_vigor = {clave: json.loads(f["valor"]) for clave, f in self._filas_vigentes(dia).items()}
            _pulso_coherente({**en_vigor, **limpios})
        guardado = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO ajuste (clave, valor, desde, guardado) VALUES (?, ?, ?, ?)",
                [(clave, json.dumps(valor), dia.isoformat(), guardado) for clave, valor in limpios.items()],
            )
        preguntar = (limpios.get("objetivo_pct", 0) != 0
                     and self.vigentes(dia).limite_saludable_pct is None)
        return {"guardados": list(limpios), "desde": dia.isoformat(), "preguntar_limite": preguntar}

    def anadir_carrera(self, nombre: Any, fecha: Any, dias_mantenimiento: Any) -> dict:
        n, dia, dias = _carrera_valida(nombre, fecha, dias_mantenimiento)
        with self._connect() as conn:
            cursor = conn.execute("INSERT INTO carrera (nombre, fecha, dias_mantenimiento) VALUES (?, ?, ?)",
                                  (n, dia.isoformat(), dias))
            nuevo = cursor.lastrowid
        if nuevo is None:  # an INSERT always has a rowid
            raise RuntimeError("SQLite did not return the id of the new race")
        return Carrera(id=nuevo, nombre=n, fecha=dia, dias_mantenimiento=dias).como_dict()

    def cambiar_carrera(self, id_carrera: int, nombre: Any, fecha: Any, dias_mantenimiento: Any) -> dict:
        n, dia, dias = _carrera_valida(nombre, fecha, dias_mantenimiento)
        with self._connect() as conn:
            cursor = conn.execute("UPDATE carrera SET nombre = ?, fecha = ?, dias_mantenimiento = ? WHERE id = ?",
                                  (n, dia.isoformat(), dias, id_carrera))
            if cursor.rowcount == 0:
                raise KeyError(id_carrera)
        return Carrera(id=id_carrera, nombre=n, fecha=dia, dias_mantenimiento=dias).como_dict()

    def quitar_carrera(self, id_carrera: int) -> dict:
        quitada = next((c for c in self.carreras() if c.id == id_carrera), None)
        if quitada is None:
            raise KeyError(id_carrera)
        with self._connect() as conn:
            conn.execute("DELETE FROM carrera WHERE id = ?", (id_carrera,))
        return quitada.como_dict()

    def sembrar_desde_entorno(self, entorno: Mapping[str, str] | None = None) -> bool:
        """First start: copy basal and kcal per step from `.env` ONCE, as "since always".

        Does nothing (False) once both are saved: from then on `.env` no longer decides
        anything. Raises `ValueError` (saying what is missing) if they are needed and absent.
        """
        con_algo = {clave for clave in ("basal_kcal", "kcal_por_paso") if self.historial(clave)}
        if con_algo == {"basal_kcal", "kcal_por_paso"}:
            return False
        config = ConfigGasto.desde_entorno(entorno)
        datos = {"basal_kcal": config.basal_kcal, "kcal_por_paso": config.kcal_por_paso}
        self.cambiar({clave: valor for clave, valor in datos.items() if clave not in con_algo}, DESDE_SIEMPRE)
        return True


# -- unit 031: «Pulso y zonas» by date (REC-19, G-49) -----------------------------------------------------------------

def _filas_fc(conn: sqlite3.Connection) -> list[tuple[str, Any, date]]:
    marcas = ",".join("?" * len(CLAVES_FC))
    filas = conn.execute(
        f"SELECT clave, valor, desde FROM ajuste WHERE clave IN ({marcas}) ORDER BY desde, id",  # noqa: S608
        CLAVES_FC,
    ).fetchall()
    return [(f[0], json.loads(f[1]), date.fromisoformat(f[2])) for f in filas]


@dataclass(frozen=True)
class PulsoVigente:
    """The HR confirmed in Ajustes in force on a day. `desde`: since when the HR max and rest of that day apply."""

    fc_max: int
    fc_reposo: int
    zonas: tuple[tuple[int, int], ...] | None
    origen: dict | None
    desde: date


class HistorialFC:
    """Every saved value of «Pulso y zonas», oldest first: what is in force on any day without asking SQLite."""

    def __init__(self, filas: list[tuple[str, Any, date]]) -> None:
        self._filas = filas  # (key, value, desde), by (desde, id): the last one per key up to a day wins

    def vigente(self, dia: date) -> PulsoVigente | None:
        """What is in force on `dia`, or None if no HR max and rest were confirmed for it."""
        valores: dict[str, tuple[Any, date]] = {}
        for clave, valor, desde in self._filas:
            if desde > dia:
                break
            valores[clave] = (valor, desde)
        if "fc_max" not in valores or "fc_reposo" not in valores:
            return None
        zonas = valores.get("zonas_fc", (None, None))[0]
        return PulsoVigente(
            fc_max=valores["fc_max"][0], fc_reposo=valores["fc_reposo"][0],
            zonas=tuple((z[0], z[1]) for z in zonas) if zonas else None,
            origen=valores.get("fc_origen", (None, None))[0],
            desde=max(valores["fc_max"][1], valores["fc_reposo"][1]),
        )

    def limites(self, dia: date) -> tuple[float, float] | None:
        """(HR max, HR rest) of `dia` for `metrics.daily_tss`; None: nothing confirmed, the environment decides."""
        pulso = self.vigente(dia)
        return None if pulso is None else (pulso.fc_max, pulso.fc_reposo)

    def valores_de_prueba(self) -> set[int]:
        """Every HR and zone limit saved while a stress test was the origin in force (what must never leak, R9)."""
        valores: set[int] = set()
        for clave, valor, desde in self._filas:
            if clave not in ("fc_max", "fc_reposo", "zonas_fc"):
                continue
            pulso = self.vigente(desde)
            if pulso is None or not pulso.origen or pulso.origen.get("tipo") != "prueba":
                continue
            if clave == "zonas_fc":
                valores.update(int(v) for z in valor for v in z)
            else:
                valores.add(int(valor))
        return valores


def historial_fc_de(db_path: Path | str = DEFAULT_DB_PATH) -> HistorialFC:
    """`HistorialFC` of a database, read only: no file, no `ajuste` table, nothing confirmed — and nothing created."""
    ruta = Path(db_path)
    if not ruta.exists():
        return HistorialFC([])
    conn = sqlite3.connect(f"{ruta.as_uri()}?mode=ro", uri=True)
    try:
        hay = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ajuste'").fetchone()
        return HistorialFC(_filas_fc(conn) if hay else [])
    finally:
        conn.close()
