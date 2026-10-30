"""Weight of the day (unit 007, REC-4): one weight per day, the last one wins (G-26).

Its own table `peso_dia` in the TriData SQLite store, and pure functions over the saved rows:
the 7-day mean, the Mifflin-St Jeor basal and the projection towards the weight target.

`basal_del_dia` is the ONE place where the basal of a day is decided. Unit 060: Garmin's BMR of the last
complete day before it (found by `cierre.config_gasto_del_dia`, which has the store); only without one, the
reserve: Mifflin-St Jeor (man, +5) with the last weight up to that day and the age and height in force
(`Ajustes.vigentes`); without a weight, age or height, the hand-written basal of the settings. Nobody else
computes it.
"""
from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..storage import DEFAULT_DB_PATH
from .ajustes import PESO_KG, AjustesDelDia

FUERA_DE_RANGO = f"el peso tiene que estar entre {PESO_KG[0]} y {PESO_KG[1]} kg"
DIAS_DE_LA_MEDIA = 7

_SCHEMA = """
CREATE TABLE IF NOT EXISTS peso_dia (
    dia TEXT PRIMARY KEY,
    kg REAL NOT NULL,
    nota TEXT,
    creado TEXT NOT NULL
);
"""


def _como_fecha(valor: Any) -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor).strip())
    except ValueError:
        raise ValueError(f"el día del peso {valor!r} no es una fecha válida") from None


def _kg(valor: Any) -> float:
    if valor is None or valor == "" or isinstance(valor, bool):
        raise ValueError("falta el peso (kg)")
    try:
        kg = float(valor.strip().replace(",", ".")) if isinstance(valor, str) else float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"el peso {valor!r} no es un número") from None
    if not math.isfinite(kg) or not PESO_KG[0] <= kg <= PESO_KG[1]:
        raise ValueError(FUERA_DE_RANGO)
    return round(kg, 2)


def peso_valido(datos: Mapping[str, Any], hoy: date) -> tuple[date, float, str | None]:
    """The checked (day, kg, note) of a weight piece; `ValueError` says what is wrong (R11)."""
    if not isinstance(datos, Mapping):
        raise ValueError("el peso tiene que ser un objeto con kg y día")
    dia = _como_fecha(datos.get("dia") or hoy)
    kg = _kg(datos.get("kg"))
    if dia > hoy:
        raise ValueError("no se puede apuntar el peso de un día que aún no ha llegado")
    nota = datos.get("nota")
    if nota is not None and not isinstance(nota, str):
        raise ValueError("la nota del peso no es un texto")
    return dia, kg, (nota or "").strip() or None


