"""Barge-in: el usuario puede hablar por encima de JARVIS para interrumpirle.

Mientras suena la respuesta, un hilo escucha el microfono. Se interrumpe solo si
hay VOZ sostenida claramente por encima del ruido ambiente **y del eco medido de
los propios altavoces** (los primeros ``ignore_ms`` de reproduccion se usan para
medir cuanto se oye JARVIS en el micro). El tiempo se cuenta en tiempo de AUDIO
(frames), no de reloj, para que sea determinista y comprobable.

Al interrumpir, el micro NO se cierra: se entrega junto con los frames
anteriores al disparo (``Handoff``) para que el siguiente turno de escucha
continue sin perder ni una palabra del usuario.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import webrtcvad

from openjarvis.voice.audio_io import DEAD_DBFS, SAMPLE_RATE, frame_dbfs

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BargeInConfig:
    echo_delay_ms: int = 150  # latencia altavoz -> micro antes de medir el eco
    ignore_ms: int = 400  # audio vivo minimo medido antes de poder disparar
    adapt_ms: int = 1500  # un TTS que sube de volumen se adopta como eco tras esto
    sustain_ms: int = 300  # voz continua necesaria para interrumpir
    margin_db: float = 12.0  # sobre el ruido ambiente
    echo_margin_db: float = 6.0  # sobre el eco medido de los altavoces
    aggressiveness: int = 2
    preroll_ms: int = 600  # audio previo al disparo que se conserva
    frame_ms: int = 30


@dataclass
class Handoff:
    """Micro aun abierto + audio previo al disparo, para el siguiente turno."""

    mic: Any
    prefix: list[bytes] = field(default_factory=list)

    def close(self) -> None:
        close = getattr(self.mic, "close", None)
        if close is not None:
            close()


class BargeInMonitor:
    def __init__(
        self,
        capture: Callable[[], Any],
        noise_floor_db: float,
        config: BargeInConfig | None = None,
    ) -> None:
        self.cfg = config or BargeInConfig()
        self.noise_floor_db = noise_floor_db
        self._capture = capture
        self._mic: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.triggered = threading.Event()
        self.error: Exception | None = None
        self.echo_db: float = -120.0
        self.trigger_db: float | None = None
        self.trigger_at_ms: int | None = None
        self._prefix: list[bytes] = []
        self.stats = {"evaluated": 0, "speech": 0, "above": 0, "max_voiced_ms": 0}
        self._t_ms = 0  # tiempo de audio visto por el hilo (frames x frame_ms)
        self._play_t0_ms: int | None = None  # cuando empezo a sonar JARVIS

    # ------------------------------------------------------------------
    def playback_started(self) -> None:
        """Avisa de que JARVIS EMPIEZA a sonar (primer audio): ancla la medicion
        del eco. Antes de esto el monitor no puede disparar: sin audio propio no
        hay eco que medir y la sintesis tarda ~1-2 s en arrancar."""
        if self._play_t0_ms is None:
            self._play_t0_ms = self._t_ms

    def start(self, playing: bool = False) -> None:
        if playing:
            self._play_t0_ms = 0
        mic = self._capture()
        mic.__enter__()
        self._mic = mic
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        cfg = self.cfg
        vad = webrtcvad.Vad(cfg.aggressiveness)
        ring: deque[bytes] = deque(maxlen=max(1, cfg.preroll_ms // cfg.frame_ms))
        # Referencia de eco: niveles VIVOS (> DEAD_DBFS) desde que suena JARVIS,
        # sin contar los ultimos `sustain_ms` (ahi puede estar empezando el
        # usuario). No depende de cuanto tarde el audio en llegar al micro
        # (medido con PulseAudio real: ~1 s de buffer de paplay).
        history: deque[float] = deque(maxlen=max(1, 4000 // cfg.frame_ms))
        # (indice, nivel, parecia_voz): los frames normales entran en la referencia
        # tras `sustain_ms`; los que PARECEN voz (sobre el umbral) solo tras
        # `adapt_ms`, para que el usuario no contamine el eco antes de disparar y
        # aun asi se adapte un TTS que sube de volumen.
        pending: list[tuple[int, float, bool]] = []
        sustain_n = max(1, cfg.sustain_ms // cfg.frame_ms)
        adapt_n = max(sustain_n + 1, cfg.adapt_ms // cfg.frame_ms)
        idx = 0
        need = max(1, cfg.ignore_ms // cfg.frame_ms)
        voiced_ms = 0
        try:
            for frame in self._mic.frames():
                if self._stop.is_set():
                    return
                level = frame_dbfs(frame)
                ring.append(frame)
                self._t_ms += cfg.frame_ms
                t0 = self._play_t0_ms
                if t0 is None:
                    continue  # JARVIS aun no suena: nada que interrumpir
                rel = self._t_ms - t0
                if rel <= cfg.echo_delay_ms:
                    continue  # el audio aun no ha llegado al micro
                if level < DEAD_DBFS:
                    continue  # ceros digitales: el audio aun no ha llegado
                idx += 1
                keep = []
                for item in pending:
                    age = idx - item[0]
                    if age >= (adapt_n if item[2] else sustain_n):
                        history.append(item[1])
                    else:
                        keep.append(item)
                pending = keep
                if len(history) < need:
                    pending.append((idx, level, False))
                    continue  # eco aun sin medir: no se dispara a ciegas
                srt = sorted(history)
                self.echo_db = srt[min(len(srt) - 1, int(0.9 * len(srt)))]
                threshold = max(
                    self.noise_floor_db + cfg.margin_db,
                    self.echo_db + cfg.echo_margin_db,
                )
                pending.append((idx, level, level >= threshold))
                try:
                    speech = vad.is_speech(frame, SAMPLE_RATE)
                except Exception:  # noqa: BLE001 - frame invalido: no es voz
                    speech = False
                st = self.stats
                st["evaluated"] += 1
                st["speech"] += bool(speech)
                st["above"] += level >= threshold
                if speech and level >= threshold:
                    voiced_ms += cfg.frame_ms
                else:
                    voiced_ms = 0
                st["max_voiced_ms"] = max(st["max_voiced_ms"], voiced_ms)
                if voiced_ms >= cfg.sustain_ms:
                    self.trigger_db = level
                    self.trigger_at_ms = rel
                    self._prefix = list(ring)
                    self.triggered.set()
                    return
        except Exception as exc:  # noqa: BLE001 - un fallo del micro no corta el habla
            self.error = exc
            logger.warning("barge-in: fallo del monitor: %s", exc)

    def stop(self) -> Handoff | None:
        """Detiene la escucha. Si hubo interrupcion devuelve el micro abierto."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        mic, self._mic = self._mic, None
        if self.triggered.is_set() and mic is not None:
            return Handoff(mic, self._prefix)
        if mic is not None:
            try:
                mic.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        return None
