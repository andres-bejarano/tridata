"""The shape of ONE change the coach prepares and makes with Andrés's «sí» (`desde_pc.preparar_desde_pc` /
`hacer_desde_pc`): `CambioInterpretado`, generic, the meaning of each field in the list of actions
(`nutricion.asistente.texto_acciones`). The code decides; whoever writes the change only fills its fields.

Unit 083: moved here from `ia/claude.py` (the interpreter of the paid API, gone) with what the coach still uses:
the lines of a change (`LineaInterpretada`), the reading of what comes as text (bug 036: «» and [] are none, a line
«cantidad|unidad|nombre|kcal»; bug 055: a number given as text, or not a number at all) and `cambio_no_entendido`,
the answer when a change does not validate (it was in `web/conversaciones.py`).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field, model_validator

_FORMA = {"dia": "una fecha AAAA-MM-DD", "desde": "una fecha AAAA-MM-DD", "fecha": "una fecha AAAA-MM-DD",
          "accion": "un id de la lista de cambios, u «otra»"}


def cambio_no_entendido(campos: list[str]) -> str:
    """Bug 055: the answer when a change does not validate, naming the fields to mend (never what was sent)."""
    if not campos:
        return "No he entendido ese cambio: no se ha preparado nada."
    malos = "; ".join(f"{c}: tiene que ser {_FORMA[c]}" if c in _FORMA else f"{c}: no tiene la forma que pide"
                      for c in campos)
    return f"No he entendido ese cambio: no se ha preparado nada. Corrige y vuelve a prepararlo ({malos})."


def _numero_enviado(texto: str) -> float | None:
    """A number written as text («1,5» too); anything else is none, so one bad piece never sinks the change."""
    try:
        return float(texto.strip().replace(",", "."))
    except ValueError:
        return None


def _linea_enviada(texto: str) -> dict[str, Any]:
    if "|" not in texto:  # just a name
        return {"nombre": texto.strip(), "cantidad": None, "unidad": None, "kcal_estimadas": None}
    cantidad, unidad, nombre, kcal = (texto.split("|") + ["", "", "", ""])[:4]
    return {"nombre": nombre.strip(), "cantidad": _numero_enviado(cantidad), "unidad": unidad.strip() or None,
            "kcal_estimadas": _numero_enviado(kcal)}


class LineaInterpretada(BaseModel):
    """One line of a change that brings several (an ingredient of a recipe, unit 023)."""

    nombre: str = Field(description="Nombre breve, tal cual lo dice")
    cantidad: float | None = Field(description="Cantidad dicha, o null")
    unidad: str | None = Field(description='"g", "ml" o "unidad", o null')
    kcal_estimadas: float | None = Field(description="Kcal estimadas solo si es algo nuevo, no guardado; o null")


class CambioInterpretado(BaseModel):
    """One change Andrés asks for (unit 021, G-37). Generic: the meaning of each field is in the list of actions."""

    @model_validator(mode="before")
    @classmethod
    def _desde_lo_enviado(cls, datos: Any) -> Any:
        """«» and [] mean none; a line given as text becomes a LineaInterpretada (bug 036)."""
        if not isinstance(datos, dict):
            return datos
        datos = {k: (None if v == "" or v == [] else v) for k, v in datos.items()}
        if isinstance(datos.get("lineas"), list):
            datos["lineas"] = [_linea_enviada(x) if isinstance(x, str) else x for x in datos["lineas"]]
        for campo in ("valor", "cantidad", "kcal_estimadas"):  # bug 055: a number given as text, or not a number at all
            if isinstance(datos.get(campo), str):
                datos[campo] = _numero_enviado(datos[campo])
        if isinstance(datos.get("valores"), str):
            numeros = (_numero_enviado(v) for v in datos["valores"].split(";"))
            datos["valores"] = [n for n in numeros if n is not None] or None
        return datos

    accion: str = Field(description='Id de la lista de cambios, u "otra"')
    objeto: str | None = Field(default=None, description="Lo que nombra, tal cual (un alimento, un ingrediente…), o null")
    campo: str | None = Field(default=None, description="Qué parte de lo nombrado cambia, si la acción lo pide; o null")
    valor: float | None = Field(default=None, description="El valor nuevo que dice, como número; o null")
    valores: list[float] | None = Field(default=None, description="Varios valores nuevos, si los dice; o null")
    texto: str | None = Field(default=None, description="Un texto nuevo, o lo que pide si accion es otra; o null")
    cantidad: float | None = Field(default=None, description="Una cantidad que dice, o null")
    unidad: str | None = Field(default=None, description="La unidad de valor o de cantidad, como diga la lista; o null")
    kcal_estimadas: float | None = Field(
        default=None, description="Kcal estimadas solo de algo NUEVO que no está guardado ni apuntado; o null")
    lineas: list[LineaInterpretada] | None = Field(default=None, description="Líneas, si la acción las pide; o null")
    dia: date | None = Field(default=None, description="Día del que habla (AAAA-MM-DD), o null si no dice ninguno")
    desde: date | None = Field(default=None, description="Desde qué día vale (AAAA-MM-DD), o null")
    fecha: date | None = Field(default=None, description="Otra fecha que dice (AAAA-MM-DD), o null")
    antes_dicho: str | None = Field(default=None, description="El valor de antes, solo si la persona lo dice; o null")
    hora: str | None = Field(default=None, description="Una hora que dice (HH:MM), si la acción la pide; o null")
    falta: str | None = Field(
        default=None, description="Si no entiendes una parte o falta un dato, una frase corta que lo diga; o null")


__all__ = ["CambioInterpretado", "LineaInterpretada", "cambio_no_entendido"]
