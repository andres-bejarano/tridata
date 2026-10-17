"""Day total and margin: pure functions over the saved rows.

Skeleton expenditure (ROADMAP, unit 002): basal + 10,000 steps × kcal per step, both fixed
values from `.env` until the settings unit. The margin of a level is
expenditure × (1 + level) − eaten, for −10 %, 0 % and +10 % (R-16, G-30).
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

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


def margenes(gasto: float, comido: float, niveles: Iterable[int] = NIVELES_POR_DEFECTO) -> dict[int, float]:
    return {nivel: round(gasto * (100 + nivel) / 100 - comido, 1) for nivel in niveles}


def resumen_del_dia(apuntes: Iterable[Mapping], dia: date, config: ConfigGasto) -> dict:
    """What the day screen shows: the whole table, total, expenditure and where it comes from, margins."""
    del_dia = [dict(a) for a in apuntes if a["dia"] == dia.isoformat()]
    total = total_del_dia(del_dia, dia)
    gasto = gasto_simple(config)
    por_paso = f"{config.kcal_por_paso:g}".replace(".", ",")
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
            "explicacion": (
                f"basal {config.basal_kcal:g} kcal + {config.pasos} pasos × {por_paso} kcal/paso "
                "(valores fijos de .env; día sin entreno)"
            ),
        },
        "margenes": [{"nivel": nivel, "kcal": kcal} for nivel, kcal in margenes(gasto, total).items()],
    }
