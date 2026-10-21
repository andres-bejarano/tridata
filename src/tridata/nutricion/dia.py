"""The whole day as the day screen shows it (unit 015: moved here, unchanged, from `web/app.py`).

`resumen_completo` is the ONE place that composes a day: settings in force that day, basal, objective
of the day, estimated expenditure with Garmin (`gasto_estimado_del_dia`), the real one if the day is
closed with Garmin (unit 014), margins, bar, weight block, what is planned and the `cierre` block. The
web (`GET /api/dia` and every route that returns a day) and nutricion.md (`historial_md`) both call it,
so the file and the screen never compute a day in two ways (R-61).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .ajustes import Ajustes
from .cierre import Cierres, config_gasto_del_dia, gasto_estimado_del_dia
from .margen import dias_de_retraso, resumen_del_dia, simular, texto_retraso
from .objetivo_dia import ObjetivosDia
from .peso import RegistroPeso, resumen_peso
from .registro import RegistroComidas

DIAS_RETRASO_MAX = 730  # the days of delay look at most two years ahead


class FaltanAjustes(Exception):
    """A day cannot be computed: the settings of that day lack basal or kcal per step."""


def plan_del_dia(vigentes: Any) -> dict:
    return {"objetivo_pct": vigentes.objetivo_pct, "motivo": vigentes.motivo_objetivo,
            "limite_saludable_pct": vigentes.limite_saludable_pct}


def _retraso(ajustes: Ajustes, fecha: date, vigentes: Any, nuevo_pct: float) -> dict | None:
    meta = vigentes.objetivo_peso
    if meta is None or meta.fecha < fecha:
        return None
    hasta = min(meta.fecha, fecha + timedelta(days=DIAS_RETRASO_MAX))
    plan = {fecha + timedelta(days=i): ajustes.vigentes(fecha + timedelta(days=i)).objetivo_pct
            for i in range((hasta - fecha).days + 1)}
    dias = dias_de_retraso(plan, fecha, nuevo_pct)
    return None if dias is None else {"dias": dias, "texto": texto_retraso(dias), "fecha_objetivo": meta.fecha.isoformat()}


def resumen_completo(fecha: date, *, registro: RegistroComidas, pesos: RegistroPeso, ajustes: Ajustes,
                     objetivos: ObjetivosDia, store: Any, cierres: Cierres, hoy: date) -> dict:
    """The day `fecha` exactly as `GET /api/dia?fecha=` answers it. `FaltanAjustes` without basal or kcal per step.

    It only reads: bringing the days up to date (`Cierres.al_dia`) is the caller's job.
    """
    vigentes = ajustes.vigentes(fecha)
    cuando = "de hoy" if fecha == hoy else "de ese día"
    try:
        _, basal, config = config_gasto_del_dia(fecha, ajustes, pesos)
        estimado = gasto_estimado_del_dia(fecha, ajustes, pesos, objetivos, store, cuando=cuando)
    except ValueError as exc:
        raise FaltanAjustes(str(exc)) from None
    ventana = pesos.entre(fecha - timedelta(days=6), fecha)
    objetivo_dia = objetivos.del_dia(fecha)
    # unit 014: a day closed with Garmin computes its margins, objective and bar with the real expenditure
    fila = cierres.del_dia(fecha)
    real = fila["gasto_real"] if fila and fila["estado"] == "cerrado_garmin" else None
    gasto = estimado if real is None else {**estimado, "kcal": real}
    plan = plan_del_dia(vigentes)
    resumen = resumen_del_dia(registro.apuntes_del_dia(fecha), fecha, config, objetivo_dia=objetivo_dia,
                              basal=basal, peso=resumen_peso(ventana, fecha, vigentes, basal),
                              gasto_estimado=gasto, plan=plan)
    bloque_gasto = resumen["gasto"]
    if real is not None:
        bloque_gasto["explicacion"] = (f"gasto real de Garmin {real:g} kcal (el estimado era {estimado['kcal']:g} "
                                       f"kcal: {bloque_gasto['explicacion']})")
    bloque_gasto.update(origen="estimado" if real is None else "garmin", estimado_kcal=estimado["kcal"],
                        real_kcal=real, calibracion=cierres.calibracion_vigente(fecha))
    if resumen["objetivo"]["origen"] == "dia":
        resumen["objetivo"]["retraso"] = _retraso(ajustes, fecha, vigentes, resumen["objetivo"]["porcentaje"])
    # unit 010: what is planned is only shown apart, it never subtracts from the margin (rule of 004)
    planeado = round(sum(a["kcal"] for a in resumen["alimentos"] if a["estado"] == "planeado"
                         and a["kcal"] is not None and not a["pendiente_cantidad"]), 1)
    return {**resumen, "plan": plan, "planeado_kcal": planeado, "con_planeado": simular(resumen, planeado),
            "cierre": cierres.bloque_del_dia(fecha, resumen["total_kcal"], estimado["kcal"]),
            "avisos_cierre": cierres.avisos_pendientes()}
