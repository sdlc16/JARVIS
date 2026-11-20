"""Deteccion de actividad de voz y cierre de turno (T2.1) via webrtcvad.

Se mantiene `webrtcvad` (extension C diminuta, sin modelo) pero el cierre
de turno ya no es "N frames seguidos sin voz": las pruebas reales y
controladas mostraron que eso cortaba la frase en la primera pausa natural
(ver JARVIS_PROGRESS.md). El diseño actual:

* **Compuerta de energia adaptativa**: un frame cuenta como voz solo si
  webrtcvad lo marca Y su nivel supera el suelo de ruido medido + un
  margen. webrtcvad por si solo marcaba como "habla" el 52-69 % del ruido
  ambiente real de este equipo (-62 dBFS) con agresividad 0-2, y con
  agresividad 3 perdia el 84 % de la voz suave (-50 dBFS).
* **Histeresis de inicio**: hacen falta `onset_frames` de voz entre los
  ultimos `onset_window` frames (un solo frame ruidoso no abre el turno).
* **Pre-roll**: se conservan `preroll_ms` de audio ANTERIOR al inicio
  detectado, para no perder la primera silaba.
* **Silencio final configurable** (por defecto 1200 ms) que distingue
  pausas internas (que se conservan) del fin real del turno, y una cola
  `tail_ms` que se mantiene tras la ultima voz.
* **Fin clasificado** (`EndReason`) y `VadStats` con niveles y pausas, para
  distinguir silencio, ausencia de voz, tope de duracion y captura rota.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum

import numpy as np
import webrtcvad

from openjarvis.voice.audio_io import (
    DEAD_DBFS,
    SAMPLE_RATE,
    audio_levels,
    frame_dbfs,
)

# webrtcvad solo acepta frames de 10/20/30ms a 8k/16k/32k/48kHz.
FRAME_MS = 30
_PAUSE_MIN_S = 0.25  # pausas internas menores no se cuentan como "pausa"


class EndReason(str, Enum):
    SILENCE = "silence"  # fin normal: silencio final >= silence_timeout_ms
    MAX_DURATION = "max_duration"  # corte por tope de seguridad del turno
    NO_SPEECH = "no_speech_timeout"  # nadie hablo dentro del tiempo maximo
    CAPTURE_ENDED = "capture_ended"  # el flujo de audio se agoto/corto


@dataclass
class VadConfig:
    """Parametros del VAD. Configurables en ``[voice]`` de config.toml."""

    # 2 + compuerta de energia: medido sobre voz sintetica mezclada con el
    # ruido real de este equipo (ver JARVIS_PROGRESS.md). aggr=3 pierde voz
    # suave; 0-2 sin compuerta marcan el ruido como habla.
    aggressiveness: int = 2
    # 1400 ms: una pausa interna de hasta ~1.3 s no parte la frase (medido con
    # clips recortados: ver JARVIS_PROGRESS.md); cuesta ~0.9 s mas de espera
    # por turno que los 500 ms anteriores. Ajustable en [voice] / --silence-ms.
    silence_timeout_ms: int = 1400  # silencio FINAL que cierra el turno
    max_utterance_ms: int = 30000  # tope de seguridad por turno
    no_speech_timeout_ms: int = 15000  # espera maxima a que alguien hable (0=∞)
    min_speech_ms: int = 250  # voz total minima para aceptar el turno
    min_voiced_ratio: float = 0.2  # fraccion minima de frames con voz
    preroll_ms: int = 300  # audio previo al inicio que se conserva
    tail_ms: int = 300  # silencio que se conserva tras la ultima voz
    onset_frames: int = 3  # frames con voz (de onset_window) para abrir turno
    onset_window: int = 5
    energy_gate: bool = True
    # voz = suelo de ruido + este margen. 6 dB: con 8-10 dB la voz suave
    # (-45/-50 dBFS) perdia bordes y la frase se partia (62-64 % capturado);
    # con 6 dB se conserva completa y el ruido real sigue rechazado.
    gate_margin_db: float = 6.0


class NoiseFloorTracker:
    """Suelo de ruido adaptativo: percentil bajo del nivel de frames recientes.

    Solo se alimenta con frames SIN turno activo (para que la propia voz no
    suba el suelo) y persiste entre turnos de una misma sesion.
    """

    def __init__(
        self,
        window_frames: int = 200,
        percentile: float = 10.0,
        default_db: float = -60.0,
        min_frames: int = 10,
        floor_min_db: float = -80.0,
        floor_max_db: float = -40.0,
    ) -> None:
        self._levels: deque[float] = deque(maxlen=window_frames)
        self._percentile = percentile
        self._default = default_db
        self._min_frames = min_frames
        self._lo, self._hi = floor_min_db, floor_max_db
        self._cached = default_db
        self._since = 0

    def update(self, db: float) -> None:
        if db < DEAD_DBFS:  # ceros digitales (arranque/micro mudo): no es ruido real
            return
        self._levels.append(db)
        self._since += 1
        if len(self._levels) >= self._min_frames and self._since >= 5:
            self._since = 0
            p = float(np.percentile(np.asarray(self._levels), self._percentile))
            self._cached = min(self._hi, max(self._lo, p))

    @property
    def floor_db(self) -> float:
        return self._cached

    @property
    def calibrated(self) -> bool:
        """True cuando ya hay frames vivos suficientes para fiarse del suelo."""
        return len(self._levels) >= self._min_frames


@dataclass
class VadStats:
    """Desglose de a donde se fue el tiempo y como era la señal.

    `wait_for_speech_seconds` es, en la practica, tiempo de reaccion del
    usuario (cuanto tardo en empezar a hablar) -- no overhead de computo.
    """

    end_reason: EndReason = EndReason.SILENCE
    wait_for_speech_seconds: float = 0.0
    speech_seconds: float = 0.0  # duracion del turno capturado (con cola)
    voiced_seconds: float = 0.0  # tiempo con voz detectada dentro del turno
    trailing_silence_seconds: float = 0.0  # silencio final que cerro el turno
    longest_pause_seconds: float = 0.0  # pausa interna mas larga conservada
    pause_count: int = 0  # pausas internas >= 250 ms conservadas
    voiced_ratio: float = 0.0
    discarded_noise_bursts: int = 0
    dead_frame_fraction: float = 0.0  # frames de ceros digitales (micro mudo)
    max_idle_dbfs: float = -120.0  # nivel maximo oido mientras esperaba voz
    preroll_seconds: float = 0.0
    peak_dbfs: float = -120.0
    rms_dbfs: float = -120.0
    noise_floor_dbfs: float = -120.0
    frames_seen: int = 0


class VoiceActivityDetector:
    def __init__(self, config: VadConfig | None = None) -> None:
        self.config = config or VadConfig()
        self._vad = webrtcvad.Vad(self.config.aggressiveness)
        self.noise_floor = NoiseFloorTracker()

    def is_speech(self, frame: bytes, sample_rate: int = SAMPLE_RATE) -> bool:
        """Decision cruda de webrtcvad (sin compuerta de energia)."""
        return self._vad.is_speech(frame, sample_rate)

    def is_voiced(
        self, frame: bytes, sample_rate: int = SAMPLE_RATE, db: float | None = None
    ) -> bool:
        """Decision final: webrtcvad Y nivel sobre el suelo de ruido + margen."""
        if not self._vad.is_speech(frame, sample_rate):
            return False
        if not self.config.energy_gate:
            return True
        level = frame_dbfs(frame) if db is None else db
        return level >= self.noise_floor.floor_db + self.config.gate_margin_db


def segment_utterance(
    frames: Iterator[bytes],
    vad: VoiceActivityDetector,
    sample_rate: int = SAMPLE_RATE,
) -> tuple[bytes, VadStats]:
    """Consume frames hasta completar UN turno de habla.

    Devuelve (PCM16LE del turno, VadStats). Nunca bloquea indefinidamente si
    `no_speech_timeout_ms` > 0, y nunca entra en bucle activo si el flujo
    se agota: en esos casos devuelve PCM vacio con el `EndReason` que lo
    explica (NO_SPEECH / CAPTURE_ENDED). Una captura rota se manifiesta como
    `CaptureError` desde el iterador (ver `audio_io.MicStream`).
    """
    cfg = vad.config
    frame_s = FRAME_MS / 1000
    silence_frames = max(1, cfg.silence_timeout_ms // FRAME_MS)
    max_frames = max(1, cfg.max_utterance_ms // FRAME_MS)
    min_voiced_frames = max(1, cfg.min_speech_ms // FRAME_MS)
    tail_frames = max(0, cfg.tail_ms // FRAME_MS)
    preroll_frames = max(0, cfg.preroll_ms // FRAME_MS)
    no_speech_frames = cfg.no_speech_timeout_ms // FRAME_MS  # 0 = sin limite
    pause_min_frames = max(1, int(_PAUSE_MIN_S / frame_s))

    stats = VadStats()
    preroll: deque[bytes] = deque(maxlen=max(1, preroll_frames))
    recent: deque[bool] = deque(maxlen=max(1, cfg.onset_window))
    confirm: deque[bool] = deque(maxlen=3)  # evita que 1 frame suelto reinicie
    collected: list[bytes] = []
    active = False
    voiced_frames = 0
    silence_run = 0
    pause_frames = 0  # racha de silencio interna en curso
    idle_frames = 0  # frames esperando voz (acumula entre descartes)
    lead_idle = 0  # frames de espera antes del primer frame con voz

    dead_frames = 0

    def is_speech_turn(speech_part_frames: int) -> bool:
        """¿Lo acumulado es habla (y no una rafaga de ruido)?"""
        if voiced_frames < min_voiced_frames:
            return False
        return voiced_frames / max(1, speech_part_frames) >= cfg.min_voiced_ratio

    def discard_noise_burst() -> bool:
        """Descarta lo acumulado y vuelve a esperar. True si vencio el plazo."""
        nonlocal active, collected, idle_frames
        stats.discarded_noise_bursts += 1
        idle_frames += len(collected)
        active = False
        collected = []
        preroll.clear()
        recent.clear()
        return bool(no_speech_frames and idle_frames >= no_speech_frames)

    def finish(reason: EndReason, discard: bool = False) -> tuple[bytes, VadStats]:
        stats.end_reason = reason
        stats.noise_floor_dbfs = vad.noise_floor.floor_db
        stats.dead_frame_fraction = dead_frames / max(1, stats.frames_seen)
        if discard or not collected:
            return b"", stats
        trailing = silence_run if reason == EndReason.SILENCE else 0
        keep = collected
        if trailing > tail_frames:
            keep = collected[: len(collected) - (trailing - tail_frames)]
        pcm = b"".join(keep)
        lv = audio_levels(pcm, sample_rate)
        stats.speech_seconds = lv.duration_seconds
        stats.peak_dbfs = lv.peak_dbfs
        stats.rms_dbfs = lv.rms_dbfs
        stats.voiced_seconds = voiced_frames * frame_s
        stats.trailing_silence_seconds = trailing * frame_s
        stats.voiced_ratio = voiced_frames / max(1, len(collected) - trailing)
        return pcm, stats

    for frame in frames:
        stats.frames_seen += 1
        db = frame_dbfs(frame)
        if db < DEAD_DBFS:
            dead_frames += 1

        if not active:
            # El suelo de ruido solo aprende de frames SIN turno activo.
            vad.noise_floor.update(db)
            stats.max_idle_dbfs = max(stats.max_idle_dbfs, db)
            voiced = vad.is_voiced(frame, sample_rate, db)
            if cfg.energy_gate and not vad.noise_floor.calibrated:
                # Aun no se conoce el suelo de ruido (primeros ~300 ms de la
                # sesion): sin esto, una sala ruidosa abriria un turno falso.
                voiced = False
            idle_frames += 1
            preroll.append(frame)
            recent.append(voiced)
            if sum(recent) >= cfg.onset_frames:
                active = True
                collected = list(preroll)
                voiced_frames = sum(recent)
                silence_run = 0
                pause_frames = 0
                confirm.clear()
                confirm.extend([True] * min(3, len(recent)))
                # frames de espera previos al primer frame con voz de la ventana
                first_voiced_age = len(recent) - next(
                    i for i, v in enumerate(recent) if v
                )
                lead_idle = max(0, idle_frames - first_voiced_age)
                stats.preroll_seconds = len(preroll) * frame_s
                continue
            if no_speech_frames and idle_frames >= no_speech_frames:
                stats.wait_for_speech_seconds = idle_frames * frame_s
                return finish(EndReason.NO_SPEECH, discard=True)
            continue

        # --- turno activo ---
        collected.append(frame)
        voiced = vad.is_voiced(frame, sample_rate, db)
        confirm.append(voiced)
        if voiced:
            voiced_frames += 1
        if sum(confirm) >= 2:  # voz confirmada: cierra la pausa en curso
            if pause_frames >= pause_min_frames:
                stats.pause_count += 1
                stats.longest_pause_seconds = max(
                    stats.longest_pause_seconds, pause_frames * frame_s
                )
            pause_frames = 0
            silence_run = 0
        else:
            silence_run += 1
            pause_frames += 1

        if silence_run >= silence_frames:
            if not is_speech_turn(len(collected) - silence_run):
                # rafaga de ruido, no habla: descartar y seguir esperando
                if discard_noise_burst():
                    stats.wait_for_speech_seconds = idle_frames * frame_s
                    return finish(EndReason.NO_SPEECH, discard=True)
                continue
            stats.wait_for_speech_seconds = lead_idle * frame_s
            return finish(EndReason.SILENCE)

        # Ruido a rafagas que mantiene "activo" un turno sin habla real: el
        # plazo de espera tambien rige aqui (medido con el micro real: un
        # turno sin hablar duraba 9.5 s con un limite de 6 s).
        if (
            no_speech_frames
            and idle_frames + len(collected) >= no_speech_frames
            and sum(confirm) < 2
            and not is_speech_turn(len(collected))
        ):
            discard_noise_burst()
            stats.wait_for_speech_seconds = idle_frames * frame_s
            return finish(EndReason.NO_SPEECH, discard=True)

        if len(collected) >= max_frames:
            if not is_speech_turn(len(collected)):  # tope alcanzado solo con ruido
                if discard_noise_burst():
                    stats.wait_for_speech_seconds = idle_frames * frame_s
                    return finish(EndReason.NO_SPEECH, discard=True)
                continue
            stats.wait_for_speech_seconds = lead_idle * frame_s
            return finish(EndReason.MAX_DURATION)

    # El iterador se agoto (captura finita o cortada).
    if active and is_speech_turn(len(collected)):
        stats.wait_for_speech_seconds = lead_idle * frame_s
        return finish(EndReason.CAPTURE_ENDED)
    stats.wait_for_speech_seconds = idle_frames * frame_s
    return finish(EndReason.CAPTURE_ENDED, discard=True)
