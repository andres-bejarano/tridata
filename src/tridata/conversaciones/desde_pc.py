"""The team on the PC (unit 065, REC-28 of `preguntar-equipo`, S-66 · R-186 to R-190): what the coach, the
nutritionist and the physio of `~/tridata-equipo` (Claude Code, through `tridata-mcp`) see and propose.

- `texto_contexto`: the same context the team of the app sees (`conversaciones.contexto.montar`, with the same
  repositories as `web/app.py`), with the medical transcriptions already made (R-190).
- Unit 066 (REC-29, S-67, S-68, G-55, R-191 to R-197): the changes are made with Andrés's «sí», without cards.
  `preparar_desde_pc` says what would be done (the card the app would show, with its warnings) and its fingerprint,
  and writes nothing; `hacer_desde_pc` does it with the same `nutricion.asistente.proponer` + `confirmar` as the
  app, only if the card is still the one shown (same fingerprint), and then makes a pass of Google Calendar as the
  app wakes it after confirming (062). `apuntar_comida_desde_pc` and `apuntar_peso_desde_pc` log a meal or a weight
  with the checks and repositories of the dictation (`web/app.py::confirmar`). Each change made leaves a message of
  its role, without cards, in the conversation «Desde el PC» of the Asistente (created the first time, reused
  after); what is only prepared, refused or fails writes nothing there (R-196, R-197).

Unit 081 (P-20261105-cf32bea5, mapa v29): without the paid API, what the app did with it is done by the coach, with
the same pattern (what reads changes nothing; what writes first says what it would do and its fingerprint, and does
it only with Andrés's «sí» and that same fingerprint):

- R-131, R-132: `preparar_plan_desde_pc` / `importar_plan_desde_pc`: the coach orders the plan's .md in its parts and
  the app checks it, crosses it with Garmin and saves it as its own import (`plan.importar.proponer_ordenado` +
  `confirmar`).
- R-221, R-222: `analiticas_pendientes` (the PDF without a transcription and where they are) and
  `preparar_transcripcion_desde_pc` / `guardar_transcripcion_desde_pc` (the transcription the coach made, kept with
  the SHA-256 of its PDF by `Medicos.guardar`); neither an answer that refuses nor a log carries a value.
- S-72, R-42, R-43: `recetario_texto` (the saved recipes and fixed foods) and, in `apuntar_comida_desde_pc`, a food
  named as a saved one (its name or an alias) or chosen by its id (`guardado`) takes the kcal and the origin of
  `Recetario.para_apuntar`, as the app does when a food is chosen from the saved ones (unit 019).
- S-73, R-151..R-154: `rutas_desde_pc` and `perfil_ruta_desde_pc` (`plan.rutas`), never a coordinate.

Unit 082 (REC-34 of `apuntar-comida`, R-225, R-228, R-229, R-231): `apuntar_toma_desde_pc` (coffees and B12 with
their hour and mg, apart from the foods and without kcal: `nutricion.suplementacion`), `leche_cafes_desde_pc` (the
milk of the coffees of a day, logged ONCE as a food with its kcal and spread among them) and the `hora` of the section
in `apuntar_comida_desde_pc`. The same rule as `apuntar_comida`: Andrés's «sí» first, and «Hecho» only when done.

Each save leaves its message in «Desde el PC» (R-196). Nothing here calls a paid AI.

What a tool answers never carries a value of Andrés beyond what the card says; the logs carry ids and counts only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..calendario import Calendario
from ..nutricion import Ajustes, Alimento, Cierres, ObjetivosDia, Recetario, RegistroComidas, RegistroPeso
from ..nutricion.asistente import (Cambiado, Contexto as ContextoAsistente, NoValida, Peticion, TarjetaMal,
                                   confirmar, descripcion_alimento, numero, proponer)
from ..nutricion.ajustes import DESDE_SIEMPRE
from ..nutricion.peso import peso_valido
from ..nutricion.recetas import normalizar
from ..nutricion.registro import validar_seccion
from ..nutricion.suplementacion import FALTA_LA_HORA, Suplementacion, describir, validar_hora
from ..plan import Planes
from ..plan import rutas as perfiles
from ..plan.importar import apartados_del_coach, confirmar as confirmar_plan, proponer_ordenado
from ..storage import DataStore
from ..sync import RUNNING_TYPES
from ..web.app import DIAS_RECIENTES
from ..web.calendario import Sincronizacion
from . import Conversaciones
from .cambios import CambioInterpretado, cambio_no_entendido
from .contexto import Contexto, montar
from .medicos import FueraDeLaCarpeta, Medicos, TranscripcionMedica

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
                  token: Path | str | None = None,
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
    Suplementacion(db_path)  # unit 082: its tables, as the app makes them at start (what fails leaves no trace)
    return Equipo(conversaciones=Conversaciones(db_path), asistente=asistente,
                  medicos=Medicos(db_path, medicos_dir),
                  calendario=Calendario(db_path, token, None), ahora=ahora)


def contexto(equipo: Equipo) -> Contexto:
    """`montar` over the repositories of the team: only the transcriptions already made."""
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


def apuntar_comida_desde_pc(equipo: Equipo, papel: str, dia: Any, alimentos: Any, texto: str = "",
                            seccion: Any = None, hora: Any = None) -> str:
    """The foods logged on `dia` as the dictation logs them (R-194): all of them or none.

    Unit 074 (R-214): with `seccion` (one of `registro.SECCIONES`) all of them go into that section of that day;
    without it, «Sin sección» as before. A section that is not one logs nothing and says which ones are valid.
    Unit 082 (R-229): with `hora` (HH:MM, only with a section) that section of that day keeps that hour.
    """
    if papel not in PAPELES_PC:
        return papel_no_valido(papel)
    try:
        el_dia = date.fromisoformat(str(dia))
    except ValueError:
        return "No se ha apuntado nada: el día tiene que ser una fecha AAAA-MM-DD."
    if not isinstance(alimentos, list) or not alimentos:
        return "No se ha apuntado nada: no hay nada que apuntar (alimentos, con su cantidad y sus kcal)."
    try:
        validar_seccion(seccion)
        if hora not in (None, "") and seccion is None:
            raise ValueError("la hora va con una sección (seccion): «Sin sección» no lleva hora")
        la_hora = None if hora in (None, "") else validar_hora(hora)
        lista = [Alimento.desde_dict({**_de_lo_guardado(equipo.asistente.recetario, a), "dia": el_dia.isoformat()}
                                     if isinstance(a, dict) else a)
                 for a in alimentos]
        equipo.asistente.registro.validar(lista)
    except ValueError as exc:
        return f"No se ha apuntado nada: {exc}."
    a = equipo.asistente
    guardados = a.registro.guardar(lista, hoy=a.hoy(), texto=texto.strip() or f"{TITULO} ({papel})",
                                   sin_cambios=False, seccion=seccion)
    que = "; ".join(descripcion_alimento({**g, "creado": None}) + (f" {g['origen']}" if g.get("origen") else "")
                    for g in guardados)
    if la_hora is not None:
        Suplementacion(a.registro.db_path).poner_hora_seccion(el_dia, seccion, la_hora)
    donde = f" en la sección {seccion}" if seccion else ""
    donde += f" a las {la_hora}" if la_hora else ""
    hecho = f"Hecho: apuntado el {el_dia.isoformat()}{donde}: {que}."
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


# -- unit 082 (REC-34): coffees and B12 with their hour, and the milk of the coffees ---------------------------------

LECHE = "leche de los cafés"


def _toma_dicha(t: dict) -> str:
    mg = f" ({numero(t['mg'])} mg{' estimados' if t.get('mg_estimado') else ''})" if t.get("mg") is not None else ""
    return f"{describir(t)} a las {t['hora']}{mg}"


def apuntar_toma_desde_pc(equipo: Equipo, papel: str, dia: Any, tomas: Any, texto: str = "") -> str:
    """R-225, G-60: the doses of `dia` (all of them or none), apart from the foods and without kcal. A coffee always
    carries its mg (the coach estimates them, S-81). R-231: today without an hour, the hour it is now; another day
    without one, nothing and «falta la hora» so the coach asks Andrés."""
    if papel not in PAPELES_PC:
        return papel_no_valido(papel)
    try:
        el_dia = date.fromisoformat(str(dia))
    except ValueError:
        return "No se ha apuntado nada: el día tiene que ser una fecha AAAA-MM-DD."
    if not isinstance(tomas, list) or not tomas or not all(isinstance(t, dict) for t in tomas):
        return "No se ha apuntado nada: no hay ninguna toma (cada una con que, forma, hora y mg)."
    sin_mg = [t for t in tomas if t.get("que") == "cafe" and t.get("mg") in (None, "")]
    if sin_mg:
        return "No se ha apuntado nada: faltan los mg de cafeína de un café: estímalos y enséñaselos a Andrés."
    a = equipo.asistente
    try:
        guardadas = Suplementacion(a.registro.db_path).apuntar_varias(el_dia, tomas, hoy=a.hoy(), ahora=equipo.ahora())
    except ValueError as exc:
        if FALTA_LA_HORA in str(exc):
            return f"No se ha apuntado nada: {exc}. Pregúntale a Andrés a qué hora fue y apúntala con ella."
        return f"No se ha apuntado nada: {exc}."
    hecho = (f"Hecho: apuntado el {el_dia.isoformat()} en Suplementación (sin kcal): "
             f"{'; '.join(_toma_dicha(t) for t in guardadas)}.")
    _apuntar(equipo, papel, texto, hecho)
    return hecho


def leche_cafes_desde_pc(equipo: Equipo, papel: str, dia: Any, ml: Any, kcal: Any = None, nombre: Any = None,
                         texto: str = "") -> str:
    """R-228, G-61: the milk Andrés put in the coffees of `dia`, logged ONCE as a food of that day with its kcal
    (`RegistroComidas`, as any food) and marked as the milk of the coffees, so it is spread among them. Said again
    the same day, the same food changes (never a second one)."""
    if papel not in PAPELES_PC:
        return papel_no_valido(papel)
    try:
        el_dia = date.fromisoformat(str(dia))
    except ValueError:
        return "No se ha apuntado la leche: el día tiene que ser una fecha AAAA-MM-DD."
    if isinstance(ml, bool) or not isinstance(ml, int | float) or ml <= 0:
        return "No se ha apuntado la leche: los ml tienen que ser más de 0."
    el_nombre = nombre.strip() if isinstance(nombre, str) and nombre.strip() else LECHE
    a = equipo.asistente
    sup = Suplementacion(a.registro.db_path)
    datos = {"nombre": el_nombre, "dia": el_dia.isoformat(), "cantidad": ml, "unidad": "ml", "kcal": kcal,
             "estimado": True}
    try:
        alimento = Alimento.desde_dict(datos)
        if alimento.kcal is None:
            raise ValueError("faltan sus kcal (las de esos ml de leche)")
        a.registro.validar([alimento])
        antes = sup.leche_del_dia(el_dia)
        if antes is not None:
            guardada = a.registro.cambiar(antes["alimento_id"], {k: v for k, v in datos.items() if k != "dia"})
        else:
            (guardada,) = a.registro.guardar([alimento], hoy=a.hoy(), texto=texto.strip() or f"{TITULO} ({papel})",
                                             sin_cambios=False)
    except ValueError as exc:
        return f"No se ha apuntado la leche: {exc}."
    sup.marcar_leche(el_dia, int(guardada["id"]))
    leche = sup.resumen(el_dia)["leche"] or {}
    cafes = [t for t in sup.resumen(el_dia)["tomas"] if t.get("leche_ml")]
    reparto = (f"repartida entre {len(cafes)} café{'s' if len(cafes) != 1 else ''} (+{numero(round(cafes[0]['leche_ml']))} "
               f"ml cada uno)" if cafes else "a lo largo del día (no hay cafés con leche ese día)")
    hecho = (f"Hecho: {'cambiada' if antes is not None else 'apuntada'} la leche de los cafés del {el_dia.isoformat()}: "
             f"{numero(leche.get('ml'))} ml, {numero(leche.get('kcal'))} kcal, {reparto}.")
    _apuntar(equipo, papel, texto, hecho)
    return hecho


# -- unit 081 · R3 (S-72, R-42, R-43): the recipes and fixed foods ----------------------------------------------------

def recetario_texto(equipo: Equipo) -> str:
    """The saved recipes and fixed foods, in JSON: what the coach logs with (R3). Read-only."""
    recetario = equipo.asistente.recetario
    recetas = []
    for r in recetario.recetas():
        v = r["en_uso"] or {}
        recetas.append({k: x for k, x in {
            "id": r["id"], "nombre": r["nombre"], "alias": r["alias"],
            "estado": "por_revisar" if r["por_revisar"] else v.get("estado"), "kcal_100g": v.get("kcal_100g"),
            "kcal_100g_aprox": v.get("kcal_100g_aprox") if v.get("kcal_100g") is None else None,
            "kcal_receta": v.get("kcal_total")}.items() if x not in (None, [])})
    ingredientes = [{k: x for k, x in {"id": f["id"], "nombre": f["nombre"], "alias": f["alias"], "kcal": f["kcal"],
                                         "por": f["por"], "comun": f["comun"] or None}.items() if x not in (None, [])}
                    for f in recetario.fijos()]
    return json.dumps({"recetas": recetas, "ingredientes": ingredientes}, ensure_ascii=False, separators=(",", ":"))


def _guardado_por_nombre(recetario: Recetario, nombre: Any) -> tuple[str, int] | None:
    """The usable recipe (not to review) or fixed food whose name or an alias is `nombre`, as the dictation matches."""
    clave = normalizar(nombre if isinstance(nombre, str) else "")
    if not clave:
        return None
    for r in recetario.recetas():
        if not r["por_revisar"] and clave in {normalizar(n) for n in [r["nombre"], *r["alias"]]}:
            return "receta", int(r["id"])
    for f in recetario.fijos():
        if clave in {normalizar(n) for n in [f["nombre"], *f["alias"]]}:
            return "fijo", int(f["id"])
    return None


def _de_lo_guardado(recetario: Recetario, crudo: dict) -> dict:
    """R3: a food chosen by its id (`guardado: {tipo, id}`) or named as a saved one takes the name, kcal, origin and
    provisional of `Recetario.para_apuntar` (never the coach's kcal), as the app with a food chosen from the saved ones
    (unit 019); any other one goes as it came (R-43, as before). ValueError, with why, if the saved one cannot be
    worked out with that quantity and unit: nothing is logged with another number in silence."""
    nombre = crudo.get("nombre") if isinstance(crudo.get("nombre"), str) and crudo["nombre"].strip() else "lo elegido"
    sin = {k: v for k, v in crudo.items() if k != "guardado"}
    elegido = crudo.get("guardado")
    if isinstance(elegido, dict):
        tipo, ident = elegido.get("tipo"), elegido.get("id")
        if tipo not in ("receta", "fijo") or isinstance(ident, bool) or not isinstance(ident, int):
            raise ValueError(f"«{nombre}» no es una receta ni un ingrediente guardado (guardado: tipo receta o fijo y "
                             "su id de get_recetas)")
    else:
        hallado = _guardado_por_nombre(recetario, crudo.get("nombre"))
        if hallado is None:
            return sin
        tipo, ident = hallado
    cantidad = crudo.get("cantidad")
    unidad = crudo.get("unidad")
    try:
        datos = recetario.para_apuntar(
            str(tipo), ident, float(cantidad) if isinstance(cantidad, int | float) and not isinstance(cantidad, bool)
            else None, unidad if isinstance(unidad, str) and unidad.strip() else None)
    except KeyError:
        raise ValueError(f"«{nombre}» ya no está guardado") from None
    except ValueError as exc:
        raise ValueError(f"«{nombre}»: {exc}") from None
    return {**sin, **datos, "estimado": False, "dudoso": False, "supuestos": [], "proteina_g": None,
            "hidratos_g": None, "grasa_g": None}


# -- unit 081 · R1 (R-131, R-132): the plan's .md ordered by the coach -----------------------------------------------

PREGUNTA_PLAN = ("Enséñaselo a Andrés tal cual y pregúntale; solo con su «sí», llama a importar_plan con este mismo "
                 "plan, las mismas elecciones y esta huella.")


def _hm(segundos: Any) -> str:
    return f"{int(segundos) // 3600}:{int(segundos) % 3600 // 60:02d}" if segundos is not None else "—"


def _coma(x: Any) -> str:
    return f"{float(x):g}".replace(".", ",")


def _hecho_semana(lado: dict) -> str:
    km = f"{_coma(lado['km'])} km" if lado.get("km") is not None else "— km"
    return f"{_hm(lado.get('segundos'))} y {km}"


def _resumen_plan(p: dict) -> str:
    r = p["resumen"]
    partes = [f"{r['carreras']} carreras", f"{r['semanas_hechas']} semanas hechas", f"{r['previstas']} previstas",
              f"{len(p['sesiones'])} sesiones ({r['sesiones_clave']} clave)"]
    partes += [f"{r['fases']} fases"] if r.get("fases") else []
    partes += [f"{r['nutricion']} de nutrición", f"{r['incidencias']} incidencias", f"{r['comparativa']} de comparativa",
               f"{r['notas']} notas"]
    return ", ".join(partes)


def _vista_plan(p: dict, elecciones: dict[str, str]) -> str:
    lineas = [f"Plan «{p['nombre']}»: {_resumen_plan(p)}."]
    if p["diferencias"]:
        lineas.append("Semanas ya hechas en las que Garmin no dice lo mismo que el plan (se guarda lo del plan, que viene "
                      "de Strava, salvo que Andrés elija Garmin):")
        for d in p["diferencias"]:
            gana = "Garmin" if elecciones.get(str(d["semana"])) == "garmin" else "lo del plan"
            lineas.append(f"- Semana {d['semana']} ({d['desde']} a {d['hasta']}): el plan dice {_hecho_semana(d['plan'])}; "
                          f"Garmin, {_hecho_semana(d['garmin'])} → se guarda {gana}")
    raras = [n["texto"] for n in p["notas"] if n["tipo"] == "nota"]
    if raras:
        lineas.append("Lo que no encaja en ninguna parte (irá como nota del plan, tal cual):")
        lineas += [f"- «{t if len(t) <= 300 else t[:300] + '…'}»".replace("\n", " / ") for t in raras]
    return "\n".join(lineas)


def _elecciones(p: dict, entrada: Any) -> dict[str, str]:
    """{"4": "plan" | "garmin"} for the weeks with a difference; ValueError saying what to mend."""
    if entrada is None:
        return {}
    if not isinstance(entrada, dict):
        raise ValueError("elecciones (un objeto con el número de semana y plan o garmin)")
    semanas = {str(d["semana"]) for d in p["diferencias"]}
    malas = sorted(str(k) for k, v in entrada.items() if str(k) not in semanas or v not in ("plan", "garmin"))
    if malas:
        raise ValueError(f"elecciones de las semanas {', '.join(malas)} (solo las semanas con diferencia, con plan o "
                         "garmin)")
    return {str(k): str(v) for k, v in entrada.items()}


def _plan_propuesto(equipo: Equipo, entrada: Any, elecciones: Any) -> tuple[dict, dict[str, str], str] | str:
    """(the proposal, the choices, its fingerprint), or why it cannot be read (field names only, never the text)."""
    if isinstance(entrada, str):
        try:
            entrada = json.loads(entrada)
        except ValueError:
            pass
    try:
        nombre, apartados = apartados_del_coach(entrada)
    except ValueError as exc:
        log.warning("plan from the PC not understood (%s)", exc)
        return f"No se entiende el plan: revisa {exc}. {NADA}"
    a = equipo.asistente
    propuesta = proponer_ordenado(apartados, a.store, a.hoy(), nombre)
    try:
        elegidas = _elecciones(propuesta, elecciones)
    except ValueError as exc:
        return f"No se entiende: revisa {exc}. {NADA}"
    visto = json.dumps({"plan": propuesta, "elecciones": elegidas}, ensure_ascii=False, sort_keys=True, default=str)
    return propuesta, elegidas, hashlib.sha256(visto.encode("utf-8")).hexdigest()[:12]


def preparar_plan_desde_pc(equipo: Equipo, entrada: Any, elecciones: Any = None) -> str:
    """R1 (R-131): what `importar_plan_desde_pc` would save of this plan, and its fingerprint. Writes nothing."""
    hecho = _plan_propuesto(equipo, entrada, elecciones)
    if isinstance(hecho, str):
        return hecho
    propuesta, elegidas, la_huella = hecho
    return (f"Esto es lo que se guardaría (aún no está guardado):\n{_vista_plan(propuesta, elegidas)}\n"
            f"{PREGUNTA_PLAN}\nHuella: {la_huella}")


def importar_plan_desde_pc(equipo: Equipo, papel: str, entrada: Any, huella_vista: str, elecciones: Any = None,
                           texto: str = "") -> str:
    """R1 (R-131, R-132): the plan saved as the import of the app saves it, only if it is still the one with
    `huella_vista` (the same rows, the same Garmin, the same choices); then the message of R-196 (R5)."""
    if papel not in PAPELES_PC:
        return papel_no_valido(papel)
    hecho = _plan_propuesto(equipo, entrada, elecciones)
    if isinstance(hecho, str):
        return hecho
    propuesta, elegidas, la_huella = hecho
    if la_huella != str(huella_vista).strip():
        return (f"{CAMBIO_VISTO} {NADA} Lo que se guardaría ahora:\n{_vista_plan(propuesta, elegidas)}\n"
                f"{PREGUNTA_PLAN}\nHuella: {la_huella}")
    planes = equipo.asistente.planes
    if planes is None:
        return f"No hay dónde guardar planes. {NADA}"
    try:  # unit 083 (R4): the plan and its phases in one write, so a failure leaves no plan to duplicate
        ident = confirmar_plan(propuesta, elegidas, planes, equipo.ahora())
    except (sqlite3.Error, ValueError) as exc:
        log.warning("plan from the PC not saved (%s)", type(exc).__name__)
        return f"No se ha podido guardar el plan: la base no lo ha aceptado. {NADA} Puedes volver a intentarlo."
    log.info("plan %d imported from the PC (%d weeks, %d sessions)", ident, len(propuesta["semanas"]),
             len(propuesta["sesiones"]))
    hecho_texto = f"Hecho: plan «{propuesta['nombre']}» importado ({_resumen_plan(propuesta)})."
    _apuntar(equipo, papel, texto, hecho_texto)
    return hecho_texto


# -- unit 081 · R2 (R-221, R-222): a medical PDF read by the coach -----------------------------------------------------

NO_EN_LA_CARPETA = ("Ese PDF no está en la carpeta de tus analíticas (TRIDATA_MEDICOS_DIR): solo se guarda la "
                    "transcripción de un PDF de esa carpeta (mira get_analiticas_pendientes).")
SIN_VALORES = "La transcripción no trae ningún valor: no se guarda."
_TIPOS = {"analitica": "analítica", "prueba_esfuerzo": "prueba de esfuerzo", "otro": "otro documento"}


def analiticas_pendientes(equipo: Equipo) -> str:
    """R-221: the PDF of the folder without a transcription (new or changed) and where they are. Read-only."""
    medicos = equipo.medicos
    pendientes = medicos.pendientes()
    if not pendientes:
        aviso = medicos.estado()["aviso"]
        if aviso:
            return f"{aviso}."
        return (f"No hay ningún PDF sin transcribir en la carpeta de tus analíticas "
                f"({len(medicos.transcripciones())} ya transcritos).")
    lineas = [f"- {p['fichero']} ({p['estado']}): {p['ruta']}" for p in pendientes]
    return ("PDF de la carpeta de tus analíticas sin transcribir (nuevo: nunca leído; cambiado: el fichero ya no es el "
            "que se leyó):\n" + "\n".join(lineas) + "\nLéelo tú y llama a preparar_transcripcion con su fichero y lo "
            "que transcribas.")


def _transcripcion(entrada: Any) -> TranscripcionMedica | str:
    """The transcription as `TranscripcionMedica`, or what to mend: field names only, never a value (R-222)."""
    if isinstance(entrada, str):
        try:
            entrada = json.loads(entrada)
        except ValueError:
            pass
    if not isinstance(entrada, dict):
        return f"No se entiende la transcripción: tiene que ser un objeto con tipo, titulo, fecha, valores y notas. {NADA}"
    datos = {"fecha": None, "notas": [], **entrada}
    if isinstance(datos.get("valores"), list):
        datos["valores"] = [{"unidad": None, "rango": None, "fecha": None, **v} if isinstance(v, dict) else v
                            for v in datos["valores"]]
    try:
        transcripcion = TranscripcionMedica.model_validate(datos)
        if transcripcion.fecha is not None:
            date.fromisoformat(transcripcion.fecha)
    except ValidationError as exc:
        campos = sorted({".".join(str(x) for x in e["loc"]) for e in exc.errors() if e["loc"]})
        log.warning("medical transcription from the PC not understood, fields %s", ", ".join(campos) or "-")
        return f"No se entiende la transcripción: revisa {', '.join(campos) or 'su forma'}. {NADA}"
    except ValueError:
        return f"No se entiende la transcripción: revisa fecha (AAAA-MM-DD o null). {NADA}"
    if not transcripcion.valores:
        return f"{SIN_VALORES} {NADA}"
    return transcripcion


def _transcripcion_vista(equipo: Equipo, fichero: Any,
                         entrada: Any) -> tuple[str, str, TranscripcionMedica, str] | str:
    """(name in the folder, SHA-256 of the PDF, the transcription, its fingerprint), or why not (never a value)."""
    try:
        nombre, sha = equipo.medicos.pdf(str(fichero))
    except FueraDeLaCarpeta:
        log.warning("medical transcription from the PC: the file is not in the folder")
        return f"{NO_EN_LA_CARPETA} {NADA}"
    transcripcion = _transcripcion(entrada)
    if isinstance(transcripcion, str):
        return transcripcion
    visto = json.dumps(transcripcion.model_dump(), ensure_ascii=False, sort_keys=True)
    return nombre, sha, transcripcion, hashlib.sha256(f"{sha}\n{visto}".encode()).hexdigest()[:12]


def _que_transcripcion(nombre: str, t: TranscripcionMedica) -> str:
    valores = f"{len(t.valores)} {'valor' if len(t.valores) == 1 else 'valores'}"
    return f"«{nombre}» ({_TIPOS[t.tipo]}, {valores}, {f'del {t.fecha}' if t.fecha else 'sin fecha'})"


def preparar_transcripcion_desde_pc(equipo: Equipo, fichero: Any, entrada: Any) -> str:
    """R-221: how many values and of what date would be kept as the transcription of `fichero`, and its fingerprint.
    Writes nothing."""
    vista = _transcripcion_vista(equipo, fichero, entrada)
    if isinstance(vista, str):
        return vista
    nombre, _, transcripcion, la_huella = vista
    return (f"Esto es lo que se guardaría (aún no está guardado): la transcripción de "
            f"{_que_transcripcion(nombre, transcripcion)}, con {len(transcripcion.notas)} notas.\n"
            "Enséñaselo a Andrés y pregúntale; solo con su «sí», llama a guardar_transcripcion con este mismo fichero, "
            f"esta transcripción y esta huella.\nHuella: {la_huella}")


def guardar_transcripcion_desde_pc(equipo: Equipo, papel: str, fichero: Any, entrada: Any, huella_vista: str,
                                   texto: str = "") -> str:
    """R-221, R-222: the transcription kept as the one of that PDF (with its SHA-256), only if it is still what was
    shown (`huella_vista`: the same PDF, the same transcription); then the message of R-196, without any value."""
    if papel not in PAPELES_PC:
        return papel_no_valido(papel)
    vista = _transcripcion_vista(equipo, fichero, entrada)
    if isinstance(vista, str):
        return vista
    nombre, sha, transcripcion, la_huella = vista
    if la_huella != str(huella_vista).strip():
        return f"{CAMBIO_VISTO} {NADA} Prepárala otra vez con preparar_transcripcion y enséñasela."
    try:
        equipo.medicos.guardar(nombre, sha, transcripcion)
    except FueraDeLaCarpeta:
        return f"{NO_EN_LA_CARPETA} {NADA}"
    except ValueError as exc:  # the PDF changed in between: its text has no value
        return f"No se ha guardado: {exc}. {NADA}"
    hecho = f"Hecho: guardada la transcripción de {_que_transcripcion(nombre, transcripcion)}."
    _apuntar(equipo, papel, texto, hecho)
    return hecho


# -- unit 081 · R4 (S-73, R-151..R-154): the profile of a route ------------------------------------------------------

NO_GARMIN = ("Garmin no responde ahora: pide a Andrés el GPX de la ruta (en Garmin Connect o en Strava, «Exportar "
             "GPX») y pásame la ruta del fichero en su PC.")
RUTAS_HECHAS = 20  # the last runs offered as a route, as the app offered them
GPX_MAX = 10 * 1024 * 1024


class _GarminCaido(Exception):
    pass


def _de_garmin(equipo: Equipo, llamada: Callable[[Any], Any]) -> Any:
    planes = equipo.asistente.planes
    try:
        if planes is None:
            raise RuntimeError("no plans, no Garmin")
        return llamada(planes.garmin())
    except Exception as exc:  # noqa: BLE001 - the unofficial way fails at times: said, and a GPX asked for (R-153)
        log.warning("route from the PC: Garmin did not answer (%s)", type(exc).__name__)
        raise _GarminCaido from None


def rutas_desde_pc(equipo: Equipo) -> str:
    """R-151: the routes saved in Garmin and the last runs done, by their id. Read-only (Garmin is only read)."""
    lineas: list[str] = []
    try:
        guardadas = _de_garmin(equipo, lambda g: g.rutas_guardadas())
    except _GarminCaido:
        lineas.append(NO_GARMIN)
    else:
        lineas.append("Rutas guardadas en Garmin (get_perfil_ruta con garmin = su id):" if guardadas
                      else "No hay rutas guardadas en Garmin.")
        for r in guardadas:
            km = f" · {_coma(round(float(r['distancia_m']) / 1000, 1))} km" if r.get("distancia_m") else ""
            sube = f" · +{round(r['sube_m'])} m" if r.get("sube_m") is not None else ""
            lineas.append(f"- {r['id']} · {r['nombre']}{km}{sube}")
    a = equipo.asistente
    hechas = []
    if a.store is not None:
        hechas = [x for x in a.store.activities_between(DESDE_SIEMPRE, a.hoy())
                  if x.get("activity_type") in RUNNING_TYPES and x.get("distance_meters")]
        hechas.sort(key=lambda x: (x.get("activity_date") or "", x["activity_id"]), reverse=True)
    if hechas:
        lineas.append("Carreras ya hechas (get_perfil_ruta con actividad = su id):")
        for x in hechas[:RUTAS_HECHAS]:
            sube = f" · +{round(x['elevation_gain_m'])} m" if x.get("elevation_gain_m") is not None else ""
            lineas.append(f"- {x['activity_id']} · {x.get('name') or 'Carrera'} · {x.get('activity_date')} · "
                          f"{_coma(round(float(x['distance_meters']) / 1000, 1))} km{sube}")
    lineas.append("O un GPX del PC: get_perfil_ruta con gpx = la ruta del fichero.")
    return "\n".join(lineas)


def texto_perfil(p: dict) -> str:
    """The profile in words: km, climb, stretches and the altitude every 100 m. Never a coordinate (G-57)."""
    tramos = "\n".join(
        f"- km {_coma(t['desde_km'])}–{_coma(t['hasta_km'])} · {t['terreno']} {t['desnivel_m']:+d} m"
        + (f" ({_coma(abs(t['pendiente_pct']))} %)" if t["terreno"] != "llano" else "")
        for t in p["tramos"])
    return (f"Perfil de la ruta {perfiles.resumen(p)}. Solo su perfil: sus coordenadas se quedan en el PC.\n"
            f"Tramos:\n{tramos}\n"
            f"Altitud cada {p['paso_m']} m desde el km 0 (m): {', '.join(str(x) for x in p['altitudes'])}\n"
            "Para la estrategia de la carrera, una línea por tramo (sus km, su terreno, el ritmo o el esfuerzo y el paso "
            "acumulado) y los geles en su km; para guardarla en la carrera del plan, preparar_cambio con la acción "
            "guardar_estrategia.")


def perfil_ruta_desde_pc(equipo: Equipo, garmin: Any = None, actividad: Any = None, gpx: Any = None) -> str:
    """R-152..R-154: the profile of a route saved in Garmin, of a run done or of a GPX of the PC. Without altitude or
    without Garmin it says so and asks for a GPX (R-153). Read-only."""
    try:
        if isinstance(gpx, str) and gpx.strip():
            ruta = Path(gpx.strip()).expanduser()
            if not ruta.is_file():
                return "No encuentro ese GPX en el PC: pide a Andrés la ruta completa del fichero."
            if ruta.stat().st_size > GPX_MAX:
                return "Ese GPX pasa de 10 MB: no lo leo."
            datos = ruta.read_bytes()
            p = perfiles.perfil(perfiles.puntos_gpx(datos), perfiles.nombre_gpx(datos) or ruta.stem, "gpx")
        elif garmin is not None and not isinstance(garmin, bool):
            curso = int(garmin)
            puntos = _de_garmin(equipo, lambda g: g.ruta(curso))
            try:
                nombre = next((r["nombre"] for r in _de_garmin(equipo, lambda g: g.rutas_guardadas())
                               if r["id"] == curso), "Ruta de Garmin")
            except _GarminCaido:
                nombre = "Ruta de Garmin"
            p = perfiles.perfil(perfiles.puntos_garmin(puntos), nombre, "garmin")
        elif isinstance(actividad, str) and actividad.strip():
            store = equipo.asistente.store
            hecha = store.get_activity(actividad.strip()) if store is not None else None
            if not hecha:
                return "Esa actividad no está en el PC: mira sus ids con get_rutas."
            datos = _de_garmin(equipo, lambda g: g.gpx_actividad(actividad.strip()))
            p = perfiles.perfil(perfiles.puntos_gpx(datos), hecha.get("name") or "Carrera", "actividad")
        else:
            return "Dime qué ruta: garmin (su id de get_rutas), actividad (su id) o gpx (la ruta del fichero en el PC)."
    except _GarminCaido:
        return NO_GARMIN
    except perfiles.SinAltitud:
        return f"{perfiles.SIN_ALTITUD} Pide a Andrés un GPX con altitud."
    except perfiles.RutaMal as exc:
        return f"{exc} Pide a Andrés el GPX de la ruta."
    except (TypeError, ValueError, OSError):
        return "No puedo leer esa ruta: pide a Andrés el GPX y pásame la ruta del fichero en su PC."
    log.info("route profile for the PC (%s, %d stretches)", p["fuente"], len(p["tramos"]))
    return texto_perfil(p)


__all__ = ["NADA", "PAPELES_PC", "TITULO", "Equipo", "analiticas_pendientes", "apuntar_comida_desde_pc",
           "apuntar_peso_desde_pc", "apuntar_toma_desde_pc", "leche_cafes_desde_pc", "contexto", "conversacion_pc", "guardar_transcripcion_desde_pc", "hacer_desde_pc",
           "huella", "importar_plan_desde_pc", "montar_equipo", "papel_no_valido", "perfil_ruta_desde_pc",
           "preparar_desde_pc", "preparar_plan_desde_pc", "preparar_transcripcion_desde_pc", "recetario_texto",
           "rutas_desde_pc", "texto_contexto", "texto_perfil"]
