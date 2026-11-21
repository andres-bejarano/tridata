"""Unit 026: the common foods the app brings, with their kcal/100 g (the egg, per unit).

Approximate values of food composition tables (BEDCA and USDA), rounded. They are loaded as ingredients
(`alimento_fijo`) marked `comun`, once and without ever touching what is Andrés's: a common food whose name
(without capitals or accents) is already a recipe, ingredient or pot of his, or an alias of his, is not loaded;
one that was removed or corrected is never loaded again (`Recetario.cargar_comunes`).

Unit 101 (REC-45, R-283): each one also brings its protein, carbohydrates and fat in grams on the same base as its kcal
(per 100 g; the egg, per unit), from the same tables, rounded to 0.1 g and marked «estimado» like its kcal (S-94).
4·P + 4·H + 9·G is within 15 % of its kcal, except where `aviso` says why (the alcohol of beer and wine has kcal and
is none of the three). They are also put in the common foods already loaded without macros that Andrés did not touch.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .recetas import Recetario

# name: (kcal, protein, carbohydrates, fat) per 100 g
_POR_100G = {
    # fruit
    "Plátano": (94, 1.2, 20.0, 0.3), "Pera": (49, 0.4, 11.0, 0.1), "Naranja": (42, 0.9, 8.6, 0.1),
    "Mandarina": (40, 0.8, 9.0, 0.2), "Fresa": (36, 0.7, 7.0, 0.4), "Uva": (69, 0.7, 16.0, 0.2),
    "Kiwi": (55, 1.1, 11.0, 0.5), "Melón": (34, 0.8, 7.5, 0.2), "Sandía": (30, 0.6, 7.0, 0.2),
    "Melocotón": (41, 0.9, 9.0, 0.3), "Piña": (50, 0.5, 11.5, 0.1), "Aguacate": (160, 2.0, 8.5, 14.7),
    "Dátil": (282, 2.5, 65.0, 0.4), "Pasas": (299, 3.1, 72.0, 0.5), "Higo": (74, 0.8, 17.0, 0.3),
    "Cereza": (63, 1.1, 14.0, 0.2),
    # vegetables
    "Tomate": (20, 0.9, 3.5, 0.2), "Lechuga": (15, 1.4, 2.0, 0.2), "Pepino": (12, 0.7, 2.0, 0.1),
    "Zanahoria": (38, 0.9, 7.5, 0.2), "Cebolla": (33, 1.1, 6.5, 0.1), "Pimiento rojo": (31, 1.0, 6.0, 0.3),
    "Pimiento verde": (20, 0.9, 3.5, 0.2), "Calabacín": (17, 1.2, 2.5, 0.3), "Brócoli": (34, 2.8, 4.5, 0.4),
    "Espinaca": (23, 2.9, 1.5, 0.4), "Patata": (80, 2.0, 17.0, 0.1), "Champiñón": (22, 3.1, 2.0, 0.3),
    "Judía verde": (31, 1.8, 5.0, 0.2), "Berenjena": (24, 1.0, 4.5, 0.2),
    # bread and cereals
    "Pan de barra": (260, 8.5, 52.0, 1.6), "Pan integral": (247, 9.0, 44.0, 3.4),
    "Arroz crudo": (360, 7.0, 79.0, 0.6), "Arroz cocido": (130, 2.7, 28.0, 0.3),
    "Pasta cruda": (360, 12.5, 72.0, 1.5), "Pasta cocida": (155, 5.8, 30.0, 0.9),
    "Copos de maíz": (380, 7.5, 84.0, 0.9), "Galleta María": (440, 7.0, 74.0, 12.5),
    "Cuscús crudo": (360, 12.8, 72.0, 0.6), "Harina": (345, 10.0, 73.0, 1.0),
    # pulses
    "Lentejas crudas": (320, 24.0, 50.0, 1.1), "Lentejas cocidas": (116, 9.0, 17.0, 0.4),
    "Garbanzos cocidos": (140, 8.5, 20.0, 2.6), "Alubias cocidas": (105, 7.5, 15.5, 0.5),
    # meat, fish
    "Pechuga de pollo cruda": (110, 23.0, 0.0, 1.5), "Pollo cocinado": (165, 31.0, 0.0, 3.6),
    "Ternera magra": (130, 21.0, 0.0, 5.0), "Lomo de cerdo": (150, 21.0, 0.0, 7.0),
    "Atún al natural": (110, 25.0, 0.0, 1.0), "Atún en aceite": (200, 26.0, 0.0, 10.5),
    "Salmón": (200, 20.0, 0.0, 13.0), "Merluza": (75, 16.0, 0.0, 1.2), "Jamón serrano": (240, 30.0, 0.0, 13.5),
    "Jamón cocido": (110, 18.0, 1.5, 3.5), "Pavo en lonchas": (100, 19.0, 1.5, 2.0), "Gambas": (85, 19.0, 0.0, 1.0),
    # dairy
    "Leche entera": (64, 3.2, 4.7, 3.6), "Leche semidesnatada": (46, 3.3, 4.8, 1.6),
    "Leche desnatada": (35, 3.4, 4.9, 0.2), "Yogur natural": (60, 3.8, 4.7, 2.9), "Yogur griego": (120, 6.0, 4.0, 9.0),
    "Queso fresco": (175, 12.0, 3.0, 13.0), "Mozzarella": (250, 18.0, 2.0, 19.0),
    "Queso curado": (400, 26.0, 0.5, 32.5), "Mantequilla": (740, 0.7, 0.6, 81.5),
    # fats and nuts
    "Aceite de oliva": (899, 0.0, 0.0, 99.9), "Almendras": (600, 21.0, 6.5, 54.0), "Cacahuetes": (590, 26.0, 8.0, 49.0),
    "Pistachos": (560, 20.0, 17.5, 45.0), "Avellanas": (630, 15.0, 7.0, 61.0),
    # sweet
    "Azúcar": (400, 0.0, 99.8, 0.0), "Miel": (300, 0.4, 75.0, 0.0), "Chocolate con leche": (540, 7.5, 56.0, 31.5),
    "Crema de cacao": (540, 6.0, 57.0, 31.0), "Croissant": (410, 8.0, 45.0, 21.5), "Magdalena": (420, 6.0, 52.0, 21.0),
    "Galletas de chocolate": (480, 6.0, 65.0, 22.0),
    # drinks (per 100 g: in ml they do not fit, unit 026 leaves it out)
    "Zumo de naranja": (45, 0.7, 10.0, 0.2), "Cerveza": (43, 0.5, 3.5, 0.0), "Vino tinto": (85, 0.1, 2.6, 0.0),
    "Refresco de cola": (42, 0.0, 10.5, 0.0), "Bebida isotónica": (25, 0.0, 6.0, 0.0),
}

_ALCOHOL = "casi todas sus kcal son del alcohol, que no es proteína, hidratos ni grasa"
_AVISOS = {"Cerveza": _ALCOHOL, "Vino tinto": _ALCOHOL}

COMUNES: tuple[dict, ...] = (
    *({"nombre": nombre, "kcal": kcal, "por": "100g", "proteina": p, "hidratos": h, "grasa": g,
       **({"aviso": _AVISOS[nombre]} if nombre in _AVISOS else {})}
      for nombre, (kcal, p, h, g) in _POR_100G.items()),
    {"nombre": "Huevo", "kcal": 75, "por": "unidad", "nota": "mediano", "proteina": 6.5, "hidratos": 0.4, "grasa": 5.2},
)


def cargar_comunes(recetario: Recetario) -> int:
    """Load the common foods that are missing (idempotent) and, unit 101, fill in the macros of the ones loaded
    before without them; returns how many were added."""
    return recetario.cargar_comunes(COMUNES)
