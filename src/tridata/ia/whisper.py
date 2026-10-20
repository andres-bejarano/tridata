"""Turn a recording into text with faster-whisper on this PC's GPU (unit 006, D3).

The audio never leaves the PC and is never written to disk: it is decoded from memory
(`io.BytesIO`, PyAV inside faster-whisper). Neither the audio nor the text go to the log.
`faster_whisper` is imported inside `cargar()`: it is the optional `voz` extra, which the test
suite and CI do not install. The model is loaded once and transcribes one recording at a time.
"""
from __future__ import annotations

import ctypes
import glob
import importlib.util
import io
import logging
import os
import site
import sys
import threading
from typing import Any

log = logging.getLogger(__name__)

MODELO_VOZ_POR_DEFECTO = "large-v3-turbo"
_LIBRERIAS_CUDA = ("nvidia/cublas/lib/libcublas*.so*", "nvidia/cudnn/lib/libcudnn*.so*")


class ErrorTranscripcion(Exception):
    """The recording could not be turned into text.

    `motivo`: "no_disponible" (no `voz` extra, CUDA or the GPU failed: try again later),
    "audio_no_valido" (cannot be decoded) or "vacio" (no voice heard).
    """

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


def _precargar_cuda() -> None:
    """cuBLAS and cuDNN installed with pip are not found on their own: load them first, globally.

    Without this, ctranslate2 would need LD_LIBRARY_PATH set before Python starts. Nothing
    happens when they are not installed (CPU-only machines, CI).
    """
    for patron in _LIBRERIAS_CUDA:
        for carpeta in site.getsitepackages():
            for fichero in sorted(glob.glob(os.path.join(carpeta, patron))):
                try:
                    ctypes.CDLL(fichero, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    log.debug("could not preload %s", os.path.basename(fichero))


class TranscriptorWhisper:
    def __init__(self, modelo: str | None = None) -> None:
        self.modelo = modelo or os.environ.get("TRIDATA_MODELO_VOZ") or MODELO_VOZ_POR_DEFECTO
        self._whisper: Any = None
        self._cerrojo_carga = threading.Lock()
        self._cerrojo_uso = threading.Lock()

    @staticmethod
    def disponible() -> bool:
        """Whether the `voz` extra is installed, without importing it (that is slow)."""
        try:
            return importlib.util.find_spec("faster_whisper") is not None
        except (ImportError, ValueError):  # ValueError: already in sys.modules without a spec
            return sys.modules.get("faster_whisper") is not None

    def cargar(self) -> None:
        """Load the model once; later calls return at once. Raises ErrorTranscripcion("no_disponible")."""
        with self._cerrojo_carga:
            if self._whisper is not None:
                return
            try:
                _precargar_cuda()
                from faster_whisper import WhisperModel

                self._whisper = WhisperModel(self.modelo, device="cuda", compute_type="float16")
            except Exception as exc:  # import, CUDA, cuDNN, the GPU, the download of the model
                log.warning("dictation not available: could not load Whisper %s (%s)", self.modelo,
                            type(exc).__name__)
                raise ErrorTranscripcion("no_disponible") from exc
            log.info("Whisper %s loaded on cuda", self.modelo)

    def transcribir(self, audio: bytes) -> str:
        """The text said in `audio` (any container PyAV decodes: MP4/AAC, WebM/Opus…), in Spanish."""
        self.cargar()
        with self._cerrojo_uso:  # one GPU, one recording at a time
            try:
                segmentos, _info = self._whisper.transcribe(io.BytesIO(audio), language="es", vad_filter=True)
                texto = " ".join(t for t in (s.text.strip() for s in segmentos) if t)
            except ValueError as exc:  # PyAV's InvalidDataError and friends: not decodable audio
                log.info("recording could not be decoded (%s)", type(exc).__name__)
                raise ErrorTranscripcion("audio_no_valido") from exc
            except Exception as exc:  # CUDA / GPU failures while transcribing
                log.warning("transcription failed (%s)", type(exc).__name__)
                raise ErrorTranscripcion("no_disponible") from exc
        if not texto:
            raise ErrorTranscripcion("vacio")
        return texto
