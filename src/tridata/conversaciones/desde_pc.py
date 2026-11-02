"""The team on the PC (unit 065, REC-28 of `preguntar-equipo`, S-66 · R-186 to R-190): what the coach, the
nutritionist and the physio of `~/tridata-equipo` (Claude Code, through `tridata-mcp`) see and propose.

- `texto_contexto`: the same context the team of the app sees (`conversaciones.contexto.montar`, with the same
  repositories as `web/app.py::_contexto_conversacion`), but with only the medical transcriptions already made: it
  never calls `Medicos.al_dia()`, which would transcribe a new PDF and spend (R-190).
- `proponer_desde_pc`: a change validated as the app validates it (`CambioInterpretado`, `cambio_no_entendido`),
  its card prepared with the same `nutricion.asistente.proponer` and left in a message of that role in the
  conversation «Desde el PC» of the Asistente (created the first time, reused after). Nothing else is written: the
  plan and the data change only when Andrés confirms the card in the app, by the route of always
  (/api/asistente/confirmar), which also wakes the calendar (062).

What a tool answers never carries a value of Andrés beyond what the card says; the logs carry ids and counts only.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..calendario import Calendario
from ..ia import CambioInterpretado
from ..nutricion import Ajustes, Cierres, ObjetivosDia, Recetario, RegistroComidas, RegistroPeso
from ..nutricion.asistente import Contexto as ContextoAsistente, Peticion, proponer
from ..plan import Planes
from ..storage import DataStore
from ..web.app import DIAS_RECIENTES
from ..web.conversaciones import SIN_TEXTO, _resultado_herramienta, cambio_no_entendido
from . import Conversaciones
from .contexto import Contexto, montar
from .medicos import Medicos

log = logging.getLogger(__name__)

TITULO = "Desde el PC"
PAPELES_PC = ("entrenador", "nutricionista", "fisio")  # the team of ~/tridata-equipo (the Asistente stays in the app)
DONDE = f"Está en la conversación «{TITULO}» de la pestaña Asistente de la app."


@dataclass(frozen=True)
class Equipo:
    """What the two tools read and write: the repositories of the app over the same SQLite."""

    conversaciones: Conversaciones
    asistente: ContextoAsistente  # the context of the cards of the 021, as /api/asistente/*
    medicos: Medicos
    calendario: Calendario
    ahora: Callable[[], datetime] = datetime.now


def montar_equipo(db_path: Path | str, *, store: DataStore | None, medicos_dir: Path | str | None = None,
                  transcriptor_medico: Any = None, token: Path | str | None = None,
                  hoy: Callable[[], date] = date.today, ahora: Callable[[], datetime] = datetime.now) -> Equipo:
    """The same repositories `web/app.py::crear_app` builds over `db_path`; `token` is Google Calendar's (by default
    `google-token.json` next to the database), only to say whether it is connected."""
    registro = RegistroComidas(db_path)
    ajustes = Ajustes(db_path)
    pesos = RegistroPeso(db_path)
    objetivos = ObjetivosDia(db_path)
    planes = Planes(db_path)

    def recientes() -> list[dict]:
        if store is None:
            return []
        dia_hoy = hoy()
        return store.activities_between(dia_hoy - timedelta(days=DIAS_RECIENTES), dia_hoy - timedelta(days=1))

    asistente = ContextoAsistente(
        registro=registro, objetivos=objetivos, ajustes=ajustes, pesos=pesos, recetario=Recetario(db_path), hoy=hoy,
        store=store, cierres=Cierres(db_path, ajustes=ajustes, pesos=pesos, objetivos=objetivos, store=store,
                                     registro=registro),
        recientes=recientes, planes=planes)
    token = Path(token) if token is not None else Path(db_path).parent / "google-token.json"
    return Equipo(conversaciones=Conversaciones(db_path), asistente=asistente,
                  medicos=Medicos(db_path, medicos_dir, transcriptor_medico),
                  calendario=Calendario(db_path, token, None), ahora=ahora)


def contexto(equipo: Equipo) -> Contexto:
    """`montar` as `_contexto_conversacion` of the app, without `al_dia()`: only the transcriptions already made."""
    a = equipo.asistente
    return montar(registro=a.registro, pesos=a.pesos, ajustes=a.ajustes, store=a.store,
                  transcripciones=equipo.medicos.transcripciones(), aviso_medico=equipo.medicos.estado()["aviso"],
                  hoy=a.hoy(), planes=a.planes, calendario_conectado=equipo.calendario.conectado)


def texto_contexto(equipo: Equipo) -> str:
    """The fixed part the team of the app reads, block after block (R-186)."""
    return "\n\n".join(contexto(equipo).bloques)


def conversacion_pc(conversaciones: Conversaciones, ahora: datetime) -> dict[str, Any]:
    """The conversation «Desde el PC», the newest with that title; created the first time (its title is fixed, so
    Claude never retitles it)."""
    for c in conversaciones.lista():
        if c["titulo"] == TITULO:
            return c
    nueva = conversaciones.crear(TITULO, ahora)
    return conversaciones.renombrar(nueva["id"], TITULO)


def papel_no_valido(papel: str) -> str:
    return (f"No hay nadie en el equipo del PC que se llame «{papel}»: los papeles son {', '.join(PAPELES_PC[:-1])} "
            f"y {PAPELES_PC[-1]}. No se ha preparado ninguna tarjeta.")


def proponer_desde_pc(equipo: Equipo, papel: str, entrada: Any, texto: str = "") -> str:
    """The card of one change in «Desde el PC», in a message of `papel` with `texto` (R-187); what the tool answers.
    A role that is not of the team, or a change that does not validate, writes nothing (R-189)."""
    if papel not in PAPELES_PC:
        return papel_no_valido(papel)
    if not isinstance(entrada, dict):
        return cambio_no_entendido([])
    try:
        cambio = CambioInterpretado.model_validate(entrada)
    except ValidationError as exc:  # bug 055: which fields, so the team can mend it; never what was sent
        campos = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
        log.warning("change from the PC not understood, fields %s", ", ".join(campos) or "-")
        return cambio_no_entendido(campos)
    a = equipo.asistente
    tarjetas = proponer([cambio], a, Peticion(texto=texto, fecha=a.hoy()))
    respuesta = " ".join(_resultado_herramienta(t) for t in tarjetas)
    pendientes = [t for t in tarjetas if t["tipo"] in ("propuesta", "elegir")]
    if not pendientes:  # a «no»: why, and nothing written
        return respuesta
    ahora = equipo.ahora()
    conv = conversacion_pc(equipo.conversaciones, ahora)
    mensaje = equipo.conversaciones.anadir_mensaje(conv["id"], "asistente", texto.strip() or SIN_TEXTO, ahora,
                                                   tarjetas=pendientes, papel=papel)
    log.info("conversation %d: card from the PC in message %d", conv["id"], mensaje["id"])
    return f"{respuesta} {DONDE}"


__all__ = ["DONDE", "PAPELES_PC", "TITULO", "Equipo", "contexto", "conversacion_pc", "montar_equipo",
           "papel_no_valido", "proponer_desde_pc", "texto_contexto"]
