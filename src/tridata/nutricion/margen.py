"""Day total and margin: pure functions over the saved rows.

Simple expenditure: basal + steps × kcal per step, with the settings in force THAT day
(`Ajustes.vigentes(dia).config_gasto()`, unit 005). The margin of a level is
expenditure × (1 + level) − eaten, for −10 %, 0 % and +10 % (R-16, G-30).

Unit 007: the basal of the day comes from `peso.basal_del_dia` (Mifflin-St Jeor with the last
weight) and the objective of the day, when there is one, brings the levels and the planned steps.
The planned training is shown but does not add kcal yet (R-15, unit 008).
"""
from __future__ import annotations

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


def resumen_del_dia(
    apuntes: Iterable[Mapping], dia: date, config: ConfigGasto, *,
    objetivo_dia: Mapping[str, Any] | None = None, basal: Any = None, peso: Mapping[str, Any] | None = None,
) -> dict:
    """What the day screen shows: the whole table, total, expenditure and where it comes from, margins.

    `config` is the one of the settings in force that day; `basal` (a `peso.BasalDelDia`) and the
    planned steps of `objetivo_dia` replace its basal and steps. `peso` is the weight block.
    """
    pasos_previstos = objetivo_dia.get("pasos") if objetivo_dia else None
    if basal is not None and basal.kcal is not None:
        config = replace(config, basal_kcal=basal.kcal)
    if pasos_previstos is not None:
        config = replace(config, pasos=pasos_previstos)
    del_dia = [dict(a) for a in apuntes if a["dia"] == dia.isoformat()]
    total = total_del_dia(del_dia, dia)
    gasto = gasto_simple(config)
    por_paso = _texto_numero(config.kcal_por_paso)
    entreno = objetivo_dia.get("entreno") if objetivo_dia else None
    if basal is None and objetivo_dia is None:  # the explanation as it was before unit 007
        explicacion = (f"basal {config.basal_kcal:g} kcal + {config.pasos} pasos × {por_paso} kcal/paso "
                       "(tus ajustes vigentes ese día; día sin entreno)")
    else:
        de_basal = f" ({basal.explicacion})" if basal is not None else ""
        de_pasos = "previstos para el día" if pasos_previstos is not None else "por defecto de tus ajustes"
        de_entreno = (f"entreno previsto «{entreno}» guardado, pero aún no suma kcal" if entreno
                      else "día sin entreno")
        explicacion = (f"basal {config.basal_kcal:g} kcal{de_basal} + {config.pasos} pasos {de_pasos} × "
                       f"{por_paso} kcal/paso (tus ajustes vigentes ese día; {de_entreno})")
    niveles, marcado = niveles_del_dia(objetivo_dia)
    return {
        "fecha": dia.isoformat(),
        "alimentos": del_dia,
        "total_kcal": total,
        "pendientes": [a for a in del_dia if a["pendiente_cantidad"]],
        "gasto": {
            "kcal": gasto,
            "basal_kcal": config.basal_kcal,
            "pasos": config.pasos,
            "kcal_por_paso": config.kcal_por_paso,
            "entreno_previsto": entreno,
            "entreno_suma_kcal": False,
            "explicacion": explicacion,
        },
        "margenes": [{"nivel": nivel, "kcal": kcal, **({"objetivo": True} if nivel == marcado else {})}
                     for nivel, kcal in margenes(gasto, total, niveles).items()],
        "objetivo_dia": dict(objetivo_dia) if objetivo_dia else None,
        "peso": dict(peso) if peso is not None else None,
    }
