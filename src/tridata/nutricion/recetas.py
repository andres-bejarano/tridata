"""Recipes, fixed foods and pots (unit 011, activity `guardar-receta`, REC-7 to REC-9).

Their own tables in the same SQLite store as the food log. A recipe has versions (one per batch,
G-33): each keeps its ingredients with the kcal they had when saved, its pot and tare, its net
weight and its kcal/100 g, so changing a fixed food or a pot later never changes a saved version,
and nothing here ever touches the foods already logged (R-55). Pure functions: `normalizar`,
`kcal_100g`, `estado_version` (G-32) and `leer_cargas` (the rows of deficit.md, D7).
`Recetario.resolver` turns what Claude interpreted into what the list shows: the kcal of what is
saved when it matches (G-31), a visible proposal when it is only alike, and the estimate otherwise.
Business errors are `ValueError` with the text for Andrés; malformed data are `ErrorDatos`.

Unit 098 (REC-43, R-269..R-273): a fixed food keeps protein, carbs and fat (`proteina`, `hidratos`, `grasa`, on the
same base as its kcal) and where they come from (`macros_origen`: «estimado» or «etiqueta»); each ingredient line of a
recipe keeps the grams of its line (`proteina_g`, `hidratos_g`, `grasa_g`): a saved one takes its fixed food's, an
estimated one the team's. A recipe adds them up (`macros_total`) and spreads them as its kcal (`macros_100g`); one
line with kcal and without macros leaves it without (`sin_macros` says how many). What is logged from a saved thing
carries them in proportion to its kcal (`para_apuntar`). `macros_que_faltan` / `validar_macros` / `guardar_macros`
fill in, once, what is missing, only as «estimado» and only what Andrés accepts; what was logged never changes.

Unit 112 (REC-55, R-340..R-342, G-75, S-115): a recipe may have variants (`receta_variante`: «sin chorizo» with its own
kcal/100 g and, if given, its macros per 100 g; no ingredients nor weighing). Their names are unique inside their
recipe without minding capitals or accents. What is logged from one is «Receta · variante» with ITS kcal/100 g
(`para_apuntar("variante", …)`, origin «de tu receta «X» · variante»); saving, changing or deleting one never changes
its recipe nor anything logged, and deleting a recipe takes its variants with it.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..storage import DEFAULT_DB_PATH

KCAL_100G_MAX = 900  # G-35: nothing passes 900 kcal/100 g (pure fat)
POR = ("100g", "unidad")
MACROS_FIJO = ("proteina", "hidratos", "grasa")  # unit 098: on a fixed food, on the base of its kcal
MACROS = ("proteina_g", "hidratos_g", "grasa_g")  # unit 098: grams in a line, a recipe or what is logged
ORIGENES_MACROS = ("estimado", "etiqueta")
NO_CUADRA = 0.2  # unit 098 (R3): 4·P + 4·H + 9·G more than 20 % away from the kcal is warned, never blocked
_DE_ORIGEN = re.compile(r"^de tu (?:ingrediente|receta) «(.+?)»")  # unit 101: the `origen` of what was logged
_DE_VARIANTE = re.compile(r"^de tu receta «(.+?)» · (.+)$")  # unit 112: the `origen` of a variant
SEPARADOR_VARIANTE = " · "  # unit 112: «Receta · variante», the name of what is logged from a variant

_SCHEMA = """
CREATE TABLE IF NOT EXISTS receta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    alias TEXT NOT NULL DEFAULT '[]',
    nota TEXT,
    version_en_uso INTEGER NOT NULL DEFAULT 1,
    por_revisar INTEGER NOT NULL DEFAULT 0,
    creado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receta_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receta_id INTEGER NOT NULL REFERENCES receta(id),
    numero INTEGER NOT NULL,
    fecha TEXT NOT NULL,
    ingredientes TEXT NOT NULL,
    kcal_total REAL,
    olla_id INTEGER,
    olla TEXT,
    tara_g REAL,
    peso_lleno_g REAL,
    peso_neto_g REAL,
    kcal_100g REAL,
    kcal_100g_aprox REAL,
    UNIQUE (receta_id, numero)
);
CREATE TABLE IF NOT EXISTS alimento_fijo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    alias TEXT NOT NULL DEFAULT '[]',
    kcal REAL NOT NULL,
    por TEXT NOT NULL CHECK (por IN ('100g', 'unidad')),
    nota TEXT,
    comun INTEGER NOT NULL DEFAULT 0,
    proteina REAL,
    hidratos REAL,
    grasa REAL,
    macros_origen TEXT
);
CREATE TABLE IF NOT EXISTS comun_quitado (
    nombre TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS olla (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    tara_g REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS receta_variante (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receta_id INTEGER NOT NULL REFERENCES receta(id),
    nombre TEXT NOT NULL,
    kcal_100g REAL NOT NULL,
    proteina REAL,
    hidratos REAL,
    grasa REAL,
    creado TEXT NOT NULL
);
"""

_PESO = ("olla_id", "peso_lleno_g", "peso_neto_g")


class ErrorDatos(ValueError):
    """What the page sent is malformed (not a number, not a list…): a 400, not a business rule."""


class YaEsReceta(ValueError):
    """The name is already a recipe: G-34 says to offer saving it as another batch."""

    def __init__(self, nombre: str, receta_id: int) -> None:
        super().__init__(f"Ya tienes la receta «{nombre}»: ¿la guardas como otra tanda?")
        self.receta_id = receta_id


# -- pure functions ---------------------------------------------------------------------------

def normalizar(texto: str | None) -> str:
    """Lower case, no accents, single spaces: how names are compared (G-34)."""
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().split())


def _redondear(valor: float) -> float:
    return round(valor + 0.0, 1)


def kcal_100g(kcal: float, *, lleno: float | None = None, tara: float | None = None,
              neto: float | None = None) -> float:
    """kcal ÷ (full pot − tare) × 100, or kcal ÷ net weight × 100, to 1 decimal (G-32, Q-9)."""
    if neto is None:
        if lleno is None or tara is None:
            raise ValueError("pon el peso de la olla llena y elige la olla, o escribe el peso neto")
        if lleno <= tara:
            raise ValueError("la olla llena no puede pesar menos que su tara")
        neto = lleno - tara
    if neto <= 0:
        raise ValueError("el peso neto tiene que ser de más de 0 g")
    return _redondear(kcal / neto * 100)


def estado_version(version: dict) -> str:
    """G-32: `sin_kcal` with a doubtful ingredient, `definitiva` weighed, `provisional` otherwise."""
    if version.get("dudosos") or version.get("kcal_total") is None:
        return "sin_kcal"
    return "definitiva" if version.get("kcal_100g") is not None else "provisional"


# -- reading what the page sends ----------------------------------------------------------------

def _numero(datos: dict, clave: str, etiqueta: str | None = None) -> float | None:
    valor = datos.get(clave)
    if valor is None or valor == "":
        return None
    if isinstance(valor, bool):
        raise ErrorDatos(f"{etiqueta or clave}: {valor!r} no es un número")
    try:
        return float(valor.replace(",", ".")) if isinstance(valor, str) else float(valor)
    except (TypeError, ValueError):
        raise ErrorDatos(f"{etiqueta or clave}: {valor!r} no es un número") from None


def _texto(datos: dict, clave: str, obligatorio: bool = False) -> str | None:
    valor = datos.get(clave)
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        if obligatorio:
            raise ErrorDatos(f"falta el {clave}")
        return None
    if not isinstance(valor, str):
        raise ErrorDatos(f"{clave} no válido")
    return " ".join(valor.split())


def _alias(datos: dict) -> list[str]:
    valor = datos.get("alias")
    if valor is None or valor == "":
        return []
    if isinstance(valor, str):
        valor = valor.split(",")
    if not isinstance(valor, list) or not all(isinstance(a, str) for a in valor):
        raise ErrorDatos("los alias tienen que ser textos")
    vistos, alias = set(), []
    for a in (" ".join(x.split()) for x in valor):
        if a and normalizar(a) not in vistos:
            vistos.add(normalizar(a))
            alias.append(a)
    return alias


def _kcal_100g_valida(valor: float | None, que: str = "las kcal/100 g") -> None:
    if valor is None:
        return
    if math.isnan(valor):  # unit 112 (R6): «nan» is not a number of kcal (an infinite one passes the cap below)
        raise ValueError(f"{que} tienen que ser un número")
    if valor < 0:
        raise ValueError(f"{que} no pueden ser negativas")
    if valor > KCAL_100G_MAX:
        raise ValueError(f"nada pasa de {KCAL_100G_MAX} kcal/100 g")


def _ingrediente_crudo(crudo: Any) -> dict:
    if not isinstance(crudo, dict):
        raise ErrorDatos("cada ingrediente tiene que ser un objeto")
    nombre = _texto(crudo, "nombre", obligatorio=True)
    cantidad = _numero(crudo, "cantidad", f"{nombre}: cantidad")
    kcal = _numero(crudo, "kcal", f"{nombre}: kcal")
    unidad = crudo.get("unidad")
    if unidad is not None and not isinstance(unidad, str):
        raise ErrorDatos(f"{nombre}: unidad no válida")
    if cantidad is not None and cantidad <= 0:
        raise ValueError(f"«{nombre}»: la cantidad tiene que ser mayor que 0")
    if kcal is not None and kcal < 0:
        raise ValueError(f"«{nombre}»: las kcal no pueden ser negativas")
    macros = _macros_validos({k: _numero(crudo, k, f"{nombre}: {k}") for k in MACROS}, None, nombre or "")
    return {"nombre": nombre, "cantidad": cantidad, "unidad": (unidad or "").strip() or None, "kcal": kcal,
            "dudoso": bool(crudo.get("dudoso")) or crudo.get("estado") == "dudoso",
            "guardado": crudo.get("guardado") if isinstance(crudo.get("guardado"), str) else None,
            "macros": macros}


def _kcal_de_fijo(fijo: dict, cantidad: float | None, unidad: str | None) -> float | None:
    """The kcal of `cantidad` of a fixed food, or None if the unit does not fit (D6)."""
    if cantidad is None:
        return None
    if fijo["por"] == "100g" and unidad == "g":
        return _redondear(cantidad * fijo["kcal"] / 100)
    if fijo["por"] == "unidad" and unidad == "unidad":
        return _redondear(cantidad * fijo["kcal"])
    return None


# -- unit 098: macros ------------------------------------------------------------------------------------------------

def _macros_de_fijo(fijo: dict, cantidad: float | None, unidad: str | None) -> dict | None:
    """The grams of protein, carbs and fat of `cantidad` of a fixed food, as its kcal; None without them or unit."""
    if cantidad is None or any(fijo.get(k) is None for k in MACROS_FIJO):
        return None
    if fijo["por"] == "100g" and unidad == "g":
        factor = cantidad / 100
    elif fijo["por"] == "unidad" and unidad == "unidad":
        factor = cantidad
    else:
        return None
    return {g: _redondear(fijo[k] * factor) for k, g in zip(MACROS_FIJO, MACROS, strict=True)}


def _suma_macros(ingredientes: list[dict]) -> dict | None:
    """R1: the macros of the whole recipe, unrounded; None if a line with kcal has none (never half a total).

    A line of 0 kcal (water, salt) adds nothing and asks for nothing; a doubtful line leaves the recipe without kcal,
    so without macros too."""
    if not ingredientes or any(i.get("estado") == "dudoso" for i in ingredientes):
        return None
    total = dict.fromkeys(MACROS, 0.0)
    for i in ingredientes:
        if not i.get("kcal"):
            continue
        if any(i.get(k) is None for k in MACROS):
            return None
        for k in MACROS:
            total[k] += i[k]
    return total


def _sin_macros(ingredientes: list[dict]) -> int:
    """How many lines with kcal have no macros (what the recipe card says it is missing)."""
    return sum(1 for i in ingredientes if i.get("estado") != "dudoso" and i.get("kcal")
               and any(i.get(k) is None for k in MACROS))


def _escalar(macros: dict | None, factor: float | None) -> dict | None:
    if macros is None or factor is None:
        return None
    return {k: _redondear(macros[k] * factor) for k in MACROS}


def _macros_validos(valores: dict[str, float | None], por: str | None, quien: str = "") -> dict[str, float] | None:
    """All three or none; never negative; per 100 g they cannot add up to more than 100 g (R3). ValueError says why."""
    de = f"«{quien}»: " if quien else ""
    if all(v is None for v in valores.values()):
        return None
    if any(v is None for v in valores.values()):
        raise ValueError(f"{de}pon los tres macros (proteína, hidratos y grasa) o ninguno")
    if any(v < 0 for v in valores.values() if v is not None):
        raise ValueError(f"{de}los macros no pueden ser negativos")
    suma = sum(v for v in valores.values() if v is not None)
    if por == "100g" and suma > 100:
        raise ValueError(f"{de}proteína + hidratos + grasa suman {suma:g} g por 100 g, y no pueden pasar de 100 g")
    return {k: float(v) for k, v in valores.items() if v is not None}


# -- unit 112: the variants of a recipe -----------------------------------------------------------------------------

def nombre_variante(receta: str, variante: str) -> str:
    """«Garbanzos de la abuela · sin chorizo»: the name of what is logged from a variant (R-341)."""
    return f"{receta}{SEPARADOR_VARIANTE}{variante}"


def datos_variante(datos: dict) -> dict:
    """Unit 112 (R6): the name, the kcal/100 g (required, finite, from 0 to 900) and the macros per 100 g (all three
    or none) of a variant. `ErrorDatos` if malformed, `ValueError` with the words for Andrés otherwise."""
    nombre = _texto(datos, "nombre", obligatorio=True)
    try:
        kcal = _numero(datos, "kcal_100g", "las kcal/100 g")
        valores = {k: _numero(datos, k, f"{nombre}: {k}") for k in MACROS_FIJO}
    except OverflowError:
        raise ValueError("las kcal/100 g tienen que ser un número finito") from None
    if kcal is None:
        raise ValueError(f"faltan las kcal/100 g de la variante «{nombre}»")
    if not math.isfinite(kcal):
        raise ValueError("las kcal/100 g tienen que ser un número finito")
    _kcal_100g_valida(kcal)
    if any(v is not None and not math.isfinite(v) for v in valores.values()):
        raise ValueError(f"«{nombre}»: los macros tienen que ser números finitos")
    return {"nombre": nombre, "kcal_100g": kcal, "macros": _macros_validos(valores, "100g", nombre or "")}


def no_cuadra(kcal: float | None, proteina: float, hidratos: float, grasa: float) -> str | None:
    """R3: the warning when 4·P + 4·H + 9·G is more than 20 % away from the kcal (never a block); None if it fits."""
    if kcal is None:
        return None
    salen = 4 * proteina + 4 * hidratos + 9 * grasa
    if (kcal > 0 and abs(salen - kcal) / kcal > NO_CUADRA) or (kcal == 0 and salen > 0):
        return f"no cuadra con sus kcal (salen {salen:.0f} de {kcal:g}): revisa"
    return None


# -- deficit.md (D7) ------------------------------------------------------------------------------

# a number: «1.234» is 1234 (thousands point), «123,4» and «98.4» are decimals; never part of another number
_NUM = r"(?<![\d.,])(?:\d{1,3}(?:\.\d{3})+(?![\d,])|\d+[.,]\d{1,2}(?![\d.,])|\d+)"
_KCAL = re.compile(rf"({_NUM})\s*(?:kcal)?", re.I)
_GRAMOS = re.compile(rf"({_NUM})\s*g\b", re.I)
_KCAL_TOTAL = re.compile(rf"({_NUM})\s*kcal\b(?!\s*/)", re.I)  # «N kcal», not «N kcal/…»
_KCAL_POR = re.compile(rf"({_NUM})\s*kcal\s*/\s*(?:(100)\s*g\b|(?:unidad|ud)\b)", re.I)  # not «kcal/g»
_INGREDIENTES = re.compile(r"\d\s*(?:g|ml)\b| \+ ", re.I)  # a quantity in g/ml or a « + »
_UNA_UNIDAD = re.compile(r"^\s*1\s*(?:unidad|ud)\b", re.I)


@dataclass
class Carga:
    """One row of deficit.md read as what it becomes in the Recetas tab."""

    tipo: str  # "receta", "fijo", "olla" or "revisar" (a recipe to review, not used when dictating)
    nombre: str
    texto: str
    nota: str | None = None
    kcal_total: float | None = None
    peso_neto_g: float | None = None
    kcal_100g: float | None = None
    kcal: float | None = None
    por: str | None = None
    tara_g: float | None = None
    motivo: str | None = None

    def describir(self) -> str:
        if self.tipo == "olla":
            return f"olla «{self.nombre}» · tara {self.tara_g:g} g"
        if self.tipo == "fijo":
            return f"alimento fijo «{self.nombre}» · {self.kcal:g} kcal/{'100 g' if self.por == '100g' else 'unidad'}"
        if self.tipo == "revisar":
            return f"receta «{self.nombre}» · por revisar ({self.motivo})"
        if self.kcal_100g is not None:
            return f"receta «{self.nombre}» · definitiva · {self.kcal_100g:g} kcal/100 g"
        return f"receta «{self.nombre}» · provisional · {self.kcal_total:g} kcal la receta entera"


def _cifra(texto: str) -> float:
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", texto):
        return float(texto.replace(".", ""))
    return float(texto.replace(",", "."))


def _kcal_md(texto: str) -> float | None:
    m = _KCAL.search(texto.replace("**", ""))
    return _cifra(m.group(1)) if m else None


def _gramos(texto: str) -> float | None:
    m = _GRAMOS.search(texto)
    return _cifra(m.group(1)) if m else None


def _nombre_y_resto(plato: str) -> tuple[str, str | None]:
    """The name is the text up to the first « (»; the rest (ingredients, clarification, tail) is the note."""
    nombre, separador, resto = plato.partition(" (")
    if not separador:
        return plato, None
    resto = "(" + resto
    cierre, abiertos = None, 0  # strip the brackets when they wrap the whole rest, as before
    for i, letra in enumerate(resto):
        abiertos += {"(": 1, ")": -1}.get(letra, 0)
        if abiertos == 0:
            cierre = i
            break
    if cierre == len(resto) - 1:
        resto = resto[1:-1]
    return nombre.strip(), resto.strip() or None


def _limpio(celda: str) -> str:
    return " ".join(celda.replace("**", "").split())


def _leer_referencia(celdas: list[str]) -> Carga | None:
    texto = " | ".join(celdas)
    if not celdas or not _limpio(celdas[0]):
        return None
    plato = _limpio(celdas[0])
    racion = _limpio(celdas[1]) if len(celdas) > 1 else ""
    kcal = _kcal_md(celdas[2]) if len(celdas) > 2 else None
    nombre, nota = _nombre_y_resto(plato)
    ingredientes = bool(nota and _INGREDIENTES.search(nota))  # otherwise the brackets are a clarification

    def revisar(motivo: str, kcal_total: float | None = kcal) -> Carga:
        return Carga("revisar", nombre, texto, nota=nota or f"de deficit.md: {texto}", kcal_total=kcal_total,
                     motivo=motivo)

    if kcal is None or kcal <= 0:
        return _leer_prosa(nombre, nota, plato, racion, texto, ingredientes, revisar)
    gramos = _gramos(racion)
    if ingredientes:
        if gramos and "total" in racion.lower():
            por_100 = _redondear(kcal / gramos * 100)
            if por_100 > KCAL_100G_MAX:
                return revisar("más de 900 kcal/100 g")
            return Carga("receta", nombre, texto, nota=nota, kcal_total=kcal, peso_neto_g=gramos, kcal_100g=por_100)
        return Carga("receta", nombre, texto, nota=nota, kcal_total=kcal)
    if gramos:
        por_100 = _redondear(kcal / gramos * 100)
        if por_100 > KCAL_100G_MAX:
            return revisar("más de 900 kcal/100 g")
        return Carga("fijo", nombre, texto, nota=nota, kcal=por_100, por="100g")
    if _UNA_UNIDAD.match(racion):
        return Carga("fijo", nombre, texto, nota=nota, kcal=kcal, por="unidad")
    return revisar("la ración no dice gramos ni «1 unidad»")


def _leer_prosa(nombre: str, nota: str | None, plato: str, prosa: str, texto: str, ingredientes: bool,
                revisar: Any) -> Carga:
    """Bug 012 · R3: a row whose kcal are written in the 2nd column, with text around them."""
    if " · " in plato:
        return revisar("varios alimentos en una fila", None)
    total = _KCAL_TOTAL.search(prosa)
    kcal_total = _cifra(total.group(1)) if total else None
    por = None if "PROVISIONAL" in prosa else _KCAL_POR.search(prosa)
    if por:
        valor = _cifra(por.group(1))
        por_100 = por.group(2) is not None
        if por_100 and valor > KCAL_100G_MAX:
            return revisar("más de 900 kcal/100 g", kcal_total)
        if not ingredientes:
            return Carga("fijo", nombre, texto, nota=nota, kcal=valor, por="100g" if por_100 else "unidad")
        if por_100 and kcal_total:
            return Carga("receta", nombre, texto, nota=nota, kcal_total=kcal_total, kcal_100g=valor)
        return revisar("trae kcal por cantidad pero no las kcal de la receta entera", kcal_total)
    if kcal_total:
        return Carga("receta", nombre, texto, nota=nota, kcal_total=kcal_total)
    return revisar("sin kcal", None)


def leer_cargas(referencias: list[list[str]], taras: list[list[str]]) -> list[Carga]:
    """The rows of «Referencias calóricas fijas» and «Taras de ollas» as recipes, fixed foods and pots.

    A dish with its ingredients in brackets is a recipe (definitive if the portion says a total
    weight, provisional otherwise); without brackets, a portion in grams is a fixed food per
    100 g and «1 unidad» one per unit; anything else is a recipe to review. A pot row is name and
    grams; a pot row that cannot be read is left out (`motivo` says why).
    """
    cargas = [c for c in (_leer_referencia(f) for f in referencias) if c is not None]
    for celdas in taras:
        nombre = _limpio(celdas[0]) if celdas else ""
        if not nombre:
            continue
        gramos = _gramos(celdas[1]) if len(celdas) > 1 else None
        cargas.append(Carga("olla", nombre, " | ".join(celdas), tara_g=gramos,
                            motivo=None if gramos else "no dice su peso en gramos"))
    return cargas


# -- the repository ---------------------------------------------------------------------------------

class Recetario:
    """Recipes (with versions), fixed foods and pots over the TriData SQLite file."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            columnas = {c["name"] for c in conn.execute("PRAGMA table_info(alimento_fijo)")}
            if "comun" not in columnas:  # a base from before unit 026
                conn.execute("ALTER TABLE alimento_fijo ADD COLUMN comun INTEGER NOT NULL DEFAULT 0")
            for columna, tipo in (("proteina", "REAL"), ("hidratos", "REAL"), ("grasa", "REAL"),
                                  ("macros_origen", "TEXT")):  # a base from before unit 098
                if columna not in columnas:
                    conn.execute(f"ALTER TABLE alimento_fijo ADD COLUMN {columna} {tipo}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- reading ----------------------------------------------------------------------------------

    @staticmethod
    def _fijo_fila(f: sqlite3.Row) -> dict:
        return {"id": f["id"], "nombre": f["nombre"], "alias": json.loads(f["alias"]), "kcal": f["kcal"],
                "por": f["por"], "nota": f["nota"], "comun": bool(f["comun"]), "proteina": f["proteina"],
                "hidratos": f["hidratos"], "grasa": f["grasa"], "macros_origen": f["macros_origen"]}

    @staticmethod
    def _version_fila(v: sqlite3.Row, en_uso: int) -> dict:
        ingredientes = json.loads(v["ingredientes"])
        datos = {k: v[k] for k in ("numero", "fecha", "kcal_total", "olla_id", "olla", "tara_g", "peso_lleno_g",
                                   "peso_neto_g", "kcal_100g", "kcal_100g_aprox")}
        datos["ingredientes"] = ingredientes
        datos["estimados"] = sum(i["estado"] == "estimado" for i in ingredientes)
        datos["dudosos"] = sum(i["estado"] == "dudoso" for i in ingredientes)
        datos["estado"] = estado_version(datos)
        datos["en_uso"] = v["numero"] == en_uso
        suma = _suma_macros(ingredientes)  # unit 098 (R1): the whole recipe, and per 100 g as its kcal
        datos["macros_total"] = _escalar(suma, 1)
        datos["macros_100g"] = _escalar(suma, Recetario._factor_100g(datos))
        datos["sin_macros"] = _sin_macros(ingredientes)
        return datos

    @staticmethod
    def _factor_100g(v: dict) -> float | None:
        """What part of the whole recipe 100 g are: its net weight, or (not weighed) its approximate kcal/100 g."""
        if v.get("kcal_100g") is not None and v.get("peso_neto_g"):
            return 100 / v["peso_neto_g"]
        if v.get("kcal_total"):
            por_100 = v["kcal_100g"] if v.get("kcal_100g") is not None else v.get("kcal_100g_aprox")
            if por_100 is not None:
                return por_100 / v["kcal_total"]
        return None

    @staticmethod
    def _variante_fila(v: sqlite3.Row) -> dict:
        """Unit 112: a variant with its kcal/100 g and its macros per 100 g (as a recipe's `macros_100g`), or None."""
        macros = None if any(v[k] is None for k in MACROS_FIJO) else {
            g: v[k] for k, g in zip(MACROS_FIJO, MACROS, strict=True)}
        return {"id": v["id"], "receta_id": v["receta_id"], "nombre": v["nombre"], "kcal_100g": v["kcal_100g"],
                "macros_100g": macros}

    def _variantes(self, conn: sqlite3.Connection, receta_id: int) -> list[dict]:
        return [self._variante_fila(v) for v in conn.execute(
            "SELECT * FROM receta_variante WHERE receta_id = ? ORDER BY nombre COLLATE NOCASE, id", (receta_id,))]

    def _receta_de(self, conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
        versiones = [self._version_fila(v, r["version_en_uso"]) for v in conn.execute(
            "SELECT * FROM receta_version WHERE receta_id = ? ORDER BY numero", (r["id"],))]
        en_uso = next((v for v in versiones if v["en_uso"]), versiones[-1] if versiones else None)
        return {"id": r["id"], "nombre": r["nombre"], "alias": json.loads(r["alias"]), "nota": r["nota"],
                "por_revisar": bool(r["por_revisar"]), "version_en_uso": r["version_en_uso"], "en_uso": en_uso,
                "versiones": versiones, "variantes": self._variantes(conn, r["id"])}

    def recetas(self) -> list[dict]:
        with self._connect() as conn:
            filas = conn.execute("SELECT * FROM receta ORDER BY nombre COLLATE NOCASE, id").fetchall()
            return [self._receta_de(conn, r) for r in filas]

    def receta(self, receta_id: int) -> dict:
        with self._connect() as conn:
            return self._receta_de(conn, self._leer(conn, "receta", receta_id))

    def fijos(self) -> list[dict]:
        with self._connect() as conn:
            return [self._fijo_fila(f) for f in conn.execute(
                "SELECT * FROM alimento_fijo ORDER BY comun, nombre COLLATE NOCASE, id")]

    def ollas(self) -> list[dict]:
        with self._connect() as conn:
            return [dict(o) for o in conn.execute("SELECT id, nombre, tara_g FROM olla ORDER BY nombre COLLATE NOCASE, id")]

    def listado(self) -> dict:
        return {"recetas": self.recetas(), "fijos": self.fijos(), "ollas": self.ollas()}

    def nombres(self) -> list[str]:
        """Names and aliases of the recipes and fixed foods that can be used when dictating (R-44).

        Never a pot, a number or an ingredient; a recipe still to review is left out.
        """
        nombres: list[str] = []
        with self._connect() as conn:
            for fila in conn.execute("SELECT nombre, alias FROM receta WHERE por_revisar = 0 ORDER BY id"):
                nombres += [fila["nombre"], *json.loads(fila["alias"])]
            for fila in conn.execute("SELECT nombre, alias FROM alimento_fijo ORDER BY id"):
                nombres += [fila["nombre"], *json.loads(fila["alias"])]
        return nombres

    @staticmethod
    def _leer(conn: sqlite3.Connection, tabla: str, ident: int) -> sqlite3.Row:
        fila = conn.execute(f"SELECT * FROM {tabla} WHERE id = ?", (ident,)).fetchone()  # noqa: S608 - fixed names
        if fila is None:
            raise KeyError(ident)
        return fila

    # -- G-34: one name, one thing ---------------------------------------------------------------------

    @staticmethod
    def _duenos(conn: sqlite3.Connection) -> dict[str, tuple[str, int, str]]:
        """normalized name or alias → (kind, id, name)."""
        duenos: dict[str, tuple[str, int, str]] = {}
        for tabla, tipo in (("receta", "receta"), ("alimento_fijo", "fijo")):
            for f in conn.execute(f"SELECT id, nombre, alias FROM {tabla} ORDER BY id"):  # noqa: S608 - fixed names
                for n in [f["nombre"], *json.loads(f["alias"])]:
                    duenos.setdefault(normalizar(n), (tipo, f["id"], f["nombre"]))
        for f in conn.execute("SELECT id, nombre FROM olla ORDER BY id"):
            duenos.setdefault(normalizar(f["nombre"]), ("olla", f["id"], f["nombre"]))
        return duenos

    @staticmethod
    def _quitar_comun(conn: sqlite3.Connection, fila: sqlite3.Row) -> None:
        """Unit 026 (R5, R7): a common food that goes is written down, so it is never loaded again."""
        conn.execute("INSERT OR IGNORE INTO comun_quitado (nombre) VALUES (?)", (normalizar(fila["nombre"]),))
        conn.execute("DELETE FROM alimento_fijo WHERE id = ?", (fila["id"],))

    def _unico(self, conn: sqlite3.Connection, nombres: list[str], propio: tuple[str, int | None]) -> None:
        """G-34; unit 026 (R7): a common food with one of these names gives way to what is being saved."""
        claves = {normalizar(n) for n in nombres}
        for f in conn.execute("SELECT * FROM alimento_fijo WHERE comun = 1").fetchall():
            if ("fijo", f["id"]) != propio and claves & {normalizar(n) for n in [f["nombre"], *json.loads(f["alias"])]}:
                self._quitar_comun(conn, f)
        duenos = self._duenos(conn)
        for n in nombres:
            dueno = duenos.get(normalizar(n))
            if dueno is None or (dueno[0], dueno[1]) == propio:
                continue
            tipo, ident, nombre = dueno
            if tipo == "receta" and propio == ("receta", None):
                raise YaEsReceta(nombre, ident)
            que = {"receta": "tu receta", "fijo": "tu ingrediente", "olla": "tu olla"}[tipo]  # S-21: «ingrediente» on screen
            raise ValueError(f"«{normalizar(n)}» ya es {que} «{nombre}»")

    # -- fixed foods ----------------------------------------------------------------------------------

    @staticmethod
    def _datos_fijo(datos: dict) -> dict:
        nombre = _texto(datos, "nombre", obligatorio=True)
        kcal = _numero(datos, "kcal")
        por = datos.get("por")
        if kcal is None:
            raise ErrorDatos("faltan las kcal")
        if por not in POR:
            raise ErrorDatos("«por» tiene que ser 100g o unidad")
        if por == "100g":
            _kcal_100g_valida(kcal)
        elif kcal < 0:
            raise ValueError("las kcal por unidad no pueden ser negativas")
        f = {"nombre": nombre, "alias": _alias(datos), "kcal": kcal, "por": por, "nota": _texto(datos, "nota")}
        if any(k in datos for k in MACROS_FIJO):  # unit 098 (R7): sent by hand, all three or none
            f["macros"] = _macros_validos({k: _numero(datos, k) for k in MACROS_FIJO}, por)
        return f

    def crear_fijo(self, datos: dict) -> dict:
        """Unit 098 (R7): macros written by hand are «de etiqueta»."""
        f = self._datos_fijo(datos)
        macros = f.get("macros")
        with self._connect() as conn:
            self._unico(conn, [f["nombre"], *f["alias"]], ("fijo", None))
            cur = conn.execute(
                "INSERT INTO alimento_fijo (nombre, alias, kcal, por, nota, proteina, hidratos, grasa, macros_origen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f["nombre"], json.dumps(f["alias"], ensure_ascii=False), f["kcal"], f["por"], f["nota"],
                 *(macros[k] if macros else None for k in MACROS_FIJO), "etiqueta" if macros else None))
            return self._fijo_fila(self._leer(conn, "alimento_fijo", int(cur.lastrowid or 0)))

    def cambiar_fijo(self, fijo_id: int, datos: dict) -> dict:
        """A2: change it in place. What was logged and the saved recipe versions keep their kcal.

        Unit 098 (R7, C-345): macros not sent stay as they were; sent and different from the saved ones, they are
        «de etiqueta» (Andrés wrote them with the label in front); sent as they were, they keep their origin. The
        recipes in use take them for what is logged from then on (their kcal do not change)."""
        with self._connect() as conn:
            antes = self._leer(conn, "alimento_fijo", fijo_id)
            f = self._datos_fijo(datos)
            self._unico(conn, [f["nombre"], *f["alias"]], ("fijo", fijo_id))
            if antes["comun"]:  # unit 026 (R4): corrected, it is his; the common one is not loaded again
                conn.execute("INSERT OR IGNORE INTO comun_quitado (nombre) VALUES (?)", (normalizar(antes["nombre"]),))
            viejos = {k: antes[k] for k in MACROS_FIJO}
            origen = antes["macros_origen"]
            if "macros" in f:
                nuevos = f["macros"] or dict.fromkeys(MACROS_FIJO)
                if nuevos != viejos:
                    origen = "etiqueta" if f["macros"] else None
                viejos = nuevos
            conn.execute("UPDATE alimento_fijo SET nombre = ?, alias = ?, kcal = ?, por = ?, nota = ?, comun = 0, "
                         "proteina = ?, hidratos = ?, grasa = ?, macros_origen = ? WHERE id = ?",
                         (f["nombre"], json.dumps(f["alias"], ensure_ascii=False), f["kcal"], f["por"], f["nota"],
                          *(viejos[k] for k in MACROS_FIJO), origen, fijo_id))
            self._recalcular_en_uso(conn)
            return self._fijo_fila(self._leer(conn, "alimento_fijo", fijo_id))

    def borrar_fijo(self, fijo_id: int, confirmado: bool = False) -> dict:
        """Unit 026 (R5): a common one removed is written down and does not come back at the next start."""
        fijo = self._borrar("alimento_fijo", fijo_id, confirmado, self._fijo_fila)
        if fijo["comun"]:
            with self._connect() as conn:
                conn.execute("INSERT OR IGNORE INTO comun_quitado (nombre) VALUES (?)", (normalizar(fijo["nombre"]),))
        return fijo

    def cargar_comunes(self, comunes: Any) -> int:
        """Unit 026 (R1, R2): add the common foods that are missing, marked `comun`; returns how many.

        One whose name is already a recipe, ingredient, pot or alias (without capitals or accents) is not loaded,
        nor one removed or corrected before (`comun_quitado`): loading twice never duplicates nor touches his.

        Unit 101 (R-283, C-356, C-357): each one comes with its macros as «estimado» if it brings them; one already
        loaded (`comun` = 1, so never corrected) with none of the three takes the list's. One with macros (of the label
        or estimated), corrected (`comun` = 0) or removed is never touched, nor is anything already logged.
        """
        nuevos, rellenos = 0, 0
        with self._connect() as conn:
            duenos = self._duenos(conn)
            quitados = {f["nombre"] for f in conn.execute("SELECT nombre FROM comun_quitado")}
            sin_macros = {normalizar(f["nombre"]): f["id"] for f in conn.execute(
                "SELECT id, nombre FROM alimento_fijo WHERE comun = 1 AND proteina IS NULL AND hidratos IS NULL "
                "AND grasa IS NULL")}
            for c in comunes:
                clave = normalizar(c["nombre"])
                macros = [c.get(k) for k in MACROS_FIJO]
                trae = all(m is not None for m in macros)
                if clave in sin_macros and trae:
                    conn.execute("UPDATE alimento_fijo SET proteina = ?, hidratos = ?, grasa = ?, "
                                 "macros_origen = 'estimado' WHERE id = ?", (*macros, sin_macros[clave]))
                    rellenos += 1
                if clave in duenos or clave in quitados:
                    continue
                conn.execute("INSERT INTO alimento_fijo (nombre, alias, kcal, por, nota, comun, proteina, hidratos, "
                             "grasa, macros_origen) VALUES (?, '[]', ?, ?, ?, 1, ?, ?, ?, ?)",
                             (c["nombre"], c["kcal"], c["por"], c.get("nota"),
                              *(macros if trae else [None] * 3), "estimado" if trae else None))
                duenos[clave] = ("fijo", 0, c["nombre"])
                nuevos += 1
            if rellenos:  # the recipes in use with those saved ingredients take them (their kcal do not change)
                self._recalcular_en_uso(conn)
        return nuevos

    # -- pots -------------------------------------------------------------------------------------------

    @staticmethod
    def _datos_olla(datos: dict) -> dict:
        nombre = _texto(datos, "nombre", obligatorio=True)
        tara = _numero(datos, "tara_g", "tara")
        if tara is None or tara <= 0:
            raise ValueError("la tara tiene que ser de más de 0 g")
        return {"nombre": nombre, "tara_g": tara}

    def crear_olla(self, datos: dict) -> dict:
        nombre = _texto(datos, "nombre", obligatorio=True) or ""
        with self._connect() as conn:
            self._unico(conn, [nombre], ("olla", None))
            o = self._datos_olla(datos)
            cur = conn.execute("INSERT INTO olla (nombre, tara_g) VALUES (?, ?)", (o["nombre"], o["tara_g"]))
            return dict(self._leer(conn, "olla", int(cur.lastrowid or 0)))

    def cambiar_olla(self, olla_id: int, datos: dict) -> dict:
        """A2: the next batch weighed in it uses the new tare; saved versions keep theirs."""
        with self._connect() as conn:
            self._leer(conn, "olla", olla_id)
            o = self._datos_olla(datos)
            self._unico(conn, [o["nombre"]], ("olla", olla_id))
            conn.execute("UPDATE olla SET nombre = ?, tara_g = ? WHERE id = ?", (o["nombre"], o["tara_g"], olla_id))
            return dict(self._leer(conn, "olla", olla_id))

    def borrar_olla(self, olla_id: int, confirmado: bool = False) -> dict:
        return self._borrar("olla", olla_id, confirmado, dict)

    def _borrar(self, tabla: str, ident: int, confirmado: bool, como: Any) -> dict:
        """R-53: only with `confirmado`. KeyError if it does not exist."""
        with self._connect() as conn:
            fila = como(self._leer(conn, tabla, ident))
            if confirmado is not True:
                raise ValueError("hay que confirmar antes de borrar: no se ha borrado nada")
            conn.execute(f"DELETE FROM {tabla} WHERE id = ?", (ident,))  # noqa: S608 - fixed names
        return fila

    # -- recipes ----------------------------------------------------------------------------------------

    def _fijos_por_nombre(self, conn: sqlite3.Connection) -> dict[str, dict]:
        por_nombre: dict[str, dict] = {}
        for f in conn.execute("SELECT * FROM alimento_fijo ORDER BY id"):
            fijo = self._fijo_fila(f)
            for n in [fijo["nombre"], *fijo["alias"]]:
                por_nombre.setdefault(normalizar(n), fijo)
        return por_nombre

    def _ingredientes(self, conn: sqlite3.Connection, crudos: Any) -> list[dict]:
        """R-40, R-47, R-48: fixed foods with THEIR kcal (never the page's), the rest estimated or doubtful."""
        if not isinstance(crudos, list):
            raise ErrorDatos("los ingredientes tienen que ser una lista")
        fijos = self._fijos_por_nombre(conn)
        resultado = []
        for crudo in crudos:
            i = _ingrediente_crudo(crudo)
            fijo = fijos.get(normalizar(i["guardado"])) or fijos.get(normalizar(i["nombre"]))
            kcal = _kcal_de_fijo(fijo, i["cantidad"], i["unidad"]) if fijo and not i["dudoso"] else None
            if kcal is not None and fijo is not None:  # unit 098: its macros, the fixed food's (never the page's)
                resultado.append({"nombre": fijo["nombre"], "cantidad": i["cantidad"], "unidad": i["unidad"],
                                  "kcal": kcal, "estado": "guardado",
                                  **(_macros_de_fijo(fijo, i["cantidad"], i["unidad"]) or dict.fromkeys(MACROS))})
            elif i["dudoso"] or i["kcal"] is None:
                resultado.append({"nombre": i["nombre"], "cantidad": i["cantidad"], "unidad": i["unidad"],
                                  "kcal": None, "estado": "dudoso", **dict.fromkeys(MACROS)})
            else:  # unit 098: an estimated line keeps the macros that came with it (the team's, or none)
                resultado.append({"nombre": i["nombre"], "cantidad": i["cantidad"], "unidad": i["unidad"],
                                  "kcal": _redondear(i["kcal"]), "estado": "estimado",
                                  **{k: (_redondear(i["macros"][k]) if i["macros"] else None) for k in MACROS}})
        return resultado

    def _version(self, conn: sqlite3.Connection, datos: dict, base: dict | None) -> dict:
        """A version's columns from what the page sent; keys it does not send keep `base`'s values."""
        base = base or {"ingredientes": [], "kcal_total": None, "olla_id": None, "olla": None, "tara_g": None,
                        "peso_lleno_g": None, "peso_neto_g": None, "kcal_100g_aprox": None}
        if "ingredientes" in datos:
            ingredientes = self._ingredientes(conn, datos["ingredientes"])
            if not ingredientes:
                raise ValueError("añade al menos un ingrediente")
            kcal_total: float | None = _redondear(sum(i["kcal"] or 0 for i in ingredientes))
        else:
            ingredientes, kcal_total = base["ingredientes"], base["kcal_total"]
        aprox = _numero(datos, "kcal_100g_aprox", "kcal/100 g aproximadas") if "kcal_100g_aprox" in datos \
            else base["kcal_100g_aprox"]
        _kcal_100g_valida(aprox, "las kcal/100 g aproximadas")
        if any(k in datos for k in _PESO):
            olla_id = datos.get("olla_id") or None
            lleno = _numero(datos, "peso_lleno_g", "olla llena")
            neto = _numero(datos, "peso_neto_g", "peso neto")
            olla = tara = None
            if olla_id is not None:
                if isinstance(olla_id, bool) or not isinstance(olla_id, int):
                    raise ErrorDatos("olla no válida")
                try:
                    fila = self._leer(conn, "olla", olla_id)
                except KeyError:
                    raise ValueError("esa olla ya no está en tus taras") from None
                olla, tara = fila["nombre"], fila["tara_g"]
            if lleno is not None:
                if tara is None:
                    raise ValueError("elige la olla en la que la has pesado, o escribe el peso neto")
                if lleno <= tara:
                    raise ValueError("la olla llena no puede pesar menos que su tara")
                neto = lleno - tara
            elif neto is not None:
                olla_id = olla = tara = None
                if neto <= 0:
                    raise ValueError("el peso neto tiene que ser de más de 0 g")
            else:
                olla_id = olla = tara = None
        else:
            olla_id, olla, tara = base["olla_id"], base["olla"], base["tara_g"]
            lleno, neto = base["peso_lleno_g"], base["peso_neto_g"]
        dudosos = sum(i["estado"] == "dudoso" for i in ingredientes)
        por_100 = None
        if neto is not None and kcal_total is not None and not dudosos:
            por_100 = kcal_100g(kcal_total, neto=neto)
            if por_100 > KCAL_100G_MAX:
                raise ValueError(f"salen {por_100:g} kcal/100 g y nada pasa de {KCAL_100G_MAX} kcal/100 g: revisa el peso")
        return {"ingredientes": ingredientes, "kcal_total": kcal_total, "olla_id": olla_id, "olla": olla,
                "tara_g": tara, "peso_lleno_g": lleno, "peso_neto_g": neto, "kcal_100g": por_100,
                "kcal_100g_aprox": aprox}

    @staticmethod
    def _insertar_version(conn: sqlite3.Connection, receta_id: int, numero: int, fecha: date, v: dict) -> None:
        conn.execute(
            "INSERT INTO receta_version (receta_id, numero, fecha, ingredientes, kcal_total, olla_id, olla, tara_g, "
            "peso_lleno_g, peso_neto_g, kcal_100g, kcal_100g_aprox) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (receta_id, numero, fecha.isoformat(), json.dumps(v["ingredientes"], ensure_ascii=False), v["kcal_total"],
             v["olla_id"], v["olla"], v["tara_g"], v["peso_lleno_g"], v["peso_neto_g"], v["kcal_100g"],
             v["kcal_100g_aprox"]))

    def crear_receta(self, datos: dict, hoy: date) -> dict:
        """A new recipe with its first version. `YaEsReceta` if the name is already a recipe (R-51)."""
        nombre = _texto(datos, "nombre", obligatorio=True) or ""
        alias = _alias(datos)
        with self._connect() as conn:
            self._unico(conn, [nombre, *alias], ("receta", None))
            if "ingredientes" not in datos:
                raise ValueError("añade al menos un ingrediente")
            v = self._version(conn, datos, None)
            cur = conn.execute("INSERT INTO receta (nombre, alias, nota, version_en_uso, por_revisar, creado) "
                               "VALUES (?, ?, ?, 1, 0, ?)",
                               (nombre, json.dumps(alias, ensure_ascii=False), _texto(datos, "nota"),
                                datetime.now().isoformat(timespec="seconds")))
            receta_id = int(cur.lastrowid or 0)
            self._insertar_version(conn, receta_id, 1, hoy, v)
            return self._receta_de(conn, self._leer(conn, "receta", receta_id))

    def otra_tanda(self, receta_id: int, datos: dict, hoy: date) -> dict:
        """R-46: a new version, used from now on; the earlier ones stay to go back to."""
        with self._connect() as conn:
            self._leer(conn, "receta", receta_id)
            if "ingredientes" not in datos:
                raise ValueError("añade al menos un ingrediente")
            v = self._version(conn, datos, None)
            numero = conn.execute("SELECT COALESCE(MAX(numero), 0) + 1 FROM receta_version WHERE receta_id = ?",
                                  (receta_id,)).fetchone()[0]
            self._insertar_version(conn, receta_id, numero, hoy, v)
            conn.execute("UPDATE receta SET version_en_uso = ?, por_revisar = 0 WHERE id = ?", (numero, receta_id))
            return self._receta_de(conn, self._leer(conn, "receta", receta_id))

    def corregir(self, receta_id: int, datos: dict) -> dict:
        """D4: change the version in use (and the name, alias or note if sent) and recompute.

        Saving it also marks a recipe to review as reviewed (D7).
        """
        with self._connect() as conn:
            fila = self._leer(conn, "receta", receta_id)
            actual = self._receta_de(conn, fila)["en_uso"]
            nombre = _texto(datos, "nombre", obligatorio=True) if "nombre" in datos else fila["nombre"]
            alias = _alias(datos) if "alias" in datos else json.loads(fila["alias"])
            nota = _texto(datos, "nota") if "nota" in datos else fila["nota"]
            self._unico(conn, [nombre or "", *alias], ("receta", receta_id))
            v = self._version(conn, datos, actual)
            conn.execute(
                "UPDATE receta_version SET ingredientes = ?, kcal_total = ?, olla_id = ?, olla = ?, tara_g = ?, "
                "peso_lleno_g = ?, peso_neto_g = ?, kcal_100g = ?, kcal_100g_aprox = ? WHERE receta_id = ? AND numero = ?",
                (json.dumps(v["ingredientes"], ensure_ascii=False), v["kcal_total"], v["olla_id"], v["olla"],
                 v["tara_g"], v["peso_lleno_g"], v["peso_neto_g"], v["kcal_100g"], v["kcal_100g_aprox"], receta_id,
                 fila["version_en_uso"]))
            conn.execute("UPDATE receta SET nombre = ?, alias = ?, nota = ?, por_revisar = 0 WHERE id = ?",
                         (nombre, json.dumps(alias, ensure_ascii=False), nota, receta_id))
            return self._receta_de(conn, self._leer(conn, "receta", receta_id))

    def usar_version(self, receta_id: int, numero: int) -> dict:
        """Go back to (or forward to) another batch. KeyError if that version does not exist."""
        with self._connect() as conn:
            self._leer(conn, "receta", receta_id)
            if conn.execute("SELECT 1 FROM receta_version WHERE receta_id = ? AND numero = ?",
                            (receta_id, numero)).fetchone() is None:
                raise KeyError(numero)
            conn.execute("UPDATE receta SET version_en_uso = ? WHERE id = ?", (numero, receta_id))
            return self._receta_de(conn, self._leer(conn, "receta", receta_id))

    def borrar_receta(self, receta_id: int, confirmado: bool = False) -> dict:
        """R-53: only with `confirmado`; the foods already logged keep their kcal and origin (R-55)."""
        with self._connect() as conn:
            receta = self._receta_de(conn, self._leer(conn, "receta", receta_id))
            if confirmado is not True:
                raise ValueError("hay que confirmar antes de borrar: no se ha borrado nada")
            conn.execute("DELETE FROM receta_variante WHERE receta_id = ?", (receta_id,))  # unit 112 (S-115)
            conn.execute("DELETE FROM receta_version WHERE receta_id = ?", (receta_id,))
            conn.execute("DELETE FROM receta WHERE id = ?", (receta_id,))
        return receta

    # -- unit 112 (R-340..R-342, G-75, S-115): the variants of a recipe -------------------------------------------------

    @staticmethod
    def _repetida(conn: sqlite3.Connection, receta_id: int, nombre: str, propia: int | None) -> str | None:
        """The name of another variant of that recipe with the same name, without capitals nor accents."""
        for v in conn.execute("SELECT id, nombre FROM receta_variante WHERE receta_id = ?", (receta_id,)):
            if v["id"] != propia and normalizar(v["nombre"]) == normalizar(nombre):
                return v["nombre"]
        return None

    def _comprobada(self, conn: sqlite3.Connection, receta_id: int, datos: dict, propia: int | None) -> dict:
        limpio = datos_variante(datos)
        receta = self._leer(conn, "receta", receta_id)
        repetida = self._repetida(conn, receta_id, limpio["nombre"], propia)
        if repetida is not None:
            raise ValueError(f"«{receta['nombre']}» ya tiene la variante «{repetida}»")
        return limpio

    def comprobar_variante(self, receta_id: int, datos: dict, variante_id: int | None = None) -> dict:
        """What `crear_variante` (or `cambiar_variante` of `variante_id`) would save, checked; writes nothing."""
        with self._connect() as conn:
            if variante_id is not None:
                antes = self._leer(conn, "receta_variante", variante_id)
                datos = self._con_lo_de_antes(antes, datos)
            return self._comprobada(conn, receta_id, datos, variante_id)

    @staticmethod
    def _con_lo_de_antes(antes: sqlite3.Row, datos: dict) -> dict:
        """A change keeps the name and the macros it does not send (the three go together)."""
        completo = {"nombre": antes["nombre"], "kcal_100g": antes["kcal_100g"], **datos}
        if not any(k in datos for k in MACROS_FIJO):
            completo.update({k: antes[k] for k in MACROS_FIJO})
        return completo

    def variante(self, variante_id: int) -> dict:
        """One variant with the name of its recipe (`receta`); KeyError if it does not exist."""
        with self._connect() as conn:
            v = self._leer(conn, "receta_variante", variante_id)
            return {**self._variante_fila(v), "receta": self._leer(conn, "receta", v["receta_id"])["nombre"]}

    def crear_variante(self, receta_id: int, datos: dict) -> dict:
        """R-340: a variant of that recipe; the recipe and what was logged do not change (G-75). KeyError without the
        recipe; ValueError if its kcal are impossible (R6) or the recipe already has one with that name (R3)."""
        with self._connect() as conn:
            limpio = self._comprobada(conn, receta_id, datos, None)
            macros = limpio["macros"]
            cur = conn.execute(
                "INSERT INTO receta_variante (receta_id, nombre, kcal_100g, proteina, hidratos, grasa, creado) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (receta_id, limpio["nombre"], limpio["kcal_100g"], *(macros[k] if macros else None for k in MACROS_FIJO),
                 datetime.now().isoformat(timespec="seconds")))
            return self._variante_fila(self._leer(conn, "receta_variante", int(cur.lastrowid or 0)))

    def cambiar_variante(self, variante_id: int, datos: dict) -> dict:
        """R-342: its kcal/100 g (and its name or macros if sent; the macros not sent stay as they were). What was
        logged with it keeps its kcal."""
        with self._connect() as conn:
            antes = self._leer(conn, "receta_variante", variante_id)
            limpio = self._comprobada(conn, antes["receta_id"], self._con_lo_de_antes(antes, datos), variante_id)
            macros = limpio["macros"]
            conn.execute("UPDATE receta_variante SET nombre = ?, kcal_100g = ?, proteina = ?, hidratos = ?, grasa = ? "
                         "WHERE id = ?", (limpio["nombre"], limpio["kcal_100g"],
                                          *(macros[k] if macros else None for k in MACROS_FIJO), variante_id))
            return self._variante_fila(self._leer(conn, "receta_variante", variante_id))

    def borrar_variante(self, variante_id: int, confirmado: bool = False) -> dict:
        """R-342: only with `confirmado`; what was logged with it keeps its kcal and origin."""
        return self._borrar("receta_variante", variante_id, confirmado, self._variante_fila)

    @staticmethod
    def _para_apuntar_variante(fila: sqlite3.Row, receta: str) -> dict:
        """A variant as `_calcular` takes it: «Receta · variante» with its kcal/100 g and macros per 100 g."""
        return {**Recetario._variante_fila(fila), "nombre": nombre_variante(receta, fila["nombre"]), "receta": receta,
                "variante": fila["nombre"]}

    def variante_por_nombre(self, nombre: str | None) -> int | None:
        """The id of the variant called «Receta · variante» (its recipe by name or alias, without capitals, accents nor
        the dot: «garbanzos de la abuela sin chorizo» too), or None."""
        clave = normalizar(nombre)
        if not clave:
            return None
        with self._connect() as conn:
            for v in conn.execute("SELECT v.id, v.nombre, r.nombre AS receta, r.alias FROM receta_variante v "
                                  "JOIN receta r ON r.id = v.receta_id ORDER BY r.id, v.id").fetchall():
                if clave in {normalizar(nombre_variante(n, v["nombre"])) for n in [v["receta"], *json.loads(v["alias"])]}:
                    return int(v["id"])
        return None

    # -- G-31: what was dictated --------------------------------------------------------------------------

    def _objetivo(self, nombre: str | None) -> tuple[str, dict] | None:
        """The usable recipe or fixed food with that name or alias (a recipe to review is not usable)."""
        clave = normalizar(nombre)
        if not clave:
            return None
        for receta in self.recetas():
            if not receta["por_revisar"] and clave in {normalizar(n) for n in [receta["nombre"], *receta["alias"]]}:
                return "receta", receta
        for fijo in self.fijos():
            if clave in {normalizar(n) for n in [fijo["nombre"], *fijo["alias"]]}:
                return "fijo", fijo
        return None

    @staticmethod
    def _calcular(tipo: str, cosa: dict, cantidad: float | None, unidad: str | None) -> dict:
        """kcal and origin of `cantidad` of a saved thing, or `problema` (and `dudoso`) if it cannot be done."""
        if tipo == "variante":  # unit 112 (R-341): by grams, with ITS kcal/100 g and macros per 100 g
            if cantidad is None or unidad != "g":
                return {"problema": f"«{cosa['nombre']}» está guardada por 100 g: dilo en gramos", "dudoso": False}
            macros = cosa.get("macros_100g")
            return {"kcal": _redondear(cantidad * cosa["kcal_100g"] / 100), "provisional": False,
                    "origen": f"de tu receta «{cosa['receta']}»{SEPARADOR_VARIANTE}{cosa['variante']}",
                    **({k: _redondear(macros[k] * cantidad / 100) for k in MACROS} if macros
                       else dict.fromkeys(MACROS))}
        if tipo == "fijo":
            kcal = _kcal_de_fijo(cosa, cantidad, unidad)
            if kcal is None:
                como = "por 100 g: dilo en gramos" if cosa["por"] == "100g" else "por unidad: dilo en unidades"
                return {"problema": f"«{cosa['nombre']}» está guardado {como}", "dudoso": False}
            origen = "de la lista de comunes" if cosa.get("comun") else f"de tu ingrediente «{cosa['nombre']}»"
            return {"kcal": kcal, "origen": origen, "provisional": False,
                    **(_macros_de_fijo(cosa, cantidad, unidad) or dict.fromkeys(MACROS))}
        v = cosa["en_uso"]
        tanda = f" (tanda {v['numero']})" if len(cosa["versiones"]) > 1 else ""
        origen = f"de tu receta «{cosa['nombre']}»{tanda}"
        if v["estado"] == "sin_kcal":
            return {"problema": f"corrige la receta «{cosa['nombre']}» en la pestaña Recetas: tiene un ingrediente "
                                "dudoso y no se puede calcular", "dudoso": True}
        if cantidad is None:
            return {"problema": None, "dudoso": False}
        suma = _suma_macros(v["ingredientes"])  # unit 098 (R4): a portion takes them in proportion to its kcal

        def con_macros(datos: dict, factor: float | None) -> dict:
            return {**datos, **(_escalar(suma, factor) or dict.fromkeys(MACROS))}

        if unidad == "receta":
            return con_macros({"kcal": _redondear(cantidad * v["kcal_total"]), "origen": origen,
                               "provisional": v["estado"] != "definitiva"}, cantidad)
        if unidad == "g":
            por_100 = Recetario._factor_100g(v)
            factor = cantidad * por_100 / 100 if por_100 is not None else None
            if v["kcal_100g"] is not None:
                return con_macros({"kcal": _redondear(cantidad * v["kcal_100g"] / 100), "origen": origen,
                                   "provisional": False}, factor)
            if v["kcal_100g_aprox"] is not None:
                return con_macros({"kcal": _redondear(cantidad * v["kcal_100g_aprox"] / 100), "origen": origen,
                                   "provisional": True}, factor)
            return {"problema": f"«{cosa['nombre']}» no está pesada: dilo en fracción («un cuarto de "
                                f"{cosa['nombre']}») o pon sus kcal/100 g aproximadas en la pestaña Recetas",
                    "dudoso": True}
        return {"problema": f"«{cosa['nombre']}» está guardada por gramos o por fracción de la receta: dilo así",
                "dudoso": False}

    def para_apuntar(self, tipo: str, ident: int, cantidad: float | None, unidad: str | None = None) -> dict:
        """Units 013 and 019 (R-57, R-58): what is logged for `cantidad` of a saved recipe or fixed food.

        Unit 112 (R-341): or of a variant (`tipo` «variante»), only by grams: «Receta · variante» with its kcal/100 g.

        `unidad` None is the thing's own: grams for a recipe and for a fixed food per 100 g, units for one per
        unit. A recipe also goes by fractions (`unidad="receta"`, `cantidad` > 0: ½ is 0.5), weighed or not: not
        weighed it is provisional; only by grams does it need its weight or approximate kcal/100 g. The kcal
        come from `_calcular` with what is saved, never from the page. `ValueError` with the text for Andrés if
        it cannot be worked out (no quantity, another unit, not weighed by grams, a doubtful ingredient, a recipe
        to review); `KeyError` if it does not exist.
        """
        with self._connect() as conn:
            if tipo == "fijo":
                cosa = self._fijo_fila(self._leer(conn, "alimento_fijo", ident))
                unidad = unidad or ("g" if cosa["por"] == "100g" else "unidad")
            elif tipo == "receta":
                cosa = self._receta_de(conn, self._leer(conn, "receta", ident))
                unidad = unidad or "g"
            elif tipo == "variante":  # unit 112 (R-341)
                fila = self._leer(conn, "receta_variante", ident)
                cosa = self._para_apuntar_variante(fila, self._leer(conn, "receta", fila["receta_id"])["nombre"])
                unidad = unidad or "g"
            else:
                raise KeyError(tipo)
        if cantidad is None or cantidad <= 0:
            raise ValueError("escribe cuánto has cogido")
        if tipo == "receta":
            v = cosa["en_uso"]
            if cosa["por_revisar"]:
                raise ValueError(f"corrige la receta «{cosa['nombre']}» en la pestaña Recetas: está por revisar")
            if v is None or v["estado"] == "sin_kcal":
                raise ValueError(f"corrige la receta «{cosa['nombre']}»: tiene un ingrediente dudoso y no se puede calcular")
            if unidad == "g" and v["kcal_100g"] is None and v["kcal_100g_aprox"] is None:
                raise ValueError(f"«{cosa['nombre']}» no está pesada: pésala o pon sus kcal/100 g aproximadas")
        calculo = self._calcular(tipo, cosa, cantidad, unidad)
        if "problema" in calculo:  # another unit than its own (or a fraction of a fixed food)
            raise ValueError(calculo["problema"] or "no se puede calcular")
        return {"nombre": cosa["nombre"], "cantidad": cantidad, "unidad": unidad, "kcal": calculo["kcal"],
                "origen": calculo["origen"], "provisional": calculo["provisional"],
                **{k: calculo.get(k) for k in MACROS}}

    def resolver(self, alimento: dict) -> dict:
        """What the list shows for one interpreted food (D1, D2, D6; R-42, R-43, R-45, R-48).

        Exact (Claude said so, or the name is the saved one as it is): the saved kcal, not estimated,
        with its origin. Alike: the same, as a visible proposal that keeps Claude's estimate in
        `kcal_estimada`. Anything Claude names that is not saved is ignored. The kcal always come
        from here, never from Claude.
        """
        salida = {**alimento, "origen": None, "provisional": False, "propuesta": None, "kcal_estimada": None}
        if alimento.get("dudoso"):
            return salida
        nombre = alimento.get("nombre")
        objetivo = self._objetivo(nombre)
        coincidencia = "exacta" if objetivo else None
        if objetivo is None and alimento.get("guardado"):
            objetivo = self._objetivo(alimento["guardado"])
            coincidencia = "exacta" if alimento.get("coincidencia") == "exacta" else "parecida"
        if objetivo is None:
            return salida
        tipo, cosa = objetivo
        calculo = self._calcular(tipo, cosa, alimento.get("cantidad"), alimento.get("unidad"))
        if "problema" in calculo:
            if coincidencia == "exacta":
                salida["nombre"] = cosa["nombre"]
            if calculo["problema"] is None:
                return salida
            if calculo["dudoso"] and coincidencia == "exacta":
                salida.update(dudoso=True, kcal=None, proteina_g=None, hidratos_g=None, grasa_g=None,
                              supuestos=[calculo["problema"]])
            else:
                salida.update(estimado=True, supuestos=[*(alimento.get("supuestos") or []), calculo["problema"]])
            return salida
        salida.update(nombre=cosa["nombre"], kcal=calculo["kcal"], estimado=False, dudoso=False,
                      **{k: calculo.get(k) for k in MACROS}, supuestos=[], origen=calculo["origen"],
                      provisional=calculo["provisional"])
        if coincidencia == "parecida":
            que = "tu receta" if tipo == "receta" else "tu ingrediente"
            salida["propuesta"] = {"dicho": nombre, "guardado": cosa["nombre"]}
            salida["kcal_estimada"] = alimento.get("kcal")
            salida["supuestos"] = [f"has dicho «{nombre}»: te propongo {que} «{cosa['nombre']}»"]
        return salida

    # -- unit 098 (R-270): filling in the macros that are missing, once ----------------------------------------------

    def _recalcular_en_uso(self, conn: sqlite3.Connection) -> list[str]:
        """The lines of saved ingredients of every recipe in use take their fixed food's macros now (their kcal stay
        as they were saved). Returns the names of the recipes whose macros changed."""
        fijos = self._fijos_por_nombre(conn)
        cambiadas = []
        for v in conn.execute("SELECT v.id, v.ingredientes, r.nombre FROM receta_version v JOIN receta r "
                              "ON r.id = v.receta_id AND r.version_en_uso = v.numero ORDER BY r.id").fetchall():
            ingredientes = json.loads(v["ingredientes"])
            nuevos = [self._linea_al_dia(i, fijos) for i in ingredientes]
            if nuevos != ingredientes:
                conn.execute("UPDATE receta_version SET ingredientes = ? WHERE id = ?",
                             (json.dumps(nuevos, ensure_ascii=False), v["id"]))
                cambiadas.append(v["nombre"])
        return cambiadas

    @staticmethod
    def _linea_al_dia(linea: dict, fijos: dict[str, dict]) -> dict:
        fijo = fijos.get(normalizar(linea.get("nombre"))) if linea.get("estado") == "guardado" else None
        if fijo is None:
            return linea
        macros = _macros_de_fijo(fijo, linea.get("cantidad"), linea.get("unidad")) or dict.fromkeys(MACROS)
        if all(linea.get(k) == macros[k] for k in MACROS):
            return linea
        return {**linea, **macros}

    def _guardados_por_nombre(self, conn: sqlite3.Connection) -> dict[str, tuple[str, dict]]:
        """Unit 101: normalized name or alias → (kind, thing) of the usable recipes and fixed foods (a recipe wins, as
        in `_objetivo`)."""
        guardados = {n: ("fijo", f) for n, f in self._fijos_por_nombre(conn).items()}
        for r in conn.execute("SELECT * FROM receta WHERE por_revisar = 0 ORDER BY id").fetchall():
            receta = self._receta_de(conn, r)
            for n in [receta["nombre"], *receta["alias"]]:
                guardados[normalizar(n)] = ("receta", receta)
        for v in conn.execute("SELECT v.*, r.nombre AS receta, r.alias FROM receta_variante v JOIN receta r "
                              "ON r.id = v.receta_id ORDER BY r.id, v.id").fetchall():  # unit 112: a recipe wins
            variante = self._para_apuntar_variante(v, v["receta"])
            for n in [v["receta"], *json.loads(v["alias"])]:
                guardados.setdefault(normalizar(nombre_variante(n, v["nombre"])), ("variante", variante))
        return guardados

    @staticmethod
    def _de_lo_guardado(apuntado: dict, guardados: dict[str, tuple[str, dict]]) -> tuple[dict | None, str | None]:
        """Unit 101 (R-284, C-358): the macros of a logged food worked out from the saved thing it comes from — by its
        `origen` («de tu ingrediente «X»», «de tu receta «X»»), or by its name without capitals nor accents — for its
        quantity, as `para_apuntar` does; (None, None) if that thing has none or it is not on the same base."""
        de_variante = _DE_VARIANTE.match(apuntado.get("origen") or "")  # unit 112: never its recipe's macros
        nombres = ([nombre_variante(de_variante.group(1), de_variante.group(2))] if de_variante
                   else [m.group(1) for m in [_DE_ORIGEN.match(apuntado.get("origen") or "")] if m])
        for nombre in [*nombres, apuntado["nombre"]]:
            guardado = guardados.get(normalizar(nombre))
            if guardado is None:
                continue
            tipo, cosa = guardado
            if tipo == "receta" and cosa["en_uso"] is None:
                return None, None
            calculo = Recetario._calcular(tipo, cosa, apuntado["cantidad"], apuntado["unidad"])
            if "problema" in calculo or any(calculo.get(k) is None for k in MACROS):
                return None, None
            if tipo == "receta":
                de = f"de tu receta «{cosa['nombre']}»"
            elif tipo == "variante":
                de = calculo["origen"]
            else:
                de = f"de la lista de comunes («{cosa['nombre']}»)" if cosa["comun"] else f"de tu ingrediente «{cosa['nombre']}»"
            return {k: calculo[g] for k, g in zip(MACROS_FIJO, MACROS, strict=True)}, de
        return None, None

    def _apuntados_sin_macros(self, conn: sqlite3.Connection, hoy: date) -> dict[str, dict]:
        """Unit 101 (R-284, S-93): clave `apuntado:<id>` → each food logged without macros from `hoy` and the 13 days
        before on (eaten and planned), with its day, quantity and kcal, and its macros already worked out when it
        comes from a saved thing that has them (`calculado`, with where from in `de`)."""
        from .registro import DIAS_MACROS, sin_macros_desde  # registro imports this module

        guardados: dict[str, tuple[str, dict]] | None = None
        faltan: dict[str, dict] = {}
        for a in sin_macros_desde(conn, hoy - timedelta(days=DIAS_MACROS - 1)):
            if guardados is None:
                guardados = self._guardados_por_nombre(conn)
            calculado, de = self._de_lo_guardado(a, guardados)
            cuanto = f"{a['cantidad']:g} {a.get('unidad') or ''}".strip() if a.get("cantidad") else "sin cantidad"
            faltan[f"apuntado:{a['id']}"] = {
                "tipo": "apuntado", "id": a["id"], "nombre": a["nombre"], "dia": a["dia"], "estado": a["estado"],
                "cantidad": a["cantidad"], "unidad": a["unidad"], "kcal": a["kcal"], "cuanto": cuanto,
                "base": f"{cuanto} el {a['dia']}", "calculado": calculado, "de": de}
        return faltan

    def _faltan(self, conn: sqlite3.Connection, hoy: date | None = None) -> tuple[dict[str, dict], list[str]]:
        """clave → what is missing (fixed foods without macros; estimated lines of the recipes in use without them;
        unit 101, with `hoy`, what was logged without them in the last 14 days), and the recipes in use without
        ingredients (they cannot have macros: theirs come from their ingredients)."""
        faltan: dict[str, dict] = {}
        for f in conn.execute("SELECT * FROM alimento_fijo ORDER BY comun, nombre COLLATE NOCASE, id"):
            if any(f[k] is None for k in MACROS_FIJO):
                faltan[f"fijo:{f['id']}"] = {"tipo": "fijo", "id": f["id"], "nombre": f["nombre"], "kcal": f["kcal"],
                                             "por": f["por"],
                                             "base": "por 100 g" if f["por"] == "100g" else "por unidad"}
        sin_ingredientes = []
        for v in conn.execute("SELECT v.id, v.ingredientes, r.id AS receta_id, r.nombre FROM receta_version v "
                              "JOIN receta r ON r.id = v.receta_id AND r.version_en_uso = v.numero "
                              "ORDER BY r.nombre COLLATE NOCASE, r.id").fetchall():
            ingredientes = json.loads(v["ingredientes"])
            if not ingredientes:
                sin_ingredientes.append(v["nombre"])
            for n, i in enumerate(ingredientes):
                if i.get("estado") == "estimado" and i.get("kcal") and any(i.get(k) is None for k in MACROS):
                    cuanto = f"{i['cantidad']:g} {i.get('unidad') or ''}".strip() if i.get("cantidad") else "sin cantidad"
                    faltan[f"receta:{v['receta_id']}:{n}"] = {
                        "tipo": "linea", "version_id": v["id"], "indice": n, "nombre": i["nombre"],
                        "receta": v["nombre"], "kcal": i["kcal"], "cantidad": i.get("cantidad"),
                        "unidad": i.get("unidad"), "base": f"{cuanto} en «{v['nombre']}»"}
        if hoy is not None:
            faltan.update(self._apuntados_sin_macros(conn, hoy))
        return faltan, sin_ingredientes

    def macros_que_faltan(self, hoy: date | None = None) -> dict:
        """R2: what has no macros yet (each with its `clave`), and the recipes that cannot have them. Read-only.

        Unit 101 (R-284): with `hoy`, also what was logged without macros in the last 14 days (`apuntado:<id>`, with
        `dia`, `cantidad`, `unidad`, `estado`, `kcal` and `calculado`/`de` when it comes from a saved thing)."""
        with self._connect() as conn:
            faltan, sin_ingredientes = self._faltan(conn, hoy)
        claves = ("tipo", "nombre", "receta", "base", "kcal", "dia", "estado", "cantidad", "unidad", "cuanto",
                  "calculado", "de")
        return {"filas": [{"clave": c, **{k: x for k, x in f.items() if k in claves}} for c, f in faltan.items()],
                "sin_ingredientes": sin_ingredientes}

    def _filas_propuestas(self, conn: sqlite3.Connection, propuestas: Any, hoy: date | None = None) -> list[dict]:
        """The team's values checked against what is missing now (R3). ValueError with every reason.

        Unit 101 (R-284): a logged food (`apuntado:<id>`) worked out from a saved thing may come with its clave alone
        (it takes `calculado`); with values, they are the team's, or Andrés's with `corregido: true` («etiqueta»)."""
        if not isinstance(propuestas, list) or not propuestas:
            raise ErrorDatos("propuestas tiene que ser una lista con clave, proteina, hidratos y grasa")
        faltan, _ = self._faltan(conn, hoy)
        todos = {f"fijo:{f['id']}": f for f in conn.execute("SELECT * FROM alimento_fijo")}
        filas, errores, vistas = [], [], set()
        for p in propuestas:
            if not isinstance(p, dict) or not isinstance(p.get("clave"), str):
                raise ErrorDatos("cada propuesta tiene que ser un objeto con su clave")
            clave = p["clave"].strip()
            if clave in vistas:
                errores.append(f"«{clave}» va dos veces")
                continue
            vistas.add(clave)
            que = faltan.get(clave)
            if que is None:
                ya = todos.get(clave)
                errores.append(f"«{ya['nombre']}» ya tiene sus macros: no se tocan" if ya is not None
                               else f"«{clave}» no es nada que falte (mira preparar_macros sin propuestas)")
                continue
            corregido = p.get("corregido", False)
            if not isinstance(corregido, bool):
                raise ErrorDatos("corregido tiene que ser true o false")
            try:
                valores = {k: _numero(p, k, f"{que['nombre']}: {k}") for k in MACROS_FIJO}
                por = que["por"] if que["tipo"] == "fijo" else None
                macros = _macros_validos(valores, por, que["nombre"])
                if macros is None and que["tipo"] == "apuntado" and que["calculado"] is not None:
                    macros, corregido = dict(que["calculado"]), False  # unit 101: worked out from what is saved
                if macros is None:
                    raise ValueError(f"«{que['nombre']}»: faltan sus macros")
                if que["tipo"] in ("linea", "apuntado") and que["unidad"] in ("g", "ml") and que["cantidad"]:
                    _macros_validos({k: v / que["cantidad"] * 100 for k, v in macros.items()}, "100g", que["nombre"])
            except ValueError as exc:
                errores.append(str(exc))
                continue
            filas.append({"clave": clave, **que, **{k: _redondear(macros[k]) for k in MACROS_FIJO},
                          "aviso": no_cuadra(que["kcal"], *(macros[k] for k in MACROS_FIJO)),
                          "marca": "etiqueta" if corregido and que["tipo"] == "apuntado" else "estimado"})
        if errores:
            raise ValueError("; ".join(errores))
        return filas

    def _aplicar_macros(self, conn: sqlite3.Connection, filas: list[dict]) -> list[str]:
        """Write the accepted rows as «estimado» and recompute the recipes in use (in the caller's transaction)."""
        from .registro import poner_macros  # registro imports this module

        lineas: dict[int, list[dict]] = {}
        for f in filas:
            if f["tipo"] == "apuntado":  # unit 101 (R-284): only its three macros and their mark; its kcal stay
                poner_macros(conn, f["id"], {g: f[k] for k, g in zip(MACROS_FIJO, MACROS, strict=True)}, f["marca"])
                continue
            if f["tipo"] == "fijo":
                conn.execute("UPDATE alimento_fijo SET proteina = ?, hidratos = ?, grasa = ?, macros_origen = "
                             "'estimado' WHERE id = ?", (f["proteina"], f["hidratos"], f["grasa"], f["id"]))
                continue
            if f["version_id"] not in lineas:
                fila = conn.execute("SELECT ingredientes FROM receta_version WHERE id = ?",
                                    (f["version_id"],)).fetchone()
                lineas[f["version_id"]] = json.loads(fila["ingredientes"])
            linea = lineas[f["version_id"]][f["indice"]]
            linea.update({g: f[k] for k, g in zip(MACROS_FIJO, MACROS, strict=True)})
        for version_id, ingredientes in lineas.items():
            conn.execute("UPDATE receta_version SET ingredientes = ? WHERE id = ?",
                         (json.dumps(ingredientes, ensure_ascii=False), version_id))
        return self._recalcular_en_uso(conn)

    def validar_macros(self, propuestas: Any, hoy: date | None = None) -> dict:
        """R2, R3: what `guardar_macros` would save (each row with its warning if it does not match its kcal) and the
        recipes in use it touches, with whether they would have macros. Writes NOTHING (it works on a copy in memory
        of the same file and throws it away). ValueError with every reason if a value is impossible. Unit 101: with
        `hoy`, the logged foods of the last 14 days too (`apuntado:<id>`)."""
        with self._connect() as conn:
            filas = self._filas_propuestas(conn, propuestas, hoy)
            copia = sqlite3.connect(":memory:")
            copia.row_factory = sqlite3.Row
            try:
                conn.backup(copia)
                recetas = self._aplicar_macros(copia, filas)
                estado = self._estado_macros(copia, recetas)
            finally:
                copia.close()
        return {"filas": filas, "recetas": estado}

    @staticmethod
    def _estado_macros(conn: sqlite3.Connection, nombres: list[str]) -> list[dict]:
        estado = []
        for v in conn.execute("SELECT v.ingredientes, r.nombre FROM receta_version v JOIN receta r "
                              "ON r.id = v.receta_id AND r.version_en_uso = v.numero ORDER BY r.nombre COLLATE NOCASE"):
            if v["nombre"] in nombres:
                ingredientes = json.loads(v["ingredientes"])
                estado.append({"nombre": v["nombre"], "sin_macros": _sin_macros(ingredientes),
                               "con_macros": _suma_macros(ingredientes) is not None})
        return estado

    def guardar_macros(self, propuestas: Any, rechazar: Any = None, hoy: date | None = None) -> dict:
        """R2 (C-343): save the accepted rows as «estimado» (minus `rechazar`, the claves Andrés said no to, which stay
        without macros) and recompute the recipes in use, in ONE write. Unit 101 (R-284): with `hoy`, the accepted
        logged foods of the last 14 days take their macros («etiqueta» if Andrés corrected them); their kcal, never."""
        if rechazar is None:
            rechazar = []
        if not isinstance(rechazar, list) or not all(isinstance(c, str) for c in rechazar):
            raise ErrorDatos("rechazar tiene que ser una lista de claves")
        with self._connect() as conn:
            filas = self._filas_propuestas(conn, propuestas, hoy)
            claves = {f["clave"] for f in filas}
            fuera = [c for c in rechazar if c.strip() not in claves]
            if fuera:
                raise ValueError(f"rechazar: {', '.join(fuera)} no está en la propuesta")
            aceptadas = [f for f in filas if f["clave"] not in {c.strip() for c in rechazar}]
            recetas = self._aplicar_macros(conn, aceptadas)
            estado = self._estado_macros(conn, recetas)
        return {"filas": aceptadas, "rechazadas": len(filas) - len(aceptadas), "recetas": estado}

    # -- D7: loading deficit.md ------------------------------------------------------------------------------

    def cargar_de_deficit(self, cargas: list[Carga], hoy: date, simular: bool = False) -> list[tuple[Carga, str]]:
        """Add what is missing: what the app already has wins and loading twice does not duplicate.

        Returns each row with «nueva», «ya estaba» or «no se carga: …». With `simular` nothing is written.
        """
        resultado: list[tuple[Carga, str]] = []
        with self._connect() as conn:
            duenos = self._duenos(conn)
            for c in cargas:
                if c.tipo == "olla" and c.tara_g is None:
                    resultado.append((c, f"no se carga: {c.motivo}"))
                    continue
                if normalizar(c.nombre) in duenos:
                    resultado.append((c, "ya estaba"))
                    continue
                duenos[normalizar(c.nombre)] = (c.tipo, 0, c.nombre)
                resultado.append((c, "nueva"))
                if simular:
                    continue
                if c.tipo == "olla":
                    conn.execute("INSERT INTO olla (nombre, tara_g) VALUES (?, ?)", (c.nombre, c.tara_g))
                elif c.tipo == "fijo":
                    conn.execute("INSERT INTO alimento_fijo (nombre, alias, kcal, por, nota) VALUES (?, '[]', ?, ?, ?)",
                                 (c.nombre, c.kcal, c.por, c.nota))
                else:
                    cur = conn.execute(
                        "INSERT INTO receta (nombre, alias, nota, version_en_uso, por_revisar, creado) "
                        "VALUES (?, '[]', ?, 1, ?, ?)",
                        (c.nombre, c.nota, int(c.tipo == "revisar"), datetime.now().isoformat(timespec="seconds")))
                    self._insertar_version(conn, int(cur.lastrowid or 0), 1, hoy, {
                        "ingredientes": [], "kcal_total": c.kcal_total, "olla_id": None, "olla": None,
                        "tara_g": None, "peso_lleno_g": None, "peso_neto_g": c.peso_neto_g, "kcal_100g": c.kcal_100g,
                        "kcal_100g_aprox": None})
        return resultado
