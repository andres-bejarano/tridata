"""The actions on the settings and the weight (unit 022 fills this family; unit 021 leaves it empty).

Each one is an `Accion` of `nutricion.asistente`, appended to `ACCIONES`: its id, its line for Claude, how it is
proposed, how it is redone with corrected values and how it is applied with the method of the Ajustes screen.
"""
from __future__ import annotations

from . import Accion

ACCIONES: list[Accion] = []
