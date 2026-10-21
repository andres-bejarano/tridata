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
_TROZO = re.compile(rf"^(.*?)\s*~?\s*({_NUM})\s*(?:kcal)?\s*$", re.I)
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
        alimentos.append(Alimento(nombre=nombre, dia=dia, cantidad=cantidad, unidad=unidad, kcal=_kcal(m.group(2)),
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
