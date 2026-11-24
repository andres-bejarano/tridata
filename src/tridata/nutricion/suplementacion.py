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

Unit 109 (REC-52, G-68..G-70, S-105..S-109): two more kinds of dose, a gel with caffeine (`gel`) and a salt tablet
(`sal`), with their product, their number (`cantidad`), their hour or their minute of the race (`minuto`, «en
carrera») and their sodium (`sodio_mg`). Their mg and sodium are given PER UNIT and kept as the total of the dose (×
the number). Neither has kcal (G-68). Saved products (`producto_suplemento`: a name not repeated without minding
capitals, gel or sal, mg of caffeine and of sodium per unit) give their values when a dose is logged from them, and
changing or deleting one never changes the doses already logged (G-70). As SQLite cannot change a `CHECK`, the table
of the doses of a store of the 082 is rebuilt ONCE, in one transaction, keeping its ids, its rows and its sequence.
A number that is not finite (`nan`, `inf`, `1e400`) is never kept (S-109).
"""
from __future__ import annotations

import math
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

TOMAS = ("cafe", "b12", "gel", "sal")
TIPOS_PRODUCTO = ("gel", "sal")
FORMAS = ("expreso", "doble", "ingles", "italiana", "soluble")
NOMBRE_FORMA = {"expreso": "expreso", "doble": "doble", "ingles": "inglés", "italiana": "de italiana",
                "soluble": "soluble"}
# S-79: the typical hour of each section; «Nutrición en carrera» and «Sin sección» have none
HORAS_TIPICAS = {"desayuno": "08:00", "media_manana": "11:00", "comida": "14:30", "merienda": "18:00",
                 "cena": "21:30"}
FALTA_LA_HORA = "falta la hora"
FALTA_HORA_O_MINUTO = "falta la hora o el minuto"  # R7 · R-323: a gel or a salt

_SCHEMA = """
CREATE TABLE IF NOT EXISTS toma_suplemento (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dia TEXT NOT NULL,
    que TEXT NOT NULL CHECK (que IN ('cafe', 'b12', 'gel', 'sal')),
    forma TEXT,
    gramos REAL,
    fraccion REAL,
    cucharadas REAL,
    hora TEXT,
    mg REAL,
    mg_estimado INTEGER NOT NULL DEFAULT 0,
    solo INTEGER NOT NULL DEFAULT 0,
    dosis TEXT,
    creado TEXT NOT NULL,
    producto TEXT,
    cantidad REAL,
    minuto INTEGER,
    sodio_mg REAL
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
CREATE TABLE IF NOT EXISTS producto_suplemento (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE COLLATE NOCASE,
    tipo TEXT NOT NULL CHECK (tipo IN ('gel', 'sal')),
    mg_cafeina REAL,
    sodio_mg REAL,
    creado TEXT NOT NULL
);
"""
# the columns of the 082, in its order (the rebuild copies them as they are), and the ones of the 109
_COLUMNAS_082 = ("id", "dia", "que", "forma", "gramos", "fraccion", "cucharadas", "hora", "mg", "mg_estimado", "solo",
                 "dosis")
_COLUMNAS_109 = ("producto", "cantidad", "minuto", "sodio_mg")
_COLUMNAS = ", ".join(_COLUMNAS_082 + _COLUMNAS_109)
_PRODUCTO = "id, nombre, tipo, mg_cafeina, sodio_mg"
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
    except OverflowError:
        raise ValueError(f"{nombre}: {valor!r} no es un número finito") from None
    except (TypeError, ValueError):
        raise ValueError(f"{nombre}: {valor!r} no es un número") from None
    if not math.isfinite(numero):  # S-109 · P-20261113-5e585faf: «nan», «inf», 1e400
        raise ValueError(f"{nombre}: {valor!r} no es un número finito")
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


def _por_unidad(base: dict, clave: str) -> float | None:
    """The value per unit of a gel or a salt already logged (its total ÷ its number)."""
    total, cantidad = base.get(clave), base.get("cantidad") or 1
    return None if total is None else total / cantidad


def _entero(n: float) -> float | int:
    return int(n) if float(n).is_integer() else n


# R7 (review of the 109): a reasonable ceiling per unit, so a total is never infinite nor too big for SQLite
TOPES = {"cantidad": 50, "mg": 1000, "sodio_mg": 5000, "minuto": 2880}


def _sin_pasar(numero: float | None, clave: str, nombre: str) -> None:
    if numero is not None and numero > TOPES[clave]:
        raise ValueError(f"{nombre}: {numero:g} es demasiado (como mucho {TOPES[clave]})")


def _campos_carrera(que: str, datos: dict, base: dict) -> dict[str, Any]:
    """R1, R4, R7: a gel or a salt: product, number (> 0), mg of caffeine (gel) and sodium PER UNIT kept × the
    number, and its minute of the race (an integer >= 0). Every number is checked before anything is missing."""
    numeros = {}
    for clave, nombre in (("cantidad", "el número"), ("mg", "los mg de cafeína"), ("sodio_mg", "los mg de sodio"),
                          ("minuto", "el minuto")):
        numeros[clave] = _numero(datos, clave, nombre, positivo=clave == "cantidad") if clave in datos else None
        _sin_pasar(numeros[clave], clave, nombre)
    cantidad = numeros["cantidad"] if "cantidad" in datos and numeros["cantidad"] is not None else (
        base.get("cantidad") or 1)
    campos: dict[str, Any] = {"que": que, "forma": None, "gramos": None, "fraccion": None, "cucharadas": None,
                              "mg_estimado": False, "solo": False, "dosis": None, "cantidad": _entero(cantidad)}
    producto = datos["producto"] if "producto" in datos else base.get("producto")
    if producto is not None and not isinstance(producto, str):
        raise ValueError("el producto tiene que ser un texto («Gel 226 cola»)")
    campos["producto"] = (producto or "").strip() or None
    if "minuto" in datos:
        minuto = numeros["minuto"]
        if minuto is not None and not float(minuto).is_integer():
            raise ValueError(f"el minuto tiene que ser un número entero de minutos, no {datos['minuto']!r}")
        campos["minuto"] = None if minuto is None else int(minuto)
    else:
        campos["minuto"] = base.get("minuto")
    if que == "gel":
        mg = numeros["mg"] if "mg" in datos else _por_unidad(base, "mg")
        if mg is None:
            raise ValueError("faltan los mg de cafeína del gel (por gel): dilos o elige uno de sus productos")
        campos["mg"] = _entero(mg * cantidad)
    else:
        campos["mg"] = None
    sodio = numeros["sodio_mg"] if "sodio_mg" in datos else _por_unidad(base, "sodio_mg")
    if que == "sal" and sodio is None:
        raise ValueError("faltan los mg de sodio de la pastilla de sal: dilos o elige uno de sus productos")
    campos["sodio_mg"] = None if sodio is None else _entero(sodio * cantidad)
    for clave, nombre in (("mg", "los mg de cafeína"), ("sodio_mg", "los mg de sodio")):
        if campos[clave] is not None and not math.isfinite(campos[clave]):  # the total too, never only its parts
            raise ValueError(f"{nombre} de la toma no dan un número finito")
    return campos


def _campos(datos: dict, previa: dict | None = None) -> dict[str, Any]:
    """The fields of a dose checked: `datos` over `previa` (a change) or alone (a new one). ValueError says why."""
    if not isinstance(datos, dict):
        raise ValueError("cada toma tiene que ser un objeto")
    base = dict(previa or {})
    que = datos.get("que", base.get("que"))
    if que not in TOMAS:
        raise ValueError(f"«{que}» no es una toma: tiene que ser café o B12 (cafe o b12), un gel con cafeína (gel) "
                         "o una pastilla de sal (sal)")
    if que in ("gel", "sal"):
        campos = _campos_carrera(que, datos, base if base.get("que") == que else {})
    else:
        campos = _campos_cafe_b12(que, datos, base)
    if "hora" in datos:
        campos["hora"] = None if datos["hora"] in (None, "") else validar_hora(datos["hora"])
    else:
        campos["hora"] = base.get("hora")
    if que in ("gel", "sal") and campos["hora"] is None and campos["minuto"] is None:
        raise ValueError(f"{FALTA_HORA_O_MINUTO} de {describir(campos)}")
    return campos


def _campos_cafe_b12(que: str, datos: dict, base: dict) -> dict[str, Any]:
    campos: dict[str, Any] = {"que": que, "producto": None, "cantidad": None, "minuto": None, "sodio_mg": None}
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
            campos["mg_estimado"] = bool(datos.get("mg_estimado", not base)) and campos["mg"] is not None
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
    return campos


def _fraccion(f: float) -> str:
    for valor, texto in _FRACCIONES.items():
        if abs(f - valor) < 0.01:
            return texto
    return f"{f:g}".replace(".", ",")


def describir(t: dict) -> str:
    """«café doble», «café de italiana 20 g (½)», «café soluble 2 cucharadas de 2 g», «B12 (1000 µg)»; unit 109:
    «Gel 226 cola», «2 × Gel 226 cola», «gel», «pastilla de sal»."""
    if t["que"] in ("gel", "sal"):
        nombre = t.get("producto") or ("gel" if t["que"] == "gel" else "pastilla de sal")
        cantidad = t.get("cantidad") or 1
        return nombre if cantidad == 1 else f"{cantidad:g} × {nombre}"
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


def _suma(valores: list) -> float | int:
    total = sum(v or 0 for v in valores)
    return int(total) if float(total).is_integer() else total


def resumen_dia(tomas: list[dict], leche: dict | None) -> dict:
    """The block of a day (R2, R4): the doses by hour (without an hour, at the end) each with its share of the milk,
    the mg of caffeine of the day, the hour of the last dose with caffeine and the milk (`repartida` False: «a lo
    largo del día»).

    Unit 109 (G-69, S-105, S-106): the gels and salts with a minute go apart, in `en_carrera` by minute; the caffeine
    adds the gels, and with any gel the day also says how much comes from each (`cafeina_cafe_mg`,
    `cafeina_geles_mg`); the last dose counts the gels with an hour; `sodio_mg` only if some dose has sodium. A day
    without gels or salt gives exactly the four keys of the 082."""
    reparto = repartir_leche(leche["ml"], tomas) if leche and leche.get("ml") else {}
    todas = [{**t, "texto": describir(t), "leche_ml": reparto.get(t["id"])} for t in tomas]
    ordenadas = sorted((t for t in todas if t.get("minuto") is None), key=_orden)
    en_carrera = sorted((t for t in todas if t.get("minuto") is not None), key=lambda t: (t["minuto"], t["id"]))
    cafes = [t for t in todas if t["que"] == "cafe"]
    geles = [t for t in todas if t["que"] == "gel"]
    horas = [t["hora"] for t in cafes + geles if t.get("hora")]
    resumen: dict[str, Any] = {
        "tomas": ordenadas, "cafeina_mg": _suma([t.get("mg") for t in cafes + geles]),
        "ultima": max(horas) if horas else None,
        "leche": None if leche is None else {**leche, "repartida": bool(reparto)}}
    if en_carrera:
        resumen["en_carrera"] = en_carrera
    if geles:
        resumen["cafeina_cafe_mg"] = _suma([t.get("mg") for t in cafes])
        resumen["cafeina_geles_mg"] = _suma([t.get("mg") for t in geles])
    con_sodio = [t["sodio_mg"] for t in todas if t.get("sodio_mg") is not None]
    if con_sodio:
        resumen["sodio_mg"] = _suma(con_sodio)
    return resumen


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


def _fila(fila: sqlite3.Row | dict) -> dict:
    datos = dict(fila)
    for clave in _COLUMNAS_109:  # a store of the 082 read before the app opened it (`leer_todo`)
        datos.setdefault(clave, None)
    datos["mg_estimado"] = bool(datos["mg_estimado"])
    datos["solo"] = bool(datos["solo"])
    for clave in ("mg", "gramos", "fraccion", "cucharadas", "cantidad", "sodio_mg"):
        if isinstance(datos[clave], float) and datos[clave].is_integer() and clave != "fraccion":
            datos[clave] = int(datos[clave])
    return datos


def _producto(fila: sqlite3.Row) -> dict:
    datos = dict(fila)
    for clave in ("mg_cafeina", "sodio_mg"):
        if isinstance(datos[clave], float) and datos[clave].is_integer():
            datos[clave] = int(datos[clave])
    return datos


def _nombre_producto(valor: object) -> str:
    if not isinstance(valor, str) or not " ".join(valor.split()):
        raise ValueError("falta el nombre del producto («Gel 226 cola»)")
    return " ".join(valor.split())


def campos_producto(datos: dict, previo: dict | None = None) -> dict[str, Any]:
    """R3 · S-107: a saved product checked (`datos` over `previo`): its name, gel or sal, and its mg of caffeine (a
    gel's, required) and of sodium (a salt's, required) per unit, finite and not negative. ValueError says why."""
    if not isinstance(datos, dict):
        raise ValueError("el producto tiene que ser un objeto")
    base = dict(previo or {})
    nombre = _nombre_producto(datos["nombre"] if "nombre" in datos else base.get("nombre"))
    tipo = datos.get("tipo", base.get("tipo"))
    if tipo not in TIPOS_PRODUCTO:
        raise ValueError(f"«{tipo}» no es un tipo de producto: tiene que ser gel o sal")
    valores = {}
    for clave, nombre_valor in (("mg_cafeina", "los mg de cafeína"), ("sodio_mg", "los mg de sodio")):
        valores[clave] = _numero(datos, clave, nombre_valor) if clave in datos else base.get(clave)
        _sin_pasar(valores[clave], "mg" if clave == "mg_cafeina" else clave, nombre_valor)
    if tipo == "sal":
        valores["mg_cafeina"] = None
        if valores["sodio_mg"] is None:
            raise ValueError("faltan los mg de sodio de la pastilla (por pastilla)")
    elif valores["mg_cafeina"] is None:
        raise ValueError("faltan los mg de cafeína del gel (por gel; 0 si no lleva)")
    return {"nombre": nombre, "tipo": tipo, **{k: None if v is None else _entero(v) for k, v in valores.items()}}


def _leche(fila: sqlite3.Row | tuple | None) -> dict | None:
    if fila is None:
        return None
    alimento_id, ml, kcal = fila[0], fila[1], fila[2]
    return {"alimento_id": alimento_id, "ml": ml, "kcal": kcal}


def _buscar_producto(conn: sqlite3.Connection, nombre: str) -> dict | None:
    clave = " ".join(nombre.split()).casefold()
    for fila in conn.execute(f"SELECT {_PRODUCTO} FROM producto_suplemento ORDER BY id"):  # noqa: S608
        if fila["nombre"].casefold() == clave:
            return _producto(fila)
    return None


def _sin_repetir(conn: sqlite3.Connection, nombre: str, producto_id: int | None) -> None:
    otro = _buscar_producto(conn, nombre)
    if otro is not None and otro["id"] != producto_id:
        raise ValueError(f"ya hay un producto guardado que se llama «{otro['nombre']}»: cámbialo a ese")


def _rehacer_tabla_082(db_path: Path) -> None:
    """R8: the table of the doses of a store of the 082 has `CHECK (que IN ('cafe', 'b12'))`, which SQLite cannot
    change. ONCE, in one transaction: a new table with the 109's columns, the rows copied with their ids, the old one
    dropped, the new one renamed and the sequence kept (a deleted id is never reused). A store without that table,
    or already rebuilt, is not touched."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        fila = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'toma_suplemento'").fetchone()
        if fila is None or "'gel'" in fila[0]:
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            secuencia = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'toma_suplemento'").fetchone()
            nueva = _SCHEMA.split(";", 1)[0].replace("IF NOT EXISTS toma_suplemento", "toma_suplemento_109")
            conn.execute(nueva)
            columnas = ", ".join((*_COLUMNAS_082, "creado"))
            conn.execute(f"INSERT INTO toma_suplemento_109 ({columnas}) "  # noqa: S608
                         f"SELECT {columnas} FROM toma_suplemento ORDER BY id")
            conn.execute("DROP TABLE toma_suplemento")
            conn.execute("ALTER TABLE toma_suplemento_109 RENAME TO toma_suplemento")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_toma_suplemento_dia ON toma_suplemento(dia)")
            if secuencia is not None:
                conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'toma_suplemento'",
                             (secuencia[0],))
                if conn.execute("SELECT changes()").fetchone()[0] == 0:
                    conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('toma_suplemento', ?)",
                                 (secuencia[0],))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


