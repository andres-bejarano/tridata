"""Fitness (unit 031, REC-19 of `ver-fitness`): what the Fitness tab reads. Read only: nothing here writes.

- `carga`: form (CTL), fatigue (ATL) and freshness (TSB) of today and the days that end today (unit 090: 14, 42, 91 or
  365; 42 by default — before, 14 fixed), with
  `metrics.compute_pmc` over the whole history (the EMAs need it) and each day computed with the HR in force THAT
  day in Ajustes (G-49); without any confirmed, the environment's, and it says so (R-108).
- `ultima_actividad` and `detalle_actividad`: the last activity up to today, and its laps (distance, time, pace or
  speed, mean HR and the zone of that HR, with the zones in force on the activity's day) and its weather.
- `recuperacion`: last night's sleep, today's resting HR and last night's HRV, each next to its 7-day mean.
- `semanas` (unit 090, REC-35): Monday-to-Sunday weeks by sport (correr, bici, nadar, fuerza, otros) with hours, km
  (strength never), load (the same TRIMP with the HR of THAT day as `carga`; only what form and fatigue count, so
  «otros» outside `ALL_SPORT_TYPES` adds 0) and seconds in each zone, approximated by laps (S-82): each lap adds its
  time to the zone of its mean HR with the zones in force that day; a lap without HR, or a day without confirmed
  zones, goes to «sin pulso». `por_semana` is the grouping shared with the coach's `get_weekly_summary`.
- `actividades_semana` (unit 093, REC-37): the activities of one week (newest first, of one sport or all), with its
  totals (the ones of `semanas`, plus the number of sessions) next to the mean of the 4 weeks before — only the weeks
  since the first activity stored, and it says how many. Each lap of `detalle_actividad` also carries, by sport, its
  cadence (steps, pedal strokes or arm strokes per minute), stride, SWOLF and climb (R-242).
- Unit 094 (REC-40): `detalle_actividad` also carries `curva`, the HR second by second of the session (reduced to
  draw, with the real seconds in each zone and the segment of each lap), and `semanas` counts the zones second by
  second where the session has its detail (S-85), by laps where not; each week says `aproximado` while one of its
  sessions is still by laps (R-253). Unit 095 (C-319): only the sessions whose detail can still be brought count for
  it — a type of `DETALLE_TYPES` not asked yet; pool, strength and the ones Garmin answered without detail do not.
- Unit 096 (REC-41, R-258): laps, curve and time in zone — of the session and of «Tus semanas» — use the zones of the
  session's SPORT in force that day (`PulsoVigente.zonas_de`, `umbral.deporte_de`): a ride with the ride's, a swim
  with the swim's, the rest with the run's. `detalle_actividad` says which (`zonas_deporte`) and whether they are
  estimated from the run (C-329). The load never uses zones: it does not change (R-262).
- Unit 097 (REC-42, R-263, R-268): the period is a shortcut (the last 14, 42, 91 or 365 days up to today) or dates
  chosen (`periodo`: from 7 days to 2 years, «hasta» not after today). With dates, the load series is cut to them
  (computed whole, as always; `hoy` is still today's) and «Tus semanas» shows the weeks that touch them.
- Unit 108 (REC-51, R-310..R-316): each week of `semanas` also carries, by sport and in total, `subida_m` (the climb of
  its activities; None when Garmin gave none) and `zonas_km` (the km in each zone: each lap adds its km to the zone of
  its mean HR, as the seconds; a session without laps, all «sin pulso»; strength None), and `km` with 2 decimals.
  `fitness` takes a second period (`otro`, its «hasta» may be after today, and then the first one's too) and answers
  it as `otro: {periodo, semanas, historial_desde}`, both periods with the weeks only up to the one in course; the
  table of this period against the one before (unit 097, `comparar`) is gone (R-264).

Every Garmin datum carries its state, as in `hoy.py` (G-47, never an invented 0): `ok`, `no_ha_llegado` (not given
yet) or `incompleto` (given half-done: a resting HR of a day whose totals Garmin cached before finishing it, or a
night of sleep without its score yet).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from .hoy import INCOMPLETO, NO_HA_LLEGADO, OK, _deporte, _hrv, _por_dia, dia_a_medias
from .metrics import compute_pmc, daily_tss, pmc_hasta, trimp_for_activity
from .nutricion.ajustes import DESDE_SIEMPRE, HistorialFC, PulsoVigente
from .storage import DataStore
from .sync import ALL_SPORT_TYPES, CYCLING_TYPES, DETALLE_TYPES, GYM_TYPES, RUNNING_TYPES, SWIMMING_TYPES

RANGOS_DIAS = (14, 42, 91, 365)  # unit 090 (R-232): 14 days · 6 weeks · 3 months · 1 year
DIAS_POR_DEFECTO = 42
SEMANAS_DE = {dias: dias // 7 for dias in RANGOS_DIAS}  # 2, 6, 13 and 52 weeks
DEPORTES_SEMANA = (("correr", RUNNING_TYPES), ("bici", CYCLING_TYPES), ("nadar", SWIMMING_TYPES),
                   ("fuerza", GYM_TYPES))
OTROS = "otros"
ZONAS_SEMANA = ("z1", "z2", "z3", "z4", "z5", "sin_pulso")
DIAS_MEDIA = 7
SIN_CONFIRMAR = "de la configuración: confírmala en Ajustes"
TODOS = "todos"
SEMANAS_MEDIA = 4  # unit 093 (R-240): the week tapped next to the mean of the 4 before


def _r1(valor: float) -> float:
    return round(valor, 1)


def _desde(dia: date) -> str:
    return "desde siempre" if dia == DESDE_SIEMPRE else f"desde el {dia.day}/{dia.month}"


def texto_pulso(pulso: PulsoVigente | None) -> dict[str, Any]:
    """Where the HR of a day comes from (R6): «de tu prueba de esfuerzo de 2025-11, desde el 1/11», or that it is the
    configuration's still to be confirmed."""
    if pulso is None:
        return {"confirmada": False, "texto": SIN_CONFIRMAR}
    origen = pulso.origen or {}
    if origen.get("tipo") == "prueba":
        return {"confirmada": True, "texto": f"de tu prueba de esfuerzo de {origen['mes']}, {_desde(pulso.desde)}"}
    return {"confirmada": True, "texto": f"de la configuración, confirmada en Ajustes {_desde(pulso.desde)}"}


