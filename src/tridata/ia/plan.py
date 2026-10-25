"""Order one section of the plan's .md with the Claude API (unit 040, R5): one call per section.

`plan/importar.py` cuts the .md by its sections and asks here for each one: which part of the plan it is and its
rows, each one ONE line of text with its fields separated by «|» (the kind first). The schema is flat — a text with
its list of values and a `list[str]`, no list of objects and no null — because a strict schema with lists of objects
is what the API refuses with «compiled grammar is too large» (bug 036). The code reads the rows and decides; whatever
it cannot read goes to the notes of the plan.

Only the text of that section and today's date leave the PC (G-53: the plan goes out to the AI to be ordered). The
client is created lazily and can be injected: the tests use a double and never call the API. Failures are
`ErrorPlan` with a sentence for the screen; nothing here logs a text of the plan (R9).
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field

MODELO_POR_DEFECTO = "claude-haiku-5-5"
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

PARTES = ("objetivos", "semanas_hechas", "previsto", "sesiones_clave", "nutricion", "incidencias", "comparativa",
          "nota")

INSTRUCCION = """\
Ordenas UN apartado del plan de entrenamiento de un corredor (un .md). Recibes la fecha de hoy, el título del \
apartado y su texto. Di qué parte del plan es y copia su contenido en filas, sin inventar nada.

parte: "objetivos" (carreras y objetivos), "semanas_hechas" (semanas ya entrenadas con lo hecho), "previsto" \
(semanas que vienen), "sesiones_clave" (las sesiones importantes por bloques), "nutricion" (nutrición en carrera), \
"incidencias" (molestias, lesiones, enfermedades), "comparativa" (comparación con otro año) o "nota" (no es ninguna).

filas: una fila por cosa, en una sola línea, con sus campos separados por «|» y el tipo primero. Fechas AAAA-MM-DD \
(con la fecha de hoy para el año que falte). Tiempos como H:MM. Kilómetros con coma decimal. Un campo que no está \
va vacío. Los tipos de fila:
- carrera|nombre|fecha|objetivo|estado o resultado (p. ej. «✅ 38:10» o «pendiente»)|principal (sí o no)
- semana|número|desde|hasta|tiempo previsto|sesiones clave en pocas palabras|tiempo en movimiento hecho|km hechos
- sesion|fecha|título|bloques separados por «;» (p. ej. «20' a 5:30; 4×1000 a 4:00 (2' rec)»)|clave (sí o no)
- nutricion|producto (con sus hidratos, sodio o cafeína)|pauta (por minuto o km)
- incidencia|fecha|texto
- comparativa|texto
- nota|texto (una línea que no es ninguno de los tipos de arriba)
Un apartado puede traer filas de varios tipos (p. ej. semanas y sus sesiones). Si el apartado no es ninguna parte, \
parte "nota" y filas vacías: se guarda tal cual.
"""


def _todo_requerido(esquema: dict[str, Any]) -> None:
    esquema["required"] = list(esquema.get("properties", {}))


class ApartadoOrdenado(BaseModel):
    """One section ordered: its part and its rows of text (flat, bug 036)."""

    model_config = ConfigDict(json_schema_extra=_todo_requerido)

    parte: Literal["objetivos", "semanas_hechas", "previsto", "sesiones_clave", "nutricion", "incidencias",
                   "comparativa", "nota"] = Field(description="Qué parte del plan es este apartado")
    filas: list[str] = Field(description="Una fila por cosa: el tipo primero y sus campos separados por «|»")


def esquema_enviado() -> dict[str, Any]:
    """The schema the SDK actually sends for `ApartadoOrdenado` (it must fit the API's grammar limit, bug 036)."""
    from anthropic.lib._parse._transform import transform_schema

    return transform_schema(ApartadoOrdenado.model_json_schema())


class ErrorPlan(Exception):
    """A section could not be ordered (no key, no network, a limit, an answer that is not usable…)."""


def construir_peticion(titulo: str, texto: str, hoy: date, modelo: str) -> dict[str, Any]:
    return {
        "model": modelo,
        "max_tokens": 16000,
        "system": INSTRUCCION,
        "messages": [{"role": "user", "content": (
            f"Hoy es {_DIAS[hoy.weekday()]}, {hoy.isoformat()}.\n\nApartado: «{titulo or 'inicio del plan'}»\n\n"
            f"{texto}")}],
        "output_format": ApartadoOrdenado,
        "output_config": {"effort": "low"},
    }


def _uso(usage: Any) -> dict[str, int]:
    return {"entrada": int(getattr(usage, "input_tokens", 0) or 0),
            "salida": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_escritura": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            "cache_lectura": int(getattr(usage, "cache_read_input_tokens", 0) or 0)}


class OrdenadorPlan:
    def __init__(self, cliente: Any = None, modelo: str | None = None) -> None:
        self._cliente = cliente
        self.modelo = modelo or os.environ.get("TRIDATA_MODELO_TEXTO") or MODELO_POR_DEFECTO

    def ordenar(self, titulo: str, texto: str, hoy: date) -> tuple[ApartadoOrdenado, dict[str, int], str]:
        """`(apartado, uso, modelo)` of one section. Raises `ErrorPlan`, without any text of the plan."""
        try:
            if self._cliente is None:
                self._cliente = anthropic.Anthropic(timeout=60.0)
            respuesta = self._cliente.messages.parse(**construir_peticion(titulo, texto, hoy, self.modelo))
        except anthropic.AuthenticationError as exc:
            raise ErrorPlan("la clave de la API no es válida") from exc
        except anthropic.RateLimitError as exc:
            raise ErrorPlan("se ha alcanzado el límite de uso de la API") from exc
        except anthropic.APIStatusError as exc:
            raise ErrorPlan(f"la API ha respondido con un error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise ErrorPlan("no hay conexión con la API") from exc
        except anthropic.AnthropicError as exc:
            raise ErrorPlan("error del cliente de la API") from exc
        except (TypeError, ValueError) as exc:  # no key, or an answer that does not validate: never its contents
            raise ErrorPlan("falta la clave de la API o la respuesta no es válida") from exc
        apartado = respuesta.parsed_output
        if respuesta.stop_reason != "end_turn" or apartado is None:
            raise ErrorPlan("la respuesta de la API no se puede usar")
        return apartado, _uso(respuesta.usage), self.modelo


__all__ = ["INSTRUCCION", "MODELO_POR_DEFECTO", "PARTES", "ApartadoOrdenado", "ErrorPlan", "OrdenadorPlan",
           "construir_peticion", "esquema_enviado"]
