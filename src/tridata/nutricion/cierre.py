"""Close the day in two steps (unit 014, REC-3 of `cerrar-dia`: R-24..R-28, G-25).

1. **The food** (`Cierres.cerrar_comida`): with the «Cerrar el día» button, or by itself when the next day
   starts (`al_dia`). What was planned for that day and not changed becomes eaten, in the same transaction.
   A day with nothing logged is never closed.
2. **The expenditure, with Garmin** (`al_dia`): a past day with the food closed is closed with Garmin's
   `total_calories` unless an objective signal says that total is not the whole day yet (R-26): it is
   missing, the exercise alone is more (G-10, the same sum as `MarkdownExporter._daily_activity_aggregates`)
   or it is below the estimated basal of that day. Then the day keeps the estimate and says why, and the
   next pass (after the next sync) looks again. No hours, no waiting.

When Garmin changes the total of a day already closed, the new one is kept, a trail row keeps the before
and after of the expenditure and the balance, and only a relevant change (G-25) warns on today's screen
until «Entendido». A total that is incomplete again is ignored.

After every close or correction with Garmin, the kcal per step are recalibrated with the last 14 days
closed with Garmin that are valid (R-28) and saved in `Ajustes` from today: the days before keep theirs.

Tables of their own in the same SQLite: `cierre_dia` (no row = open), `cierre_rastro`, `calibracion`.
The eaten total and the balance are never stored: they are always computed from the rows (G-18), so a
forgotten food added to a closed day recomputes by itself.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..exporters import MarkdownExporter
from ..storage import DEFAULT_DB_PATH
from .ajustes import Ajustes, AjustesDelDia
from .gasto import estimar_gasto
from .margen import PASOS_POR_DEFECTO, ConfigGasto, config_del_dia, kcal_enteras, total_del_dia
from .objetivo_dia import ObjetivosDia
from .peso import BasalDelDia, RegistroPeso, basal_del_dia
from .registro import RegistroComidas

MOTIVOS_CIERRE = ("boton", "dia_siguiente")
RELEVANTE_KCAL = 200  # G-25: a change of MORE than this is relevant
DIAS_CALIBRACION = 14
MIN_DIAS_CALIBRACION = 3
MIN_PASOS_CALIBRACION = 1000
RANGO_KCAL_POR_PASO = (0.01, 0.2)  # the same range Ajustes accepts
TEXTO_ESPERA = "Garmin aún no tiene el día completo: sigue con el gasto estimado"
FUERA_DE_RANGO = "fuera de rango (0,01…0,2 kcal/paso)"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cierre_dia (
    dia TEXT PRIMARY KEY,
    estado TEXT NOT NULL CHECK (estado IN ('comida_cerrada', 'cerrado_garmin')),
    comida_cerrada_en TEXT NOT NULL,
    motivo_cierre TEXT NOT NULL CHECK (motivo_cierre IN ('boton', 'dia_siguiente')),
    gasto_estimado REAL,
    desglose TEXT,
    gasto_real REAL,
    cerrado_garmin_en TEXT,
    espera_motivo TEXT
);
CREATE TABLE IF NOT EXISTS cierre_rastro (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dia TEXT NOT NULL,
    fecha TEXT NOT NULL,
    gasto_antes REAL NOT NULL,
    gasto_despues REAL NOT NULL,
    balance_antes REAL NOT NULL,
    balance_despues REAL NOT NULL,
    relevante INTEGER NOT NULL,
    visto INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cierre_rastro_dia ON cierre_rastro(dia);
CREATE TABLE IF NOT EXISTS calibracion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    kcal_por_paso REAL NOT NULL,
    dias INTEGER NOT NULL,
    diferencia_media REAL,
    descartada_motivo TEXT
);
"""


# -- texts ------------------------------------------------------------------------------------------

def _kcal(n: float) -> str:
    """2550 -> «2.550», 2412.5 -> «2.412,5» (no sign)."""
    n = round(abs(float(n)), 1)
    texto = f"{n:,.0f}" if n.is_integer() else f"{n:,.1f}"
    return texto.replace(",", "·").replace(".", ",").replace("·", ".")


def _con_signo(n: float) -> str:
    return ("+" if n > 0 else "−" if n < 0 else "") + _kcal(n)


