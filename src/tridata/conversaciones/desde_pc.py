"""The team on the PC (unit 065, REC-28 of `preguntar-equipo`, S-66 · R-186 to R-190): what the coach, the
nutritionist and the physio of `~/tridata-equipo` (Claude Code, through `tridata-mcp`) see and propose.

- `texto_contexto`: the same context the team of the app sees (`conversaciones.contexto.montar`, with the same
  repositories as `web/app.py::_contexto_conversacion`), but with only the medical transcriptions already made: it
  never calls `Medicos.al_dia()`, which would transcribe a new PDF and spend (R-190).
- Unit 066 (REC-29, S-67, S-68, G-55, R-191 to R-197): the changes are made with Andrés's «sí», without cards.
  `preparar_desde_pc` says what would be done (the card the app would show, with its warnings) and its fingerprint,
  and writes nothing; `hacer_desde_pc` does it with the same `nutricion.asistente.proponer` + `confirmar` as the
  app, only if the card is still the one shown (same fingerprint), and then makes a pass of Google Calendar as the
  app wakes it after confirming (062). `apuntar_comida_desde_pc` and `apuntar_peso_desde_pc` log a meal or a weight
  with the checks and repositories of the dictation (`web/app.py::confirmar`). Each change made leaves a message of
  its role, without cards, in the conversation «Desde el PC» of the Asistente (created the first time, reused
  after); what is only prepared, refused or fails writes nothing there (R-196, R-197).

What a tool answers never carries a value of Andrés beyond what the card says; the logs carry ids and counts only.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..calendario import Calendario
from ..ia import CambioInterpretado
from ..nutricion import Ajustes, Alimento, Cierres, ObjetivosDia, Recetario, RegistroComidas, RegistroPeso
from ..nutricion.asistente import (Cambiado, Contexto as ContextoAsistente, NoValida, Peticion, TarjetaMal,
                                   confirmar, descripcion_alimento, numero, proponer)
from ..nutricion.peso import peso_valido
from ..plan import Planes
from ..storage import DataStore
from ..web.app import DIAS_RECIENTES
from ..web.calendario import Sincronizacion
from ..web.conversaciones import cambio_no_entendido
from . import Conversaciones
from .contexto import Contexto, montar
from .medicos import Medicos

log = logging.getLogger(__name__)

TITULO = "Desde el PC"
PAPELES_PC = ("entrenador", "nutricionista", "fisio")  # the team of ~/tridata-equipo (the Asistente stays in the app)
NADA = "No se ha hecho nada."
CAMBIO_VISTO = "Ha cambiado desde que se lo enseñaste a Andrés (o esa huella no es la suya)."
PREGUNTA = ("Enséñaselo a Andrés tal cual y pregúntale; solo con su «sí», llama a hacer_cambio con este mismo cambio "
            "y esta huella.")
# what a card shows (and so what Andrés says «sí» to): its fingerprint changes when any of it changes
_LO_QUE_SE_VE = ("accion", "parametros", "texto", "antes", "despues", "dia", "desde", "donde", "avisos", "lineas")


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
            f"y {PAPELES_PC[-1]}. {NADA}")


def huella(tarjeta: dict) -> str:
    """12 hex of what the card shows: the same change over the same base gives the same one."""
    visto = json.dumps({k: tarjeta.get(k) for k in _LO_QUE_SE_VE}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(visto.encode("utf-8")).hexdigest()[:12]


def _vista(tarjeta: dict) -> str:
    """The card in words, as the app shows it: its text, before and after, where, the watch's lines and warnings."""
    lineas = [f"«{tarjeta['texto']}»"]
    for clave, etiqueta in (("antes", "Antes"), ("despues", "Después"), ("donde", "Dónde")):
        if tarjeta.get(clave):
            lineas.append(f"{etiqueta}: {tarjeta[clave]}")
    if tarjeta.get("lineas"):
        lineas.append("Al reloj: " + " · ".join(tarjeta["lineas"]))
    lineas += [f"Aviso: {a}" for a in tarjeta.get("avisos") or []]
    return "\n".join(lineas)


