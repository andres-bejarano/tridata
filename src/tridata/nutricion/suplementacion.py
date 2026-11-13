"""Unit 082 (REC-34 of `apuntar-comida`, G-60..G-62, S-75..S-81): the supplements of the day and the hours of the day.

- A dose (`toma_suplemento`): a coffee (its form: expreso, doble, inglés, italiana with grams and the part of the pot,
  soluble with spoons and grams) or the B12, with its hour and, for a coffee, its mg of caffeine (estimated by the
  coach, S-81, or written by hand). It lives apart from the foods and has NO kcal (G-60): the total, the margin and the
  foods of the day never change with it.
- The milk of the coffees (`leche_cafes`): logged ONCE as an ordinary food of the day with its kcal (`RegistroComidas`,
  untouched), and this table only says which food of that day it is, so it is spread in equal parts among the
  coffees of that day not marked «solo» (G-61). The spread is worked out on reading: adding, removing or marking a
  coffee «solo» changes it, never the kcal. Deleting that food removes the spread.
- The hour of a section of a day (`hora_seccion`): the one Andrés says; without it, the typical one (S-79, `~`).

Its own tables, created with `CREATE TABLE IF NOT EXISTS` over the same SQLite: a store of before opens as it was.
`leer_todo` reads them without creating anything (the context of the team is read only).
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..storage import DEFAULT_DB_PATH
from .registro import SECCIONES, validar_seccion

TOMAS = ("cafe", "b12")
FORMAS = ("expreso", "doble", "ingles", "italiana", "soluble")
NOMBRE_FORMA = {"expreso": "expreso", "doble": "doble", "ingles": "inglés", "italiana": "de italiana",
                "soluble": "soluble"}
# S-79: the typical hour of each section; «Nutrición en carrera» and «Sin sección» have none
HORAS_TIPICAS = {"desayuno": "08:00", "media_manana": "11:00", "comida": "14:30", "merienda": "18:00",
                 "cena": "21:30"}
FALTA_LA_HORA = "falta la hora"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS toma_suplemento (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dia TEXT NOT NULL,
    que TEXT NOT NULL CHECK (que IN ('cafe', 'b12')),
    forma TEXT,
    gramos REAL,
    fraccion REAL,
    cucharadas REAL,
    hora TEXT,
    mg REAL,
    mg_estimado INTEGER NOT NULL DEFAULT 0,
    solo INTEGER NOT NULL DEFAULT 0,
    dosis TEXT,
    creado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_toma_suplemento_dia ON toma_suplemento(dia);
CREATE TABLE IF NOT EXISTS hora_seccion (
    dia TEXT NOT NULL,
    seccion TEXT NOT NULL,
    hora TEXT NOT NULL,
    PRIMARY KEY (dia, seccion)
);
CREATE TABLE IF NOT EXISTS leche_cafes (
    dia TEXT PRIMARY KEY,
    alimento_id INTEGER NOT NULL
);
"""
_COLUMNAS = "id, dia, que, forma, gramos, fraccion, cucharadas, hora, mg, mg_estimado, solo, dosis"
_HORA = re.compile(r"(\d{1,2}):(\d{2})")
_FRACCIONES = {0.25: "¼", 1 / 3: "⅓", 0.5: "½", 2 / 3: "⅔", 0.75: "¾", 1.0: "entera"}


# -- pure rules ----------------------------------------------------------------------------------------------------------

def validar_hora(valor: object) -> str:
    """«HH:MM» (or «H:MM») of a day, as «HH:MM»; ValueError saying why otherwise (R8)."""
    encontrada = _HORA.fullmatch(valor.strip()) if isinstance(valor, str) else None
    if encontrada is None or int(encontrada.group(1)) > 23 or int(encontrada.group(2)) > 59:
        raise ValueError(f"«{valor}» no es una hora: tiene que ser HH:MM, de 00:00 a 23:59")
    return f"{int(encontrada.group(1)):02d}:{encontrada.group(2)}"


