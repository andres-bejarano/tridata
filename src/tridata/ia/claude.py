"""Turn a written meal into a list of foods with the Claude API.

Only the written text, today's date and a fixed instruction leave the PC (R-12, C-12):
never weights, previous days, totals or anything saved. The client is created lazily and
can be injected, so tests use a double and never call the API.
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

import anthropic
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

MODELO_POR_DEFECTO = "claude-haiku-5-5"
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

INSTRUCCION = """\
Eres el intérprete de un diario de comidas. Recibes la fecha de hoy y un texto que la persona \
ha escrito o dictado sobre lo que ha comido o piensa comer. Devuelve la lista de alimentos que \
menciona, uno por elemento, siguiendo estas reglas:

1. Vale lo último: si se corrige dentro del texto (cantidad, alimento o día, p. ej. «37 gramos \
de avena… no, 33 de avena»), quédate solo con lo último que dijo. Lo corregido no aparece.
2. Día: usa la fecha de hoy para resolver «hoy», «mañana», «pasado mañana», «ayer» o un día de \
la semana («el sábado» es el próximo sábado; si hoy es sábado, es hoy). Sin día, es hoy. Formato \
AAAA-MM-DD.
3. Cantidad y unidad: si dice un peso o un volumen, usa "g" o "ml" (convierte kilos y litros). \
Si cuenta piezas («un plátano», «dos huevos», «una tostada»), la cantidad es ese número y la \
unidad "unidad". Para medidas vagas («un trozo», «un poco», «un puñado») estima una cantidad \
típica, márcala como estimado y explícalo en supuestos.
4. Sin cantidad: si no dice ninguna cantidad (p. ej. «leche en el café»), pon cantidad, unidad y \
kcal a null. No la inventes: queda pendiente de cantidad.
5. Kcal estimadas: no hay recetas ni alimentos guardados, así que estima las kcal de la cantidad \
indicada con un valor promedio, sin preguntar nada, y marca estimado = true. Solo si la persona \
da ella misma las kcal (p. ej. «35 kcal cada 100 ml») calcúlalas con ese dato y marca \
estimado = false.
6. Dudoso: si una palabra o parte del texto no la entiendes o no es un alimento reconocible \
(p. ej. «kfjd»), inclúyela tal cual como nombre con dudoso = true y kcal = null. No adivines.
7. Macros (proteína, hidratos, grasa en gramos): solo si los conoces con razonable confianza; \
si no, null.
8. Supuestos: frases cortas con lo que has supuesto para calcular (p. ej. «plátano mediano de \
120 g», «lentejas cocidas»). Lista vacía si no hay.
9. No añadas alimentos que no se mencionen, no repitas un alimento corregido y no hagas \
preguntas. Nombres en español, en minúsculas y breves.
"""


class AlimentoInterpretado(BaseModel):
    nombre: str = Field(description="Nombre breve del alimento, o la palabra no entendida si es dudoso")
    cantidad: float | None = Field(description="Cantidad numérica; null si no se dijo")
    unidad: str | None = Field(description='"g", "ml" o "unidad"; null si no hay cantidad')
    dia: date = Field(description="Día al que pertenece (AAAA-MM-DD)")
    kcal: float | None = Field(description="Kcal de esa cantidad; null si es dudoso o falta la cantidad")
    proteina_g: float | None = Field(description="Proteína en gramos, o null")
    hidratos_g: float | None = Field(description="Hidratos de carbono en gramos, o null")
    grasa_g: float | None = Field(description="Grasa en gramos, o null")
    estimado: bool = Field(description="true si las kcal son una estimación promedio")
    dudoso: bool = Field(description="true si no se ha entendido esta parte del texto")
    supuestos: list[str] = Field(description="Supuestos usados para calcular")


class ListaInterpretada(BaseModel):
    alimentos: list[AlimentoInterpretado]


class ErrorInterpretacion(Exception):
    """Claude could not interpret the text (no key, no network, rate limit, refusal…)."""


def construir_peticion(texto: str, hoy: date, modelo: str) -> dict[str, Any]:
    """The whole request: the fixed instruction, today's date and the text. Nothing else."""
    return {
        "model": modelo,
        "max_tokens": 16000,
        "system": INSTRUCCION,
        "messages": [{
            "role": "user",
            "content": f"Hoy es {_DIAS[hoy.weekday()]}, {hoy.isoformat()}.\n\nLo que ha escrito:\n{texto}",
        }],
        "output_format": ListaInterpretada,
        "output_config": {"effort": "low"},
    }


class InterpreteClaude:
    def __init__(self, cliente: Any = None, modelo: str | None = None) -> None:
        self._cliente = cliente
        self.modelo = modelo or os.environ.get("TRIDATA_MODELO_TEXTO") or MODELO_POR_DEFECTO

    def interpretar(self, texto: str, hoy: date) -> list[AlimentoInterpretado]:
        try:
            if self._cliente is None:
                self._cliente = anthropic.Anthropic(timeout=30.0)
            respuesta = self._cliente.messages.parse(**construir_peticion(texto, hoy, self.modelo))
        except anthropic.AuthenticationError as exc:
            raise ErrorInterpretacion("la clave de la API no es válida") from exc
        except anthropic.RateLimitError as exc:
            raise ErrorInterpretacion("se ha alcanzado el límite de uso de la API") from exc
        except anthropic.APIStatusError as exc:
            raise ErrorInterpretacion(f"la API ha respondido con un error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise ErrorInterpretacion("no hay conexión con la API") from exc
        except anthropic.AnthropicError as exc:
            raise ErrorInterpretacion("error del cliente de la API") from exc
        except (TypeError, ValueError) as exc:
            # The SDK raises TypeError when no API key is configured, and the structured
            # output is validated with pydantic (ValueError). Logged so a real bug still shows.
            log.exception("Claude interpretation failed")
            raise ErrorInterpretacion("falta la clave de la API o la respuesta no es válida") from exc

        lista = respuesta.parsed_output
        if respuesta.stop_reason != "end_turn" or lista is None:
            raise ErrorInterpretacion(f"respuesta no utilizable (stop_reason={respuesta.stop_reason})")
        for alimento in lista.alimentos:
            if alimento.dudoso:  # a doubtful item never adds kcal (R-7)
                alimento.kcal = alimento.proteina_g = alimento.hidratos_g = alimento.grasa_g = None
        return list(lista.alimentos)
