"""The actions on recipes and ingredients (unit 021): save an ingredient, or correct its kcal (R-80, R-81).

`guardar_ingrediente`: the name said, compared without case or accents with every name and alias (G-34). An
ingredient with that name → correct its kcal, with the before of the base (R-67); a recipe or a pot → not
proposed, with the words of the Recetas tab; nothing → save a new one. The check is the one of that tab
(`Recetario._datos_fijo` and `_unico`, done on a connection that is rolled back: proposing writes nothing), and it
is applied with `crear_fijo` / `cambiar_fijo`, so what was logged with it keeps its kcal (R-85). Unit 023 adds
the other actions on recipes and ingredients here.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..recetas import Recetario, normalizar
from . import Accion, Contexto, Peticion, TarjetaMal, campo, no, numero, tarjeta

YA_APUNTADO = "lo ya apuntado con él conserva sus kcal"


def _por(unidad: str | None) -> str | None:
    u = normalizar(unidad).replace(" ", "")
    if u in ("g", "100g", "gramos", "ciengramos", "porcada100", "kcal/100g"):
        return "100g"
    if u in ("unidad", "unidades", "ud", "u", "porunidad"):
        return "unidad"
    return None


def _kcal(kcal: Any, por: str) -> str:
    return f"{numero(kcal)} kcal/100 g" if por == "100g" else f"{numero(kcal)} kcal por unidad"


def _como_numero(valor: Any) -> Any:
    """What the card shows of a value: the number if it is one (also «285» or «2,5»), else as it came."""
    if isinstance(valor, str):
        try:
            return float(valor.strip().replace(",", "."))
        except ValueError:
            return valor
    return valor


def _sin_escribir(recetario: Recetario) -> sqlite3.Connection:
    conn = sqlite3.connect(recetario._db_path)  # noqa: SLF001 - the same store as the Recetas tab
    conn.row_factory = sqlite3.Row
    return conn


def _dueno(recetario: Recetario, nombre: str) -> tuple[str, int, str] | None:
    conn = _sin_escribir(recetario)
    try:
        return recetario._duenos(conn).get(normalizar(nombre))  # noqa: SLF001 - G-34, one name one thing
    finally:
        conn.close()


def _comprobar(recetario: Recetario, datos: dict, propio: tuple[str, int | None]) -> tuple[dict | None, str | None]:
    """The checks of the Recetas tab, with their words; what `_unico` would change is rolled back."""
    try:
        limpio = Recetario._datos_fijo(datos)  # noqa: SLF001
    except ValueError as exc:
        return None, str(exc)
    conn = _sin_escribir(recetario)
    try:
        recetario._unico(conn, [limpio["nombre"], *limpio["alias"]], propio)  # noqa: SLF001
    except ValueError as exc:
        return None, str(exc)
    finally:
        conn.rollback()
        conn.close()
    return limpio, None


def _proponer(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    nombre = " ".join((cambio.objeto or "").split())
    if not nombre:
        return no("No sé qué ingrediente quieres guardar")
    if cambio.valor is None:
        return no(f"No sé cuántas kcal tiene {nombre}")
    por = _por(cambio.unidad)
    if por is None:
        return no(f"No sé si las kcal de {nombre} son por 100 g o por unidad")
    return _rehacer({"nombre": nombre, "kcal": cambio.valor, "por": por}, {}, ctx, peticion)


def _rehacer(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    nombre, por = parametros["nombre"], parametros["por"]
    if not isinstance(nombre, str) or not nombre.strip() or por not in ("100g", "unidad"):
        raise TarjetaMal("la tarjeta no dice qué ingrediente")
    kcal = _como_numero(valores.get("kcal", parametros["kcal"]))
    campos = [campo("kcal", "kcal/100 g" if por == "100g" else "kcal por unidad", "numero", kcal)]
    dueno = _dueno(ctx.recetario, nombre)
    if dueno is not None and dueno[0] != "fijo":  # a recipe or a pot: the words of the Recetas tab (R-81)
        _, error = _comprobar(ctx.recetario, {"nombre": nombre, "kcal": kcal, "por": por}, ("fijo", None))
        return no(error or f"«{nombre}» ya es otra cosa guardada")
    if dueno is None:
        _, error = _comprobar(ctx.recetario, {"nombre": nombre, "kcal": kcal, "por": por}, ("fijo", None))
        despues = _kcal(kcal, por)
        return tarjeta(accion="guardar_ingrediente", parametros={"nombre": nombre, "kcal": kcal, "por": por},
                       peticion=peticion, texto=f"Voy a guardar: {nombre} {despues} en Ingredientes", despues=despues,
                       donde="en Nutrición › Recetas › Ingredientes", campos=campos, error=error)
    fijo = next(f for f in ctx.recetario.fijos() if f["id"] == dueno[1])
    _, error = _comprobar(ctx.recetario, {**fijo, "kcal": kcal, "por": por}, ("fijo", fijo["id"]))
    if error is None and float(kcal) == float(fijo["kcal"]) and por == fijo["por"]:
        return no(f"«{fijo['nombre']}» ya tiene {_kcal(fijo['kcal'], por)} en Ingredientes")
    antes, despues = _kcal(fijo["kcal"], fijo["por"]), _kcal(kcal, por)
    de = numero(fijo["kcal"]) if por == fijo["por"] else antes
    return tarjeta(accion="guardar_ingrediente",
                   parametros={"id": fijo["id"], "nombre": nombre, "kcal": kcal, "por": por}, peticion=peticion,
                   texto=f"Voy a corregir {fijo['nombre']} en Ingredientes: {de} → {despues}", antes=antes,
                   despues=despues, donde="en Nutrición › Recetas › Ingredientes", avisos=[YA_APUNTADO], campos=campos,
                   error=error)


def _aplicar(parametros: dict, ctx: Contexto) -> str:
    datos = {"nombre": parametros["nombre"], "kcal": parametros["kcal"], "por": parametros["por"]}
    if parametros.get("id") is None:
        guardado = ctx.recetario.crear_fijo(datos)
        return f"{guardado['nombre']} está en Ingredientes con {_kcal(guardado['kcal'], guardado['por'])}"
    fijo = next(f for f in ctx.recetario.fijos() if f["id"] == parametros["id"])
    guardado = ctx.recetario.cambiar_fijo(fijo["id"], {**fijo, "kcal": parametros["kcal"], "por": parametros["por"]})
    return f"{guardado['nombre']} queda en {_kcal(guardado['kcal'], guardado['por'])}; {YA_APUNTADO}"


ACCIONES = [
    Accion("guardar_ingrediente", "guardar un ingrediente nuevo con sus kcal, o corregir las kcal de uno guardado "
           "(«el queso son tantas por cada cien»). objeto: el nombre del ingrediente, tal cual; valor: las kcal; "
           'unidad: "g" si son por cada cien gramos, "unidad" si son por unidad.',
           _proponer, _rehacer, _aplicar),
]
