"""The JSON routes of the Asistente's conversations (unit 032, REC-20), mounted by `crear_app` (as `web/recetas.py`).

    GET  /api/conversaciones                    {conversaciones: newest use first, each with its title, `usada` and
                                                the beginning of its last message; tope: {dolares, gastado,
                                                alcanzado}} (R1, R9)
    POST /api/conversaciones                    {"texto", "pensar"?} a new conversation, titled with its first message
                                                (R7), and its answer as it arrives (below)
    GET  /api/conversaciones/N                  {conversacion, mensajes, linea}: everything said and the line «Ve
                                                tus …, hasta hoy 9:42» (R2, R5); 404 if it does not exist
    POST /api/conversaciones/N/mensajes         {"texto", "pensar"?} -> the answer as it arrives (R2)
    POST /api/conversaciones/N/reintentar       {"mensaje_id"} an unanswered message of Andrés, sent again without
                                                writing it (R6); 404 if it is not one
    POST /api/conversaciones/N/titulo           {"titulo"} Andrés's title (R7); empty is 400
    POST /api/conversaciones/N/borrar           {"confirmado": true} the whole conversation and nothing else (R7);
                                                without it 400 and nothing is deleted
    POST /api/conversaciones/mensaje/N/tarjetas {"tarjetas"} the cards of an answer still waiting (R8: one confirmed
                                                or discarded with /api/asistente/* goes)
    GET  /api/conversaciones/medicos            what it knows of the blood test and the stress test (R4); never calls
                                                the API
    POST /api/conversaciones/medicos/releer     reads every PDF again («Volver a leerlas»)
    POST /api/conversaciones/dictado            raw audio -> {"texto"} transcribed on this PC, for the box of the chat
                                                (S-37); 503 without the transcriber

Unit 041 (REC-23): the team.

    POST /api/conversaciones                    also {"papel"}: who Andrés talks to (R1; without it the Asistente,
                                                400 if there is no such role); the list and the conversation carry it
    POST /api/conversaciones/N/opinion          {"papel"} «Pedir opinión a…»: that role answers in this conversation,
                                                with its name, seeing what was said (R3); nothing of Andrés is saved.
                                                400 for an unknown role or a conversation without anything said
    GET  /api/equipo                            {papeles: each role with its model, modelos: the catalogue, tope}
    PUT  /api/equipo                            {"papel", "modelo"} the model of a role, among the catalogue (R7);
                                                anything else is 400 and nothing changes

Every answer keeps the role and the model that gave it, and the line says the model. A role can open an activity
(`abrir_actividad`: its laps with pace, HR, cadence and climb, by its date or its id, R4).

An answer is `application/x-ndjson`, one JSON per line: {"tipo": "inicio", conversacion, mensaje} (Andrés's message,
saved «sin respuesta» until the answer arrives), {"tipo": "trozo", texto} as it arrives, and {"tipo": "fin",
conversacion, mensaje (the answer, with its `tarjetas`), pregunta, linea} or {"tipo": "error", error, mensaje} (it
stays «sin respuesta», R6). At the spending cap of the month it is a 429 and nothing is sent (R9).

The logs say the id of the conversation, counts and token use; never a text nor a medical value (R12).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ..conversaciones import TOPE_DOLARES_MES, Conversaciones, NoEsta, titulo_provisional
from ..conversaciones.contexto import VE_SIEMPRE, Contexto, linea
from ..conversaciones.medicos import Medicos
from ..conversaciones.papeles import PAPELES, POR_DEFECTO, bloque
from ..fitness import detalle_actividad
from ..ia.modelos import MODELOS, nombre_de
from ..ia import ErrorTranscripcion
from ..ia.claude import CambioInterpretado
from ..ia.conversacion import ErrorConversacion, Respuesta
from ..nutricion.asistente import Contexto as ContextoAsistente, Peticion, proponer
from ..plan import Planes

log = logging.getLogger(__name__)

AUDIO_MAX_BYTES = 10 * 1024 * 1024
NO_DICTADO = "El dictado no está disponible en el PC ahora mismo. Escribe la pregunta."
SIN_TEXTO = "Te he dejado el cambio aquí abajo."


def _error(mensaje: str, estado: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": mensaje, **extra}, status_code=estado)


async def _cuerpo(request: Request) -> dict:
    try:
        datos = await request.json()
    except (ValueError, json.JSONDecodeError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _linea_json(evento: dict) -> bytes:
    return (json.dumps(evento, ensure_ascii=False) + "\n").encode()


def _nota_tarjetas(propuesto: list[str]) -> str:
    """What Claude is told it proposed in an earlier answer (the card itself never goes back)."""
    return f"\n\n[Le propuse en una tarjeta: {'; '.join(propuesto)}]" if propuesto else ""


def _nombre(papel: str | None) -> str:
    return PAPELES[papel].nombre if papel in PAPELES else PAPELES[POR_DEFECTO].nombre


def peticion_de_opinion(papel: str) -> str:
    """What the role asked for an opinion is told (unit 041, R3): the turn of Andrés that asks it."""
    return f"[Andrés pide la opinión del {_nombre(papel)} sobre lo hablado]"


def _historial(mensajes: list[dict], papel: str = POR_DEFECTO) -> list[dict[str, Any]]:
    """What was said in THIS conversation (S-41), without the unanswered messages of Andrés. Unit 041: an answer of
    another role than `papel` (the one that answers now) goes with its name, «[Fisio] …», and an opinion asked in
    between is a turn of Andrés, so user and assistant always take turns."""
    historial: list[dict[str, Any]] = []
    for m in mensajes:
        if m["quien"] == "andres":
            if not m["sin_respuesta"]:
                historial.append({"role": "user", "content": m["texto"]})
        elif historial:
            if historial[-1]["role"] == "assistant":
                historial.append({"role": "user", "content": peticion_de_opinion(m["papel"])})
            texto = (m["texto"] or "…") + _nota_tarjetas(m["propuesto"])
            if (m["papel"] or POR_DEFECTO) != papel:
                texto = f"[{_nombre(m['papel'])}] {texto}"
            historial.append({"role": "assistant", "content": texto})
    return historial


def detalle_con_vueltas(store: Any, ajustes: Any, ident: str) -> dict | None:
    """The detail of Fitness of one activity plus the cadence and the climb of it and of each lap (as
    `/api/plan/actividad`, unit 040), or None if it is not there."""
    detalle = detalle_actividad(store, ajustes.historial_fc(), ident)
    if detalle is None:
        return None
    actividad = store.get_activity(ident) or {}
    laps = store.get_laps(ident)
    detalle["actividad"] = {**detalle["actividad"], "cadencia": actividad.get("avg_cadence"),
                            "desnivel_m": actividad.get("elevation_gain_m")}
    detalle["vueltas"] = [{**v, "cadencia": lap.get("avg_cadence"), "desnivel_m": lap.get("elevation_gain_m")}
                          for v, lap in zip(detalle["vueltas"], laps, strict=True)]
    return detalle


def abrir_actividad(store: Any, ajustes: Any, entrada: dict) -> str:
    """The result of the tool `abrir_actividad` (R4): {"actividades": [the detail of each one]} by its id or its date,
    as JSON; an empty list (and why) if there is none."""
    if store is None:
        return json.dumps({"actividades": [], "aviso": "No hay datos de Garmin en el PC."}, ensure_ascii=False)
    ident = str(entrada.get("id") or "")
    fecha = str(entrada.get("fecha") or "")
    ids: list[str] = []
    if ident.strip():
        ids = [ident.strip()]
    elif fecha.strip():
        try:
            dia = date.fromisoformat(fecha.strip())
        except ValueError:
            return json.dumps({"actividades": [], "aviso": "Esa fecha no es AAAA-MM-DD."}, ensure_ascii=False)
        ids = [a["activity_id"] for a in store.activities_between(dia, dia)]
    detalles = [d for d in (detalle_con_vueltas(store, ajustes, i) for i in ids) if d is not None]
    return json.dumps({"actividades": detalles}, ensure_ascii=False)


def _resultado_herramienta(tarjeta: dict) -> str:
    if tarjeta["tipo"] == "propuesta":
        return (f"Tarjeta preparada: «{tarjeta['texto']}». Andrés la confirma o la descarta en la app; no está hecho "
                "hasta que la confirme.")
    if tarjeta["tipo"] == "elegir":
        return f"Andrés tiene que elegir en la app: {tarjeta['texto']}"
    return f"No se ha preparado ninguna tarjeta: {tarjeta['texto']}."


def rutas(conversaciones: Conversaciones, conversador: Any, medicos: Medicos, montar: Callable[[], Contexto],
          asistente: ContextoAsistente, hoy: Callable[[], date], ahora: Callable[[], datetime],
          transcriptor: Any = None, planes: Planes | None = None) -> list[Route]:
    """`montar()` brings the medical documents up to date and builds the context of now (`conversaciones.contexto`);
    `asistente` is the context of the cards of the 021, the same as /api/asistente/* (its `store` and `ajustes` open
    an activity); `planes` the training plans, for the line (unit 041)."""

    def _tope() -> dict[str, Any]:
        gastado = conversaciones.gasto_del_mes(ahora())
        return {"dolares": TOPE_DOLARES_MES, "gastado": round(gastado, 4), "alcanzado": gastado >= TOPE_DOLARES_MES}

    def _sin_tope() -> JSONResponse:
        return _error(f"Has llegado al tope de {TOPE_DOLARES_MES:g} $ de este mes: el asistente vuelve el día 1. "
                      "La caja de Hoy y de Nutrición › Día sigue funcionando.", 429, tope=True)

    def _conversar(ident: int, texto: str | None, pensar: bool, papel: str | None = None) -> Iterator[bytes]:
        """Andrés's message, saved first «sin respuesta», then the answer as it arrives (a sync generator: Starlette
        runs it in its thread pool). Unit 041: the role of the conversation answers; with `papel`, «Pedir opinión
        a…»: that role answers what was said (`texto` None: nothing of Andrés is saved)."""
        momento = ahora()
        antes = conversaciones.mensajes(ident)
        papel = papel or conversaciones.conversacion(ident)["papel"]
        modelo = conversaciones.modelo_de(papel)
        pregunta: dict[str, Any] | None = None
        if texto is not None:
            pregunta = conversaciones.anadir_mensaje(ident, "andres", texto, momento, sin_respuesta=True,
                                                     contexto_hasta=momento)
        mensaje = texto if texto is not None else peticion_de_opinion(papel)
        yield _linea_json({"tipo": "inicio", "conversacion": conversaciones.conversacion(ident), "mensaje": pregunta})
        tarjetas: list[dict] = []
        primero = not antes

        def sin_respuesta() -> dict[str, Any] | None:
            return conversaciones.mensaje(pregunta["id"]) if pregunta is not None else None

        def al_usar(nombre: str, entrada: dict) -> str:
            if nombre == "poner_titulo":
                if primero and isinstance(entrada.get("titulo"), str):
                    conversaciones.titular_sola(ident, entrada["titulo"])
                return "Título puesto."
            if nombre == "proponer_cambio":
                try:
                    cambio = CambioInterpretado.model_validate(entrada)
                except ValidationError:
                    return "No he entendido ese cambio: no se ha preparado ninguna tarjeta."
                nuevas = proponer([cambio], asistente, Peticion(texto=mensaje, fecha=hoy()))
                tarjetas.extend(nuevas)
                return " ".join(_resultado_herramienta(t) for t in nuevas)
            if nombre == "abrir_actividad":
                return abrir_actividad(asistente.store, asistente.ajustes, entrada)
            return "Esa herramienta no existe."

        try:
            ctx = montar()
            respuesta: Respuesta | None = None
            for evento in conversador.responder(ctx.bloques, _historial(antes, papel), mensaje, pensar=pensar,
                                                al_usar=al_usar, modelo=modelo, papel=bloque(papel)):
                if isinstance(evento, Respuesta):
                    respuesta = evento
                else:
                    yield _linea_json({"tipo": "trozo", "texto": evento})
            if respuesta is None:
                raise ErrorConversacion("la respuesta no ha llegado entera")
        except ErrorConversacion as exc:
            log.warning("conversation %d: no answer (%s)", ident, type(exc.__cause__ or exc).__name__)
            for uso in exc.usos:  # bug 038: what was charged is counted, answer or not
                conversaciones.apuntar_gasto(ahora(), exc.modelo or "", uso, "mensaje")
            yield _linea_json({"tipo": "error", "error": f"Sin respuesta: {exc}. Puedes reintentarlo.",
                               "conversacion": conversaciones.conversacion(ident), "mensaje": sin_respuesta()})
            return
        except Exception as exc:  # noqa: BLE001 - said on the screen; logged without any text
            log.error("conversation %d: no answer (unexpected %s)", ident, type(exc).__name__)
            yield _linea_json({"tipo": "error", "error": "Sin respuesta: algo ha fallado en el PC. Puedes reintentarlo.",
                               "conversacion": conversaciones.conversacion(ident), "mensaje": sin_respuesta()})
            return
        for uso in respuesta.usos or (respuesta.uso,):  # one row per call: the price tier goes per call (bug 038)
            conversaciones.apuntar_gasto(ahora(), respuesta.modelo, uso, "mensaje")
        if pregunta is not None:
            pregunta = conversaciones.respondido(pregunta["id"])
        cuerpo = respuesta.texto or (SIN_TEXTO if tarjetas else "…")
        if respuesta.cortada:
            cuerpo += " (respuesta cortada)"
        contestado = conversaciones.anadir_mensaje(ident, "asistente", cuerpo, ahora(), contexto_hasta=momento,
                                                   modelo=respuesta.modelo, uso=respuesta.uso, tarjetas=tarjetas,
                                                   papel=papel)
        u = respuesta.uso
        log.info("conversation %d: answered by %s with %s, %d cards, tokens in=%d out=%d cache_write=%d "
                 "cache_read=%d", ident, papel, respuesta.modelo, len(tarjetas), u["entrada"], u["salida"],
                 u["cache_escritura"], u["cache_lectura"])
        yield _linea_json({"tipo": "fin", "conversacion": conversaciones.conversacion(ident), "mensaje": contestado,
                           "pregunta": pregunta, "linea": linea(ctx, momento, respuesta.modelo)})

    def _flujo(ident: int, texto: str | None, pensar: bool, papel: str | None = None) -> StreamingResponse:
        return StreamingResponse(_conversar(ident, texto, pensar, papel), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def _texto(cuerpo: dict) -> str | None:
        texto = cuerpo.get("texto")
        return texto.strip() if isinstance(texto, str) and texto.strip() else None

    def _linea(modelo: str | None = None) -> str:
        """The line of R5 without building the whole context: what the medical documents say now and, unit 041, the
        plan in course and its incidents, and the model."""
        estado = medicos.estado()
        tipos = {d["tipo"] for d in estado["documentos"]}
        ve = [*VE_SIEMPRE, *(n for t, n in (("analitica", "analítica"), ("prueba_esfuerzo", "prueba de esfuerzo"))
                             if t in tipos)]
        ident = planes.en_curso(hoy()) if planes is not None else None
        plan = planes.leer(ident) if planes is not None and ident is not None else None
        return linea(Contexto(bloques=(), ve=tuple(ve), aviso=estado["aviso"], plan=plan["nombre"] if plan else None,
                              incidencias=len(plan["incidencias"]) if plan else 0), ahora(), modelo)

    def _equipo() -> dict[str, Any]:
        """The team with the model of each role (R7) and the catalogue it is chosen from."""
        elegidos = conversaciones.modelos_de_papeles()
        return {"papeles": [{"id": k, "nombre": p.nombre, "icono": p.icono, "descripcion": p.descripcion,
                             "modelo": elegidos[k], "modelo_nombre": nombre_de(elegidos[k])}
                            for k, p in PAPELES.items()],
                "modelos": [{"id": m.id, "proveedor": m.proveedor, "nombre": m.nombre, "descripcion": m.descripcion}
                            for m in MODELOS.values()],
                "tope": _tope()}

    async def lista(request: Request) -> Response:
        datos = await run_in_threadpool(lambda: {"conversaciones": conversaciones.lista(), "tope": _tope(),
                                                 "linea": _linea(), "papeles": _equipo()["papeles"]})
        return JSONResponse(datos)

    def _papel_pedido(cuerpo: dict, por_defecto: str | None) -> str | None:
        papel = cuerpo.get("papel", por_defecto)
        return papel if isinstance(papel, str) and papel in PAPELES else None

    async def nueva(request: Request) -> Response:
        cuerpo = await _cuerpo(request)
        texto = _texto(cuerpo)
        if texto is None:
            return _error("Escribe tu pregunta.", 400)
        papel = _papel_pedido(cuerpo, POR_DEFECTO)
        if papel is None:
            return _error("Elige con quién quieres hablar: Entrenador, Fisio, Nutricionista o Asistente.", 400)
        if await run_in_threadpool(conversaciones.tope_alcanzado, ahora()):
            return _sin_tope()
        conversacion = await run_in_threadpool(conversaciones.crear, titulo_provisional(texto), ahora(), papel)
        log.info("conversation %d: created for %s", conversacion["id"], papel)
        return _flujo(conversacion["id"], texto, cuerpo.get("pensar") is True)

    async def opinion(request: Request) -> Response:
        ident = request.path_params["id"]
        cuerpo = await _cuerpo(request)
        try:
            mensajes = await run_in_threadpool(conversaciones.mensajes, ident)
            await run_in_threadpool(conversaciones.conversacion, ident)
        except NoEsta:
            return _error("Esa conversación ya no está.", 404)
        papel = _papel_pedido(cuerpo, None)
        if papel is None:
            return _error("Elige a quién pedir opinión: Entrenador, Fisio, Nutricionista o Asistente.", 400)
        if not any(m["quien"] == "asistente" for m in mensajes):
            return _error("Aún no habéis hablado de nada: pregunta primero.", 400)
        if await run_in_threadpool(conversaciones.tope_alcanzado, ahora()):
            return _sin_tope()
        log.info("conversation %d: opinion of %s", ident, papel)
        return _flujo(ident, None, False, papel)

    def _vista(ident: int) -> dict[str, Any]:
        conversacion = conversaciones.conversacion(ident)
        return {"conversacion": conversacion, "mensajes": conversaciones.mensajes(ident),
                "linea": _linea(conversaciones.modelo_de(conversacion["papel"]))}

    async def ver_equipo(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(_equipo))

    async def cambiar_equipo(request: Request) -> Response:
        cuerpo = await _cuerpo(request)
        papel, modelo = cuerpo.get("papel"), cuerpo.get("modelo")
        if not isinstance(papel, str) or not isinstance(modelo, str):
            return _error("Di el papel y el modelo: no se ha cambiado nada.", 400)
        try:
            await run_in_threadpool(conversaciones.elegir_modelo, papel, modelo)
        except ValueError as exc:
            return _error(f"{exc} No se ha cambiado nada.", 400)
        log.info("team: %s answers with %s", papel, modelo)
        return JSONResponse(await run_in_threadpool(_equipo))

    async def ver(request: Request) -> Response:
        try:
            return JSONResponse(await run_in_threadpool(_vista, request.path_params["id"]))
        except NoEsta:
            return _error("Esa conversación ya no está.", 404)

    async def enviar(request: Request) -> Response:
        ident = request.path_params["id"]
        cuerpo = await _cuerpo(request)
        texto = _texto(cuerpo)
        try:
            await run_in_threadpool(conversaciones.conversacion, ident)
        except NoEsta:
            return _error("Esa conversación ya no está.", 404)
        if texto is None:
            return _error("Escribe tu pregunta.", 400)
        if await run_in_threadpool(conversaciones.tope_alcanzado, ahora()):
            return _sin_tope()
        return _flujo(ident, texto, cuerpo.get("pensar") is True)

    async def reintentar(request: Request) -> Response:
        ident = request.path_params["id"]
        cuerpo = await _cuerpo(request)
        ident_mensaje = cuerpo.get("mensaje_id")
        mensaje = None
        if isinstance(ident_mensaje, int) and not isinstance(ident_mensaje, bool):
            try:
                mensaje = await run_in_threadpool(conversaciones.mensaje, ident_mensaje)
            except NoEsta:
                mensaje = None
        if (mensaje is None or mensaje["conversacion_id"] != ident or mensaje["quien"] != "andres"
                or not mensaje["sin_respuesta"]):
            return _error("Ese mensaje no está esperando respuesta.", 404)
        if await run_in_threadpool(conversaciones.tope_alcanzado, ahora()):
            return _sin_tope()
        await run_in_threadpool(conversaciones.quitar_mensaje, mensaje["id"])  # it goes again, at the end
        return _flujo(ident, mensaje["texto"], cuerpo.get("pensar") is True)

    async def titulo(request: Request) -> Response:
        cuerpo = await _cuerpo(request)
        nuevo = cuerpo.get("titulo")
        if not isinstance(nuevo, str):
            return _error("El título no puede quedar vacío.", 400)
        try:
            conversacion = await run_in_threadpool(conversaciones.renombrar, request.path_params["id"], nuevo)
        except NoEsta:
            return _error("Esa conversación ya no está.", 404)
        except ValueError as exc:
            return _error(str(exc), 400)
        return JSONResponse({"conversacion": conversacion})

    async def borrar(request: Request) -> Response:
        if (await _cuerpo(request)).get("confirmado") is not True:
            return _error("Hay que confirmar antes de borrar: no se ha borrado nada.", 400)
        ident = request.path_params["id"]
        try:
            await run_in_threadpool(conversaciones.borrar, ident)
        except NoEsta:
            return _error("Esa conversación ya no está.", 404)
        log.info("conversation %d: deleted", ident)
        return JSONResponse({"borrada": True})

    async def poner_tarjetas(request: Request) -> Response:
        tarjetas = (await _cuerpo(request)).get("tarjetas")
        if not isinstance(tarjetas, list) or not all(isinstance(t, dict) for t in tarjetas):
            return _error("Eso no son tarjetas.", 400)
        try:
            mensaje = await run_in_threadpool(conversaciones.poner_tarjetas, request.path_params["id"], tarjetas)
        except NoEsta:
            return _error("Esa respuesta ya no está.", 404)
        return JSONResponse({"mensaje": mensaje})

    async def ver_medicos(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(medicos.estado))

    async def releer_medicos(request: Request) -> Response:
        if await run_in_threadpool(conversaciones.tope_alcanzado, ahora()):
            return _sin_tope()
        return JSONResponse(await run_in_threadpool(lambda: medicos.al_dia(forzar=True)))

    async def dictado(request: Request) -> Response:
        if transcriptor is None:
            return _error(NO_DICTADO, 503)
        trozos = bytearray()
        async for trozo in request.stream():
            trozos.extend(trozo)
            if len(trozos) > AUDIO_MAX_BYTES:
                return _error("La grabación pasa de 10 MB.", 413)
        if not trozos:
            return _error("La grabación está vacía.", 422)
        try:
            texto = await run_in_threadpool(transcriptor.transcribir, bytes(trozos))
        except ErrorTranscripcion as exc:
            if exc.motivo in ("vacio", "audio_no_valido"):
                return _error("No he oído nada en la grabación.", 422)
            return _error(NO_DICTADO, 503)
        return JSONResponse({"texto": texto})

    return [
        Route("/api/conversaciones", lista),
        Route("/api/conversaciones", nueva, methods=["POST"]),
        Route("/api/conversaciones/medicos", ver_medicos),
        Route("/api/conversaciones/medicos/releer", releer_medicos, methods=["POST"]),
        Route("/api/conversaciones/dictado", dictado, methods=["POST"]),
        Route("/api/conversaciones/mensaje/{id:int}/tarjetas", poner_tarjetas, methods=["POST"]),
        Route("/api/conversaciones/{id:int}", ver),
        Route("/api/conversaciones/{id:int}/mensajes", enviar, methods=["POST"]),
        Route("/api/conversaciones/{id:int}/opinion", opinion, methods=["POST"]),
        Route("/api/equipo", ver_equipo),
        Route("/api/equipo", cambiar_equipo, methods=["PUT"]),
        Route("/api/conversaciones/{id:int}/reintentar", reintentar, methods=["POST"]),
        Route("/api/conversaciones/{id:int}/titulo", titulo, methods=["POST"]),
        Route("/api/conversaciones/{id:int}/borrar", borrar, methods=["POST"]),
    ]