def _entero_con_signo(n: float) -> str:
    """Unit 017 (R13): −1789.8 -> «−1.790»; never «−0»."""
    entero = round(float(n))
    return ("+" if entero > 0 else "−" if entero < 0 else "") + kcal_enteras(n)


def _pct(p: float | None) -> str:
    if p is None:
        return "—"
    numero = f"{round(abs(p), 1):g}".replace(".", ",")
    return ("+" if p > 0 else "−" if p < 0 else "") + numero


def _por_paso(n: float) -> str:
    return f"{n:g}".replace(".", ",")


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


# -- the expenditure of a day ----------------------------------------------------------------------

def config_gasto_del_dia(dia: date, ajustes: Ajustes, pesos: RegistroPeso) -> tuple[AjustesDelDia, BasalDelDia,
                                                                                      ConfigGasto]:
    """Settings in force, basal (Mifflin-St Jeor with the last weight, or by hand) and expenditure config.

    Without a hand-written basal a day with a weight still uses its Mifflin-St Jeor one; without either,
    or without kcal per step, `ValueError` says what is missing (the web answers 409).
    """
    vigentes = ajustes.vigentes(dia)
    ultimo = pesos.ultimo_hasta(dia)
    basal = basal_del_dia(vigentes, [ultimo] if ultimo else [], dia)
    try:
        config = vigentes.config_gasto()
    except ValueError:
        if basal.kcal is None or vigentes.kcal_por_paso is None:
            raise
        pasos = PASOS_POR_DEFECTO if vigentes.pasos_por_defecto is None else vigentes.pasos_por_defecto
        config = ConfigGasto(basal_kcal=basal.kcal, kcal_por_paso=vigentes.kcal_por_paso, pasos=pasos)
    return vigentes, basal, config


def gasto_estimado_del_dia(dia: date, ajustes: Ajustes, pesos: RegistroPeso, objetivos: ObjetivosDia,
                           store: Any, *, cuando: str = "de ese día") -> dict:
    """The estimated expenditure of `dia` as `estimar_gasto` gives it (`kcal`, `pasos`, `desglose`,
    `avisos`, `garmin`), with the settings, weight, objective and Garmin rows of THAT day.

    `store` is the `DataStore` (or None: no Garmin data). `ValueError` without basal or kcal per step.
    """
    _, basal, config = config_gasto_del_dia(dia, ajustes, pesos)
    objetivo_dia = objetivos.del_dia(dia)
    stats = store.daily_stats_on(dia) if store is not None else None
    actividades = store.activities_between(dia, dia) if store is not None else []
    return estimar_gasto(config_del_dia(config, basal, objetivo_dia), stats, actividades,
                         objetivo_dia.get("previsto") if objetivo_dia else None, cuando=cuando)


# -- pure rules -------------------------------------------------------------------------------------

def _agregados(actividades: Iterable[Mapping[str, Any]]) -> tuple[float, float]:
    """Exercise kcal and estimated running steps of the given activities (the exporter's own sum)."""
    por_dia = MarkdownExporter._daily_activity_aggregates([dict(a) for a in actividades])
    return (sum(v["exercise_calories"] for v in por_dia.values()),
            sum(v["running_steps_est"] for v in por_dia.values()))


def garmin_incompleto(stats: Mapping[str, Any] | None, actividades: Iterable[Mapping[str, Any]],
                      basal: float | None) -> str | None:
    """Why Garmin's total of a day is not the whole day yet (R-26), or None if it can close the day."""
    total = stats.get("total_calories") if stats else None
    if total is None:
        return "Garmin aún no trae el total de kcal de ese día"
    ejercicio, _ = _agregados(actividades)
    if ejercicio > total:
        return (f"el ejercicio solo ({_kcal(ejercicio)} kcal) supera el total de Garmin ({_kcal(total)} kcal): "
                "aún no lo ha sumado")
    if basal is not None and total < basal:
        return f"el total de Garmin ({_kcal(total)} kcal) queda por debajo del basal estimado ({_kcal(basal)} kcal)"
    return None


def balance(comido: float, gasto: float) -> dict:
    """Balance = eaten − expenditure (negative = deficit) and its % of the expenditure."""
    kcal = round(comido - gasto, 1)
    return {"kcal": kcal, "pct": round(kcal / gasto * 100, 1) if gasto > 0 else None}