def _sin_propuesta(tarjeta: dict) -> str:
    """A «no» or an «elegir» of the catalogue: why, or the question; nothing is done (R-197)."""
    if tarjeta["tipo"] == "elegir":
        opciones = "; ".join(c.get("texto", "") for c in tarjeta.get("candidatos") or [])
        return (f"Hay más de uno: {tarjeta['texto']} ({opciones}). Pregunta a Andrés cuál es y prepáralo otra vez "
                f"diciéndolo. {NADA}")
    return f"No se puede: {tarjeta['texto']}. {NADA}"


def _cambio(entrada: Any) -> CambioInterpretado | str:
    """The change validated as the app validates it, or what to mend (bug 055: which fields, never what was sent)."""
    if not isinstance(entrada, dict):
        return cambio_no_entendido([])
    try:
        return CambioInterpretado.model_validate(entrada)
    except ValidationError as exc:
        campos = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
        log.warning("change from the PC not understood, fields %s", ", ".join(campos) or "-")
        return cambio_no_entendido(campos)


def _tarjeta(equipo: Equipo, cambio: CambioInterpretado, texto: str) -> dict:
    a = equipo.asistente
    (tarjeta,) = proponer([cambio], a, Peticion(texto=texto, fecha=a.hoy()))
    return tarjeta


def preparar_desde_pc(equipo: Equipo, entrada: Any, texto: str = "") -> str:
    """What `hacer_desde_pc` would do with this change, in words, and its fingerprint. Writes nothing (R-191)."""
    cambio = _cambio(entrada)
    if isinstance(cambio, str):
        return cambio
    tarjeta = _tarjeta(equipo, cambio, texto)
    if tarjeta["tipo"] != "propuesta":
        return _sin_propuesta(tarjeta)
    return f"Esto es lo que se haría (aún no está hecho):\n{_vista(tarjeta)}\n{PREGUNTA}\nHuella: {huella(tarjeta)}"


def _ha_cambiado(tarjeta: dict) -> str:
    """The base changed since Andrés saw it (or the fingerprint is not the one shown): nothing done (R-197)."""
    if tarjeta["tipo"] != "propuesta":
        return f"{CAMBIO_VISTO} {_sin_propuesta(tarjeta)}"
    return (f"{CAMBIO_VISTO} {NADA} Lo que se haría ahora:\n{_vista(tarjeta)}\n{PREGUNTA}\n"
            f"Huella: {huella(tarjeta)}")


def _calendario(equipo: Equipo) -> str:
    """One pass of Google Calendar, as the app wakes it after confirming a card (062); what to add if it failed."""
    a = equipo.asistente
    if a.planes is None:
        return ""
    estado = Sincronizacion(equipo.calendario, a.planes, a.hoy, equipo.ahora, a.store).vuelta()  # never raises
    if estado["conectado"] and estado["error"]:  # a good pass clears it: this one failed
        return f" Pero Google Calendar no se ha actualizado ({estado['error']})"
    return ""


def _apuntar(equipo: Equipo, papel: str, texto: str, hecho: str) -> None:
    """R-196: what was done, in a message of `papel` at this hour in «Desde el PC», without cards."""
    ahora = equipo.ahora()
    conv = conversacion_pc(equipo.conversaciones, ahora)
    cuerpo = f"{texto.strip()}\n\n{hecho}" if texto.strip() else hecho
    mensaje = equipo.conversaciones.anadir_mensaje(conv["id"], "asistente", cuerpo, ahora, tarjetas=[], papel=papel)
    log.info("conversation %d: change from the PC done, message %d", conv["id"], mensaje["id"])


