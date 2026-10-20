"""Day total and margin: pure functions over the saved rows.

Simple expenditure: basal + steps × kcal per step, with the settings in force THAT day
(`Ajustes.vigentes(dia).config_gasto()`, unit 005). The margin of a level is
expenditure × (1 + level) − eaten, for −10 %, 0 % and +10 % (R-16, G-30).

Unit 007: the basal of the day comes from `peso.basal_del_dia` (Mifflin-St Jeor with the last
weight) and the objective of the day, when there is one, brings the levels and the planned steps.
The planned training text is shown but does not add kcal.

Unit 008: with `gasto_estimado` (from `gasto.estimar_gasto`: Garmin of the day + planned training)
the margins are computed on the full expenditure. Every day also has its objective (the % of
`objetivo_dia` if Andrés changed it, else the plan of `Ajustes.vigentes`, else maintenance), the
bar of what is eaten against it, the healthy limit, what still fits and the days of delay.
"""
from __future__ import annotations

import math
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

NIVELES_POR_DEFECTO = (-10, 0, 10)
PASOS_POR_DEFECTO = 10_000


@dataclass(frozen=True)
class ConfigGasto:
    basal_kcal: float
    kcal_por_paso: float
    pasos: int = PASOS_POR_DEFECTO

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str] | None = None) -> ConfigGasto:
        """Read TRIDATA_BASAL_KCAL and TRIDATA_KCAL_POR_PASO (decimal comma allowed)."""
        entorno = os.environ if entorno is None else entorno
        valores = []
        for clave in ("TRIDATA_BASAL_KCAL", "TRIDATA_KCAL_POR_PASO"):
            bruto = (entorno.get(clave) or "").strip()
            if not bruto:
                raise ValueError(f"Falta {clave} en .env (copia la línea de .env.example y pon tu valor).")
            try:
                valor = float(bruto.replace(",", "."))
            except ValueError:
                raise ValueError(f"{clave}={bruto!r} no es un número.") from None
            if valor <= 0:
                raise ValueError(f"{clave} tiene que ser mayor que 0.")
            valores.append(valor)
        return cls(basal_kcal=valores[0], kcal_por_paso=valores[1])


def gasto_simple(config: ConfigGasto) -> float:
    return round(config.basal_kcal + config.pasos * config.kcal_por_paso, 1)


def total_del_dia(apuntes: Iterable[Mapping], dia: date) -> float:
    """Eaten kcal of `dia`: only `comido` rows with a quantity (pending and planned don't add)."""
    return round(sum(
        a["kcal"] for a in apuntes
        if a["dia"] == dia.isoformat() and a["estado"] == "comido"
        and not a["pendiente_cantidad"] and a["kcal"] is not None
    ), 1)


def margenes(gasto: float, comido: float, niveles: Iterable[float] = NIVELES_POR_DEFECTO) -> dict[float, float]:
    return {nivel: round(gasto * (100 + nivel) / 100 - comido, 1) for nivel in niveles}


def niveles_del_dia(objetivo: Mapping[str, Any] | None) -> tuple[tuple[float, ...], float | None]:
    """Levels of the margin of a day and the one marked as its objective.

    Without objective (or without levels): −10/0/+10 %. The % of the objective, if said, is always
    one of the levels and is the marked one. Levels are shown from lowest to highest.
    """
    objetivo = objetivo or {}
    porcentaje = objetivo.get("porcentaje")
    niveles = list(objetivo.get("niveles") or NIVELES_POR_DEFECTO)
    if porcentaje is not None and porcentaje not in niveles:
        niveles.append(porcentaje)
    return tuple(sorted(set(niveles))), porcentaje


def _texto_numero(n: float) -> str:
    return f"{n:g}".replace(".", ",")


def config_del_dia(config: ConfigGasto, basal: Any = None, objetivo_dia: Mapping[str, Any] | None = None) -> ConfigGasto:
    """The expenditure config of a day: the basal of `basal_del_dia` and the planned steps of unit 007."""
    pasos_previstos = objetivo_dia.get("pasos") if objetivo_dia else None
    if basal is not None and basal.kcal is not None:
        config = replace(config, basal_kcal=basal.kcal)
    if pasos_previstos is not None:
        config = replace(config, pasos=pasos_previstos)
    return config


# -- objective of the day, bar, healthy limit, what fits, days of delay (unit 008) -------------

def _pct_texto(n: float) -> str:
    return "mantenimiento" if n == 0 else f"{'+' if n > 0 else '−'}{_texto_numero(abs(n))} %"


