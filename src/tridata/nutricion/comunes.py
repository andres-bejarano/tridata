"""Unit 026: the common foods the app brings, with their kcal/100 g (the egg, per unit).

Approximate values of food composition tables (BEDCA and USDA), rounded. They are loaded as ingredients
(`alimento_fijo`) marked `comun`, once and without ever touching what is Andrés's: a common food whose name
(without capitals or accents) is already a recipe, ingredient or pot of his, or an alias of his, is not loaded;
one that was removed or corrected is never loaded again (`Recetario.cargar_comunes`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .recetas import Recetario

_POR_100G = {
    # fruit
    "Plátano": 94, "Pera": 49, "Naranja": 42, "Mandarina": 40, "Fresa": 36, "Uva": 69, "Kiwi": 55, "Melón": 34,
    "Sandía": 30, "Melocotón": 41, "Piña": 50, "Aguacate": 160, "Dátil": 282, "Pasas": 299, "Higo": 74,
    "Cereza": 63,
    # vegetables
    "Tomate": 20, "Lechuga": 15, "Pepino": 12, "Zanahoria": 38, "Cebolla": 33, "Pimiento rojo": 31,
    "Pimiento verde": 20, "Calabacín": 17, "Brócoli": 34, "Espinaca": 23, "Patata": 80, "Champiñón": 22,
    "Judía verde": 31, "Berenjena": 24,
    # bread and cereals
    "Pan de barra": 260, "Pan integral": 247, "Arroz crudo": 360, "Arroz cocido": 130, "Pasta cruda": 360,
    "Pasta cocida": 155, "Copos de maíz": 380, "Galleta María": 440, "Cuscús crudo": 360, "Harina": 345,
    # pulses
    "Lentejas crudas": 320, "Lentejas cocidas": 116, "Garbanzos cocidos": 140, "Alubias cocidas": 105,
    # meat, fish
    "Pechuga de pollo cruda": 110, "Pollo cocinado": 165, "Ternera magra": 130, "Lomo de cerdo": 150,
    "Atún al natural": 110, "Atún en aceite": 200, "Salmón": 200, "Merluza": 75, "Jamón serrano": 240,
    "Jamón cocido": 110, "Pavo en lonchas": 100, "Gambas": 85,
    # dairy
    "Leche entera": 64, "Leche semidesnatada": 46, "Leche desnatada": 35, "Yogur natural": 60,
    "Yogur griego": 120, "Queso fresco": 175, "Mozzarella": 250, "Queso curado": 400, "Mantequilla": 740,
    # fats and nuts
    "Aceite de oliva": 899, "Almendras": 600, "Cacahuetes": 590, "Pistachos": 560, "Avellanas": 630,
    # sweet
    "Azúcar": 400, "Miel": 300, "Chocolate con leche": 540, "Crema de cacao": 540, "Croissant": 410,
    "Magdalena": 420, "Galletas de chocolate": 480,
    # drinks (per 100 g: in ml they do not fit, unit 026 leaves it out)
    "Zumo de naranja": 45, "Cerveza": 43, "Vino tinto": 85, "Refresco de cola": 42, "Bebida isotónica": 25,
}

COMUNES: tuple[dict, ...] = (
    *({"nombre": nombre, "kcal": kcal, "por": "100g"} for nombre, kcal in _POR_100G.items()),
    {"nombre": "Huevo", "kcal": 75, "por": "unidad", "nota": "mediano"},
)


def cargar_comunes(recetario: Recetario) -> int:
    """Load the common foods that are missing (idempotent); returns how many were added."""
    return recetario.cargar_comunes(COMUNES)
