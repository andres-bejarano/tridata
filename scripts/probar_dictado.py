#!/usr/bin/env python
"""Smoke test of the dictation with the REAL Whisper model on this PC's GPU (unit 006, R8/R9).

    python scripts/probar_dictado.py <audio> [<audio> ...] [--veces N]

Loads the model of TRIDATA_MODELO_VOZ (default large-v3-turbo) the same way `tridata web` does,
transcribes each recording N times and prints the device, the load seconds, the seconds of each
transcription and the text. Needs the `voz` extra. Not part of the test suite (it is not named
test_*): it uses the GPU. The test audio (invented phrases) goes to .runtime/, NEVER to the repo.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from tridata.ia import ErrorTranscripcion, TranscriptorWhisper


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audios", nargs="+", type=Path, help="recordings (MP4/AAC from the iPhone, WebM, WAV…)")
    parser.add_argument("--veces", type=int, default=1, help="transcriptions per recording (latency, R9)")
    parser.add_argument("--limite", type=float, default=5.0, help="seconds a transcription may take (R9)")
    args = parser.parse_args()

    if not TranscriptorWhisper.disponible():
        print("faster-whisper is not installed: pip install -e '.[voz]'", file=sys.stderr)
        return 1
    transcriptor = TranscriptorWhisper()
    inicio = time.perf_counter()
    try:
        transcriptor.cargar()
    except ErrorTranscripcion as exc:
        print(f"could not load Whisper {transcriptor.modelo}: {exc.__cause__!r}", file=sys.stderr)
        return 1
    modelo = transcriptor._whisper.model
    print(f"modelo: {transcriptor.modelo} · dispositivo: {modelo.device} · tipo: {modelo.compute_type} · "
          f"carga: {time.perf_counter() - inicio:.1f} s")

    tiempos = []
    for ruta in args.audios:
        audio = ruta.read_bytes()
        for vez in range(1, args.veces + 1):
            inicio = time.perf_counter()
            try:
                texto = transcriptor.transcribir(audio)
            except ErrorTranscripcion as exc:
                texto = f"<ErrorTranscripcion {exc.motivo}>"
            segundos = time.perf_counter() - inicio
            tiempos.append(segundos)
            print(f"{ruta.name} #{vez}: {segundos:.2f} s · {texto}")
    dentro = sum(t <= args.limite for t in tiempos)
    print(f"{dentro}/{len(tiempos)} transcripciones en {args.limite:g} s o menos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
