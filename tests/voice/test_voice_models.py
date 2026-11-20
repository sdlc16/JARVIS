"""VAD real + faster-whisper REAL sobre grabaciones sinteticas controladas.

Prueba la frase completa del usuario (la que fallo en la prueba real): con el
cierre de turno corregido, el texto transcrito debe incluir el FINAL de la
frase, no solo el primer trozo. Requiere el extra ``speech`` y el modelo
`base` de faster-whisper ya descargado (se omite si no esta disponible).
Sigue siendo audio sintetico, no microfono real.
"""

from __future__ import annotations

import re
import unicodedata

import pytest

pytest.importorskip("faster_whisper")

from openjarvis.speech.faster_whisper import FasterWhisperBackend  # noqa: E402
from openjarvis.voice import audio_io  # noqa: E402
from openjarvis.voice.vad import (  # noqa: E402
    VadConfig,
    VoiceActivityDetector,
    segment_utterance,
)
from tests.voice.helpers import frames_of, speech_turn  # noqa: E402

PHRASE = ["hola_jarvis", "como_estas", "explica", "controlar_pc"]


def norm(text: str) -> str:
    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z ]", " ", t)


@pytest.fixture(scope="module")
def stt():
    backend = FasterWhisperBackend(model_size="base", device="cpu", compute_type="int8")
    try:
        backend._ensure_model()
    except Exception as exc:  # modelo no descargado / sin red
        pytest.skip(f"faster-whisper base no disponible: {exc}")
    return backend


def transcribe(stt, pcm: bytes) -> str:
    wav = audio_io.pcm_to_wav_bytes(pcm)
    return norm(stt.transcribe(wav, format="wav", language="es").text)


@pytest.mark.slow
@pytest.mark.parametrize("pause_s", [0.4, 0.9])
def test_full_phrase_with_natural_pauses_reaches_whisper_intact(stt, pause_s):
    sig, _ = speech_turn(PHRASE, level_db=-30, pause_s=pause_s)
    pcm, st = segment_utterance(frames_of(sig), VoiceActivityDetector(VadConfig()))
    text = transcribe(stt, pcm)
    assert "hola" in text.split()[:3], text
    assert "computadora" in text, f"se perdio el final de la frase: {text!r}"


@pytest.mark.slow
def test_old_500ms_cut_loses_the_end_of_the_phrase_regression(stt):
    sig, _ = speech_turn(PHRASE, level_db=-30, pause_s=0.9)
    pcm, _ = segment_utterance(
        frames_of(sig),
        VoiceActivityDetector(VadConfig(silence_timeout_ms=500, energy_gate=False)),
    )
    text = transcribe(stt, pcm) if pcm else ""
    assert "computadora" not in text  # lo que vivio el usuario


@pytest.mark.slow
def test_quiet_voice_is_still_transcribed(stt):
    sig, _ = speech_turn(PHRASE, level_db=-45, pause_s=0.5)
    pcm, _ = segment_utterance(frames_of(sig), VoiceActivityDetector(VadConfig()))
    assert "computadora" in transcribe(stt, pcm)