def zona(pulso: float | None, zonas: tuple[tuple[int, int], ...] | None) -> int | None:
    """The zone (1-5) of a mean HR: the highest whose «from» it reaches; below zone 1, none; above the max, 5."""
    if pulso is None or not zonas or pulso < zonas[0][0]:
        return None
    return max(n for n, (desde, _) in enumerate(zonas, start=1) if pulso >= desde)


def comprobar_dias(dias: int) -> int:
    """`dias` if it is one of the four ranges (unit 090); otherwise ValueError."""
    if dias not in RANGOS_DIAS:
        raise ValueError(f"El rango es de 14, 42, 91 o 365 días, no {dias}.")
    return dias


MIN_DIAS_PERIODO, MAX_DIAS_PERIODO = 7, 731  # unit 097 (R-268): from 7 days to 2 years, both ends counted
NO_VALE = "El periodo no vale: "


def _fecha(valor: date | str) -> date:
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{NO_VALE}«{valor}» no es una fecha (AAAA-MM-DD).") from None


def periodo(hoy: date, dias: int | None = None, desde: date | str | None = None,
            hasta: date | str | None = None, futuro: bool = False) -> tuple[date, date]:
    """Unit 097 (R-263, R-268): `(desde, hasta)` of the period on screen. A shortcut (`dias`, one of the four
    ranges; 42 without anything) is the last `dias` days up to `hoy`. Dates: both ends counted, from 7 days to 2 years,
    «hasta» not after `hoy`; otherwise ValueError saying why. Unit 108 (R-316): with `futuro` (comparing two periods)
    the «hasta» may be after `hoy`."""
    if desde is None and hasta is None:
        n = comprobar_dias(DIAS_POR_DEFECTO if dias is None else dias)
        return hoy - timedelta(days=n - 1), hoy
    if desde is None or hasta is None:
        raise ValueError(f"{NO_VALE}hacen falta «desde» y «hasta».")
    inicio, fin = _fecha(desde), _fecha(hasta)
    if inicio > fin:
        raise ValueError(f"{NO_VALE}el “desde” tiene que ser antes del “hasta”.")
    if fin > hoy and not futuro:
        raise ValueError(f"{NO_VALE}el “hasta” no puede ser después de hoy.")
    n = (fin - inicio).days + 1
    if n < MIN_DIAS_PERIODO:
        raise ValueError(f"{NO_VALE}tiene que tener al menos {MIN_DIAS_PERIODO} días, y tiene {n}.")
    if n > MAX_DIAS_PERIODO:
        raise ValueError(f"{NO_VALE}como mucho 2 años ({MAX_DIAS_PERIODO} días), y tiene {n}.")
    return inicio, fin


