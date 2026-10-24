"""The assistant that acts (unit 021, activity `pedir-al-asistente`, REC-12): the code decides, Claude only understands.

Claude turns what Andrés says into `CambioInterpretado`s (`ia/claude.py`, one generic shape); this package turns
each one into a result the page shows: a card (`propuesta`: «Voy a …», before → after, the concrete day and where
it stays, with the fields that can be corrected), a choice (`elegir`: the candidates, R-69) or a `no` (what is not
understood or missing, not found, does not pass the check or is done in another tab). The «before» always comes
from the base and every check is the one of the screen by hand, with its words (G-38, G-39). Nothing is written
while proposing (R-1); a card carries its action, its parameters and what it showed, so confirming needs no state
on the server: `confirmar` redoes it with what is in the base NOW and, if what Andrés saw is not what it would do,
applies nothing and gives the redone card (G-41, R-78, S-20). A card does not expire.

The catalogue is split by families so the next units do not collide: `dia` (steps of a day, remove a logged food),
`recetas` (save an ingredient or correct its kcal), `ajustes` (empty: unit 022) and `fuera` (what the assistant
does not change and where it is done, R-89). Each action declares its id, its line for Claude, how it is proposed,
how it is redone with corrected values and how it is applied (`Accion`).

What stays: table `tarjeta_asistente` (same SQLite, created when the context is built), one row per card confirmed
(`aplicada`) or discarded (`descartada`); a proposal nobody decided on is never saved.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..ajustes import Ajustes
from ..cierre import Cierres
from ..dia import FaltanAjustes, resumen_completo
from ..objetivo_dia import ObjetivosDia
from ..peso import RegistroPeso
from ..recetas import Recetario, normalizar
from ..registro import RegistroComidas

log = logging.getLogger(__name__)

AUN_NO = "Eso aún no lo sé hacer: hazlo a mano en su pestaña (Nutrición › Día, Balance, Recetas o Ajustes)"
NO_SALE = "No he podido preparar ese cambio: hazlo a mano en su pestaña"
CAMBIADO = "Ha cambiado desde que la viste: mírala otra vez"
_LO_QUE_SE_VE = ("accion", "texto", "antes", "despues", "dia", "desde")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tarjeta_asistente (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hora TEXT NOT NULL,
    texto_peticion TEXT NOT NULL,
    fecha_peticion TEXT NOT NULL,
    accion TEXT NOT NULL,
    toca TEXT NOT NULL,
    antes TEXT,
    despues TEXT,
    dia TEXT,
    desde TEXT,
    avisos TEXT NOT NULL DEFAULT '[]',
    corregida INTEGER NOT NULL DEFAULT 0,
    estado TEXT NOT NULL CHECK (estado IN ('aplicada', 'descartada'))
);
"""


class TarjetaMal(ValueError):
    """What the page sent is not a card (or not one this catalogue knows): a 400, nothing changes."""


class Cambiado(Exception):
    """Confirming: what is in the base now is not what the card showed. `tarjeta` is the redone one (a 409)."""

    def __init__(self, tarjeta: dict) -> None:
        super().__init__(CAMBIADO)
        self.tarjeta = tarjeta


class NoValida(Exception):
    """Confirming a card that does not pass the check of the screen by hand (a 422): its text says why."""


# -- what a request is and what the actions need ------------------------------------------------------------------

@dataclass(frozen=True)
class Peticion:
    """What was said and the day it was said (today, or the day of a recording): it resolves every «hoy»."""

    texto: str
    fecha: date

    def como_dict(self) -> dict:
        return {"texto": self.texto, "fecha": self.fecha.isoformat()}

    @classmethod
    def desde_dict(cls, datos: Any) -> Peticion:
        if not isinstance(datos, dict) or not isinstance(datos.get("texto"), str):
            raise TarjetaMal("la tarjeta no dice qué se pidió")
        return cls(texto=datos["texto"], fecha=fecha_de(datos.get("fecha")))


