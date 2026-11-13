"""Unit 032 (REC-20 of `preguntar-equipo`): the pieces of the team's conversations and medical documents, without
the web.

- the conversations kept on the PC (`conversaciones/__init__.py`): the ones of before keep their role and the model
  of each answer (R1 of 041, an additive migration); since unit 083 the only writer is the coach («Desde el PC»);
- the medical transcription (`conversaciones/medicos.py`): kept with the SHA-256 of its PDF, out of the context when
  the PDF goes, and why it is missing when it is (R4); nothing in the logs (R12).

Unit 083 (P-20261113-33e057c3): the request to the API, its answer, its tools, its prices and the spending cap, and
the transcription of a PDF by the API, are gone with the paid AI; their tests went with them (`hallazgos.md`).
Every PDF and every value is invented (a PDF written by the test).
"""
from __future__ import annotations

import logging
from datetime import datetime

import pytest

from tridata.conversaciones import Conversaciones
from tridata.conversaciones.medicos import Medicos, TranscripcionMedica, ValorMedico

AHORA = datetime(2026, 10, 24, 9, 42)
VALOR_INVENTADO = "321,7"


def transcripcion_inventada(es_prueba: bool = False) -> TranscripcionMedica:
    """The invented transcription of a blood test (or of a stress test), as the coach would save it."""
    return TranscripcionMedica(
        tipo="prueba_esfuerzo" if es_prueba else "analitica",
        titulo="Prueba de esfuerzo de prueba" if es_prueba else "Analítica de prueba",
        fecha="2025-11-12" if es_prueba else "2026-03-02",
        valores=[ValorMedico(prueba="ferritina de prueba", valor=VALOR_INVENTADO, unidad="ng/mL",
                             rango="30-400", fecha=None)] if not es_prueba else
                [ValorMedico(prueba="consumo de oxígeno de prueba", valor="58,31", unidad="ml/kg/min",
                             rango=None, fecha=None)],
        notas=["Nota inventada del informe."])


def guardar_todas(medicos: Medicos) -> None:
    """Every PDF of the folder without its transcription, saved as the coach does it (`Medicos.guardar`)."""
    for p in medicos.pendientes():
        medicos.guardar(p["fichero"], p["huella"], transcripcion_inventada("ergometria" in p["fichero"]
                                                                          or "informe" in p["fichero"]))


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


def test_cada_pdf_se_guarda_una_vez_y_otra_si_cambia(tmp_path, carpeta, caplog):  # R4 · P1 (unit 083: by the coach)
    caplog.set_level(logging.DEBUG)
    medicos = Medicos(tmp_path / "data.sqlite3", carpeta)
    assert len(medicos.pendientes()) == 2
    guardar_todas(medicos)
    estado = medicos.estado()
    assert estado["aviso"] is None and medicos.pendientes() == []
    assert sorted(d["tipo"] for d in estado["documentos"]) == ["analitica", "prueba_esfuerzo"]
    assert Medicos(tmp_path / "data.sqlite3", carpeta).pendientes() == []  # it lives in the local database
    (carpeta / "analiticas" / "analitica-de-prueba.pdf").write_bytes(pdf_sintetico("analitica inventada nueva"))
    assert [p["estado"] for p in medicos.pendientes()] == ["cambiado"]  # only the one that changed
    transcritas = medicos.transcripciones()
    assert any(v["valor"] == VALOR_INVENTADO for t in transcritas for v in t["valores"])
    assert VALOR_INVENTADO not in caplog.text and "ferritina" not in caplog.text  # R12: never in the logs
    assert "58,31" not in caplog.text


def test_un_pdf_que_desaparece_sale_del_contexto(tmp_path, carpeta):  # R4
    medicos = Medicos(tmp_path / "data.sqlite3", carpeta)
    guardar_todas(medicos)
    (carpeta / "pruebas" / "informe-de-prueba.pdf").unlink()
    assert [t["tipo"] for t in medicos.transcripciones()] == ["analitica"]
    assert [d["tipo"] for d in medicos.estado()["documentos"]] == ["analitica"]


def test_sin_carpeta_lo_dice(tmp_path):  # R4
    sin = Medicos(tmp_path / "data.sqlite3", None).estado()
    assert "TRIDATA_MEDICOS_DIR" in sin["aviso"] and sin["documentos"] == []
    no_esta = Medicos(tmp_path / "data.sqlite3", tmp_path / "no-existe").estado()
    assert no_esta["aviso"] and no_esta["documentos"] == []
    vacia = tmp_path / "vacia"
    vacia.mkdir()
    assert "PDF" in Medicos(tmp_path / "data.sqlite3", vacia).estado()["aviso"]


# -- unit 041 (REC-23): the role and the model of each answer, and the old conversations ------------------------------

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
                         modelo="claude-sonnet-5-5")  # a message of before kept its model; it is still read
    assert (m["papel"], m["modelo"]) == ("fisio", "claude-sonnet-5-5")
    with pytest.raises(ValueError):
        c.crear("Mal", AHORA, papel="masajista")
