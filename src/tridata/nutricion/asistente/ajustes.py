"""The actions on the settings and the weight (unit 022, REC-15 of `pedir-al-asistente`).

`cambiar_ajuste` (R-86, R-87): one key of `Ajustes` (`CLAVES`) with the date from which it applies, «desde hoy» by
default and correctable on the card (field `desde`). The check and its words are the ones of the Ajustes screen
(`_validar`); before = what is in force that date. A date before today says which past days are evaluated with the
new value and that what was eaten does not change. Applied with `Ajustes.cambiar({clave: valor}, desde)`.
`cambiar_carrera` (R-86): a race found by its name (without case or accents), applied with `Ajustes.cambiar_carrera`.
`peso_dia` (R-88): CORRECT the weight of a day («ayer pesé 71,3, no 71,8»), applied with `RegistroPeso.apuntar`
(one per day, the last one wins, never a day to come). A weight said without correcting («peso 72,4») is still the
piece «Peso del día» of the review, not a card.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..ajustes import CLAVES, _carrera_valida, _validar
from ..peso import media_7_dias, peso_valido
from ..recetas import normalizar
from . import Accion, Contexto, Peticion, TarjetaMal, campo, corta, elegir, fecha_de, no, numero, tarjeta
from .dia import aviso_antes, cifra_dicha

# key -> (how the card names it, unit after the value)
_NOMBRES: dict[str, tuple[str, str]] = {
    "edad": ("Edad", "años"),
    "altura_cm": ("Altura", "cm"),
    "pasos_por_defecto": ("Pasos por defecto", ""),
    "basal_kcal": ("Basal", "kcal"),
    "kcal_por_paso": ("Kcal por paso", ""),
    "limite_saludable_pct": ("Límite saludable", "%"),
    "objetivo_pct": ("Objetivo del plan", "%"),
    "objetivo_peso": ("Objetivo de peso", ""),
}


def _entero_si_puede(valor: Any) -> Any:
    """300.0 → 300, so the words of Ajustes say «(no 300)»."""
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    return valor


def _como_numero(valor: Any) -> Any:
    if isinstance(valor, str):
        try:
            return _entero_si_puede(float(valor.strip().replace(",", ".")))
        except ValueError:
            return valor
    return _entero_si_puede(valor)


def _con_unidad(n: Any, unidad: str) -> str:
    return f"{numero(n)} {unidad}".strip()


def _valor_texto(clave: str, valor: Any) -> str:
    """«25 %», «175 cm», «72 kg (2027-02-01)»; «sin poner» when it was never set."""
    if valor is None:
        return "sin poner"
    if clave == "objetivo_peso":
        return f"{numero(valor['kg'])} kg ({valor['fecha']})"
    return _con_unidad(valor, _NOMBRES[clave][1])


def _vigencia(desde: date, peticion: Peticion) -> str:
    return f"vale desde hoy, {corta(desde)}" if desde == peticion.fecha else f"desde {desde.isoformat()}"


def _aviso_pasado(desde: date, peticion: Peticion, con: str) -> list[str]:
    """R-87: the past days evaluated with the new value; what was eaten does not change."""
    if desde >= peticion.fecha:
        return []
    ayer = peticion.fecha - timedelta(days=1)
    if desde == ayer:
        return [f"el día {corta(desde)} se evaluará con {con}; lo comido no cambia"]
    del_ = str(desde.day) if (desde.month, desde.year) == (ayer.month, ayer.year) else corta(desde)
    return [f"los días del {del_} al {corta(ayer)} se evaluarán con {con}; lo comido no cambia"]


# -- cambiar_ajuste -------------------------------------------------------------------------------------------------

def _proponer_ajuste(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    clave = cambio.campo
    if clave not in CLAVES:
        return no("No sé qué ajuste quieres cambiar: hazlo en Ajustes")
    if cambio.valor is None and not (clave == "objetivo_peso" and cambio.fecha is not None):
        return no(f"No sé qué valor quieres para {_NOMBRES[clave][0].lower()}")
    desde = cambio.desde or peticion.fecha
    parametros = {"clave": clave, "valor": cambio.valor, "desde": desde.isoformat()}
    if clave == "objetivo_peso":
        parametros["fecha_objetivo"] = cambio.fecha.isoformat() if cambio.fecha else None
    return _rehacer_ajuste(parametros, {}, ctx, peticion)


def _rehacer_ajuste(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    clave = parametros.get("clave")
    if clave not in CLAVES:
        raise TarjetaMal("la tarjeta no dice qué ajuste")
    error = None
    try:
        desde = fecha_de(valores.get("desde", parametros["desde"]))
    except TarjetaMal:
        if "desde" not in valores:
            raise
        desde, error = fecha_de(parametros["desde"]), "la fecha desde la que vale no es una fecha"
    antes = ctx.ajustes.valores_con_fecha(desde)[clave]["valor"]
    valor = _como_numero(valores.get("valor", parametros["valor"]))
    if clave == "objetivo_peso":
        fecha_objetivo = valores.get("fecha_objetivo", parametros.get("fecha_objetivo"))
        dato: Any = {"kg": antes["kg"] if valor is None and antes else valor,
                     "fecha": fecha_objetivo or (antes["fecha"] if antes else None)}
    else:
        dato = valor
    nombre, unidad = _NOMBRES[clave]
    limpio: Any = None
    try:
        limpio = _validar(clave, dato, desde)
    except ValueError as exc:
        error = error or str(exc)
    if error is None and antes is not None and (
            limpio == antes if clave == "objetivo_peso" else float(limpio) == float(antes)):
        return no(f"{nombre}: ya es {_valor_texto(clave, antes)} {_vigencia(desde, peticion)}")
    despues = _valor_texto(clave, limpio) if error is None else str(dato)
    antes_corto = _valor_texto(clave, antes) if clave == "objetivo_peso" or antes is None else numero(antes)
    cambio = f"{nombre}: {antes_corto} → {despues}"
    con = (f"el objetivo de {despues}" if clave == "objetivo_peso" else
           f"el {despues}" if unidad == "%" else f"{nombre.lower()} {despues}")
    campos = [campo("valor", f"{nombre} ({unidad})" if unidad else ("kg" if clave == "objetivo_peso" else nombre),
                    "numero", dato["kg"] if clave == "objetivo_peso" else valor)]
    if clave == "objetivo_peso":
        campos.append(campo("fecha_objetivo", "Para el", "fecha", dato["fecha"]))
    campos.append(campo("desde", "Vale desde", "fecha", desde.isoformat()))
    salida = {**parametros, "valor": dato["kg"] if clave == "objetivo_peso" else valor, "desde": desde.isoformat()}
    if clave == "objetivo_peso":
        salida["fecha_objetivo"] = dato["fecha"]
    return tarjeta(accion="cambiar_ajuste", parametros=salida, peticion=peticion,
                   texto=f"Voy a cambiar en Ajustes — {cambio} · {_vigencia(desde, peticion)}",
                   antes=_valor_texto(clave, antes), despues=despues, desde=desde,
                   donde=f"en Ajustes, {_vigencia(desde, peticion)}; lo de antes de esa fecha sigue igual",
                   avisos=_aviso_pasado(desde, peticion, con) if error is None else [], campos=campos, error=error)


def _aplicar_ajuste(parametros: dict, ctx: Contexto) -> str:
    clave, desde = parametros["clave"], fecha_de(parametros["desde"])
    dato = ({"kg": parametros["valor"], "fecha": parametros["fecha_objetivo"]} if clave == "objetivo_peso"
            else parametros["valor"])
    guardado = ctx.ajustes.cambiar({clave: dato}, desde)  # the same method as the Ajustes screen
    nuevo = ctx.ajustes.valores_con_fecha(desde)[clave]["valor"]
    hecho = f"{_NOMBRES[clave][0].lower()} es {_valor_texto(clave, nuevo)} desde el {corta(desde)}"
    if guardado.get("preguntar_limite"):
        hecho += "; ponle un límite saludable en Ajustes"
    return hecho


# -- cambiar_carrera ------------------------------------------------------------------------------------------------

def _capital(texto: str) -> str:
    return texto[:1].upper() + texto[1:]


def _proponer_carrera(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    if not cambio.objeto:
        return no("No sé qué carrera quieres cambiar")
    if cambio.fecha is None and cambio.valor is None:
        return no(f"No sé qué quieres cambiar de {cambio.objeto}")
    buscado = normalizar(cambio.objeto)
    carreras = ctx.ajustes.carreras()
    encontradas = ([c for c in carreras if normalizar(c.nombre) == buscado]
                   or [c for c in carreras if buscado and buscado in normalizar(c.nombre)])
    if not encontradas:
        return no(f"No tienes ninguna carrera «{cambio.objeto}» en Ajustes")
    parametros = {"fecha": cambio.fecha.isoformat() if cambio.fecha else None, "dias": cambio.valor}
    if cambio.antes_dicho:
        parametros["antes_dicho"] = cambio.antes_dicho
    if len(encontradas) > 1:
        return elegir(accion="cambiar_carrera", parametros={**parametros, "candidatos": [c.id for c in encontradas]},
                      peticion=peticion, texto="¿Cuál de tus carreras?",
                      candidatos=[{"eleccion": c.id, "texto": f"{c.nombre}, {c.fecha.isoformat()}"} for c in encontradas])
    return _rehacer_carrera({**parametros, "id": encontradas[0].id}, {}, ctx, peticion)


def _rehacer_carrera(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    ident = parametros.get("id")
    if "eleccion" in valores:
        if valores["eleccion"] not in parametros.get("candidatos", []):
            return no("Elige una de las que te he enseñado")
        ident = valores["eleccion"]
    if isinstance(ident, bool) or not isinstance(ident, int):
        raise TarjetaMal("la tarjeta no dice qué carrera")
    carrera = next((c for c in ctx.ajustes.carreras() if c.id == ident), None)
    if carrera is None:
        return no("Esa carrera ya no está en Ajustes.")
    fecha = valores.get("fecha", parametros.get("fecha")) or carrera.fecha.isoformat()
    dias = _como_numero(valores.get("dias", parametros.get("dias")))
    dias = carrera.dias_mantenimiento if dias is None else dias
    error = None
    try:
        _, nueva, dias_ok = _carrera_valida(carrera.nombre, fecha, dias)
    except ValueError as exc:
        error, nueva, dias_ok = str(exc), carrera.fecha, carrera.dias_mantenimiento
    if error is None and nueva == carrera.fecha and dias_ok == carrera.dias_mantenimiento:
        return no(f"{_capital(carrera.nombre)} ya es el {carrera.fecha.isoformat()}")
    partes = []
    if error is not None or nueva != carrera.fecha:
        partes.append(f"{carrera.fecha.isoformat()} → {fecha if error else nueva.isoformat()}")
    if error is None and dias_ok != carrera.dias_mantenimiento:
        partes.append(f"{carrera.dias_mantenimiento} → {dias_ok} días de mantenimiento antes")
    avisos = []
    dicho = cifra_dicha(parametros.get("antes_dicho"))
    if dicho is not None and dicho.is_integer() and int(dicho) != carrera.fecha.day and int(dicho) != carrera.dias_mantenimiento:
        avisos.append(f"tenías el {corta(carrera.fecha)}, no el {int(dicho)}")
    return tarjeta(accion="cambiar_carrera",
                   parametros={"id": carrera.id, "fecha": str(fecha), "dias": dias,
                               **({"antes_dicho": parametros["antes_dicho"]} if parametros.get("antes_dicho") else {})},
                   peticion=peticion,
                   texto=f"Voy a cambiar en Ajustes — {_capital(carrera.nombre)}: {'; '.join(partes)} · "
                         f"{_vigencia(peticion.fecha, peticion)}",
                   antes=carrera.fecha.isoformat(), despues=nueva.isoformat(), desde=peticion.fecha,
                   donde="en Ajustes › Carreras, y en el plan de esos días", avisos=avisos,
                   campos=[campo("fecha", "Fecha de la carrera", "fecha", str(fecha)),
                           campo("dias", "Días de mantenimiento antes", "numero", dias)],
                   error=error)


def _aplicar_carrera(parametros: dict, ctx: Contexto) -> str:
    carrera = next(c for c in ctx.ajustes.carreras() if c.id == parametros["id"])
    guardada = ctx.ajustes.cambiar_carrera(carrera.id, carrera.nombre, parametros["fecha"], parametros["dias"])
    return f"{guardada['nombre']} es el {guardada['fecha']}"


# -- peso_dia -------------------------------------------------------------------------------------------------------

def _kg(n: float) -> str:
    return f"{numero(n)} kg"


def _proponer_peso(cambio: Any, ctx: Contexto, peticion: Peticion) -> dict:
    if cambio.valor is None:
        return no("No sé qué peso quieres apuntar")
    dia = cambio.dia or peticion.fecha
    parametros = {"dia": dia.isoformat(), "kg": cambio.valor}
    if cambio.antes_dicho:
        parametros["antes_dicho"] = cambio.antes_dicho
    return _rehacer_peso(parametros, {}, ctx, peticion)


def _rehacer_peso(parametros: dict, valores: dict, ctx: Contexto, peticion: Peticion) -> dict:
    error = None
    try:
        dia = fecha_de(valores.get("dia", parametros["dia"]))
    except TarjetaMal:
        if "dia" not in valores:
            raise
        dia, error = fecha_de(parametros["dia"]), "el día no es una fecha"
    crudo = _como_numero(valores.get("kg", parametros["kg"]))
    kg: Any = crudo
    try:
        _, kg, _ = peso_valido({"dia": dia, "kg": crudo}, peticion.fecha)  # the day it was said (G-41)
    except ValueError as exc:
        error = error or str(exc)
    guardado = ctx.pesos.del_dia(dia)
    antes = guardado["kg"] if guardado else None
    if error is None and antes is not None and float(antes) == float(kg):
        return no(f"El peso del {corta(dia)} ya es {_kg(antes)}")
    despues = _kg(kg) if error is None else str(crudo)
    if antes is None:
        texto = f"Voy a apuntar el peso del {corta(dia)}: {despues}"
    else:
        texto = f"Voy a corregir el peso del {corta(dia)}: {numero(antes)} → {despues}"
    lineas = []
    if error is None:
        ventana = [p for p in ctx.pesos.entre(dia - timedelta(days=6), dia) if p["dia"] != dia.isoformat()]
        m0 = media_7_dias(ctx.pesos.entre(dia - timedelta(days=6), dia), dia)
        m1 = media_7_dias([*ventana, {"dia": dia.isoformat(), "kg": kg}], dia)
        if m1 is not None and (m0 is None or m0.kg != m1.kg):
            lineas.append(f"Media de 7 días del {corta(dia)}: {_kg(m0.kg) if m0 else 'sin media'} → {_kg(m1.kg)}")
    return tarjeta(accion="peso_dia", parametros={**parametros, "dia": dia.isoformat(), "kg": kg}, peticion=peticion,
                   texto=texto, antes=_kg(antes) if antes is not None else None, despues=despues, dia=dia,
                   donde=f"un solo peso el {corta(dia)}: el nuevo sustituye al que había",
                   avisos=aviso_antes(parametros.get("antes_dicho"), antes, _kg) if error is None else [],
                   lineas=lineas,
                   campos=[campo("kg", "Peso (kg)", "numero", kg), campo("dia", "Día", "fecha", dia.isoformat())],
                   error=error)


def _aplicar_peso(parametros: dict, ctx: Contexto) -> str:
    dia = fecha_de(parametros["dia"])
    antes = ctx.pesos.del_dia(dia)
    guardado = ctx.pesos.apuntar(dia, parametros["kg"], antes["nota"] if antes else None, hoy=ctx.hoy())
    return f"el peso del {corta(dia)} es {_kg(guardado['kg'])}"


ACCIONES: list[Accion] = [
    Accion("cambiar_ajuste", "cambiar un ajuste con la fecha desde la que vale («desde el lunes el límite es "
           "tanto», «mi objetivo es tantos kilos para tal día»). campo: uno de " + ", ".join(CLAVES) + "; valor: el "
           "valor nuevo (en objetivo_peso, los kg); fecha: solo en objetivo_peso, el día para el que lo quiere; "
           "desde: el día desde el que vale si lo dice, o null.",
           _proponer_ajuste, _rehacer_ajuste, _aplicar_ajuste),
    Accion("cambiar_carrera", "cambiar la fecha o los días de mantenimiento de una carrera guardada. objeto: el "
           "nombre de la carrera; fecha: su fecha nueva, o null; valor: los días de mantenimiento antes, solo si los "
           "dice; antes_dicho: la fecha de antes, solo si la dice.",
           _proponer_carrera, _rehacer_carrera, _aplicar_carrera),
    Accion("peso_dia", "corregir el peso de un día ya apuntado («ayer pesé tanto, no tanto», «corrige el peso del "
           "lunes»). Un peso dicho sin corregir nada va en la pieza peso, no aquí. valor: los kg nuevos; dia: el día "
           "del peso, o null si es hoy; antes_dicho: el peso de antes, solo si lo dice.",
           _proponer_peso, _rehacer_peso, _aplicar_peso),
]