@dataclass
class Contexto:
    """The repositories an action reads and changes, the same the screens by hand use."""

    registro: RegistroComidas
    objetivos: ObjetivosDia
    ajustes: Ajustes
    pesos: RegistroPeso
    recetario: Recetario
    hoy: Callable[[], date] = date.today
    store: Any = None
    cierres: Cierres | None = None
    recientes: Callable[[], list[dict]] = field(default=lambda: [])
    tarjetas: Tarjetas = field(init=False)

    def __post_init__(self) -> None:
        if self.cierres is None:
            self.cierres = Cierres(self.registro.db_path, ajustes=self.ajustes, pesos=self.pesos,
                                   objetivos=self.objetivos, store=self.store, registro=self.registro)
        self.tarjetas = Tarjetas(self.registro.db_path)

    def resumen(self, dia: date, objetivos: Any = None) -> dict | None:
        """The day as `GET /api/dia` shows it (None without settings that day); `objetivos` to simulate a change."""
        if self.cierres is None:  # set in __post_init__
            raise RuntimeError("the context has no close of the day")
        try:
            return resumen_completo(dia, registro=self.registro, pesos=self.pesos, ajustes=self.ajustes,
                                    objetivos=objetivos or self.objetivos, store=self.store, cierres=self.cierres,
                                    hoy=self.hoy())
        except FaltanAjustes:
            return None


@dataclass(frozen=True)
class Accion:
    """One action of the catalogue. Its results are the dicts of `tarjeta`, `elegir` and `no`."""

    id: str
    linea_para_claude: str  # what it is and which fields of `CambioInterpretado` it uses; never a figure (G-38)
    proponer: Callable[[Any, Contexto, Peticion], dict]  # (cambio, ctx, peticion)
    rehacer: Callable[[dict, dict, Contexto, Peticion], dict]  # (parametros, valores corregidos o elección, ctx, …)
    aplicar: Callable[[dict, Contexto], str]  # (parametros, ctx) -> what was done, for «Hecho: …»
    cambia: bool = True  # False: the assistant does not change this (R-89), it only says where it is done


# -- the shapes of a result -------------------------------------------------------------------------------------------

def no(texto: str) -> dict:
    """Nothing is proposed, and why (G-40)."""
    return {"tipo": "no", "texto": texto}


def campo(clave: str, etiqueta: str, tipo: str, valor: Any) -> dict:
    """A value of the card that can be corrected: `numero`, `fecha` or `texto`."""
    return {"clave": clave, "etiqueta": etiqueta, "tipo": tipo, "valor": valor}


def tarjeta(*, accion: str, parametros: dict, peticion: Peticion, texto: str, antes: str | None = None,
            despues: str | None = None, dia: date | None = None, desde: date | None = None, donde: str | None = None,
            avisos: list[str] | None = None, lineas: list[str] | None = None, campos: list[dict] | None = None,
            error: str | None = None) -> dict:
    """A card «Voy a …». With `error` it does not pass the check: it is shown but cannot be confirmed (R-71)."""
    return {"tipo": "propuesta", "accion": accion, "parametros": parametros, "peticion": peticion.como_dict(),
            "texto": texto, "antes": antes, "despues": despues, "dia": dia.isoformat() if dia else None,
            "desde": desde.isoformat() if desde else None, "donde": donde, "avisos": list(avisos or []),
            "lineas": list(lineas or []), "campos": list(campos or []), "error": error, "corregida": False}


def elegir(*, accion: str, parametros: dict, peticion: Peticion, texto: str, candidatos: list[dict]) -> dict:
    """What was named is there more than once: nothing is proposed until one is chosen (R-69)."""
    return {"tipo": "elegir", "accion": accion, "parametros": parametros, "peticion": peticion.como_dict(),
            "texto": texto, "candidatos": candidatos}


# -- small helpers shared by the families ----------------------------------------------------------------------------

def fecha_de(valor: Any) -> date:
    if isinstance(valor, date):
        return valor
    if not isinstance(valor, str):
        raise TarjetaMal(f"no es una fecha: {valor!r}")
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise TarjetaMal(f"no es una fecha: {valor!r}") from None


def corta(dia: date) -> str:
    """The concrete day as the cards say it: «22-10» (G-41)."""
    return f"{dia.day:02d}-{dia.month:02d}"


def numero(n: Any) -> str:
    """«15.000», «290», «0,5»: Spanish separators, whole numbers without decimals."""
    if isinstance(n, bool) or not isinstance(n, (int, float)):
        return str(n)
    if float(n).is_integer():
        return f"{int(n):,}".replace(",", ".")
    return f"{n:,.2f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",").replace(" ", ".")


