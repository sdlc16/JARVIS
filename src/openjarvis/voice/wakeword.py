"""Palabra de activacion local ("Hey Jarvis") con openWakeWord (ONNX, CPU).

Sin nube y sin grabar: solo se procesan tramos de 80 ms en memoria y se descartan.
openWakeWord espera PCM int16 a 16 kHz en bloques multiplos de 80 ms (1280
muestras). Se exige que la puntuacion supere el umbral en ``patience`` bloques
consecutivos para reducir falsos positivos de un solo bloque.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

CHUNK_SAMPLES = 1280  # 80 ms a 16 kHz
CHUNK_BYTES = CHUNK_SAMPLES * 2


_ALIASES = {"jarvis": {"harbys", "jarbys", "yarbis", "jarbis"}}


def matches_wake_word(text: str, word: str = "jarvis", min_ratio: float = 0.75) -> bool:
    """Modo STT: ¿aparece la palabra de activacion (con errores tipicos de Whisper)?

    Umbral medido: acepta jarvis/yarvis/charvis/jarbis (>=0.77) y rechaza
    javier/marvin/travis/servicio (<=0.67).
    """
    import difflib
    import re
    import unicodedata

    word = word.lower()
    plain = unicodedata.normalize("NFKD", (text or "").lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    for w in re.findall(r"[a-z]+", plain):
        if w == word or w in _ALIASES.get(word, ()):
            return True
        if len(w) >= 4 and difflib.SequenceMatcher(None, w, word).ratio() >= min_ratio:
            return True
    return False


class WakeWordUnavailable(RuntimeError):
    """openWakeWord o su modelo no estan instalados."""


@dataclass(slots=True)
class WakeResult:
    detected: bool
    score: float  # puntuacion maxima observada
    chunks: int  # bloques de 80 ms consumidos
    detected_at_chunk: int | None = None


class WakeWordDetector:
    def __init__(
        self,
        model: str = "hey_jarvis",
        threshold: float = 0.5,
        patience: int = 2,
        engine: object | None = None,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold debe estar en (0, 1]")
        if patience < 1:
            raise ValueError("patience debe ser >= 1")
        self.model_name = model
        self.threshold = threshold
        self.patience = patience
        self._engine = engine if engine is not None else self._load(model)
        self._streak = 0

    @staticmethod
    def _load(model: str) -> object:
        try:
            from openwakeword.model import Model
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise WakeWordUnavailable(
                "Falta openwakeword (uv pip install openwakeword)."
            ) from exc
        try:
            return Model(wakeword_models=[model], inference_framework="onnx")
        except Exception as exc:  # noqa: BLE001 - modelo sin descargar, etc.
            raise WakeWordUnavailable(
                f"No se pudo cargar el modelo '{model}': {exc}. Descargalo con "
                "openwakeword.utils.download_models()."
            ) from exc

    def reset(self) -> None:
        self._streak = 0
        reset = getattr(self._engine, "reset", None)
        if reset is not None:
            reset()

    def score_chunk(self, chunk: bytes) -> float:
        """Puntuacion (0-1) de un bloque de 80 ms."""
        if len(chunk) != CHUNK_BYTES:
            raise ValueError(f"Se esperan {CHUNK_BYTES} bytes, llegaron {len(chunk)}")
        out = self._engine.predict(np.frombuffer(chunk, dtype=np.int16))
        return float(max(out.values())) if out else 0.0

    def feed(self, chunk: bytes) -> tuple[bool, float]:
        """Procesa un bloque. Devuelve (activado, puntuacion)."""
        score = self.score_chunk(chunk)
        self._streak = self._streak + 1 if score >= self.threshold else 0
        return self._streak >= self.patience, score

    def listen(
        self, frames: Iterable[bytes], max_chunks: int | None = None
    ) -> WakeResult:
        """Consume tramos PCM de cualquier tamano hasta detectar la palabra."""
        self.reset()
        buf = bytearray()
        best, n = 0.0, 0
        for frame in frames:
            buf += frame
            while len(buf) >= CHUNK_BYTES:
                chunk, buf = bytes(buf[:CHUNK_BYTES]), buf[CHUNK_BYTES:]
                hit, score = self.feed(chunk)
                best, n = max(best, score), n + 1
                if hit:
                    return WakeResult(True, best, n, n)
                if max_chunks is not None and n >= max_chunks:
                    return WakeResult(False, best, n)
        return WakeResult(False, best, n)