def carga(store: DataStore, historial: HistorialFC | None, hoy: date,
          dias: int = DIAS_POR_DEFECTO, periodo: tuple[date, date] | None = None) -> dict[str, Any]:
    """`{hoy, serie, pulso}`: today's CTL/ATL/TSB, the `dias` days up to today and where today's HR comes from (R1,
    R6). The series is computed whole (the EMAs need it) and only cut. Rounded to one decimal, as `tridata export-pmc`
    writes them. No activities at all: `hoy` None, `serie` []. Unit 097: with `periodo` (`desde`, `hasta`) the series
    is cut to those dates instead; `hoy` is still today's."""
    comprobar_dias(dias)
    limites = historial.limites if historial is not None else None
    serie = pmc_hasta(compute_pmc(daily_tss(store.get_activities_with_laps(ALL_SPORT_TYPES), limites)), hoy)
    puntos = [{"fecha": p["date"].isoformat(), "tss": _r1(p["tss"]), "ctl": _r1(p["ctl"]), "atl": _r1(p["atl"]),
               "tsb": _r1(p["tsb"])} for p in serie]
    hoy_punto = puntos[-1] if puntos and puntos[-1]["fecha"] == hoy.isoformat() else None
    if periodo is None:
        vistos = puntos[-dias:]
    else:
        inicio, fin = (d.isoformat() for d in periodo)
        vistos = [p for p in puntos if inicio <= p["fecha"] <= fin]
    return {"hoy": hoy_punto, "serie": vistos,
            "pulso": texto_pulso(historial.vigente(hoy) if historial is not None else None)}


def _resumen(store: DataStore, historial: HistorialFC | None, a: dict) -> dict[str, Any]:
    """The card of one activity (R2): never a 0 where Garmin gave nothing."""
    laps = store.get_laps(a["activity_id"])
    dia = date.fromisoformat(a["activity_date"])
    limites = historial.limites(dia) if historial is not None else None
    trimp = trimp_for_activity(a, laps, limites) if a.get("avg_hr") or any(v.get("avg_hr") for v in laps) else 0.0
    return {"actividad_id": a["activity_id"], "nombre": a.get("name") or _deporte(a.get("activity_type")),
            "deporte": _deporte(a.get("activity_type")), "fecha": a["activity_date"],
            "distancia_m": a.get("distance_meters") or None, "duracion_s": a.get("duration_seconds") or None,
            "pulso_medio": a.get("avg_hr") or None, "carga": _r1(trimp) if trimp else None,
            "vueltas": len(laps), "clima": store.get_weather(a["activity_id"]) is not None}


def ultima_actividad(store: DataStore, historial: HistorialFC | None, hoy: date) -> dict[str, Any] | None:
    """The newest activity up to `hoy` (one of tomorrow is not the last one), or None if there is none."""
    actividades = store.activities_between(DESDE_SIEMPRE, hoy)
    return _resumen(store, historial, actividades[-1]) if actividades else None


def _vuelta(n: int, v: dict, tipo: str | None, zonas: tuple[tuple[int, int], ...] | None) -> dict[str, Any]:
    distancia, segundos = v.get("distance_meters") or None, v.get("duration_seconds") or None
    ritmo_km = velocidad = ritmo_100 = None
    if distancia and segundos:
        if tipo in CYCLING_TYPES:
            velocidad = _r1(distancia / segundos * 3.6)
        elif tipo in SWIMMING_TYPES:
            ritmo_100 = round(segundos / distancia * 100)
        else:
            ritmo_km = round(v.get("avg_pace_seconds_per_km") or segundos / distancia * 1000)
    pulso = v.get("avg_hr") or None
    return {"n": n, "distancia_m": distancia, "duracion_s": segundos, "ritmo_s_km": ritmo_km,
            "ritmo_s_100m": ritmo_100, "velocidad_kmh": velocidad, "pulso_medio": pulso, "zona": zona(pulso, zonas),
            **_cadencia_y_desnivel(v, tipo)}


def _redondo(valor: float | None) -> int | None:
    return round(valor) if valor else None  # what Garmin did not give (or gave as 0) is None, never 0 (G-47)


def _cadencia_y_desnivel(v: dict, tipo: str | None) -> dict[str, Any]:
    """Unit 093 (R-242): only the keys of the lap's sport — run: steps per minute, stride (m) and climb (m); ride:
    pedal strokes per minute and climb; swim: arm strokes per minute and SWOLF; any other sport, none. Unit 107
    (R-130): `cadencia_unidad` says which, by the sport («ppm», «rpm», «brazadas/min»). Also over an activity (the
    summary of the detail): what it does not carry (a ride's cadence) is None."""
    if tipo in RUNNING_TYPES:
        zancada = v.get("avg_stride_length_cm") or None
        return {"cadencia": _redondo(v.get("avg_cadence")), "cadencia_unidad": "ppm",
                "zancada_m": round(zancada / 100, 2) if zancada else None,
                "subida_m": _redondo(v.get("elevation_gain_m"))}
    if tipo in CYCLING_TYPES:
        return {"cadencia": _redondo(v.get("avg_bike_cadence")), "cadencia_unidad": "rpm",
                "subida_m": _redondo(v.get("elevation_gain_m"))}
    if tipo in SWIMMING_TYPES:
        return {"cadencia": _redondo(v.get("avg_swim_cadence")), "cadencia_unidad": "brazadas/min",
                "swolf": _redondo(v.get("avg_swolf"))}
    return {}