class RegistroPeso:
    """Repository of the weights of each day over the TriData SQLite file."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def apuntar(self, dia: date | str, kg: Any, nota: str | None = None, *, hoy: date | None = None) -> dict:
        """Save the weight of `dia`; if there was one, it is replaced (the last one wins, G-26).

        With `hoy`, a later day is rejected. `ValueError` if the weight is not valid.
        """
        dia_ok, kg_ok, nota_ok = peso_valido({"dia": dia, "kg": kg, "nota": nota}, hoy or _como_fecha(dia))
        ahora = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO peso_dia (dia, kg, nota, creado) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(dia) DO UPDATE SET kg = excluded.kg, nota = excluded.nota, creado = excluded.creado",
                (dia_ok.isoformat(), kg_ok, nota_ok, ahora),
            )
        return {"dia": dia_ok.isoformat(), "kg": kg_ok, "nota": nota_ok, "creado": ahora}

    def del_dia(self, dia: date) -> dict | None:
        with self._connect() as conn:
            fila = conn.execute("SELECT dia, kg, nota, creado FROM peso_dia WHERE dia = ?", (dia.isoformat(),)).fetchone()
        return dict(fila) if fila else None

    def ultimo_hasta(self, dia: date) -> dict | None:
        """The weight of `dia` or, if there is none, the last one before it."""
        with self._connect() as conn:
            fila = conn.execute(
                "SELECT dia, kg, nota, creado FROM peso_dia WHERE dia <= ? ORDER BY dia DESC LIMIT 1",
                (dia.isoformat(),),
            ).fetchone()
        return dict(fila) if fila else None

    def entre(self, desde: date, hasta: date) -> list[dict]:
        """Weights from `desde` to `hasta`, both included, by day."""
        with self._connect() as conn:
            filas = conn.execute(
                "SELECT dia, kg, nota, creado FROM peso_dia WHERE dia BETWEEN ? AND ? ORDER BY dia",
                (desde.isoformat(), hasta.isoformat()),
            ).fetchall()
        return [dict(f) for f in filas]


# -- pure rules --------------------------------------------------------------------------

def _dia_de(peso: Mapping) -> date:
    return _como_fecha(peso["dia"])


@dataclass(frozen=True)
class MediaPeso:
    kg: float
    dias_con_peso: int

    def como_dict(self) -> dict:
        return {"kg": self.kg, "dias_con_peso": self.dias_con_peso}


def media_7_dias(pesos: Iterable[Mapping], dia: date) -> MediaPeso | None:
    """Mean of the weights of `dia` and its 6 days before (only the days that have one); None if none."""
    desde = dia - timedelta(days=DIAS_DE_LA_MEDIA - 1)
    kgs = [float(p["kg"]) for p in pesos if desde <= _dia_de(p) <= dia]
    if not kgs:
        return None
    return MediaPeso(kg=round(sum(kgs) / len(kgs), 2), dias_con_peso=len(kgs))


def basal_mifflin(kg: float, altura_cm: float, edad: int) -> int:
    """Mifflin-St Jeor for a man: 10·kg + 6.25·cm − 5·age + 5, rounded to whole kcal."""
    return round(10 * kg + 6.25 * altura_cm - 5 * edad + 5)


def _numero(n: float) -> str:
    return f"{n:g}".replace(".", ",")


@dataclass(frozen=True)
class BasalDelDia:
    kcal: float | None  # None only when the settings have no hand-written basal either
    origen: str  # "garmin" (unit 060: Garmin's BMR), "peso" (Mifflin-St Jeor) or "ajustes" (hand-written)
    kg: float | None  # the weight used, if any
    dia_del_peso: date | None
    explicacion: str
    dia_garmin: date | None = None  # unit 060: the complete day whose Garmin BMR it is

    def como_dict(self) -> dict:
        return {"kcal": self.kcal, "origen": self.origen, "kg": self.kg,
                "dia_del_peso": self.dia_del_peso.isoformat() if self.dia_del_peso else None,
                "dia_garmin": self.dia_garmin.isoformat() if self.dia_garmin else None,
                "explicacion": self.explicacion}


def basal_del_dia(vigentes: AjustesDelDia, pesos: Iterable[Mapping], dia: date, *,
                  bmr_garmin: tuple[date, float] | None = None) -> BasalDelDia:
    """The basal of `dia`: Garmin's BMR of the last complete day (`bmr_garmin`, unit 060) if there is one; else
    Mifflin-St Jeor with the last weight up to `dia`, else the hand-written one."""
    if bmr_garmin is not None:
        dia_bmr, bmr = bmr_garmin
        return BasalDelDia(kcal=round(float(bmr)), origen="garmin", kg=None, dia_del_peso=None,
                           explicacion=f"el BMR de tu Garmin del {dia_bmr.isoformat()}, el último día completo",
                           dia_garmin=dia_bmr)
    previos = [p for p in pesos if _dia_de(p) <= dia]
    ultimo = max(previos, key=_dia_de) if previos else None
    a_mano, edad, altura = vigentes.basal_kcal, vigentes.edad, vigentes.altura_cm
    faltan = [que for que, valor in (("edad", edad), ("altura", altura)) if valor is None]
    if ultimo is not None and edad is not None and altura is not None:
        kg = float(ultimo["kg"])
        dia_peso = _dia_de(ultimo)
        cuando = "de hoy" if dia_peso == dia else f"del {dia_peso.isoformat()}"
        return BasalDelDia(
            kcal=basal_mifflin(kg, float(altura), int(edad)), origen="peso", kg=kg, dia_del_peso=dia_peso,
            explicacion=(f"Mifflin-St Jeor con tu peso {cuando} ({_numero(kg)} kg), "
                         f"{_numero(float(altura))} cm y {edad} años"),
        )
    if ultimo is None:
        motivo = "aún no hay ningún peso apuntado"
    else:
        motivo = f"falta {' y '.join(faltan)} en Ajustes"
    return BasalDelDia(kcal=a_mano, origen="ajustes", kg=None, dia_del_peso=None,
                       explicacion=f"el basal escrito a mano en Ajustes: {motivo}")


def proyeccion(media: float | None, objetivo_kg: float, fecha_objetivo: date, hoy: date) -> dict:
    """Kg still to go from the 7-day mean to the target, and kg per week needed until its date."""
    dias = (fecha_objetivo - hoy).days
    faltan = None if media is None else round(media - objetivo_kg, 2)
    por_semana = None if faltan is None or dias <= 0 else round(faltan / (dias / 7), 2)
    return {"objetivo_kg": objetivo_kg, "fecha": fecha_objetivo.isoformat(), "dias": dias,
            "faltan_kg": faltan, "kg_por_semana": por_semana}


def resumen_peso(pesos: Iterable[Mapping], dia: date, vigentes: AjustesDelDia, basal: BasalDelDia) -> dict:
    """The weight block of the day screen: weight of the day, 7-day mean, basal and projection."""
    pesos = list(pesos)
    del_dia = next((p for p in pesos if _dia_de(p) == dia), None)
    media = media_7_dias(pesos, dia)
    objetivo = vigentes.objetivo_peso
    texto: str | None
    if objetivo is None:
        proy, texto = None, "sin objetivo de peso"
    else:
        proy = proyeccion(media.kg if media else None, objetivo.kg, objetivo.fecha, dia)
        texto = None if media else "sin media de 7 días: falta apuntar algún peso"
    return {
        "del_dia": {"dia": del_dia["dia"], "kg": del_dia["kg"], "nota": del_dia["nota"]} if del_dia else None,
        "media_7_dias": media.como_dict() if media else None,
        "media_texto": None if media else "sin pesos en los últimos 7 días",
        "basal": basal.como_dict(),
        "proyeccion": proy,
        "proyeccion_texto": texto,
    }
