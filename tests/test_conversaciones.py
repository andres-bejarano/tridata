"""Unit 032 (REC-20 of `preguntar-equipo`): the pieces of the Asistente's conversations, without the web.

- the request to the API (`ia/conversacion.py`): the fixed part first, with its cache mark and no hour; then what was
  said in THAT conversation and the new message; the model Andrés chose (Haiku, Sonnet with «pensar más») (R2, R10);
- the answer as it arrives (streaming), its token use, the tools (a card of change, the title) and the errors (R6, R8);
- the spending cap of 15 $ a month (R9; 5 $ until unit 041, R10);
- the medical transcription (`conversaciones/medicos.py`): each PDF once, again only when it changes, nothing in the
  logs (R4, R12).

Every call to the API goes to a double (`ClaudeFalso`, `PdfFalso`): no test spends. Every PDF and every value is
invented (a PDF written by the test). `ClaudeFalso` is reused by `test_conversaciones_web.py` and
`test_conversaciones_contexto.py`.
"""
from __future__ import annotations

import base64
import copy
import json
import logging
from datetime import datetime
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from tridata.conversaciones import TOPE_DOLARES_MES, Conversaciones, dolares, titulo_provisional
from tridata.conversaciones.medicos import Medicos, TranscripcionMedica, TranscriptorPDF, ValorMedico
from tridata.ia.conversacion import (
    MODELO_PENSAR, MODELO_POR_DEFECTO, ConversadorClaude, ErrorConversacion, Respuesta, construir_peticion,
)

AHORA = datetime(2026, 10, 24, 9, 42)
BLOQUES = ("Instrucciones fijas de prueba.", "Transcripción médica inventada: ferritina de prueba 321,7.",
           '{"dias": [{"fecha": "2026-10-24", "peso": 71.9}]}')
VALOR_INVENTADO = "321,7"


# -- the doubles of the API ----------------------------------------------------------------------------------------

class _Flujo:
    """What `messages.stream(...)` gives: a context manager with `text_stream` and `get_final_message()`."""

    def __init__(self, trozos: list[str], final: SimpleNamespace, corte: Exception | None = None) -> None:
        self._trozos, self._final, self._corte = trozos, final, corte
        self.current_message_snapshot = final  # what the SDK has seen so far (bug 038: the use of a cut round)

    def __enter__(self) -> _Flujo:
        return self

    def __exit__(self, *exc) -> bool:
        return False

    @property
    def text_stream(self):
        yield from self._trozos[:1] if self._corte else self._trozos
        if self._corte is not None:
            raise self._corte

    def get_final_message(self) -> SimpleNamespace:
        return self._final


