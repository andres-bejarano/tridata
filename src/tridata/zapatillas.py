"""The shoes in use and their km (unit 102, REC-46 of `ver-fitness`: R-286..R-291, G-66, S-97, S-98).

The SQLite keeps each shoe Strava gave (`zapatilla`): its id in Strava, name, km, where the km come from (`strava` or
`andres`) and when, its cap, whether it is in use, and the last attempt to read Strava with its reason
(`zapatilla_lectura`). Only these two tables are created; nothing else is touched.

- R1: a read of Strava (`guardar_lectura`) leaves in use only what Strava gives and does not mark `retirada`; what it
  marks retired, or no longer gives, stays «no en activo» and is not shown.
- R3 (S-98): a new shoe starts with the cap of its name (SonicBlast or Nimbus 700, Speed Sky Tokyo 400) or 700;
  Andrés changes it (a whole number from 50 to 3000) and no read changes it again.
- R4 (S-97): 50 km or less to the cap → «toca cambiarla pronto»; at the cap or past it → «pasada». Hoy warns only then.
- R5: a failed read (`leer_de_strava`) touches no km and writes down the attempt and why; so does an empty `shoes`
  when there are shoes saved (Strava sometimes gives none: it is not «no shoes»).
- R6 (G-66): the km said by Andrés (`poner_km`, in Fitness or to the coach) stay «dicho por ti» with their date; only a
  read of Strava LATER than that with km equal or higher replaces them (the km of a shoe never go down).

`leer_sin_crear` is what the context of the coach reads: read only, it creates nothing (trap of the 082).
"""
from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .strava import ErrorStrava, Strava

