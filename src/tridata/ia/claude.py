"""Turn what Andrés writes into its pieces with the Claude API, in ONE request.

The pieces (unit 007): foods (as before), the weight of a day, the objective of the day and the
doubtful phrases that fit nowhere. Only the written text, today's date and a fixed instruction
leave the PC (R-12, C-12): never saved weights, the mean, the basal, the profile or any target.
Unit 011: when there are saved recipes or fixed foods, their NAMES and aliases go too (R-44) and
Claude says, for each food, which one it is (`guardado`) and whether it is `exacta` or `parecida`;
their kcal are put by the app, never by Claude. With nothing saved the request is as before. The client is created lazily and
can be injected, so tests use a double and never call the API.
Unit 021 (the assistant that acts, G-38): one more piece, `cambios`, in the SAME request. For it the request also
carries the NAMES of what is logged on the day of the request (`apuntado(dia)`, never quantities or kcal) and the
fixed list of the actions the assistant knows (`acciones()`, written by `nutricion.asistente`), both only when there
are any. `CambioInterpretado` is generic so the next actions never touch this module; the code decides, Claude only
understands.
"""
from __future__ import annotations

import logging
import os
from datetime import date
from collections.abc import Callable
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field, model_validator

log = logging.getLogger(__name__)

MODELO_POR_DEFECTO = "claude-haiku-5-5"
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

INSTRUCCION = """\
Eres el intérprete de un diario de comidas y de peso. Recibes la fecha de hoy y un texto que la \
persona ha escrito o dictado: lo que ha comido o piensa comer, su peso, o el mensaje con el que \
abre el día («nuevo día …»). Separa el texto en piezas: alimentos, peso, objetivo_dia y dudosas. \
Un texto puede traer una sola pieza o varias; lo que no aparezca va vacío (lista vacía o null).

Alimentos: la lista de alimentos que menciona, uno por elemento, siguiendo estas reglas:

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
5. Recetas y alimentos guardados: puede venir una lista con los nombres y alias de lo que la \
persona tiene guardado (solo los nombres). Si un alimento es uno de esa lista, pon en guardado ese \
nombre o alias tal cual aparece en la lista y en coincidencia "exacta" si lo dice por ese nombre o \
alias (aunque cambie el plural o las mayúsculas), o "parecida" si solo se le parece (p. ej. dice \
«garbanzos» y está guardado «garbanzos de la abuela»). Si no se parece a ninguno, o no hay lista, \
guardado y coincidencia van a null; no uses nombres que no estén en la lista. Una fracción de una \
receta guardada («un cuarto de …», «media …», «la mitad de …») va con la unidad "receta" y la \
cantidad en decimales (un cuarto = 0.25, media = 0.5). Kcal estimadas: estima siempre las kcal de \
la cantidad indicada con un valor promedio, sin preguntar nada, y marca estimado = true, también \
con guardado (las de lo guardado las pone la aplicación). Solo si la persona da ella misma las \
kcal (p. ej. «35 kcal cada 100 ml») calcúlalas con ese dato y marca estimado = false.
6. Dudoso: si una palabra o parte del texto no la entiendes o no es un alimento reconocible \
(p. ej. «kfjd»), inclúyela tal cual como nombre con dudoso = true y kcal = null. No adivines.
7. Macros (proteína, hidratos, grasa en gramos): solo si los conoces con razonable confianza; \
si no, null.
8. Supuestos: frases cortas con lo que has supuesto para calcular (p. ej. «plátano mediano de \
120 g», «lentejas cocidas»). Lista vacía si no hay.
9. No añadas alimentos que no se mencionen, no repitas un alimento corregido y no hagas \
preguntas. Nombres en español, en minúsculas y breves.

Las otras piezas:

10. peso: si dice su peso («peso 70,2», «hoy peso 70,2», «ayer pesaba 70,5»), pon los kg (con \
decimales, la coma es decimal), el día (como en la regla 2; sin día, es hoy) y una nota con lo \
que añada sobre ese peso (p. ej. «retención tras la cena»), o null si no añade nada. Un número \
suelto justo después de «nuevo día» es el peso de hoy. Si dice varios pesos, vale el último. Sin \
peso, peso = null. El peso nunca es un alimento.
11. objetivo_dia: solo si abre el día («nuevo día», «hoy toca …») o dice cómo quiere el día. \
tipo: el tipo de día en una o dos palabras, en minúsculas (p. ej. «descanso», «entreno», \
«carrera»). porcentaje: el % de déficit o superávit que pide, con signo (un déficit, como \
«menos 15» o «déficit del 15», va en negativo; un superávit, en positivo); null si no dice \
ningún %. niveles: \
los % de margen que quiere ver ese día, con signo (p. ej. «niveles menos 15 y 0»), o null si no \
los dice. entreno: el entreno previsto tal como lo dice, breve (p. ej. «30 min suaves»), o null. \
pasos: los pasos previstos como número entero («8.000 pasos» es 8000), o null. Vale lo último \
dicho. Si no dice nada de esto, objetivo_dia = null.
12. «nuevo día», «buenos días» y palabras parecidas no son alimentos ni dudosas: solo abren el \
día.
13. dudosas: frases del texto que no son un alimento, ni el peso, ni parte del objetivo del día, \
y que no sabes dónde poner (p. ej. «y lo del martes ya sabes»). Cópialas tal cual, una por \
elemento; no las conviertas en alimentos. Una palabra suelta que parece un alimento pero no \
entiendes sigue la regla 6. Lista vacía si no hay.
14. cambios: si pide cambiar, quitar o corregir algo que ya existe (lo apuntado, los pasos previstos de un \
día, las kcal de un ingrediente…) o guardar un ingrediente con sus kcal, va a cambios, no a alimentos ni a \
objetivo_dia. Puede venir la lista de los cambios que sabes proponer, con qué campos usa cada uno, y los \
nombres de lo apuntado ese día: en accion pon uno de esa lista; si pide un cambio que no está en ella, \
accion = "otra" y en texto lo que pide. objeto: lo que nombra, tal cual lo dice o como aparece en lo apuntado \
o en lo guardado. dia: el día del que habla (como en la regla 2), o null si no dice ninguno. No pongas kcal, \
cantidades ni valores de antes que la persona no haya dicho. No dejes nada a medias: si no entiendes una parte del \
cambio o falta un dato (qué cambiar, qué poner, cuánto), pon en falta una frase corta para la persona que \
diga qué no sabes (p. ej. «No sé qué quieres cambiar a …» o «No sé qué poner en lugar del yogur») y no \
adivines lo demás. Una sustitución («quita X, al final fue Y») es un solo cambio, no dos. Cada cambio \
independiente va en su elemento. Sin cambios, lista vacía.
"""