def hacer_desde_pc(equipo: Equipo, papel: str, entrada: Any, huella_vista: str, texto: str = "") -> str:
    """The change applied as if Andrés confirmed its card in the app (R-192, R-193), only if the card is still the one
    with `huella_vista`; then Google Calendar and the message of R-196. Otherwise nothing is written (R-197)."""
    if papel not in PAPELES_PC:
        return papel_no_valido(papel)
    cambio = _cambio(entrada)
    if isinstance(cambio, str):
        return cambio
    tarjeta = _tarjeta(equipo, cambio, texto)
    if tarjeta["tipo"] != "propuesta":
        return _sin_propuesta(tarjeta)
    if huella(tarjeta) != str(huella_vista).strip():
        return _ha_cambiado(tarjeta)
    try:
        resultado = confirmar(tarjeta, equipo.asistente)
    except Cambiado as exc:
        return _ha_cambiado(exc.tarjeta)
    except NoValida as exc:  # e.g. Garmin that does not take it (R-159): its text says why and what to do
        return f"{exc} {NADA}"
    except TarjetaMal as exc:
        return f"No se ha podido: {exc}. {NADA}"
    hecho = f"Hecho: {resultado['hecho']}. Era: «{tarjeta['texto']}»."
    aviso = _calendario(equipo)
    _apuntar(equipo, papel, texto, hecho)
    return hecho + aviso


def apuntar_comida_desde_pc(equipo: Equipo, papel: str, dia: Any, alimentos: Any, texto: str = "") -> str:
    """The foods logged on `dia` as the dictation logs them, without section (R-194): all of them or none."""
    if papel not in PAPELES_PC:
        return papel_no_valido(papel)
    try:
        el_dia = date.fromisoformat(str(dia))
    except ValueError:
        return "No se ha apuntado nada: el día tiene que ser una fecha AAAA-MM-DD."
    if not isinstance(alimentos, list) or not alimentos:
        return "No se ha apuntado nada: no hay nada que apuntar (alimentos, con su cantidad y sus kcal)."
    try:
        lista = [Alimento.desde_dict({**a, "dia": el_dia.isoformat()} if isinstance(a, dict) else a)
                 for a in alimentos]
        equipo.asistente.registro.validar(lista)
    except ValueError as exc:
        return f"No se ha apuntado nada: {exc}."
    a = equipo.asistente
    guardados = a.registro.guardar(lista, hoy=a.hoy(), texto=texto.strip() or f"{TITULO} ({papel})",
                                   sin_cambios=False)
    que = "; ".join(descripcion_alimento({**g, "creado": None}) for g in guardados)
    hecho = f"Hecho: apuntado el {el_dia.isoformat()}: {que}."
    _apuntar(equipo, papel, texto, hecho)
    return hecho


def apuntar_peso_desde_pc(equipo: Equipo, papel: str, kg: Any, dia: Any, nota: str | None = None) -> str:
    """The weight of `dia` as the weight piece of the dictation (R-192, C-243); out of range, nothing."""
    if papel not in PAPELES_PC:
        return papel_no_valido(papel)
    a = equipo.asistente
    hoy = a.hoy()
    try:
        el_dia, el_kg, la_nota = peso_valido({"dia": dia, "kg": kg, "nota": nota}, hoy)
    except ValueError as exc:
        return f"No se ha apuntado el peso: {exc}."
    a.pesos.apuntar(el_dia, el_kg, la_nota, hoy=hoy)
    hecho = f"Hecho: peso del {el_dia.isoformat()} apuntado: {numero(el_kg)} kg."
    _apuntar(equipo, papel, "", hecho)
    return hecho


__all__ = ["NADA", "PAPELES_PC", "TITULO", "Equipo", "apuntar_comida_desde_pc", "apuntar_peso_desde_pc", "contexto",
           "conversacion_pc", "hacer_desde_pc", "huella", "montar_equipo", "papel_no_valido", "preparar_desde_pc",
           "texto_contexto"]
