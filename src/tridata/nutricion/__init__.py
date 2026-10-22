"""Food log: what Andrés eats or plans to eat, the day total and the margin.

Its own tables in the same SQLite store as the Garmin data (`storage.DEFAULT_DB_PATH`).
Totals and margins are always computed from the saved rows, never remembered (G-18).
Profile and plan settings, each with the date from which it applies: `Ajustes.vigentes(dia)`.
Weight of the day and its basal: `RegistroPeso`, `basal_del_dia`; objective of the day: `ObjetivosDia`.
Full expenditure with Garmin and the planned training: `estimar_gasto` (unit 008).
Recipes, fixed foods and pots, and resolving what is dictated with them: `Recetario` (unit 011).
Closing the day (the food, then the expenditure with Garmin), its trail and the kcal per step calibrated
with Garmin: `Cierres`; the estimated expenditure of any day: `gasto_estimado_del_dia` (unit 014).
The Balance tab (week, chart, list of weeks, spreading what is above the objective): module `balance`
(unit 017); what is spent with what is already done: `gasto_hecho`.

Note: the submodule `balance` is imported BEFORE `cierre`, so the name `balance` of this package stays the
function `cierre.balance` (importing a submodule binds its name on the package). Import the module with
`from tridata.nutricion.balance import …`, never `import tridata.nutricion.balance as …`.
"""
from .ajustes import DESDE_SIEMPRE, Ajustes, AjustesDelDia, Carrera, ObjetivoPeso
from .balance import (  # before .cierre: see the note above
    BARRAS_POR_DIA_MAX, dias_que_quedan, fila_del_dia, proponer_reparto, semanas, serie, texto_reparto,
)
from .cierre import (
    Cierres, balance, calibrar, config_gasto_del_dia, es_relevante, garmin_incompleto, gasto_estimado_del_dia,
    media_semana,
)
from .gasto import estimar_gasto, gasto_hecho, kcal_previstas
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
    "BARRAS_POR_DIA_MAX", "dias_que_quedan", "fila_del_dia", "gasto_hecho", "proponer_reparto", "semanas", "serie",
    "texto_reparto",
    "DESDE_SIEMPRE", "NIVELES_POR_DEFECTO", "Ajustes", "AjustesDelDia", "Alimento", "BasalDelDia", "Carrera", "Cierres",
    "ConfigGasto", "MediaPeso", "ObjetivoPeso", "ObjetivosDia", "Recetario", "RegistroComidas", "RegistroPeso", "balance",
    "barra", "basal_del_dia", "basal_mifflin", "calibrar", "config_del_dia", "config_gasto_del_dia", "dias_de_retraso",
    "es_relevante", "estimar_gasto", "garmin_incompleto", "gasto_estimado_del_dia", "gasto_simple", "gramos_que_caben",
    "kcal_previstas", "limite_saludable", "margenes", "media_7_dias", "media_semana", "niveles_del_dia",
    "objetivo_valido", "peso_valido", "proyeccion", "resumen_del_dia", "resumen_peso", "simular", "texto_retraso",
    "total_del_dia",
]
