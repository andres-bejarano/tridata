"""The actions on a day (units 021 and 022): what was logged and the plan of today or a day to come.

Unit 021:
`pasos_dia` (R-76, the steps part): before = the steps set for that day or the default ones of Ajustes; the check is
the one of the screen of the day (`pasos_validos`); its effect on the expenditure and on the maintenance margin is
the day recomputed as `GET /api/dia` does. A past day is not changed: Garmin brings its steps.
`quitar_alimento` (R-74, the removing part): the food of the day of the request (or the day said), found by name;
the card is the confirmation (G-37) and it is removed with `RegistroComidas.borrar`, as the screen by hand does.

Unit 022 (REC-13):
`cambiar_alimento` (R-74): substitute a logged food by another or correct its quantity, as ONE action and ONE write
(G-40). What comes in is computed as when it is logged: something saved takes the kcal and the origin of the base
(`Recetario.para_apuntar`); a new quantity of the same thing not saved scales its kcal; anything else takes Claude's
`kcal_estimadas`, marked «estimado» on the card and in the row.
`mover_alimento` (R-75): to another day with the rule of the screen by hand (`RegistroComidas.cambiar`): a day to
come → planeado, today or a past day → comido. Its kcal do not change, so its origin stays.
`niveles_dia`, `objetivo_dia` (with the delay of the weight target, as the sheet «Cambiar %», R-18) and
`entreno_previsto` (scaled from an activity of the last 14 days, `kcal_previstas`), applied with
`ObjetivosDia.cambiar`: today or a day that has not come («ese día ya pasó»; a past day is spread in Balance).
The warning «tenías X, no Y» (R-77) in every action with a before, `pasos_dia` included: what Andrés said of the
before (`antes_dicho`) against what the base has.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import date
from typing import Any

from ..dia import _retraso, plan_del_dia
from ..gasto import kcal_previstas
from ..margen import NIVELES_POR_DEFECTO, PASOS_POR_DEFECTO, objetivo_del_dia
from ..objetivo_dia import _porcentaje, niveles_validos, pasos_validos
from ..recetas import normalizar
from ..registro import Alimento, RegistroComidas
from . import (
    Accion, Contexto, Peticion, TarjetaMal, buscar_apuntado, campo, corta, descripcion_alimento, elegir, fecha_de, no,
    numero, tarjeta,
)

YA_PASO = "ese día ya pasó: sus pasos los trae Garmin"
YA_PASO_PLAN = "ese día ya pasó: un día pasado se reparte en Balance"
NO_ESTA = "Ese alimento ya no está apuntado."
NO_ENCUENTRO = "no encuentro ese entreno en los últimos 14 días"


# -- R-77: what Andrés says of the before ------------------------------------------------------------------------------

_CIFRA = re.compile(r"(\d{1,3}(?:\.\d{3})+(?![\d,])|\d+(?:[.,]\d+)?)\s*(mil\b)?", re.IGNORECASE)


def cifra_dicha(texto: Any) -> float | None:
    """The number in what was said of the before: «8 mil» 8000, «8.000» 8000, «71,8» 71.8; None if there is none."""
    if not isinstance(texto, str):
        return None
    m = _CIFRA.search(texto)
    if m is None:
        return None
    crudo = m.group(1)
    n = float(crudo.replace(".", "")) if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", crudo) else float(crudo.replace(",", "."))
    return n * 1000 if m.group(2) else n


def aviso_antes(antes_dicho: Any, antes: Any, como: Callable[[float], str]) -> list[str]:
    """«tenías 10.000, no 8.000» when what he said of the before is not what the base has (R-77); else nothing."""
    dicho = cifra_dicha(antes_dicho)
    if dicho is None or antes is None or isinstance(antes, bool):
        return []
    try:
        guardado = float(antes)
    except (TypeError, ValueError):
        return []
    return [] if abs(guardado - dicho) < 1e-9 else [f"tenías {como(guardado)}, no {como(dicho)}"]


def _con_dicho(parametros: dict, antes_dicho: Any) -> dict:
    return {**parametros, "antes_dicho": antes_dicho} if antes_dicho else parametros


# -- the effect of a change of the plan of a day on its expenditure ------------------------------------------------------

class _ConCambios:
    """The objectives of the day as they would be with other values on `dia` (to show the effect)."""

    def __init__(self, objetivos: Any, dia: date, **campos: Any) -> None:
        self._objetivos, self._dia, self._campos = objetivos, dia, campos

    def del_dia(self, dia: date) -> dict | None:
        actual = self._objetivos.del_dia(dia)
        if dia != self._dia:
            return actual
        base = actual or {"dia": dia.isoformat(), "tipo": None, "porcentaje": None, "niveles": None, "entreno": None,
                          "pasos": None, "previsto": None, "repartido": False, "creado": None}
        return {**base, **self._campos}

    def __getattr__(self, nombre: str) -> Any:
        return getattr(self._objetivos, nombre)


def _mantenimiento(resumen: dict) -> float | None:
    return next((m["kcal"] for m in resumen.get("margenes", []) if m.get("nivel") == 0), None)


def _efecto(ctx: Contexto, dia: date, **campos: Any) -> list[str]:
    """The expenditure and the maintenance margin before and after, computed on the PC (R-67, R-76)."""
    antes = ctx.resumen(dia)
    despues = ctx.resumen(dia, objetivos=_ConCambios(ctx.objetivos, dia, **campos))
    if antes is None or despues is None:
        return []
    lineas = []
    g0, g1 = round(antes["gasto"]["kcal"]), round(despues["gasto"]["kcal"])
    if g0 != g1:
        lineas.append(f"Gasto estimado del {corta(dia)}: {numero(g0)} → {numero(g1)} kcal")
    m0, m1 = _mantenimiento(antes), _mantenimiento(despues)
    if m0 is not None and m1 is not None and round(m1 - m0):
        diferencia = round(m1 - m0)
        lineas.append(f"En mantenimiento te quedan {numero(abs(diferencia))} kcal {'más' if diferencia > 0 else 'menos'}")
    return lineas


def _dia_corregido(parametros: dict, valores: dict) -> tuple[date, str | None]:
    """The day of the card, or the one corrected on it (a corrected day that is not a date keeps the card's)."""
    try:
        return fecha_de(valores.get("dia", parametros["dia"])), None
    except TarjetaMal:
        if "dia" not in valores:
            raise
        return fecha_de(parametros["dia"]), "el día no es una fecha"


# -- pasos_dia ------------------------------------------------------------------------------------------------------

def _por_defecto(ctx: Contexto, dia: date) -> int:
    valor = ctx.ajustes.vigentes(dia).pasos_por_defecto
    return PASOS_POR_DEFECTO if valor is None else int(valor)


def _pasos_de(ctx: Contexto, dia: date) -> int:
    objetivo = ctx.objetivos.del_dia(dia)
    return int(objetivo["pasos"]) if objetivo and objetivo.get("pasos") is not None else _por_defecto(ctx, dia)


def _proponer_pasos(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    if cambio.valor is None:
        return no("No sé cuántos pasos quieres para ese día")
    dia = cambio.dia or peticion.fecha
    return _rehacer_pasos(_con_dicho({"dia": dia.isoformat(), "pasos": cambio.valor}, cambio.antes_dicho), {}, ctx,
                          peticion)


def _rehacer_pasos(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    dia, error = _dia_corregido(parametros, valores)
    crudo = valores.get("pasos", parametros["pasos"])
    try:
        pasos: Any = pasos_validos(crudo)
        if pasos is None:
            pasos, error = crudo, error or "faltan los pasos previstos"
    except ValueError as exc:
        pasos, error = crudo, error or str(exc)
    if dia < peticion.fecha:  # resolved with the day it was said, so a card confirmed after midnight still applies
        error = YA_PASO
    antes, defecto = _pasos_de(ctx, dia), _por_defecto(ctx, dia)
    if error is None and pasos == antes:
        return no(f"Los pasos previstos del {corta(dia)} ya son {numero(antes)}")
    return tarjeta(
        accion="pasos_dia", parametros=_con_dicho({"dia": dia.isoformat(), "pasos": pasos}, parametros.get("antes_dicho")),
        peticion=peticion, texto=f"Voy a cambiar los pasos previstos del {corta(dia)}: {numero(antes)} → {numero(pasos)}",
        antes=numero(antes), despues=numero(pasos), dia=dia,
        donde=f"solo ese día, los pasos por defecto siguen en {numero(defecto)}",
        avisos=aviso_antes(parametros.get("antes_dicho"), antes, numero),
        lineas=_efecto(ctx, dia, pasos=pasos) if error is None else [],
        campos=[campo("pasos", "Pasos previstos", "numero", pasos), campo("dia", "Día", "fecha", dia.isoformat())],
        error=error)


def _aplicar_pasos(parametros: dict, ctx: Contexto) -> str:
    dia = fecha_de(parametros["dia"])
    guardado = ctx.objetivos.cambiar(dia, pasos=parametros["pasos"])  # the same method as the screen by hand
    return f"los pasos previstos del {corta(dia)} son {numero(guardado['pasos'])}"


# -- finding a logged food (quitar, cambiar, mover) ------------------------------------------------------------------

def _buscar(cambio: Any, ctx: Contexto, peticion: Peticion, accion: str, que: str,
            parametros: dict) -> dict | list[dict]:
    """The rows logged with that name on the day said (or of the request); a `no` or an `elegir` if not one."""
    dia = cambio.dia or peticion.fecha
    encontrados = buscar_apuntado(ctx, dia, cambio.objeto)
    if not encontrados:
        cuando = "Hoy" if dia == ctx.hoy() else f"El {corta(dia)}"
        return no(f"{cuando} no tienes ningún {cambio.objeto} apuntado")
    if len(encontrados) == 1:
        return encontrados
    return elegir(accion=accion, parametros={**parametros, "dia": dia.isoformat(),
                                             "candidatos": [a["id"] for a in encontrados]},
                  peticion=peticion, texto=f"¿Cuál quieres {que} del {corta(dia)}?",
                  candidatos=[{"eleccion": a["id"], "texto": descripcion_alimento(a)} for a in encontrados])


def _fila_elegida(parametros: dict, valores: dict, ctx: Contexto) -> dict:
    """The row of the card (or the candidate chosen); a `no` dict if it is no longer logged."""
    ident = parametros.get("id")
    if "eleccion" in valores:
        if valores["eleccion"] not in parametros.get("candidatos", []):
            return no("Elige uno de los que te he enseñado")
        ident = valores["eleccion"]
    if isinstance(ident, bool) or not isinstance(ident, int):
        raise TarjetaMal("la tarjeta no dice qué alimento")
    try:
        return ctx.registro.alimento(ident)
    except KeyError:
        return no(NO_ESTA)


def _que(fila: dict) -> str:
    """«yogur natural de prueba 125 g (75 kcal)»: the food without its hour."""
    return descripcion_alimento({**fila, "creado": None})


def _cuanto(fila: dict) -> str:
    """«100 g (360 kcal)»: the quantity and the kcal, without the name."""
    return descripcion_alimento({**fila, "nombre": "", "creado": None}).strip()


# -- quitar_alimento --------------------------------------------------------------------------------------------------

def _tarjeta_quitar(fila: dict, peticion: Peticion) -> dict:
    dia = date.fromisoformat(fila["dia"])
    que = descripcion_alimento(fila)
    return tarjeta(accion="quitar_alimento", parametros={"id": fila["id"], "dia": fila["dia"]}, peticion=peticion,
                   texto=f"Voy a quitar del {corta(dia)}: {que}", antes=que, dia=dia,
                   donde=f"en Nutrición › Día, el {corta(dia)}; lo demás del día no cambia")


def _proponer_quitar(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    if not cambio.objeto:
        return no("No sé qué quieres quitar")
    encontrados = _buscar(cambio, ctx, peticion, "quitar_alimento", "quitar", {})
    if isinstance(encontrados, dict):
        return encontrados
    return _tarjeta_quitar(encontrados[0], peticion)


def _rehacer_quitar(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    fila = _fila_elegida(parametros, valores, ctx)
    return fila if fila.get("tipo") == "no" else _tarjeta_quitar(fila, peticion)


def _aplicar_quitar(parametros: dict, ctx: Contexto) -> str:
    borrado = ctx.registro.borrar(parametros["id"])  # the card was the confirmation (G-37, R-11)
    return f"he quitado {borrado['nombre']} del {corta(date.fromisoformat(borrado['dia']))}"


# -- cambiar_alimento -------------------------------------------------------------------------------------------------

def _unidad(texto: Any) -> str | None:
    u = normalizar(texto if isinstance(texto, str) else None).replace(" ", "")
    if u in ("g", "gr", "gramo", "gramos"):
        return "g"
    if u in ("unidad", "unidades", "ud", "uds", "u"):
        return "unidad"
    return (texto.strip() or None) if isinstance(texto, str) else None


def _como_numero(valor: Any) -> Any:
    if isinstance(valor, str):
        try:
            return float(valor.strip().replace(",", "."))
        except ValueError:
            return valor
    return valor


def _origen_corto(origen: str) -> str:
    """«de tu ingrediente «manzana de prueba»» → «de tu ingrediente» (the name is already on the card)."""
    return re.sub(r"\s*«[^»]*»", "", origen).strip()


def _lo_nuevo(ctx: Contexto, fila: dict, nombre: str, cantidad: Any, unidad: str | None,
              kcal_estimadas: Any) -> tuple[dict | None, str | None]:
    """What comes in, computed as when it is logged; (None, why) if it cannot be worked out."""
    sustituye = normalizar(nombre) != normalizar(fila["nombre"])
    guardado = ctx.recetario._objetivo(nombre)  # noqa: SLF001 - the same lookup as logging a dictated food (G-31)
    if cantidad is None and not sustituye:
        cantidad = fila["cantidad"]
    if unidad is None and not sustituye:
        unidad = fila["unidad"]
    if cantidad is None and guardado and guardado[0] == "fijo" and guardado[1]["por"] == "unidad":
        cantidad = 1
    if cantidad is not None and (isinstance(cantidad, bool) or not isinstance(cantidad, (int, float)) or cantidad <= 0):
        return None, f"{nombre}: la cantidad tiene que ser mayor que 0"
    if guardado is not None:
        tipo, cosa = guardado
        try:
            calculado = ctx.recetario.para_apuntar(tipo, cosa["id"], cantidad, unidad)
        except (KeyError, ValueError) as exc:
            return None, str(exc)
        return {"nombre": calculado["nombre"], "cantidad": calculado["cantidad"], "unidad": calculado["unidad"],
                "kcal": calculado["kcal"], "origen": calculado["origen"], "provisional": calculado["provisional"],
                "estimado": False}, None
    if (not sustituye and kcal_estimadas is None and fila["kcal"] is not None and fila["cantidad"]
            and cantidad is not None and unidad == fila["unidad"]):
        return {"nombre": fila["nombre"], "cantidad": cantidad, "unidad": unidad,
                "kcal": round(fila["kcal"] * cantidad / fila["cantidad"], 1), "origen": fila["origen"],
                "provisional": fila["provisional"], "estimado": fila["estimado"]}, None
    if kcal_estimadas is None:
        return None, f"No sé cuántas kcal tiene {nombre}: dímelo o guárdalo en Recetas"
    if isinstance(kcal_estimadas, bool) or not isinstance(kcal_estimadas, (int, float)) or kcal_estimadas < 0:
        return None, f"{nombre}: las kcal no valen"
    return {"nombre": nombre, "cantidad": cantidad, "unidad": unidad, "kcal": kcal_estimadas, "origen": None,
            "provisional": False, "estimado": True}, None


def _proponer_cambiar(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    if not cambio.objeto:
        return no("No sé qué alimento quieres cambiar")
    nombre = " ".join((cambio.texto or "").split()) or None
    if nombre is None and cambio.cantidad is None:
        return no(f"No sé qué quieres cambiar de {cambio.objeto}")
    parametros = _con_dicho({"nombre": nombre, "cantidad": cambio.cantidad, "unidad": _unidad(cambio.unidad),
                             "kcal_estimadas": cambio.kcal_estimadas}, cambio.antes_dicho)
    encontrados = _buscar(cambio, ctx, peticion, "cambiar_alimento", "cambiar", parametros)
    if isinstance(encontrados, dict):
        return encontrados
    return _rehacer_cambiar({**parametros, "id": encontrados[0]["id"]}, {}, ctx, peticion)


def _rehacer_cambiar(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    fila = _fila_elegida(parametros, valores, ctx)
    if fila.get("tipo") == "no":
        return fila
    dia = date.fromisoformat(fila["dia"])
    nombre = parametros.get("nombre") or fila["nombre"]
    if not isinstance(nombre, str):
        raise TarjetaMal("la tarjeta no dice qué alimento entra")
    cantidad = _como_numero(valores.get("cantidad", parametros.get("cantidad")))
    estimadas = _como_numero(valores.get("kcal", parametros.get("kcal_estimadas")))
    nuevo, error = _lo_nuevo(ctx, fila, nombre, cantidad, parametros.get("unidad"), estimadas)
    salida = {k: v for k, v in parametros.items() if k not in ("eleccion", "candidatos")}
    salida.update(id=fila["id"], dia=fila["dia"], cantidad=cantidad,
                  kcal_estimadas=estimadas if "kcal" in valores else parametros.get("kcal_estimadas"))
    if nuevo is None:
        return tarjeta(accion="cambiar_alimento", parametros=salida, peticion=peticion,
                       texto=f"Voy a cambiar en el {corta(dia)}: {_que(fila)} → {nombre}", antes=_que(fila), dia=dia,
                       campos=[campo("cantidad", "Cantidad", "numero", cantidad)], error=error)
    if all(nuevo[k] == fila[k] for k in ("nombre", "cantidad", "unidad", "kcal")):
        return no(f"{fila['nombre']} del {corta(dia)} ya es {_cuanto(fila)}")
    salida["nuevo"] = nuevo
    de = f", {_origen_corto(nuevo['origen'])}" if nuevo["origen"] else (", estimado" if nuevo["estimado"] else "")
    if normalizar(nuevo["nombre"]) != normalizar(fila["nombre"]):
        texto = f"Voy a cambiar en el {corta(dia)}: {_que(fila)} → {_que(nuevo)}{de}"
    else:
        texto = f"Voy a cambiar {fila['nombre']} del {corta(dia)}: {_cuanto(fila)} → {_cuanto(nuevo)}{de}"
    campos = [campo("cantidad", "Cantidad", "numero", nuevo["cantidad"])]
    if nuevo["estimado"] and nuevo["origen"] is None and nuevo["nombre"] != fila["nombre"]:
        campos.append(campo("kcal", "kcal (estimado)", "numero", nuevo["kcal"]))
    unidad = fila["unidad"] or "g"
    return tarjeta(accion="cambiar_alimento", parametros=salida, peticion=peticion, texto=texto, antes=_que(fila),
                   despues=_que(nuevo), dia=dia, donde=f"en Nutrición › Día, el {corta(dia)}; lo demás del día no cambia",
                   avisos=aviso_antes(parametros.get("antes_dicho"), fila["cantidad"],
                                      lambda n: f"{numero(n)} {unidad}"),
                   campos=campos)


def _aplicar_cambiar(parametros: dict, ctx: Contexto) -> str:
    """ONE write (G-40): name, quantity, unit and kcal with their origin, checked as `RegistroComidas.cambiar` does.

    `RegistroComidas.cambiar` drops the origin of new kcal (unit 016, kcal by hand); here they come from what is
    saved (or are Claude's estimate), so they keep it, in the same UPDATE."""
    nuevo, ident = parametros["nuevo"], parametros["id"]
    with closing(sqlite3.connect(ctx.registro.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            fila = RegistroComidas._leer(conn, ident)  # noqa: SLF001 - the same store and row as the screen by hand
            limpio = Alimento.desde_dict({**nuevo, "dia": fila["dia"]})
            if limpio.cantidad is not None and limpio.kcal is None:
                raise ValueError(f"«{limpio.nombre}» tiene cantidad pero no kcal")
            otro = normalizar(limpio.nombre) != normalizar(fila["nombre"])
            conn.execute(
                "UPDATE alimento_apuntado SET nombre = ?, cantidad = ?, unidad = ?, kcal = ?, estimado = ?, "
                "pendiente_cantidad = ?, origen = ?, provisional = ?, proteina_g = ?, hidratos_g = ?, grasa_g = ? "
                "WHERE id = ?",
                (limpio.nombre, limpio.cantidad, limpio.unidad, limpio.kcal, int(bool(nuevo["estimado"])),
                 int(limpio.pendiente_cantidad), nuevo["origen"], int(bool(nuevo["provisional"])),
                 None if otro else fila["proteina_g"], None if otro else fila["hidratos_g"],
                 None if otro else fila["grasa_g"], ident))
            guardado = RegistroComidas._fila(RegistroComidas._leer(conn, ident))  # noqa: SLF001
    return f"en el {corta(date.fromisoformat(guardado['dia']))} queda {_que(guardado)}"


# -- mover_alimento ---------------------------------------------------------------------------------------------------

def _proponer_mover(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    if not cambio.objeto:
        return no("No sé qué alimento quieres pasar a otro día")
    if cambio.fecha is None:
        return no(f"No sé a qué día quieres pasar {cambio.objeto}")
    parametros = {"destino": cambio.fecha.isoformat()}
    encontrados = _buscar(cambio, ctx, peticion, "mover_alimento", "pasar a otro día", parametros)
    if isinstance(encontrados, dict):
        return encontrados
    return _rehacer_mover({**parametros, "id": encontrados[0]["id"]}, {}, ctx, peticion)


def _rehacer_mover(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    fila = _fila_elegida(parametros, valores, ctx)
    if fila.get("tipo") == "no":
        return fila
    origen = date.fromisoformat(fila["dia"])
    error = None
    try:
        destino = fecha_de(valores.get("fecha", parametros["destino"]))
    except TarjetaMal:
        if "fecha" not in valores:
            raise
        destino, error = fecha_de(parametros["destino"]), "el día no es una fecha"
    if error is None and destino == origen:
        return no(f"{fila['nombre']} ya está en el {corta(origen)}")
    estado = "planeado" if destino > ctx.hoy() else "comido"  # the rule of `RegistroComidas.cambiar` (`_estado`)
    return tarjeta(accion="mover_alimento", parametros={"id": fila["id"], "destino": destino.isoformat()},
                   peticion=peticion,
                   texto=f"Voy a mover {_que(fila)} del {corta(origen)} al {corta(destino)}, como {estado}",
                   antes=corta(origen), despues=corta(destino), dia=origen,
                   donde=f"en Nutrición › Día: sale del {corta(origen)} y entra en el {corta(destino)}",
                   campos=[campo("fecha", "Pasar al día", "fecha", destino.isoformat())], error=error)


def _aplicar_mover(parametros: dict, ctx: Contexto) -> str:
    fila = ctx.registro.alimento(parametros["id"])
    movido = ctx.registro.cambiar(fila["id"], {"nombre": fila["nombre"], "cantidad": fila["cantidad"],
                                               "unidad": fila["unidad"], "kcal": fila["kcal"],
                                               "dia": parametros["destino"]}, hoy=ctx.hoy())
    return f"{movido['nombre']} está en el {corta(date.fromisoformat(movido['dia']))}, {movido['estado']}"


# -- niveles_dia, objetivo_dia ------------------------------------------------------------------------------------------

def _pct(n: float) -> str:
    """«−20», «0», «+10»: a level with its sign."""
    return "0" if n == 0 else f"{'+' if n > 0 else '−'}{numero(abs(n))}"


def _niveles_texto(niveles: list) -> str:
    return " / ".join(_pct(n) for n in niveles) + " %"


def _niveles_crudos(valor: Any) -> Any:
    """The levels as said or as corrected on the card («-25, -10»)."""
    if isinstance(valor, str):
        return [p.replace("%", "").strip() for p in re.split(r"[,;/]", valor) if p.replace("%", "").strip()]
    return valor


def _proponer_niveles(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    if not cambio.valores:
        return no("No sé qué niveles quieres para ese día")
    dia = cambio.dia or peticion.fecha
    return _rehacer_niveles({"dia": dia.isoformat(), "niveles": list(cambio.valores)}, {}, ctx, peticion)


def _rehacer_niveles(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    dia, error = _dia_corregido(parametros, valores)
    crudos = _niveles_crudos(valores.get("niveles", parametros["niveles"]))
    limpios: list | None = None
    try:
        limpios = niveles_validos(crudos)
    except ValueError as exc:
        error = error or str(exc)
    if dia < peticion.fecha:
        error = YA_PASO_PLAN
    objetivo = ctx.objetivos.del_dia(dia) or {}
    antes = sorted(objetivo.get("niveles") or NIVELES_POR_DEFECTO)
    if error is None and limpios == antes:
        return no(f"Los niveles del {corta(dia)} ya son {_niveles_texto(antes)}")
    nuevos = limpios if limpios is not None else (crudos if isinstance(crudos, list) else [])
    sufijo = ""
    if limpios and all(n < 0 for n in limpios):
        sufijo = " (déficit)"
    elif limpios and all(n > 0 for n in limpios):
        sufijo = " (superávit)"
    despues = _niveles_texto(limpios) if limpios is not None else ", ".join(str(n) for n in nuevos)
    return tarjeta(accion="niveles_dia", parametros={"dia": dia.isoformat(), "niveles": nuevos}, peticion=peticion,
                   texto=f"Voy a cambiar los niveles del {corta(dia)}: {_niveles_texto(antes)} → {despues}{sufijo}",
                   antes=_niveles_texto(antes), despues=despues, dia=dia,
                   donde="solo ese día, en sus márgenes de Hoy y Nutrición › Día",
                   campos=[campo("niveles", "Niveles (%)", "texto", ", ".join(numero(n) if isinstance(n, (int, float))
                                                                             else str(n) for n in nuevos)),
                           campo("dia", "Día", "fecha", dia.isoformat())],
                   error=error)


def _aplicar_niveles(parametros: dict, ctx: Contexto) -> str:
    dia = fecha_de(parametros["dia"])
    guardado = ctx.objetivos.cambiar(dia, niveles=parametros["niveles"])  # the same method as «Ajustar el día»
    return f"los niveles del {corta(dia)} son {_niveles_texto(guardado['niveles'])}"


def _pct_objetivo(n: float) -> str:
    return f"{_pct(n)} %"


def _proponer_objetivo(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    if cambio.valor is None:
        return no("No sé qué % quieres para ese día")
    dia = cambio.dia or peticion.fecha
    return _rehacer_objetivo({"dia": dia.isoformat(), "porcentaje": cambio.valor}, {}, ctx, peticion)


def _rehacer_objetivo(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    dia, error = _dia_corregido(parametros, valores)
    crudo = valores.get("porcentaje", parametros["porcentaje"])
    porcentaje: Any = crudo
    try:
        porcentaje = _porcentaje(crudo, "el % del objetivo del día")
        if porcentaje is None:
            porcentaje, error = crudo, error or "falta el % del objetivo del día"
    except ValueError as exc:
        error = error or str(exc)
    if dia < peticion.fecha:
        error = YA_PASO_PLAN
    vigentes = ctx.ajustes.vigentes(dia)
    antes = objetivo_del_dia(ctx.objetivos.del_dia(dia), plan_del_dia(vigentes))["porcentaje"]
    if error is None and porcentaje == antes:
        return no(f"El objetivo del {corta(dia)} ya es {_pct_objetivo(antes)}")
    avisos = []
    if error is None:
        retraso = _retraso(ctx.ajustes, dia, vigentes, porcentaje)  # the same as the sheet «Cambiar %» (R-18)
        if retraso is not None and retraso["dias"] != 0:
            avisos.append(retraso["texto"].replace("al objetivo", "a tu objetivo de peso", 1))
    despues = _pct_objetivo(porcentaje) if error is None else str(porcentaje)
    return tarjeta(accion="objetivo_dia", parametros={"dia": dia.isoformat(), "porcentaje": porcentaje},
                   peticion=peticion, texto=f"Voy a cambiar el objetivo del {corta(dia)}: {_pct_objetivo(antes)} → {despues}",
                   antes=_pct_objetivo(antes), despues=despues, dia=dia,
                   donde="solo ese día; el plan de Ajustes no cambia", avisos=avisos,
                   campos=[campo("porcentaje", "Objetivo (%)", "numero", porcentaje),
                           campo("dia", "Día", "fecha", dia.isoformat())],
                   error=error)


def _aplicar_objetivo(parametros: dict, ctx: Contexto) -> str:
    dia = fecha_de(parametros["dia"])
    guardado = ctx.objetivos.cambiar(dia, porcentaje=parametros["porcentaje"])  # as «Cambiar %»
    return f"el objetivo del {corta(dia)} es {_pct_objetivo(guardado['porcentaje'])}"


# -- entreno_previsto -------------------------------------------------------------------------------------------------

def _actividad_corta(a: dict) -> dict:
    """The activity as the planned training keeps it (the same fields as `web/app.py` `_actividad_corta`)."""
    return {"actividad_id": a.get("activity_id"), "fecha": a.get("activity_date"),
            "nombre": a.get("name") or a.get("activity_type"), "tipo": a.get("activity_type")}


def _parecida(cambio: Any, ctx: Contexto) -> dict | None:
    """The activity of the last 14 days like the one he names (its kind and/or its day); the latest if several."""
    candidatas = list(ctx.recientes())
    if cambio.fecha is not None:
        candidatas = [a for a in candidatas if a.get("activity_date") == cambio.fecha.isoformat()]
    buscado = normalizar(cambio.objeto)
    if buscado:
        def parece(a: dict) -> bool:
            nombres = [normalizar(a.get("name")), normalizar(a.get("activity_type"))]
            return any(n and (buscado in n or n in buscado) for n in nombres)
        candidatas = [a for a in candidatas if parece(a)]
    elif cambio.fecha is None:
        return None
    return candidatas[-1] if candidatas else None


def _previsto_texto(previsto: dict | None) -> str:
    if not previsto:
        return "ninguno"
    return f"{numero(previsto['minutos'])} min de {previsto.get('nombre') or 'entreno'} ({numero(round(previsto['kcal']))} kcal)"


def _proponer_entreno(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    if cambio.cantidad is None:
        return no("No sé cuántos minutos quieres de entreno previsto")
    actividad = _parecida(cambio, ctx)
    if actividad is None:
        return no(NO_ENCUENTRO)
    dia = cambio.dia or peticion.fecha
    return _rehacer_entreno({"dia": dia.isoformat(), "actividad_id": actividad.get("activity_id"),
                             "minutos": cambio.cantidad}, {}, ctx, peticion)


def _rehacer_entreno(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    dia, error = _dia_corregido(parametros, valores)
    actividad = next((a for a in ctx.recientes() if a.get("activity_id") == parametros["actividad_id"]), None)
    if actividad is None:
        return no(NO_ENCUENTRO)
    minutos = _como_numero(valores.get("minutos", parametros["minutos"]))
    kcal = 0.0
    try:
        kcal = kcal_previstas(actividad, minutos)
    except ValueError as exc:
        error = error or str(exc)
    if dia < peticion.fecha:
        error = YA_PASO_PLAN
    previsto = {**_actividad_corta(actividad), "minutos": minutos, "kcal": kcal}
    antes = (ctx.objetivos.del_dia(dia) or {}).get("previsto")
    if error is None and antes and antes.get("actividad_id") == previsto["actividad_id"] \
            and antes.get("minutos") == minutos and antes.get("kcal") == kcal:
        return no(f"El entreno previsto del {corta(dia)} ya es {_previsto_texto(antes)}")
    de = corta(date.fromisoformat(actividad["activity_date"])) if actividad.get("activity_date") else "reciente"
    despues = _previsto_texto(previsto) if error is None else f"{minutos} min de {previsto['nombre']}"
    texto = (f"Voy a poner de entreno previsto del {corta(dia)}: {numero(minutos) if error is None else minutos} min "
             f"de {previsto['nombre']} ({numero(round(kcal))} kcal, estimadas desde el del {de})")
    return tarjeta(accion="entreno_previsto",
                   parametros={"dia": dia.isoformat(), "actividad_id": previsto["actividad_id"], "minutos": minutos,
                               "previsto": previsto},
                   peticion=peticion, texto=texto, antes=_previsto_texto(antes), despues=despues, dia=dia,
                   donde="solo ese día; suma al gasto estimado", avisos=[] if not antes else
                   [f"sustituye al previsto que tenías: {_previsto_texto(antes)}"],
                   lineas=_efecto(ctx, dia, previsto=previsto) if error is None else [],
                   campos=[campo("minutos", "Minutos", "numero", minutos), campo("dia", "Día", "fecha", dia.isoformat())],
                   error=error)


def _aplicar_entreno(parametros: dict, ctx: Contexto) -> str:
    dia = fecha_de(parametros["dia"])
    guardado = ctx.objetivos.cambiar(dia, previsto=parametros["previsto"])  # as choosing it on the screen
    return f"el entreno previsto del {corta(dia)} es {_previsto_texto(guardado['previsto'])}"


ACCIONES = [
    Accion("pasos_dia", "cambiar los pasos previstos de hoy o de un día que aún no ha llegado. valor: los pasos "
           "nuevos, como número entero; dia: el día del que habla, o null; antes_dicho: los de antes, solo si los dice.",
           _proponer_pasos, _rehacer_pasos, _aplicar_pasos),
    Accion("quitar_alimento", "quitar un alimento apuntado. objeto: su nombre, como lo dice o como está en lo "
           "apuntado; dia: el día en que está apuntado si lo dice, o null.",
           _proponer_quitar, _rehacer_quitar, _aplicar_quitar),
    Accion("cambiar_alimento", "sustituir un alimento apuntado por otro («quita el yogur, al final fue una manzana») "
           "o corregir su cantidad («la pasta eran tantos gramos»), en un solo cambio. objeto: el apuntado, como lo "
           "dice; texto: lo que entra en su lugar si lo sustituye, o null si solo cambia la cantidad; cantidad y "
           "unidad: la cantidad nueva si la dice; kcal_estimadas: solo si lo que entra es nuevo; dia: el día en que "
           "está apuntado si lo dice, o null; antes_dicho: la cantidad de antes, solo si la dice.",
           _proponer_cambiar, _rehacer_cambiar, _aplicar_cambiar),
    Accion("mover_alimento", "pasar un alimento apuntado a otro día («el plátano pásalo a mañana»). objeto: su "
           "nombre; dia: el día en que está apuntado si lo dice, o null; fecha: el día al que lo pasa.",
           _proponer_mover, _rehacer_mover, _aplicar_mover),
    Accion("niveles_dia", "cambiar los niveles del margen de hoy o de un día que aún no ha llegado (como mucho "
           "cinco). valores: los % de cada nivel con su signo, negativo si es déficit y positivo si es superávit; "
           "dia: el día del que habla, o null.",
           _proponer_niveles, _rehacer_niveles, _aplicar_niveles),
    Accion("objetivo_dia", "cambiar el % del objetivo de hoy o de un día que aún no ha llegado («hoy "
           "mantenimiento»). valor: el % con su signo, negativo si es déficit, cero si es mantenimiento; dia: el día "
           "del que habla, o null.",
           _proponer_objetivo, _rehacer_objetivo, _aplicar_objetivo),
    Accion("entreno_previsto", "poner el entreno previsto de hoy o de un día que aún no ha llegado como uno de los "
           "últimos días («hoy además tantos minutos de bici como el del domingo»). objeto: qué entreno es, como lo "
           "dice; cantidad: los minutos; fecha: el día del entreno parecido si lo dice; dia: el día del que habla, o "
           "null.",
           _proponer_entreno, _rehacer_entreno, _aplicar_entreno),
]
