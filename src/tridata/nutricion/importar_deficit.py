"""One-off import of deficit.md and its weight chart (unit 009, REC-6, G-28).

Read by CODE, no AI: `leer` turns the Markdown tables (and, optionally, the chart's `days` and
`weight` arrays) into weights, days with their food lines, «sin pesar» rows, the names of the
reference recipes and pot tares (counted, not imported), and the dubious items Andrés decides
one by one. `aplicar` writes only what is missing: what the app already has always wins.
`Importaciones` keeps one record per confirmed import (summary, fingerprints, decisions).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..storage import DEFAULT_DB_PATH
from .peso import RegistroPeso, peso_valido
from .registro import Alimento, RegistroComidas

TOTAL_IMPORTADO = "total del día (importado)"
SUPUESTO = "importado de deficit.md"
KCAL_DIA = (500, 8000)
TOLERANCIA_DETALLE = 0.05

PESOS = "Seguimiento de peso"
REGISTRO = "Registro diario"
TARAS = "Taras de ollas"
REFERENCIAS = "Referencias calóricas fijas"
GRAFICA = "Gráfica de peso"

NO_CUADRA = "el detalle no cuadra con el total"
SIN_TOTAL = "el día está en curso o sin un total"
RANGO = "el total es un rango"
FUERA_KCAL = f"el total del día tiene que estar entre {KCAL_DIA[0]} y {KCAL_DIA[1]:,} kcal".replace(",", ".")
FECHA_IMPOSIBLE = "fecha imposible"
FECHA_NO_RECONOCIDA = "fecha no reconocida"
POSTERIOR = "día posterior a hoy"

# kcal: «2.150» is 2150 (dot = thousands); weights use a decimal dot or comma instead.
_NUM = r"\d{1,3}(?:\.\d{3})+(?!\d)|\d+"
_TOTAL_SIMPLE = re.compile(rf"^~?\s*({_NUM})\s*(?:kcal)?\s*(?:\((.*)\))?\s*$", re.I)
_RANGO = re.compile(rf"^~?\s*({_NUM})\s*(?:kcal)?\s*[-–—]\s*~?\s*({_NUM})", re.I)
_TOTAL_DETALLE = re.compile(rf"(?:→\s*)?\bTOTAL\b\s*:?\s*~?\s*({_NUM})\s*(?:kcal)?")
_SUBTOTAL = re.compile(rf"[=→]\s*~?\s*({_NUM})\s*(?:kcal)?", re.I)
_ETIQUETA_NUMERADA = re.compile(r"\d+\s*[ªº]?\s*comida\s*(?:\([^)]*\))?\s*:", re.I)
_ETIQUETA = re.compile(r"\b(?:desayuno|comida|cena|merienda|postre|real)\s*:", re.I)
_TROZOS = re.compile(r"\s+/\s+|\s+\+\s+|;")
# bug 020 (R8): «−480» / «-480» after a space is a negative kcal (an «ajuste hasta el total del día»)
_TROZO = re.compile(rf"^(.*?)\s*~?\s*(?:(?<=\s)([-−]))?({_NUM})\s*(?:kcal)?\s*$", re.I)
_CANTIDAD = re.compile(r"(\d+(?:[.,]\d+)?)\s*(g|ml)\b", re.I)
_FECHA_DMA = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_FECHA_ISO = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")
_KG = re.compile(r"(\d+(?:[.,]\d+)?)")


class ErrorImportacion(ValueError):
    """A source file is missing something the import needs; the message says what."""


@dataclass(frozen=True)
class Duda:
    """Why a kcal cell cannot be imported as it is, and the proposal if there is one."""

    motivo: str
    propuesta: float | None = None


@dataclass
class Peso:
    dia: date
    kg: float
    nota: str | None
    texto: str


@dataclass
class Dia:
    dia: date
    alimentos: list[Alimento]
    texto: str

    @property
    def con_detalle(self) -> bool:
        return not (len(self.alimentos) == 1 and self.alimentos[0].nombre == TOTAL_IMPORTADO)


@dataclass
class Dudoso:
    """An item that does not count until Andrés accepts the proposal, corrects it or omits it."""

    apartado: str
    texto: str
    motivo: str
    propuesta: float | None
    dia: date | None
    tipo: str  # "peso" or "dia"
    corregible: bool = True  # False: a date that does not exist or has not come yet (only omit)
    filas: int = 1  # rows of the source it stands for (a repeated day shows all of them)
    nota: str | None = None
    supuestos: list[str] = field(default_factory=list)
    alimentos: list[Alimento] | None = None  # what accepting the proposal saves, if not a day total

    def como_dict(self) -> dict:
        return {"apartado": self.apartado, "texto": self.texto, "motivo": self.motivo,
                "propuesta": self.propuesta, "dia": self.dia.isoformat() if self.dia else None}


@dataclass
class Lectura:
    pesos: list[Peso] = field(default_factory=list)
    dias: list[Dia] = field(default_factory=list)
    sin_pesar: int = 0
    filas_pesos: int = 0  # rows of the weight table plus the weights only the chart has
    filas_registro: int = 0
    referencias: list[str] = field(default_factory=list)
    taras: list[str] = field(default_factory=list)
    dudosos: list[Dudoso] = field(default_factory=list)

    def resumen(self) -> str:
        con_total = [d for d in self.dias if not d.con_detalle]
        con_detalle = [d for d in self.dias if d.con_detalle]
        alimentos = sum(len(d.alimentos) for d in con_detalle)
        return (
            f"{len(self.pesos)} pesos · {len(self.dias)} días ({len(con_total)} con total, "
            f"{len(con_detalle)} con {alimentos} alimentos) · {self.sin_pesar} sin pesar · "
            f"{len(self.dudosos)} dudosos · encontradas y NO importadas: {len(self.referencias)} recetas o "
            f"alimentos de referencia, {len(self.taras)} taras"
        )


def numero(valor: float | None) -> str:
    """A number as Andrés writes it: no trailing zeros, decimal comma."""
    return "—" if valor is None else f"{valor:g}".replace(".", ",")


def _kcal(texto: str) -> float:
    return float(texto.replace(".", ""))


def _limpia(celda: str) -> str:
    return celda.replace("**", "").strip()


# -- the kcal column ------------------------------------------------------------------------

def _total(kcal: float, dia: date, comentario: str | None = None) -> list[Alimento]:
    supuestos = [SUPUESTO] + ([comentario.strip()] if comentario and comentario.strip() else [])
    return [Alimento(nombre=TOTAL_IMPORTADO, dia=dia, cantidad=1, unidad="día", kcal=kcal, estimado=True,
                     supuestos=supuestos)]


def _detalle(texto: str, dia: date) -> list[Alimento] | Duda:
    texto = re.sub(r"\[[^\]]*\]", " ", texto)
    texto = _ETIQUETA.sub(" ", _ETIQUETA_NUMERADA.sub(" ", texto))
    totales = _TOTAL_DETALLE.findall(texto)
    total = _kcal(totales[-1]) if totales else None
    texto = _SUBTOTAL.sub(" ", _TOTAL_DETALLE.sub(" ", texto))
    alimentos: list[Alimento] = []
    completo = True
    for trozo in _TROZOS.split(texto):
        trozo = trozo.strip(" ,.→=\t")
        if not trozo:
            continue
        m = _TROZO.match(trozo)
        nombre = m.group(1).strip(" ~,") if m else ""
        if not m or not nombre:
            completo = False
            continue
        cantidad, unidad = 1.0, "ración"
        cantidades = _CANTIDAD.findall(nombre)
        if len(cantidades) == 1:
            cantidad, unidad = float(cantidades[0][0].replace(",", ".")), cantidades[0][1].lower()
            nombre = " ".join(_CANTIDAD.sub(" ", nombre).split())
        kcal = -_kcal(m.group(3)) if m.group(2) else _kcal(m.group(3))
        alimentos.append(Alimento(nombre=nombre, dia=dia, cantidad=cantidad, unidad=unidad, kcal=kcal,
                                  estimado=True, supuestos=[SUPUESTO]))
    suma = sum(a.kcal or 0 for a in alimentos)
    if not alimentos or not completo or (total is not None and abs(suma - total) > TOLERANCIA_DETALLE * total):
        return Duda(NO_CUADRA, total)
    return alimentos


def leer_kcal(celda: str, dia: date) -> list[Alimento] | Duda:
    """The «Kcal consumidas» cell of one day: its food lines, or why it is dubious."""
    texto = _limpia(celda)
    m = _TOTAL_SIMPLE.match(texto)
    if m:
        return _total(_kcal(m.group(1)), dia, m.group(2))
    m = _RANGO.match(texto)
    if m:
        return Duda(RANGO, round((_kcal(m.group(1)) + _kcal(m.group(2))) / 2))
    if "en curso" in texto.lower() or not re.search(r"\d", texto):
        return Duda(SIN_TOTAL)
    return _detalle(texto, dia)


def _fuera_de_rango(alimentos: list[Alimento]) -> bool:
    suma = sum(a.kcal or 0 for a in alimentos)
    return not KCAL_DIA[0] <= suma <= KCAL_DIA[1]


def kcal_valida(kcal: float) -> bool:
    return KCAL_DIA[0] <= kcal <= KCAL_DIA[1]


# -- Markdown tables -------------------------------------------------------------------------

def _secciones(md: str) -> dict[str, str]:
    """`## Title` → its text up to the next `## `, keyed by the title without leading symbols."""
    secciones: dict[str, str] = {}
    actual: str | None = None
    for linea in md.splitlines():
        if linea.startswith("## "):
            actual = re.sub(r"^[^\wÁÉÍÓÚÑáéíóúñ]+", "", linea[3:]).strip()
            secciones[actual] = ""
        elif actual is not None:
            secciones[actual] += linea + "\n"
    return secciones


def _seccion(secciones: Mapping[str, str], comienzo: str, obligatoria: bool) -> str:
    for titulo, texto in secciones.items():
        if titulo.lower().startswith(comienzo.lower()):
            return texto
    if obligatoria:
        raise ErrorImportacion(f"a deficit.md le falta la sección «{comienzo}»")
    return ""


def _es_separador(celdas: list[str]) -> bool:
    return bool(celdas) and all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in celdas if c.strip())


def _tabla(texto: str) -> list[list[str]]:
    """Data rows of the Markdown tables in `texto` (header rows and separators left out)."""
    filas = [[c.strip() for c in linea.strip().strip("|").split("|")]
             for linea in texto.splitlines() if linea.strip().startswith("|")]
    datos = []
    for i, celdas in enumerate(filas):
        siguiente = filas[i + 1] if i + 1 < len(filas) else None
        if _es_separador(celdas) or (siguiente is not None and _es_separador(siguiente)):
            continue
        datos.append(celdas)
    return datos


def _nombre_referencia(celda: str) -> str:
    return _limpia(celda.split(" (")[0])


# -- the chart -------------------------------------------------------------------------------

def _array(html: str, nombre: str) -> tuple[list[str], int]:
    m = re.search(rf"\b(?:const|let|var)\s+{nombre}\s*=\s*\[(.*?)\]", html, re.S)
    if not m:
        raise ErrorImportacion(f"a la gráfica le falta `{nombre}`")
    return [x.strip() for x in m.group(1).split(",") if x.strip()], m.end()


def leer_grafica(html: str, anio: int) -> dict[date, float]:
    """`days` (`d/m`, no year) and `weight` of the chart → weight by date.

    The year is `anio` and goes up by one each time the month goes down; `null` days are left
    out; `weight[i] = x;` lines after the arrays are applied.
    """
    dias, _ = _array(html, "days")
    pesos, fin = _array(html, "weight")
    for indice, valor in re.findall(r"\bweight\[(\d+)\]\s*=\s*([\d.,]+|null)\s*;", html[fin:]):
        if int(indice) < len(pesos):
            pesos[int(indice)] = valor
    resultado: dict[date, float] = {}
    mes_anterior = None
    for texto_dia, texto_peso in zip(dias, pesos, strict=False):
        m = re.fullmatch(r"['\"`]?\s*(\d{1,2})/(\d{1,2})\s*['\"`]?", texto_dia)
        if not m:
            continue
        dia, mes = int(m.group(1)), int(m.group(2))
        if mes_anterior is not None and mes < mes_anterior:
            anio += 1
        mes_anterior = mes
        if texto_peso == "null":
            continue
        try:
            resultado[date(anio, mes, dia)] = float(texto_peso.replace(",", "."))
        except ValueError:
            continue
    return resultado


# -- reading -----------------------------------------------------------------------------------

def _peso(kg_texto: str, dia: date, nota: str, hoy: date) -> tuple[date, float, str | None] | str:
    try:
        return peso_valido({"dia": dia, "kg": kg_texto.replace(",", "."), "nota": nota}, hoy)
    except ValueError as exc:
        return str(exc)


def _leer_pesos(texto: str, hoy: date, lectura: Lectura) -> tuple[dict[date, list[tuple[float, str | None, str]]], set[date]]:
    """Weight rows → by day (to find repeats) and the set of dates the table mentions at all."""
    por_dia: dict[date, list[tuple[float, str | None, str]]] = {}
    fechas: set[date] = set()
    for celdas in _tabla(texto):
        lectura.filas_pesos += 1
        original = " | ".join(celdas)
        fecha_texto = celdas[0] if celdas else ""
        kg = _KG.search(celdas[1]) if len(celdas) > 1 else None
        if kg is None:
            lectura.sin_pesar += 1
            continue
        nota = _limpia(celdas[2]) if len(celdas) > 2 else ""
        m = _FECHA_DMA.search(fecha_texto)
        if not m:
            lectura.dudosos.append(Dudoso(PESOS, original, FECHA_NO_RECONOCIDA, None, None, "peso", False))
            continue
        try:
            dia = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            lectura.dudosos.append(Dudoso(PESOS, original, FECHA_IMPOSIBLE, None, None, "peso", False))
            continue
        fechas.add(dia)
        valido = _peso(kg.group(1), dia, nota, hoy)
        if isinstance(valido, str):
            lectura.dudosos.append(Dudoso(PESOS, original, valido, None, dia, "peso", dia <= hoy, nota=nota or None))
            continue
        por_dia.setdefault(dia, []).append((valido[1], valido[2], original))
    return por_dia, fechas


def _cruzar_grafica(por_dia: dict[date, list[tuple[float, str | None, str]]], fechas: set[date],
                    grafica: dict[date, float], hoy: date, lectura: Lectura) -> None:
    for dia, filas in sorted(por_dia.items()):
        if len(filas) > 1:
            kg, nota, _ = filas[-1]
            texto = " · ".join(f"fila {i}: {original}" for i, (_, _, original) in enumerate(filas, 1))
            lectura.dudosos.append(Dudoso(PESOS, texto, f"día repetido en la tabla de pesos ({len(filas)} filas)",
                                          kg, dia, "peso", filas=len(filas), nota=nota))
            continue
        kg, nota, original = filas[0]
        en_grafica = grafica.get(dia)
        if en_grafica is not None and abs(en_grafica - kg) > 1e-6:
            lectura.dudosos.append(Dudoso(
                PESOS, f"{original} (tabla: {numero(kg)} kg · gráfica: {numero(en_grafica)} kg)",
                f"la gráfica dice {numero(en_grafica)} kg y la tabla {numero(kg)} kg", kg, dia, "peso", nota=nota))
            continue
        lectura.pesos.append(Peso(dia, kg, nota, original))
    for dia, kg in sorted(grafica.items()):
        if dia in fechas:
            continue
        lectura.filas_pesos += 1
        texto = f"{dia.day}/{dia.month} · {numero(kg)} kg (gráfica)"
        valido = _peso(str(kg), dia, "", hoy)
        if isinstance(valido, str):
            lectura.dudosos.append(Dudoso(GRAFICA, texto, f"solo está en la gráfica y {valido}", None, dia, "peso",
                                          dia <= hoy))
        else:
            lectura.dudosos.append(Dudoso(GRAFICA, texto, "el peso solo está en la gráfica, no en la tabla",
                                          valido[1], dia, "peso"))


def _leer_registro(texto: str, hoy: date, lectura: Lectura) -> None:
    por_dia: dict[date, list[tuple[str, str]]] = {}
    for celdas in _tabla(texto):
        lectura.filas_registro += 1
        original = " | ".join(celdas)
        fecha_texto = _limpia(celdas[0]) if celdas else ""
        if not _FECHA_ISO.match(fecha_texto):
            lectura.dudosos.append(Dudoso(REGISTRO, original, FECHA_NO_RECONOCIDA, None, None, "dia", False))
            continue
        try:
            dia = date.fromisoformat(fecha_texto)
        except ValueError:
            lectura.dudosos.append(Dudoso(REGISTRO, original, FECHA_IMPOSIBLE, None, None, "dia", False))
            continue
        if dia > hoy:
            lectura.dudosos.append(Dudoso(REGISTRO, original, POSTERIOR, None, dia, "dia", False))
            continue
        if len(celdas) < 4:
            lectura.dudosos.append(Dudoso(REGISTRO, original, "falta la columna de kcal consumidas", None, dia, "dia"))
            continue
        por_dia.setdefault(dia, []).append((celdas[3], original))
    for dia, filas in sorted(por_dia.items()):
        celda = filas[-1][0]
        leido = leer_kcal(celda, dia)
        if len(filas) > 1:
            texto_filas = " · ".join(f"fila {i}: {c}" for i, (c, _) in enumerate(filas, 1))
            if isinstance(leido, Duda):
                propuesta, alimentos = leido.propuesta, None
            else:
                propuesta, alimentos = sum(a.kcal or 0 for a in leido), leido
            lectura.dudosos.append(Dudoso(
                REGISTRO, texto_filas, f"día repetido en el registro ({len(filas)} filas; se propone la última)",
                propuesta, dia, "dia", filas=len(filas), alimentos=alimentos))
        elif isinstance(leido, Duda):
            lectura.dudosos.append(Dudoso(REGISTRO, celda, leido.motivo, leido.propuesta, dia, "dia"))
        elif _fuera_de_rango(leido):
            lectura.dudosos.append(Dudoso(REGISTRO, celda, FUERA_KCAL, None, dia, "dia",
                                          supuestos=leido[0].supuestos[1:] if len(leido) == 1 else []))
        else:
            lectura.dias.append(Dia(dia, leido, celda))


def _anio_inicial(md_pesos: str, hoy: date) -> int:
    for celdas in _tabla(md_pesos):
        m = _FECHA_DMA.search(celdas[0] if celdas else "")
        if m:
            return int(m.group(3))
    return hoy.year


def leer(md_texto: str, html_texto: str | None, hoy: date) -> Lectura:
    """Everything the two files say, checked. `ErrorImportacion` if a needed part is missing."""
    secciones = _secciones(md_texto)
    texto_pesos = _seccion(secciones, PESOS, True)
    texto_registro = _seccion(secciones, REGISTRO, True)
    grafica = leer_grafica(html_texto, _anio_inicial(texto_pesos, hoy)) if html_texto is not None else {}
    lectura = Lectura()
    por_dia, fechas = _leer_pesos(texto_pesos, hoy, lectura)
    _cruzar_grafica(por_dia, fechas, grafica, hoy, lectura)
    _leer_registro(texto_registro, hoy, lectura)
    lectura.taras = [_limpia(c[0]) for c in _tabla(_seccion(secciones, TARAS, False)) if c and _limpia(c[0])]
    lectura.referencias = [_nombre_referencia(c[0]) for c in _tabla(_seccion(secciones, REFERENCIAS, False))
                           if c and _nombre_referencia(c[0])]
    return lectura


@dataclass
class FilasRecetas:
    """The whole rows (cells) of «Referencias calóricas fijas» and «Taras de ollas» (unit 011, D7)."""

    referencias: list[list[str]]
    taras: list[list[str]]


def filas_de_recetas(md_texto: str) -> FilasRecetas:
    """The two tables `leer` only counts by name, with every cell, for `tridata importar-recetas`.

    Neither section is required: a file without them gives empty lists.
    """
    secciones = _secciones(md_texto)
    return FilasRecetas(referencias=_tabla(_seccion(secciones, REFERENCIAS, False)),
                        taras=_tabla(_seccion(secciones, TARAS, False)))


# -- bug 020: the foods of a day that was imported as one total line ---------------------------------
#
# `tridata importar-detalle` re-reads the «Registro diario» and, only on the days whose lines in the base are
# all the single imported total, puts one line per food plus an «ajuste hasta el total del día» so the day
# adds up exactly what it did. Read by rules, no AI (contract R3).

AJUSTE = "ajuste hasta el total del día"
SIN_KCAL = "sin kcal en el registro"

_EMPIEZA_NUMERO = re.compile(r"^~?\s*\d")
_EMPIEZA_EN_CURSO = re.compile(r"^en\s+curso\b", re.I)
_NUMERO_INICIAL = re.compile(rf"^~?\s*({_NUM})")
_KCAL_FINAL = re.compile(rf"(?:~\s*({_NUM})\s*(?:kcal)?|({_NUM})\s*kcal)\s*$", re.I)
_KCAL_ANTES_DE_BARRA = re.compile(rf"(?:~\s*{_NUM}\s*(?:kcal)?|{_NUM}\s*kcal)$", re.I)
_ETIQUETA_LIBRE = re.compile(r"^([^\d:()\[\]~]+?)\s*(\([^\d()\[\]]*\))?\s*:\s*")
_ETIQUETA_NUMERO = re.compile(r"^\d+\s*[ªº]?\s*comida\s*(?:\([^)]*\))?\s*:\s*", re.I)
_ABREN, _CIERRAN = "([", ")]"


def _primer_parentesis(texto: str) -> tuple[str, str] | None:
    """The inside of the first top-level «(…)» (nested ones counted) and what follows it; None without one."""
    profundidad, inicio = 0, None
    for i, c in enumerate(texto):
        if c in _ABREN:
            if profundidad == 0 and c == "(" and inicio is None:
                inicio = i
            profundidad += 1
        elif c in _CIERRAN and profundidad:
            profundidad -= 1
            if profundidad == 0 and inicio is not None:
                return texto[inicio + 1:i], texto[i + 1:]
    return (texto[inicio + 1:], "") if inicio is not None else None


def _partir(lista: str) -> list[str]:
    """R3.2: the pieces of the list, only at the top level: «;», « / », « + » and «/» right after kcal."""
    trozos, actual, profundidad, i = [], "", 0, 0
    while i < len(lista):
        c = lista[i]
        if c in _ABREN:
            profundidad += 1
        elif c in _CIERRAN and profundidad:
            profundidad -= 1
        elif profundidad == 0:
            if c == ";":
                trozos.append(actual)
                actual, i = "", i + 1
                continue
            if c in "/+" and lista[i - 1:i] == " " and lista[i + 1:i + 2] == " ":
                trozos.append(actual)
                actual, i = "", i + 2
                continue
            siguiente = lista[i + 1:i + 2]
            if (c == "/" and _KCAL_ANTES_DE_BARRA.search(actual) and siguiente and not siguiente.isspace()
                    and not siguiente.isdigit()):
                trozos.append(actual)
                actual, i = "", i + 1
                continue
        actual += c
        i += 1
    trozos.append(actual)
    return [t.strip() for t in trozos if t.strip()]


def _sin_etiqueta(trozo: str) -> str:
    """R3.3: «desayuno:», «plato de prueba (comida/cena):», «1ª comida (14:30):» … out of the start."""
    for _ in range(3):
        m = _ETIQUETA_NUMERO.match(trozo)
        if not m:
            m = _ETIQUETA_LIBRE.match(trozo)
            if m and len(m.group(1).split()) > 5:
                m = None
        if not m:
            break
        trozo = trozo[m.end():]
    return trozo


def _hasta_subtotal(trozo: str) -> str:
    """R3.3: cut at the first top-level «=» or «→»: what follows is a subtotal."""
    profundidad = 0
    for i, c in enumerate(trozo):
        if c in _ABREN:
            profundidad += 1
        elif c in _CIERRAN and profundidad:
            profundidad -= 1
        elif profundidad == 0 and c in "=→":
            return trozo[:i]
    return trozo


def _sin_parentesis(trozo: str) -> tuple[str, list[str]]:
    """The text without its top-level «(…)» and «[…]», and their insides (for the assumptions)."""
    fuera, dentros, actual, profundidad = "", [], "", 0
    for c in trozo:
        if c in _ABREN:
            if profundidad:
                actual += c
            profundidad += 1
        elif c in _CIERRAN and profundidad:
            profundidad -= 1
            if profundidad:
                actual += c
            else:
                dentros.append(actual.strip())
                actual = ""
                fuera += " "
        elif profundidad:
            actual += c
        else:
            fuera += c
    if profundidad:  # an unclosed bracket: its text goes with the assumptions too
        dentros.append(actual.strip())
    return fuera, [x for x in dentros if x]


def _nombre_limpio(texto: str) -> str:
    return " ".join(texto.split()).strip(" ,.;:-—–~*")


@dataclass
class _Trozo:
    texto: str  # without label, subtotal nor kcal: what goes to the assumptions
    nombre: str
    cantidad: float | None
    unidad: str | None
    kcal: float | None
    dentro: list[str]


def _leer_trozo(crudo: str) -> _Trozo | None:
    trozo = _hasta_subtotal(_sin_etiqueta(crudo.replace("**", "").strip())).strip()
    fuera, dentro = _sin_parentesis(trozo)
    kcal = None
    m = _KCAL_FINAL.search(fuera)
    if m:
        kcal = _kcal(m.group(1) or m.group(2))
        fuera = fuera[:m.start()]
        resto = _KCAL_FINAL.search(trozo)
        trozo = trozo[:resto.start()] if resto else trozo
    texto = _nombre_limpio(trozo)
    nombre = _nombre_limpio(fuera)
    if not texto and kcal is None:
        return None
    cantidad, unidad = None, None
    cantidades = _CANTIDAD.findall(nombre)
    if len(cantidades) == 1:
        cantidad, unidad = float(cantidades[0][0].replace(",", ".")), cantidades[0][1].lower()
        nombre = _nombre_limpio(_CANTIDAD.sub(" ", nombre))
    return _Trozo(texto, nombre or "sin nombre", cantidad, unidad, kcal, dentro)


def _lista(celda: str) -> tuple[str, str | None] | None:
    """R3.1: the list of a cell and the comment after it; None when the cell has no list at all."""
    texto = _limpia(celda)
    parentesis = _primer_parentesis(texto)
    if _EMPIEZA_NUMERO.match(texto) or _EMPIEZA_EN_CURSO.match(texto):
        if parentesis is None:
            return None
        lista, despues = parentesis
        comentario = _nombre_limpio(despues)
        return lista, comentario or None
    return texto, None


def total_del_fichero(celda: str) -> float | None:
    """R4: the total the registry gives a day: the number at the start, else the last «TOTAL ~N»."""
    texto = _limpia(celda)
    m = _NUMERO_INICIAL.match(texto)
    if m:
        return _kcal(m.group(1))
    totales = _TOTAL_DETALLE.findall(texto)
    return _kcal(totales[-1]) if totales else None


def lineas_del_detalle(celda: str, dia: date, total: float, supuestos_previos: list[str]) -> list[Alimento] | None:
    """R3: the food lines of the cell plus the «ajuste hasta el total del día» to `total`; None without detail.

    `supuestos_previos` are those of the line it replaces: beyond «importado de deficit.md», they go (with the
    comment after the list and the pieces without kcal) to the adjustment, or to the first line without one.
    """
    lista = _lista(celda)
    if lista is None:
        return None
    crudos = _partir(lista[0])
    trozos = [t for t in (_leer_trozo(c) for c in crudos) if t is not None]
    if len(trozos) < 2 or not any(t.kcal is not None for t in trozos):
        return None

    def linea(nombre: str, cantidad: float, unidad: str, kcal: float, supuestos: list[str]) -> Alimento:
        return Alimento(nombre=nombre, dia=dia, cantidad=cantidad, unidad=unidad, kcal=kcal, estimado=True,
                        supuestos=[SUPUESTO, *supuestos])

    lineas: list[Alimento] = []
    sin_kcal: list[tuple[int, str]] = []
    pendientes: list[tuple[int, _Trozo]] = []
    for i, t in enumerate(trozos):
        if t.kcal is None:
            if t.cantidad is not None:
                pendientes.append((i, t))
            else:
                sin_kcal.append((i, t.texto))
            continue
        if pendientes:
            juntos = [p for _, p in pendientes] + [t]
            lineas.append(linea(" + ".join(p.nombre for p in juntos), 1, "ración", t.kcal,
                                [" + ".join(p.texto for p in juntos), *(x for p in juntos for x in p.dentro)]))
            pendientes = []
        elif t.cantidad is not None and t.unidad is not None:
            lineas.append(linea(t.nombre, t.cantidad, t.unidad, t.kcal, t.dentro))
        else:
            lineas.append(linea(t.nombre, 1, "ración", t.kcal, t.dentro))
    sin_kcal += [(i, p.texto) for i, p in pendientes]

    extra = [s for s in supuestos_previos if s != SUPUESTO]
    if lista[1]:
        extra.append(lista[1])
    if sin_kcal:
        extra.append(f"{SIN_KCAL}: " + " · ".join(texto for _, texto in sorted(sin_kcal)))
    ajuste = total - sum(a.kcal or 0 for a in lineas)
    if abs(ajuste) > 1e-9:
        lineas.append(linea(AJUSTE, 1, "ración", ajuste, extra))
    elif extra:
        lineas[0].supuestos += extra
    return lineas


# what happens to each day (R2, R4)
CAMBIA = "cambia"
SIN_DETALLE = "sigue igual: sin detalle"
CON_APP = "no se toca: algo apuntado en la app"
YA_DETALLE = "no se toca: ya tiene sus alimentos"
NO_COINCIDE = "no se toca"
NO_EN_BASE = "no está en la base: no se añade"
CAMBIADO_ENTRETANTO = "no se toca: ha cambiado en la base mientras tanto, se queda como estaba"


def _kcal_texto(n: float, signo: bool = False) -> str:
    entero = int(abs(float(n)) + 0.5)
    prefijo = ("+" if n > 0 else "−" if n < 0 else "") if signo else ("−" if n < 0 and entero else "")
    return prefijo + f"{entero:,}".replace(",", ".")


@dataclass
class DiaDetalle:
    dia: date
    resultado: str
    total: float | None = None  # the kcal the day has in the base
    fichero: float | None = None  # the total the registry or the dubious decision gives
    lineas: list[Alimento] = field(default_factory=list)

    def texto(self) -> str:
        if self.resultado == NO_COINCIDE:
            return (f"{self.dia} · no se toca: el total del fichero ({_kcal_texto(self.fichero or 0) if self.fichero is not None else '—'}) "
                    f"no es el de la base ({_kcal_texto(self.total or 0)})")
        if self.resultado != CAMBIA:
            return f"{self.dia} · {self.resultado}"
        filas = [f"{self.dia} · cambia: {len(self.lineas)} líneas, suman {_kcal_texto(self.total or 0)} kcal "
                 "(igual que antes)"]
        for a in self.lineas:
            cantidad = f"{numero(a.cantidad)} {a.unidad}" if a.unidad in ("g", "ml") else "1 ración"
            supuestos = [s for s in a.supuestos if s != SUPUESTO]
            filas.append(f"    {a.nombre} · {cantidad} · {_kcal_texto(a.kcal or 0, signo=a.nombre == AJUSTE)} kcal"
                         + (f" — {' · '.join(supuestos)}" if supuestos else ""))
        return "\n".join(filas)


@dataclass
class PlanDetalle:
    dias: list[DiaDetalle]

    def cambian(self) -> list[DiaDetalle]:
        return [x for x in self.dias if x.resultado == CAMBIA]

    def resumen(self) -> str:
        cuenta = {r: sum(x.resultado == r for x in self.dias) for r in (SIN_DETALLE, CON_APP, YA_DETALLE, NO_COINCIDE)}
        cambian = self.cambian()
        ajustes = sum(a.nombre == AJUSTE for x in cambian for a in x.lineas)
        alimentos = sum(len(x.lineas) for x in cambian) - ajustes
        return (f"Detalle: {len(cambian)} días cambiados ({alimentos} alimentos, {ajustes} ajustes) · "
                f"{cuenta[SIN_DETALLE]} sin detalle siguen igual · no se tocan: {cuenta[CON_APP]} con algo de la app, "
                f"{cuenta[YA_DETALLE]} que ya tenía sus alimentos, {cuenta[NO_COINCIDE]} cuyo total no coincide")


def solo_total_importado(lineas: list[dict]) -> bool:
    """R4: every line of the day is the imported total, without an interpretation."""
    return bool(lineas) and all(x["interpretacion_id"] is None and x["nombre"] == TOTAL_IMPORTADO for x in lineas)


def _de_la_app(lineas: list[dict]) -> bool:
    return any(x["interpretacion_id"] is not None or SUPUESTO not in json.loads(x["supuestos"] or "[]")
               for x in lineas)


def lineas_de_la_base(db_path: Path | str) -> dict[date, list[dict]]:
    """Every food line of the base by day, read-only (the dry run never writes)."""
    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        existe = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alimento_apuntado'").fetchone()
        filas = conn.execute("SELECT id, dia, nombre, kcal, supuestos, interpretacion_id FROM alimento_apuntado "
                             "ORDER BY id").fetchall() if existe else []
    finally:
        conn.close()
    por_dia: dict[date, list[dict]] = {}
    for f in filas:
        por_dia.setdefault(date.fromisoformat(f["dia"]), []).append(dict(f))
    return por_dia


def decididos(ultima: Mapping | None) -> dict[date, float]:
    """R4: the total Andrés accepted or corrected for each dubious day of the «Registro diario»."""
    resultado: dict[date, float] = {}
    for x in ((ultima or {}).get("resumen") or {}).get("dudosos") or []:
        if x.get("apartado") == REGISTRO and x.get("decision") in (ACEPTADO, CORREGIDO) and x.get("dia") \
                and x.get("valor") is not None:
            resultado[date.fromisoformat(x["dia"])] = float(x["valor"])
    return resultado


def planear_detalle(md_texto: str, base: Mapping[date, list[dict]], decisiones: Mapping[date, float]) -> PlanDetalle:
    """What `importar-detalle` would do with each day of the registry. `ErrorImportacion` without the section."""
    texto = _seccion(_secciones(md_texto), REGISTRO, True)
    celdas: dict[date, str] = {}
    for fila in _tabla(texto):
        fecha = _limpia(fila[0]) if fila else ""
        if len(fila) < 4 or not _FECHA_ISO.match(fecha):
            continue
        try:
            celdas[date.fromisoformat(fecha)] = fila[3]  # repeated rows: the last one, as unit 009
        except ValueError:
            continue
    dias = []
    for dia, celda in sorted(celdas.items()):
        lineas = base.get(dia, [])
        if not lineas:
            dias.append(DiaDetalle(dia, NO_EN_BASE))
            continue
        if not solo_total_importado(lineas):
            dias.append(DiaDetalle(dia, CON_APP if _de_la_app(lineas) else YA_DETALLE))
            continue
        total = sum(x["kcal"] or 0 for x in lineas)
        supuestos = list(dict.fromkeys(s for x in lineas for s in json.loads(x["supuestos"] or "[]")))
        nuevas = lineas_del_detalle(celda, dia, total, supuestos)
        if nuevas is None:
            dias.append(DiaDetalle(dia, SIN_DETALLE, total))
            continue
        fichero = decisiones.get(dia, total_del_fichero(celda))
        if fichero is None or abs(fichero - total) > 1e-6:
            dias.append(DiaDetalle(dia, NO_COINCIDE, total, fichero))
            continue
        dias.append(DiaDetalle(dia, CAMBIA, total, fichero, nuevas))
    return PlanDetalle(dias)


# -- decisions and writing ---------------------------------------------------------------------

ACEPTADO, CORREGIDO, OMITIDO = "aceptado", "corregido", "omitido"


def valor_valido(dudoso: Dudoso, valor: float) -> bool:
    """A correction must be a sensible kcal total or weight."""
    if dudoso.tipo == "peso":
        return isinstance(_peso(str(valor), dudoso.dia or date.min, "", dudoso.dia or date.min), tuple)
    return kcal_valida(valor)


@dataclass
class Resumen:
    pesos_nuevos: int = 0
    pesos_ya: int = 0
    pesos_omitidos: int = 0
    pesos_repetidos: int = 0  # extra rows of a repeated weight day that was resolved to one
    sin_pesar: int = 0
    filas_pesos: int = 0
    dias_nuevos: int = 0
    dias_ya: int = 0
    dias_omitidos: int = 0
    dias_repetidos: int = 0
    filas_registro: int = 0
    ya_con_peso: list[str] = field(default_factory=list)
    ya_con_comidas: list[str] = field(default_factory=list)

    def como_dict(self) -> dict:
        return dict(self.__dict__)

    def texto(self) -> str:
        lineas = [
            f"Guardado: {self.pesos_nuevos} pesos nuevos, {self.dias_nuevos} días nuevos; "
            f"{self.pesos_ya} y {self.dias_ya} ya estaban · omitidos: {self.pesos_omitidos} pesos, "
            f"{self.dias_omitidos} días · {self.sin_pesar} sin pesar",
        ]
        lineas += [f"  {dia}: ya tenía peso en la app (no se toca)" for dia in self.ya_con_peso]
        lineas += [f"  {dia}: ya tenía comidas en la app (no se tocan)" for dia in self.ya_con_comidas]
        rep_p = f" + {self.pesos_repetidos} repetidas" if self.pesos_repetidos else ""
        rep_d = f" + {self.dias_repetidos} repetidas" if self.dias_repetidos else ""
        lineas.append(
            f"Cuadre: pesos {self.filas_pesos} filas = {self.pesos_nuevos} nuevos + {self.pesos_ya} ya estaban + "
            f"{self.pesos_omitidos} omitidos + {self.sin_pesar} sin pesar{rep_p}; registro {self.filas_registro} "
            f"filas = {self.dias_nuevos} nuevos + {self.dias_ya} ya estaban + {self.dias_omitidos} omitidos{rep_d}"
        )
        return "\n".join(lineas)


Decision = tuple[str, float | None]


def _resuelto(dudoso: Dudoso, decision: Decision) -> Peso | Dia | None:
    """What a decided dubious item saves, or None if it is omitted."""
    que, valor = decision
    if que == OMITIDO or dudoso.dia is None or not dudoso.corregible:
        return None
    if que == ACEPTADO:
        if dudoso.propuesta is None:
            return None
        if dudoso.alimentos is not None:
            return Dia(dudoso.dia, dudoso.alimentos, dudoso.texto)
        valor = dudoso.propuesta
    if valor is None:
        return None
    if dudoso.tipo == "peso":
        return Peso(dudoso.dia, valor, dudoso.nota, dudoso.texto)
    alimentos = _total(valor, dudoso.dia)
    alimentos[0].supuestos += dudoso.supuestos + [f"revisado al importar: {dudoso.motivo}"]
    return Dia(dudoso.dia, alimentos, dudoso.texto)


def aplicar(lectura: Lectura, decisiones: Mapping[int, Decision], registro: RegistroComidas,
            pesos: RegistroPeso, hoy: date) -> Resumen:
    """Write only what is missing; a day with foods or a weight already saved is never touched.

    `decisiones` maps the index of each dubious item to (aceptado | corregido | omitido, value);
    a dubious item without a decision is omitted.
    """
    resumen = Resumen(sin_pesar=lectura.sin_pesar, filas_pesos=lectura.filas_pesos,
                      filas_registro=lectura.filas_registro)
    a_guardar: list[tuple[Peso | Dia, int]] = [(p, 1) for p in lectura.pesos] + [(d, 1) for d in lectura.dias]
    for i, dudoso in enumerate(lectura.dudosos):
        resuelto = _resuelto(dudoso, decisiones.get(i, (OMITIDO, None)))
        if resuelto is None:
            if dudoso.tipo == "peso":
                resumen.pesos_omitidos += dudoso.filas
            else:
                resumen.dias_omitidos += dudoso.filas
            continue
        a_guardar.append((resuelto, dudoso.filas))
    for cosa, filas in sorted(a_guardar, key=lambda x: (isinstance(x[0], Dia), x[0].dia)):
        if isinstance(cosa, Peso):
            resumen.pesos_repetidos += filas - 1
            if pesos.del_dia(cosa.dia) is not None:
                resumen.pesos_ya += 1
                resumen.ya_con_peso.append(cosa.dia.isoformat())
            else:
                pesos.apuntar(cosa.dia, cosa.kg, cosa.nota, hoy=hoy)
                resumen.pesos_nuevos += 1
        else:
            resumen.dias_repetidos += filas - 1
            if registro.importar_dia(cosa.dia, cosa.alimentos, hoy) == 0:
                resumen.dias_ya += 1
                resumen.ya_con_comidas.append(cosa.dia.isoformat())
            else:
                resumen.dias_nuevos += 1
    return resumen


# -- the import record ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS importacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    huella TEXT NOT NULL,
    resumen TEXT NOT NULL
);
"""


def huella(md: bytes, html: bytes | None) -> str:
    return (f"md:{hashlib.sha256(md).hexdigest()} "
            f"html:{hashlib.sha256(html).hexdigest() if html is not None else '-'}")


class Importaciones:
    """Repository of confirmed imports. Reading never creates the table (nor the file)."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def registrar(self, huella: str, resumen: dict) -> dict:
        fecha = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            cur = conn.execute("INSERT INTO importacion (fecha, huella, resumen) VALUES (?, ?, ?)",
                               (fecha, huella, json.dumps(resumen, ensure_ascii=False)))
            return {"id": cur.lastrowid, "fecha": fecha, "huella": huella, "resumen": resumen}

    def ultima(self) -> dict | None:
        if not self._db_path.is_file():
            return None
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        try:
            existe = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='importacion'").fetchone()
            if not existe:
                return None
            fila = conn.execute(
                "SELECT id, fecha, huella, resumen FROM importacion ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        if fila is None:
            return None
        return {"id": fila[0], "fecha": fila[1], "huella": fila[2], "resumen": json.loads(fila[3])}
