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

Unit 103 (REC-47 of `ver-fitness`: R-292..R-295, G-67, S-99, S-100): «manda Garmin». The shoes may be given here
(`dar_de_alta`, fuente `aqui`): name, km of the start, cap and use (`suave`, `calidad`, `competicion`); the moment
they are given is their start (`partida`). Each run of Garmin after the start goes once into `zapatilla_carrera`
(`contar`) with the shoe the rule G-67 gives (`regla_g67`, by the sessions of the plan that day), or none when it is not
clear (then Fitness and Hoy ask: `pendientes`). The km of a shoe here = its km at the start + the runs after its start
counted with it. Andrés may choose or change the shoe of a run (`elegir`, origen `andres`: the rule never changes it
again). The km by hand (`poner_km`) of a shoe here is a new start. Strava's reads (102) never touch these shoes.
"""
from __future__ import annotations

import json
import logging
import math
import re
import secrets
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .strava import ErrorStrava, Strava

log = logging.getLogger(__name__)

TOPE_NUEVA = 700  # S-98: a new shoe of Strava, until Andrés changes it
TOPES = (("sonicblast", 700), ("nimbus", 700), ("speed sky tokyo", 400))  # R-288: the ones of today
AVISO_KM = 50  # S-97
TOPE_MIN, TOPE_MAX = 50, 3000
KM_MAX = 10000
BIEN, PRONTO, PASADA = "bien", "pronto", "pasada"
TEXTO_ESTADO = {BIEN: None, PRONTO: "toca cambiarla pronto", PASADA: "pasada"}
STRAVA, ANDRES = "strava", "andres"
AQUI, REGLA = "aqui", "regla"  # unit 103: a shoe counted here; a run whose shoe the rule G-67 gave
SUAVE, CALIDAD, COMPETICION = "suave", "calidad", "competicion"
USOS = (SUAVE, CALIDAD, COMPETICION)
TIPOS_CARRERA = ("running", "trail_running", "treadmill_running", "indoor_running")  # = sync.RUNNING_TYPES
N_ULTIMAS = 10
TEXTO_ORIGEN = {REGLA: "por el plan", ANDRES: "elegida por ti"}
USO_MAL = "Di para qué la usas: suave, calidad o competicion."
NOMBRE_MAL = "Ponle un nombre a la zapatilla."
NOMBRE_REPETIDO = "Ya tienes en activo una zapatilla con ese nombre."
NO_ESTA = "Esa zapatilla no está entre las que tienes en activo."
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
CREATE TABLE IF NOT EXISTS zapatilla_carrera (
    activity_id TEXT PRIMARY KEY,
    fecha TEXT NOT NULL,
    km REAL NOT NULL,
    zapatilla_id TEXT,
    origen TEXT NOT NULL
);
"""
# unit 103: added to a base of the 102 without them (checked first, so it is idempotent and touches no row)
_NUEVAS = (("uso", "TEXT"), ("km_partida", "REAL"), ("partida", "TEXT"))
_CARRERAS = ("SELECT c.activity_id, c.fecha, c.km, c.zapatilla_id, c.origen, z.nombre FROM zapatilla_carrera c "
             "LEFT JOIN zapatilla z ON z.id = c.zapatilla_id")
_COLUMNAS = "id, nombre, km, fuente, fecha_km, tope, en_activo, intento, motivo, uso, km_partida, partida"


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


def validar_uso(valor: Any) -> str:
    uso = _normal(str(valor)) if isinstance(valor, str) else ""
    if uso not in USOS:
        raise ErrorZapatilla(USO_MAL)
    return uso


def validar_nombre(valor: Any) -> str:
    nombre = " ".join(str(valor).split()) if isinstance(valor, str) else ""
    if not nombre or len(nombre) > 80:
        raise ErrorZapatilla(NOMBRE_MAL)
    return nombre


# -- unit 103: the rule G-67 -------------------------------------------------------------------------------------------