def _deporte_zonas(tipo: str | None) -> str:
    from .umbral import deporte_de  # here: `umbral` reads this module

    return deporte_de(tipo)


def zonas_sesion(pulso: PulsoVigente | None, tipo: str | None) -> tuple[tuple[tuple[int, int], ...] | None, bool]:
    """Unit 096 (R-258): `(zonas, estimadas)` of a session of type `tipo` with the HR in force on its day."""
    return pulso.zonas_de(_deporte_zonas(tipo)) if pulso is not None else (None, False)


def _zonas_deporte(tipo: str | None, zonas: tuple[tuple[int, int], ...] | None, estimadas: bool) -> dict[str, Any]:
    """Unit 096 (C-329): which sport's zones a session uses, its threshold and «zonas de bici estimadas desde
    carrera» when they are."""
    from .umbral import NOMBRE

    deporte = _deporte_zonas(tipo)
    texto = f"zonas de {NOMBRE[deporte]}" + (" estimadas desde carrera" if estimadas else "")
    return {"deporte": deporte, "estimadas": estimadas, "umbral": zonas[-1][0] if zonas else None, "texto": texto}


def detalle_actividad(store: DataStore, historial: HistorialFC | None, actividad_id: str) -> dict[str, Any] | None:
    """`{actividad, vueltas, clima, zonas, pulso, curva, zonas_deporte}` of one activity (R3), or None if it does not
    exist. `zonas` says whether zones were confirmed for its day (without them no lap gets a zone); `clima` None: none
    synced. Unit 094: `curva` (see `_curva`), None without detail second by second (not brought yet, or tried, R-254).
    Unit 096: every zone with its sport's of that day; `zonas_deporte` says which and whether estimated. Unit 107
    (R-130): `actividad` also carries the cadence and climb of its sport (`_cadencia_y_desnivel`), for Plan's summary."""
    a = store.get_activity(actividad_id)
    if a is None:
        return None
    pulso = historial.vigente(date.fromisoformat(a["activity_date"])) if historial is not None else None
    zonas, estimadas = zonas_sesion(pulso, a.get("activity_type"))
    vueltas = [_vuelta(i, v, a.get("activity_type"), zonas)
               for i, v in enumerate(store.get_laps(actividad_id), start=1)]
    actividad = {**_resumen(store, historial, a), **_cadencia_y_desnivel(a, a.get("activity_type"))}
    return {"actividad": actividad, "vueltas": vueltas, "clima": store.get_weather(actividad_id),
            "zonas": zonas is not None, "pulso": texto_pulso(pulso),
            "curva": _curva(store.leer_detalle(actividad_id), vueltas, zonas),
            "zonas_deporte": _zonas_deporte(a.get("activity_type"), zonas, estimadas)}


# -- Unit 094 (REC-40, R-250..R-253): the HR second by second ------------------------------------------------------

MAX_PUNTOS_CURVA = 600  # the curve is reduced only to draw it; the time in zone counts every second


def _segundos_por_zona(detalle: dict[str, list[float | None]],
                       zonas: tuple[tuple[int, int], ...] | None) -> dict[str, float]:
    """R-252: each point adds the seconds until the next one (the last, one second) to the zone of its HR, with the
    zones of that day; an HR under zone 1 is zone 1 (as by laps); no HR, or no zones that day: «sin pulso»."""
    t, hr = detalle["t"], detalle["pulso"]
    segundos = _ceros()
    clave_de: dict[float, str] = {}
    for i, (ti, p) in enumerate(zip(t, hr, strict=True)):
        if ti is None:
            continue
        siguiente = t[i + 1] if i + 1 < len(t) else None
        paso = 1.0 if siguiente is None else siguiente - ti
        if paso <= 0:
            continue
        if not p or not zonas:
            clave = "sin_pulso"
        else:
            clave = clave_de.get(p) or clave_de.setdefault(p, f"z{zona(p, zonas) or 1}")
        segundos[clave] += paso
    return segundos


