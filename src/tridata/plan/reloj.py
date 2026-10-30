"""A session of the plan to the watch (unit 042, REC-26 of `mandar-sesion-reloj`): its blocks as a guided running
workout of Garmin Connect, scheduled on its day, and the same as a FIT file when Garmin fails (R-159, G-58).

The blocks are lines of text with a fixed shape (unit 040), read here without AI:

- an optional label («calentamiento», «enfriar», «vuelta a la calma»), an optional «N×» and how much: a time
  («20'», «12'30"», «90"», «1h30») or a distance («10 km», «2,5 km», «400 m»; a bare number after «N×» is metres);
- the pace, «a 4:00» (the target is that pace ±5" per km) or «a 3:55-4:05» (that range), as a range of speed in
  m/s; «suave», «fácil», «trote», «rodaje» or «por esfuerzo»: no target;
- after «N×», the recovery between parentheses: «(2' rec)», «(200 m rec trote)», or with its pace and no «rec»:
  «(5' a 4:50)»; written with «con» it is the same: «con 3' de recuperación suave» (bug 053);
- a block that is only a recovery, «3' rec», is a recovery step without target (bug 053);
- what does not change the step is read and left out: «por km» or «el km» after the pace, «continuos»,
  «esfuerzo cómodo» and a note of the pulse, «pulso por debajo de 150» (bug 053).

Anything else in a block (a word it does not know, a recovery without how much, a line without time or distance)
makes the whole session not translatable, saying which block (R6): nothing is sent half done. Without a label, the
first block of several is the warm-up, the last the cool-down and the rest the work. Only running (bike and
swimming are out of scope).

`mandar` replaces what was sent before (R-160): unschedule and delete the old one, then upload and schedule the new
one; if scheduling fails, the new one is deleted too. Nothing here logs a text of the plan.
"""
from __future__ import annotations

import logging
import os
import re
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from ..garmin_client import GarminAuthError, GarminClient, GarminRateLimitError
from ..nutricion.recetas import normalizar

log = logging.getLogger(__name__)

MARGEN_S_KM = 5  # the target of a pace: ±5" per km
_NO_CARRERA = re.compile(r"\b(bici\w*|rodillo|ciclismo|natacion|nadar|piscina|gimnasio|gym|fuerza)\b")
_ETIQUETAS = {"calentamiento": "warmup", "calentar": "warmup", "enfriar": "cooldown", "enfriamiento": "cooldown",
              "calma": "cooldown"}
_PALABRAS = {"suave", "suaves", "facil", "faciles", "trote", "rodaje", "por", "esfuerzo", "calentamiento", "calentar",
             "enfriar", "enfriamiento", "vuelta", "a", "la", "calma", "de", "en", "continuo", "continuos", "seguido",
             "seguidos", "comodo", "comoda"}
_RECUPERA = {"rec", "recuperacion", "recuperar"}  # a block that is only a recovery (bug 053)
_CANTIDAD = re.compile(
    r"\s*(?:(?P<h>\d+)\s*h\s*(?P<hm>\d{1,2})?\s*'?"
    r"|(?P<m>\d+)\s*'(?:\s*(?P<ms>\d{1,2})\s*\")?"
    r"|(?P<s>\d+)\s*\""
    r"|(?P<km>\d+(?:[.,]\d+)?)\s*(?:km|k)\b"
    r"|(?P<metros>\d+)\s*m\b"
    r"|(?P<desnudo>\d+)\b)")
_RITMO = re.compile(r"\ba\s*(\d{1,2}):([0-5]\d)(?:\s*-\s*(\d{1,2}):([0-5]\d))?\s*(?:min\s*)?(?:(?:/|por|el)\s*km\b)?")
_CON_REC = re.compile(r"\bcon\s+([^(),]*\brec\w*[^(),]*)")  # «con 3' de recuperación suave» = «(3' rec suave)»
_PULSO = re.compile(r"\b(?:pulso|fc)\s+(?:por debajo de|menos de|bajo|<)\s*\d{2,3}(?:\s*ppm)?")
_PARENTESIS = re.compile(r"\(([^)]*)\)")

_PASO = {"warmup": (1, "Calentamiento"), "cooldown": (2, "Vuelta a la calma"), "interval": (3, "Bloque"),
         "recovery": (4, "Recuperación"), "repeat": (6, "Repetición")}
