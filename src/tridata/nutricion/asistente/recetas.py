"""The actions on recipes and ingredients (unit 021): save an ingredient, or correct its kcal (R-80, R-81).

`guardar_ingrediente`: the name said, compared without case or accents with every name and alias (G-34). An
ingredient with that name → correct its kcal, with the before of the base (R-67); a recipe or a pot → not
proposed, with the words of the Recetas tab; nothing → save a new one. The check is the one of that tab
(`Recetario._datos_fijo` and `_unico`, done on a connection that is rolled back: proposing writes nothing), and it
is applied with `crear_fijo` / `cambiar_fijo`, so what was logged with it keeps its kcal (R-85).

Unit 023 (REC-14, R-82..R-85) adds the kitchen: `nueva_receta` (with its `lineas`; a name that is already a recipe
becomes another batch, or the correction of the one in use if Andrés says so), `corregir_receta` (an ingredient or
its grams in the batch in use), `pesar_receta` (full pot with the tare from the base, or net weight),
`borrar_receta` and `borrar_ingrediente` (the card is the confirmation, G-37). Every figure is computed here with
what is saved: the lines go through `Recetario._version` on a connection that is rolled back, so the kcal of a
saved ingredient are always the saved ones, what is not saved is «estimado» with the `kcal_estimadas` Claude gave
for that line, and a line without them is not proposed (G-40). It is applied with `crear_receta`, `otra_tanda`,
`corregir`, `borrar_receta(…, True)` and `borrar_fijo(…, True)`, as the Recetas tab does; what was logged keeps
its own kcal (G-33, R-85). Pots and tares are not changed saying it (G-43).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..recetas import Recetario, normalizar
from . import Accion, Contexto, Peticion, TarjetaMal, campo, no, numero, tarjeta
from .fuera import _DONDE, NO_LO_CAMBIO

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


# -- unit 023: the kitchen -------------------------------------------------------------------------------------------

OLLAS = NO_LO_CAMBIO + next(donde for ident, _, donde in _DONDE if ident == "ollas")  # G-43
CON_ELLA = "lo ya apuntado con ella no cambia"
CON_EL = "lo ya apuntado con él no cambia"
RECETAS = "en Nutrición › Recetas"
NO_ESTA_RECETA = "Esa receta ya no está en Recetas"


def _buscar(recetario: Recetario, nombre: str, tipos: tuple[str, ...]) -> list[tuple[str, int, str]]:
    """What has that name or alias (any kind: G-34, one name one thing); else, the things of `tipos` whose name or
    alias contains it (without case or accents), once each."""
    clave = normalizar(nombre)
    if not clave:
        return []
    conn = _sin_escribir(recetario)
    try:
        duenos = recetario._duenos(conn)  # noqa: SLF001
    finally:
        conn.close()
    if clave in duenos:
        return [duenos[clave]]
    vistos: dict[tuple[str, int], tuple[str, int, str]] = {}
    for n, dueno in duenos.items():
        if dueno[0] in tipos and clave in n:
            vistos.setdefault((dueno[0], dueno[1]), dueno)
    return list(vistos.values())


def _cual(encontrados: list[tuple[str, int, str]]) -> str:
    return "¿Cuál? Tengo " + ", ".join(f"«{d[2]}»" for d in encontrados)


def _receta(ctx: Contexto, nombre: str) -> tuple[dict | None, dict | None]:
    """(the recipe, None) or (None, the `no` that says why not)."""
    encontrados = _buscar(ctx.recetario, nombre, ("receta",))
    if not encontrados:
        return None, no(f"No tengo ninguna receta «{nombre}» en Recetas")
    if len(encontrados) > 1:
        return None, no(_cual(encontrados))
    tipo, ident, propio = encontrados[0]
    if tipo == "olla":
        return None, no(OLLAS)
    if tipo != "receta":
        return None, no(f"«{propio}» es un ingrediente, no una receta")
    return ctx.recetario.receta(ident), None


def _es_numero(valor: Any) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _lineas_dichas(cambio: Any) -> list[dict]:
    """The lines of `CambioInterpretado` as the card keeps them: what was said, never resolved yet."""
    return [{"nombre": " ".join((linea.nombre or "").split()), "cantidad": linea.cantidad,
             "unidad": (linea.unidad or "").strip() or None, "kcal": linea.kcal_estimadas}
            for linea in (cambio.lineas or [])]


def _corregidas(lineas: Any, valores: dict) -> list[dict]:
    """The lines with the grams corrected on the card (`g0`, `g1`…); an estimate follows its grams."""
    if not isinstance(lineas, list) or not all(isinstance(x, dict) and isinstance(x.get("nombre"), str) for x in lineas):
        raise TarjetaMal("la tarjeta no dice qué ingredientes")
    resultado = []
    for i, original in enumerate(lineas):
        linea = dict(original)
        if f"g{i}" in valores:
            nueva = _como_numero(valores[f"g{i}"])
            antes, kcal = linea.get("cantidad"), linea.get("kcal")
            if _es_numero(kcal) and _es_numero(nueva) and _es_numero(antes) and (antes or 0) > 0:
                linea["kcal"] = kcal * nueva / antes
            linea["cantidad"] = nueva
        resultado.append(linea)
    return resultado


def _fijos(ctx: Contexto, nombre: str) -> list[tuple[str, int, str]]:
    """The saved ingredients a line names. His own win over the common ones of unit 026: «calabaza» is his
    «calabaza de prueba» before the common «Calabaza», and «aceite» his «aceite de prueba» before «Aceite de oliva».
    Else the one with that very name, else every one whose name or alias contains it."""
    clave = normalizar(nombre)
    if not clave:
        return []
    conn = _sin_escribir(ctx.recetario)
    try:
        duenos = ctx.recetario._duenos(conn)  # noqa: SLF001
    finally:
        conn.close()
    comunes = {f["id"] for f in ctx.recetario.fijos() if f["comun"]}
    exacto = duenos.get(clave)
    exacto = exacto if exacto and exacto[0] == "fijo" else None
    if exacto and exacto[1] not in comunes:
        return [exacto]
    trozo: dict[int, tuple[str, int, str]] = {}
    for n, dueno in duenos.items():
        if dueno[0] == "fijo" and clave in n:
            trozo.setdefault(dueno[1], dueno)
    propios = [d for d in trozo.values() if d[1] not in comunes]
    if len(propios) == 1:
        return propios
    return [exacto] if exacto else list(trozo.values())


def _resolver(ctx: Contexto, linea: dict) -> tuple[dict | None, str | None]:
    """A said line → what the Recetas tab receives (a saved ingredient by its name, or an estimate), or why not."""
    nombre, unidad = linea["nombre"], linea.get("unidad") or "g"
    if not nombre:
        return None, "No sé qué ingrediente es uno de ellos"
    if linea.get("cantidad") is None:
        return None, f"No sé cuánto {nombre} lleva"
    fijos = _fijos(ctx, nombre)
    if len(fijos) == 1:
        propio = fijos[0][2]
        return {"nombre": propio, "cantidad": linea["cantidad"], "unidad": unidad, "kcal": None,
                "guardado": propio}, None
    if linea.get("kcal") is not None:
        return {"nombre": nombre, "cantidad": linea["cantidad"], "unidad": unidad, "kcal": linea["kcal"]}, None
    if len(fijos) > 1:
        return None, _cual(fijos)
    return None, f"No sé qué es «{nombre}»"


def _crudo_de(ingrediente: dict) -> dict:
    """An ingredient of a saved version, as the Recetas tab sends it back when correcting."""
    return {"nombre": ingrediente["nombre"], "cantidad": ingrediente["cantidad"], "unidad": ingrediente["unidad"],
            "kcal": ingrediente["kcal"], "estado": ingrediente["estado"],
            "guardado": ingrediente["nombre"] if ingrediente["estado"] == "guardado" else None}


def _indice(ctx: Contexto, crudos: list[dict], nombre: str) -> int | None:
    """Which ingredient of the batch is the one said: the same name, the saved ingredient it names, or the only one
    whose name contains it."""
    clave = normalizar(nombre)
    nombres = [normalizar(c["nombre"]) for c in crudos]
    if clave in nombres:
        return nombres.index(clave)
    fijos = _fijos(ctx, nombre)
    if len(fijos) == 1 and normalizar(fijos[0][2]) in nombres:
        return nombres.index(normalizar(fijos[0][2]))
    trozo = [i for i, n in enumerate(nombres) if clave and clave in n]
    return trozo[0] if len(trozo) == 1 else None


def _cuanto(cantidad: Any, unidad: str | None) -> str:
    return f"{numero(cantidad)} {unidad or 'g'}"


def _mezclar(ctx: Contexto, existentes: list[dict], lineas: list[dict]) -> tuple[list[dict], list[str], list[int], str | None]:
    """The batch with the said lines changed or added: (ingredients, what changes, which ones were said, why not)."""
    crudos = [_crudo_de(i) for i in existentes]
    cambios: list[str] = []
    dichos: list[int] = []
    for linea in lineas:
        i = _indice(ctx, crudos, linea["nombre"])
        if i is None:
            crudo, problema = _resolver(ctx, linea)
            if crudo is None:
                return [], [], [], problema
            crudos.append(crudo)
            dichos.append(len(crudos) - 1)
            cambios.append(f"añado {crudo['nombre']} {_cuanto(crudo['cantidad'], crudo['unidad'])}")
            continue
        actual = crudos[i]
        nueva, unidad = linea.get("cantidad"), linea.get("unidad") or actual["unidad"]
        if nueva is None:
            return [], [], [], f"No sé cuánto {actual['nombre']} lleva"
        cambiado = {**actual, "cantidad": nueva, "unidad": unidad}
        antes = actual["cantidad"]
        if actual["estado"] == "estimado" and _es_numero(actual["kcal"]) and _es_numero(nueva) and _es_numero(antes) \
                and antes > 0:
            cambiado["kcal"] = actual["kcal"] * nueva / antes
        crudos[i] = cambiado
        dichos.append(i)
        if nueva != antes or unidad != actual["unidad"]:
            de = numero(antes) if unidad == actual["unidad"] else _cuanto(antes, actual["unidad"])
            cambios.append(f"{actual['nombre']} {de} → {_cuanto(nueva, unidad)}")
    return crudos, cambios, dichos, None


def _simular(recetario: Recetario, datos: dict, base: dict | None) -> tuple[dict | None, str | None]:
    """The version the Recetas tab would save, with its checks and words; nothing is written."""
    conn = _sin_escribir(recetario)
    try:
        return recetario._version(conn, datos, base), None  # noqa: SLF001
    except ValueError as exc:
        return None, str(exc)
    finally:
        conn.rollback()
        conn.close()


def _linea(ingrediente: dict) -> str:
    """«calabaza de prueba 800 g · 200 kcal»: one line of detail of the card."""
    cuanto = "sin cantidad" if ingrediente["cantidad"] is None else _cuanto(ingrediente["cantidad"], ingrediente["unidad"])
    kcal = "sin kcal" if ingrediente["kcal"] is None else f"{numero(ingrediente['kcal'])} kcal"
    marca = {"estimado": " (estimado)", "dudoso": " (dudoso)"}.get(ingrediente["estado"], "")
    return f"{ingrediente['nombre']} {cuanto} · {kcal}{marca}"


def _dudosa(version: dict, dichos: list[int]) -> str | None:
    """G-40: a said line whose kcal are not known (a saved ingredient in a unit that does not fit, …)."""
    for i in dichos:
        ingrediente = version["ingredientes"][i]
        if ingrediente["estado"] == "dudoso":
            return f"No sé cuántas kcal son {_cuanto(ingrediente['cantidad'], ingrediente['unidad'])} de {ingrediente['nombre']}"
    return None


def _kcal_100(valor: Any) -> str:
    return "sin pesar" if valor is None else f"{numero(valor)} kcal/100 g"


def _campos_gramos(lineas: list[dict], nombres: list[str]) -> list[dict]:
    return [campo(f"g{i}", f"{nombres[i]} ({linea.get('unidad') or 'g'})", "numero", linea.get("cantidad"))
            for i, linea in enumerate(lineas)]


# -- nueva_receta ------------------------------------------------------------------------------------------------------

def _proponer_nueva(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    nombre = " ".join((cambio.objeto or "").split())
    if not nombre:
        return no("No sé cómo se llama la receta")
    modo = normalizar(cambio.campo).replace(" ", "_") or None
    if modo == "corregir":
        return _corregir_por_nombre(nombre, _lineas_dichas(cambio), ctx, peticion)
    return _rehacer_nueva({"nombre": nombre, "lineas": _lineas_dichas(cambio),
                           "modo": "otra_tanda" if modo == "otra_tanda" else None}, {}, ctx, peticion)


def _rehacer_nueva(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    nombre, modo = parametros["nombre"], parametros.get("modo")
    if not isinstance(nombre, str) or not nombre.strip() or modo not in (None, "otra_tanda"):
        raise TarjetaMal("la tarjeta no dice qué receta")
    lineas = _corregidas(parametros["lineas"], valores)
    if not lineas:
        return no("añade al menos un ingrediente")
    receta = None
    if modo == "otra_tanda":
        receta, rechazo = _receta(ctx, nombre)
        if receta is None:
            return rechazo or no(NO_ESTA_RECETA)
    else:
        exacto = _buscar(ctx.recetario, nombre, ())
        if exacto and exacto[0][0] == "receta":
            receta = ctx.recetario.receta(exacto[0][1])
        elif exacto:
            _, error = _comprobar_nombre(ctx.recetario, nombre)
            return no(error or f"«{nombre}» ya es otra cosa guardada")
    problema: str | None = None
    if receta is not None and modo == "otra_tanda":
        crudos, _, dichos, problema = _mezclar(ctx, receta["en_uso"]["ingredientes"], lineas)
    else:
        crudos, dichos = [], []
        for linea in lineas:
            crudo, problema = _resolver(ctx, linea)
            if crudo is None:
                break
            crudos.append(crudo)
            dichos.append(len(crudos) - 1)
    if problema is not None:
        return no(problema)
    version, error = _simular(ctx.recetario, {"ingredientes": crudos}, None)
    if version is not None:
        problema = _dudosa(version, dichos)
        if problema is not None:
            return no(problema)
    nombres = [crudos[i]["nombre"] for i in dichos]
    despues = f"{numero(version['kcal_total'])} kcal la receta entera, sin pesar" if version else None
    comun: dict[str, Any] = {"peticion": peticion, "despues": despues, "lineas": [_linea(i) for i in version["ingredientes"]] if version else [],
             "campos": _campos_gramos(lineas, nombres), "error": error}
    if receta is None:
        return tarjeta(accion="nueva_receta", parametros={**parametros, "ingredientes": crudos},
                       texto=f"Voy a guardar la receta {nombre}" + (f": {despues}" if despues else ""),
                       donde=RECETAS, **comun)
    en_uso = receta["en_uso"]
    otra = max(v["numero"] for v in receta["versiones"]) + 1
    avisos = [] if modo == "otra_tanda" else [f"ya tienes la receta «{receta['nombre']}»: la guardo como otra tanda"]
    avisos += [f"la tanda {en_uso['numero']} se conserva", CON_ELLA]
    return tarjeta(accion="nueva_receta",
                   parametros={**parametros, "receta_id": receta["id"], "ingredientes": crudos},
                   texto=f"Voy a guardar otra tanda de {receta['nombre']} (la {otra})" + (f": {despues}" if despues else ""),
                   antes=f"tanda {en_uso['numero']}: {numero(en_uso['kcal_total'])} kcal la receta entera, "
                         f"{_kcal_100(en_uso['kcal_100g'])}",
                   donde=f"{RECETAS}, como la tanda en uso", avisos=avisos, **comun)


def _comprobar_nombre(recetario: Recetario, nombre: str) -> tuple[None, str | None]:
    conn = _sin_escribir(recetario)
    try:
        recetario._unico(conn, [nombre], ("receta", None))  # noqa: SLF001
    except ValueError as exc:
        return None, str(exc)
    finally:
        conn.rollback()
        conn.close()
    return None, None


def _aplicar_nueva(parametros: dict, ctx: Contexto) -> str:
    datos = {"nombre": parametros["nombre"], "ingredientes": parametros["ingredientes"]}
    if parametros.get("receta_id") is None:
        receta = ctx.recetario.crear_receta(datos, ctx.hoy())
        return f"{receta['nombre']} está en Recetas con {numero(receta['en_uso']['kcal_total'])} kcal la receta entera"
    receta = ctx.recetario.otra_tanda(parametros["receta_id"], {"ingredientes": parametros["ingredientes"]}, ctx.hoy())
    return f"{receta['nombre']} tiene la tanda {receta['version_en_uso']} en uso; {CON_ELLA}"


# -- corregir_receta ---------------------------------------------------------------------------------------------------

def _corregir_por_nombre(nombre: str, lineas: list[dict], ctx: Contexto, peticion: Peticion) -> dict:
    receta, problema = _receta(ctx, nombre)
    if receta is None:
        return problema or no(NO_ESTA_RECETA)
    return _rehacer_corregir({"receta_id": receta["id"], "lineas": lineas}, {}, ctx, peticion)


def _proponer_corregir(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    nombre = " ".join((cambio.objeto or "").split())
    if not nombre:
        return no("No sé qué receta quieres corregir")
    if not cambio.lineas:
        return no(f"No sé qué quieres cambiar de {nombre}")
    return _corregir_por_nombre(nombre, _lineas_dichas(cambio), ctx, peticion)


def _leer_receta(ctx: Contexto, ident: Any) -> dict | None:
    if isinstance(ident, bool) or not isinstance(ident, int):
        raise TarjetaMal("la tarjeta no dice qué receta")
    try:
        return ctx.recetario.receta(ident)
    except KeyError:
        return None


def _rehacer_corregir(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    receta = _leer_receta(ctx, parametros["receta_id"])
    if receta is None:
        return no(NO_ESTA_RECETA)
    lineas = _corregidas(parametros["lineas"], valores)
    en_uso = receta["en_uso"]
    crudos, cambios, dichos, problema = _mezclar(ctx, en_uso["ingredientes"], lineas)
    if problema is not None:
        return no(problema)
    if not cambios:
        return no(f"{receta['nombre']} ya lleva eso")
    version, error = _simular(ctx.recetario, {"ingredientes": crudos}, en_uso)
    if version is not None:
        problema = _dudosa(version, dichos)
        if problema is not None:
            return no(problema)
    pesada = en_uso["kcal_100g"] is not None or (version is not None and version["kcal_100g"] is not None)
    texto = f"Voy a corregir {receta['nombre']} (tanda {en_uso['numero']}): " + ", ".join(cambios)
    antes = f"{numero(en_uso['kcal_total'])} kcal la receta entera" + (f", {_kcal_100(en_uso['kcal_100g'])}" if pesada else "")
    despues = None
    if version is not None:
        texto += f", receta entera {numero(en_uso['kcal_total'])} → {numero(version['kcal_total'])} kcal"
        despues = f"{numero(version['kcal_total'])} kcal la receta entera"
        if pesada:
            de = numero(en_uso["kcal_100g"]) if en_uso["kcal_100g"] is not None else "sin pesar"
            a = numero(version["kcal_100g"]) if version["kcal_100g"] is not None else "sin pesar"
            texto += f", {de} → {a} kcal/100 g"
            despues += f", {_kcal_100(version['kcal_100g'])}"
    return tarjeta(accion="corregir_receta", parametros={**parametros, "ingredientes": crudos}, peticion=peticion,
                   texto=texto, antes=antes, despues=despues, donde=f"{RECETAS}, en la tanda en uso",
                   avisos=[CON_ELLA], lineas=[_linea(i) for i in version["ingredientes"]] if version else [],
                   campos=_campos_gramos(lineas, [crudos[i]["nombre"] for i in dichos]), error=error)


def _aplicar_corregir(parametros: dict, ctx: Contexto) -> str:
    receta = ctx.recetario.corregir(parametros["receta_id"], {"ingredientes": parametros["ingredientes"]})
    v = receta["en_uso"]
    resto = f", {_kcal_100(v['kcal_100g'])}" if v["kcal_100g"] is not None else ""
    return f"{receta['nombre']} queda en {numero(v['kcal_total'])} kcal la receta entera{resto}; {CON_ELLA}"


# -- pesar_receta ------------------------------------------------------------------------------------------------------

def _proponer_pesar(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    nombre = " ".join((cambio.objeto or "").split())
    if not nombre:
        return no("No sé qué receta quieres pesar")
    receta, problema = _receta(ctx, nombre)
    if receta is None:
        return problema or no(NO_ESTA_RECETA)
    if cambio.valor is None:
        return no(f"No sé cuánto pesa {receta['nombre']}")
    olla_id = None
    if normalizar(cambio.campo) != "neto":
        if not (cambio.texto or "").strip():
            return no("elige la olla en la que la has pesado, o escribe el peso neto")
        ollas = [d for d in _buscar(ctx.recetario, cambio.texto, ("olla",)) if d[0] == "olla"]
        if len(ollas) != 1:
            return no(_cual(ollas) if ollas else f"No tengo ninguna olla «{cambio.texto.strip()}» en Taras")
        olla_id = ollas[0][1]
    return _rehacer_pesar({"receta_id": receta["id"], "olla_id": olla_id, "peso": cambio.valor}, {}, ctx, peticion)


def _rehacer_pesar(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    receta = _leer_receta(ctx, parametros["receta_id"])
    if receta is None:
        return no(NO_ESTA_RECETA)
    olla_id = parametros.get("olla_id")
    peso = _como_numero(valores.get("peso", parametros["peso"]))
    olla = None
    if olla_id is not None:
        olla = next((o for o in ctx.recetario.ollas() if o["id"] == olla_id), None)
        if olla is None:
            return no("esa olla ya no está en tus taras")
        datos: dict = {"olla_id": olla_id, "peso_lleno_g": peso}
    else:
        datos = {"peso_neto_g": peso}
    en_uso = receta["en_uso"]
    version, error = _simular(ctx.recetario, datos, en_uso)
    if version is not None and version["kcal_100g"] is None and error is None:
        error = "la receta tiene ingredientes sin kcal: corrígelos antes de pesarla"
    nombre = receta["nombre"]
    if version is not None and error is None:
        k = f"{numero(version['kcal_100g'])} kcal/100 g, definitiva"
        if olla is not None:
            texto = (f"Voy a pesar {nombre}: {numero(peso)} − {numero(olla['tara_g'])} ({olla['nombre']}) = "
                     f"{numero(version['peso_neto_g'])} g → {k}")
        else:
            texto = f"Voy a pesar {nombre}: {numero(version['peso_neto_g'])} g netos → {k}"
        despues: str | None = k
    else:
        texto = f"Voy a pesar {nombre}: {numero(peso)} g" + (f" en la {olla['nombre']}" if olla else " netos")
        despues = None
    return tarjeta(accion="pesar_receta", parametros={**parametros, "peso": peso}, peticion=peticion, texto=texto,
                   antes=_kcal_100(en_uso["kcal_100g"]), despues=despues,
                   donde=f"{RECETAS}, en la misma tanda ({en_uso['numero']})", avisos=[CON_ELLA],
                   campos=[campo("peso", "Olla llena (g)" if olla else "Peso neto (g)", "numero", peso)], error=error)


def _aplicar_pesar(parametros: dict, ctx: Contexto) -> str:
    if parametros.get("olla_id") is not None:
        datos = {"olla_id": parametros["olla_id"], "peso_lleno_g": parametros["peso"]}
    else:
        datos = {"peso_neto_g": parametros["peso"]}
    receta = ctx.recetario.corregir(parametros["receta_id"], datos)
    return f"{receta['nombre']} queda en {_kcal_100(receta['en_uso']['kcal_100g'])}, definitiva"


# -- borrar_receta, borrar_ingrediente ------------------------------------------------------------------------------------

def _proponer_borrar(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    nombre = " ".join((cambio.objeto or "").split())
    if not nombre:
        return no("No sé qué quieres borrar")
    tipos = ("receta",) if cambio.accion == "borrar_receta" else ("fijo",)
    encontrados = _buscar(ctx.recetario, nombre, tipos)
    if not encontrados:
        return no(f"No tengo ningún «{nombre}» en Recetas ni en Ingredientes")
    if len(encontrados) > 1:
        return no(_cual(encontrados))
    tipo, ident, _ = encontrados[0]
    if tipo == "olla":
        return no(OLLAS)
    return _rehacer_borrar({"tipo": tipo, "id": ident}, {}, ctx, peticion)


def _rehacer_borrar(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    tipo, ident = parametros.get("tipo"), parametros.get("id")
    if tipo not in ("receta", "fijo") or isinstance(ident, bool) or not isinstance(ident, int):
        raise TarjetaMal("la tarjeta no dice qué borrar")
    if tipo == "receta":
        receta = _leer_receta(ctx, ident)
        if receta is None:
            return no(NO_ESTA_RECETA)
        tandas = len(receta["versiones"])
        return tarjeta(accion="borrar_receta", parametros=parametros, peticion=peticion,
                       texto=f"Voy a borrar {receta['nombre']} de Recetas; {CON_ELLA}", antes=receta["nombre"],
                       donde=f"{RECETAS}, con {'su tanda' if tandas == 1 else f'sus {tandas} tandas'}")
    fijo = next((f for f in ctx.recetario.fijos() if f["id"] == ident), None)
    if fijo is None:
        return no("Ese ingrediente ya no está en Ingredientes")
    return tarjeta(accion="borrar_ingrediente", parametros=parametros, peticion=peticion,
                   texto=f"Voy a borrar {fijo['nombre']} de Ingredientes; {CON_EL}", antes=fijo["nombre"],
                   donde=f"{RECETAS} › Ingredientes; las recetas guardadas conservan sus kcal")


def _aplicar_borrar(parametros: dict, ctx: Contexto) -> str:
    if parametros["tipo"] == "receta":  # the card was the confirmation (G-37, R-84)
        receta = ctx.recetario.borrar_receta(parametros["id"], confirmado=True)
        return f"he borrado {receta['nombre']} de Recetas; {CON_ELLA}"
    fijo = ctx.recetario.borrar_fijo(parametros["id"], confirmado=True)
    return f"he borrado {fijo['nombre']} de Ingredientes; {CON_EL}"


ACCIONES = [
    Accion("guardar_ingrediente", "guardar un ingrediente nuevo con sus kcal, o corregir las kcal de uno guardado "
           "(«el queso son tantas por cada cien»). objeto: el nombre del ingrediente, tal cual; valor: las kcal; "
           'unidad: "g" si son por cada cien gramos, "unidad" si son por unidad.',
           _proponer, _rehacer, _aplicar),
    Accion("nueva_receta", "guardar una receta nueva con sus ingredientes, u otra tanda de una que ya tiene. objeto: "
           "el nombre de la receta; lineas: cada ingrediente con su nombre (como está guardado, si lo está), "
           'cantidad, unidad ("g", "ml" o "unidad") y kcal_estimadas solo si no está guardado; campo: "otra_tanda" si '
           'dice que es otra tanda de una receta que ya tiene (lineas: solo lo que cambia), "corregir" si dice que '
           "corrige la que usa, o null.",
           _proponer_nueva, _rehacer_nueva, _aplicar_nueva),
    Accion("corregir_receta", "corregir la tanda en uso de una receta guardada: un ingrediente o sus gramos («a los "
           "garbanzos les puse tanto de aceite, no tanto»). objeto: la receta; lineas: solo los ingredientes que "
           "cambian o se añaden, con su cantidad nueva y su unidad; antes_dicho: lo de antes, solo si lo dice.",
           _proponer_corregir, _rehacer_corregir, _aplicar_corregir),
    Accion("pesar_receta", "pesar una receta: lo que pesa la olla llena y en qué olla, o su peso neto. objeto: la "
           'receta; valor: los gramos; texto: el nombre de la olla, o null; campo: "neto" si es el peso neto, o null. '
           "Cambiar lo que pesa una olla vacía (su tara) no es esto: es ollas.",
           _proponer_pesar, _rehacer_pesar, _aplicar_pesar),
    Accion("borrar_receta", "borrar una receta guardada. objeto: su nombre.",
           _proponer_borrar, _rehacer_borrar, _aplicar_borrar),
    Accion("borrar_ingrediente", "borrar un ingrediente guardado. objeto: su nombre.",
           _proponer_borrar, _rehacer_borrar, _aplicar_borrar),
]
