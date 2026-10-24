"""What the assistant does not change, and where Andrés does it in the app (unit 021, R-89, G-43).

Each one is in the list for Claude so it can name it; proposing any of them is a `no` that says where it is done,
and never a card. `otra` is anything else that is not in the list. The app has no «Historial» tab: the past days
are seen in Nutrición › Día with ◀.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import AUN_NO, Accion, Contexto, Peticion, TarjetaMal, no

NO_LO_CAMBIO = "Eso no lo cambio yo: "
_DONDE = (
    ("borrar_dia", "borrar un día entero", "en Nutrición › Día, vete a ese día y quita lo que sobre"),
    ("borrar_peso", "borrar un peso", "tu peso se apunta o se corrige en Nutrición › Balance"),
    ("ollas", "guardar, cambiar o borrar una olla o una tara", "las ollas están en Nutrición › Recetas → Taras"),
    ("datos_garmin", "cambiar lo que trae Garmin (pasos hechos, sueño, pulso, gasto real)",
     "los datos de Garmin se traen con «Actualizar Garmin»"),
    ("entrenos_hechos", "cambiar o borrar un entreno ya hecho",
     "los entrenos hechos los trae Garmin: pulsa «Actualizar Garmin»"),
    ("cerrar_dia", "cerrar el día", "en Nutrición › Día, pulsa «Cerrar el día»"),
)


def _fuera(texto: str) -> tuple[Callable[..., dict], Callable[..., dict], Callable[..., str]]:
    """Proposing or redoing it is a `no` with `texto`; it is never applied."""
    def proponer(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
        return no(texto)

    def rehacer(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
        return no(texto)

    def aplicar(parametros: dict, ctx: Contexto) -> str:
        raise TarjetaMal(texto)

    return proponer, rehacer, aplicar


ACCIONES = [Accion(ident, linea, *_fuera(NO_LO_CAMBIO + donde), cambia=False) for ident, linea, donde in _DONDE] + [
    Accion("otra", "cualquier otro cambio que no esté en esta lista; en texto, lo que pide.", *_fuera(AUN_NO),
           cambia=False),
]
