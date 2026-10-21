"""Food log: what Andrés eats or plans to eat, the day total and the margin.

Its own tables in the same SQLite store as the Garmin data (`storage.DEFAULT_DB_PATH`).
Totals and margins are always computed from the saved rows, never remembered (G-18).
Profile and plan settings, each with the date from which it applies: `Ajustes.vigentes(dia)`.
Weight of the day and its basal: `RegistroPeso`, `basal_del_dia`; objective of the day: `ObjetivosDia`.
Full expenditure with Garmin and the planned training: `estimar_gasto` (unit 008).
Recipes, fixed foods and pots, and resolving what is dictated with them: `Recetario` (unit 011).
"""
from .ajustes import DESDE_SIEMPRE, Ajustes, AjustesDelDia, Carrera, ObjetivoPeso
from .gasto import estimar_gasto, kcal_previstas
from .margen import (
    NIVELES_POR_DEFECTO, ConfigGasto, barra, config_del_dia, dias_de_retraso, gasto_simple, gramos_que_caben,
    limite_saludable, margenes, niveles_del_dia, resumen_del_dia, simular, texto_retraso, total_del_dia,
)
from .objetivo_dia import ObjetivosDia, objetivo_valido
from .peso import (
    BasalDelDia, MediaPeso, RegistroPeso, basal_del_dia, basal_mifflin, media_7_dias, peso_valido, proyeccion,
    resumen_peso,
)
from .recetas import Recetario
from .registro import Alimento, RegistroComidas

__all__ = [
    "DESDE_SIEMPRE", "NIVELES_POR_DEFECTO", "Ajustes", "AjustesDelDia", "Alimento", "BasalDelDia", "Carrera",
    "ConfigGasto", "MediaPeso", "ObjetivoPeso", "ObjetivosDia", "Recetario", "RegistroComidas", "RegistroPeso", "barra",
    "basal_del_dia", "basal_mifflin", "config_del_dia", "dias_de_retraso", "estimar_gasto", "gasto_simple",
    "gramos_que_caben", "kcal_previstas", "limite_saludable", "margenes", "media_7_dias", "niveles_del_dia",
    "objetivo_valido", "peso_valido", "proyeccion", "resumen_del_dia", "resumen_peso", "simular", "texto_retraso",
    "total_del_dia",
]