def _reducir(t: list[float | None], hr: list[float | None], maximo: int) -> tuple[list[float], list[float | None]]:
    """At most `maximo` points to draw: the mean of each bucket of consecutive points (HR without its gaps; a bucket
    without any HR is a gap of the line)."""
    puntos = [(ti, p) for ti, p in zip(t, hr, strict=True) if ti is not None]
    cubo = max(1, -(-len(puntos) // maximo))
    ts: list[float] = []
    ps: list[float | None] = []
    for i in range(0, len(puntos), cubo):
        trozo = puntos[i:i + cubo]
        con = [p for _, p in trozo if p]
        ts.append(round(sum(ti for ti, _ in trozo) / len(trozo), 1))
        ps.append(round(sum(con) / len(con), 1) if con else None)
    return ts, ps


def _curva(detalle: dict[str, list[float | None]] | None, vueltas: list[dict[str, Any]],
           zonas: tuple[tuple[int, int], ...] | None) -> dict[str, Any] | None:
    """R-250..R-252: `{t, pulso, fin_s, zonas_s, limites, tramos}` of a session with detail, or None. `t`/`pulso`:
    the curve reduced to `MAX_PUNTOS_CURVA` points; `fin_s`: its last second; `zonas_s`: seconds in each zone counted
    with every point; `limites`: the zones `[[desde, hasta]…]` of that day for the bands (None without them);
    `tramos`: each lap `{n, inicio_s, fin_s, zona}` by adding up their durations (the timer, as `t`)."""
    if detalle is None:
        return None
    t, pulso = _reducir(detalle["t"], detalle["pulso"], MAX_PUNTOS_CURVA)
    tramos, inicio = [], 0.0
    for v in vueltas:
        fin = inicio + float(v.get("duracion_s") or 0)
        tramos.append({"n": v["n"], "inicio_s": round(inicio), "fin_s": round(fin), "zona": v.get("zona")})
        inicio = fin
    ultimos = [ti for ti in detalle["t"] if ti is not None]
    return {"t": t, "pulso": pulso, "fin_s": ultimos[-1] if ultimos else 0,
            "zonas_s": {k: round(s) for k, s in _segundos_por_zona(detalle, zonas).items()},
            "limites": [list(z) for z in zonas] if zonas else None, "tramos": tramos}


def _media(valores: list[Any], decimales: int | None = 1) -> float | None:
    hay = [v for v in valores if v]
    return round(sum(hay) / len(hay), decimales) if hay else None


def recuperacion(store: DataStore, hoy: date) -> dict[str, dict[str, Any]]:
    """`{sueno, pulso_reposo, hrv}` of `hoy`, each with its `estado` and its 7-day mean (R4, R5)."""
    datos = store.export_all()
    noches = _por_dia(datos["sleep"], "sleep_date")
    semana = [hoy - timedelta(days=i) for i in range(DIAS_MEDIA)]

    sueno = noches.get(hoy.isoformat()) or {}
    segundos = sueno.get("total_sleep_seconds") or None
    media_sueno = _media([(noches.get(d.isoformat()) or {}).get("total_sleep_seconds") for d in semana], None)
    estado_sueno = NO_HA_LLEGADO if not segundos else (OK if sueno.get("sleep_score") else INCOMPLETO)

    stats = [store.daily_stats_on(d) or {} for d in semana]
    valor = stats[0].get("resting_hr") or None
    estado_pulso = NO_HA_LLEGADO if not valor else (
        INCOMPLETO if dia_a_medias(stats[0], store.activities_between(hoy, hoy)) else OK)

    return {
        "sueno": {"estado": estado_sueno, "segundos": round(segundos) if segundos else None,
                  "puntuacion": sueno.get("sleep_score") or None, "media_7d": media_sueno},
        "pulso_reposo": {"estado": estado_pulso, "valor": valor, "media_7d": _media([s.get("resting_hr") for s in stats])},
        "hrv": _hrv(_por_dia(datos["hrv"], "hrv_date"), hoy),
    }


def lunes_de(dia: date) -> date:
    return dia - timedelta(days=dia.weekday())


def por_semana(actividades: list[dict], primer_lunes: date, n: int,
               deporte_de: Callable[[dict], str]) -> dict[date, dict[str, list[dict]]]:
    """The `n` weeks from `primer_lunes` (Monday → sport → its activities, in the order they come): only grouping,
    each caller sums its way. Shared by Fitness › weeks and the coach's `get_weekly_summary` (unit 090). A week with
    nothing is there, empty; an activity outside the weeks is left out."""
    semanas: dict[date, dict[str, list[dict]]] = {primer_lunes + timedelta(weeks=i): {} for i in range(n)}
    for a in actividades:
        grupo = semanas.get(lunes_de(date.fromisoformat(a["activity_date"])))
        if grupo is not None:
            grupo.setdefault(deporte_de(a), []).append(a)
    return semanas


def deporte_semana(actividad: dict) -> str:
    """correr · bici · nadar · fuerza, or «otros» (a walk, yoga…)."""
    tipo = actividad.get("activity_type")
    return next((nombre for nombre, tipos in DEPORTES_SEMANA if tipo in tipos), OTROS)


def _ceros() -> dict[str, float]:
    return dict.fromkeys(ZONAS_SEMANA, 0.0)


def _zonas_por_vueltas(a: dict, laps: list[dict], zonas: tuple[tuple[int, int], ...] | None) -> dict[str, float]:
    """R-235 (S-82): each lap adds its time to the zone of its mean HR with the zones of that day. A lap with HR
    under zone 1 is zone 1 (it has HR; Z1 is the lowest). Without HR, or without zones that day: «sin pulso». A session
    without laps counts as one lap with its duration and mean HR."""
    vueltas = laps or [{"duration_seconds": a.get("duration_seconds"), "avg_hr": a.get("avg_hr")}]
    segundos = _ceros()
    for v in vueltas:
        pulso = v.get("avg_hr") or None
        clave = "sin_pulso" if pulso is None or not zonas else f"z{zona(pulso, zonas) or 1}"
        segundos[clave] += float(v.get("duration_seconds") or 0)
    return segundos


def _km_por_zona(a: dict, laps: list[dict], zonas: tuple[tuple[int, int], ...] | None) -> dict[str, float]:
    """Unit 108 (R-311, C-395): the km of a session in each zone, by laps — each lap adds its km to the zone of its
    mean HR (as `_zonas_por_vueltas`, with the zones of its sport that day). A session without laps (or whose laps
    carry no distance), all «sin pulso». The laps are scaled to the session's distance, so the zones add up to the km
    that the week counts."""
    km = float(a.get("distance_meters") or 0) / 1000
    resultado = _ceros()
    por_vuelta = [(float(v.get("distance_meters") or 0) / 1000, v.get("avg_hr") or None) for v in laps]
    suma = sum(k for k, _ in por_vuelta)
    if not suma:
        resultado["sin_pulso"] = km
        return resultado
    for k, pulso in por_vuelta:
        clave = "sin_pulso" if pulso is None or not zonas else f"z{zona(pulso, zonas) or 1}"
        resultado[clave] += k * km / suma
    return resultado


def _zonas_de(a: dict, laps: list[dict], detalle: dict[str, list[float | None]] | None,
              zonas: tuple[tuple[int, int], ...] | None) -> dict[str, float]:
    """Unit 094 (R-253, S-85): the seconds in each zone of a session — second by second where it has detail, by laps
    (S-82) where it has not."""
    return _segundos_por_zona(detalle, zonas) if detalle is not None else _zonas_por_vueltas(a, laps, zonas)


def _vacio(con_km: bool = True) -> dict[str, Any]:
    return {"horas": 0.0, "km": 0.0 if con_km else None, "carga": 0.0, "zonas": _ceros(),
            "zonas_km": _ceros() if con_km else None, "subida_m": None}


def _redondeo(t: dict[str, Any]) -> dict[str, Any]:
    """Unit 108: km with 2 decimals («81,96 km», as Strava), the climb in whole metres."""
    return {"horas": round(t["horas"], 2), "km": None if t["km"] is None else round(t["km"], 2),
            "carga": _r1(t["carga"]), "zonas": {k: round(v) for k, v in t["zonas"].items()},
            "zonas_km": None if t["zonas_km"] is None else {k: round(v, 2) for k, v in t["zonas_km"].items()},
            "subida_m": None if t["subida_m"] is None else round(t["subida_m"])}


def semanas(store: DataStore | None, historial: HistorialFC | None, hoy: date, n: int,
            ultimo_lunes: date | None = None) -> list[dict[str, Any]]:
    """R3-R6: the `n` Monday-to-Sunday weeks that end in this one (`en_curso`), oldest first, each with its five sports
    (always there, at zero if nothing) and its `total`. Activities up to `hoy`. Strength never adds km (`km` None).
    Without a store (no Garmin), the weeks at zero. Unit 093: `ultimo_lunes` makes them end in that week instead."""
    ultimo = ultimo_lunes or lunes_de(hoy)
    primer_lunes = ultimo - timedelta(weeks=n - 1)
    actividades: list[dict] = []
    vueltas_de: Callable[[str], list[dict]] = lambda _id: []  # noqa: E731
    detalle_de: Callable[[str], dict | None] = lambda _id: None  # noqa: E731
    sin_pedir: Callable[[dict], bool] = lambda _a: False  # noqa: E731
    if store is not None:
        hasta = min(hoy, ultimo + timedelta(days=6))
        actividades, vueltas_de = store.activities_between(primer_lunes, hasta), store.get_laps
        detalle_de = store.leer_detalle
        sin_pedir = lambda a: (a.get("activity_type") in DETALLE_TYPES  # noqa: E731
                               and store.estado_detalle(a["activity_id"]) is None)
    grupos = por_semana(actividades, primer_lunes, n, deporte_semana)
    resultado = []
    for lunes, por_deporte in grupos.items():
        deportes = {nombre: _vacio(nombre != "fuerza") for nombre, _ in DEPORTES_SEMANA}
        deportes[OTROS] = _vacio()
        total = _vacio()
        aproximado = False  # units 094-095 (R-253, C-319): some session whose detail can still be brought
        for nombre, lista in por_deporte.items():
            t = deportes[nombre]
            for a in lista:
                laps = vueltas_de(a["activity_id"])
                detalle = detalle_de(a["activity_id"])
                aproximado = aproximado or (detalle is None and sin_pedir(a))
                dia = date.fromisoformat(a["activity_date"])
                pulso = historial.vigente(dia) if historial is not None else None
                horas = float(a.get("duration_seconds") or 0) / 3600
                km = float(a.get("distance_meters") or 0) / 1000 if t["km"] is not None else 0.0
                trimp = 0.0
                if a.get("activity_type") in ALL_SPORT_TYPES:  # what form and fatigue count (daily_tss)
                    trimp = trimp_for_activity(a, laps, historial.limites(dia) if historial is not None else None)
                limites_zona = zonas_sesion(pulso, a.get("activity_type"))[0]  # unit 096
                zonas = _zonas_de(a, laps, detalle, limites_zona)
                km_zona = _km_por_zona(a, laps, limites_zona) if t["km"] is not None else None  # unit 108
                subida = a.get("elevation_gain_m")
                for destino in (t, total):
                    destino["horas"] += horas
                    destino["carga"] += trimp
                    if destino["km"] is not None:
                        destino["km"] += km
                    for clave, seg in zonas.items():
                        destino["zonas"][clave] += seg
                    if km_zona is not None:
                        for clave, k in km_zona.items():
                            destino["zonas_km"][clave] += k
                    if subida is not None:
                        destino["subida_m"] = (destino["subida_m"] or 0.0) + float(subida)
        resultado.append({"lunes": lunes.isoformat(), "domingo": (lunes + timedelta(days=6)).isoformat(),
                          "en_curso": lunes == lunes_de(hoy), "aproximado": aproximado,
                          "deportes": {k: _redondeo(v) for k, v in deportes.items()}, "total": _redondeo(total)})
    return resultado


def comprobar_semana(lunes: str, deporte: str, hoy: date) -> tuple[date, str]:
    """Unit 093: the Monday (`AAAA-MM-DD`, a Monday not after this week's) and the sport (`todos` or one of «Tus
    semanas») of GET /api/fitness/semana, or ValueError saying why."""
    try:
        dia = date.fromisoformat(lunes)
    except ValueError:
        raise ValueError(f"«{lunes}» no es una fecha (AAAA-MM-DD).") from None
    if dia.weekday() != 0:
        raise ValueError(f"El {dia.isoformat()} no es lunes: la semana va de lunes a domingo.")
    if dia > lunes_de(hoy):
        raise ValueError("Esa semana aún no ha llegado.")
    deportes = (TODOS, *(nombre for nombre, _ in DEPORTES_SEMANA), OTROS)
    if deporte not in deportes:
        raise ValueError(f"El deporte es uno de {', '.join(deportes)}, no «{deporte}».")
    return dia, deporte


def _cifras(semana: dict[str, Any], deporte: str, sesiones: int) -> dict[str, Any]:
    t = semana["total"] if deporte == TODOS else semana["deportes"][deporte]
    return {"horas": t["horas"], "km": t["km"], "carga": t["carga"], "sesiones": sesiones}


def actividades_semana(store: DataStore | None, historial: HistorialFC | None, lunes: date, deporte: str,
                       hoy: date) -> dict[str, Any]:
    """Unit 093 (R-239, R-240, R-243): `{lunes, domingo, en_curso, deporte, actividades, totales, media,
    semanas_media}` of the week of `lunes` up to `hoy`. `actividades`: the cards of `_resumen`, newest first, of
    `deporte` («todos» or one of «Tus semanas»), [] if none. `totales`: hours, km, load (the figures of `semanas`, no
    second aggregation) and sessions; `media`: the same, the mean of the up to 4 weeks before that have history (since
    the week of the first activity stored), `semanas_media` of them; None if there is none."""
    *antes, esta = semanas(store, historial, hoy, SEMANAS_MEDIA + 1, ultimo_lunes=lunes)
    primer_lunes = lunes - timedelta(weeks=SEMANAS_MEDIA)
    todas = [] if store is None else store.activities_between(primer_lunes, min(hoy, lunes + timedelta(days=6)))
    delas = [a for a in todas if deporte == TODOS or deporte_semana(a) == deporte]
    grupos = por_semana(delas, primer_lunes, SEMANAS_MEDIA + 1, lambda _a: deporte)
    sesiones = {lun.isoformat(): len(g.get(deporte, [])) for lun, g in grupos.items()}

    primera = store.primera_fecha_actividad() if store is not None else None
    con_historia = [s for s in antes if primera is not None and s["lunes"] >= lunes_de(primera).isoformat()]
    media = None
    if con_historia:
        previas = [_cifras(s, deporte, sesiones[s["lunes"]]) for s in con_historia]
        k = len(previas)
        media = {"horas": round(sum(p["horas"] for p in previas) / k, 2),
                 "km": None if previas[0]["km"] is None else _r1(sum(p["km"] for p in previas) / k),
                 "carga": _r1(sum(p["carga"] for p in previas) / k),
                 "sesiones": _r1(sum(p["sesiones"] for p in previas) / k)}

    de_esta = [a for a in delas if a["activity_date"] >= lunes.isoformat()]
    de_esta.sort(key=lambda a: (a["activity_date"], a.get("start_time_local") or "", a["activity_id"]), reverse=True)
    return {"lunes": esta["lunes"], "domingo": esta["domingo"], "en_curso": esta["en_curso"], "deporte": deporte,
            "actividades": [_resumen(store, historial, a) for a in de_esta] if store is not None else [],
            "totales": _cifras(esta, deporte, sesiones[esta["lunes"]]), "media": media,
            "semanas_media": len(con_historia)}


def semanas_periodo(store: DataStore | None, historial: HistorialFC | None, hoy: date, dias: int | None,
                    desde: date, hasta: date) -> list[dict[str, Any]]:
    """Unit 097 (R-263): «Tus semanas» of the period — with a shortcut, as always (`SEMANAS_DE`); with dates, the
    Monday-to-Sunday weeks that touch them. Unit 108 (R-316): only up to the week in course; the ones still to come
    are not there (a period all of it to come, [])."""
    if dias is not None:
        return semanas(store, historial, hoy, SEMANAS_DE[dias])
    ultimo = min(lunes_de(hasta), lunes_de(hoy))
    n = (ultimo - lunes_de(desde)).days // 7 + 1
    return semanas(store, historial, hoy, n, ultimo_lunes=ultimo) if n > 0 else []


def historial_desde(store: DataStore | None, desde: date) -> str | None:
    """Unit 108 (R-316): the day of the first activity stored when it is after `desde` (the period starts before the
    history), else None. Without a store, None."""
    primera = store.primera_fecha_actividad() if store is not None else None
    return primera.isoformat() if primera is not None and primera > desde else None


def datos_periodo(store: DataStore | None, inicio: date, fin: date, atajo: int | None) -> dict[str, Any]:
    """`{desde, hasta, dias, atajo, semanas}` of a period: `semanas` is how many weeks it has, the ones still to come
    too (unit 108: «el otro periodo no llega a la sem N» against «aún no ha llegado»)."""
    total = SEMANAS_DE[atajo] if atajo is not None else (lunes_de(fin) - lunes_de(inicio)).days // 7 + 1
    return {"desde": inicio.isoformat(), "hasta": fin.isoformat(), "dias": (fin - inicio).days + 1, "atajo": atajo,
            "semanas": total}


def otro_periodo(store: DataStore | None, historial: HistorialFC | None, hoy: date,
                 otro: tuple[date, date]) -> dict[str, Any]:
    """Unit 108 (R-313, R-316): the second period to compare with — `{periodo, semanas, historial_desde}`."""
    inicio, fin = otro
    return {"periodo": datos_periodo(store, inicio, fin, None),
            "semanas": semanas_periodo(store, historial, hoy, None, inicio, fin),
            "historial_desde": historial_desde(store, inicio)}


def fitness(store: DataStore, historial: HistorialFC | None, hoy: date, dias: int | None = None,
            desde: date | str | None = None, hasta: date | str | None = None,
            otro: tuple[date | str, date | str] | None = None) -> dict[str, Any]:
    """What GET /api/fitness answers: the three blocks of the tab, and (unit 090) the weeks of the range. Unit 097:
    the range is a shortcut (`dias`, 42 by default) or dates (`desde`, `hasta`; ValueError if they are not valid);
    `periodo` says which. Unit 108: `historial_desde` of the period, and with `otro` (a second period, its «hasta» and
    this one's may be after today) `otro: {periodo, semanas, historial_desde}`; no `comparacion` any more (R-264)."""
    comparando = otro is not None
    inicio, fin = periodo(hoy, dias, desde, hasta, futuro=comparando)
    atajo = None if desde is not None or hasta is not None else (DIAS_POR_DEFECTO if dias is None else dias)
    corte = None if atajo is not None else (inicio, fin)
    respuesta = {"fecha": hoy.isoformat(), "periodo": datos_periodo(store, inicio, fin, atajo),
                 "carga": carga(store, historial, hoy, atajo or DIAS_POR_DEFECTO, periodo=corte),
                 "ultima_actividad": ultima_actividad(store, historial, hoy), "recuperacion": recuperacion(store, hoy),
                 "semanas": semanas_periodo(store, historial, hoy, atajo, inicio, fin),
                 "historial_desde": historial_desde(store, inicio)}
    if otro is not None:
        respuesta["otro"] = otro_periodo(store, historial, hoy, periodo(hoy, desde=otro[0], hasta=otro[1], futuro=True))
    return respuesta