_FIN = {"distance": {"conditionTypeId": 1, "conditionTypeKey": "distance", "displayOrder": 1, "displayable": True},
        "time": {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True},
        "iterations": {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7,
                       "displayable": False}}
_SIN_OBJETIVO = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
_RITMO_OBJETIVO = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6}
_CARRERA = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}


class NoTraducible(ValueError):
    """The session cannot go to the watch as it is (R6): its text says why, naming the block."""


class GarminNoLlega(RuntimeError):
    """Garmin did not take the session or did not answer (R-159): its text says why, in Andrés's words."""


class SinCredenciales(GarminAuthError):
    """GARMIN_EMAIL / GARMIN_PASSWORD are missing in `.env`."""


@dataclass(frozen=True)
class _Cuanto:
    fin: str  # "time" (seconds) | "distance" (metres)
    valor: float
    en_km: bool = False

    def texto(self) -> str:
        if self.fin == "distance":
            if self.en_km:
                km = self.valor / 1000
                return f"{km:g} km".replace(".", ",")
            return f"{self.valor:g} m"
        s = round(self.valor)
        if s < 60:
            return f'{s}"'
        if s >= 3600:
            h, m = divmod(s // 60, 60)
            return f"{h}h{m:02d}" if m else f"{h}h"
        m, resto = divmod(s, 60)
        return f"{m}'{resto:02d}\"" if resto else f"{m}'"


@dataclass(frozen=True)
class _Bloque:
    veces: int | None
    cuanto: _Cuanto
    ritmo: tuple[int, int] | None  # (fastest, slowest) in seconds per km
    recuperacion: _Cuanto | None
    tipo: str | None  # a label: "warmup" | "cooldown" | "recovery"
    ritmo_rec: tuple[int, int] | None = None  # the pace of the recovery, «(5' a 4:50)» (bug 053)


# -- reading the blocks -------------------------------------------------------------------------------------------

def _normal(texto: str) -> str:
    s = texto.lower()
    for a, b in (("’", "'"), ("´", "'"), ("`", "'"), ("′", "'"), ("″", '"'), ("–", "-"), ("—", "-"),
                 ("“", '"'), ("”", '"'), ("''", '"')):
        s = s.replace(a, b)
    s = re.sub(r"(\d)\s*[x×]\s*(\d)", r"\1×\2", s)
    return " ".join(s.split())


def _cantidad(s: str, *, desnudo: bool) -> tuple[_Cuanto, str] | None:
    m = _CANTIDAD.match(s)
    if m is None or (m["desnudo"] is not None and not desnudo):
        return None
    if m["h"] is not None:
        cuanto = _Cuanto("time", int(m["h"]) * 3600 + int(m["hm"] or 0) * 60)
    elif m["m"] is not None:
        cuanto = _Cuanto("time", int(m["m"]) * 60 + int(m["ms"] or 0))
    elif m["s"] is not None:
        cuanto = _Cuanto("time", int(m["s"]))
    elif m["km"] is not None:
        cuanto = _Cuanto("distance", round(float(m["km"].replace(",", ".")) * 1000), en_km=True)
    else:
        cuanto = _Cuanto("distance", int(m["metros"] or m["desnudo"]))
    if cuanto.valor <= 0:
        return None
    return cuanto, s[m.end():]


def _ritmo_s(minutos: str, segundos: str) -> int:
    return int(minutos) * 60 + int(segundos)


def _bloque(texto: str) -> _Bloque:
    raro = NoTraducible(f"No sé leer el bloque «{texto}»: dime su tiempo o distancia y su ritmo")
    s = _normal(texto)
    tipo = None
    etiqueta = re.match(r"(calentamiento|calentar|enfriar|enfriamiento|vuelta a la calma)\s*:?\s*", s)
    if etiqueta:
        tipo = _ETIQUETAS[etiqueta.group(1).split()[-1]]
        s = s[etiqueta.end():]
    veces = None
    repeticion = re.match(r"(\d+)\s*×\s*", s)
    if repeticion:
        veces = int(repeticion.group(1))
        s = s[repeticion.end():]
    leido = _cantidad(s, desnudo=veces is not None)
    if leido is None or (veces is not None and veces < 1):
        raise raro
    cuanto, s = leido

    con = _CON_REC.search(s) if veces is not None else None
    if con:
        s = f"{s[:con.start()]}({con.group(1)}){s[con.end():]}"
    recuperacion = ritmo_rec = None
    for dentro in _PARENTESIS.findall(s):
        marcado = _RITMO.search(dentro)
        if ("rec" not in dentro and marcado is None) or veces is None or recuperacion is not None:
            raise raro
        if marcado:
            ritmo_rec = _rango(marcado)
            dentro = dentro[:marcado.start()] + " " + dentro[marcado.end():]
        resto = re.sub(r"\b(rec\w*|de|trote|suave|andando|al)\b", " ", dentro)
        leida = _cantidad(resto.strip(), desnudo=True)
        if leida is None or leida[1].strip():
            raise raro
        recuperacion = leida[0]
    s = _PARENTESIS.sub(" ", s)
    s = _PULSO.sub(" ", s)

    ritmo = None
    marcado = _RITMO.search(s)
    if marcado:
        ritmo = _rango(marcado)
        s = s[:marcado.start()] + " " + s[marcado.end():]
    if (ritmo is not None and ritmo[0] <= 0) or (ritmo_rec is not None and ritmo_rec[0] <= 0):
        raise raro
    palabras = [p for p in re.split(r"[\s,.;:+]+", normalizar(s)) if p]
    if veces is None and palabras and palabras[0] in _RECUPERA:  # «3' rec»: only a recovery, without target
        if ritmo is not None or tipo is not None or any(p not in _RECUPERA | {"de", "trote", "suave", "andando"}
                                                        for p in palabras):
            raise raro
        return _Bloque(veces=None, cuanto=cuanto, ritmo=None, recuperacion=None, tipo="recovery")
    for palabra in palabras:
        if palabra not in _PALABRAS:
            raise raro
        tipo = tipo or _ETIQUETAS.get(palabra)
    return _Bloque(veces=veces, cuanto=cuanto, ritmo=ritmo, recuperacion=recuperacion, tipo=tipo, ritmo_rec=ritmo_rec)


def _rango(marcado: re.Match[str]) -> tuple[int, int]:
    """(fastest, slowest) of a pace «a 4:00» (±5") or «a 3:55-4:05»."""
    uno = _ritmo_s(marcado.group(1), marcado.group(2))
    if marcado.group(3):
        otro = _ritmo_s(marcado.group(3), marcado.group(4))
        return min(uno, otro), max(uno, otro)
    return uno - MARGEN_S_KM, uno + MARGEN_S_KM


def _bloques(sesion: Mapping[str, Any]) -> list[tuple[str, _Bloque]]:
    """[(step type, block)] of the session, or NoTraducible."""
    titulo = str(sesion.get("titulo") or "")
    textos = [str(b) for b in sesion.get("bloques") or []]
    if _NO_CARRERA.search(normalizar(" ".join([titulo, *textos]))):
        raise NoTraducible(f"Solo sé mandar al reloj sesiones de carrera a pie: «{titulo}» no lo es")
    if not textos:
        raise NoTraducible(f"«{titulo}» no tiene bloques que mandar al reloj")
    leidos = [_bloque(t) for t in textos]
    tipos = []
    for i, b in enumerate(leidos):
        if b.veces is not None:
            tipos.append("repeat")
        elif b.tipo:
            tipos.append(b.tipo)
        elif len(leidos) > 1 and i == 0:
            tipos.append("warmup")
        elif len(leidos) > 1 and i == len(leidos) - 1:
            tipos.append("cooldown")
        else:
            tipos.append("interval")
    return list(zip(tipos, leidos, strict=True))


# -- what the card shows ------------------------------------------------------------------------------------------

def _mmss(s: int) -> str:
    return f"{s // 60}:{s % 60:02d}"


def _ritmo_texto(b: _Bloque) -> str:
    return f"a {_mmss(b.ritmo[0])}-{_mmss(b.ritmo[1])} /km" if b.ritmo else "sin ritmo"


def lineas(sesion: Mapping[str, Any]) -> list[str]:
    """One line per block with how much and its margin of pace (C-199): «4 × 12' a 3:55-4:05 /km, con 2' de
    recuperación»."""
    salida = []
    for tipo, b in _bloques(sesion):
        if tipo == "repeat":
            linea = f"{b.veces} × {b.cuanto.texto()} {_ritmo_texto(b)}"
            if b.recuperacion:
                linea += f", con {b.recuperacion.texto()} de recuperación"
                if b.ritmo_rec:
                    linea += f" a {_mmss(b.ritmo_rec[0])}-{_mmss(b.ritmo_rec[1])} /km"
        else:
            linea = f"{_PASO[tipo][1]}: {b.cuanto.texto()} {_ritmo_texto(b)}"
        salida.append(linea)
    return salida


# -- the workout of Garmin Connect ------------------------------------------------------------------------------

def _tipo_paso(tipo: str) -> dict[str, Any]:
    ident, _ = _PASO[tipo]
    return {"stepTypeId": ident, "stepTypeKey": tipo, "displayOrder": ident}


def _paso(orden: int, tipo: str, cuanto: _Cuanto, ritmo: tuple[int, int] | None, grupo: int | None) -> dict:
    paso: dict[str, Any] = {"type": "ExecutableStepDTO", "stepOrder": orden, "stepType": _tipo_paso(tipo),
                            "endCondition": dict(_FIN[cuanto.fin]), "endConditionValue": cuanto.valor}
    if ritmo:  # the speeds: from the slowest pace (less m/s) to the fastest
        paso.update(targetType=dict(_RITMO_OBJETIVO), targetValueOne=1000 / ritmo[1], targetValueTwo=1000 / ritmo[0])
    else:
        paso["targetType"] = dict(_SIN_OBJETIVO)
    if grupo is not None:
        paso["childStepId"] = grupo
    return paso


def _segundos(cuanto: _Cuanto, ritmo: tuple[int, int] | None) -> float:
    if cuanto.fin == "time":
        return cuanto.valor
    return cuanto.valor / 1000 * (sum(ritmo) / 2) if ritmo else 0


def a_garmin(sesion: Mapping[str, Any]) -> dict[str, Any]:
    """The JSON of a guided running workout (`upload_workout`) for the session, or NoTraducible (R6)."""
    pasos: list[dict] = []
    orden = grupos = 0
    total = 0.0
    for tipo, b in _bloques(sesion):
        orden += 1
        if tipo != "repeat":
            pasos.append(_paso(orden, tipo, b.cuanto, b.ritmo, None))
            total += _segundos(b.cuanto, b.ritmo)
            continue
        grupos += 1
        veces = b.veces or 1
        grupo: dict[str, Any] = {"type": "RepeatGroupDTO", "stepOrder": orden, "stepType": _tipo_paso("repeat"),
                                 "childStepId": grupos, "numberOfIterations": veces, "smartRepeat": False,
                                 "endCondition": dict(_FIN["iterations"]), "endConditionValue": float(veces),
                                 "workoutSteps": []}
        orden += 1
        grupo["workoutSteps"].append(_paso(orden, "interval", b.cuanto, b.ritmo, grupos))
        vuelta = _segundos(b.cuanto, b.ritmo)
        if b.recuperacion:
            orden += 1
            grupo["workoutSteps"].append(_paso(orden, "recovery", b.recuperacion, b.ritmo_rec, grupos))
            vuelta += _segundos(b.recuperacion, b.ritmo_rec)
        total += veces * vuelta
        pasos.append(grupo)
    return {"workoutName": str(sesion["titulo"]), "description": "Desde TriData", "sportType": dict(_CARRERA),
            "estimatedDurationInSecs": round(total),
            "workoutSegments": [{"segmentOrder": 1, "sportType": dict(_CARRERA), "workoutSteps": pasos}]}


# -- unit 061 (R4): a workout of Garmin Connect back to the text of a session ------------------------------------------

DESDE_TRIDATA = "Desde TriData"  # the description `a_garmin` writes: not a text of Andrés's


class _NoSeDice(ValueError):
    """A step the text of a session cannot say (a heart-rate target, the lap button, a nested repeat…)."""


def _cuanto_de(paso: Mapping[str, Any]) -> _Cuanto:
    fin = (paso.get("endCondition") or {}).get("conditionTypeKey")
    valor = paso.get("endConditionValue")
    if fin not in ("time", "distance") or not valor or float(valor) <= 0:
        raise _NoSeDice(fin)
    if fin == "time":
        return _Cuanto("time", round(float(valor)))
    metros = round(float(valor))
    return _Cuanto("distance", metros, en_km=metros >= 1000 and metros % 100 == 0)


def _ritmo_de(paso: Mapping[str, Any]) -> tuple[int, int] | None:
    """(fastest, slowest) in seconds per km of a pace target; None without a target."""
    for clave, uno, dos in (("targetType", "targetValueOne", "targetValueTwo"),
                            ("secondaryTargetType", "secondaryTargetValueOne", "secondaryTargetValueTwo")):
        tipo = (paso.get(clave) or {}).get("workoutTargetTypeKey")
        if clave == "secondaryTargetType":
            if tipo not in (None, "no.target"):
                raise _NoSeDice(tipo)
            continue
        if tipo in (None, "no.target"):
            return None
        if tipo != "pace.zone" or not paso.get(uno) or not paso.get(dos):
            raise _NoSeDice(tipo)
        rapido, lento = sorted(round(1000 / float(paso[v])) for v in (uno, dos))
        return rapido, lento
    return None


def _ritmo_dicho(ritmo: tuple[int, int]) -> str:
    """«a 4:10» when it is that pace ±5" (what `_rango` reads back), else the range «a 4:00-4:20»."""
    if ritmo[1] - ritmo[0] == 2 * MARGEN_S_KM:
        return f"a {_mmss(ritmo[0] + MARGEN_S_KM)}"
    return f"a {_mmss(ritmo[0])}-{_mmss(ritmo[1])}"


def _tipo_de(paso: Mapping[str, Any]) -> str:
    return str((paso.get("stepType") or {}).get("stepTypeKey") or "")


def _repeticion_dicha(grupo: Mapping[str, Any]) -> str:
    """«4×1 km a 4:10 (2' rec)»: the work and, optionally, its recovery (with its pace: «(5' a 4:50)»)."""
    veces, hijos = grupo.get("numberOfIterations"), list(grupo.get("workoutSteps") or [])
    if (not isinstance(veces, int) or veces < 1 or not 1 <= len(hijos) <= 2
            or any(h.get("type") != "ExecutableStepDTO" for h in hijos) or _tipo_de(hijos[0]) != "interval"):
        raise _NoSeDice("repeat")
    ritmo = _ritmo_de(hijos[0])
    texto = f"{veces}×{_cuanto_de(hijos[0]).texto()}" + (f" {_ritmo_dicho(ritmo)}" if ritmo else "")
    if len(hijos) == 2:
        if _tipo_de(hijos[1]) != "recovery":
            raise _NoSeDice("repeat")
        cuanto, ritmo_rec = _cuanto_de(hijos[1]), _ritmo_de(hijos[1])
        texto += f" ({cuanto.texto()} {_ritmo_dicho(ritmo_rec) if ritmo_rec else 'rec'})"
    return texto


def _bloques_dichos(pasos: list[Mapping[str, Any]]) -> list[str]:
    """One line per step, as the coach writes them; a label only where the place of the line would say another
    type (`_bloques`: the first of several is the warm-up, the last the cool-down)."""
    salida = []
    for i, paso in enumerate(pasos):
        if paso.get("type") == "RepeatGroupDTO":
            salida.append(_repeticion_dicha(paso))
            continue
        if paso.get("type") != "ExecutableStepDTO":
            raise _NoSeDice(str(paso.get("type")))
        tipo, cuanto, ritmo = _tipo_de(paso), _cuanto_de(paso), _ritmo_de(paso)
        if tipo == "recovery":
            if ritmo is not None:
                raise _NoSeDice("recovery with a pace")
            salida.append(f"{cuanto.texto()} rec")
            continue
        por_sitio = ("warmup" if len(pasos) > 1 and i == 0 else
                     "cooldown" if len(pasos) > 1 and i == len(pasos) - 1 else "interval")
        etiquetas = {"warmup": "calentamiento ", "cooldown": "vuelta a la calma ", por_sitio: ""}
        if tipo not in etiquetas:  # an interval where its place says warm-up or cool-down; another type
            raise _NoSeDice(tipo)
        cola = f" {_ritmo_dicho(ritmo)}" if ritmo else (" suave" if tipo in ("warmup", "cooldown") else "")
        salida.append(f"{etiquetas[tipo]}{cuanto.texto()}{cola}")
    return salida


def _firma(entreno: Mapping[str, Any]) -> list[tuple]:
    """What makes two workouts the same for the watch: type, end and target (in s/km) of each step, in order."""
    def una(p: Mapping[str, Any]) -> tuple:
        if p.get("type") == "RepeatGroupDTO":
            return ("repeat", p.get("numberOfIterations"), tuple(una(h) for h in p.get("workoutSteps") or []))
        fin = (p.get("endCondition") or {}).get("conditionTypeKey")
        return (_tipo_de(p), fin, round(float(p.get("endConditionValue") or 0)), _ritmo_de(p))
    return [una(p) for s in entreno.get("workoutSegments") or [] for p in s.get("workoutSteps") or []]


def de_garmin(entreno: Mapping[str, Any]) -> tuple[str, list[str]]:
    """The title and the blocks of a session for a workout of Garmin Connect (the inverse of `a_garmin`): a running
    workout whose steps the text can say comes back as the coach writes it («15' a 5:45», «4×1 km a 4:10 (2' rec)»),
    and only if sending it again gives the same workout. Anything else (another sport, a heart-rate or power target,
    the lap button): its duration and Garmin's description as it is, one block per line."""
    titulo = " ".join(str(entreno.get("workoutName") or "").split()) or "Entreno de Garmin"
    segmentos = list(entreno.get("workoutSegments") or [])
    if (entreno.get("sportType") or {}).get("sportTypeKey") == "running" and len(segmentos) == 1:
        try:
            bloques = _bloques_dichos(list(segmentos[0].get("workoutSteps") or []))
            if bloques and _firma(a_garmin({"titulo": titulo, "bloques": bloques})) == _firma(entreno):
                return titulo, bloques
        except (_NoSeDice, NoTraducible, TypeError, ValueError):
            pass
    bloques = []
    segundos = entreno.get("estimatedDurationInSecs")
    if isinstance(segundos, (int, float)) and segundos > 0:
        bloques.append(_Cuanto("time", round(segundos)).texto())
    descripcion = str(entreno.get("description") or "").strip()
    if descripcion and descripcion != DESDE_TRIDATA:
        bloques += [linea.strip() for linea in descripcion.splitlines() if linea.strip()]
    return titulo, bloques


# -- the same as a FIT file (R-159) ------------------------------------------------------------------------------

_FIT_EPOCA = datetime(1989, 12, 31, tzinfo=timezone.utc)
_NADA32 = 0xFFFFFFFF
_INTENSIDAD = {"interval": 0, "recovery": 4, "warmup": 2, "cooldown": 3}
_CRC = (0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
        0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400)
# (field, size, base type) of each message, as the FIT profile numbers them
_DEF_FICHERO = ((0, 1, 0x00), (1, 2, 0x84), (2, 2, 0x84), (3, 4, 0x8C), (4, 4, 0x86))
_DEF_PASO = ((254, 2, 0x84), (1, 1, 0x00), (2, 4, 0x86), (3, 1, 0x00), (4, 4, 0x86), (5, 4, 0x86), (6, 4, 0x86),
             (7, 1, 0x00))


def _crc(datos: bytes) -> int:
    crc = 0
    for byte in datos:
        for nibble in (byte & 0xF, byte >> 4):
            tmp = _CRC[crc & 0xF]
            crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC[nibble]
    return crc


def _definicion(local: int, global_: int, campos: tuple[tuple[int, int, int], ...]) -> bytes:
    return (bytes([0x40 | local, 0, 0]) + struct.pack("<HB", global_, len(campos))
            + b"".join(bytes(c) for c in campos))


def _paso_fit(indice: int, tipo: str, cuanto: _Cuanto, ritmo: tuple[int, int] | None) -> bytes:
    duracion, valor = (0, round(cuanto.valor * 1000)) if cuanto.fin == "time" else (1, round(cuanto.valor * 100))
    if ritmo:  # speed, custom range in mm/s
        objetivo, bajo, alto = 0, round(1000 / ritmo[1] * 1000), round(1000 / ritmo[0] * 1000)
    else:
        objetivo, bajo, alto = 2, _NADA32, _NADA32  # open
    return b"\x01" + struct.pack("<HBIBIIIB", indice, duracion, valor, objetivo, 0, bajo, alto, _INTENSIDAD[tipo])


def fit_local(sesion: Mapping[str, Any]) -> bytes:
    """The session as a FIT workout file (file_id, workout and its workout_steps), made here without Garmin."""
    pasos: list[bytes] = []
    for tipo, b in _bloques(sesion):
        if tipo != "repeat":
            pasos.append(_paso_fit(len(pasos), tipo, b.cuanto, b.ritmo))
            continue
        desde = len(pasos)
        pasos.append(_paso_fit(len(pasos), "interval", b.cuanto, b.ritmo))
        if b.recuperacion:
            pasos.append(_paso_fit(len(pasos), "recovery", b.recuperacion, b.ritmo_rec))
        # repeat_until_steps_cmplt: from step `desde`, `veces` times
        pasos.append(b"\x01" + struct.pack("<HBIBIIIB", len(pasos), 6, desde, 2, b.veces or 1, _NADA32, _NADA32, 0))
    dia = date.fromisoformat(str(sesion["fecha"]))
    creado = int((datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc) - _FIT_EPOCA).total_seconds())
    nombre = str(sesion["titulo"]).encode("utf-8")[:47] + b"\0"
    datos = (_definicion(0, 0, _DEF_FICHERO) + b"\x00" + struct.pack("<BHHII", 5, 255, 0, 1, creado)
             + _definicion(1, 26, ((4, 1, 0x00), (6, 2, 0x84), (8, len(nombre), 0x07)))
             + b"\x01" + struct.pack("<BH", 1, len(pasos)) + nombre
             + _definicion(1, 27, _DEF_PASO) + b"".join(pasos))
    cabecera = struct.pack("<BBHI4s", 14, 0x20, 2132, len(datos), b".FIT")
    cabecera += struct.pack("<H", _crc(cabecera))
    fichero = cabecera + datos
    return fichero + struct.pack("<H", _crc(fichero))