TOPE_NUEVA = 700  # S-98: a new shoe of Strava, until Andrés changes it
TOPES = (("sonicblast", 700), ("nimbus", 700), ("speed sky tokyo", 400))  # R-288: the ones of today
AVISO_KM = 50  # S-97
TOPE_MIN, TOPE_MAX = 50, 3000
KM_MAX = 10000
BIEN, PRONTO, PASADA = "bien", "pronto", "pasada"
TEXTO_ESTADO = {BIEN: None, PRONTO: "toca cambiarla pronto", PASADA: "pasada"}
STRAVA, ANDRES = "strava", "andres"
VACIA = "Strava no ha dado ninguna zapatilla (lista vacía): se reintenta mañana"
TOPE_MAL = f"El tope es un número entero de km, de {TOPE_MIN} a {TOPE_MAX}."
KM_MAL = f"Los km son un número de 0 a {KM_MAX}."
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS zapatilla (
    id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    km REAL NOT NULL,
    fuente TEXT NOT NULL,
    fecha_km TEXT NOT NULL,
    tope INTEGER NOT NULL,
    en_activo INTEGER NOT NULL DEFAULT 1,
    intento TEXT,
    motivo TEXT
);
CREATE TABLE IF NOT EXISTS zapatilla_lectura (clave TEXT PRIMARY KEY, valor TEXT);
"""
_COLUMNAS = "id, nombre, km, fuente, fecha_km, tope, en_activo, intento, motivo"


class ErrorZapatilla(ValueError):
    """A cap or km that is not valid: its text says why, safe to show."""


def _momento(ahora: datetime) -> str:
    return ahora.isoformat(timespec="minutes")


def tope_sembrado(nombre: str) -> int:
    """R-288, S-98: the cap a shoe starts with, by its name."""
    bajo = " ".join(nombre.lower().split())
    return next((tope for trozo, tope in TOPES if trozo in bajo), TOPE_NUEVA)


def estado(km: float, tope: int) -> str:
    """S-97: `pronto` with 50 km or less left, `pasada` at the cap or past it, `bien` otherwise."""
    if km >= tope:
        return PASADA
    if km >= tope - AVISO_KM:
        return PRONTO
    return BIEN


def validar_tope(valor: Any) -> int:
    if isinstance(valor, bool):
        raise ErrorZapatilla(TOPE_MAL)
    if isinstance(valor, str) and valor.strip().isascii() and valor.strip().isdigit():
        valor = int(valor.strip())
    if not isinstance(valor, int) or not TOPE_MIN <= valor <= TOPE_MAX:
        raise ErrorZapatilla(TOPE_MAL)
    return valor


def validar_km(valor: Any) -> float:
    if isinstance(valor, bool) or valor is None:
        raise ErrorZapatilla(KM_MAL)
    if isinstance(valor, str):
        texto = valor.strip().replace(",", ".")
        if not texto.isascii():
            raise ErrorZapatilla(KM_MAL)
        try:
            valor = float(texto)
        except ValueError:
            raise ErrorZapatilla(KM_MAL) from None
    if not isinstance(valor, (int, float)) or not math.isfinite(valor) or not 0 <= valor <= KM_MAX:
        raise ErrorZapatilla(KM_MAL)
    return round(float(valor), 1)


def _corta(dia: date) -> str:
    return f"{dia.day}/{dia.month}"


def _cuando(dia: date, hoy: date) -> str:
    return "hoy" if dia == hoy else f"el {_corta(dia)}"


def _km(km: float) -> str:
    return str(round(km))


def vista(fila: dict[str, Any], hoy: date, lectura: dict[str, Any] | None = None) -> dict[str, Any]:
    """R2 (C-364): what Fitness, the API and the coach say of one shoe."""
    km, tope = float(fila["km"]), int(fila["tope"])
    marca = estado(km, tope)
    quedan = round(tope - km)
    dia_km = date.fromisoformat(fila["fecha_km"][:10])
    texto_fuente = f"{'de Strava' if fila['fuente'] == STRAVA else 'dicho por ti'}, {_cuando(dia_km, hoy)}"
    resto = f"quedan {quedan}" if quedan > 0 else (f"te has pasado {-quedan}" if quedan < 0 else "en el tope")
    texto = f"{fila['nombre']} · {_km(km)} de {tope} km · {resto} · {texto_fuente}"
    texto_lectura = None
    if lectura and lectura.get("motivo") and lectura.get("intento") and lectura["intento"] > fila["fecha_km"]:
        # C-368: the last attempt after these km failed
        texto_lectura = f"{_cuando(date.fromisoformat(lectura['intento'][:10]), hoy)} no se pudo leer"
        texto += f" · {texto_lectura}"
    return {"id": fila["id"], "nombre": fila["nombre"], "km": km, "tope": tope, "quedan": quedan, "estado": marca,
            "texto_estado": TEXTO_ESTADO[marca], "fuente": fila["fuente"], "fecha_km": dia_km.isoformat(),
            "texto_fuente": texto_fuente, "texto_lectura": texto_lectura, "texto": texto}


def aviso_hoy(lista: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """R4 (C-366, C-367): the shoes of `lista` (`vista`s) that Hoy warns of; empty when none."""
    return [{"id": z["id"], "nombre": z["nombre"], "km": z["km"], "tope": z["tope"], "estado": z["estado"],
             "texto": f"{z['nombre']} {_km(z['km'])} de {z['tope']} km, {z['texto_estado']}"}
            for z in lista if z["estado"] != BIEN]


def _ordenadas(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(filas, key=lambda z: (z["nombre"].lower(), z["id"]))


class Zapatillas:
    """The shoes of Andrés this PC keeps, over the same SQLite as the app."""

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
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- reading what is kept ----------------------------------------------------------------------------------------

    def todas(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(f) for f in conn.execute(f"SELECT {_COLUMNAS} FROM zapatilla ORDER BY id")]  # noqa: S608

    def activas(self) -> list[dict[str, Any]]:
        return _ordenadas([z for z in self.todas() if z["en_activo"]])

    def estado_lectura(self) -> dict[str, str | None]:
        with self._connect() as conn:
            valores = dict(conn.execute("SELECT clave, valor FROM zapatilla_lectura").fetchall())
        return {"ultima": valores.get("ultima"), "intento": valores.get("intento"), "motivo": valores.get("motivo")}

    def lista(self, hoy: date) -> list[dict[str, Any]]:
        """R2: every shoe in use, as Fitness shows it, by name."""
        lectura = self.estado_lectura()
        return [vista(z, hoy, lectura) for z in self.activas()]

    def vista(self, hoy: date) -> dict[str, Any]:
        """GET /api/zapatillas: the list and, when the last read failed, why (C-368)."""
        lectura = self.estado_lectura()
        aviso = None
        if lectura["motivo"] and lectura["intento"]:
            cuando = _cuando(date.fromisoformat(lectura["intento"][:10]), hoy)
            aviso = f"{cuando[0].upper()}{cuando[1:]} no se pudo leer Strava: {lectura['motivo']}."
        return {"zapatillas": self.lista(hoy), "lectura": lectura, "aviso_lectura": aviso}

    def buscar(self, que: str) -> list[dict[str, Any]]:
        """The shoes in use whose id is `que` or whose name has it (for the coach)."""
        clave = " ".join(str(que).lower().split())
        activas = self.activas()
        exacta = [z for z in activas if z["id"] == str(que).strip() or z["nombre"].lower() == clave]
        return exacta or [z for z in activas if clave and clave in " ".join(z["nombre"].lower().split())]

    def _una(self, conn: sqlite3.Connection, ident: str) -> sqlite3.Row:
        fila = conn.execute(f"SELECT {_COLUMNAS} FROM zapatilla WHERE id = ? AND en_activo = 1",  # noqa: S608
                            (ident,)).fetchone()
        if fila is None:
            raise KeyError(ident)
        return fila

    # -- writing --------------------------------------------------------------------------------------------------

    def guardar_lectura(self, leidas: list[dict[str, Any]], ahora: datetime) -> dict[str, int]:
        """R1, R3, R6: one good read of Strava merged into what is kept; the attempt written down as good."""
        momento = _momento(ahora)
        nuevas = 0
        with self._connect() as conn:
            guardadas = {f["id"]: dict(f) for f in conn.execute(f"SELECT {_COLUMNAS} FROM zapatilla")}  # noqa: S608
            vistas: set[str] = set()
            for z in leidas:
                ident = str(z["id"])
                vistas.add(ident)
                antes = guardadas.get(ident)
                if z.get("retirada"):
                    if antes is not None:
                        conn.execute("UPDATE zapatilla SET en_activo = 0 WHERE id = ?", (ident,))
                    continue
                km = round(float(z["km"]), 1)
                if antes is None:
                    conn.execute("INSERT INTO zapatilla (id, nombre, km, fuente, fecha_km, tope, en_activo) "
                                 "VALUES (?, ?, ?, ?, ?, ?, 1)", (ident, z["nombre"], km, STRAVA, momento,
                                                                  tope_sembrado(z["nombre"])))
                    nuevas += 1
                    continue
                pisa = antes["fuente"] == STRAVA or (momento > antes["fecha_km"] and km >= antes["km"])  # R6
                if pisa:
                    conn.execute("UPDATE zapatilla SET nombre = ?, km = ?, fuente = ?, fecha_km = ?, en_activo = 1 "
                                 "WHERE id = ?", (z["nombre"], km, STRAVA, momento, ident))
                else:
                    conn.execute("UPDATE zapatilla SET nombre = ?, en_activo = 1 WHERE id = ?", (z["nombre"], ident))
            for ident in set(guardadas) - vistas:  # Strava no longer gives it: not in use
                conn.execute("UPDATE zapatilla SET en_activo = 0 WHERE id = ?", (ident,))
            conn.execute("UPDATE zapatilla SET intento = ?, motivo = NULL", (momento,))
            self._poner(conn, ultima=momento, intento=momento, motivo=None)
            activas = conn.execute("SELECT COUNT(*) FROM zapatilla WHERE en_activo = 1").fetchone()[0]
        return {"activas": int(activas), "nuevas": nuevas, "leidas": len(leidas)}

    def apuntar_fallo(self, motivo: str, ahora: datetime) -> None:
        """R5: no km is touched; the attempt and why."""
        momento = _momento(ahora)
        with self._connect() as conn:
            conn.execute("UPDATE zapatilla SET intento = ?, motivo = ?", (momento, motivo))
            self._poner(conn, intento=momento, motivo=motivo)

    @staticmethod
    def _poner(conn: sqlite3.Connection, **valores: str | None) -> None:
        conn.executemany("INSERT INTO zapatilla_lectura (clave, valor) VALUES (?, ?) "
                         "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", valores.items())

    def cambiar_tope(self, ident: str, valor: Any) -> dict[str, Any]:
        """R3 (C-365): the cap Andrés says; `ErrorZapatilla` if it is not valid, `KeyError` if it is not in use."""
        tope = validar_tope(valor)
        with self._connect() as conn:
            self._una(conn, ident)
            conn.execute("UPDATE zapatilla SET tope = ? WHERE id = ?", (tope, ident))
            return dict(self._una(conn, ident))

    def poner_km(self, ident: str, valor: Any, ahora: datetime) -> dict[str, Any]:
        """R6 (C-369): the km Andrés says, «dicho por ti» from `ahora`."""
        km = validar_km(valor)
        with self._connect() as conn:
            self._una(conn, ident)
            conn.execute("UPDATE zapatilla SET km = ?, fuente = ?, fecha_km = ? WHERE id = ?",
                         (km, ANDRES, _momento(ahora), ident))
            return dict(self._una(conn, ident))


def leer_de_strava(strava: Strava, repo: Zapatillas, ahora: datetime) -> dict[str, int]:
    """One read of Strava (the routine's step and the first one after connecting). A failure is written down with its
    reason and raised again as `ErrorStrava` (R5): no km is touched."""
    try:
        leidas = strava.zapatillas()
    except ErrorStrava as exc:
        repo.apuntar_fallo(str(exc), ahora)
        raise
    if not leidas and repo.activas():  # the anecdote of `shoes: []`: «could not read», not «no shoes»
        repo.apuntar_fallo(VACIA, ahora)
        raise ErrorStrava(VACIA)
    return repo.guardar_lectura(leidas, ahora)


def leer_sin_crear(db_path: Path | str, hoy: date) -> list[dict[str, Any]]:
    """The shoes in use for the context of the coach, WITHOUT creating anything (`mode=ro`); [] without the table."""
    ruta = Path(db_path)
    if not ruta.is_file():
        return []
    conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tablas = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "zapatilla" not in tablas:
            return []
        filas = [dict(f) for f in conn.execute(f"SELECT {_COLUMNAS} FROM zapatilla WHERE en_activo = 1")]  # noqa: S608
        lectura: dict[str, Any] = {}
        if "zapatilla_lectura" in tablas:
            lectura = dict(conn.execute("SELECT clave, valor FROM zapatilla_lectura").fetchall())
    finally:
        conn.close()
    return [vista(z, hoy, lectura) for z in _ordenadas(filas)]


def para_el_contexto(lista: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What `get_contexto` carries of each shoe; each date with its weekday (trap of the 052)."""
    return [{"nombre": z["nombre"], "km": z["km"], "tope": z["tope"], "quedan": z["quedan"], "estado": z["estado"],
             "fuente": z["fuente"], "fecha_km": z["fecha_km"],
             "dia_km": _DIAS[date.fromisoformat(z["fecha_km"]).weekday()]} for z in lista]


__all__ = ["AVISO_KM", "BIEN", "PASADA", "PRONTO", "TOPE_MAX", "TOPE_MIN", "TOPE_NUEVA", "VACIA", "ErrorZapatilla",
           "Zapatillas", "aviso_hoy", "estado", "leer_de_strava", "leer_sin_crear", "para_el_contexto",
           "tope_sembrado", "validar_km", "validar_tope", "vista"]