def descripcion_alimento(fila: dict) -> str:
    """«yogur natural de prueba 125 g (75 kcal), 17:30»: what a logged food is, with its hour."""
    cantidad, unidad = fila.get("cantidad"), fila.get("unidad")
    if cantidad is None:
        cuanto = "sin cantidad"
    elif unidad == "unidad":
        cuanto = f"{numero(cantidad)} {'unidad' if cantidad == 1 else 'unidades'}"
    else:
        cuanto = f"{numero(cantidad)} {unidad or 'g'}"
    kcal = f"{numero(round(fila['kcal']))} kcal" if fila.get("kcal") is not None else "sin kcal"
    creado = fila.get("creado") or ""
    hora = creado[11:16] if len(creado) >= 16 else ""
    return f"{fila['nombre']} {cuanto} ({kcal})" + (f", {hora}" if hora else "")


def buscar_apuntado(ctx: Contexto, dia: date, objeto: str) -> list[dict]:
    """What is logged on `dia` with that name: without case or accents, the whole name first, else a piece of it."""
    buscado = normalizar(objeto)
    if not buscado:
        return []
    apuntes = ctx.registro.apuntes_del_dia(dia)
    iguales = [a for a in apuntes if normalizar(a["nombre"]) == buscado]
    return iguales or [a for a in apuntes if buscado in normalizar(a["nombre"])]


def nombres_apuntados(registro: RegistroComidas) -> Callable[[date], list[str]]:
    """`dia -> the NAMES logged that day`, once each (S-17, G-38): what goes to Claude, never quantities or kcal."""
    def nombres(dia: date) -> list[str]:
        vistos: list[str] = []
        for a in registro.apuntes_del_dia(dia):
            if a["nombre"] not in vistos:
                vistos.append(a["nombre"])
        return vistos
    return nombres


# -- the catalogue -------------------------------------------------------------------------------------------------

def catalogo() -> dict[str, Accion]:
    from . import ajustes, dia, fuera, recetas  # the families import the helpers above

    acciones: dict[str, Accion] = {}
    for familia in (dia, recetas, ajustes, fuera):
        for accion in familia.ACCIONES:
            acciones[accion.id] = accion
    return acciones


def texto_acciones() -> str:
    """The fixed list of what the assistant knows, for Claude. No figure and no data of Andrés in it (G-38)."""
    todas = list(catalogo().values())
    cambia = [f"- {a.id}: {a.linea_para_claude}" for a in todas if a.cambia]
    no_cambia = [f"- {a.id}: {a.linea_para_claude}" for a in todas if not a.cambia]
    return ("Cambios que sabes proponer (accion: qué es y qué campos usa; los demás campos, null):\n"
            + "\n".join(cambia)
            + "\nLo que no cambias tú (pon esa accion y nada más; se le dirá dónde se hace):\n"
            + "\n".join(no_cambia))


def proponer(cambios: list[Any], ctx: Contexto, peticion: Peticion) -> list[dict]:
    """One result per change Claude understood, in order. Writes nothing (R-1)."""
    acciones = catalogo()
    resultados = []
    for cambio in cambios:
        if getattr(cambio, "falta", None):  # G-40: nothing half done, not even the part that was understood
            resultados.append(no(cambio.falta))
            continue
        accion = acciones.get(cambio.accion)
        if accion is None:
            resultados.append(no(AUN_NO))
            continue
        try:
            resultado = accion.proponer(cambio, ctx, peticion)
        except Exception:  # noqa: BLE001 - logged; one change that fails never hides the others nor the foods
            log.exception("could not propose the change %s", cambio.accion)
            resultado = no(NO_SALE)
        if resultado["tipo"] == "propuesta" and resultado["error"]:  # G-39: what does not pass is not proposed
            resultado = no(resultado["error"])
        resultados.append(resultado)
    return resultados


def _accion_de(t: Any, tipos: tuple[str, ...]) -> tuple[Accion, dict, Peticion]:
    if not isinstance(t, dict) or t.get("tipo") not in tipos:
        raise TarjetaMal("eso no es una tarjeta del asistente")
    ident = t.get("accion")
    accion = catalogo().get(ident) if isinstance(ident, str) else None
    if accion is None or not accion.cambia:
        raise TarjetaMal("esa tarjeta no es de nada que sepa hacer")
    if not isinstance(t.get("parametros"), dict):
        raise TarjetaMal("a la tarjeta le faltan sus datos")
    return accion, t["parametros"], Peticion.desde_dict(t.get("peticion"))