def es_relevante(antes: float, despues: float, comido: float, limite_pct: float | None) -> bool:
    """G-25: more than 200 kcal, the balance changes sign, or its % crosses the healthy limit of that day."""
    if abs(despues - antes) > RELEVANTE_KCAL:
        return True
    b_antes, b_despues = comido - antes, comido - despues
    if (b_antes < 0) != (b_despues < 0):
        return True
    if limite_pct is not None and antes > 0 and despues > 0:
        return (abs(b_antes / antes * 100) > limite_pct) != (abs(b_despues / despues * 100) > limite_pct)
    return False


def media_semana(dias: Iterable[Mapping[str, float]]) -> dict:
    """Mean balance per day and Σ balance / Σ expenditure in %, over days with `comido` and `gasto`."""
    lista = list(dias)
    if not lista:
        return {"dias": 0, "balance_medio_kcal": None, "balance_pct": None}
    suma = sum(d["comido"] - d["gasto"] for d in lista)
    gasto = sum(d["gasto"] for d in lista)
    return {"dias": len(lista), "balance_medio_kcal": round(suma / len(lista), 1),
            "balance_pct": round(suma / gasto * 100, 1) if gasto > 0 else None}


def _vale_para_calibrar(d: Mapping[str, Any]) -> bool:
    pasos = d.get("pasos_sin_carrera")
    return (d.get("real") is not None and d.get("basal") is not None and pasos is not None
            and pasos >= MIN_PASOS_CALIBRACION)