def _numero(datos: dict, clave: str, nombre: str, *, positivo: bool = False) -> float | None:
    valor = datos.get(clave)
    if valor is None or valor == "":
        return None
    if isinstance(valor, bool):
        raise ValueError(f"{nombre}: {valor!r} no es un número")
    try:
        numero = float(str(valor).replace(",", ".")) if isinstance(valor, str) else float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{nombre}: {valor!r} no es un número") from None
    if numero < 0:
        raise ValueError(f"{nombre} no pueden ser negativos" if nombre.startswith("los") else
                         f"{nombre}: no puede ser negativo")
    if positivo and numero <= 0:
        raise ValueError(f"{nombre}: tiene que ser mayor que 0")
    return numero


def _forma(valor: object) -> str:
    if not isinstance(valor, str) or valor not in FORMAS:
        raise ValueError(f"«{valor}» no es una forma de café: tiene que ser una de {', '.join(FORMAS)}")
    return valor


def _campos(datos: dict, previa: dict | None = None) -> dict[str, Any]:
    """The fields of a dose checked: `datos` over `previa` (a change) or alone (a new one). ValueError says why."""
    if not isinstance(datos, dict):
        raise ValueError("cada toma tiene que ser un objeto")
    base = dict(previa or {})
    que = datos.get("que", base.get("que"))
    if que not in TOMAS:
        raise ValueError(f"«{que}» no es una toma: tiene que ser café o B12 (cafe o b12)")
    campos: dict[str, Any] = {"que": que}
    if que == "cafe":
        campos["forma"] = _forma(datos["forma"] if "forma" in datos else base.get("forma"))
        for clave, nombre in (("gramos", "los gramos"), ("cucharadas", "las cucharadas")):
            campos[clave] = (_numero(datos, clave, nombre, positivo=True) if clave in datos else base.get(clave))
        if "fraccion" in datos:
            fraccion = _numero(datos, "fraccion", "la fracción de la cafetera", positivo=True)
            if fraccion is not None and fraccion > 1:
                raise ValueError("la fracción de la cafetera tiene que ser de 0 a 1 (½ es 0.5)")
            campos["fraccion"] = fraccion
        else:
            campos["fraccion"] = base.get("fraccion")
        if "mg" in datos:
            campos["mg"] = _numero(datos, "mg", "los mg de cafeína")
            # a new one from the coach is estimated unless it says otherwise (S-81); by hand, the caller says False
            campos["mg_estimado"] = bool(datos.get("mg_estimado", previa is None)) and campos["mg"] is not None
        else:
            campos["mg"], campos["mg_estimado"] = base.get("mg"), bool(base.get("mg_estimado"))
        campos["solo"] = bool(datos["solo"]) if "solo" in datos else bool(base.get("solo"))
        campos["dosis"] = None
    else:  # the B12: only taken, and its dose if he says it (S-81)
        campos.update(forma=None, gramos=None, fraccion=None, cucharadas=None, mg=None, mg_estimado=False, solo=False)
        dosis = datos["dosis"] if "dosis" in datos else base.get("dosis")
        if dosis is not None and not isinstance(dosis, str):
            raise ValueError("la dosis tiene que ser un texto («1000 µg»)")
        campos["dosis"] = (dosis or "").strip() or None
    if "hora" in datos:
        campos["hora"] = None if datos["hora"] in (None, "") else validar_hora(datos["hora"])
    else:
        campos["hora"] = base.get("hora")
    return campos


def _fraccion(f: float) -> str:
    for valor, texto in _FRACCIONES.items():
        if abs(f - valor) < 0.01:
            return texto
    return f"{f:g}".replace(".", ",")


