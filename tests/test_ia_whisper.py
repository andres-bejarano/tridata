"""Whisper transcriber (unit 006, R8): `faster_whisper.WhisperModel` is a double in sys.modules.

No test loads the real model, touches the GPU or the CUDA libraries.
"""
import ctypes
import io
import logging
import sys
import types
from unittest.mock import MagicMock

import pytest

from tridata.ia import ErrorTranscripcion, TranscriptorWhisper
from tridata.ia import whisper as modulo

TEXTO = "300 gramos de lentejas y un plátano"


def _resultado(*textos):
    return iter([types.SimpleNamespace(text=t) for t in textos]), types.SimpleNamespace(language="es", duration=3.0)


@pytest.fixture
def modelo(monkeypatch):
    """A fake `faster_whisper` whose WhisperModel(...) returns a model double."""
    falso = types.ModuleType("faster_whisper")
    falso.WhisperModel = MagicMock(name="WhisperModel")
    falso.WhisperModel.return_value.transcribe.side_effect = lambda *a, **k: _resultado(
        " 300 gramos de lentejas", " y un plátano ")
    monkeypatch.setitem(sys.modules, "faster_whisper", falso)
    monkeypatch.setattr(modulo, "_precargar_cuda", lambda: None)
    monkeypatch.delenv("TRIDATA_MODELO_VOZ", raising=False)
    return falso.WhisperModel


def test_large_v3_turbo_en_la_gpu_en_espanol_y_texto_unido(modelo):  # R8
    texto = TranscriptorWhisper().transcribir(b"audio-inventado")

    assert texto == TEXTO
    modelo.assert_called_once_with("large-v3-turbo", device="cuda", compute_type="float16")
    args, kwargs = modelo.return_value.transcribe.call_args
    assert isinstance(args[0], io.BytesIO) and args[0].getvalue() == b"audio-inventado"
    assert kwargs["language"] == "es"
    assert kwargs["vad_filter"] is True


def test_el_modelo_se_cambia_en_el_entorno(modelo, monkeypatch):  # R8
    monkeypatch.setenv("TRIDATA_MODELO_VOZ", "small")
    TranscriptorWhisper().transcribir(b"x")
    assert modelo.call_args.args[0] == "small"


def test_se_carga_una_sola_vez(modelo):  # R8
    t = TranscriptorWhisper()
    t.cargar()
    t.transcribir(b"uno")
    t.transcribir(b"dos")
    assert modelo.call_count == 1
    assert modelo.return_value.transcribe.call_count == 2


@pytest.mark.parametrize("textos", [(), ("   ",), (" ", "")])
def test_sin_voz_es_vacio(modelo, textos):  # R4
    modelo.return_value.transcribe.side_effect = lambda *a, **k: _resultado(*textos)
    with pytest.raises(ErrorTranscripcion) as error:
        TranscriptorWhisper().transcribir(b"silencio")
    assert error.value.motivo == "vacio"


def test_si_falla_la_gpu_no_esta_disponible(modelo):  # R4
    modelo.side_effect = RuntimeError("CUDA failed with error out of memory")
    with pytest.raises(ErrorTranscripcion) as error:
        TranscriptorWhisper().transcribir(b"x")
    assert error.value.motivo == "no_disponible"


def test_si_falla_la_gpu_al_transcribir_no_esta_disponible(modelo):  # R4
    def roto():
        raise RuntimeError("CUDA error")
        yield  # pragma: no cover

    modelo.return_value.transcribe.side_effect = lambda *a, **k: (roto(), None)
    with pytest.raises(ErrorTranscripcion) as error:
        TranscriptorWhisper().transcribir(b"x")
    assert error.value.motivo == "no_disponible"


def test_audio_que_no_se_decodifica_no_es_valido(modelo):  # R4
    class InvalidDataError(ValueError):  # what PyAV raises for data that is not audio
        pass

    modelo.return_value.transcribe.side_effect = InvalidDataError("Invalid data found when processing input")
    with pytest.raises(ErrorTranscripcion) as error:
        TranscriptorWhisper().transcribir(b"no-es-audio")
    assert error.value.motivo == "audio_no_valido"


def test_sin_el_extra_voz_no_esta_disponible(monkeypatch):  # R8
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # import faster_whisper -> ImportError
    monkeypatch.setattr(modulo, "_precargar_cuda", lambda: None)
    assert TranscriptorWhisper.disponible() is False
    with pytest.raises(ErrorTranscripcion) as error:
        TranscriptorWhisper().transcribir(b"x")
    assert error.value.motivo == "no_disponible"


def test_el_texto_no_va_al_log(modelo, caplog):  # R2
    caplog.set_level(logging.DEBUG)
    TranscriptorWhisper().transcribir(b"audio-inventado")
    assert "lentejas" not in caplog.text
    assert "audio-inventado" not in caplog.text


def test_precarga_las_librerias_cuda_de_pip(tmp_path, monkeypatch):  # R8
    for ruta in ("nvidia/cublas/lib/libcublas.so.12", "nvidia/cudnn/lib/libcudnn.so.9"):
        (tmp_path / ruta).parent.mkdir(parents=True)
        (tmp_path / ruta).write_bytes(b"")
    cargadas = MagicMock()
    monkeypatch.setattr(modulo.site, "getsitepackages", lambda: [str(tmp_path)])
    monkeypatch.setattr(modulo.ctypes, "CDLL", cargadas)

    modulo._precargar_cuda()

    rutas = [c.args[0] for c in cargadas.call_args_list]
    assert any(r.endswith("libcublas.so.12") for r in rutas)
    assert any(r.endswith("libcudnn.so.9") for r in rutas)
    assert all(c.kwargs["mode"] == ctypes.RTLD_GLOBAL for c in cargadas.call_args_list)