class ClaudeFalso:
    """A Claude client double for `messages.stream`. It keeps every request (`peticiones`).

    `responder(texto, *herramientas)` queues one round: its text and the tools it calls (`(nombre, entrada)`); a
    round with tools ends with `stop_reason="tool_use"`. With nothing queued it answers «Respuesta de prueba.».
    `fallo` (an exception) is raised instead of answering. `rechazar()` queues a round that ends in `refusal`;
    `cortar(entrada, salida)` one whose connection breaks after the first piece, with that partial use seen. Like the real cache, a request whose `tools` + `system`
    up to its last `cache_control` + model are byte-identical to an earlier one is read from the cache.
    """

    def __init__(self) -> None:
        self.peticiones: list[dict] = []
        self.rondas: list[tuple[str, list[tuple[str, dict]]]] = []
        self.fallo: Exception | None = None
        self._prefijos: set[str] = set()
        self.messages = self

    def responder(self, texto: str, *herramientas: tuple[str, dict]) -> None:
        self.rondas.append((texto, list(herramientas)))

    def rechazar(self) -> None:
        self.rondas.append(("No puedo con eso.", [("__refusal__", {})]))

    def cortar(self, entrada: int, salida: int) -> None:
        self.rondas.append(("Empiezo a responder y se corta.", [("__corte__", {"entrada": entrada, "salida": salida})]))

    def stream(self, **peticion):
        self.peticiones.append(copy.deepcopy(peticion))
        if self.fallo is not None:
            raise self.fallo
        texto, herramientas = self.rondas.pop(0) if self.rondas else ("Respuesta de prueba.", [])
        especial = herramientas[0][0] if herramientas and herramientas[0][0].startswith("__") else None
        if especial == "__corte__":
            parcial = herramientas[0][1]
            uso = SimpleNamespace(input_tokens=parcial["entrada"], output_tokens=parcial["salida"],
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0)
            final = SimpleNamespace(content=[], stop_reason=None, usage=uso)
            return _Flujo([texto], final, corte=fallo_de_red())
        if especial == "__refusal__":
            herramientas = []
        system = peticion.get("system", [])
        marcas = [i for i, b in enumerate(system) if isinstance(b, dict) and b.get("cache_control")]
        con_marca = bool(marcas)
        # like the real cache: the prefix up to the LAST mark (unit 041: the role goes after it and is not cached)
        prefijo = json.dumps([peticion.get("tools"), system[:marcas[-1] + 1] if marcas else system, peticion["model"]],
                             sort_keys=True, ensure_ascii=False)
        leido = con_marca and prefijo in self._prefijos
        if con_marca:
            self._prefijos.add(prefijo)
        n = len(prefijo) // 4
        uso = SimpleNamespace(input_tokens=40, output_tokens=len(texto) // 4 + 1,
                              cache_creation_input_tokens=0 if leido or not con_marca else n,
                              cache_read_input_tokens=n if leido else 0)
        contenido = ([SimpleNamespace(type="text", text=texto)] if texto else []) + [
            SimpleNamespace(type="tool_use", id=f"herr-{len(self.peticiones)}-{i}", name=nombre, input=entrada)
            for i, (nombre, entrada) in enumerate(herramientas)]
        mitad = len(texto) // 2
        trozos = [t for t in (texto[:mitad], texto[mitad:]) if t]
        parada = "refusal" if especial == "__refusal__" else "tool_use" if herramientas else "end_turn"
        final = SimpleNamespace(content=contenido, stop_reason=parada, usage=uso)
        return _Flujo(trozos, final)


def _peticion_http() -> httpx2.Request:
    return httpx2.Request("POST", "https://example.invalid/v1/messages")


def fallo_de_red() -> Exception:
    return anthropic.APIConnectionError(request=_peticion_http())


class PdfFalso:
    """A Claude client double for `messages.parse` (the transcription of a PDF): one invented answer per call."""

    def __init__(self) -> None:
        self.peticiones: list[dict] = []
        self.fallo: Exception | None = None
        self.messages = self

    def parse(self, **peticion):
        self.peticiones.append(peticion)
        if self.fallo is not None:
            raise self.fallo
        datos = base64.b64decode(peticion["messages"][0]["content"][0]["source"]["data"])
        es_prueba = b"ergometria" in datos
        salida = TranscripcionMedica(
            tipo="prueba_esfuerzo" if es_prueba else "analitica",
            titulo="Prueba de esfuerzo de prueba" if es_prueba else "Analítica de prueba",
            fecha="2025-11-12" if es_prueba else "2026-03-02",
            valores=[ValorMedico(prueba="ferritina de prueba", valor=VALOR_INVENTADO, unidad="ng/mL",
                                 rango="30-400", fecha=None)] if not es_prueba else
                    [ValorMedico(prueba="consumo de oxígeno de prueba", valor="58,31", unidad="ml/kg/min",
                                 rango=None, fecha=None)],
            notas=["Nota inventada del informe."])
        uso = SimpleNamespace(input_tokens=3000, output_tokens=400, cache_creation_input_tokens=0,
                              cache_read_input_tokens=0)
        return SimpleNamespace(parsed_output=salida, stop_reason="end_turn", usage=uso)


