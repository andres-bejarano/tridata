"""AI of the food log: interpretation of the text (Claude API) and transcription of the voice (Whisper)."""
from .claude import (
    INSTRUCCION, MODELO_POR_DEFECTO, AlimentoInterpretado, CambioInterpretado, ErrorInterpretacion, InterpreteClaude,
    Interpretacion, LineaInterpretada, ListaInterpretada, ObjetivoDiaInterpretado, PesoInterpretado, construir_peticion,
)
from .whisper import MODELO_VOZ_POR_DEFECTO, ErrorTranscripcion, TranscriptorWhisper

__all__ = [
    "INSTRUCCION", "MODELO_POR_DEFECTO", "MODELO_VOZ_POR_DEFECTO", "AlimentoInterpretado", "CambioInterpretado",
    "ErrorInterpretacion", "ErrorTranscripcion", "InterpreteClaude", "Interpretacion", "LineaInterpretada",
    "ListaInterpretada", "ObjetivoDiaInterpretado", "PesoInterpretado", "TranscriptorWhisper", "construir_peticion",
]