def rehacer(t: Any, ctx: Contexto, *, eleccion: Any = None, valores: Any = None) -> dict:
    """The card (or the choice) again with what is in the base now, with a chosen candidate or corrected values
    (R-69, R-71); as if it had been said so. A card that does not pass keeps its fields and says why."""
    accion, parametros, peticion = _accion_de(t, ("propuesta", "elegir"))
    if valores is not None and not isinstance(valores, dict):
        raise TarjetaMal("los valores corregidos no se entienden")
    cambios = dict(valores or {})
    if eleccion is not None:
        cambios["eleccion"] = eleccion
    try:
        resultado = accion.rehacer(parametros, cambios, ctx, peticion)
    except TarjetaMal:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise TarjetaMal(f"la tarjeta no se entiende ({exc})") from None
    if resultado["tipo"] == "propuesta":
        resultado["corregida"] = bool(t.get("corregida")) or bool(valores)
    return resultado


def confirmar(t: Any, ctx: Contexto) -> dict:
    """Redo and compare (G-41, R-78, S-20): applied only if it would do exactly what the card showed.

    Raises `Cambiado` (with the redone card, nothing applied), `NoValida` or `TarjetaMal`. Returns
    {"hecho": what was done, "tarjeta": the card applied}; its row stays in `tarjeta_asistente`.
    """
    accion, _, _ = _accion_de(t, ("propuesta",))
    rehecha = rehacer(t, ctx)
    if rehecha["tipo"] != "propuesta":
        raise Cambiado(rehecha)
    if rehecha["error"]:
        raise NoValida(rehecha["error"])
    if any(rehecha.get(k) != t.get(k) for k in _LO_QUE_SE_VE):
        raise Cambiado(rehecha)
    hecho = accion.aplicar(rehecha["parametros"], ctx)
    ctx.tarjetas.guardar(rehecha, "aplicada")
    return {"hecho": hecho, "tarjeta": rehecha}


def descartar(t: Any, ctx: Contexto) -> None:
    """Nothing changes (R-70); the card is written down as `descartada`, as it was shown."""
    _accion_de(t, ("propuesta",))
    for clave in ("texto",):
        if not isinstance(t.get(clave), str):
            raise TarjetaMal("a la tarjeta le falta su texto")
    for clave in ("antes", "despues", "dia", "desde"):
        if t.get(clave) is not None and not isinstance(t.get(clave), str):
            raise TarjetaMal("la tarjeta no se entiende")
    if not isinstance(t.get("avisos", []), list) or not all(isinstance(a, str) for a in t.get("avisos", [])):
        raise TarjetaMal("la tarjeta no se entiende")
    ctx.tarjetas.guardar(t, "descartada")


# -- what stays ----------------------------------------------------------------------------------------------------

class Tarjetas:
    """Repository of `tarjeta_asistente`: the cards Andrés confirmed or discarded."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
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

    def guardar(self, t: dict, estado: str) -> dict:
        peticion = Peticion.desde_dict(t.get("peticion"))
        fila = {
            "hora": datetime.now().isoformat(timespec="seconds"), "texto_peticion": peticion.texto,
            "fecha_peticion": peticion.fecha.isoformat(), "accion": t["accion"], "toca": t["texto"],
            "antes": t.get("antes"), "despues": t.get("despues"), "dia": t.get("dia"), "desde": t.get("desde"),
            "avisos": json.dumps(t.get("avisos") or [], ensure_ascii=False), "corregida": int(bool(t.get("corregida"))),
            "estado": estado,
        }
        with self._connect() as conn:
            cur = conn.execute(f"INSERT INTO tarjeta_asistente ({', '.join(fila)}) "  # noqa: S608 - fixed names
                               f"VALUES ({', '.join('?' for _ in fila)})", tuple(fila.values()))
            return {"id": cur.lastrowid, **fila}

    def todas(self) -> list[dict]:
        with self._connect() as conn:
            return [dict(f) for f in conn.execute("SELECT * FROM tarjeta_asistente ORDER BY id")]


__all__ = [
    "AUN_NO", "CAMBIADO", "Accion", "Cambiado", "Contexto", "NoValida", "Peticion", "TarjetaMal", "Tarjetas",
    "buscar_apuntado", "campo", "catalogo", "confirmar", "corta", "descartar", "descripcion_alimento", "elegir",
    "fecha_de", "no", "nombres_apuntados", "numero", "proponer", "rehacer", "tarjeta", "texto_acciones",
]
