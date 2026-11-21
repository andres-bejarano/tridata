"""The macros of a day against what its training asks (unit 099, REC-43 of `consultar-margen`: R-274..R-279, G-65).

Read only: nothing here writes. Only what is EATEN counts (never what is planned), and of that only the foods that
carry their three macros (protein, carbohydrates and fat, unit 098) and their kcal.

- **Objective (G-65):** protein 1.6-2.0 g/kg every day; carbohydrates by the hours of training of the day (none 3-5,
  less than 1 h 5-7, from 1 to 3 h both included 6-10, more than 3 h 8-12 g/kg); fat at least 20 % of the kcal of
  what was eaten WITH macros (what has none cannot lower its share). With the last weight logged up to that day;
  without one, only g/kg, «apunta tu peso para verlo en gramos» and no verdict for protein nor carbohydrates.
- **Hours (R-275):** the larger of the activities of Garmin that day and the sessions of that day in the plan in
  course, each one lasting what `calendario.sincronizar.duracion` reads (one it cannot read is one hour, C-270).
- **Verdict (R-276, R-277):** only for a past day, and only if the kcal eaten with their three macros are at least
  80 % of the kcal eaten; below that it is «aproximado: macros del N % de las kcal», and with none «sin macros».
  Protein and carbohydrates «bajo», «en_rango» or «alto»; fat «bajo» or «bien».
- **Weeks (R-278):** the last 8 Monday-to-Sunday weeks (this one included): per macro the mean a day (g/kg; fat in
  % of kcal) and in how many days it fell in range, over the past days with a verdict.

`vista` is what the card of Hoy (`/api/hoy`) and the context of the team (`conversaciones/contexto.py`) read: the
same figures in both.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

from ..calendario.sincronizar import duracion
from ..plan import Planes
from ..storage import DataStore
from .peso import RegistroPeso
from .registro import MACROS, RegistroComidas

PROTEINA_G_KG = (1.6, 2.0)
GRASA_PCT_MIN = 20
UMBRAL_PCT = 80  # R-277: below this share of the kcal with macros there is no verdict
SEMANAS = 8
AVISO_SIN_PESO = "apunta tu peso para verlo en gramos"
SIN_MACROS = "sin macros"
COMPLETO, APROXIMADO, SIN = "completo", "aproximado", "sin_macros"


def franja_hidratos(horas: float) -> tuple[int, int]:
    """G-65: the range of carbohydrates in g/kg for `horas` of training."""
    if horas <= 0:
        return (3, 5)
    if horas < 1:
        return (5, 7)
    if horas <= 3:
        return (6, 10)
    return (8, 12)


def _gramos(kg: float | None, rango: tuple[float, float]) -> list[int] | None:
    return None if kg is None else [round(kg * rango[0]), round(kg * rango[1])]


def objetivo(kg: float | None, horas: float, kcal: float | None = None) -> dict[str, Any]:
    """The objective of a day (R-274): the ranges in g/kg and in g (None without a weight); fat, its minimum share and,
    with `kcal`, its grams."""
    hidratos = franja_hidratos(horas)
    grasa: dict[str, Any] = {"pct_min": GRASA_PCT_MIN}
    if kcal is not None:
        grasa["g_min"] = round(kcal * GRASA_PCT_MIN / 100 / 9)
    return {"proteina": {"g_kg": list(PROTEINA_G_KG), "g": _gramos(kg, PROTEINA_G_KG)},
            "hidratos": {"g_kg": list(hidratos), "g": _gramos(kg, hidratos)},
            "grasa": grasa}


def _veredicto(valor: float, rango: tuple[float, float]) -> str:
    if valor < rango[0]:
        return "bajo"
    if valor > rango[1]:
        return "alto"
    return "en_rango"


def _con_macros(a: dict) -> bool:
    return a.get("kcal") is not None and all(a.get(k) is not None for k in MACROS)


def del_dia(apuntes: Iterable[dict], kg: float | None, horas: float, *, es_hoy: bool) -> dict[str, Any]:
    """The macros of one day from its logged foods (`RegistroComidas.apuntes_del_dia`), its weight and its hours.

    `estado` is `completo`, `aproximado` (less than 80 % of the kcal eaten have macros) or `sin_macros`; the
    `veredicto` of each macro is None today («llevas»), when it is not `completo` and, for protein and carbohydrates,
    without a weight."""
    comido = [a for a in apuntes if a.get("estado") == "comido"]
    kcal_comidas = sum(a.get("kcal") or 0 for a in comido)
    con = [a for a in comido if _con_macros(a)]
    kcal_con = sum(a["kcal"] for a in con)
    gramos = {k: sum(a[k] for a in con) for k in MACROS}
    pct = kcal_con / kcal_comidas * 100 if kcal_comidas > 0 else None
    if not con or kcal_con <= 0:
        estado, aviso = SIN, SIN_MACROS
    elif pct is not None and pct < UMBRAL_PCT:
        estado, aviso = APROXIMADO, f"aproximado: macros del {round(pct)} % de las kcal"
    else:
        estado, aviso = COMPLETO, None
    juzga = estado == COMPLETO and not es_hoy
    o = objetivo(kg, horas)

    def macro(clave: str, nombre: str, rango: tuple[float, float]) -> dict[str, Any]:
        g = gramos[clave]
        g_kg = g / kg if kg else None
        return {"g": round(g, 1), "g_kg": None if g_kg is None else round(g_kg, 2),
                "objetivo_g_kg": o[nombre]["g_kg"], "objetivo_g": o[nombre]["g"],
                "veredicto": _veredicto(g_kg, rango) if juzga and g_kg is not None else None}

    pct_grasa = gramos["grasa_g"] * 9 / kcal_con * 100 if estado != SIN else None
    return {
        "kg": kg, "horas": round(horas, 2), "kcal_comidas": round(kcal_comidas), "kcal_con_macros": round(kcal_con),
        "pct_kcal_con_macros": None if pct is None else round(pct), "estado": estado, "aviso": aviso,
        "aviso_peso": AVISO_SIN_PESO if kg is None else None,
        "proteina": macro("proteina_g", "proteina", PROTEINA_G_KG),
        "hidratos": macro("hidratos_g", "hidratos", franja_hidratos(horas)),
        "grasa": {"g": round(gramos["grasa_g"], 1), "pct_kcal": None if pct_grasa is None else round(pct_grasa),
                  "objetivo_pct_min": GRASA_PCT_MIN,
                  "veredicto": (("bien" if pct_grasa >= GRASA_PCT_MIN else "bajo")
                                if juzga and pct_grasa is not None else None)},
    }


# -- the hours of training (R-275) -----------------------------------------------------------------------------------

def horas_por_dia(store: DataStore | None, planes: Planes | None, desde: date, hasta: date) -> dict[date, float]:
    """The hours of training of each day from `desde` to `hasta`: the larger of Garmin's and the plan's."""
    garmin: dict[date, float] = {}
    if store is not None:
        for a in store.activities_between(desde, hasta):
            dia = date.fromisoformat(str(a.get("activity_date"))[:10])
            garmin[dia] = garmin.get(dia, 0.0) + float(a.get("duration_seconds") or 0) / 3600
    leidos: dict[int, dict[str, float]] = {}  # the plan's hours by day, each plan read once

    def del_plan(dia: date) -> float:
        ident = planes.en_curso(dia) if planes is not None else None
        if ident is None or planes is None:
            return 0.0
        if ident not in leidos:
            plan = planes.leer(ident) or {}
            por_dia: dict[str, float] = {}
            for s in plan.get("sesiones") or []:
                horas = duracion([str(b) for b in s.get("bloques") or []]).total_seconds() / 3600
                por_dia[s["fecha"]] = por_dia.get(s["fecha"], 0.0) + horas
            leidos[ident] = por_dia
        return leidos[ident].get(dia.isoformat(), 0.0)

    return {d: max(garmin.get(d, 0.0), del_plan(d))
            for d in (desde + timedelta(days=i) for i in range((hasta - desde).days + 1))}


def horas_de_entreno(store: DataStore | None, planes: Planes | None, dia: date) -> float:
    """R-275: the hours of training of `dia`, the larger of Garmin's and the plan's (`horas_por_dia`)."""
    return horas_por_dia(store, planes, dia, dia)[dia]


# -- the days and the weeks --------------------------------------------------------------------------------------------

def _dias(registro: RegistroComidas, pesos: RegistroPeso, store: DataStore | None, planes: Planes | None,
          desde: date, hasta: date, hoy: date) -> dict[date, dict[str, Any]]:
    """`del_dia` of every day from `desde` to `hasta`, each one with the last weight logged up to it."""
    horas = horas_por_dia(store, planes, desde, hasta)
    anterior = pesos.ultimo_hasta(desde - timedelta(days=1))
    kg_de = {p["dia"]: float(p["kg"]) for p in pesos.entre(desde, hasta)}
    kg = float(anterior["kg"]) if anterior else None
    salida = {}
    for i in range((hasta - desde).days + 1):
        dia = desde + timedelta(days=i)
        kg = kg_de.get(dia.isoformat(), kg)
        salida[dia] = {"fecha": dia.isoformat(),
                       **del_dia(registro.apuntes_del_dia(dia), kg, horas[dia], es_hoy=dia >= hoy)}
    return salida


def _resumen(valores: list[tuple[float, bool]], clave: str, decimales: int) -> dict[str, Any] | None:
    """The mean a day and the days in range of one macro in one week; None without a day with a verdict."""
    if not valores:
        return None
    media = sum(v for v, _ in valores) / len(valores)
    return {clave: round(media, decimales) if decimales else round(media),
            "en_rango": sum(ok for _, ok in valores), "dias": len(valores)}


def _semanas(dias: dict[date, dict[str, Any]], hoy: date, n: int) -> list[dict[str, Any]]:
    lunes = hoy - timedelta(days=hoy.weekday())
    salida = []
    for k in range(n - 1, -1, -1):
        inicio = lunes - timedelta(weeks=k)
        filas = [dias[d] for d in (inicio + timedelta(days=i) for i in range(7)) if d < hoy and d in dias]
        semana: dict[str, Any] = {"lunes": inicio.isoformat(), "domingo": (inicio + timedelta(days=6)).isoformat()}
        for m in ("proteina", "hidratos"):
            semana[m] = _resumen([(f[m]["g_kg"], f[m]["veredicto"] == "en_rango") for f in filas
                                  if f[m]["veredicto"] is not None], "media_g_kg", 1)
        semana["grasa"] = _resumen([(f["grasa"]["pct_kcal"], f["grasa"]["veredicto"] == "bien") for f in filas
                                    if f["grasa"]["veredicto"] is not None], "media_pct", 0)
        semana["sin_macros"] = all(semana[m] is None for m in ("proteina", "hidratos", "grasa"))
        salida.append(semana)
    return salida


def semanas(*, registro: RegistroComidas, pesos: RegistroPeso, store: DataStore | None, planes: Planes | None,
            hoy: date, n: int = SEMANAS) -> list[dict[str, Any]]:
    """R-278: the last `n` Monday-to-Sunday weeks, oldest first, the current one last (only its past days count)."""
    desde = hoy - timedelta(days=hoy.weekday()) - timedelta(weeks=n - 1)
    return _semanas(_dias(registro, pesos, store, planes, desde, hoy, hoy), hoy, n)


def vista(*, registro: RegistroComidas, pesos: RegistroPeso, store: DataStore | None, planes: Planes | None,
          hoy: date) -> dict[str, Any]:
    """`{hoy, ayer, semanas}`: what the card «Macros» of Hoy shows and the team reads (R-276..R-279)."""
    desde = min(hoy - timedelta(days=hoy.weekday()) - timedelta(weeks=SEMANAS - 1), hoy - timedelta(days=1))
    dias = _dias(registro, pesos, store, planes, desde, hoy, hoy)
    return {"hoy": dias[hoy], "ayer": dias[hoy - timedelta(days=1)], "semanas": _semanas(dias, hoy, SEMANAS)}


def texto(dia: dict[str, Any]) -> str:
    """One line for the team: «proteína 95 de 112-140 g · hidratos 300 de 420-700 g · grasa 22 % (al menos 20 %)»;
    without a weight, «proteína 95 g (1.6-2 g/kg)»."""
    partes = []
    for clave, nombre in (("proteina", "proteína"), ("hidratos", "hidratos")):
        m = dia[clave]
        if m["objetivo_g"]:
            partes.append(f"{nombre} {m['g']:g} de {m['objetivo_g'][0]}-{m['objetivo_g'][1]} g")
        else:
            partes.append(f"{nombre} {m['g']:g} g ({m['objetivo_g_kg'][0]:g}-{m['objetivo_g_kg'][1]:g} g/kg)")
    g = dia["grasa"]
    partes.append(f"grasa {g['pct_kcal']} % (al menos {g['objetivo_pct_min']} %)" if g["pct_kcal"] is not None
                  else f"grasa (al menos {g['objetivo_pct_min']} %)")
    return " · ".join(partes + [a for a in (dia["aviso"], dia["aviso_peso"]) if a])


__all__ = ["AVISO_SIN_PESO", "GRASA_PCT_MIN", "PROTEINA_G_KG", "SEMANAS", "UMBRAL_PCT", "del_dia", "franja_hidratos",
           "horas_de_entreno", "horas_por_dia", "objetivo", "semanas", "texto", "vista"]