def _normal(texto: str) -> str:
    """Lower case and without accents: «Séries» is «series»."""
    sin = unicodedata.normalize("NFKD", texto.lower())
    return " ".join("".join(c for c in sin if not unicodedata.combining(c)).split())


_CALIDAD = re.compile(r"\b(series?|ritmos?|tiradas?)\b|\d+\s*[x×]\s*\d")
_SUAVE = re.compile(r"\b(rodajes?|suaves?|shake\s*-?\s*out|suelt[oa]s?)\b")


def uso_g67(titulos: list[str], competicion: bool) -> str | None:
    """G-67: `competicion` if Garmin marks it or the plan has a race that day; `calidad` if a session of the day says
    series, ritmo or tirada or has repetitions («2×15'», «3x2 km»); `suave` if it says rodaje, suave, shake out or
    suelto; None with no session, nothing that fits, or two different uses."""
    if competicion:
        return COMPETICION
    usos = set()
    for titulo in titulos:
        limpio = _normal(titulo)
        if _CALIDAD.search(limpio):
            usos.add(CALIDAD)
        if _SUAVE.search(limpio):
            usos.add(SUAVE)
    return usos.pop() if len(usos) == 1 else None


def regla_g67(titulos: list[str], competicion: bool, activas: list[dict[str, Any]]) -> str | None:
    """R2, R3: the id of the ONE shoe in use of the use G-67 gives; None when the rule is not sure (it asks)."""
    uso = uso_g67(titulos, competicion)
    del_uso = [z["id"] for z in activas if uso is not None and z.get("uso") == uso]
    return del_uso[0] if len(del_uso) == 1 else None


def _corta(dia: date) -> str:
    return f"{dia.day}/{dia.month}"


def _cuando(dia: date, hoy: date) -> str:
    return "hoy" if dia == hoy else f"el {_corta(dia)}"


def _km(km: float) -> str:
    return str(round(km))


def _km_carrera(km: float) -> str:
    """«8,1 km» or «5 km»: one decimal, with a comma, only when it has one."""
    return f"{km:.1f}".replace(".", ",").removesuffix(",0") + " km"