def _todo_requerido(esquema: dict[str, Any]) -> None:
    """Claude must always say every piece (null or an empty list when absent), never skip one."""
    esquema["required"] = list(esquema.get("properties", {}))


class AlimentoInterpretado(BaseModel):
    model_config = ConfigDict(json_schema_extra=_todo_requerido)

    nombre: str = Field(description="Nombre breve del alimento, o la palabra no entendida si es dudoso")
    cantidad: float | None = Field(description="Cantidad numérica; null si no se dijo")
    unidad: str | None = Field(
        description='"g", "ml", "unidad" o "receta" (fracción de una receta guardada); null si no hay cantidad')
    dia: date = Field(description="Día al que pertenece (AAAA-MM-DD)")
    kcal: float | None = Field(description="Kcal de esa cantidad; null si es dudoso o falta la cantidad")
    proteina_g: float | None = Field(description="Proteína en gramos, o null")
    hidratos_g: float | None = Field(description="Hidratos de carbono en gramos, o null")
    grasa_g: float | None = Field(description="Grasa en gramos, o null")
    estimado: bool = Field(description="true si las kcal son una estimación promedio")
    dudoso: bool = Field(description="true si no se ha entendido esta parte del texto")
    supuestos: list[str] = Field(description="Supuestos usados para calcular")
    guardado: str | None = Field(
        default=None, description="Nombre o alias de la lista de guardados al que corresponde, tal cual; o null")
    coincidencia: Literal["exacta", "parecida"] | None = Field(
        default=None, description='"exacta", "parecida" o null si no corresponde a ningún guardado')


