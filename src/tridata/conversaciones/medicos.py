"""The medical documents of the team's context (unit 032, R4, P1, G-50): kept only on this PC.

The PDFs live in the folder `TRIDATA_MEDICOS_DIR` of `.env` (outside any repository; only its path is written down).
Their transcription (test, value, unit, reference range, date, and the notes of the report) is kept in the local
SQLite (`transcripcion_medica`) with the SHA-256 of the file. A PDF that is no longer in the folder leaves the
context. What goes in the context is that transcription, never the PDFs.

Unit 081 (REC-32, R-221, R-222): without the paid API, the coach of the PC reads the PDF itself. `pendientes` says
which PDF of the folder have no transcription (new or changed), where they are and their SHA-256, never their
contents; `guardar` keeps the transcription the coach made of one, only if the file is in the folder (`pdf`: a name
relative to it or a path inside it, never outside) and its SHA-256 is the one given; a transcription without any
value is not kept. Unit 083: the transcriber of the API (`TranscriptorPDF`) is gone; the transcriptions it made
stay as they were.

Nothing here logs a value, a test name or the contents of a document: only counts (R12, G-50). The transcription of
this module is what `tridata comprobar-privados` also looks for (`valores_privados`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)

SIN_CARPETA = ("No sé dónde están tus analíticas y tu prueba de esfuerzo: falta TRIDATA_MEDICOS_DIR en .env "
               "(la carpeta de sus PDF)")
NO_ESTA_CARPETA = "No encuentro la carpeta de tus analíticas (TRIDATA_MEDICOS_DIR)"
SIN_PDF = "En la carpeta de tus analíticas (TRIDATA_MEDICOS_DIR) no hay ningún PDF"

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


class FueraDeLaCarpeta(LookupError):
    """Unit 081: that file is not a PDF of the folder of the medical documents (or there is no folder)."""


def _huella(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _documento(fila: sqlite3.Row) -> dict[str, Any]:
    return {"fichero": fila["fichero"], "tipo": fila["tipo"], "titulo": fila["titulo"], "fecha": fila["fecha"],
            "valores": json.loads(fila["valores"]), "notas": json.loads(fila["notas"]), "leida": fila["leida"]}


class Medicos:
    """The transcriptions of the PDFs of `carpeta`, in the SQLite of `db_path`."""

    def __init__(self, db_path: Path | str, carpeta: Path | str | None, transcriptor: Any = None) -> None:
        """`transcriptor` (`transcribir(pdf) -> (TranscripcionMedica, ...)`) is only for `al_dia`, see there."""
        self._db_path = Path(db_path)
        self.carpeta = Path(carpeta).expanduser() if carpeta else None
        self._transcriptor = transcriptor
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

    def al_dia(self) -> dict[str, Any]:
        """Unit 083: kept ONLY because `tests/test_privados.py` (outside this unit) seeds its transcription with a
        double through it; no code calls it and there is no transcriber of the API any more. Each PDF of the folder
        without its transcription is transcribed by the injected `transcriptor` and kept; without one, nothing."""
        if self._transcriptor is not None:
            for p in self.pendientes():
                transcripcion = self._transcriptor.transcribir(Path(p["ruta"]).read_bytes())[0]
                self._escribir(p["fichero"], p["huella"], transcripcion)
        return self.estado()

    def _escribir(self, nombre: str, huella: str, transcripcion: TranscripcionMedica) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO transcripcion_medica (fichero, huella, tipo, titulo, fecha, valores, notas, "
                "leida) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (nombre, huella, transcripcion.tipo, transcripcion.titulo, transcripcion.fecha,
                 json.dumps([v.model_dump() for v in transcripcion.valores], ensure_ascii=False),
                 json.dumps(transcripcion.notas, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))

    def _guardadas(self) -> dict[str, str]:
        with self._connect() as conn:
            return {f["fichero"]: f["huella"] for f in conn.execute("SELECT fichero, huella FROM transcripcion_medica")}

    def pendientes(self) -> list[dict[str, Any]]:
        """Unit 081 (R-221): each PDF of the folder without its transcription: `{fichero, ruta, huella, estado}`, with
        `estado` "nuevo" (never read) or "cambiado" (read when it was another file). Never its contents."""
        pdfs = self._pdfs() or {}
        guardadas = self._guardadas()
        salida = []
        for fichero, ruta in pdfs.items():
            huella = _huella(ruta)
            if guardadas.get(fichero) != huella:
                salida.append({"fichero": fichero, "ruta": str(ruta), "huella": huella,
                               "estado": "cambiado" if fichero in guardadas else "nuevo"})
        return salida

    def pdf(self, fichero: str) -> tuple[str, str]:
        """Unit 081 (R-222): `(name in the folder, its SHA-256 now)` of `fichero`, a name relative to the folder or a
        path inside it. `FueraDeLaCarpeta` if it is not one of its PDF (outside it, «..», not there, no folder)."""
        pdfs = self._pdfs()
        if not pdfs or self.carpeta is None:
            raise FueraDeLaCarpeta
        dado = Path(str(fichero)).expanduser()
        try:
            ruta = (dado if dado.is_absolute() else self.carpeta / dado).resolve()
            nombre = ruta.relative_to(self.carpeta.resolve()).as_posix()
        except (OSError, ValueError):
            raise FueraDeLaCarpeta from None
        if nombre not in pdfs:
            raise FueraDeLaCarpeta
        return nombre, _huella(pdfs[nombre])

    def guardar(self, fichero: str, huella: str, transcripcion: TranscripcionMedica) -> str:
        """Unit 081 (R-221): keeps `transcripcion` as the one of `fichero` and returns its
        name in the folder. `FueraDeLaCarpeta` as `pdf`; ValueError if the file is not the one with `huella` any more
        or the transcription has no value. Logs counts only."""
        nombre, ahora = self.pdf(fichero)
        if ahora != huella:
            raise ValueError("el PDF ha cambiado desde que se leyó")
        if not transcripcion.valores:
            raise ValueError("la transcripción no trae ningún valor")
        self._escribir(nombre, huella, transcripcion)
        log.info("medical document transcribed on the PC: %d values, %d notes", len(transcripcion.valores),
                 len(transcripcion.notas))
        return nombre

    def transcripciones(self) -> list[dict[str, Any]]:
        """The transcriptions of the PDFs still in the folder, by file name: what goes in the context."""
        pdfs = self._pdfs()
        if not pdfs:
            return []
        with self._connect() as conn:
            filas = conn.execute("SELECT * FROM transcripcion_medica ORDER BY fichero").fetchall()
        return [_documento(f) for f in filas if f["fichero"] in pdfs]

    def estado(self) -> dict[str, Any]:
        """What the team knows of the medical documents (R4): each one with its values, and a warning when the folder
        or its PDF are missing."""
        pdfs = self._pdfs()
        documentos = self.transcripciones()
        aviso = None
        if self.carpeta is None:
            aviso = SIN_CARPETA
        elif pdfs is None:
            aviso = NO_ESTA_CARPETA
        elif not pdfs:
            aviso = SIN_PDF
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


__all__ = ["FueraDeLaCarpeta", "Medicos", "TranscripcionMedica", "ValorMedico", "valores_privados"]