def describir(t: dict) -> str:
    """«café doble», «café de italiana 20 g (½)», «café soluble 2 cucharadas de 2 g», «B12 (1000 µg)»."""
    if t["que"] == "b12":
        return f"B12 ({t['dosis']})" if t.get("dosis") else "B12"
    texto = f"café {NOMBRE_FORMA.get(t.get('forma') or '', t.get('forma') or '')}".strip()
    if t.get("forma") == "italiana":
        if t.get("gramos"):
            texto += f" {t['gramos']:g} g"
        if t.get("fraccion"):
            texto += f" ({_fraccion(t['fraccion'])})"
    elif t.get("forma") == "soluble" and t.get("cucharadas"):
        texto += f" {t['cucharadas']:g} cucharada{'s' if t['cucharadas'] != 1 else ''}"
        if t.get("gramos"):
            texto += f" de {t['gramos']:g} g"
    return texto


def repartir_leche(ml: float, tomas: list[dict]) -> dict[int, float]:
    """G-61: `ml` in equal parts among the coffees not marked «solo»: {id: ml}; empty without any."""
    con_leche = [t["id"] for t in tomas if t["que"] == "cafe" and not t.get("solo")]
    return {i: ml / len(con_leche) for i in con_leche} if con_leche else {}


def _orden(t: dict) -> tuple:
    return (t.get("hora") is None, t.get("hora") or "", t["id"])


def resumen_dia(tomas: list[dict], leche: dict | None) -> dict:
    """The block of a day (R2, R4): the doses by hour (without an hour, at the end) each with its share of the milk,
    the mg of caffeine of the day, the hour of the last dose with caffeine and the milk (`repartida` False: «a lo
    largo del día»)."""
    reparto = repartir_leche(leche["ml"], tomas) if leche and leche.get("ml") else {}
    ordenadas = [{**t, "texto": describir(t), "leche_ml": reparto.get(t["id"])} for t in sorted(tomas, key=_orden)]
    cafes = [t for t in ordenadas if t["que"] == "cafe"]
    total = sum(t.get("mg") or 0 for t in cafes)
    horas = [t["hora"] for t in cafes if t.get("hora")]
    return {"tomas": ordenadas, "cafeina_mg": int(total) if float(total).is_integer() else total,
            "ultima": max(horas) if horas else None,
            "leche": None if leche is None else {**leche, "repartida": bool(reparto)}}


def horas_de_secciones(puestas: dict[str, str]) -> dict[str, dict]:
    """R5 · S-79: the hour of each section of the day: the one put (`aproximada` False) or the typical one."""
    horas = {}
    for seccion in SECCIONES:
        if puestas.get(seccion):
            horas[seccion] = {"hora": puestas[seccion], "aproximada": False}
        else:
            tipica = HORAS_TIPICAS.get(seccion)
            horas[seccion] = {"hora": tipica, "aproximada": tipica is not None}
    return horas


def _fila(fila: sqlite3.Row) -> dict:
    datos = dict(fila)
    datos["mg_estimado"] = bool(datos["mg_estimado"])
    datos["solo"] = bool(datos["solo"])
    for clave in ("mg", "gramos", "fraccion", "cucharadas"):
        if isinstance(datos[clave], float) and datos[clave].is_integer() and clave != "fraccion":
            datos[clave] = int(datos[clave])
    return datos


def _leche(fila: sqlite3.Row | tuple | None) -> dict | None:
    if fila is None:
        return None
    alimento_id, ml, kcal = fila[0], fila[1], fila[2]
    return {"alimento_id": alimento_id, "ml": ml, "kcal": kcal}