class PesoInterpretado(BaseModel):
    kg: float = Field(description="Peso en kilos (p. ej. 70.2)")
    dia: date = Field(description="Día de ese peso (AAAA-MM-DD)")
    nota: str | None = Field(description="Lo que dice sobre ese peso, o null")


class ObjetivoDiaInterpretado(BaseModel):
    tipo: str | None = Field(description="Tipo de día en minúsculas (descanso, entreno…), o null")
    porcentaje: float | None = Field(description="% pedido con signo (déficit negativo), o null")
    niveles: list[float] | None = Field(description="% de margen que quiere ver, con signo, o null")
    entreno: str | None = Field(description="Entreno previsto tal como lo dice, breve, o null")
    pasos: int | None = Field(description="Pasos previstos, o null")


class LineaInterpretada(BaseModel):
    """One line of a change that brings several (an ingredient of a recipe, unit 023)."""

    model_config = ConfigDict(json_schema_extra=_todo_requerido)

    nombre: str = Field(description="Nombre breve, tal cual lo dice")
    cantidad: float | None = Field(description="Cantidad dicha, o null")
    unidad: str | None = Field(description='"g", "ml" o "unidad", o null')
    kcal_estimadas: float | None = Field(description="Kcal estimadas solo si es algo nuevo, no guardado; o null")


def _cambio_compacto(esquema: dict[str, Any]) -> None:
    """Bug 036: the schema SENT for a change, small enough for the API's grammar limit.

    Texts and dates travel as a plain string («» = none) instead of string-or-null, and each line as one text
    «cantidad|unidad|nombre|kcal» instead of a nested object; `_desde_lo_enviado` turns them back on the way in.
    `valores` travels as one text «-10;-20» too; plain numbers keep their null.
    """
    _todo_requerido(esquema)
    for nombre, propiedad in esquema.get("properties", {}).items():
        ramas = propiedad.get("anyOf")
        if not ramas:
            continue
        otra = next(r for r in ramas if r != {"type": "null"})
        if nombre == "lineas":
            del propiedad["anyOf"]
            propiedad.update(type="array", items={"type": "string"}, description=(
                "Líneas, si la acción las pide, cada una «cantidad|unidad|nombre|kcal» (vacío lo que no se sabe, "
                "p. ej. «100|g|arroz|»); lista vacía si no"))
        elif nombre == "valores":
            del propiedad["anyOf"]
            propiedad.update(type="string", description="Varios valores nuevos, si los dice, separados por «;» "
                                                        "(p. ej. «-10;-20»); vacío si no")
        elif otra.get("type") == "string":
            del propiedad["anyOf"]
            propiedad["type"] = "string"
            propiedad["description"] = propiedad.get("description", "").replace("o null", "o vacío")


def _numero_enviado(texto: str) -> float | None:
    """A number Claude wrote as text («1,5» too); anything else is none, so one bad piece never sinks the answer."""
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


class CambioInterpretado(BaseModel):
    """One change Andrés asks for (unit 021, G-37). Generic: the meaning of each field is in the list of actions."""

    model_config = ConfigDict(json_schema_extra=_cambio_compacto)

    @model_validator(mode="before")
    @classmethod
    def _desde_lo_enviado(cls, datos: Any) -> Any:
        """«» and [] mean none; a line given as text becomes a LineaInterpretada (bug 036)."""
        if not isinstance(datos, dict):
            return datos
        datos = {k: (None if v == "" or v == [] else v) for k, v in datos.items()}
        if isinstance(datos.get("lineas"), list):
            datos["lineas"] = [_linea_enviada(x) if isinstance(x, str) else x for x in datos["lineas"]]
        if isinstance(datos.get("valores"), str):
            numeros = (_numero_enviado(v) for v in datos["valores"].split(";"))
            datos["valores"] = [n for n in numeros if n is not None] or None
        return datos

    accion: str = Field(description='Id de la lista de cambios que sabes proponer, u "otra"')
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
    falta: str | None = Field(
        default=None, description="Si no entiendes una parte o falta un dato, una frase corta que lo diga; o null")


