"""One turn of a conversation of the Asistente with the Claude API (unit 032, REC-20), answered as it arrives.

The request is ordered for the cache (P3): `tools` and `system` are the fixed part — the instructions with the
catalogue of actions, the medical transcription and the data of the day, built on this PC by
`conversaciones.contexto`, without the hour — with ONE cache mark at the end of `system`; then what was said in that
conversation and the new message. Two messages in a row without a change in the data read the fixed part from the
cache (`usage.cache_read_input_tokens`).

The model is the one Andrés chose on 2026-10-23: Haiku 5.5 by default (`TRIDATA_MODELO_ASISTENTE` in .env changes
it) and, for one message with «pensar más», Sonnet 5.5. Both count for the same cap (R9, done by the web).

Fixed tools, always the same (so the prefix never changes): `proponer_cambio`, whose input is a `CambioInterpretado` of
unit 021 (the code turns it into the usual card; nothing changes until Andrés confirms, R-96), and `poner_titulo`,
the title of a conversation that starts. When Claude calls them the turn goes on with their results (the manual
loop of the SDK, at most `RONDAS` requests) and the text of every round is one answer.

Unit 041 (REC-23): the team. A request carries, AFTER the cache mark, a small block with the role that answers
(`conversaciones.papeles.bloque`), and the model of that role (`modelo`); the fixed part stays the same for the four
roles, so they share the cache (R2). A third fixed tool, `abrir_actividad`, gives the laps of one activity by its date
or its id (R4): the laps never go in the fixed part (S-54). Its schema is flat, two texts (bug 036).

The client is created lazily and can be injected: the tests use a double and never call the API. Failures are
`ErrorConversacion` with a sentence for the screen, as `ErrorInterpretacion`. Nothing here logs a text.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import anthropic

from .claude import CambioInterpretado

log = logging.getLogger(__name__)

MODELO_POR_DEFECTO = "claude-haiku-5-5"
MODELO_PENSAR = "claude-sonnet-5-5"
MAX_TOKENS = 16000
RONDAS = 4  # a turn with tools: the first request and, at most, three more with their results
ESFUERZO = {False: "low", True: "medium"}  # chat answers do well at low; «pensar más» thinks a bit more

HERRAMIENTAS: list[dict[str, Any]] = [
    {
        "name": "proponer_cambio",
        "description": (
            "Propone UN cambio en sus datos (lo apuntado, los pasos o el objetivo de un día, un ajuste con su fecha, "
            "una carrera, el peso de un día, una receta o un ingrediente) usando una de las acciones de la lista de "
            "cambios que sabes proponer. La app enseña una tarjeta «Voy a …» y Andrés la confirma o la descarta: "
            "nada cambia hasta entonces. Úsala solo si Andrés pide un cambio o si le propones uno concreto; un cambio "
            "por llamada."),
        "input_schema": CambioInterpretado.model_json_schema(),
    },
    {
        "name": "poner_titulo",
        "description": ("Pone el título de una conversación que empieza: de 2 a 6 palabras, en español, con el tema "
                        "(p. ej. «10 km el 3 de diciembre», «Peso de noviembre»). Solo en el primer mensaje."),
        "input_schema": {"type": "object", "properties": {"titulo": {"type": "string"}}, "required": ["titulo"],
                         "additionalProperties": False},
    },
    {
        "name": "abrir_actividad",
        "description": ("Abre un entreno hecho para ver sus vueltas una a una (distancia, tiempo, ritmo, pulso, "
                        "cadencia y desnivel de cada vuelta) y su clima. Por su fecha (todas las de ese día) o por su "
                        "id. Úsala cuando necesites el detalle de una sesión; el contexto solo trae su resumen."),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "El día del entreno (AAAA-MM-DD), o \"\" si das su id"},
                "id": {"type": "string", "description": "El id del entreno, o \"\" si das su fecha"},
            },
            "required": ["fecha", "id"],
            "additionalProperties": False,
        },
    },
]


class ErrorConversacion(Exception):
    """Claude did not answer (no key, no network, a limit, a refusal…): the message stays «sin respuesta».

    `modelo` and `usos` (one per call already charged, the cut one included if its use was seen) say what was paid
    anyway, so the spending cap counts it (bug 038). No call answered: `usos` is empty."""

    def __init__(self, mensaje: str, modelo: str | None = None, usos: tuple[dict[str, int], ...] = ()) -> None:
        super().__init__(mensaje)
        self.modelo = modelo
        self.usos = usos


@dataclass(frozen=True)
class Respuesta:
    """The whole answer of one turn: its text, the model, the token use of every round and whether it was cut."""

    texto: str
    modelo: str
    uso: dict[str, int]
    cortada: bool = False
    usos: tuple[dict[str, int], ...] = ()  # the use of each call: the price goes per call (bug 038)


def construir_peticion(bloques: Sequence[str], historial: list[dict[str, Any]], mensaje: str, modelo: str, *,
                       pensar: bool = False, papel: str | None = None) -> dict[str, Any]:
    """The request: the fixed part (`bloques`, the last one with the cache mark), the role that answers after the
    mark (unit 041, R2), what was said and the message."""
    system: list[dict[str, Any]] = [{"type": "text", "text": b} for b in bloques]
    if system:
        system[-1]["cache_control"] = {"type": "ephemeral"}
    if papel:
        system.append({"type": "text", "text": papel})
    return {
        "model": modelo,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "tools": HERRAMIENTAS,
        "messages": [*historial, {"role": "user", "content": mensaje}],
        "output_config": {"effort": ESFUERZO[pensar]},
    }


def _uso(usage: Any) -> dict[str, int]:
    return {"entrada": int(getattr(usage, "input_tokens", 0) or 0),
            "salida": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_escritura": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            "cache_lectura": int(getattr(usage, "cache_read_input_tokens", 0) or 0)}


def _sumar(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    return {k: a.get(k, 0) + b.get(k, 0) for k in ("entrada", "salida", "cache_escritura", "cache_lectura")}


def _pagado(usos: list[dict[str, int]], flujo: Any) -> tuple[dict[str, int], ...]:
    """The calls already charged plus, if a round broke on the way, the use the SDK had seen of it (bug 038)."""
    try:
        parcial = _uso(flujo.current_message_snapshot.usage) if flujo is not None else None
    except Exception:  # noqa: BLE001 - no snapshot yet (it broke before `message_start`): nothing seen, nothing added
        parcial = None
    return tuple(usos) + ((parcial,) if parcial and any(parcial.values()) else ())


class ConversadorClaude:
    def __init__(self, cliente: Any = None, modelo: str | None = None, modelo_pensar: str = MODELO_PENSAR) -> None:
        self._cliente = cliente
        self.modelo = modelo or os.environ.get("TRIDATA_MODELO_ASISTENTE") or MODELO_POR_DEFECTO
        self.modelo_pensar = modelo_pensar

    def modelo_para(self, pensar: bool) -> str:
        return self.modelo_pensar if pensar else self.modelo

    def responder(self, bloques: Sequence[str], historial: list[dict[str, Any]], mensaje: str, *,
                  pensar: bool = False,
                  al_usar: Callable[[str, dict[str, Any]], str] | None = None,
                  modelo: str | None = None, papel: str | None = None) -> Iterator[str | Respuesta]:
        """Yields each piece of text as it arrives and, last, the `Respuesta`. Raises `ErrorConversacion`.

        `al_usar(nombre, entrada)` runs each tool Claude calls and returns the text of its result. `modelo` is the
        one of the role that answers (unit 041; «pensar más» is still `modelo_pensar`) and `papel` its block."""
        modelo = self.modelo_pensar if pensar else (modelo or self.modelo)
        peticion = construir_peticion(bloques, historial, mensaje, modelo, pensar=pensar, papel=papel)
        uso = {"entrada": 0, "salida": 0, "cache_escritura": 0, "cache_lectura": 0}
        usos: list[dict[str, int]] = []
        flujo: Any = None  # the round on the way: if it breaks, what the SDK saw of its use is paid too
        dicho = ""  # what Andrés has seen arrive, every round; the answer is this
        cortada = True  # too many rounds: what was said is kept
        try:
            if self._cliente is None:
                self._cliente = anthropic.Anthropic(timeout=60.0)
            for _ in range(RONDAS):
                primero = True
                with self._cliente.messages.stream(**peticion) as flujo:
                    for trozo in flujo.text_stream:
                        if not trozo:
                            continue
                        if primero and dicho and not dicho[-1].isspace() and not trozo[0].isspace():
                            trozo = " " + trozo  # the text of a new round never glues to the one before
                        primero = False
                        dicho += trozo
                        yield trozo
                    final = flujo.get_final_message()
                flujo = None
                usos.append(_uso(final.usage))
                uso = _sumar(uso, usos[-1])
                if final.stop_reason == "refusal":
                    raise ErrorConversacion("el asistente no ha querido responder a eso", modelo, tuple(usos))
                llamadas = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
                if final.stop_reason != "tool_use" or not llamadas:
                    cortada = final.stop_reason == "max_tokens"
                    break
                resultados = []
                for llamada in llamadas:
                    entrada = llamada.input if isinstance(llamada.input, dict) else {}
                    texto = al_usar(llamada.name, entrada) if al_usar is not None else "Hecho."
                    resultados.append({"type": "tool_result", "tool_use_id": llamada.id, "content": texto})
                peticion = {**peticion, "messages": [*peticion["messages"],
                                                     {"role": "assistant", "content": final.content},
                                                     {"role": "user", "content": resultados}]}
        except ErrorConversacion:
            raise
        except anthropic.AuthenticationError as exc:
            raise ErrorConversacion("la clave de la API no es válida", modelo, _pagado(usos, flujo)) from exc
        except anthropic.RateLimitError as exc:
            raise ErrorConversacion("se ha alcanzado el límite de uso de la API", modelo, _pagado(usos, flujo)) from exc
        except anthropic.APIStatusError as exc:
            raise ErrorConversacion(f"la API ha respondido con un error {exc.status_code}", modelo,
                                    _pagado(usos, flujo)) from exc
        except anthropic.APIConnectionError as exc:
            raise ErrorConversacion("no hay conexión con la API", modelo, _pagado(usos, flujo)) from exc
        except anthropic.AnthropicError as exc:
            raise ErrorConversacion("error del cliente de la API", modelo, _pagado(usos, flujo)) from exc
        except (TypeError, ValueError) as exc:
            # The SDK raises TypeError when no API key is configured. Logged without any text of the conversation.
            log.warning("conversation turn failed: %s", type(exc).__name__)
            raise ErrorConversacion("falta la clave de la API o la respuesta no es válida", modelo,
                                    _pagado(usos, flujo)) from exc
        yield Respuesta(texto=dicho.strip(), modelo=modelo, uso=uso, cortada=cortada, usos=tuple(usos))


__all__ = ["HERRAMIENTAS", "MODELO_PENSAR", "MODELO_POR_DEFECTO", "ConversadorClaude", "ErrorConversacion",
           "Respuesta", "construir_peticion"]
