"""Food log: what Andrés eats or plans to eat, the day total and the margin.

Its own tables in the same SQLite store as the Garmin data (`storage.DEFAULT_DB_PATH`).
Totals and margins are always computed from the saved rows, never remembered (G-18).
Profile and plan settings, each with the date from which it applies: `Ajustes.vigentes(dia)`.
"""
from .ajustes import DESDE_SIEMPRE, Ajustes, AjustesDelDia, Carrera, ObjetivoPeso
from .margen import NIVELES_POR_DEFECTO, ConfigGasto, gasto_simple, margenes, resumen_del_dia, total_del_dia
from .registro import Alimento, RegistroComidas

__all__ = [
    "DESDE_SIEMPRE", "NIVELES_POR_DEFECTO", "Ajustes", "AjustesDelDia", "Alimento", "Carrera", "ConfigGasto",
    "ObjetivoPeso", "RegistroComidas", "gasto_simple", "margenes", "resumen_del_dia", "total_del_dia",
]
