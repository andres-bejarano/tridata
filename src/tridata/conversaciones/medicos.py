"""The medical documents of the Asistente's context (unit 032, R4, P1, G-50): read ONCE, kept only on this PC.

The PDFs live in the folder `TRIDATA_MEDICOS_DIR` of `.env` (outside any repository; only its path is written down).
Each one is sent ONCE to the API as a `document` block to transcribe its values (test, value, unit, reference range,
date) and the notes of the report; the transcription is kept in the local SQLite (`transcripcion_medica`) with the
SHA-256 of the file, and it is read again only when the file changes or Andrés asks for it («Volver a leerlas»). A
PDF that is no longer in the folder leaves the context. What goes in every message is that transcription (a few
thousand tokens), never the PDFs.

Nothing here logs a value, a test name or the contents of a document: only counts (R12, G-50). The transcription of
this module is what `tridata comprobar-privados` also looks for (`valores_privados`).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import sqlite3
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)

MODELO_TRANSCRIPCION = "claude-haiku-5-5"
SIN_CARPETA = ("No sé dónde están tus analíticas y tu prueba de esfuerzo: falta TRIDATA_MEDICOS_DIR en .env "
               "(la carpeta de sus PDF)")
NO_ESTA_CARPETA = "No encuentro la carpeta de tus analíticas (TRIDATA_MEDICOS_DIR)"
SIN_PDF = "En la carpeta de tus analíticas (TRIDATA_MEDICOS_DIR) no hay ningún PDF"

INSTRUCCION = """\
Transcribe este documento médico de la persona a datos estructurados, sin interpretar ni resumir: es para que su \
asistente de entrenamiento y nutrición lo tenga delante. tipo: "analitica" (análisis de sangre u orina), \
"prueba_esfuerzo" (ergometría, prueba de esfuerzo o sus fases) u "otro". titulo: qué documento es, breve. fecha: la \
del documento (AAAA-MM-DD) o null. valores: cada prueba o medida con su valor tal cual aparece (con su coma o punto), \
su unidad, su rango de referencia si lo da y su fecha si es distinta de la del documento; en una prueba de esfuerzo, \
también cada fase o escalón con su potencia o velocidad, su frecuencia cardiaca, su consumo de oxígeno, su lactato o \
lo que dé, y los umbrales y máximos. notas: las conclusiones o comentarios del informe, copiados de forma literal y \
breve. No inventes nada que no esté en el documento.
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripcion_medica (
    fichero TEXT PRIMARY KEY,
    huella TEXT NOT NULL,
    tipo TEXT NOT NULL,
    titulo TEXT NOT NULL,
    fecha TEXT,
    valores TEXT NOT NULL,
    notas TEXT NOT NULL,
    leida TEXT NOT NULL
);
"""


def _todo_requerido(esquema: dict[str, Any]) -> None:
    esquema["required"] = list(esquema.get("properties", {}))


class ValorMedico(BaseModel):
    model_config = ConfigDict(json_schema_extra=_todo_requerido)

    prueba: str = Field(description="Nombre de la prueba o medida, tal cual")
    valor: str = Field(description="El valor tal cual aparece, con su coma o punto")
    unidad: str | None = Field(description="Su unidad, o null")
    rango: str | None = Field(description="Su rango de referencia, o null")
    fecha: str | None = Field(description="Su fecha (AAAA-MM-DD) si es distinta de la del documento, o null")


class TranscripcionMedica(BaseModel):
    model_config = ConfigDict(json_schema_extra=_todo_requerido)

    tipo: Literal["analitica", "prueba_esfuerzo", "otro"]
    titulo: str
    fecha: str | None
    valores: list[ValorMedico]
    notas: list[str]


class ErrorMedico(Exception):
    """A document could not be transcribed (no key, no network, an answer that is not usable…)."""


class TranscriptorPDF:
    """One PDF -> its transcription, with the Claude API (`messages.parse`). The client is lazy and injectable."""

    def __init__(self, cliente: Any = None, modelo: str = MODELO_TRANSCRIPCION) -> None:
        self._cliente = cliente
        self.modelo = modelo

    def transcribir(self, pdf: bytes) -> tuple[TranscripcionMedica, dict[str, int], str]:
        datos = base64.standard_b64encode(pdf).decode("ascii")
        try:
            if self._cliente is None:
                self._cliente = anthropic.Anthropic(timeout=120.0)
            respuesta = self._cliente.messages.parse(
                model=self.modelo, max_tokens=16000,
                messages=[{"role": "user", "content": [
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": datos}},
                    {"type": "text", "text": INSTRUCCION},
                ]}],
                output_format=TranscripcionMedica,
            )
        except anthropic.AnthropicError as exc:
            raise ErrorMedico(type(exc).__name__) from exc
        except (TypeError, ValueError) as exc:  # no key, or an answer that does not validate: never its contents
            raise ErrorMedico(type(exc).__name__) from exc
        salida = respuesta.parsed_output
        if respuesta.stop_reason != "end_turn" or salida is None:
            raise ErrorMedico(f"stop_reason={respuesta.stop_reason}")
        u = respuesta.usage
        uso = {"entrada": int(getattr(u, "input_tokens", 0) or 0), "salida": int(getattr(u, "output_tokens", 0) or 0),
               "cache_escritura": int(getattr(u, "cache_creation_input_tokens", 0) or 0),
               "cache_lectura": int(getattr(u, "cache_read_input_tokens", 0) or 0)}
        return salida, uso, self.modelo


def _huella(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _documento(fila: sqlite3.Row) -> dict[str, Any]:
    return {"fichero": fila["fichero"], "tipo": fila["tipo"], "titulo": fila["titulo"], "fecha": fila["fecha"],
            "valores": json.loads(fila["valores"]), "notas": json.loads(fila["notas"]), "leida": fila["leida"]}


def _plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


class Medicos:
    """The transcriptions of the PDFs of `carpeta`, in the SQLite of `db_path`.

    `transcriptor` has `transcribir(pdf) -> (TranscripcionMedica, uso, modelo)`; `al_gastar(modelo, uso)` gets the use
    of each transcription (it counts for the cap of the month, R9)."""

    def __init__(self, db_path: Path | str, carpeta: Path | str | None, transcriptor: Any = None,
                 al_gastar: Callable[[str, dict[str, int]], Any] | None = None) -> None:
        self._db_path = Path(db_path)
        self.carpeta = Path(carpeta).expanduser() if carpeta else None
        self._transcriptor = transcriptor
        self._al_gastar = al_gastar
        self._fallidos = 0
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

    def _pdfs(self) -> dict[str, Path] | None:
        if self.carpeta is None or not self.carpeta.is_dir():
            return None
        return {p.relative_to(self.carpeta).as_posix(): p for p in sorted(self.carpeta.rglob("*"))
                if p.is_file() and p.suffix.lower() == ".pdf"}

    def al_dia(self, *, forzar: bool = False) -> dict[str, Any]:
        """Transcribes each PDF new or changed (all with `forzar`) and forgets the ones that are gone. Returns
        `estado()`. A document that cannot be read is said in `aviso` and tried again the next time."""
        pdfs = self._pdfs()
        self._fallidos = 0
        if pdfs is None:
            return self.estado()
        with self._connect() as conn:
            guardadas = {f["fichero"]: f["huella"] for f in conn.execute("SELECT fichero, huella FROM transcripcion_medica")}
            for fichero in set(guardadas) - set(pdfs):
                conn.execute("DELETE FROM transcripcion_medica WHERE fichero = ?", (fichero,))
        leidos = 0
        for fichero, ruta in pdfs.items():
            huella = _huella(ruta)
            if not forzar and guardadas.get(fichero) == huella:
                continue
            if self._transcriptor is None:
                self._transcriptor = TranscriptorPDF()
            try:
                transcripcion, uso, modelo = self._transcriptor.transcribir(ruta.read_bytes())
            except ErrorMedico as exc:
                log.warning("a medical document could not be transcribed (%s)", exc)
                self._fallidos += 1
                continue
            if self._al_gastar is not None:
                self._al_gastar(modelo, uso)
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO transcripcion_medica (fichero, huella, tipo, titulo, fecha, valores, "
                    "notas, leida) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (fichero, huella, transcripcion.tipo, transcripcion.titulo, transcripcion.fecha,
                     json.dumps([v.model_dump() for v in transcripcion.valores], ensure_ascii=False),
                     json.dumps(transcripcion.notas, ensure_ascii=False),
                     datetime.now().isoformat(timespec="seconds")))
            leidos += 1
            log.info("medical document transcribed: %d values, %d notes", len(transcripcion.valores),
                     len(transcripcion.notas))
        if leidos or self._fallidos:
            log.info("medical documents: %d transcribed, %d failed, %d in the folder", leidos, self._fallidos, len(pdfs))
        return self.estado()

    def transcripciones(self) -> list[dict[str, Any]]:
        """The transcriptions of the PDFs still in the folder, by file name: what goes in the context."""
        pdfs = self._pdfs()
        if not pdfs:
            return []
        with self._connect() as conn:
            filas = conn.execute("SELECT * FROM transcripcion_medica ORDER BY fichero").fetchall()
        return [_documento(f) for f in filas if f["fichero"] in pdfs]

    def estado(self) -> dict[str, Any]:
        """What the Asistente knows of the medical documents (R4): each one with its values, and a warning when the
        folder or a PDF is missing or a document could not be read."""
        pdfs = self._pdfs()
        documentos = self.transcripciones()
        aviso = None
        if self.carpeta is None:
            aviso = SIN_CARPETA
        elif pdfs is None:
            aviso = NO_ESTA_CARPETA
        elif not pdfs:
            aviso = SIN_PDF
        elif self._fallidos:
            aviso = (f"No he podido leer {_plural(self._fallidos, 'documento', 'documentos')}: lo intentaré en el "
                     "próximo mensaje")
        return {"carpeta": str(self.carpeta) if self.carpeta else None, "aviso": aviso, "documentos": documentos}


# -- what `tridata comprobar-privados` looks for (R12, C-160) --------------------------------------------------------

_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")


def _distintivos(valor: str) -> set[str]:
    """The numbers of a value that are worth looking for: with decimals (in both spellings) or of 3 digits or more.
    One or two whole digits («91», «7») appear everywhere in a repository and would never let it say 0."""
    encontrados: set[str] = set()
    for n in _NUMERO.findall(valor):
        if "," in n or "." in n:
            encontrados |= {n.replace(".", ","), n.replace(",", ".")}
        elif len(n) >= 3:
            encontrados.add(n)
    return encontrados


def normalizar_nombre(texto: str) -> str:
    """Lowercase, without accents and with single spaces: how a test's name is compared (bug 037)."""
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().split())


def valores_privados(db_path: Path | str) -> set[tuple[str, str]]:
    """Every `(name of the test, distinctive number)` of the medical transcriptions saved in `db_path`, read only
    (nothing is created). Bug 037: a number counts only next to the name of its test — on its own, «13.87» or «2468»
    is just as likely a constant of the code."""
    ruta = Path(db_path)
    if not ruta.exists():
        return set()
    conn = sqlite3.connect(f"{ruta.as_uri()}?mode=ro", uri=True)
    try:
        hay = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'transcripcion_medica'").fetchone()
        filas = conn.execute("SELECT valores FROM transcripcion_medica").fetchall() if hay else []
    finally:
        conn.close()
    pares: set[tuple[str, str]] = set()
    for (texto,) in filas:
        for v in json.loads(texto):
            nombre = normalizar_nombre(str(v.get("prueba") or ""))
            if nombre:
                pares |= {(nombre, n) for n in _distintivos(str(v.get("valor") or ""))}
    return pares


__all__ = ["ErrorMedico", "Medicos", "TranscripcionMedica", "TranscriptorPDF", "ValorMedico", "valores_privados"]
