"""Unit 032 through the web (`web/conversaciones.py`): list, create, send with the answer as it arrives, retry,
rename, delete, the cards of change inside a conversation, the spending cap, the medical transcription and the card
of Hoy. Starlette's TestClient over a temporary SQLite; Claude is `ClaudeFalso` (no API, no cost).

Plans `preguntar-equipo` REC-20: R1 (C-153), R2 (C-154, C-157), R6 (C-158), R7 (C-159, C-161), R8 (R-96, C-137),
R9, R11 (R-97, R-98), R12 (C-160). Every value is invented.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from test_conversaciones import VALOR_INVENTADO, ClaudeFalso, PdfFalso, fallo_de_red
from test_conversaciones_contexto import HOY, Mundo
from tridata.conversaciones import Conversaciones
from tridata.conversaciones.medicos import TranscriptorPDF
from tridata.ia.conversacion import ConversadorClaude
from tridata.nutricion import ObjetivosDia
from tridata.web import crear_app

AHORA = datetime(2026, 10, 24, 9, 42)
PRIMERO = "quiero hacer 10 km el 3 de diciembre en 45 minutos"


class Web(Mundo):
    """The app over the invented world of `test_conversaciones_contexto.Mundo`, with Claude doubled."""

    def __init__(self, tmp_path, *, con_transcriptor: bool = True) -> None:
        super().__init__(tmp_path)
        self.claude = ClaudeFalso()
        self.pdf = PdfFalso()
        self.ahora = AHORA
        self.transcriptor = MagicMock(spec=["transcribir"])
        self.transcriptor.transcribir.return_value = "¿y el jueves qué hago?"
        self.cliente = TestClient(crear_app(
            self.registro, MagicMock(), self.ajustes, hoy=lambda: self.ahora.date(), pesos=self.pesos,
            objetivos=ObjetivosDia(self.db), store=self.store, ahora=lambda: self.ahora,
            transcriptor=self.transcriptor if con_transcriptor else None,
            conversador=ConversadorClaude(cliente=self.claude), medicos_dir=tmp_path / "medicos",
            transcriptor_medico=TranscriptorPDF(cliente=self.pdf)))

    def sembrar(self) -> None:
        """Nothing more: the world of `Mundo` is already sown when it is built (kept for the readers)."""

    @staticmethod
    def eventos(r) -> list[dict]:
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/x-ndjson")
        return [json.loads(linea) for linea in r.text.splitlines() if linea.strip()]

    def _fin(self, r) -> dict:
        eventos = self.eventos(r)
        assert eventos[0]["tipo"] == "inicio"
        return {"eventos": eventos, **eventos[-1]}

    def nueva(self, texto: str, **extra) -> dict:
        return self._fin(self.cliente.post("/api/conversaciones", json={"texto": texto, **extra}))

    def enviar(self, ident: int, texto: str, **extra) -> dict:
        return self._fin(self.cliente.post(f"/api/conversaciones/{ident}/mensajes", json={"texto": texto, **extra}))

    def ver(self, ident: int) -> dict:
        r = self.cliente.get(f"/api/conversaciones/{ident}")
        assert r.status_code == 200, r.text
        return r.json()

    def lista(self) -> list[dict]:
        return self.cliente.get("/api/conversaciones").json()["conversaciones"]


def _web(tmp_path) -> Web:
    return Web(tmp_path)


# -- R1 · C-153: the list ------------------------------------------------------------------------------------------

def test_sin_conversaciones_la_lista_esta_vacia(tmp_path):  # R1
    web = _web(tmp_path)
    r = web.cliente.get("/api/conversaciones")
    assert r.status_code == 200
    assert r.json()["conversaciones"] == []


def test_la_lista_va_de_la_mas_reciente_a_la_mas_antigua(tmp_path):  # R1 · C-153
    web = _web(tmp_path)
    ids = []
    for hace, texto in ((3, "comidas de la semana"), (2, "entreno"), (1, "peso")):
        web.ahora = AHORA - timedelta(days=hace)
        web.claude.responder(f"Respuesta sobre {texto}.")
        ids.append(web.nueva(texto)["conversacion"]["id"])
    web.ahora = AHORA
    web.claude.responder("Seguimos con las comidas.")
    web.enviar(ids[0], "¿y hoy?")  # used now: it goes up
    lista = web.lista()
    assert [c["id"] for c in lista] == [ids[0], ids[2], ids[1]]
    assert lista[0]["titulo"] == "Comidas de la semana" and lista[0]["usada"].startswith("2026-10-24T09:42")
    assert lista[0]["ultima"] == "Seguimos con las comidas."


# -- R2 · R7 · C-154, C-161: a new conversation, its title and its answer --------------------------------------------

def test_nueva_conversacion_se_titula_sola_y_guarda_la_respuesta(tmp_path):  # R2, R7 · C-154, C-161
    web = _web(tmp_path)
    web.claude.responder("Vas bien: tu carrera de prueba al río salió a buen ritmo.",
                         ("poner_titulo", {"titulo": "10 km el 3 de diciembre"}))
    web.claude.responder(" Tu carga está subiendo.")
    fin = web.nueva(PRIMERO)
    tipos = [e["tipo"] for e in fin["eventos"]]
    assert tipos[0] == "inicio" and tipos[-1] == "fin" and tipos.count("trozo") >= 2  # it arrives in pieces
    assert "".join(e["texto"] for e in fin["eventos"] if e["tipo"] == "trozo") == fin["mensaje"]["texto"]
    assert fin["mensaje"]["texto"] == "Vas bien: tu carrera de prueba al río salió a buen ritmo. Tu carga está subiendo."
    assert fin["conversacion"]["titulo"] == "10 km el 3 de diciembre"
    assert fin["linea"].startswith("Ve tus comidas, peso, entrenos, sueño, pulso, HRV, analítica y prueba de esfuerzo")
    ident = fin["conversacion"]["id"]
    vista = web.ver(ident)
    assert [(m["quien"], m["texto"], m["sin_respuesta"]) for m in vista["mensajes"]] == [
        ("andres", PRIMERO, False), ("asistente", fin["mensaje"]["texto"], False)]
    assert all(m["contexto_hasta"] == "2026-10-24T09:42:00" for m in vista["mensajes"])
    assert vista["linea"].endswith("hasta hoy 9:42")
    # Andrés changes the title, and a later answer never puts it back
    r = web.cliente.post(f"/api/conversaciones/{ident}/titulo", json={"titulo": "Mi 10K"})
    assert r.status_code == 200 and r.json()["conversacion"]["titulo"] == "Mi 10K"
    web.claude.responder("Vale.", ("poner_titulo", {"titulo": "Otro título"}))
    web.claude.responder("")
    web.enviar(ident, "gracias")
    assert web.ver(ident)["conversacion"]["titulo"] == "Mi 10K"
    assert web.cliente.post(f"/api/conversaciones/{ident}/titulo", json={"titulo": "  "}).status_code == 400


def test_sin_titulo_de_claude_queda_el_provisional(tmp_path):  # R7
    web = _web(tmp_path)
    fin = web.nueva(PRIMERO)
    assert fin["conversacion"]["titulo"] == "Quiero hacer 10 km el 3 de…"


def test_cada_conversacion_manda_solo_lo_suyo_y_el_contexto_al_dia(tmp_path):  # R2 · C-157, S-41
    web = _web(tmp_path)
    web.claude.responder("Primera respuesta de la carrera.")
    a = web.nueva(PRIMERO)["conversacion"]["id"]
    web.claude.responder("Respuesta del peso.")
    web.nueva("peso")
    # days later, a race yesterday: the same conversation, what was said in it and the context of now
    web.ahora = AHORA + timedelta(days=7)
    from tridata.models import Activity
    web.store.save_activities([Activity(activity_id="ayer", activity_date=web.ahora.date() - timedelta(days=1),
                                        activity_type="running", name="Carrera de ayer de prueba",
                                        duration_seconds=1500, distance_meters=5000, avg_hr=160)])
    web.claude.responder("La carrera de ayer fue rápida.")
    web.enviar(a, "¿qué tal la carrera de ayer?")
    ultima = web.claude.peticiones[-1]
    assert ultima["messages"] == [{"role": "user", "content": PRIMERO},
                                  {"role": "assistant", "content": "Primera respuesta de la carrera."},
                                  {"role": "user", "content": "¿qué tal la carrera de ayer?"}]
    assert "Carrera de ayer de prueba" in ultima["system"][-1]["text"]
    vista = web.ver(a)
    assert len(vista["mensajes"]) == 4 and vista["linea"].endswith("hasta hoy 9:42")


def test_404_y_400(tmp_path):  # R2
    web = _web(tmp_path)
    assert web.cliente.post("/api/conversaciones", json={"texto": "  "}).status_code == 400
    assert web.cliente.post("/api/conversaciones/99/mensajes", json={"texto": "hola"}).status_code == 404
    assert web.cliente.get("/api/conversaciones/99").status_code == 404
    assert web.claude.peticiones == []


# -- R6 · C-158: without an answer, saved; «Reintentar» sends it without writing it again ------------------------

def test_sin_respuesta_queda_guardado_y_se_reintenta(tmp_path):  # R6 · C-158
    web = _web(tmp_path)
    web.claude.responder("Hola.")
    ident = web.nueva("hola")["conversacion"]["id"]
    web.claude.fallo = fallo_de_red()
    fin = web.enviar(ident, "¿llego en 45 minutos?")
    assert fin["tipo"] == "error" and fin["error"]
    assert fin["mensaje"]["sin_respuesta"] is True and fin["mensaje"]["texto"] == "¿llego en 45 minutos?"
    mensajes = web.ver(ident)["mensajes"]
    assert [(m["quien"], m["sin_respuesta"]) for m in mensajes] == [
        ("andres", False), ("asistente", False), ("andres", True)]
    # the unanswered one is not sent as history of the next ones
    web.claude.fallo = None
    web.claude.responder("Sí, llegas.")
    r = web.cliente.post(f"/api/conversaciones/{ident}/reintentar", json={"mensaje_id": mensajes[-1]["id"]})
    fin = web._fin(r)
    assert fin["tipo"] == "fin" and fin["mensaje"]["texto"] == "Sí, llegas."
    assert web.claude.peticiones[-1]["messages"][-1] == {"role": "user", "content": "¿llego en 45 minutos?"}
    mensajes = web.ver(ident)["mensajes"]
    assert [(m["quien"], m["texto"], m["sin_respuesta"]) for m in mensajes] == [
        ("andres", "hola", False), ("asistente", "Hola.", False),
        ("andres", "¿llego en 45 minutos?", False), ("asistente", "Sí, llegas.", False)]
    # only an unanswered message of Andrés in that conversation can be retried
    otro = mensajes[1]["id"]
    assert web.cliente.post(f"/api/conversaciones/{ident}/reintentar", json={"mensaje_id": otro}).status_code == 404


def test_una_conversacion_nueva_sin_respuesta_queda_guardada(tmp_path):  # R6
    web = _web(tmp_path)
    web.claude.fallo = fallo_de_red()
    fin = web.nueva(PRIMERO)
    assert fin["tipo"] == "error"
    assert [c["titulo"] for c in web.lista()] == ["Quiero hacer 10 km el 3 de…"]
    assert web.ver(fin["conversacion"]["id"])["mensajes"][0]["sin_respuesta"] is True


# -- R7 · C-159: delete, only with «confirmado», and nothing else ----------------------------------------------------

def test_borrar_pide_confirmar_y_no_toca_nada_mas(tmp_path):  # R7 · R-118, C-159
    web = _web(tmp_path)
    web.claude.responder("Del peso.")
    peso = web.nueva("Peso de noviembre")["conversacion"]["id"]
    web.claude.responder("De la carrera.")
    otra = web.nueva(PRIMERO)["conversacion"]["id"]
    comidas = web.registro.apuntes_del_dia(HOY)
    pesos = web.pesos.entre(HOY - timedelta(days=400), HOY)
    assert web.cliente.post(f"/api/conversaciones/{peso}/borrar", json={}).status_code == 400
    assert len(web.lista()) == 2
    r = web.cliente.post(f"/api/conversaciones/{peso}/borrar", json={"confirmado": True})
    assert r.status_code == 200
    assert [c["id"] for c in web.lista()] == [otra]
    assert web.cliente.get(f"/api/conversaciones/{peso}").status_code == 404
    assert len(web.ver(otra)["mensajes"]) == 2
    assert web.registro.apuntes_del_dia(HOY) == comidas
    assert web.pesos.entre(HOY - timedelta(days=400), HOY) == pesos
    assert web.cliente.post(f"/api/conversaciones/{peso}/borrar", json={"confirmado": True}).status_code == 404


# -- R8 · R-96, C-137: the card of the 021 inside a conversation -----------------------------------------------------

def test_una_propuesta_de_cambio_sale_como_tarjeta_y_nada_cambia_hasta_confirmar(tmp_path):  # R8 · R-96, C-137
    web = _web(tmp_path)
    web.claude.responder("Te dejo el cambio de la carrera.",
                         ("proponer_cambio", {"accion": "cambiar_carrera", "objeto": "10K de prueba",
                                              "fecha": "2026-12-04"}))
    web.claude.responder(" Confírmalo si te cuadra.")
    fin = web.nueva("cambia mi 10K al 4 de diciembre")
    (tarjeta,) = fin["mensaje"]["tarjetas"]
    assert tarjeta["tipo"] == "propuesta" and tarjeta["accion"] == "cambiar_carrera"
    assert tarjeta["texto"].startswith("Voy a")
    assert [c.fecha.isoformat() for c in web.ajustes.carreras()] == ["2026-12-03"]  # nothing changed
    resultado = web.claude.peticiones[1]["messages"][-1]["content"][0]["content"]
    assert "Voy a" in resultado  # Claude is told what the card says, and that Andrés decides
    ident, mensaje = fin["conversacion"]["id"], fin["mensaje"]["id"]
    assert web.ver(ident)["mensajes"][1]["tarjetas"] == [tarjeta]  # it stays with the answer
    r = web.cliente.post("/api/asistente/confirmar", json={"tarjeta": tarjeta})  # the confirmation of the 021
    assert r.status_code == 200, r.text
    assert [c.fecha.isoformat() for c in web.ajustes.carreras()] == ["2026-12-04"]
    r = web.cliente.post(f"/api/conversaciones/mensaje/{mensaje}/tarjetas", json={"tarjetas": []})
    assert r.status_code == 200
    assert web.ver(ident)["mensajes"][1]["tarjetas"] == []
    # the next request tells Claude what it proposed
    web.claude.responder("Hecho queda.")
    web.enviar(ident, "gracias")
    assert "Voy a" in web.claude.peticiones[-1]["messages"][1]["content"]


def test_un_cambio_que_no_se_entiende_no_es_tarjeta(tmp_path):  # R8 · G-40
    web = _web(tmp_path)
    web.claude.responder("Mira.", ("proponer_cambio", {"accion": "otra", "texto": "pintar la casa"}))
    web.claude.responder("")
    fin = web.nueva("pinta la casa")
    assert [t["tipo"] for t in fin["mensaje"]["tarjetas"]] == ["no"]
    web.claude.responder("Mira.", ("proponer_cambio", {"esto": "no es un cambio"}))
    web.claude.responder("")
    fin = web.enviar(fin["conversacion"]["id"], "otra")
    assert fin["tipo"] == "fin" and fin["mensaje"]["tarjetas"] == []


# -- R9: the spending cap --------------------------------------------------------------------------------------------

def test_al_llegar_al_tope_avisa_y_no_manda_hasta_el_mes_siguiente(tmp_path):  # R9
    web = _web(tmp_path)
    Conversaciones(web.db).apuntar_gasto(AHORA, "claude-sonnet-5-5", {"entrada": 0, "salida": 500_000,
                                                                     "cache_escritura": 0, "cache_lectura": 0},
                                         "mensaje")  # 5 $ this month
    r = web.cliente.post("/api/conversaciones", json={"texto": "hola"})
    assert r.status_code == 429 and "5 $" in r.json()["error"]
    assert web.claude.peticiones == [] and web.lista() == []
    assert web.cliente.get("/api/conversaciones").json()["tope"]["alcanzado"] is True
    assert web.cliente.get("/api/hoy").status_code == 200  # the rest of the app goes on
    web.ahora = datetime(2026, 11, 1, 8, 0)
    assert web.nueva("hola")["tipo"] == "fin"


def test_el_gasto_de_cada_respuesta_se_apunta(tmp_path):  # R9
    web = _web(tmp_path)
    web.nueva("hola")
    web.nueva("otra", pensar=True)
    assert [p["model"] for p in web.claude.peticiones] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    gastado = Conversaciones(web.db).gasto_del_mes(AHORA)
    assert gastado > 0
    tope = web.cliente.get("/api/conversaciones").json()["tope"]
    assert tope["dolares"] == 5.0 and tope["alcanzado"] is False


# -- R4: what it knows of the medical documents ---------------------------------------------------------------------

def test_ver_y_volver_a_leer_los_documentos_medicos(tmp_path):  # R4 · G-50
    web = _web(tmp_path)
    r = web.cliente.get("/api/conversaciones/medicos")
    assert r.status_code == 200 and web.pdf.peticiones == []  # looking never calls the API
    web.nueva("hola")  # the first message transcribes them, once
    assert len(web.pdf.peticiones) == 2
    web.nueva("otra")
    assert len(web.pdf.peticiones) == 2
    docs = web.cliente.get("/api/conversaciones/medicos").json()["documentos"]
    assert sorted(d["tipo"] for d in docs) == ["analitica", "prueba_esfuerzo"]
    assert any(v["valor"] == VALOR_INVENTADO for d in docs for v in d["valores"])
    r = web.cliente.post("/api/conversaciones/medicos/releer", json={})
    assert r.status_code == 200 and len(web.pdf.peticiones) == 4


# -- R11 · R-97, R-98: the card of Hoy ------------------------------------------------------------------------------

def test_hoy_ensena_el_principio_de_la_ultima_respuesta(tmp_path):  # R11
    web = _web(tmp_path)
    assert web.cliente.get("/api/hoy").json()["asistente"] is None
    web.claude.responder("Primera respuesta.")
    web.nueva("uno")
    web.claude.responder("Hoy rodaje suave de 40 minutos; el jueves, series. " * 6)
    ident = web.nueva(PRIMERO)["conversacion"]["id"]
    a = web.cliente.get("/api/hoy").json()["asistente"]
    assert a["conversacion_id"] == ident and a["titulo"] == "Quiero hacer 10 km el 3 de…"
    assert a["texto"].startswith("Hoy rodaje suave de 40 minutos") and len(a["texto"]) <= 141
    assert a["texto"].endswith("…")


# -- dictating in a conversation (S-37): the box of the chat --------------------------------------------------------

def test_dictar_en_una_conversacion_da_el_texto(tmp_path):  # R2 · S-37
    web = _web(tmp_path)
    r = web.cliente.post("/api/conversaciones/dictado", content=b"audio de prueba",
                         headers={"content-type": "audio/mp4"})
    assert r.status_code == 200 and r.json() == {"texto": "¿y el jueves qué hago?"}
    assert web.claude.peticiones == []  # it only transcribes: the text goes in the box
    sin = Web(tmp_path / "otro", con_transcriptor=False)
    r = sin.cliente.post("/api/conversaciones/dictado", content=b"audio", headers={"content-type": "audio/mp4"})
    assert r.status_code == 503


# -- R12 · G-50: the logs say ids, counts and tokens, never what is said nor a medical value -------------------------

def test_los_registros_no_llevan_lo_hablado_ni_valores_medicos(tmp_path, caplog):  # R12 · C-160
    caplog.set_level(logging.DEBUG)
    web = _web(tmp_path)
    web.claude.responder("Respuesta secreta de prueba.")
    ident = web.nueva("pregunta secreta de prueba")["conversacion"]["id"]
    web.claude.fallo = fallo_de_red()
    web.enviar(ident, "otra pregunta secreta")
    for prohibido in ("secreta", VALOR_INVENTADO, "58,31", "ferritina", "tostada de prueba", "71.9"):
        assert prohibido not in caplog.text, prohibido
    assert f"conversation {ident}" in caplog.text
