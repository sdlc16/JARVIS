"""Deteccion de actividad de voz (T2.1) via webrtcvad.

Se elige `webrtcvad` sobre `silero-vad` para esta primera version:
es una extension C diminuta (sin modelo que descargar, CPU, <1ms por
frame), suficiente para el caso de uso (detectar inicio/fin de turno
de habla con un corte de silencio claro). Si las pruebas reales con
el usuario muestran demasiados falsos positivos/negativos en ambiente
ruidoso, se documentara y se evaluara migrar a silero-vad (mas preciso,
pero necesita cargar un modelo ONNX) -- decision reversible, ver
DEVELOPMENT_TASKS.md T2.1.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import webrtcvad

from openjarvis.voice.audio_io import SAMPLE_RATE

# webrtcvad solo acepta frames de 10/20/30ms a 8k/16k/32k/48kHz.
FRAME_MS = 30


@dataclass
class VadConfig:
    # Medido en este equipo (ver JARVIS_PROGRESS.md Fase 2): con ruido
    # ambiente real, aggressiveness=0..2 marcaba 70-85% de los frames como
    # "habla" (falsos positivos); aggressiveness=3 lo bajo a ~15%. No es un
    # valor universal -- depende del microfono/ambiente -- pero es el mejor
    # punto de partida medido, no asumido (ver CLAUDE.md "medir, no asumir").
    aggressiveness: int = 3  # 0 (permisivo) .. 3 (agresivo filtrando ruido)
    silence_timeout_ms: int = 500  # T2.1: cortar la escucha <500ms tras silencio
    max_utterance_ms: int = 15000  # tope de seguridad por turno
    min_speech_ms: int = 150  # ignora clics/ruido brevisimo como "habla"


@dataclass
class VadStats:
    """Desglose de a donde se fue el tiempo de `segment_utterance`.

    `wait_for_speech_seconds` es, en la practica, tiempo de reaccion del
    usuario (cuanto tardo en empezar a hablar tras "Escuchando...") -- no
    es overhead de procesamiento que se pueda optimizar, es el propio
    diseno de un asistente por voz. Se reporta por separado precisamente
    para no confundirlo con latencia real de CPU.
    """

    wait_for_speech_seconds: float = 0.0
    speech_seconds: float = 0.0
    trailing_silence_seconds: float = 0.0
    discarded_noise_bursts: int = 0


class VoiceActivityDetector:
    def __init__(self, config: VadConfig | None = None) -> None:
        self.config = config or VadConfig()
        self._vad = webrtcvad.Vad(self.config.aggressiveness)

    def is_speech(self, frame: bytes, sample_rate: int = SAMPLE_RATE) -> bool:
        return self._vad.is_speech(frame, sample_rate)


def segment_utterance(
    frames: Iterator[bytes],
    vad: VoiceActivityDetector,
    sample_rate: int = SAMPLE_RATE,
) -> tuple[bytes, VadStats]:
    """Consume frames hasta completar UN turno de habla y lo devuelve.

    Espera a que empiece el habla (ignora silencio inicial indefinidamente),
    luego acumula frames hasta `silence_timeout_ms` de silencio continuo o
    `max_utterance_ms` total. Devuelve (PCM16LE del turno sin la cola de
    silencio final, VadStats). Si el habla detectada es mas corta que
    `min_speech_ms` (ruido puntual), se descarta y se vuelve a esperar.
    """
    cfg = vad.config
    silence_limit_frames = max(1, cfg.silence_timeout_ms // FRAME_MS)
    max_frames = max(1, cfg.max_utterance_ms // FRAME_MS)
    min_speech_frames = max(1, cfg.min_speech_ms // FRAME_MS)
    frame_s = FRAME_MS / 1000
    discarded = 0

    while True:
        collected: list[bytes] = []
        silence_run = 0
        speech_frames = 0
        wait_frames = 0
        started = False

        for frame in frames:
            speaking = vad.is_speech(frame, sample_rate)

            if not started:
                if speaking:
                    started = True
                    collected.append(frame)
                    speech_frames += 1
                else:
                    wait_frames += 1
                continue

            collected.append(frame)
            if speaking:
                silence_run = 0
                speech_frames += 1
            else:
                silence_run += 1
                if silence_run >= silence_limit_frames:
                    break

            if len(collected) >= max_frames:
                break

        if not started:
            # el iterador se agoto sin habla (no deberia pasar con un stream real)
            continue

        if speech_frames < min_speech_frames:
            discarded += 1
            continue  # ruido puntual, descartar y volver a esperar

        # recorta la cola de silencio que disparo el corte
        trimmed = (
            collected[: len(collected) - silence_run] if silence_run else collected
        )
        stats = VadStats(
            wait_for_speech_seconds=wait_frames * frame_s,
            speech_seconds=len(trimmed) * frame_s,
            trailing_silence_seconds=silence_run * frame_s,
            discarded_noise_bursts=discarded,
        )
        return b"".join(trimmed), stats
