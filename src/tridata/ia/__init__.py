"""AI of the food log: interpretation of the text (Claude API) and transcription of the voice (Whisper)."""
from .claude import (
    INSTRUCCION, MODELO_POR_DEFECTO, AlimentoInterpretado, ErrorInterpretacion, InterpreteClaude, Interpretacion,
    ListaInterpretada, ObjetivoDiaInterpretado, PesoInterpretado, construir_peticion,
)
from .whisper import MODELO_VOZ_POR_DEFECTO, ErrorTranscripcion, TranscriptorWhisper

__all__ = [
    "INSTRUCCION", "MODELO_POR_DEFECTO", "MODELO_VOZ_POR_DEFECTO", "AlimentoInterpretado", "ErrorInterpretacion",
    "ErrorTranscripcion", "InterpreteClaude", "Interpretacion", "ListaInterpretada", "ObjetivoDiaInterpretado",
    "PesoInterpretado", "TranscriptorWhisper", "construir_peticion",
]
