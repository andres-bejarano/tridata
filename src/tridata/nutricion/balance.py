"""The Balance tab (unit 017): the week from Monday to Sunday, the weight-and-deficit chart, the list of weeks
and the proposal to spread what is above the objective (`cerrar-dia` «Historial y gráfica», R-24, R-25;
`consultar-margen` R-19, R-20, C-20, G-22).

It only ADDS UP what `nutricion.dia.resumen_completo` gives day by day: `resumen` is a function
`fecha -> resumen_completo | None` (None when that day lacks settings). Nothing is stored and nothing is
recomputed apart (G-18): eaten, expenditure (real if closed with Garmin, else estimated), balance and the
week mean of unit 014 come from that summary. Only totals leave here: no food, no names (Q-3).

What counts in the sum and the mean: the days with the food closed (R-24). What is spread: Σ (eaten −
objective of the day in kcal) of those days, over the days that are left this week (today if its food is
still open, and the next ones up to Sunday), never the maintenance days before a race and never past the
healthy limit (−50 % without one). Nothing ever passes to the next week (R-19).

Unit 018: only what is still pending is spread again. Already spread = Σ (objective with the plan's % −
objective now) of the days of the week whose % came from «Aplicar» (`repartido`); pending = what is above −
already spread. «Volver al plan» and the spread never touch the maintenance days before a race.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from datetime import date, timedelta
from typing import Any

from .ajustes import Ajustes
from .margen import kcal_enteras
from .peso import media_7_dias

BARRAS_POR_DIA_MAX = 91  # a range longer than this is drawn with one bar per week
TOPE_SIN_LIMITE = 50  # without a healthy limit, the range of the objectives (−50..+50 %)
CUENTAN = ("comida_cerrada", "cerrado_garmin")
NADA = "Vas en tu objetivo o por debajo esta semana: no hay nada que repartir"
SIN_DIAS = "No quedan días esta semana para repartirlo; la semana que viene empieza de cero"
YA_REPARTIDO = "Lo que llevas por encima ya está repartido en los días que quedan."
REDONDEO_KCAL = 10  # unit 018: less than this pending after a spread is its rounding to less deficit, not new kcal
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

Resumen = Callable[[date], "Mapping[str, Any] | None"]


def lunes_de(dia: date) -> date:
    return dia - timedelta(days=dia.weekday())


# -- one day, one week ----------------------------------------------------------------------------

def fila_del_dia(resumen: Mapping[str, Any]) -> dict:
    """Only the totals of a day: eaten, expenditure and where it comes from, balance, objective. No food."""
    cierre = resumen["cierre"]
    objetivo = resumen["objetivo"]
    return {
        "dia": resumen["fecha"], "estado": cierre["estado"], "comido_kcal": resumen["total_kcal"],
        "gasto_kcal": resumen["gasto"]["kcal"], "gasto_origen": resumen["gasto"]["origen"],
        "balance_kcal": cierre["balance_kcal"], "balance_pct": cierre["balance_pct"],
        "objetivo_pct": objetivo["porcentaje"], "objetivo_kcal": objetivo["kcal"],
        "objetivo_origen": objetivo["origen"], "motivo": (resumen.get("plan") or {}).get("motivo"),
        "cuenta": cierre["estado"] in CUENTAN,
        **({"repartido": True} if objetivo.get("repartido") else {}),  # unit 018: only the days of the spread
    }


def _ya_repartido(fila: Mapping[str, Any], resumen: Mapping[str, Any] | None) -> float:
    """What the spread took off this day: (objective with the plan's % − objective now) in kcal."""
    if resumen is None or not fila.get("repartido"):
        return 0.0
    plan_pct = resumen["objetivo"].get("plan_pct")
    if plan_pct is None:
        return 0.0
    return (plan_pct - fila["objetivo_pct"]) * fila["gasto_kcal"] / 100


def _filas(desde: date, hasta: date, resumen: Resumen) -> list[tuple[dict, Mapping[str, Any] | None]]:
    filas = []
    for i in range((hasta - desde).days + 1):
        dia = desde + timedelta(days=i)
        r = resumen(dia)
        filas.append(({"dia": dia.isoformat(), "estado": "sin_ajustes", "cuenta": False} if r is None
                      else fila_del_dia(r), r))
    return filas


def semana(lunes: date, *, resumen: Resumen) -> dict:
    """The week (Monday to Sunday) that contains `lunes`: its 7 rows, the sum of the balance and what is
    above the objective over the days that count, and the week of unit 014 as it is."""
    desde = lunes_de(lunes)
    filas = _filas(desde, desde + timedelta(days=6), resumen)
    cuentan = [f for f, _ in filas if f["cuenta"]]
    de_014 = next((r["cierre"]["semana"] for _, r in filas if r is not None), None)
    por_encima = round(sum(f["comido_kcal"] - f["objetivo_kcal"] for f in cuentan), 1)
    ya_repartido = round(sum(_ya_repartido(f, r) for f, r in filas), 1)
    return {
        "desde": desde.isoformat(), "hasta": (desde + timedelta(days=6)).isoformat(),
        "filas": [f for f, _ in filas],
        "acumulado_kcal": round(sum(f["balance_kcal"] for f in cuentan), 1),
        "por_encima_kcal": por_encima,
        "ya_repartido_kcal": ya_repartido,  # unit 018
        "pendiente_kcal": round(por_encima - ya_repartido, 1),
        "semana": de_014,
    }


# -- the chart and the list of weeks ---------------------------------------------------------------

def _medias(desde: date, hasta: date, pesos: list[Mapping[str, Any]]) -> list[dict]:
    medias = []
    for i in range((hasta - desde).days + 1):
        dia = desde + timedelta(days=i)
        media = media_7_dias(pesos, dia)
        if media is not None:
            medias.append({"dia": dia.isoformat(), **media.como_dict()})
    return medias


def serie(desde: date, hasta: date, *, resumen: Resumen, pesos: Iterable[Mapping[str, Any]],
          hoy: date | None = None) -> dict:
    """Bars of the balance (one per day with the food closed, and today open as provisional), or one per week
    (its mean per day, `semana.balance_medio_kcal`) if the range is longer than 91 days; the weights of the
    range and the 7-day mean of each day (`media_7_dias`, the one of the Peso tab). `pesos` may start 6 days
    before `desde` so its first days have their mean."""
    pesos = [dict(p) for p in pesos]
    filas = _filas(desde, hasta, resumen)
    dias = (hasta - desde).days + 1
    if dias > BARRAS_POR_DIA_MAX:
        agrupado = "semana"
        barras = []
        lunes = lunes_de(desde)
        while lunes <= hasta:
            de_la_semana = [(f, r) for f, r in filas if lunes.isoformat() <= f["dia"] <= (lunes + timedelta(days=6)).isoformat()]
            s = next((r["cierre"]["semana"] for _, r in de_la_semana if r is not None), None)
            if s is not None and s["dias"]:
                barras.append({"desde": lunes.isoformat(), "hasta": (lunes + timedelta(days=6)).isoformat(),
                               "dia": lunes.isoformat(), "balance_kcal": s["balance_medio_kcal"],
                               "balance_pct": s["balance_pct"], "dias": s["dias"],
                               "provisional": False})
            lunes += timedelta(days=7)
    else:
        agrupado = "dia"
        barras = []
        for f, _ in filas:
            provisional = hoy is not None and f["dia"] == hoy.isoformat() and f["estado"] == "abierto" \
                and f.get("comido_kcal", 0) > 0
            if f["cuenta"] or provisional:
                barras.append({"dia": f["dia"], "balance_kcal": f["balance_kcal"], "balance_pct": f["balance_pct"],
                               "provisional": provisional})
    return {
        "desde": desde.isoformat(), "hasta": hasta.isoformat(), "agrupado": agrupado, "barras": barras,
        "pesos": [{"dia": p["dia"], "kg": p["kg"]} for p in pesos if desde.isoformat() <= p["dia"] <= hasta.isoformat()],
        "medias": _medias(desde, hasta, pesos),
    }


def semanas(desde: date, hasta: date, *, resumen: Resumen, pesos: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Every week from the one of `hasta` back to the one of `desde`, most recent first, with its sum, its mean
    (unit 014) and its mean weight (the mean of the weights of that week; None without any)."""
    pesos = [dict(p) for p in pesos]
    filas = _filas(desde, hasta, resumen)
    lista = []
    lunes = lunes_de(hasta)
    while lunes >= lunes_de(desde):
        domingo = lunes + timedelta(days=6)
        de_la_semana = [(f, r) for f, r in filas if lunes.isoformat() <= f["dia"] <= domingo.isoformat()]
        s = next((r["cierre"]["semana"] for _, r in de_la_semana if r is not None), None) or {}
        kgs = [float(p["kg"]) for p in pesos if lunes.isoformat() <= p["dia"] <= domingo.isoformat()]
        lista.append({
            "desde": lunes.isoformat(), "hasta": domingo.isoformat(), "dias": s.get("dias", 0),
            "acumulado_kcal": round(sum(f["balance_kcal"] for f, _ in de_la_semana if f["cuenta"]), 1),
            "balance_medio_kcal": s.get("balance_medio_kcal"), "balance_pct": s.get("balance_pct"),
            "peso_medio_kg": round(sum(kgs) / len(kgs), 2) if kgs else None,
        })
        lunes -= timedelta(days=7)
    return lista


# -- spreading what is above the objective (R-20) ---------------------------------------------------

def dias_que_quedan(hoy: date, semana: Mapping[str, Any], ajustes: Ajustes) -> list[dict]:
    """Today if its food is still open, and the next days up to Sunday, each marked with the race it keeps
    maintenance for (`Carrera.mantiene`). A day without settings is left out."""
    quedan = []
    for f in semana["filas"]:
        dia = date.fromisoformat(f["dia"])
        if dia < hoy or f["estado"] == "sin_ajustes" or (dia == hoy and f["cuenta"]):
            continue
        carrera = next((c for c in ajustes.vigentes(dia).carreras if c.mantiene(dia)), None)
        quedan.append({"dia": f["dia"], "gasto_kcal": f["gasto_kcal"], "objetivo_pct": f["objetivo_pct"],
                       "mantenimiento": carrera.nombre if carrera else None})
    return quedan


def _nuevo_pct(antes: float, kcal: float, gasto: float) -> float:
    """% now − kcal / expenditure × 100, to 1 decimal, rounded to the side of LESS deficit (never past the limit)."""
    exacto = round((antes - kcal / gasto * 100) * 10, 6)
    nuevo = math.ceil(exacto) / 10
    return int(nuevo) if float(nuevo).is_integer() else nuevo


def proponer_reparto(por_encima_kcal: float, dias: Iterable[Mapping[str, Any]], limite_pct: float | None) -> dict:
    """Pure: how to make up `por_encima_kcal` over `dias` (each `{dia, gasto_kcal, objetivo_pct, mantenimiento}`).

    Equal parts in kcal; a day that would go past the healthy limit (−`limite_pct` %, or −50 % without one)
    stays at it and what does not fit goes to the others; what fits nowhere is said in `sin_repartir_kcal`.
    Maintenance days are not touched. Saves nothing: the caller applies it only when Andrés says so.
    """
    lista = [dict(x) for x in dias]
    sin_cambio = [{"dia": x["dia"], "motivo": f"mantenimiento por el {x['mantenimiento']}"}
                  for x in lista if x["mantenimiento"]]
    libres = [x for x in lista if not x["mantenimiento"] and x["gasto_kcal"] > 0]
    vacio = {"por_encima_kcal": round(por_encima_kcal, 1), "dias": [], "sin_cambio": sin_cambio,
             "sin_repartir_kcal": 0, "motivo": None}
    if por_encima_kcal <= 0:
        return {**vacio, "sin_cambio": [], "motivo": "nada_que_repartir"}
    if not libres:
        return {**vacio, "motivo": "sin_dias"}
    tope = TOPE_SIN_LIMITE if limite_pct is None else limite_pct
    cabe = {x["dia"]: max(0.0, (x["objetivo_pct"] + tope) * x["gasto_kcal"] / 100) for x in libres}
    parte: dict[str, float] = {}
    abiertos = [x["dia"] for x in libres]
    resto = float(por_encima_kcal)
    while abiertos and resto > 1e-9:  # water filling: the days that hit their limit drop out
        cada = resto / len(abiertos)
        llenos = [d for d in abiertos if cabe[d] - parte.get(d, 0) <= cada]
        if not llenos:
            for d in abiertos:
                parte[d] = parte.get(d, 0) + cada
            resto = 0
            break
        for d in llenos:
            falta = cabe[d] - parte.get(d, 0)
            parte[d] = parte.get(d, 0) + falta
            resto -= falta
            abiertos.remove(d)
    propuestos = []
    for x in libres:
        kcal = parte.get(x["dia"], 0)
        if kcal <= 0:
            continue
        nuevo = _nuevo_pct(x["objetivo_pct"], kcal, x["gasto_kcal"])
        real = round((x["objetivo_pct"] - nuevo) * x["gasto_kcal"] / 100, 1)
        propuestos.append({"dia": x["dia"], "antes_pct": x["objetivo_pct"], "nuevo_pct": nuevo,
                           "kcal": int(real) if float(real).is_integer() else real,
                           "objetivo_kcal": round(x["gasto_kcal"] * (100 + nuevo) / 100, 1)})
    sin_repartir = round(max(0.0, resto), 1)  # what fits in no day (the rounding to less deficit is not said)
    return {**vacio, "dias": propuestos, "sin_repartir_kcal": int(sin_repartir) if float(sin_repartir).is_integer()
            else sin_repartir}


def reparto_de_la_semana(s: Mapping[str, Any], dias: Iterable[Mapping[str, Any]], limite_pct: float | None) -> dict:
    """Unit 018 (R-20): the proposal over what is still PENDING this week (`semana()`), not over all that is above.

    When nothing is pending because it is already spread on the days that are left, `motivo` is "ya_repartido".
    """
    ya = s.get("ya_repartido_kcal", 0) or 0
    pendiente = s.get("pendiente_kcal", s["por_encima_kcal"])
    if ya > 0 and pendiente < REDONDEO_KCAL:
        pendiente = 0
    propuesta = proponer_reparto(pendiente, dias, limite_pct)
    if ya > 0:
        propuesta = {**propuesta, "ya_repartido_kcal": ya}
        if propuesta["motivo"] == "nada_que_repartir":
            propuesta["motivo"] = "ya_repartido"
    return propuesta


def _pct(n: float) -> str:
    texto = f"{abs(n):g}".replace(".", ",")
    return f"{'−' if n < 0 else '+' if n > 0 else ''}{texto} %"


def _nombre(iso: str) -> str:
    dia = date.fromisoformat(iso)
    return f"{_DIAS[dia.weekday()]} {dia.day}"


def _enumerar(nombres: list[str]) -> str:
    return nombres[0] if len(nombres) == 1 else f"{', '.join(nombres[:-1])} y {nombres[-1]}"


def texto_reparto(propuesta: Mapping[str, Any]) -> str:
    """The proposal in one paragraph, as the Balance tab shows it (kcal in whole numbers)."""
    if propuesta["motivo"] == "nada_que_repartir":
        return NADA
    if propuesta["motivo"] == "ya_repartido":
        return YA_REPARTIDO
    if propuesta["motivo"] == "sin_dias":
        return SIN_DIAS
    if propuesta.get("ya_repartido_kcal"):  # unit 018: part of it is already spread, only the rest is proposed
        partes = [f"Te quedan {kcal_enteras(propuesta['por_encima_kcal'])} kcal por encima sin repartir esta semana."]
    else:
        partes = [f"Llevas {kcal_enteras(propuesta['por_encima_kcal'])} kcal por encima de tu objetivo esta semana."]
    grupos: dict[tuple, list[str]] = {}
    for x in propuesta["dias"]:
        grupos.setdefault((x["nuevo_pct"], x["objetivo_kcal"], round(x["kcal"])), []).append(_nombre(x["dia"]))
    frases = []
    for (nuevo, objetivo, kcal), nombres in grupos.items():
        cada = " cada uno" if len(nombres) > 1 else ""
        frases.append(f"{_enumerar(nombres)} a {_pct(nuevo)} ({kcal_enteras(objetivo)} kcal; −{kcal_enteras(kcal)}{cada})")
    if round(propuesta["sin_repartir_kcal"]) >= 1:
        frases.append(f"quedan {kcal_enteras(propuesta['sin_repartir_kcal'])} kcal sin repartir sin pasar de tu límite")
    if frases:
        partes.append(f"Para compensarlo: {'; '.join(frases)}.")
    motivos: dict[str, list[str]] = {}
    for x in propuesta["sin_cambio"]:
        motivos.setdefault(x["motivo"], []).append(_nombre(x["dia"]))
    for motivo, nombres in motivos.items():
        texto = _enumerar(nombres)
        partes.append(f"{texto[0].upper()}{texto[1:]} no {'cambian' if len(nombres) > 1 else 'cambia'}: {motivo}.")
    return " ".join(partes)
