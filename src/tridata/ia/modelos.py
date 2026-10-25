"""The models the app knows (unit 041, G-56): the ones a role of the team can answer with, chosen in Ajustes.

Each one with its provider, its name on the screen and its price, from which the spending of the Asistente is counted
(`conversaciones.dolares`). Today only Anthropic. A new provider will be one more entry here and its client, without
touching the screens (the study P-20261025-6d148e21 will say which).

Prices in $ per million tokens: (input, output, cache write 5 min, cache read). Official prices of the SDK reference
(2026-10-06): Haiku 5.5 0.10 / 0.50 up to 100K tokens in one call and 0.50 / 2.50 above (bug 038), Sonnet 5.5 2 / 10;
a cache write is 1.25x and a read 0.1x the input.
"""
from __future__ import annotations

from dataclasses import dataclass

Precios = tuple[float, float, float, float]


@dataclass(frozen=True)
class Modelo:
    id: str
    proveedor: str
    nombre: str  # «Sonnet»: what the line of a conversation and each answer say
    descripcion: str  # «más fuerte»: what Ajustes › El equipo says next to it
    precios: Precios
    precios_largos: Precios | None = None  # above `LARGO_DESDE` tokens in one call


MODELOS: dict[str, Modelo] = {
    "claude-haiku-5-5": Modelo("claude-haiku-5-5", "anthropic", "Haiku", "barato",
                               (0.10, 0.50, 0.125, 0.01), (0.50, 2.50, 0.625, 0.05)),
    "claude-sonnet-5-5": Modelo("claude-sonnet-5-5", "anthropic", "Sonnet", "más fuerte", (2.0, 10.0, 2.5, 0.20)),
}
MAS_CARO = "claude-sonnet-5-5"  # an unknown model is counted at the dearest price: the cap never falls short


def nombre_de(modelo: str | None) -> str:
    """«Sonnet»; a model out of the catalogue, its id as it is."""
    conocido = MODELOS.get(modelo or "")
    return conocido.nombre if conocido else (modelo or "")


__all__ = ["MAS_CARO", "MODELOS", "Modelo", "nombre_de"]