class Suplementacion:
    """Repository of the doses, the milk of the coffees and the hours of the sections, over the TriData SQLite."""

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

    # -- doses ---------------------------------------------------------------------------------------------------------

    @staticmethod
    def _nueva(dia: date, datos: dict, hoy: date, ahora: datetime) -> dict:
        """R7 · S-80: a dose of today without an hour takes the hour it is logged at; of another day, ValueError."""
        campos = _campos(datos)
        if campos["hora"] is None:
            if dia != hoy:
                raise ValueError(f"{FALTA_LA_HORA} de {describir(campos)} (es del {dia.isoformat()}, no de hoy)")
            campos["hora"] = f"{ahora.hour:02d}:{ahora.minute:02d}"
        return campos

    def apuntar_varias(self, dia: date, lista: list[dict], hoy: date, ahora: datetime) -> list[dict]:
        """Several doses of `dia`, all of them or none (every one checked before anything is written)."""
        nuevas = [self._nueva(dia, datos, hoy, ahora) for datos in lista]
        creado = ahora.isoformat(timespec="seconds")
        with self._connect() as conn:
            ids = []
            for c in nuevas:
                cur = conn.execute(
                    "INSERT INTO toma_suplemento (dia, que, forma, gramos, fraccion, cucharadas, hora, mg, mg_estimado, "
                    "solo, dosis, creado) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (dia.isoformat(), c["que"], c["forma"], c["gramos"], c["fraccion"], c["cucharadas"], c["hora"],
                     c["mg"], int(c["mg_estimado"]), int(c["solo"]), c["dosis"], creado))
                ids.append(cur.lastrowid)
            return [_fila(self._leer(conn, i)) for i in ids if i is not None]

    def apuntar(self, dia: date, datos: dict, hoy: date, ahora: datetime) -> dict:
        """One dose of `dia` (R1, R3); ValueError says why it is not logged (R7, R8)."""
        (toma,) = self.apuntar_varias(dia, [datos], hoy, ahora)
        return toma

    def cambiar(self, toma_id: int, datos: dict) -> dict:
        """R3: its hour, form, details, mg (by hand: no longer estimated) or «solo». KeyError / ValueError, nothing."""
        with self._connect() as conn:
            previa = _fila(self._leer(conn, toma_id))
            if "mg" in datos and "mg_estimado" not in datos:
                datos = {**datos, "mg_estimado": False}
            c = _campos(datos, previa)
            if c["hora"] is None:
                raise ValueError(f"{FALTA_LA_HORA}: una toma apuntada no se queda sin hora")
            conn.execute(
                "UPDATE toma_suplemento SET que = ?, forma = ?, gramos = ?, fraccion = ?, cucharadas = ?, hora = ?, "
                "mg = ?, mg_estimado = ?, solo = ?, dosis = ? WHERE id = ?",
                (c["que"], c["forma"], c["gramos"], c["fraccion"], c["cucharadas"], c["hora"], c["mg"],
                 int(c["mg_estimado"]), int(c["solo"]), c["dosis"], toma_id))
            return _fila(self._leer(conn, toma_id))

    def borrar(self, toma_id: int) -> dict:
        """Delete one dose and return it as it was; KeyError if it does not exist. Asking first is the caller's."""
        with self._connect() as conn:
            fila = _fila(self._leer(conn, toma_id))
            conn.execute("DELETE FROM toma_suplemento WHERE id = ?", (toma_id,))
        return fila

    def toma(self, toma_id: int) -> dict:
        with self._connect() as conn:
            return _fila(self._leer(conn, toma_id))

    def tomas_del_dia(self, dia: date) -> list[dict]:
        with self._connect() as conn:
            filas = conn.execute(f"SELECT {_COLUMNAS} FROM toma_suplemento WHERE dia = ? ORDER BY id",  # noqa: S608
                                 (dia.isoformat(),)).fetchall()
        return [_fila(f) for f in filas]

    @staticmethod
    def _leer(conn: sqlite3.Connection, toma_id: int) -> sqlite3.Row:
        fila = conn.execute(f"SELECT {_COLUMNAS} FROM toma_suplemento WHERE id = ?",  # noqa: S608
                            (toma_id,)).fetchone()
        if fila is None:
            raise KeyError(toma_id)
        return fila

    # -- the milk of the coffees ---------------------------------------------------------------------------------------

    def marcar_leche(self, dia: date, alimento_id: int) -> None:
        """R4: the food `alimento_id` of `dia` is the milk of its coffees (one per day: it replaces the mark)."""
        with self._connect() as conn:
            conn.execute("INSERT INTO leche_cafes (dia, alimento_id) VALUES (?, ?) "
                         "ON CONFLICT(dia) DO UPDATE SET alimento_id = excluded.alimento_id",
                         (dia.isoformat(), alimento_id))

    def leche_del_dia(self, dia: date) -> dict | None:
        """{alimento_id, ml, kcal} of the milk of `dia`; None without one or if its food was deleted."""
        with self._connect() as conn:
            fila = conn.execute("SELECT a.id, a.cantidad, a.kcal FROM leche_cafes l JOIN alimento_apuntado a "
                                "ON a.id = l.alimento_id WHERE l.dia = ?", (dia.isoformat(),)).fetchone()
        return _leche(fila)

    # -- hours of the sections -----------------------------------------------------------------------------------------

    def poner_hora_seccion(self, dia: date, seccion: object, hora: object) -> dict:
        """R5: the hour of `seccion` on `dia` (None clears it: the typical one again). ValueError, nothing written."""
        if seccion is None:
            raise ValueError("falta la sección: «Sin sección» no lleva hora")
        validar_seccion(seccion)
        nueva = None if hora in (None, "") else validar_hora(hora)
        with self._connect() as conn:
            if nueva is None:
                conn.execute("DELETE FROM hora_seccion WHERE dia = ? AND seccion = ?", (dia.isoformat(), seccion))
            else:
                conn.execute("INSERT INTO hora_seccion (dia, seccion, hora) VALUES (?, ?, ?) "
                             "ON CONFLICT(dia, seccion) DO UPDATE SET hora = excluded.hora",
                             (dia.isoformat(), seccion, nueva))
        return self.horas_del_dia(dia)

    def horas_del_dia(self, dia: date) -> dict[str, dict]:
        with self._connect() as conn:
            filas = conn.execute("SELECT seccion, hora FROM hora_seccion WHERE dia = ?", (dia.isoformat(),)).fetchall()
        return horas_de_secciones({f["seccion"]: f["hora"] for f in filas})

    # -- the day -------------------------------------------------------------------------------------------------------

    def resumen(self, dia: date) -> dict:
        """The block «Suplementación» of `dia` (R2, R4)."""
        resumen = resumen_dia(self.tomas_del_dia(dia), self.leche_del_dia(dia))
        return {**resumen, "tomas": [{k: v for k, v in t.items() if k != "dia"} for t in resumen["tomas"]]}