# -- to Garmin Connect -------------------------------------------------------------------------------------------

def cliente_garmin(entorno: Mapping[str, str] | None = None) -> GarminClient:
    """A logged-in `GarminClient` with GARMIN_EMAIL / GARMIN_PASSWORD (the same way as the daily sync, G-58)."""
    entorno = os.environ if entorno is None else entorno
    email, password = entorno.get("GARMIN_EMAIL"), entorno.get("GARMIN_PASSWORD")
    if not email or not password:
        raise SinCredenciales("GARMIN_EMAIL / GARMIN_PASSWORD are missing")
    cliente = GarminClient(email, password)
    cliente.login()
    return cliente


def mandar(sesion: Mapping[str, Any], conectar: Callable[[], Any],
           al_quitar: Callable[[], None] | None = None) -> tuple[int, int]:
    """Uploads the session and schedules it on its day: (workoutId, workoutScheduleId).

    If it had been sent (`garmin_workout_id`, `garmin_schedule_id`), the old one is unscheduled and deleted first and
    `al_quitar()` is called (R-160). NoTraducible before connecting; GarminNoLlega if Garmin fails (R-159).
    """
    entreno = a_garmin(sesion)
    dia = date.fromisoformat(str(sesion["fecha"]))
    try:
        cliente = conectar()
        viejo_programado, viejo = sesion.get("garmin_schedule_id"), sesion.get("garmin_workout_id")
        if viejo_programado:
            cliente.quitar_programado(int(viejo_programado))
        if viejo:
            cliente.borrar_entreno(int(viejo))
        if (viejo or viejo_programado) and al_quitar is not None:
            al_quitar()
        workout_id = cliente.subir_entreno(entreno)
        try:
            schedule_id = cliente.programar(workout_id, dia)
        except Exception:
            try:  # never a workout left in the library without its day
                cliente.borrar_entreno(workout_id)
            except Exception:  # noqa: BLE001 - the first error is the one that is said
                log.warning("could not delete the workout that was not scheduled")
            raise
    except SinCredenciales:
        raise GarminNoLlega("faltan tus datos de Garmin (GARMIN_EMAIL y GARMIN_PASSWORD en .env)") from None
    except GarminRateLimitError:
        raise GarminNoLlega("Garmin está limitando las peticiones (429): inténtalo más tarde") from None
    except GarminAuthError:
        raise GarminNoLlega("no he podido entrar en tu Garmin Connect") from None
    except Exception as exc:  # noqa: BLE001 - refused, no connection: the card says it and offers the file
        log.warning("Garmin did not take the workout (%s)", type(exc).__name__)
        raise GarminNoLlega("Garmin no la ha aceptado o no responde") from None
    return workout_id, schedule_id


__all__ = ["GarminNoLlega", "NoTraducible", "SinCredenciales", "a_garmin", "cliente_garmin", "de_garmin", "fit_local",
           "lineas", "mandar"]