class Suplementacion:
    """Repository of the doses, the milk of the coffees and the hours of the sections, over the TriData SQLite."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        _rehacer_tabla_082(self._db_path)
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
    def _del_producto(conn: sqlite3.Connection, datos: dict) -> dict:
        """R3, R6 · G-70: a dose chosen from a saved product (`producto_id`) or naming one (`producto`, without
        minding capitals) takes its kind, its name and its values per unit, unless `datos` says them."""
        if not isinstance(datos, dict):
            return datos
        producto = None
        if datos.get("producto_id") not in (None, ""):
            producto_id = datos["producto_id"]
            if isinstance(producto_id, bool) or not isinstance(producto_id, int):
                raise ValueError(f"el producto {producto_id!r} no es uno de los guardados")
            fila = conn.execute(f"SELECT {_PRODUCTO} FROM producto_suplemento WHERE id = ?",  # noqa: S608
                                (producto_id,)).fetchone()
            if fila is None:
                raise ValueError("ese producto ya no está entre los guardados")
            producto = _producto(fila)
        elif isinstance(datos.get("producto"), str) and datos.get("que") in (None, "gel", "sal"):
            producto = _buscar_producto(conn, datos["producto"])
        datos = {k: v for k, v in datos.items() if k != "producto_id"}
        if producto is None:
            return datos
        que = datos.get("que") or producto["tipo"]
        if que != producto["tipo"]:
            raise ValueError(f"«{producto['nombre']}» es {'un gel' if producto['tipo'] == 'gel' else 'una sal'}, "
                             f"no {'un gel' if que == 'gel' else 'una sal' if que == 'sal' else que}")
        nuevos = {"que": que, "producto": producto["nombre"]}
        if que == "gel" and "mg" not in datos:
            nuevos["mg"] = producto["mg_cafeina"]
        if "sodio_mg" not in datos:
            nuevos["sodio_mg"] = producto["sodio_mg"]
        return {**datos, **nuevos}

    @staticmethod
    def _nueva(dia: date, datos: dict, hoy: date, ahora: datetime) -> dict:
        """R7 · S-80: a dose of today without an hour takes the hour it is logged at; of another day, ValueError.
        Unit 109 (R-323): a gel or a salt needs its hour or its minute, also today."""
        campos = _campos(datos)
        if campos["hora"] is None and campos["que"] in ("cafe", "b12"):
            if dia != hoy:
                raise ValueError(f"{FALTA_LA_HORA} de {describir(campos)} (es del {dia.isoformat()}, no de hoy)")
            campos["hora"] = f"{ahora.hour:02d}:{ahora.minute:02d}"
        return campos

    def apuntar_varias(self, dia: date, lista: list[dict], hoy: date, ahora: datetime) -> list[dict]:
        """Several doses of `dia`, all of them or none (every one checked before anything is written)."""
        creado = ahora.isoformat(timespec="seconds")
        with self._connect() as conn:
            nuevas = [self._nueva(dia, self._del_producto(conn, datos), hoy, ahora) for datos in lista]
            ids = []
            for c in nuevas:
                cur = conn.execute(
                    "INSERT INTO toma_suplemento (dia, que, forma, gramos, fraccion, cucharadas, hora, mg, mg_estimado, "
                    "solo, dosis, creado, producto, cantidad, minuto, sodio_mg) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (dia.isoformat(), c["que"], c["forma"], c["gramos"], c["fraccion"], c["cucharadas"], c["hora"],
                     c["mg"], int(c["mg_estimado"]), int(c["solo"]), c["dosis"], creado, c["producto"], c["cantidad"],
                     c["minuto"], c["sodio_mg"]))
                ids.append(cur.lastrowid)
            return [_fila(self._leer(conn, i)) for i in ids if i is not None]

    def apuntar(self, dia: date, datos: dict, hoy: date, ahora: datetime) -> dict:
        """One dose of `dia` (R1, R3); ValueError says why it is not logged (R7, R8)."""
        (toma,) = self.apuntar_varias(dia, [datos], hoy, ahora)
        return toma

    def cambiar(self, toma_id: int, datos: dict) -> dict:
        """R3: its hour, form, details, mg (by hand: no longer estimated) or «solo». KeyError / ValueError, nothing.
        Unit 109 (R5): a gel or a salt also its product (another saved one with `producto_id` takes its values), its
        number (the values per unit kept), its mg and sodium per unit and its minute."""
        with self._connect() as conn:
            previa = _fila(self._leer(conn, toma_id))
            if "mg" in datos and "mg_estimado" not in datos:
                datos = {**datos, "mg_estimado": False}
            if datos.get("producto_id") not in (None, ""):
                datos = self._del_producto(conn, {**datos, "que": previa["que"]})
            c = _campos(datos, previa)
            if c["hora"] is None and c["que"] in ("cafe", "b12"):
                raise ValueError(f"{FALTA_LA_HORA}: una toma apuntada no se queda sin hora")
            conn.execute(
                "UPDATE toma_suplemento SET que = ?, forma = ?, gramos = ?, fraccion = ?, cucharadas = ?, hora = ?, "
                "mg = ?, mg_estimado = ?, solo = ?, dosis = ?, producto = ?, cantidad = ?, minuto = ?, sodio_mg = ? "
                "WHERE id = ?",
                (c["que"], c["forma"], c["gramos"], c["fraccion"], c["cucharadas"], c["hora"], c["mg"],
                 int(c["mg_estimado"]), int(c["solo"]), c["dosis"], c["producto"], c["cantidad"], c["minuto"],
                 c["sodio_mg"], toma_id))
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

    # -- unit 109: saved products (R3 · S-107, G-70) -------------------------------------------------------------------

    def productos(self) -> list[dict]:
        """The saved gels and salts, by name."""
        with self._connect() as conn:
            filas = conn.execute(f"SELECT {_PRODUCTO} FROM producto_suplemento "  # noqa: S608
                                 "ORDER BY nombre COLLATE NOCASE, id").fetchall()
        return [_producto(f) for f in filas]

    def producto(self, producto_id: int) -> dict:
        with self._connect() as conn:
            return _producto(self._leer_producto(conn, producto_id))

    def guardar_producto(self, datos: dict, ahora: datetime | None = None) -> dict:
        """A new saved product; ValueError (nothing saved) if it does not pass or its name is already saved."""
        p = campos_producto(datos)
        with self._connect() as conn:
            _sin_repetir(conn, p["nombre"], None)
            cur = conn.execute("INSERT INTO producto_suplemento (nombre, tipo, mg_cafeina, sodio_mg, creado) "
                               "VALUES (?, ?, ?, ?, ?)",
                               (p["nombre"], p["tipo"], p["mg_cafeina"], p["sodio_mg"],
                                (ahora or datetime.now()).isoformat(timespec="seconds")))
            return _producto(self._leer_producto(conn, int(cur.lastrowid or 0)))

    def cambiar_producto(self, producto_id: int, datos: dict) -> dict:
        """G-70: changes the product only; the doses logged from it keep their values. KeyError / ValueError."""
        with self._connect() as conn:
            previo = _producto(self._leer_producto(conn, producto_id))
            p = campos_producto(datos, previo)
            _sin_repetir(conn, p["nombre"], producto_id)
            conn.execute("UPDATE producto_suplemento SET nombre = ?, tipo = ?, mg_cafeina = ?, sodio_mg = ? "
                         "WHERE id = ?", (p["nombre"], p["tipo"], p["mg_cafeina"], p["sodio_mg"], producto_id))
            return _producto(self._leer_producto(conn, producto_id))

    def borrar_producto(self, producto_id: int) -> dict:
        """Deletes a saved product (the doses logged from it stay as they are); KeyError if it does not exist."""
        with self._connect() as conn:
            previo = _producto(self._leer_producto(conn, producto_id))
            conn.execute("DELETE FROM producto_suplemento WHERE id = ?", (producto_id,))
        return previo

    def buscar_producto(self, nombre: str) -> dict | None:
        """The saved product with that name, without minding capitals or spaces; None if there is none."""
        with self._connect() as conn:
            return _buscar_producto(conn, nombre)

    @staticmethod
    def _leer_producto(conn: sqlite3.Connection, producto_id: int) -> sqlite3.Row:
        fila = conn.execute(f"SELECT {_PRODUCTO} FROM producto_suplemento WHERE id = ?",  # noqa: S608
                            (producto_id,)).fetchone()
        if fila is None:
            raise KeyError(producto_id)
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
            hay = {c[1] for c in conn.execute("PRAGMA table_info(toma_suplemento)")}
            columnas = ", ".join(c for c in _COLUMNAS.split(", ") if c in hay)  # a store of the 082 not opened yet
            for f in conn.execute(f"SELECT {columnas} FROM toma_suplemento ORDER BY id"):  # noqa: S608
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


def leer_productos(db_path: Path | str) -> list[dict]:
    """Unit 109 (R6): the saved products, WITHOUT creating anything (the context is read only); [] without any."""
    ruta = Path(db_path)
    if not ruta.is_file():
        return []
    conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'producto_suplemento'").fetchone():
            return [_producto(f) for f in conn.execute(f"SELECT {_PRODUCTO} FROM producto_suplemento "  # noqa: S608
                                                       "ORDER BY nombre COLLATE NOCASE, id")]
        return []
    finally:
        conn.close()


__all__ = ["FALTA_HORA_O_MINUTO", "FALTA_LA_HORA", "FORMAS", "HORAS_TIPICAS", "TIPOS_PRODUCTO", "TOMAS", "Suplementacion",
           "campos_producto", "describir", "horas_de_secciones", "leer_productos", "leer_todo", "repartir_leche",
           "resumen_dia", "validar_hora"]
