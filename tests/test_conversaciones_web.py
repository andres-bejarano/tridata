"""Unit 032 through the web (`web/conversaciones.py`): list, create, send with the answer as it arrives, retry,
rename, delete, the cards of change inside a conversation, the spending cap, the medical transcription and the card
of Hoy. Starlette's TestClient over a temporary SQLite; Claude is `ClaudeFalso` (no API, no cost).

Plans `preguntar-equipo` REC-20: R1 (C-153), R2 (C-154, C-157), R6 (C-158), R7 (C-159, C-161), R8 (R-96, C-137),
R9, R11 (R-97, R-98), R12 (C-160). Every value is invented.

Unit 041 (REC-23): a conversation is born with a role and the list shows it (R1 · C-178), «Pedir opinión a…» (R3 ·
C-180), `abrir_actividad` (R4 · C-181), the model of each role in Ajustes › El equipo with its price and the cap of
15 $ (R7, R10 · C-184).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from test_conversaciones import VALOR_INVENTADO, ClaudeFalso, PdfFalso, fallo_de_red
from test_conversaciones_contexto import HOY, Mundo
from tridata.conversaciones import Conversaciones, dolares
from tridata.conversaciones.medicos import TranscriptorPDF
from tridata.ia.conversacion import ConversadorClaude
from tridata.models import Activity, ActivityLap
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
    assert vista["linea"].endswith("hasta hoy 9:42 · Haiku")  # unit 041 (R7): and the model
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
    assert "Carrera de ayer de prueba" in ultima["system"][-2]["text"]  # unit 041: the role goes after it
    vista = web.ver(a)
    assert len(vista["mensajes"]) == 4 and vista["linea"].endswith("hasta hoy 9:42 · Haiku")


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
    Conversaciones(web.db).apuntar_gasto(AHORA, "claude-sonnet-5-5", {"entrada": 0, "salida": 1_500_000,
                                                                     "cache_escritura": 0, "cache_lectura": 0},
                                         "mensaje")  # 15 $ this month (unit 041, R10)
    r = web.cliente.post("/api/conversaciones", json={"texto": "hola"})
    assert r.status_code == 429 and "15 $" in r.json()["error"]
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
    assert tope["dolares"] == 15.0 and tope["alcanzado"] is False  # unit 041, R10


def _filas_de_gasto(web) -> list[tuple[str, int, int]]:
    import sqlite3

    with sqlite3.connect(web.db) as conn:
        return conn.execute("SELECT que, tokens_entrada, tokens_salida FROM gasto_asistente ORDER BY id").fetchall()


def test_el_gasto_se_apunta_por_llamada(tmp_path):  # bug 038 R2
    web = _web(tmp_path)
    web.claude.responder("Te lo dejo.", ("poner_titulo", {"titulo": "Carrera"}))
    web.claude.responder(" Listo.")
    web.nueva("hola")
    assert [que for que, _, _ in _filas_de_gasto(web) if que == "mensaje"] == ["mensaje", "mensaje"]


def test_un_turno_que_se_corta_tras_una_ronda_apunta_lo_cobrado(tmp_path):  # bug 038 R1
    web = _web(tmp_path)
    web.claude.responder("Te lo dejo.", ("poner_titulo", {"titulo": "Carrera"}))
    web.claude.cortar(entrada=5_000, salida=7)
    fin = web.nueva("hola")
    assert fin["tipo"] == "error" and fin["mensaje"]["sin_respuesta"]
    mensajes = [(e, s) for que, e, s in _filas_de_gasto(web) if que == "mensaje"]
    assert len(mensajes) == 2 and mensajes[-1] == (5_000, 7)
    assert Conversaciones(web.db).gasto_del_mes(AHORA) > 0


def test_un_rechazo_apunta_lo_cobrado(tmp_path):  # bug 038 R1
    web = _web(tmp_path)
    web.claude.rechazar()
    assert web.nueva("hola")["tipo"] == "error"
    assert len([1 for que, _, _ in _filas_de_gasto(web) if que == "mensaje"]) == 1


def test_sin_llamada_respondida_no_se_apunta_nada(tmp_path):  # bug 038 R1
    web = _web(tmp_path)
    web.claude.fallo = fallo_de_red()
    assert web.nueva("hola")["tipo"] == "error"
    assert [f for f in _filas_de_gasto(web) if f[0] == "mensaje"] == []


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


# -- unit 041 · R1 · C-178: a conversation is born with a role ----------------------------------------------------

def test_nueva_conversacion_con_papel_y_la_lista_lo_ensena(tmp_path):  # R1 · C-178
    web = _web(tmp_path)
    web.claude.responder("Desde el entreno: vas bien.")
    entrenador = web.nueva("¿cómo planteas la tirada?", papel="entrenador")
    assert entrenador["conversacion"]["papel"] == "entrenador"
    assert entrenador["mensaje"]["papel"] == "entrenador"
    de_siempre = web.nueva("hola")  # without a role: the Asistente, as before
    assert de_siempre["conversacion"]["papel"] == "asistente" and de_siempre["mensaje"]["papel"] == "asistente"
    assert {c["id"]: c["papel"] for c in web.lista()} == {entrenador["conversacion"]["id"]: "entrenador",
                                                          de_siempre["conversacion"]["id"]: "asistente"}
    r = web.cliente.post("/api/conversaciones", json={"texto": "hola", "papel": "masajista"})
    assert r.status_code == 400 and len(web.lista()) == 2  # nothing created


def test_cada_papel_responde_con_su_papel_y_su_modelo(tmp_path):  # R1, R7 · C-178, C-184
    web = _web(tmp_path)
    fin = web.nueva("¿y el gemelo?", papel="fisio")
    peticion = web.claude.peticiones[-1]
    assert peticion["model"] == "claude-sonnet-5-5" and "Fisio" in peticion["system"][-1]["text"]
    assert (fin["mensaje"]["papel"], fin["mensaje"]["modelo"]) == ("fisio", "claude-sonnet-5-5")
    assert fin["linea"].endswith(" · Sonnet")
    assert web.ver(fin["conversacion"]["id"])["linea"].endswith(" · Sonnet")
    web.enviar(fin["conversacion"]["id"], "¿y mañana?")
    assert "Fisio" in web.claude.peticiones[-1]["system"][-1]["text"]  # the conversation keeps its role


# -- R3 · C-180: «Pedir opinión a…» ---------------------------------------------------------------------------------

def test_pedir_opinion_hace_responder_a_otro_papel_viendo_lo_hablado(tmp_path):  # R3 · C-180
    web = _web(tmp_path)
    web.claude.responder("Tirada de 2h30 por bloques de prueba.")
    fin = web.nueva("¿cómo planteas la tirada larga de 2h30?", papel="entrenador")
    ident = fin["conversacion"]["id"]
    web.claude.responder("Con la presión del gemelo de prueba, si no va a más, puedes hacerla.")
    r = web.cliente.post(f"/api/conversaciones/{ident}/opinion", json={"papel": "fisio"})
    eventos = web.eventos(r)
    assert eventos[0]["tipo"] == "inicio" and eventos[-1]["tipo"] == "fin", eventos[-1]
    opinion = eventos[-1]["mensaje"]
    assert (opinion["quien"], opinion["papel"], opinion["modelo"]) == ("asistente", "fisio", "claude-sonnet-5-5")
    assert opinion["texto"].startswith("Con la presión del gemelo de prueba")
    peticion = web.claude.peticiones[-1]
    assert "Fisio" in peticion["system"][-1]["text"]
    dicho = json.dumps(peticion["messages"], ensure_ascii=False)
    assert "¿cómo planteas la tirada larga de 2h30?" in dicho and "Tirada de 2h30 por bloques de prueba." in dicho
    assert "Entrenador" in dicho  # it knows who said what
    assert peticion["messages"][-1]["role"] == "user" and "Fisio" in json.dumps(peticion["messages"][-1],
                                                                                 ensure_ascii=False)
    vista = web.ver(ident)
    assert [(m["quien"], m["papel"]) for m in vista["mensajes"]] == [
        ("andres", None), ("asistente", "entrenador"), ("asistente", "fisio")]
    assert vista["conversacion"]["papel"] == "entrenador"  # still the coach's conversation
    web.enviar(ident, "vale, ¿y el domingo?")
    siguiente = web.claude.peticiones[-1]
    assert "Entrenador" in siguiente["system"][-1]["text"]
    roles = [m["role"] for m in siguiente["messages"]]
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False))  # user and assistant take turns


def test_pedir_opinion_mal_pedida_no_llama(tmp_path):  # R3
    web = _web(tmp_path)
    vacia = web.cliente.post("/api/conversaciones/999/opinion", json={"papel": "fisio"})
    assert vacia.status_code == 404
    fin = web.nueva("hola", papel="entrenador")
    antes = len(web.claude.peticiones)
    r = web.cliente.post(f"/api/conversaciones/{fin['conversacion']['id']}/opinion", json={"papel": "masajista"})
    assert r.status_code == 400 and len(web.claude.peticiones) == antes


# -- R4 · C-181: a role opens an activity ----------------------------------------------------------------------------

def _series(web) -> None:
    """An invented session of 4 repetitions on Thursday 22-10, with its laps (pace, HR, cadence, climb)."""
    web.store.save_activities([Activity(activity_id="series-1", activity_date=AHORA.date() - timedelta(days=2),
                                        activity_type="running", name="Series de prueba", duration_seconds=3000,
                                        distance_meters=10000, avg_hr=158, avg_cadence=176)])
    web.store.save_activity_laps([
        ActivityLap("series-1", i, distance_meters=1000.0, duration_seconds=d, avg_hr=hr, avg_cadence=c,
                    elevation_gain_m=e)
        for i, (d, hr, c, e) in enumerate([(244, 161, 181, 3), (241, 165, 183, 2), (239, 168, 184, 4),
                                           (236, 172, 186, 1)], start=1)])


def test_un_papel_abre_una_actividad_por_su_fecha_o_su_id(tmp_path):  # R4 · C-181, S-54
    web = _web(tmp_path)
    _series(web)
    web.claude.responder("Abro el jueves.", ("abrir_actividad", {"fecha": "2026-10-22", "id": ""}))
    web.claude.responder(" La cuarta serie fue la más rápida, a 3:56 con 172 de pulso.")
    fin = web.nueva("¿cómo me salieron las series del jueves?", papel="entrenador")
    assert fin["tipo"] == "fin" and "3:56" in fin["mensaje"]["texto"]
    resultado = web.claude.peticiones[1]["messages"][-1]["content"][0]
    assert resultado["type"] == "tool_result"
    datos = json.loads(resultado["content"])
    (actividad,) = datos["actividades"]
    assert actividad["actividad"]["nombre"] == "Series de prueba"
    vueltas = actividad["vueltas"]
    assert len(vueltas) == 4
    assert [v["ritmo_s_km"] for v in vueltas] == [244, 241, 239, 236]
    assert [v["pulso_medio"] for v in vueltas] == [161, 165, 168, 172]
    assert [v["cadencia"] for v in vueltas] == [181, 183, 184, 186]
    assert [v["desnivel_m"] for v in vueltas] == [3, 2, 4, 1]
    # by its id too; an id or a date without anything says so
    web.claude.responder("", ("abrir_actividad", {"fecha": "", "id": "series-1"}),
                         ("abrir_actividad", {"fecha": "2026-10-01", "id": ""}))
    web.claude.responder("Vale.")
    web.enviar(fin["conversacion"]["id"], "¿y por su id?")
    por_id, nada = web.claude.peticiones[-1]["messages"][-1]["content"]
    assert len(json.loads(por_id["content"])["actividades"][0]["vueltas"]) == 4
    assert json.loads(nada["content"])["actividades"] == []
    # the laps never go in the fixed part
    fija = "\n".join(b["text"] for b in web.claude.peticiones[0]["system"])
    assert '"cadencia"' not in fija and '"vueltas"' not in fija


# -- R7, R10 · C-184: the model of each role, its price and the cap ------------------------------------------------

def test_el_equipo_en_ajustes_elige_el_modelo_de_cada_papel(tmp_path):  # R7 · C-184, G-56
    web = _web(tmp_path)
    equipo = web.cliente.get("/api/equipo").json()
    assert [(p["id"], p["nombre"], p["modelo"]) for p in equipo["papeles"]] == [
        ("entrenador", "Entrenador", "claude-sonnet-5-5"), ("fisio", "Fisio", "claude-sonnet-5-5"),
        ("nutricionista", "Nutricionista", "claude-sonnet-5-5"), ("asistente", "Asistente", "claude-haiku-5-5")]
    assert {m["id"]: m["nombre"] for m in equipo["modelos"]} == {"claude-haiku-5-5": "Haiku",
                                                                 "claude-sonnet-5-5": "Sonnet"}
    assert equipo["tope"]["dolares"] == 15.0
    r = web.cliente.put("/api/equipo", json={"papel": "entrenador", "modelo": "claude-haiku-5-5"})
    assert r.status_code == 200
    assert {p["id"]: p["modelo"] for p in r.json()["papeles"]}["entrenador"] == "claude-haiku-5-5"
    for malo in ({"papel": "entrenador", "modelo": "gpt-de-prueba"}, {"papel": "masajista", "modelo": "claude-haiku-5-5"},
                 {}):
        r = web.cliente.put("/api/equipo", json=malo)
        assert r.status_code == 400, malo
    assert {p["id"]: p["modelo"] for p in web.cliente.get("/api/equipo").json()["papeles"]}["entrenador"] == \
        "claude-haiku-5-5"
    # the coach answers with Haiku now, and it says so; its cost counts for the cap at Haiku's price
    fin = web.nueva("¿cómo planteas la tirada?", papel="entrenador")
    assert web.claude.peticiones[-1]["model"] == "claude-haiku-5-5"
    assert fin["mensaje"]["modelo"] == "claude-haiku-5-5" and fin["linea"].endswith(" · Haiku")
    import sqlite3

    with sqlite3.connect(web.db) as conn:
        filas = conn.execute("SELECT modelo, dolares FROM gasto_asistente WHERE que = 'mensaje'").fetchall()
    assert [f[0] for f in filas] == ["claude-haiku-5-5"]  # the medical transcription goes apart («transcripcion»)
    assert filas[0][1] == pytest.approx(dolares("claude-haiku-5-5", fin["mensaje"]["uso"]))


# -- unit 045 (REC-25): «Adjuntar ruta», its profile in the message and the strategy by stretches -------------------

from pathlib import Path  # noqa: E402

GPX_INVENTADO = (Path(__file__).parent / "fixtures" / "ruta-inventada.gpx").read_text()
COORDENADAS = ("10.5", "10,5", "-30.2", "-30,2", "30.2", "30,2")  # of the invented GPX: never towards the model


class GarminDeRutas:
    """Garmin doubled where the app connects to it (`plan.reloj.cliente_garmin`): two saved routes and the GPX of an
    activity, all invented; `falla` makes every call fail as the unofficial way sometimes does."""

    def __init__(self) -> None:
        from tridata.plan.rutas import puntos_gpx

        self.llamadas: list[tuple] = []
        self.falla: Exception | None = None
        self._puntos = [{"distancia_m": x.distancia_m, "altitud_m": x.altitud_m, "lat": x.lat, "lon": x.lon}
                        for x in puntos_gpx(GPX_INVENTADO)]

    def _llamada(self, *que):
        self.llamadas.append(que)
        if self.falla is not None:
            raise self.falla

    def rutas_guardadas(self) -> list[dict]:
        self._llamada("rutas_guardadas")
        return [{"id": 11, "nombre": "Ruta de prueba", "distancia_m": 20000.0, "sube_m": 118.0, "baja_m": 117.0},
                {"id": 12, "nombre": "Ruta llana de prueba", "distancia_m": 10000.0, "sube_m": None, "baja_m": None}]

    def ruta(self, ident: int) -> list[dict]:
        self._llamada("ruta", ident)
        if ident == 12:  # a course without altitude
            return [{**p, "altitud_m": None} for p in self._puntos]
        return self._puntos

    def gpx_actividad(self, ident: str) -> bytes:
        self._llamada("gpx_actividad", ident)
        return GPX_INVENTADO.encode()


@pytest.fixture
def garmin(monkeypatch):
    from tridata.plan import reloj

    doble = GarminDeRutas()
    monkeypatch.setattr(reloj, "cliente_garmin", lambda entorno=None: doble)
    return doble


def _con_el_entrenador(web: Web) -> int:
    web.claude.responder("Cuéntame qué carrera preparamos.")
    return web.nueva("quiero preparar una carrera", papel="entrenador")["conversacion"]["id"]


def test_adjuntar_ruta_ensena_las_de_garmin_y_las_actividades(tmp_path, garmin):  # R1 · C-193
    web = _web(tmp_path)
    r = web.cliente.get("/api/rutas")
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["garmin"] == [{"id": 11, "nombre": "Ruta de prueba", "distancia_km": 20.0, "sube_m": 118},
                               {"id": 12, "nombre": "Ruta llana de prueba", "distancia_km": 10.0, "sube_m": None}]
    assert [(a["id"], a["nombre"], a["fecha"], a["distancia_km"]) for a in datos["actividades"]] == [
        ("a1", "Carrera de prueba al río", "2026-10-21", 10.0)]  # what was run (not the bike)
    assert datos["aviso"] is None


def test_una_ruta_de_garmin_da_su_perfil_y_queda_en_la_conversacion(tmp_path, garmin):  # R1, R2 · C-193, C-194
    web = _web(tmp_path)
    ident = _con_el_entrenador(web)
    r = web.cliente.post(f"/api/conversaciones/{ident}/ruta", json={"garmin": 11})
    assert r.status_code == 200, r.text
    ruta = r.json()["ruta"]
    assert ruta["nombre"] == "Ruta de prueba" and ruta["fuente"] == "garmin"
    assert [t["terreno"] for t in ruta["tramos"]].count("subida") == 2
    assert ("ruta", 11) in garmin.llamadas
    assert web.ver(ident)["ruta"] == ruta  # kept with the conversation, drawn when it is opened again
    assert "ruta adjunta: Ruta de prueba" in web.ver(ident)["linea"]
    # an activity already run, by its GPX
    r = web.cliente.post(f"/api/conversaciones/{ident}/ruta", json={"actividad": "a1"})
    assert r.status_code == 200, r.text
    assert r.json()["ruta"]["nombre"] == "Carrera de prueba al río" and r.json()["ruta"]["fuente"] == "actividad"
    assert ("gpx_actividad", "a1") in garmin.llamadas
    assert web.ver(ident)["ruta"]["nombre"] == "Carrera de prueba al río"  # the last one attached


def test_sin_altitud_o_sin_garmin_lo_dice_y_ofrece_el_gpx(tmp_path, garmin):  # R3 · C-195
    from tridata.garmin_client import GarminRutaError

    web = _web(tmp_path)
    ident = _con_el_entrenador(web)
    r = web.cliente.post(f"/api/conversaciones/{ident}/ruta", json={"garmin": 12})
    assert r.status_code == 422 and "altitud" in r.json()["error"] and r.json()["subir_gpx"] is True
    sin_ele = GPX_INVENTADO.replace("<ele>", "<!--").replace("</ele>", "-->")
    r = web.cliente.post(f"/api/conversaciones/{ident}/ruta", json={"gpx": sin_ele, "nombre": "sin-altitud.gpx"})
    assert r.status_code == 422 and "altitud" in r.json()["error"]
    assert web.ver(ident)["ruta"] is None  # no profile invented
    garmin.falla = GarminRutaError("no answer")
    lista = web.cliente.get("/api/rutas").json()
    assert lista["garmin"] == [] and "Garmin" in lista["aviso"] and "GPX" in lista["aviso"]
    assert lista["actividades"]  # what is on the PC is still there
    r = web.cliente.post(f"/api/conversaciones/{ident}/ruta", json={"garmin": 11})
    assert r.status_code == 502 and "GPX" in r.json()["error"] and r.json()["subir_gpx"] is True
    assert web.ver(ident)["ruta"] is None
    r = web.cliente.post(f"/api/conversaciones/{ident}/ruta", json={"gpx": "esto no es un gpx"})
    assert r.status_code == 422
    assert web.cliente.post(f"/api/conversaciones/{ident}/ruta", json={}).status_code == 400
    assert web.cliente.post("/api/conversaciones/999/ruta", json={"gpx": GPX_INVENTADO}).status_code == 404


def test_al_modelo_va_el_perfil_en_el_mensaje_y_la_parte_fija_no_cambia(tmp_path, garmin):  # R4 · C-196, G-57
    web = _web(tmp_path)
    ident = _con_el_entrenador(web)
    r = web.cliente.post(f"/api/conversaciones/{ident}/ruta", json={"gpx": GPX_INVENTADO, "nombre": "mi-ruta.gpx"})
    assert r.status_code == 200, r.text
    assert r.json()["ruta"]["nombre"] == "Ruta inventada"  # the name inside the GPX
    web.claude.responder("Vamos con ello.")
    web.enviar(ident, "prepárame la carrera para 1:25")
    sin_ruta, con_ruta = web.claude.peticiones[0], web.claude.peticiones[-1]
    assert con_ruta["system"] == sin_ruta["system"] and con_ruta["tools"] == sin_ruta["tools"]  # the cache holds
    mensaje = con_ruta["messages"][-1]
    assert mensaje["role"] == "user" and isinstance(mensaje["content"], str)
    assert "Ruta adjunta" in mensaje["content"] and "subida" in mensaje["content"] and "cada 100 m" in mensaje["content"]
    assert mensaje["content"].rstrip().endswith("prepárame la carrera para 1:25")  # and what Andrés said
    lo_dicho = json.dumps(con_ruta["messages"], ensure_ascii=False)
    for c in COORDENADAS:  # not one coordinate in what goes with the messages…
        assert c not in lo_dicho, c
    todo = json.dumps(con_ruta, ensure_ascii=False)
    for c in ("10.500", "10,500", "-30.2", "-30,2"):  # …nor anywhere in the request (the data of the context carry
        assert c not in todo, c                       # an invented tsb of -10.5: the loose «10.5» is not looked for)
    assert "Ruta adjunta" not in json.dumps(con_ruta["system"], ensure_ascii=False)
    # what stays of Andrés is what he said; the line says the route
    assert web.ver(ident)["mensajes"][-2]["texto"] == "prepárame la carrera para 1:25"
    assert "ruta adjunta: Ruta inventada" in web.ver(ident)["linea"]


def test_una_conversacion_nueva_puede_empezar_por_la_ruta(tmp_path, garmin):  # R1
    web = _web(tmp_path)
    r = web.cliente.post("/api/conversaciones/ruta", json={"papel": "entrenador", "gpx": GPX_INVENTADO})
    assert r.status_code == 200, r.text
    conversacion = r.json()["conversacion"]
    assert conversacion["papel"] == "entrenador" and "Ruta inventada" in conversacion["titulo"]
    assert web.ver(conversacion["id"])["ruta"]["nombre"] == "Ruta inventada"
    assert web.claude.peticiones == []  # attaching never calls the API
    r = web.cliente.post("/api/conversaciones/ruta", json={"papel": "nadie", "gpx": GPX_INVENTADO})
    assert r.status_code == 400


def test_la_estrategia_por_tramos_se_pide_y_se_guarda_en_la_carrera_al_confirmar(tmp_path, garmin):  # R5, R6
    from test_plan import plan_de_prueba

    from tridata.plan import Planes

    web = _web(tmp_path)
    planes = Planes(web.db)
    plan_id = planes.crear(plan_de_prueba(), AHORA)
    ident = _con_el_entrenador(web)
    assert web.cliente.post(f"/api/conversaciones/{ident}/ruta", json={"gpx": GPX_INVENTADO}).status_code == 200
    tabla = ("- km 0-4 · llano · 4:15/km · paso 17:00\n- km 4-6,5 · subida · por esfuerzo · paso 28:40\n"
             "- km 6,5-20 · bajada y llano · 4:10/km · paso 1:24:55\nGeles: km 6,5.")
    web.claude.responder(tabla, ("proponer_cambio", {
        "accion": "guardar_estrategia", "objeto": "Maratón de ejemplo",
        "texto": "km 0-4 · llano · 4:15/km · paso 17:00 | km 4-6,5 · subida · por esfuerzo · paso 28:40 | "
                 "km 6,5-20 · bajada y llano · 4:10/km · paso 1:24:55 | gel: km 6,5"}))
    web.claude.responder(" ¿La guardo en tu plan?")
    fin = web.enviar(ident, "prepárame la carrera para 1:25")
    pedido = web.claude.peticiones[-2]["messages"][-1]["content"]
    assert "estrategia" in pedido and "tramo" in pedido and "paso" in pedido and "gel" in pedido  # R5: asked for
    assert "guardar_estrategia" in pedido
    (tarjeta,) = fin["mensaje"]["tarjetas"]
    assert tarjeta["texto"] == "Voy a guardar en el plan: estrategia de Maratón de ejemplo (3 tramos, 1 gel)"
    assert planes.estrategia(plan_id, "Maratón de ejemplo") == []  # G-55: not until it is confirmed
    r = web.cliente.post("/api/asistente/confirmar", json={"tarjeta": tarjeta})
    assert r.status_code == 200, r.text
    assert planes.estrategia(plan_id, "Maratón de ejemplo") == [
        "km 0-4 · llano · 4:15/km · paso 17:00", "km 4-6,5 · subida · por esfuerzo · paso 28:40",
        "km 6,5-20 · bajada y llano · 4:10/km · paso 1:24:55", "gel: km 6,5"]