def objetivo_del_dia(objetivo_dia: Mapping[str, Any] | None, plan: Mapping[str, Any] | None) -> dict:
    """The % of the day: the one Andrés set for that day, else the plan's, else maintenance (said)."""
    propio = objetivo_dia.get("porcentaje") if objetivo_dia else None
    del_plan = plan.get("objetivo_pct") if plan else None
    if propio is not None:
        de_plan = "" if del_plan is None else f" (el plan decía {_pct_texto(del_plan)})"
        return {"porcentaje": propio, "origen": "dia", "plan_pct": del_plan,
                "texto": f"Objetivo de hoy: {_pct_texto(propio)}, cambiado para este día{de_plan}."}
    if del_plan is not None:
        motivo = (plan or {}).get("motivo") or "ajuste"
        por = f" por la {motivo}" if motivo.startswith("carrera ") else " del plan"
        return {"porcentaje": del_plan, "origen": "plan", "plan_pct": del_plan,
                "texto": f"Objetivo de hoy: {_pct_texto(del_plan)}{por}."}
    return {"porcentaje": 0, "origen": "sin_plan", "plan_pct": None,
            "texto": "Sin plan en Ajustes: el objetivo de hoy es mantenimiento."}


def limite_saludable(gasto: float, objetivo_pct: float, limite_pct: float | None) -> dict | None:
    """Mark of the healthy limit on the bar: gasto × (1 − limit) in a deficit (or maintenance),
    gasto × (1 + limit) in a surplus; warns if the objective goes past it (R-23, C-39). None without limit."""
    if limite_pct is None:
        return None
    superavit = objetivo_pct > 0
    kcal = round(gasto * (100 + (limite_pct if superavit else -limite_pct)) / 100, 1)
    superado = abs(objetivo_pct) > limite_pct
    lado = "superávit" if superavit else "déficit"
    aviso = (f"El objetivo de hoy ({_pct_texto(objetivo_pct)}) pasa del límite saludable de {lado} "
             f"({_texto_numero(limite_pct)} %).") if superado else None
    return {"pct": limite_pct, "lado": "superavit" if superavit else "deficit", "kcal": kcal,
            "superado": superado, "aviso": aviso}


def barra(objetivo_kcal: float, comido: float, limite: Mapping[str, Any] | None = None) -> dict:
    """% eaten of the objective of the day and the kcal left (or how far past it) (R-23)."""
    pct = round(comido / objetivo_kcal * 100) if objetivo_kcal > 0 else 0
    resto = round(objetivo_kcal - comido, 1)
    quedan, pasado = max(resto, 0.0), max(-resto, 0.0)
    texto = f"te has pasado {_texto_numero(pasado)} kcal" if pasado else f"quedan {_texto_numero(quedan)} kcal"
    return {"objetivo_kcal": round(objetivo_kcal, 1), "comido_kcal": round(comido, 1), "pct": pct,
            "quedan_kcal": quedan, "pasado_kcal": pasado, "texto": texto,
            "limite": dict(limite) if limite is not None else None}


def gramos_que_caben(margen: float, kcal: float, cantidad: float) -> dict:
    """How much of a food (`kcal` per `cantidad` of its unit) fits in `margen` (R-17). Saves nothing."""
    if margen <= 0:
        return {"cantidad": 0, "texto": "no te cabe hoy"}
    if kcal <= 0 or cantidad <= 0:
        return {"cantidad": None, "texto": "sin kcal: no se puede calcular"}
    n = math.floor(margen / kcal * cantidad)
    return {"cantidad": n, "texto": f"te caben {n}"}


def dias_de_retraso(plan: Mapping[date, float | None], hoy: date, nuevo_pct: float) -> float | None:
    """Days later (+) or earlier (−) the weight target is reached if today is `nuevo_pct` instead of the plan.

    (% of the plan today − new %) / mean % of the plan from today to the target date (`plan`, one
    % per day; a day without plan counts 0 %). None if there is no plan or it never gets there.
    """
    if hoy not in plan or not plan:
        return None
    valores = [p or 0 for p in plan.values()]
    media = sum(valores) / len(valores)
    if media == 0:
        return None
    return round(((plan[hoy] or 0) - nuevo_pct) / media, 2)


def texto_retraso(dias: float) -> str:
    if dias == 0:
        return "llegas al objetivo el mismo día"
    n = abs(dias)
    cuantos = f"{_texto_numero(round(n, 1))} {'día' if n == 1 else 'días'}"
    return f"llegas al objetivo {cuantos} {'más tarde' if dias > 0 else 'antes'}"


def simular(resumen: Mapping[str, Any], kcal: float) -> dict:
    """The margins and the bar of a day if `kcal` more were eaten. Pure: saves nothing (REC-2)."""
    comido = round(resumen["total_kcal"] + kcal, 1)
    gasto = resumen["gasto"]["kcal"]
    niveles = [m["nivel"] for m in resumen["margenes"]]
    marcado = next((m["nivel"] for m in resumen["margenes"] if m.get("objetivo")), None)
    b = resumen["barra"]
    return {
        "comido_kcal": comido,
        "margenes": [{"nivel": n, "kcal": k, **({"objetivo": True} if n == marcado else {})}
                     for n, k in margenes(gasto, comido, niveles).items()],
        "barra": barra(b["objetivo_kcal"], comido, b["limite"]),
    }