def dias_para_calibrar(dias: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The last 14 valid days (steps not incomplete and at least 1,000 without running), oldest first."""
    validos = sorted((d for d in dias if _vale_para_calibrar(d)), key=lambda d: d["dia"])
    return validos[-DIAS_CALIBRACION:]


def calibrar(dias: Iterable[Mapping[str, Any]]) -> float | None:
    """kcal/step = Σ(real − basal − kcal of activities) / Σ steps without running (R-28), to 0.001.

    Each day: `dia`, `real`, `basal`, `actividades_kcal`, `pasos_sin_carrera` (None if Garmin's steps
    were incomplete). None with fewer than 3 valid days. The range is NOT checked here.
    """
    usados = dias_para_calibrar(dias)
    if len(usados) < MIN_DIAS_CALIBRACION:
        return None
    resto = sum(d["real"] - d["basal"] - (d.get("actividades_kcal") or 0) for d in usados)
    pasos = sum(d["pasos_sin_carrera"] for d in usados)
    return round(resto / pasos, 3)


def _texto_calibracion(fila: Mapping[str, Any]) -> str:
    por_paso = _por_paso(fila["kcal_por_paso"])
    if fila["descartada_motivo"]:
        return (f"calibración con Garmin del {fila['fecha']} descartada: daba {por_paso} kcal/paso, "
                f"{fila['descartada_motivo']}; siguen las kcal por paso de Ajustes")
    dif = fila["diferencia_media"]
    de_media = "" if dif is None else f", diferencia media {_con_signo(dif)} kcal"
    return f"calibrado con Garmin el {fila['fecha']}: {por_paso} kcal/paso ({fila['dias']} días{de_media})"


# -- the repository ---------------------------------------------------------------------------------

class Cierres:
    """The state of each day (open, food closed, closed with Garmin), its trail and the calibration.

    Lives in the same SQLite as the food log. `ajustes`, `pesos` and `objetivos` default to that same
    file; `store` is the Garmin data (`DataStore`), without it no day can close with Garmin.
    """

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH, *, ajustes: Ajustes | None = None,
                 pesos: RegistroPeso | None = None, objetivos: ObjetivosDia | None = None, store: Any = None,
                 registro: RegistroComidas | None = None) -> None:
        self._db_path = Path(db_path)
        self._registro = registro or RegistroComidas(self._db_path)
        self._ajustes = ajustes or Ajustes(self._db_path)
        self._pesos = pesos or RegistroPeso(self._db_path)
        self._objetivos = objetivos or ObjetivosDia(self._db_path)
        self._store = store
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

    # -- reading ----------------------------------------------------------------------------------

    def del_dia(self, dia: date) -> dict | None:
        """The row of `dia` (None = open)."""
        with self._connect() as conn:
            fila = conn.execute("SELECT * FROM cierre_dia WHERE dia = ?", (dia.isoformat(),)).fetchone()
        if fila is None:
            return None
        datos = dict(fila)
        datos["desglose"] = json.loads(datos["desglose"]) if datos["desglose"] else None
        return datos

    def rastro(self, dia: date) -> list[dict]:
        with self._connect() as conn:
            filas = conn.execute("SELECT * FROM cierre_rastro WHERE dia = ? ORDER BY id", (dia.isoformat(),)).fetchall()
        return [{"id": f["id"], "fecha": f["fecha"], "gasto_antes": f["gasto_antes"],
                 "gasto_despues": f["gasto_despues"], "balance_antes": f["balance_antes"],
                 "balance_despues": f["balance_despues"], "relevante": bool(f["relevante"]),
                 "visto": bool(f["visto"])} for f in filas]

    def avisos_pendientes(self) -> list[dict]:
        """The relevant corrections not seen yet, for today's screen (R-27)."""
        with self._connect() as conn:
            filas = conn.execute("SELECT * FROM cierre_rastro WHERE relevante = 1 AND visto = 0 ORDER BY id").fetchall()
        return [{"id": f["id"], "dia": f["dia"],
                 "texto": (f"Garmin ha corregido el gasto del {f['dia']}: {_kcal(f['gasto_antes'])} → "
                           f"{_kcal(f['gasto_despues'])} kcal; balance {_con_signo(f['balance_antes'])} → "
                           f"{_con_signo(f['balance_despues'])}"),
                 "gasto_antes": f["gasto_antes"], "gasto_despues": f["gasto_despues"],
                 "balance_antes": f["balance_antes"], "balance_despues": f["balance_despues"]} for f in filas]

    def ultima_calibracion(self, hasta: date) -> dict | None:
        """The last calibration done up to `hasta`, with its text for the breakdown."""
        with self._connect() as conn:
            fila = conn.execute("SELECT * FROM calibracion WHERE fecha <= ? ORDER BY fecha DESC, id DESC LIMIT 1",
                                (hasta.isoformat(),)).fetchone()
        if fila is None:
            return None
        datos = dict(fila)
        return {**datos, "texto": _texto_calibracion(datos)}

    def _comido(self, dia: date) -> float:
        return total_del_dia(self._registro.apuntes_del_dia(dia), dia)

    def semana(self, dia: date, comido_kcal: float | None = None, gasto_kcal: float | None = None) -> dict:
        """Monday to Sunday around `dia`, over the days with the food closed (R-24).

        Each day with its real expenditure if closed with Garmin, else the estimated one. `comido_kcal`
        and `gasto_kcal` are those of `dia` itself, already computed by the caller.
        """
        lunes = dia - timedelta(days=dia.weekday())
        domingo = lunes + timedelta(days=6)
        with self._connect() as conn:
            filas = conn.execute("SELECT dia, estado, gasto_real FROM cierre_dia WHERE dia BETWEEN ? AND ? ORDER BY dia",
                                 (lunes.isoformat(), domingo.isoformat())).fetchall()
        dias = []
        for f in filas:
            d = date.fromisoformat(f["dia"])
            if d == dia and comido_kcal is not None and gasto_kcal is not None:
                dias.append({"comido": comido_kcal, "gasto": gasto_kcal})
                continue
            if f["estado"] == "cerrado_garmin" and f["gasto_real"] is not None:
                gasto = f["gasto_real"]
            else:
                try:
                    gasto = gasto_estimado_del_dia(d, self._ajustes, self._pesos, self._objetivos, self._store)["kcal"]
                except ValueError:
                    continue  # a day without settings cannot be averaged
            dias.append({"comido": self._comido(d), "gasto": gasto})
        media = media_semana(dias)
        n = media["dias"]
        if n:
            texto = (f"media de la semana {_entero_con_signo(media['balance_medio_kcal'])} kcal/día "
                     f"({_pct(media['balance_pct'])} %), {n} {'día' if n == 1 else 'días'}")
        else:
            texto = "media de la semana: aún no hay ningún día con la comida cerrada"
        return {"desde": lunes.isoformat(), "hasta": domingo.isoformat(), **media, "texto": texto}

    def bloque_del_dia(self, dia: date, comido_kcal: float, gasto_estimado_kcal: float, *,
                       semanas: dict[date, dict] | None = None) -> dict:
        """The `cierre` block of the day screen (and of unit 015): state, its text, balance, trail and week.

        `semanas` (unit 017) is a dictionary that lives only during ONE request that composes many days: the
        week of each Monday is computed once and the other days of that week take it from there (it is the
        same for every day of the week). Nothing is kept after the request.
        """
        fila = self.del_dia(dia)
        estado = fila["estado"] if fila else "abierto"
        real = fila["gasto_real"] if fila and estado == "cerrado_garmin" else None
        espera = fila["espera_motivo"] if fila and estado == "comida_cerrada" else None
        gasto = real if real is not None else gasto_estimado_kcal
        b = balance(comido_kcal, gasto)
        cifras = f"{_entero_con_signo(b['kcal'])} kcal ({_pct(b['pct'])} %)"
        if estado == "cerrado_garmin":
            texto = (f"Cerrado con Garmin: gasto real {kcal_enteras(gasto)} "
                     f"(estimado {kcal_enteras(gasto_estimado_kcal)})")
            balance_texto = f"balance {cifras}, con el gasto real de Garmin"
        elif estado == "comida_cerrada":
            texto = TEXTO_ESPERA if espera else "Comida cerrada · gasto estimado"
            balance_texto = f"balance provisional {cifras}, con el gasto estimado"
        else:
            texto = "Abierto"
            balance_texto = f"balance hasta ahora {cifras}, con el gasto estimado"
        return {
            "estado": estado, "texto": texto, "espera_motivo": espera,
            "comido_kcal": round(comido_kcal, 1), "gasto_estimado_kcal": gasto_estimado_kcal,
            "gasto_real_kcal": real, "balance_kcal": b["kcal"], "balance_pct": b["pct"],
            "balance_texto": balance_texto,
            "comida_cerrada_en": fila["comida_cerrada_en"] if fila else None,
            "motivo_cierre": fila["motivo_cierre"] if fila else None,
            "cerrado_garmin_en": fila["cerrado_garmin_en"] if fila else None,
            "rastro": [{k: v for k, v in r.items() if k not in ("id", "visto")} for r in self.rastro(dia)],
            "semana": self._semana_del_dia(dia, comido_kcal, gasto, fila is not None, semanas),
        }

    def _semana_del_dia(self, dia: date, comido_kcal: float, gasto: float, cerrado: bool,
                        semanas: dict[date, dict] | None) -> dict:
        lunes = dia - timedelta(days=dia.weekday())
        if semanas is not None and lunes in semanas:
            return dict(semanas[lunes])
        semana = self.semana(dia, comido_kcal, gasto) if cerrado else self.semana(dia)
        if semanas is not None:
            semanas[lunes] = semana
        return dict(semana)

    # -- writing ----------------------------------------------------------------------------------

    def cerrar_comida(self, dia: date, motivo: str = "boton") -> bool:
        """Close the food of `dia`: what is planned and not changed becomes eaten, in one transaction.

        True if it closed now, False if it was already closed (nothing changes). `ValueError` if the
        day has nothing logged (it is never closed) or the reason is unknown. Whether `dia` is not in
        the future is the caller's job.
        """
        if motivo not in MOTIVOS_CIERRE:
            raise ValueError(f"motivo de cierre {motivo!r} no válido")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM cierre_dia WHERE dia = ?", (dia.isoformat(),)).fetchone():
                return False
            if not conn.execute("SELECT 1 FROM alimento_apuntado WHERE dia = ? LIMIT 1", (dia.isoformat(),)).fetchone():
                raise ValueError(f"el {dia.isoformat()} no tiene nada apuntado: no hay comida que cerrar")
            self._registro.cerrar_planeados(dia, conn)
            conn.execute("INSERT INTO cierre_dia (dia, estado, comida_cerrada_en, motivo_cierre) "
                         "VALUES (?, 'comida_cerrada', ?, ?)", (dia.isoformat(), _ahora(), motivo))
        return True

    def marcar_visto(self, aviso_id: int) -> None:
        """«Entendido»: the warning is marked seen, never deleted. KeyError if there is no such warning."""
        with self._connect() as conn:
            if conn.execute("UPDATE cierre_rastro SET visto = 1 WHERE id = ? AND relevante = 1",
                            (aviso_id,)).rowcount == 0:
                raise KeyError(aviso_id)

    def _garmin_del_dia(self, dia: date) -> tuple[dict | None, list[dict]]:
        if self._store is None:
            return None, []
        return self._store.daily_stats_on(dia), self._store.activities_between(dia, dia)

    def al_dia(self, hoy: date) -> dict:
        """Bring every day up to date; idempotent. Called by `tridata sync` and by today's /api/dia.

        1. close the food of the past days with something logged; 2. close with Garmin the past days
        whose total is the whole day; 3. look at the corrections of the days closed with Garmin;
        4. if anything closed or was corrected with Garmin, recalibrate the kcal per step.
        Today is never closed with Garmin: its total is never the whole day yet.
        """
        resultado: dict[str, Any] = {"comidas_cerradas": 0, "cerrados_garmin": 0, "esperan": 0,
                                     "correcciones": 0, "calibracion": None}
        with self._connect() as conn:
            pendientes = [f[0] for f in conn.execute(
                "SELECT DISTINCT dia FROM alimento_apuntado WHERE dia < ? AND dia NOT IN (SELECT dia FROM cierre_dia) "
                "ORDER BY dia", (hoy.isoformat(),))]
        for texto in pendientes:
            if self.cerrar_comida(date.fromisoformat(texto), "dia_siguiente"):
                resultado["comidas_cerradas"] += 1

        with self._connect() as conn:
            abiertos = [(f["dia"], f["espera_motivo"]) for f in conn.execute(
                "SELECT dia, espera_motivo FROM cierre_dia WHERE estado = 'comida_cerrada' AND dia < ? ORDER BY dia",
                (hoy.isoformat(),))]
        for texto, espera in abiertos:
            if self._cerrar_con_garmin(date.fromisoformat(texto), espera):
                resultado["cerrados_garmin"] += 1
            else:
                resultado["esperan"] += 1

        resultado["correcciones"] = self._correcciones()
        if resultado["cerrados_garmin"] or resultado["correcciones"]:
            resultado["calibracion"] = self._calibrar(hoy)
        return resultado

    def _cerrar_con_garmin(self, dia: date, espera_antes: str | None) -> bool:
        stats, actividades = self._garmin_del_dia(dia)
        motivo = garmin_incompleto(stats, actividades, None)  # without a total there is no need for the basal
        estimado: dict | None = None
        if motivo is None:
            try:
                _, basal, _ = config_gasto_del_dia(dia, self._ajustes, self._pesos)
                motivo = garmin_incompleto(stats, actividades, basal.kcal)
                if motivo is None:
                    estimado = gasto_estimado_del_dia(dia, self._ajustes, self._pesos, self._objetivos, self._store)
            except ValueError:
                return False  # without settings for that day there is no estimate to keep beside the real one
        if stats is None or estimado is None:
            if motivo != espera_antes:
                with self._connect() as conn:
                    conn.execute("UPDATE cierre_dia SET espera_motivo = ? WHERE dia = ?", (motivo, dia.isoformat()))
            return False
        with self._connect() as conn:
            conn.execute(
                "UPDATE cierre_dia SET estado = 'cerrado_garmin', gasto_estimado = ?, desglose = ?, gasto_real = ?, "
                "cerrado_garmin_en = ?, espera_motivo = NULL WHERE dia = ? AND estado = 'comida_cerrada'",
                (estimado["kcal"], json.dumps(estimado["desglose"], ensure_ascii=False),
                 float(stats["total_calories"]), _ahora(), dia.isoformat()))
        return True

    def _correcciones(self) -> int:
        if self._store is None:
            return 0
        with self._connect() as conn:
            cerrados = [(f["dia"], f["gasto_real"]) for f in conn.execute(
                "SELECT dia, gasto_real FROM cierre_dia WHERE estado = 'cerrado_garmin' ORDER BY dia")]
        hechas = 0
        for texto, real in cerrados:
            dia = date.fromisoformat(texto)
            stats = self._store.daily_stats_on(dia)
            total = stats.get("total_calories") if stats else None
            if total is None or float(total) == real:
                continue
            try:
                vigentes, basal, _ = config_gasto_del_dia(dia, self._ajustes, self._pesos)
            except ValueError:
                continue
            if garmin_incompleto(stats, self._store.activities_between(dia, dia), basal.kcal) is not None:
                continue  # incomplete again: the real one it had stays, without a trail (G-25)
            nuevo = float(total)
            comido = self._comido(dia)
            relevante = es_relevante(real, nuevo, comido, vigentes.limite_saludable_pct)
            with self._connect() as conn:
                # unit 028 (R7): the sync and the open app may both get here with the same old `real`; only the
                # one whose UPDATE still finds it writes the trail, in the same transaction: one warning, not two
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute("UPDATE cierre_dia SET gasto_real = ? WHERE dia = ? AND gasto_real = ?",
                                (nuevo, texto, real)).rowcount != 1:
                    continue
                conn.execute(
                    "INSERT INTO cierre_rastro (dia, fecha, gasto_antes, gasto_despues, balance_antes, balance_despues, "
                    "relevante) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (texto, _ahora(), real, nuevo, balance(comido, real)["kcal"], balance(comido, nuevo)["kcal"],
                     int(relevante)))
            hechas += 1
        return hechas

    def _dias_cerrados_para_calibrar(self) -> list[dict]:
        """The most recent days closed with Garmin, newest first, until 14 valid ones are found."""
        with self._connect() as conn:
            filas = conn.execute("SELECT dia, gasto_real, gasto_estimado FROM cierre_dia "
                                 "WHERE estado = 'cerrado_garmin' ORDER BY dia DESC").fetchall()
        dias: list[dict] = []
        for f in filas:
            dia = date.fromisoformat(f["dia"])
            stats, actividades = self._garmin_del_dia(dia)
            try:
                _, basal, _ = config_gasto_del_dia(dia, self._ajustes, self._pesos)
            except ValueError:
                continue
            ejercicio, carrera = _agregados(actividades)
            pasos = stats.get("steps") if stats else None
            carrera = round(carrera)
            sin_carrera = None if pasos is None or carrera > pasos else int(pasos) - carrera
            datos = {"dia": dia, "real": f["gasto_real"], "basal": basal.kcal, "actividades_kcal": ejercicio,
                     "pasos_sin_carrera": sin_carrera, "estimado": f["gasto_estimado"]}
            dias.append(datos)
            if len(dias_para_calibrar(dias)) >= DIAS_CALIBRACION:
                break
        return dias

    def _calibrar(self, hoy: date) -> dict | None:
        """Recalibrate the kcal per step from today (R-28). The row of `calibracion`, or None if nothing changed."""
        dias = self._dias_cerrados_para_calibrar()
        valor = calibrar(dias)
        if valor is None:
            return None
        usados = dias_para_calibrar(dias)
        diferencias = [d["real"] - d["estimado"] for d in usados if d["estimado"] is not None]
        diferencia = round(sum(diferencias) / len(diferencias), 1) if diferencias else None
        descartada = None if RANGO_KCAL_POR_PASO[0] <= valor <= RANGO_KCAL_POR_PASO[1] else FUERA_DE_RANGO
        anterior = self.ultima_calibracion(hoy)
        if descartada is None and valor == self._ajustes.vigentes(hoy).kcal_por_paso and not (
                anterior and anterior["descartada_motivo"]):
            return None  # only saved if it changes
        if descartada is None and valor != self._ajustes.vigentes(hoy).kcal_por_paso:
            self._ajustes.cambiar({"kcal_por_paso": valor}, desde=hoy)
        with self._connect() as conn:
            conn.execute("INSERT INTO calibracion (fecha, kcal_por_paso, dias, diferencia_media, descartada_motivo) "
                         "VALUES (?, ?, ?, ?, ?)", (hoy.isoformat(), valor, len(usados), diferencia, descartada))
        return self.ultima_calibracion(hoy)

    def calibracion_vigente(self, dia: date) -> dict | None:
        """What the breakdown of `dia` says about the calibration: the last one, if it is the kcal per step
        in force that day, or if it was discarded. None otherwise (e.g. changed by hand after it)."""
        ultima = self.ultima_calibracion(dia)
        if ultima is None:
            return None
        if ultima["descartada_motivo"] or ultima["kcal_por_paso"] == self._ajustes.vigentes(dia).kcal_por_paso:
            return ultima
        return None
