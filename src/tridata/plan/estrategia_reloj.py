"""The strategy of a race split in steps for the watch (unit 115, REC-57 of `preparar-carrera`, R-353..R-356).

The strategy is the lines `guardar_estrategia` saved (unit 045), one per stretch and one per gel (the gels are left
out here). Each stretch says its km («km 0-5», «km 4 a 6,5»), its pace («a 4:10», or a range «a 4:05-4:15», whose middle
is taken) or «por esfuerzo» (no pace: a step without target), and optionally its time of passage at its end
(«paso 33:30», «paso 1:12:30»). The stretches go one after another from the start, without holes.

`pasos` gives the blocks of a session of the plan, written as `plan/reloj.py` reads them, so the session goes to the
watch as any other one:

- `tramos` (the default, R-354): one step per stretch, «5 km a 4:10», «3 km por esfuerzo»;
- `km`, every x km: «1 km a 4:10», the last one what is left («400 m a 4:00»);
- `minutos`, every x minutes: «15' a 4:04», the last one what is left («7' a 4:00»); it needs the time of every stretch:
  its pace, or for one «por esfuerzo» its time of passage.

The pace of a step is the mean of the part of the strategy it covers (its time over its distance, so a step across two
stretches takes both); a step that touches a stretch «por esfuerzo» goes without target. More steps than the watch
takes (`MAX_PASOS`) is an `EstrategiaMal` that proposes joining them: nothing goes half done.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..nutricion.recetas import normalizar

# The steps of one workout the watch takes. Not found in `conocimiento/garmin-entrenos.md` nor in a page of Garmin
# read for this unit: the cap the contract gives (50) until it is measured on the real watch (hallazgos of 115).
MAX_PASOS = 50
_EPS = 1e-6

_KM = re.compile(r"\bkm\s*(\d+(?:[.,]\d+)?)\s*(?:-|–|—|a\b|al\b|hasta(?:\s+el)?\b)\s*(?:km\s*)?(\d+(?:[.,]\d+)?)")
_RITMO = re.compile(r"\ba\s*(\d{1,2}):([0-5]\d)(?:\s*[-–]\s*(\d{1,2}):([0-5]\d))?")
_PASO = re.compile(r"\bpaso\s*(?:a\s*las?\s*|en\s*)?(\d{1,2}):([0-5]\d)(?::([0-5]\d))?")
COMO = {"tramos": "tramos", "tramo": "tramos", "km": "km", "kilometros": "km", "kilometro": "km", "distancia": "km",
        "minutos": "minutos", "minuto": "minutos", "min": "minutos", "tiempo": "minutos"}


class EstrategiaMal(ValueError):
    """The strategy cannot be split as asked: its text says why and what to do."""


@dataclass(frozen=True)
class Tramo:
    linea: str
    desde: float  # km
    hasta: float
    ritmo: int | None  # seconds per km; None: by effort
    paso: int | None  # the time of passage at its end, in seconds from the start


def _km(texto: str) -> float:
    return float(texto.replace(",", "."))


def _numero(x: float) -> str:
    return f"{round(x, 3):g}".replace(".", ",")


def es_gel(linea: str) -> bool:
    return normalizar(linea).startswith("gel")


def tramos(lineas: list[str]) -> list[Tramo]:
    """The stretches of the strategy, in order, or `EstrategiaMal` saying what is missing (R-355)."""
    salida = []
    for linea in (str(x).strip() for x in lineas):
        if not linea or es_gel(linea):
            continue
        texto = linea.lower()
        km = _KM.search(texto)
        if km is None:
            raise EstrategiaMal(f"El tramo «{linea}» no dice sus km: escríbelo como «km 0-5 · a 4:10» y guárdala otra "
                                "vez")
        desde, hasta = _km(km[1]), _km(km[2])
        if hasta <= desde:
            raise EstrategiaMal(f"El tramo «{linea}» acaba antes de empezar")
        resto = texto[:km.start()] + " " + texto[km.end():]
        ritmo = _RITMO.search(resto)
        segundos = None
        if ritmo is not None:
            uno = int(ritmo[1]) * 60 + int(ritmo[2])
            otro = int(ritmo[3]) * 60 + int(ritmo[4]) if ritmo[3] else uno
            segundos = round((uno + otro) / 2)
        paso = _PASO.search(resto)
        cuando = None
        if paso is not None:
            cuando = (int(paso[1]) * 3600 + int(paso[2]) * 60 + int(paso[3]) if paso[3]
                      else int(paso[1]) * 60 + int(paso[2]))
        salida.append(Tramo(linea, desde, hasta, segundos if segundos else None, cuando))
    if not salida:
        raise EstrategiaMal("Esa carrera no tiene estrategia por tramos guardada: prepárala antes sobre su ruta (cada "
                            "tramo con sus km y su ritmo) y guárdala con guardar_estrategia")
    salida.sort(key=lambda t: t.desde)
    if salida[0].desde > _EPS:
        raise EstrategiaMal(f"Falta un tramo entre la salida y el km {_numero(salida[0].desde)}")
    for antes, despues in zip(salida, salida[1:], strict=False):
        if despues.desde > antes.hasta + _EPS:
            raise EstrategiaMal(f"Falta un tramo entre el km {_numero(antes.hasta)} y el km {_numero(despues.desde)}")
        if despues.desde < antes.hasta - _EPS:
            raise EstrategiaMal(f"Los tramos «{antes.linea}» y «{despues.linea}» se pisan")
    return salida


def _distancia(km: float) -> str:
    metros = round(km * 1000)
    return f"{_numero(metros / 1000)} km" if metros >= 1000 else f"{metros} m"


def _tiempo(segundos: float) -> str:
    s = round(segundos)
    minutos, resto = divmod(s, 60)
    if not minutos:
        return f'{resto}"'
    return f"{minutos}'{resto:02d}\"" if resto else f"{minutos}'"


def _bloque(cuanto: str, ritmo: float | None) -> str:
    if ritmo is None:
        return f"{cuanto} por esfuerzo"
    s = round(ritmo)
    return f"{cuanto} a {s // 60}:{s % 60:02d}"


def _como(como: str | None) -> str:
    clave = normalizar(como or "tramos").strip()
    if clave not in COMO:
        raise EstrategiaMal(f"No sé partirla «{como}»: por los tramos de la estrategia, cada tantos km o cada tantos "
                            "minutos")
    return COMO[clave]


def _tiempos(lista: list[Tramo]) -> list[tuple[float, float]]:
    """(start, end) in seconds of each stretch: its pace by its km, or for one by effort its time of passage."""
    salida: list[tuple[float, float]] = []
    reloj = 0.0
    for t in lista:
        if t.ritmo is not None:
            fin = reloj + t.ritmo * (t.hasta - t.desde)
        elif t.paso is not None and t.paso > reloj:
            fin = float(t.paso)
        else:
            raise EstrategiaMal(f"Para partirla por tiempo necesito el ritmo o el tiempo de paso de cada tramo, y "
                                f"«{t.linea}» no lo tiene: pártela por km o por sus tramos")
        salida.append((reloj, fin))
        reloj = fin
    return salida


def _cortes(total: float, cada: float) -> list[tuple[float, float]]:
    cortes, inicio = [], 0.0
    while total - inicio > _EPS:
        fin = min(inicio + cada, total)
        cortes.append((inicio, fin))
        inicio = fin
        if len(cortes) > MAX_PASOS * 20:  # a cut so small it would never end: it is too many anyway
            break
    return cortes


def _demasiados(n: int) -> EstrategiaMal:
    return EstrategiaMal(f"Salen {n} pasos y tu reloj admite como mucho {MAX_PASOS}: junta tramos (por ejemplo, cada "
                         "2 km en vez de cada km, o por los tramos de la estrategia)")


def pasos(lineas: list[str], como: str | None = "tramos", cada: float | None = None) -> list[str]:
    """The blocks of the session of the strategy (`plan/reloj.py` reads them), split by its stretches, every `cada` km
    or every `cada` minutes; `EstrategiaMal` with why (R-355, R-356)."""
    manera = _como(como)
    lista = tramos(lineas)
    if manera == "tramos":
        if len(lista) > MAX_PASOS:
            raise _demasiados(len(lista))
        return [_bloque(_distancia(t.hasta - t.desde), t.ritmo) for t in lista]
    if cada is None or cada <= 0:
        raise EstrategiaMal("Dime cada cuántos km o cada cuántos minutos partirla")
    if manera == "km":
        cortes = _cortes(lista[-1].hasta, float(cada))
        if len(cortes) > MAX_PASOS:
            raise _demasiados(len(cortes))
        salida = []
        for a, b in cortes:
            dentro = [(t, min(b, t.hasta) - max(a, t.desde)) for t in lista]
            dentro = [(t, d) for t, d in dentro if d > _EPS]
            ritmo = None if any(t.ritmo is None for t, _ in dentro) else (
                sum(t.ritmo * d for t, d in dentro if t.ritmo is not None) / sum(d for _, d in dentro))
            salida.append(_bloque(_distancia(b - a), ritmo))
        return salida
    tiempos = _tiempos(lista)
    cortes = _cortes(tiempos[-1][1], float(cada) * 60)
    if len(cortes) > MAX_PASOS:
        raise _demasiados(len(cortes))
    salida = []
    for a, b in cortes:
        partes = []
        for t, (t0, t1) in zip(lista, tiempos, strict=True):
            solapa = min(b, t1) - max(a, t0)
            if solapa > _EPS:
                partes.append((t, solapa, solapa * (t.hasta - t.desde) / (t1 - t0)))
        ritmo = None if any(t.ritmo is None for t, _, _ in partes) else (
            sum(s for _, s, _ in partes) / sum(km for _, _, km in partes))
        salida.append(_bloque(_tiempo(b - a), ritmo))
    return salida


__all__ = ["COMO", "MAX_PASOS", "EstrategiaMal", "Tramo", "es_gel", "pasos", "tramos"]