def _explicacion_completa(gasto: Mapping[str, Any], basal: Any, entreno: str | None, de_pasos: str) -> str:
    d = gasto["desglose"]
    de_basal = f" ({basal.explicacion})" if basal is not None else ""
    if d["pasos_hechos"] is None:
        pasos = f"{gasto['pasos']} pasos {de_pasos}"
    else:
        pasos = f"{gasto['pasos']} pasos ({d['pasos_hechos']} hechos + {d['pasos_supuestos']} supuestos)"
    partes = [f"basal {d['basal_kcal']:g} kcal{de_basal}",
              f"{pasos} × {_texto_numero(d['kcal_por_paso'])} kcal/paso"]
    for h in d["entreno_hecho"]:
        partes.append(f"{h['nombre']} {h['kcal']:g} kcal (hecho, Garmin)")
    if d["previsto"]:
        p = d["previsto"]
        partes.append(f"{d['previsto_kcal']:g} kcal de entreno previsto ({p.get('nombre') or 'entreno'}, "
                      f"{_texto_numero(p['minutos'])} min)")
    texto = " + ".join(partes)
    if entreno and not d["previsto"]:
        texto += f"; el entreno «{entreno}» que dijiste no suma kcal hasta que elijas el entreno previsto"
    return texto


def resumen_del_dia(
    apuntes: Iterable[Mapping], dia: date, config: ConfigGasto, *,
    objetivo_dia: Mapping[str, Any] | None = None, basal: Any = None, peso: Mapping[str, Any] | None = None,
    gasto_estimado: Mapping[str, Any] | None = None, plan: Mapping[str, Any] | None = None,
) -> dict:
    """What the day screen shows: the whole table, total, expenditure and where it comes from, margins,
    objective of the day and its bar.

    `config` is the one of the settings in force that day; `basal` (a `peso.BasalDelDia`) and the
    planned steps of `objetivo_dia` replace its basal and steps. `peso` is the weight block.
    `gasto_estimado` (unit 008) is the full expenditure of `gasto.estimar_gasto`; `plan` is the plan
    of that day (`objetivo_pct`, `motivo`, `limite_saludable_pct`).
    """
    pasos_previstos = objetivo_dia.get("pasos") if objetivo_dia else None
    config = config_del_dia(config, basal, objetivo_dia)
    del_dia = [dict(a) for a in apuntes if a["dia"] == dia.isoformat()]
    total = total_del_dia(del_dia, dia)
    gasto = gasto_simple(config) if gasto_estimado is None else gasto_estimado["kcal"]
    por_paso = _texto_numero(config.kcal_por_paso)
    entreno = objetivo_dia.get("entreno") if objetivo_dia else None
    de_pasos = "previstos para el día" if pasos_previstos is not None else "por defecto de tus ajustes"
    if gasto_estimado is not None:
        explicacion = _explicacion_completa(gasto_estimado, basal, entreno, de_pasos)
    elif basal is None and objetivo_dia is None:  # the explanation as it was before unit 007
        explicacion = (f"basal {config.basal_kcal:g} kcal + {config.pasos} pasos × {por_paso} kcal/paso "
                       "(tus ajustes vigentes ese día; día sin entreno)")
    else:
        de_basal = f" ({basal.explicacion})" if basal is not None else ""
        de_entreno = (f"entreno previsto «{entreno}» guardado, pero aún no suma kcal" if entreno
                      else "día sin entreno")
        explicacion = (f"basal {config.basal_kcal:g} kcal{de_basal} + {config.pasos} pasos {de_pasos} × "
                       f"{por_paso} kcal/paso (tus ajustes vigentes ese día; {de_entreno})")
    niveles, marcado = niveles_del_dia(objetivo_dia)
    objetivo = objetivo_del_dia(objetivo_dia, plan)
    limite = limite_saludable(gasto, objetivo["porcentaje"], plan.get("limite_saludable_pct") if plan else None)
    objetivo_kcal = round(gasto * (100 + objetivo["porcentaje"]) / 100, 1)
    bloque_gasto: dict[str, Any] = {
        "kcal": gasto,
        "basal_kcal": config.basal_kcal,
        "pasos": config.pasos if gasto_estimado is None else gasto_estimado["pasos"],
        "kcal_por_paso": config.kcal_por_paso,
        "entreno_previsto": entreno,
        "entreno_suma_kcal": gasto_estimado is not None,
        "explicacion": explicacion,
    }
    if gasto_estimado is not None:
        bloque_gasto.update(desglose=gasto_estimado["desglose"], avisos=list(gasto_estimado["avisos"]),
                            garmin=gasto_estimado["garmin"])
    return {
        "fecha": dia.isoformat(),
        "alimentos": del_dia,
        "total_kcal": total,
        "pendientes": [a for a in del_dia if a["pendiente_cantidad"]],
        "gasto": bloque_gasto,
        "margenes": [{"nivel": nivel, "kcal": kcal, **({"objetivo": True} if nivel == marcado else {})}
                     for nivel, kcal in margenes(gasto, total, niveles).items()],
        "niveles_elegidos": bool(objetivo_dia and objetivo_dia.get("niveles")),
        "objetivo_dia": dict(objetivo_dia) if objetivo_dia else None,
        "objetivo": {**objetivo, "kcal": objetivo_kcal, "retraso": None},
        "barra": barra(objetivo_kcal, total, limite),
        "peso": dict(peso) if peso is not None else None,
    }
