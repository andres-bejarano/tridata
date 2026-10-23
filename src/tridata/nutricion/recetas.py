"""Recipes, fixed foods and pots (unit 011, activity `guardar-receta`, REC-7 to REC-9).

Their own tables in the same SQLite store as the food log. A recipe has versions (one per batch,
G-33): each keeps its ingredients with the kcal they had when saved, its pot and tare, its net
weight and its kcal/100 g, so changing a fixed food or a pot later never changes a saved version,
and nothing here ever touches the foods already logged (R-55). Pure functions: `normalizar`,
`kcal_100g`, `estado_version` (G-32) and `leer_cargas` (the rows of deficit.md, D7).
`Recetario.resolver` turns what Claude interpreted into what the list shows: the kcal of what is
saved when it matches (G-31), a visible proposal when it is only alike, and the estimate otherwise.
Business errors are `ValueError` with the text for Andrés; malformed data are `ErrorDatos`.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..storage import DEFAULT_DB_PATH

KCAL_100G_MAX = 900  # G-35: nothing passes 900 kcal/100 g (pure fat)
POR = ("100g", "unidad")

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
    comun INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS comun_quitado (
    nombre TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS olla (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    tara_g REAL NOT NULL
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
    return {"nombre": nombre, "cantidad": cantidad, "unidad": (unidad or "").strip() or None, "kcal": kcal,
            "dudoso": bool(crudo.get("dudoso")) or crudo.get("estado") == "dudoso",
            "guardado": crudo.get("guardado") if isinstance(crudo.get("guardado"), str) else None}


def _kcal_de_fijo(fijo: dict, cantidad: float | None, unidad: str | None) -> float | None:
    """The kcal of `cantidad` of a fixed food, or None if the unit does not fit (D6)."""
    if cantidad is None:
        return None
    if fijo["por"] == "100g" and unidad == "g":
        return _redondear(cantidad * fijo["kcal"] / 100)
    if fijo["por"] == "unidad" and unidad == "unidad":
        return _redondear(cantidad * fijo["kcal"])
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
                "por": f["por"], "nota": f["nota"], "comun": bool(f["comun"])}

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
        return datos

    def _receta_de(self, conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
        versiones = [self._version_fila(v, r["version_en_uso"]) for v in conn.execute(
            "SELECT * FROM receta_version WHERE receta_id = ? ORDER BY numero", (r["id"],))]
        en_uso = next((v for v in versiones if v["en_uso"]), versiones[-1] if versiones else None)
        return {"id": r["id"], "nombre": r["nombre"], "alias": json.loads(r["alias"]), "nota": r["nota"],
                "por_revisar": bool(r["por_revisar"]), "version_en_uso": r["version_en_uso"], "en_uso": en_uso,
                "versiones": versiones}

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
        return {"nombre": nombre, "alias": _alias(datos), "kcal": kcal, "por": por, "nota": _texto(datos, "nota")}

    def crear_fijo(self, datos: dict) -> dict:
        f = self._datos_fijo(datos)
        with self._connect() as conn:
            self._unico(conn, [f["nombre"], *f["alias"]], ("fijo", None))
            cur = conn.execute("INSERT INTO alimento_fijo (nombre, alias, kcal, por, nota) VALUES (?, ?, ?, ?, ?)",
                               (f["nombre"], json.dumps(f["alias"], ensure_ascii=False), f["kcal"], f["por"], f["nota"]))
            return self._fijo_fila(self._leer(conn, "alimento_fijo", int(cur.lastrowid or 0)))

    def cambiar_fijo(self, fijo_id: int, datos: dict) -> dict:
        """A2: change it in place. What was logged and the saved recipe versions keep their kcal."""
        with self._connect() as conn:
            antes = self._leer(conn, "alimento_fijo", fijo_id)
            f = self._datos_fijo(datos)
            self._unico(conn, [f["nombre"], *f["alias"]], ("fijo", fijo_id))
            if antes["comun"]:  # unit 026 (R4): corrected, it is his; the common one is not loaded again
                conn.execute("INSERT OR IGNORE INTO comun_quitado (nombre) VALUES (?)", (normalizar(antes["nombre"]),))
            conn.execute("UPDATE alimento_fijo SET nombre = ?, alias = ?, kcal = ?, por = ?, nota = ?, comun = 0 "
                         "WHERE id = ?",
                         (f["nombre"], json.dumps(f["alias"], ensure_ascii=False), f["kcal"], f["por"], f["nota"],
                          fijo_id))
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
        """
        nuevos = 0
        with self._connect() as conn:
            duenos = self._duenos(conn)
            quitados = {f["nombre"] for f in conn.execute("SELECT nombre FROM comun_quitado")}
            for c in comunes:
                clave = normalizar(c["nombre"])
                if clave in duenos or clave in quitados:
                    continue
                conn.execute("INSERT INTO alimento_fijo (nombre, alias, kcal, por, nota, comun) VALUES (?, '[]', ?, ?, ?, 1)",
                             (c["nombre"], c["kcal"], c["por"], c.get("nota")))
                duenos[clave] = ("fijo", 0, c["nombre"])
                nuevos += 1
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
            if kcal is not None and fijo is not None:
                resultado.append({"nombre": fijo["nombre"], "cantidad": i["cantidad"], "unidad": i["unidad"],
                                  "kcal": kcal, "estado": "guardado"})
            elif i["dudoso"] or i["kcal"] is None:
                resultado.append({"nombre": i["nombre"], "cantidad": i["cantidad"], "unidad": i["unidad"],
                                  "kcal": None, "estado": "dudoso"})
            else:
                resultado.append({"nombre": i["nombre"], "cantidad": i["cantidad"], "unidad": i["unidad"],
                                  "kcal": _redondear(i["kcal"]), "estado": "estimado"})
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
            conn.execute("DELETE FROM receta_version WHERE receta_id = ?", (receta_id,))
            conn.execute("DELETE FROM receta WHERE id = ?", (receta_id,))
        return receta

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
        if tipo == "fijo":
            kcal = _kcal_de_fijo(cosa, cantidad, unidad)
            if kcal is None:
                como = "por 100 g: dilo en gramos" if cosa["por"] == "100g" else "por unidad: dilo en unidades"
                return {"problema": f"«{cosa['nombre']}» está guardado {como}", "dudoso": False}
            origen = "de la lista de comunes" if cosa.get("comun") else f"de tu ingrediente «{cosa['nombre']}»"
            return {"kcal": kcal, "origen": origen, "provisional": False}
        v = cosa["en_uso"]
        tanda = f" (tanda {v['numero']})" if len(cosa["versiones"]) > 1 else ""
        origen = f"de tu receta «{cosa['nombre']}»{tanda}"
        if v["estado"] == "sin_kcal":
            return {"problema": f"corrige la receta «{cosa['nombre']}» en la pestaña Recetas: tiene un ingrediente "
                                "dudoso y no se puede calcular", "dudoso": True}
        if cantidad is None:
            return {"problema": None, "dudoso": False}
        if unidad == "receta":
            return {"kcal": _redondear(cantidad * v["kcal_total"]), "origen": origen,
                    "provisional": v["estado"] != "definitiva"}
        if unidad == "g":
            if v["kcal_100g"] is not None:
                return {"kcal": _redondear(cantidad * v["kcal_100g"] / 100), "origen": origen, "provisional": False}
            if v["kcal_100g_aprox"] is not None:
                return {"kcal": _redondear(cantidad * v["kcal_100g_aprox"] / 100), "origen": origen,
                        "provisional": True}
            return {"problema": f"«{cosa['nombre']}» no está pesada: dilo en fracción («un cuarto de "
                                f"{cosa['nombre']}») o pon sus kcal/100 g aproximadas en la pestaña Recetas",
                    "dudoso": True}
        return {"problema": f"«{cosa['nombre']}» está guardada por gramos o por fracción de la receta: dilo así",
                "dudoso": False}

    def para_apuntar(self, tipo: str, ident: int, cantidad: float | None, unidad: str | None = None) -> dict:
        """Units 013 and 019 (R-57, R-58): what is logged for `cantidad` of a saved recipe or fixed food.

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
                "origen": calculo["origen"], "provisional": calculo["provisional"]}

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
                      proteina_g=None, hidratos_g=None, grasa_g=None, supuestos=[], origen=calculo["origen"],
                      provisional=calculo["provisional"])
        if coincidencia == "parecida":
            que = "tu receta" if tipo == "receta" else "tu ingrediente"
            salida["propuesta"] = {"dicho": nombre, "guardado": cosa["nombre"]}
            salida["kcal_estimada"] = alimento.get("kcal")
            salida["supuestos"] = [f"has dicho «{nombre}»: te propongo {que} «{cosa['nombre']}»"]
        return salida

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