def pdf_sintetico(texto: str) -> bytes:
    """A tiny valid one-page PDF written here: it carries only `texto` (invented)."""
    flujo = f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET".encode()
    objetos = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(flujo) + flujo + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    salida, posiciones = bytearray(b"%PDF-1.4\n"), []
    for i, cuerpo in enumerate(objetos, start=1):
        posiciones.append(len(salida))
        salida += b"%d 0 obj\n" % i + cuerpo + b"\nendobj\n"
    xref = len(salida)
    salida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    salida += b"".join(b"%010d 00000 n \n" % p for p in posiciones)
    salida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, xref)
    return bytes(salida)


def _todo(generador) -> tuple[list[str], Respuesta]:
    eventos = list(generador)
    assert isinstance(eventos[-1], Respuesta)
    assert all(isinstance(e, str) for e in eventos[:-1])
    return eventos[:-1], eventos[-1]


# -- R10 · P3: the request ------------------------------------------------------------------------------------------

def test_la_parte_fija_va_primero_con_su_marca_de_cache_y_sin_hora():  # R10 · P3
    historial = [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "hola, Andrés"}]
    p = construir_peticion(BLOQUES, historial, "¿cómo voy?", MODELO_POR_DEFECTO)
    assert p["model"] == "claude-haiku-5-5"
    assert [b["text"] for b in p["system"]] == list(BLOQUES)
    assert p["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in b for b in p["system"][:-1])  # one mark, at the end of the fixed part
    assert p["messages"] == historial + [{"role": "user", "content": "¿cómo voy?"}]
    assert {h["name"] for h in p["tools"]} == {"proponer_cambio", "poner_titulo", "abrir_actividad"}  # 041: + R4
    # the same fixed part and tools whatever is said: the cache is by exact prefix
    otra = construir_peticion(BLOQUES, [], "otra cosa", MODELO_POR_DEFECTO)
    assert (otra["system"], otra["tools"]) == (p["system"], p["tools"])
    assert "9:42" not in json.dumps(p, ensure_ascii=False)


def test_modelo_por_defecto_pensar_mas_y_variable(monkeypatch):  # R9 · decisión de Andrés
    monkeypatch.delenv("TRIDATA_MODELO_ASISTENTE", raising=False)
    assert MODELO_POR_DEFECTO == "claude-haiku-5-5" and MODELO_PENSAR == "claude-sonnet-5-5"
    claude = ClaudeFalso()
    conversador = ConversadorClaude(cliente=claude)
    _, r = _todo(conversador.responder(BLOQUES, [], "hola"))
    assert r.modelo == claude.peticiones[-1]["model"] == "claude-haiku-5-5"
    _, r = _todo(conversador.responder(BLOQUES, [], "hola", pensar=True))
    assert r.modelo == claude.peticiones[-1]["model"] == "claude-sonnet-5-5"
    monkeypatch.setenv("TRIDATA_MODELO_ASISTENTE", "claude-modelo-de-prueba")
    _, r = _todo(ConversadorClaude(cliente=claude).responder(BLOQUES, [], "hola"))
    assert claude.peticiones[-1]["model"] == "claude-modelo-de-prueba"


# -- R2 · Q-15: the answer as it arrives ---------------------------------------------------------------------------

def test_la_respuesta_llega_a_trozos_y_trae_su_uso():  # R2, R9
    claude = ClaudeFalso()
    claude.responder("Vas bien encaminado para el 10K de prueba.")
    trozos, r = _todo(ConversadorClaude(cliente=claude).responder(BLOQUES, [], "¿cómo voy?"))
    assert len(trozos) == 2 and "".join(trozos) == r.texto == "Vas bien encaminado para el 10K de prueba."
    assert set(r.uso) == {"entrada", "salida", "cache_escritura", "cache_lectura"}
    assert r.uso["entrada"] == 40 and r.uso["cache_escritura"] > 0 and r.uso["cache_lectura"] == 0


def test_dos_mensajes_seguidos_sin_cambios_leen_la_cache():  # R10 · P3
    claude = ClaudeFalso()
    conversador = ConversadorClaude(cliente=claude)
    _, primero = _todo(conversador.responder(BLOQUES, [], "¿cómo voy?"))
    _, segundo = _todo(conversador.responder(BLOQUES, [{"role": "user", "content": "¿cómo voy?"},
                                                       {"role": "assistant", "content": primero.texto}], "¿y mañana?"))
    assert primero.uso["cache_lectura"] == 0
    assert segundo.uso["cache_lectura"] > 0 and segundo.uso["cache_escritura"] == 0
    cambiado = (*BLOQUES[:2], '{"dias": [{"fecha": "2026-10-24", "peso": 71.8}]}')  # a datum changed: new cache
    _, tercero = _todo(conversador.responder(cambiado, [], "¿y ahora?"))
    assert tercero.uso["cache_lectura"] == 0


# -- R8: the tools (a card of change, the title) ---------------------------------------------------------------------

def test_las_herramientas_vuelven_con_su_resultado_y_la_respuesta_sigue():  # R8, R7
    claude = ClaudeFalso()
    cambio = {"accion": "cambiar_carrera", "objeto": "10K de prueba", "fecha": "2026-12-03"}
    claude.responder("Te preparo el cambio.", ("poner_titulo", {"titulo": "10 km el 3 de diciembre"}),
                     ("proponer_cambio", cambio))
    claude.responder(" Confírmalo abajo.")
    usadas = []

    def al_usar(nombre: str, entrada: dict) -> str:
        usadas.append((nombre, entrada))
        return f"hecho {nombre}"

    _, r = _todo(ConversadorClaude(cliente=claude).responder(BLOQUES, [], "apúntame esa carrera", al_usar=al_usar))
    assert usadas == [("poner_titulo", {"titulo": "10 km el 3 de diciembre"}), ("proponer_cambio", cambio)]
    assert r.texto == "Te preparo el cambio. Confírmalo abajo."
    assert len(claude.peticiones) == 2
    segunda = claude.peticiones[1]
    assert segunda["system"] == claude.peticiones[0]["system"]  # the same prefix: the cache keeps serving
    asistente, resultados = segunda["messages"][-2], segunda["messages"][-1]
    assert asistente["role"] == "assistant" and resultados["role"] == "user"
    assert [(b["type"], b["content"]) for b in resultados["content"]] == [
        ("tool_result", "hecho poner_titulo"), ("tool_result", "hecho proponer_cambio")]
    assert r.uso["entrada"] == 80  # the use of both rounds


@pytest.mark.parametrize("fallo", [
    fallo_de_red,
    lambda: anthropic.RateLimitError("límite", response=httpx2.Response(429, request=_peticion_http()), body=None),
    lambda: anthropic.AuthenticationError("sin clave", response=httpx2.Response(401, request=_peticion_http()),
                                          body=None),
    lambda: anthropic.InternalServerError("caído", response=httpx2.Response(500, request=_peticion_http()),
                                          body=None),
], ids=["red", "limite", "clave", "500"])
def test_un_fallo_de_la_api_es_error_conversacion(fallo):  # R6
    claude = ClaudeFalso()
    claude.fallo = fallo()
    with pytest.raises(ErrorConversacion) as exc:
        _todo(ConversadorClaude(cliente=claude).responder(BLOQUES, [], "hola"))
    assert str(exc.value)  # a sentence for the screen


# -- R9: the spending cap --------------------------------------------------------------------------------------------

def test_precio_por_modelo_con_la_cache():  # R9 · bug 038 R2: a call of 3M tokens in is in Haiku's long tier
    uso = {"entrada": 1_000_000, "salida": 1_000_000, "cache_escritura": 1_000_000, "cache_lectura": 1_000_000}
    assert dolares("claude-haiku-5-5", uso) == pytest.approx(0.50 + 2.50 + 0.625 + 0.05)
    assert dolares("claude-sonnet-5-5", uso) == pytest.approx(2 + 10 + 2.5 + 0.20)
    assert dolares("modelo-desconocido", uso) == dolares("claude-sonnet-5-5", uso)  # unknown: the dearest


def test_haiku_hasta_100k_de_entrada_a_precio_normal_y_por_encima_al_largo():  # bug 038 R2
    hasta = {"entrada": 60_000, "salida": 10_000, "cache_escritura": 20_000, "cache_lectura": 20_000}  # 100K in
    assert dolares("claude-haiku-5-5", hasta) == pytest.approx(
        (60_000 * 0.10 + 10_000 * 0.50 + 20_000 * 0.125 + 20_000 * 0.01) / 1_000_000)
    pasa = {**hasta, "cache_lectura": 20_001}  # the cache counts as input: 100.001
    assert dolares("claude-haiku-5-5", pasa) == pytest.approx(
        (60_000 * 0.50 + 10_000 * 2.50 + 20_000 * 0.625 + 20_001 * 0.05) / 1_000_000)
    assert dolares("claude-sonnet-5-5", pasa) == pytest.approx(
        (60_000 * 2 + 10_000 * 10 + 20_000 * 2.5 + 20_001 * 0.20) / 1_000_000)  # Sonnet has one price


def test_la_respuesta_trae_el_uso_de_cada_llamada():  # bug 038 R2: the price goes per call
    claude = ClaudeFalso()
    claude.responder("Primero.", ("poner_titulo", {"titulo": "Carrera"}))
    claude.responder("Después.")
    _, r = _todo(ConversadorClaude(cliente=claude).responder(BLOQUES, [], "hola", al_usar=lambda n, e: "hecho"))
    assert len(r.usos) == 2
    assert {k: sum(u[k] for u in r.usos) for k in r.uso} == r.uso


def test_un_rechazo_lleva_el_uso_de_lo_ya_cobrado():  # bug 038 R1
    claude = ClaudeFalso()
    claude.responder("Primero.", ("poner_titulo", {"titulo": "Carrera"}))
    claude.rechazar()
    with pytest.raises(ErrorConversacion) as exc:
        _todo(ConversadorClaude(cliente=claude).responder(BLOQUES, [], "hola", al_usar=lambda n, e: "hecho"))
    assert exc.value.modelo == MODELO_POR_DEFECTO and len(exc.value.usos) == 2  # the round before and the refusal
    assert all(u["entrada"] > 0 for u in exc.value.usos)


def test_un_corte_a_mitad_lleva_las_rondas_hechas_y_el_uso_parcial():  # bug 038 R1
    claude = ClaudeFalso()
    claude.responder("Primero.", ("poner_titulo", {"titulo": "Carrera"}))
    claude.cortar(entrada=5_000, salida=7)
    with pytest.raises(ErrorConversacion) as exc:
        _todo(ConversadorClaude(cliente=claude).responder(BLOQUES, [], "hola", al_usar=lambda n, e: "hecho"))
    assert len(exc.value.usos) == 2
    assert (exc.value.usos[-1]["entrada"], exc.value.usos[-1]["salida"]) == (5_000, 7)


def test_sin_ninguna_llamada_respondida_no_hay_uso():  # bug 038 R1
    claude = ClaudeFalso()
    claude.fallo = fallo_de_red()
    with pytest.raises(ErrorConversacion) as exc:
        _todo(ConversadorClaude(cliente=claude).responder(BLOQUES, [], "hola"))
    assert exc.value.usos == ()


def test_el_tope_del_mes_corta_y_vuelve_el_mes_siguiente(tmp_path):  # R9 · unit 041 R10: 15 $
    assert TOPE_DOLARES_MES == 15.0
    c = Conversaciones(tmp_path / "data.sqlite3")
    casi = {"entrada": 0, "salida": 27_000_000, "cache_escritura": 0, "cache_lectura": 0}  # 13,50 $ with Haiku
    c.apuntar_gasto(datetime(2026, 9, 30, 23, 0), "claude-haiku-5-5", casi, "mensaje")  # last month: not counted
    assert not c.tope_alcanzado(AHORA)
    c.apuntar_gasto(AHORA, "claude-haiku-5-5", casi, "mensaje")
    assert c.gasto_del_mes(AHORA) == pytest.approx(13.5)
    assert not c.tope_alcanzado(AHORA)
    c.apuntar_gasto(AHORA, "claude-sonnet-5-5", {"entrada": 0, "salida": 150_000, "cache_escritura": 0,
                                                 "cache_lectura": 0}, "mensaje")  # «pensar más» counts too
    assert c.tope_alcanzado(AHORA)
    assert not c.tope_alcanzado(datetime(2026, 11, 1, 0, 1))


def test_el_titulo_provisional_es_el_principio_del_mensaje():  # R7
    assert titulo_provisional("quiero hacer 10 km el 3 de diciembre en 45 minutos") == "Quiero hacer 10 km el 3 de…"
    assert titulo_provisional("  peso  ") == "Peso"
    assert titulo_provisional("") == "Conversación"


# -- R4 · R12 · G-50: the medical transcription ----------------------------------------------------------------------

@pytest.fixture
def carpeta(tmp_path):
    c = tmp_path / "medicos"
    (c / "analiticas").mkdir(parents=True)
    (c / "pruebas").mkdir()
    (c / "analiticas" / "analitica-de-prueba.pdf").write_bytes(pdf_sintetico("analitica inventada"))
    (c / "pruebas" / "informe-de-prueba.pdf").write_bytes(pdf_sintetico("ergometria inventada"))
    (c / "notas.txt").write_text("no es un PDF")
    return c


def test_cada_pdf_se_transcribe_una_vez_y_otra_si_cambia(tmp_path, carpeta, caplog):  # R4 · P1
    caplog.set_level(logging.DEBUG)
    pdf = PdfFalso()
    gastos = []
    medicos = Medicos(tmp_path / "data.sqlite3", carpeta, TranscriptorPDF(cliente=pdf),
                      al_gastar=lambda modelo, uso: gastos.append((modelo, uso)))
    estado = medicos.al_dia()
    assert len(pdf.peticiones) == 2 and len(gastos) == 2
    assert estado["aviso"] is None
    assert sorted(d["tipo"] for d in estado["documentos"]) == ["analitica", "prueba_esfuerzo"]
    peticion = pdf.peticiones[0]
    documento = peticion["messages"][0]["content"][0]
    assert documento["type"] == "document" and documento["source"]["media_type"] == "application/pdf"
    assert base64.b64decode(documento["source"]["data"]).startswith(b"%PDF-")

    medicos.al_dia()  # nothing changed: no call
    assert len(pdf.peticiones) == 2
    otra = Medicos(tmp_path / "data.sqlite3", carpeta, TranscriptorPDF(cliente=pdf))  # a new start of the app
    otra.al_dia()
    assert len(pdf.peticiones) == 2  # it lives in the local database

    (carpeta / "analiticas" / "analitica-de-prueba.pdf").write_bytes(pdf_sintetico("analitica inventada nueva"))
    medicos.al_dia()
    assert len(pdf.peticiones) == 3  # only the one that changed

    medicos.al_dia(forzar=True)  # «Volver a leerlas»
    assert len(pdf.peticiones) == 5

    transcritas = medicos.transcripciones()
    assert any(v["valor"] == VALOR_INVENTADO for t in transcritas for v in t["valores"])
    assert VALOR_INVENTADO not in caplog.text and "ferritina" not in caplog.text  # R12: never in the logs
    assert "58,31" not in caplog.text


def test_un_pdf_que_desaparece_sale_del_contexto(tmp_path, carpeta):  # R4
    medicos = Medicos(tmp_path / "data.sqlite3", carpeta, TranscriptorPDF(cliente=PdfFalso()))
    medicos.al_dia()
    (carpeta / "pruebas" / "informe-de-prueba.pdf").unlink()
    estado = medicos.al_dia()
    assert [t["tipo"] for t in medicos.transcripciones()] == ["analitica"]
    assert [d["tipo"] for d in estado["documentos"]] == ["analitica"]


def test_sin_carpeta_lo_dice_y_no_llama(tmp_path):  # R4
    pdf = PdfFalso()
    sin = Medicos(tmp_path / "data.sqlite3", None, TranscriptorPDF(cliente=pdf)).al_dia()
    assert "TRIDATA_MEDICOS_DIR" in sin["aviso"] and sin["documentos"] == []
    no_esta = Medicos(tmp_path / "data.sqlite3", tmp_path / "no-existe", TranscriptorPDF(cliente=pdf)).al_dia()
    assert no_esta["aviso"] and no_esta["documentos"] == []
    vacia = tmp_path / "vacia"
    vacia.mkdir()
    assert "PDF" in Medicos(tmp_path / "data.sqlite3", vacia, TranscriptorPDF(cliente=pdf)).al_dia()["aviso"]
    assert pdf.peticiones == []


def test_un_pdf_que_no_se_puede_leer_lo_dice_y_sigue(tmp_path, carpeta, caplog):  # R4
    pdf = PdfFalso()
    pdf.fallo = fallo_de_red()
    medicos = Medicos(tmp_path / "data.sqlite3", carpeta, TranscriptorPDF(cliente=pdf))
    estado = medicos.al_dia()
    assert estado["documentos"] == [] and "2" in estado["aviso"]
    pdf.fallo = None
    assert len(medicos.al_dia()["documentos"]) == 2  # not saved as read: tried again next time


# -- unit 041 (REC-23): the role and the model of each answer, `abrir_actividad` and the old conversations ----------

def test_el_conversador_responde_con_el_modelo_y_el_papel_que_se_le_piden():  # R3, R7 · C-180, C-184
    claude = ClaudeFalso()
    conversador = ConversadorClaude(cliente=claude)
    _, r = _todo(conversador.responder(BLOQUES, [], "hola", modelo="claude-sonnet-5-5", papel="Eres el fisio."))
    assert r.modelo == claude.peticiones[-1]["model"] == "claude-sonnet-5-5"
    assert claude.peticiones[-1]["system"][-1] == {"type": "text", "text": "Eres el fisio."}
    assert claude.peticiones[-1]["system"][-2]["cache_control"] == {"type": "ephemeral"}
    _, r = _todo(conversador.responder(BLOQUES, [], "hola", modelo="claude-haiku-5-5", pensar=True))
    assert r.modelo == "claude-sonnet-5-5"  # «pensar más» is still Sonnet
    _, r = _todo(conversador.responder(BLOQUES, [], "hola"))
    assert r.modelo == MODELO_POR_DEFECTO and len(claude.peticiones[-1]["system"]) == len(BLOQUES)


def test_abrir_actividad_es_una_herramienta_fija_y_plana():  # R4 · C-181, S-54 · bug 036 (flat schemas)
    p = construir_peticion(BLOQUES, [], "hola", MODELO_POR_DEFECTO)
    herramientas = {h["name"]: h for h in p["tools"]}
    assert set(herramientas) == {"proponer_cambio", "poner_titulo", "abrir_actividad"}
    esquema = herramientas["abrir_actividad"]["input_schema"]
    assert set(esquema["properties"]) == {"fecha", "id"}
    assert all(v == {"type": "string", "description": v["description"]} for v in esquema["properties"].values())
    assert esquema["required"] == ["fecha", "id"] and esquema["additionalProperties"] is False
    assert "vueltas" in herramientas["abrir_actividad"]["description"]


def _base_de_antes(db) -> None:
    """The tables of unit 032 as they were, with one conversation and its answer (invented)."""
    import sqlite3

    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE conversacion (id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT NOT NULL,
            titulo_auto INTEGER NOT NULL DEFAULT 1, creada TEXT NOT NULL, usada TEXT NOT NULL);
        CREATE TABLE mensaje (id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversacion_id INTEGER NOT NULL REFERENCES conversacion(id) ON DELETE CASCADE,
            quien TEXT NOT NULL CHECK (quien IN ('andres', 'asistente')), texto TEXT NOT NULL, hora TEXT NOT NULL,
            contexto_hasta TEXT, sin_respuesta INTEGER NOT NULL DEFAULT 0, modelo TEXT, tokens_entrada INTEGER,
            tokens_salida INTEGER, tokens_cache_escritura INTEGER, tokens_cache_lectura INTEGER,
            tarjetas TEXT NOT NULL DEFAULT '[]', propuesto TEXT NOT NULL DEFAULT '[]');
        INSERT INTO conversacion (titulo, creada, usada) VALUES ('Peso de prueba', '2026-10-20T09:00:00',
            '2026-10-20T09:00:00');
        INSERT INTO mensaje (conversacion_id, quien, texto, hora) VALUES (1, 'andres', '¿y mi peso?',
            '2026-10-20T09:00:00');
        INSERT INTO mensaje (conversacion_id, quien, texto, hora, modelo) VALUES (1, 'asistente', 'Va bien.',
            '2026-10-20T09:00:05', 'claude-haiku-5-5');
    """)
    conn.commit()
    conn.close()


def test_las_conversaciones_de_antes_son_del_asistente(tmp_path):  # R1 · C-178 (aditive migration)
    db = tmp_path / "data.sqlite3"
    _base_de_antes(db)
    c = Conversaciones(db)
    assert c.conversacion(1)["papel"] == "asistente"
    assert [c_["papel"] for c_ in c.lista()] == ["asistente"]
    andres, respuesta = c.mensajes(1)
    assert (andres["papel"], respuesta["papel"], respuesta["modelo"]) == (None, "asistente", "claude-haiku-5-5")
    Conversaciones(db)  # opened again: the migration does not fail twice


def test_cada_conversacion_nace_con_su_papel_y_cada_respuesta_guarda_papel_y_modelo(tmp_path):  # R1, R3
    c = Conversaciones(tmp_path / "data.sqlite3")
    nueva = c.crear("Tirada de prueba", AHORA, papel="entrenador")
    assert nueva["papel"] == "entrenador" and c.crear("Otra", AHORA)["papel"] == "asistente"
    m = c.anadir_mensaje(nueva["id"], "asistente", "Opino como fisio.", AHORA, papel="fisio",
                         modelo="claude-sonnet-5-5")
    assert (m["papel"], m["modelo"]) == ("fisio", "claude-sonnet-5-5")
    with pytest.raises(ValueError):
        c.crear("Mal", AHORA, papel="masajista")


def test_el_modelo_de_cada_papel_se_elige_entre_los_del_catalogo(tmp_path):  # R7 · C-184, G-56
    c = Conversaciones(tmp_path / "data.sqlite3")
    assert c.modelos_de_papeles() == {"entrenador": "claude-sonnet-5-5", "fisio": "claude-sonnet-5-5",
                                      "nutricionista": "claude-sonnet-5-5", "asistente": "claude-haiku-5-5"}
    c.elegir_modelo("entrenador", "claude-haiku-5-5")
    assert c.modelo_de("entrenador") == "claude-haiku-5-5"
    assert Conversaciones(tmp_path / "data.sqlite3").modelo_de("entrenador") == "claude-haiku-5-5"  # kept on the PC
    with pytest.raises(ValueError):
        c.elegir_modelo("entrenador", "modelo-fuera-del-catalogo")
    with pytest.raises(ValueError):
        c.elegir_modelo("masajista", "claude-haiku-5-5")
    assert c.modelo_de("entrenador") == "claude-haiku-5-5"