def vista(fila: dict[str, Any], hoy: date, lectura: dict[str, Any] | None = None) -> dict[str, Any]:
    """R2 (C-364): what Fitness, the API and the coach say of one shoe."""
    km, tope = float(fila["km"]), int(fila["tope"])
    marca = estado(km, tope)
    quedan = round(tope - km)
    aqui = fila["fuente"] == AQUI
    momento: str = (fila.get("partida") if aqui else None) or fila["fecha_km"]
    dia_km = date.fromisoformat(momento[:10])
    if aqui:  # unit 103 (R5): «contados aquí desde el 23/11»
        texto_fuente = f"contados aquí desde {_cuando(dia_km, hoy)}"
    else:
        texto_fuente = f"{'de Strava' if fila['fuente'] == STRAVA else 'dicho por ti'}, {_cuando(dia_km, hoy)}"
    resto = f"quedan {quedan}" if quedan > 0 else (f"te has pasado {-quedan}" if quedan < 0 else "en el tope")
    texto = f"{fila['nombre']} · {_km(km)} de {tope} km · {resto} · {texto_fuente}"
    texto_lectura = None
    if (not aqui and lectura and lectura.get("motivo") and lectura.get("intento")
            and lectura["intento"] > fila["fecha_km"]):
        # C-368: the last attempt after these km failed
        texto_lectura = f"{_cuando(date.fromisoformat(lectura['intento'][:10]), hoy)} no se pudo leer"
        texto += f" · {texto_lectura}"
    return {"id": fila["id"], "nombre": fila["nombre"], "km": km, "tope": tope, "quedan": quedan, "estado": marca,
            "texto_estado": TEXTO_ESTADO[marca], "fuente": fila["fuente"], "fecha_km": dia_km.isoformat(),
            "texto_fuente": texto_fuente, "texto_lectura": texto_lectura, "texto": texto, "uso": fila.get("uso")}


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
            tiene = {f["name"] for f in conn.execute("PRAGMA table_info(zapatilla)")}
            for columna, tipo in _NUEVAS:
                if columna not in tiene:
                    conn.execute(f"ALTER TABLE zapatilla ADD COLUMN {columna} {tipo}")

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
        """GET /api/zapatillas: the list and, when the last read failed, why (C-368); unit 103: the runs without a shoe
        (`pendientes`) and the last ones (`ultimas`)."""
        lectura = self.estado_lectura()
        aviso = None
        if lectura["motivo"] and lectura["intento"]:
            cuando = _cuando(date.fromisoformat(lectura["intento"][:10]), hoy)
            aviso = f"{cuando[0].upper()}{cuando[1:]} no se pudo leer Strava: {lectura['motivo']}."
        return {"zapatillas": self.lista(hoy), "lectura": lectura, "aviso_lectura": aviso,
                "pendientes": self.pendientes(hoy), "ultimas": self.ultimas(hoy), "opciones": self._opciones()}

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
            guardadas = {f["id"]: dict(f) for f in conn.execute(  # unit 103: never the shoes counted here
                f"SELECT {_COLUMNAS} FROM zapatilla WHERE fuente != ?", (AQUI,))}  # noqa: S608
            vistas: set[str] = set()
            de_aqui = self._de_aqui(conn)
            for z in leidas:
                ident = str(z["id"])
                vistas.add(ident)
                antes = guardadas.get(ident)
                if ident in de_aqui:
                    continue
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
        """R6 (C-369): the km Andrés says, «dicho por ti» from `ahora`. Unit 103 (R5): of a shoe counted here, a new
        start: from `ahora` only the runs after it add."""
        km = validar_km(valor)
        with self._connect() as conn:
            fila = self._una(conn, ident)
            if fila["fuente"] == AQUI:
                conn.execute("UPDATE zapatilla SET km_partida = ?, partida = ?, fecha_km = ? WHERE id = ?",
                             (km, _momento(ahora), _momento(ahora), ident))
                self._recalcular(conn, ident)
            else:
                conn.execute("UPDATE zapatilla SET km = ?, fuente = ?, fecha_km = ? WHERE id = ?",
                             (km, ANDRES, _momento(ahora), ident))
            return dict(self._una(conn, ident))

    # -- unit 103 (REC-47): the shoes given here and their runs from Garmin ---------------------------------------

    @staticmethod
    def _de_aqui(conn: sqlite3.Connection) -> set[str]:
        return {f[0] for f in conn.execute("SELECT id FROM zapatilla WHERE fuente = ?", (AQUI,))}

    @staticmethod
    def _activas_aqui(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        filas = conn.execute(f"SELECT {_COLUMNAS} FROM zapatilla WHERE fuente = ? AND en_activo = 1",  # noqa: S608
                             (AQUI,))
        return _ordenadas([dict(f) for f in filas])

    def _opciones(self) -> list[dict[str, str]]:
        """The shoes a run may be given: those here, in use, by name."""
        with self._connect() as conn:
            return [{"id": z["id"], "nombre": z["nombre"]} for z in self._activas_aqui(conn)]

    def dar_de_alta(self, nombre: Any, km: Any, tope: Any, uso: Any, ahora: datetime) -> dict[str, Any]:
        """R1 (R-292): a shoe counted here from `ahora`; `ErrorZapatilla` with why if anything is not valid."""
        limpio, km_ok, tope_ok, uso_ok = validar_nombre(nombre), validar_km(km), validar_tope(tope), validar_uso(uso)
        momento = _momento(ahora)
        with self._connect() as conn:
            if any(z["nombre"].lower() == limpio.lower() for z in conn.execute(
                    "SELECT nombre FROM zapatilla WHERE en_activo = 1")):
                raise ErrorZapatilla(NOMBRE_REPETIDO)
            ident = f"{AQUI}-{secrets.token_hex(4)}"
            conn.execute("INSERT INTO zapatilla (id, nombre, km, fuente, fecha_km, tope, en_activo, uso, km_partida, "
                         "partida) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                         (ident, limpio, km_ok, AQUI, momento, tope_ok, uso_ok, km_ok, momento))
            return dict(self._una(conn, ident))

    def retirar(self, ident: str) -> None:
        """R1: «no en activo»: out of the list and of the question, and no new run; what it counted stays with it.
        `KeyError` if it is not a shoe here in use."""
        with self._connect() as conn:
            if self._una(conn, ident)["fuente"] != AQUI:
                raise KeyError(ident)
            conn.execute("UPDATE zapatilla SET en_activo = 0 WHERE id = ?", (ident,))

    @staticmethod
    def _recalcular(conn: sqlite3.Connection, ident: str) -> None:
        """km = the km of its start + its runs after the start (R2, R5)."""
        fila = conn.execute("SELECT km_partida, partida FROM zapatilla WHERE id = ?", (ident,)).fetchone()
        if fila is None or fila["partida"] is None:
            return
        (suma,) = conn.execute("SELECT COALESCE(SUM(km), 0) FROM zapatilla_carrera WHERE zapatilla_id = ? "
                               "AND fecha > ?", (ident, fila["partida"])).fetchone()
        conn.execute("UPDATE zapatilla SET km = ? WHERE id = ?", (round(float(fila["km_partida"] or 0) + suma, 1),
                                                                 ident))

    def contar(self) -> dict[str, int]:
        """R2, R3, R6: every run of Garmin after the start of the shoes here goes ONCE into `zapatilla_carrera` with
        the shoe of the rule G-67, or none; the km of those shoes counted again. Without Garmin's base or without
        shoes here, nothing; without the plan, every new run asks."""
        with self._connect() as conn:
            tablas = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            (desde,) = conn.execute("SELECT MIN(partida) FROM zapatilla WHERE fuente = ?", (AQUI,)).fetchone()
            if "activities" not in tablas or desde is None:
                return {"nuevas": 0, "sin_zapatilla": 0}
            hechas = {f[0] for f in conn.execute("SELECT activity_id FROM zapatilla_carrera")}
            nuevas = []
            for ident, dia, payload in conn.execute("SELECT activity_id, activity_date, payload FROM activities "
                                                    "WHERE activity_date >= ? ORDER BY activity_date, activity_id",
                                                    (desde[:10],)):
                if ident in hechas:
                    continue
                datos = json.loads(payload)
                inicio = str(datos.get("start_time_local") or f"{dia}T00:00")
                if datos.get("activity_type") not in TIPOS_CARRERA or inicio <= desde:
                    continue
                km = round(float(datos.get("distance_meters") or 0) / 1000, 1)
                nuevas.append((str(ident), inicio, km, datos.get("event_type") == "race"))
            if not nuevas:
                return {"nuevas": 0, "sin_zapatilla": 0}
            activas = self._activas_aqui(conn)
            plan = _PlanDelDia(self._db_path) if "plan_sesion" in tablas else None
            filas = []
            for ident, inicio, km, marcada in nuevas:
                titulos, carrera_plan = plan.del_dia(date.fromisoformat(inicio[:10])) if plan else ([], False)
                candidatas = [z for z in activas if (z["partida"] or "") < inicio]
                filas.append((ident, inicio, km, regla_g67(titulos, marcada or carrera_plan, candidatas), REGLA))
            conn.executemany("INSERT OR IGNORE INTO zapatilla_carrera (activity_id, fecha, km, zapatilla_id, origen) "
                             "VALUES (?, ?, ?, ?, ?)", filas)
            tocadas = {f[3] for f in filas if f[3] is not None}
            sin = sum(1 for f in filas if f[3] is None)
            for zapatilla in tocadas:
                self._recalcular(conn, zapatilla)
        return {"nuevas": len(nuevas), "sin_zapatilla": sin}

    def elegir(self, activity_id: str, zapatilla_id: str) -> None:
        """R3, R4 (C-375, C-376): Andrés says the shoe of a run; the km move at once and the rule never changes it
        again. `KeyError` without that run, `ErrorZapatilla` if the shoe is not one here in use."""
        with self._connect() as conn:
            fila = conn.execute("SELECT zapatilla_id FROM zapatilla_carrera WHERE activity_id = ?",
                                (activity_id,)).fetchone()
            if fila is None:
                raise KeyError(activity_id)
            if zapatilla_id not in {z["id"] for z in self._activas_aqui(conn)}:
                raise ErrorZapatilla(NO_ESTA)
            conn.execute("UPDATE zapatilla_carrera SET zapatilla_id = ?, origen = ? WHERE activity_id = ?",
                         (zapatilla_id, ANDRES, activity_id))
            for ident in {zapatilla_id, fila["zapatilla_id"]} - {None}:
                self._recalcular(conn, ident)

    def _carreras(self, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(f) for f in conn.execute(f"{_CARRERAS} {sql}", args)]  # noqa: S608 (only fixed SQL here)

    def buscar_carrera(self, que: Any, km: float | None = None) -> list[dict[str, Any]]:
        """The counted runs that are `que` (its id, or its day: «2026-11-26» or «26/11»); with several that day, those
        of `km` (for the coach)."""
        clave = str(que or "").strip()
        dia = None
        try:
            dia = date.fromisoformat(clave).isoformat()
        except ValueError:
            trozos = clave.split("/")
            if len(trozos) == 2 and all(t.isascii() and t.isdigit() for t in trozos):
                dia = f"-{int(trozos[1]):02d}-{int(trozos[0]):02d}"
        halladas = [c for c in self._carreras("ORDER BY c.fecha", ())
                    if c["activity_id"] == clave or (dia and (c["fecha"][:10] == dia or c["fecha"][4:10] == dia))]
        if len(halladas) > 1 and km is not None:
            halladas = [c for c in halladas if abs(float(c["km"]) - km) < 0.05] or halladas
        return halladas

    def pendientes(self, hoy: date) -> list[dict[str, Any]]:
        """R3 (C-375): the runs without a shoe, oldest first, each with its question and a button per shoe here in
        use."""
        return pregunta(self._carreras("WHERE c.zapatilla_id IS NULL ORDER BY c.fecha", ()), self._opciones(), hoy)

    def ultimas(self, hoy: date, n: int = N_ULTIMAS) -> list[dict[str, Any]]:
        """R4: the last `n` runs since the start, newest first, with their shoe and who chose it."""
        return [{"activity_id": c["activity_id"], "fecha": c["fecha"][:10], "km": c["km"],
                 "zapatilla_id": c["zapatilla_id"], "nombre": c["nombre"] if c["zapatilla_id"] else None,
                 "origen": c["origen"] if c["zapatilla_id"] else None,
                 "texto_origen": TEXTO_ORIGEN[c["origen"]] if c["zapatilla_id"] else None,
                 "texto": f"{_corta(date.fromisoformat(c['fecha'][:10]))} · {_km_carrera(c['km'])}"}
                for c in self._carreras("ORDER BY c.fecha DESC LIMIT ?", (n,))]


class _PlanDelDia:
    """The titles of the sessions of a day and whether a race of the plan is that day, in the plan in course on that
    day (`Planes.en_curso`)."""

    def __init__(self, db_path: Path) -> None:
        from .plan import Planes

        self._planes = Planes(db_path)
        self._db_path = db_path

    def del_dia(self, dia: date) -> tuple[list[str], bool]:
        plan_id = self._planes.en_curso(dia)
        if plan_id is None:
            return [], False
        conn = sqlite3.connect(self._db_path)
        try:
            titulos = [f[0] for f in conn.execute("SELECT titulo FROM plan_sesion WHERE plan_id = ? AND fecha = ? "
                                                   "ORDER BY orden", (plan_id, dia.isoformat()))]
            (carreras,) = conn.execute("SELECT COUNT(*) FROM plan_carrera WHERE plan_id = ? AND fecha = ?",
                                       (plan_id, dia.isoformat())).fetchone()
        finally:
            conn.close()
        return titulos, bool(carreras)


def pregunta(carreras: list[dict[str, Any]], opciones: list[dict[str, str]], hoy: date) -> list[dict[str, Any]]:
    """R3: «¿Con qué zapatilla corriste el 26/11 (8,1 km)?» for each run without a shoe."""
    return [{"activity_id": c["activity_id"], "fecha": c["fecha"][:10], "km": c["km"], "opciones": opciones,
             "texto": f"¿Con qué zapatilla corriste {_cuando(date.fromisoformat(c['fecha'][:10]), hoy)} "
                      f"({_km_carrera(c['km'])})?"} for c in carreras]


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


def contar_sin_cortar(repo: Zapatillas) -> None:
    """Unit 103 (R6): the new runs counted before Fitness or Hoy answer, so «Actualizar Garmin» shows at once. A failure
    is logged (its kind, never data) and the answer goes on with what was counted before."""
    try:
        repo.contar()
    except (sqlite3.Error, ValueError) as exc:
        log.warning("shoes not counted: %s", type(exc).__name__)


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
        filas = [dict(f) for f in conn.execute("SELECT * FROM zapatilla WHERE en_activo = 1")]
        lectura: dict[str, Any] = {}
        if "zapatilla_lectura" in tablas:
            lectura = dict(conn.execute("SELECT clave, valor FROM zapatilla_lectura").fetchall())
    finally:
        conn.close()
    return [vista(z, hoy, lectura) for z in _ordenadas(filas)]


def pendientes_sin_crear(db_path: Path | str, hoy: date) -> list[dict[str, Any]]:
    """Unit 103 (S-100): the runs without a shoe for the context of the coach, read only (`mode=ro`); [] without them."""
    ruta = Path(db_path)
    if not ruta.is_file():
        return []
    conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tablas = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "zapatilla_carrera" not in tablas:
            return []
        carreras = [dict(f) for f in conn.execute("SELECT activity_id, fecha, km FROM zapatilla_carrera "
                                                  "WHERE zapatilla_id IS NULL ORDER BY fecha")]
        opciones = [{"id": f["id"], "nombre": f["nombre"]} for f in _ordenadas([dict(f) for f in conn.execute(
            "SELECT * FROM zapatilla WHERE fuente = ? AND en_activo = 1", (AQUI,))])]
    finally:
        conn.close()
    return pregunta(carreras, opciones, hoy)


def para_el_contexto(lista: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What `get_contexto` carries of each shoe; each date with its weekday (trap of the 052). Unit 103: its use."""
    return [{"nombre": z["nombre"], "km": z["km"], "tope": z["tope"], "quedan": z["quedan"], "estado": z["estado"],
             "fuente": z["fuente"], "fecha_km": z["fecha_km"], "uso": z.get("uso"),
             "dia_km": _DIAS[date.fromisoformat(z["fecha_km"]).weekday()]} for z in lista]


def carreras_para_el_contexto(pendientes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Unit 103: what `get_contexto` carries of each run without a shoe, with its weekday."""
    return [{"carrera": p["activity_id"], "fecha": p["fecha"], "dia": _DIAS[date.fromisoformat(p["fecha"]).weekday()],
             "km": p["km"]} for p in pendientes]


__all__ = ["AQUI", "AVISO_KM", "BIEN", "PASADA", "PRONTO", "TIPOS_CARRERA", "TOPE_MAX", "TOPE_MIN", "TOPE_NUEVA",
           "USOS", "VACIA", "ErrorZapatilla", "Zapatillas", "aviso_hoy", "carreras_para_el_contexto", "contar_sin_cortar",
           "estado",
           "leer_de_strava", "leer_sin_crear", "para_el_contexto", "pendientes_sin_crear", "pregunta", "regla_g67",
           "tope_sembrado", "uso_g67", "validar_km", "validar_nombre", "validar_tope", "validar_uso", "vista"]
