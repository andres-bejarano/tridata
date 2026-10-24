"""The actions on a day (unit 021): the planned steps of today or a future day, and removing a logged food.

`pasos_dia` (R-76, the steps part): before = the steps set for that day or the default ones of Ajustes; the check is
the one of the screen of the day (`pasos_validos`); its effect on the expenditure and on the maintenance margin is
the day recomputed as `GET /api/dia` does. A past day is not changed: Garmin brings its steps. Unit 022 adds the
other actions of the day here (levels, %, planned training, change or move a food).
`quitar_alimento` (R-74, the removing part): the food of the day of the request (or the day said), found by name;
the card is the confirmation (G-37) and it is removed with `RegistroComidas.borrar`, as the screen by hand does.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from ..margen import PASOS_POR_DEFECTO
from ..objetivo_dia import pasos_validos
from . import (
    Accion, Contexto, Peticion, TarjetaMal, buscar_apuntado, campo, corta, descripcion_alimento, elegir, fecha_de, no,
    numero, tarjeta,
)

YA_PASO = "ese día ya pasó: sus pasos los trae Garmin"
NO_ESTA = "Ese alimento ya no está apuntado."


# -- pasos_dia ------------------------------------------------------------------------------------------------------

class _ConPasos:
    """The objectives of the day as they would be with other planned steps on `dia` (to show the effect)."""

    def __init__(self, objetivos: Any, dia: date, pasos: int) -> None:
        self._objetivos, self._dia, self._pasos = objetivos, dia, pasos

    def del_dia(self, dia: date) -> dict | None:
        actual = self._objetivos.del_dia(dia)
        if dia != self._dia:
            return actual
        base = actual or {"dia": dia.isoformat(), "tipo": None, "porcentaje": None, "niveles": None, "entreno": None,
                          "pasos": None, "previsto": None, "repartido": False, "creado": None}
        return {**base, "pasos": self._pasos}

    def __getattr__(self, nombre: str) -> Any:
        return getattr(self._objetivos, nombre)


def _por_defecto(ctx: Contexto, dia: date) -> int:
    valor = ctx.ajustes.vigentes(dia).pasos_por_defecto
    return PASOS_POR_DEFECTO if valor is None else int(valor)


def _pasos_de(ctx: Contexto, dia: date) -> int:
    objetivo = ctx.objetivos.del_dia(dia)
    return int(objetivo["pasos"]) if objetivo and objetivo.get("pasos") is not None else _por_defecto(ctx, dia)


def _mantenimiento(resumen: dict) -> float | None:
    return next((m["kcal"] for m in resumen.get("margenes", []) if m.get("nivel") == 0), None)


def _efecto(ctx: Contexto, dia: date, pasos: int) -> list[str]:
    """The expenditure and the maintenance margin before and after, computed on the PC (R-67, R-76)."""
    antes = ctx.resumen(dia)
    despues = ctx.resumen(dia, objetivos=_ConPasos(ctx.objetivos, dia, pasos))
    if antes is None or despues is None:
        return []
    lineas = [f"Gasto estimado del {corta(dia)}: {numero(round(antes['gasto']['kcal']))} → "
              f"{numero(round(despues['gasto']['kcal']))} kcal"]
    m0, m1 = _mantenimiento(antes), _mantenimiento(despues)
    if m0 is not None and m1 is not None and round(m1 - m0):
        diferencia = round(m1 - m0)
        lineas.append(f"En mantenimiento te quedan {numero(abs(diferencia))} kcal {'más' if diferencia > 0 else 'menos'}")
    return lineas


def _proponer_pasos(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    if cambio.valor is None:
        return no("No sé cuántos pasos quieres para ese día")
    dia = cambio.dia or peticion.fecha
    return _rehacer_pasos({"dia": dia.isoformat(), "pasos": cambio.valor}, {}, ctx, peticion)


def _rehacer_pasos(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    error = None
    try:
        dia = fecha_de(valores.get("dia", parametros["dia"]))
    except TarjetaMal:
        if "dia" not in valores:
            raise
        dia, error = fecha_de(parametros["dia"]), "el día no es una fecha"
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
        accion="pasos_dia", parametros={"dia": dia.isoformat(), "pasos": pasos}, peticion=peticion,
        texto=f"Voy a cambiar los pasos previstos del {corta(dia)}: {numero(antes)} → {numero(pasos)}",
        antes=numero(antes), despues=numero(pasos), dia=dia,
        donde=f"solo ese día, los pasos por defecto siguen en {numero(defecto)}",
        lineas=_efecto(ctx, dia, pasos) if error is None else [],
        campos=[campo("pasos", "Pasos previstos", "numero", pasos), campo("dia", "Día", "fecha", dia.isoformat())],
        error=error)


def _aplicar_pasos(parametros: dict, ctx: Contexto) -> str:
    dia = fecha_de(parametros["dia"])
    guardado = ctx.objetivos.cambiar(dia, pasos=parametros["pasos"])  # the same method as the screen by hand
    return f"los pasos previstos del {corta(dia)} son {numero(guardado['pasos'])}"


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
    dia = cambio.dia or peticion.fecha
    encontrados = buscar_apuntado(ctx, dia, cambio.objeto)
    if not encontrados:
        cuando = "Hoy" if dia == ctx.hoy() else f"El {corta(dia)}"
        return no(f"{cuando} no tienes ningún {cambio.objeto} apuntado")
    if len(encontrados) == 1:
        return _tarjeta_quitar(encontrados[0], peticion)
    return elegir(accion="quitar_alimento", parametros={"dia": dia.isoformat(),
                                                        "candidatos": [a["id"] for a in encontrados]},
                  peticion=peticion, texto=f"¿Cuál quieres quitar del {corta(dia)}?",
                  candidatos=[{"eleccion": a["id"], "texto": descripcion_alimento(a)} for a in encontrados])


def _rehacer_quitar(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    ident = parametros.get("id")
    if "eleccion" in valores:
        if valores["eleccion"] not in parametros.get("candidatos", []):
            return no("Elige uno de los que te he enseñado")
        ident = valores["eleccion"]
    if isinstance(ident, bool) or not isinstance(ident, int):
        raise TarjetaMal("la tarjeta no dice qué alimento quitar")
    try:
        fila = ctx.registro.alimento(ident)
    except KeyError:
        return no(NO_ESTA)
    return _tarjeta_quitar(fila, peticion)


def _aplicar_quitar(parametros: dict, ctx: Contexto) -> str:
    borrado = ctx.registro.borrar(parametros["id"])  # the card was the confirmation (G-37, R-11)
    return f"he quitado {borrado['nombre']} del {corta(date.fromisoformat(borrado['dia']))}"


ACCIONES = [
    Accion("pasos_dia", "cambiar los pasos previstos de hoy o de un día que aún no ha llegado. valor: los pasos "
           "nuevos, como número entero; dia: el día del que habla, o null; antes_dicho: los de antes, solo si los dice.",
           _proponer_pasos, _rehacer_pasos, _aplicar_pasos),
    Accion("quitar_alimento", "quitar un alimento apuntado. objeto: su nombre, como lo dice o como está en lo "
           "apuntado; dia: el día en que está apuntado si lo dice, o null.",
           _proponer_quitar, _rehacer_quitar, _aplicar_quitar),
]