def leer_todo(db_path: Path | str) -> tuple[dict[str, list[dict]], dict[str, dict[str, str]], dict[str, dict]]:
    """Every dose, hour of section and milk by day, WITHOUT creating anything (R6: the context is read only).

    A store of before the 082 (no tables yet) gives three empty dicts.
    """
    tomas: dict[str, list[dict]] = defaultdict(list)
    horas: dict[str, dict[str, str]] = defaultdict(dict)
    leches: dict[str, dict] = {}
    ruta = Path(db_path)
    if not ruta.is_file():
        return tomas, horas, leches
    conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tablas = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "toma_suplemento" in tablas:
            for f in conn.execute(f"SELECT {_COLUMNAS} FROM toma_suplemento ORDER BY id"):  # noqa: S608
                tomas[f["dia"]].append(_fila(f))
        if "hora_seccion" in tablas:
            for f in conn.execute("SELECT dia, seccion, hora FROM hora_seccion"):
                horas[f["dia"]][f["seccion"]] = f["hora"]
        if "leche_cafes" in tablas and "alimento_apuntado" in tablas:
            for f in conn.execute("SELECT l.dia, a.id, a.cantidad, a.kcal FROM leche_cafes l JOIN alimento_apuntado a "
                                  "ON a.id = l.alimento_id"):
                leches[f[0]] = {"alimento_id": f[1], "ml": f[2], "kcal": f[3]}
    finally:
        conn.close()
    return tomas, horas, leches


__all__ = ["FALTA_LA_HORA", "FORMAS", "HORAS_TIPICAS", "TOMAS", "Suplementacion", "describir", "horas_de_secciones",
           "leer_todo", "repartir_leche", "resumen_dia", "validar_hora"]