def _todo_requerido_menos_cambios(esquema: dict[str, Any]) -> None:
    """Every piece is required, as before unit 021; `cambios` may be left out (it then means none)."""
    esquema["required"] = [p for p in esquema.get("properties", {}) if p != "cambios"]


class ListaInterpretada(BaseModel):
    """The whole interpretation of one text: every piece found in it."""

    model_config = ConfigDict(json_schema_extra=_todo_requerido_menos_cambios)

    alimentos: list[AlimentoInterpretado]
    peso: PesoInterpretado | None = Field(default=None, description="Peso dicho en el texto, o null")
    objetivo_dia: ObjetivoDiaInterpretado | None = Field(
        default=None, description="Objetivo del día dicho al abrir el día, o null")
    dudosas: list[str] = Field(default_factory=list, description="Frases que no sabes dónde poner")
    cambios: list[CambioInterpretado] = Field(
        default_factory=list, description="Cambios de algo que ya existe que pide (regla 14); lista vacía si no hay")


Interpretacion = ListaInterpretada  # the name the web uses: one interpretation, all its pieces


def esquema_enviado() -> dict[str, Any]:
    """The schema the SDK actually sends for `ListaInterpretada` (bug 036: it must fit the API's grammar limit)."""
    from anthropic.lib._parse._transform import transform_schema

    return transform_schema(ListaInterpretada.model_json_schema())


class ErrorInterpretacion(Exception):
    """Claude could not interpret the text (no key, no network, rate limit, refusal…)."""


def construir_peticion(texto: str, hoy: date, modelo: str, guardados: list[str] | None = None,
                       apuntado: list[str] | None = None, acciones: str | None = None) -> dict[str, Any]:
    """The whole request: the fixed instruction, today's date, the saved NAMES (if any), the NAMES logged that
    day (if any, unit 021), the fixed list of actions (if given) and the text."""
    lista = ""
    if guardados:
        lista = "Recetas y alimentos guardados (solo nombres y alias): " + ", ".join(f"«{n}»" for n in guardados) + "\n\n"
    if apuntado:
        lista += "Lo apuntado ese día (solo nombres): " + ", ".join(f"«{n}»" for n in apuntado) + "\n\n"
    if acciones:
        lista += acciones.strip() + "\n\n"
    return {
        "model": modelo,
        "max_tokens": 16000,
        "system": INSTRUCCION,
        "messages": [{
            "role": "user",
            "content": f"Hoy es {_DIAS[hoy.weekday()]}, {hoy.isoformat()}.\n\n{lista}Lo que ha escrito:\n{texto}",
        }],
        "output_format": ListaInterpretada,
        "output_config": {"effort": "low"},
    }


class InterpreteClaude:
    def __init__(self, cliente: Any = None, modelo: str | None = None,
                 guardados: Callable[[], list[str]] | None = None,
                 apuntado: Callable[[date], list[str]] | None = None,
                 acciones: Callable[[], str] | None = None) -> None:
        """`guardados()` gives the names and aliases saved right now (`Recetario.nombres`), read on every call.

        Unit 021: `apuntado(dia)` the NAMES logged on the day of the request and `acciones()` the fixed list of what
        the assistant knows (`nutricion.asistente`). Both are public attributes, so they can also be set later.
        """
        self._cliente = cliente
        self.modelo = modelo or os.environ.get("TRIDATA_MODELO_TEXTO") or MODELO_POR_DEFECTO
        self._guardados = guardados
        self.apuntado = apuntado
        self.acciones = acciones

    def interpretar(self, texto: str, hoy: date) -> ListaInterpretada:
        try:
            if self._cliente is None:
                self._cliente = anthropic.Anthropic(timeout=30.0)
            nombres = self._guardados() if self._guardados is not None else None
            apuntado = getattr(self, "apuntado", None)
            acciones = getattr(self, "acciones", None)
            respuesta = self._cliente.messages.parse(**construir_peticion(
                texto, hoy, self.modelo, nombres, apuntado(hoy) if apuntado is not None else None,
                acciones() if acciones is not None else None))
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
        return lista
