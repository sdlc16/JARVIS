"""Kokoro TTS backend — fully open-source, runs locally.

Requires the kokoro package: pip install kokoro
Falls back gracefully if not installed.
"""

from __future__ import annotations

import io
from typing import List

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult


@TTSRegistry.register("kokoro")
class KokoroTTSBackend(TTSBackend):
    """Kokoro TTS — local open-source voice synthesis."""

    backend_id = "kokoro"

    # Voces conocidas por idioma (codigo Kokoro -> lista de voice_id).
    # "e" = espanol. Kokoro no distingue variante latam/Espana dentro de
    # un mismo codigo de idioma -- ver JARVIS_PROGRESS.md T2.4 para la
    # evaluacion real de como suena cada una.
    _VOICES_BY_LANG = {
        "a": ["af_heart", "af_bella", "am_adam", "am_michael"],
        "e": ["ef_dora", "em_alex", "em_santa"],
    }

    def __init__(
        self, *, model_path: str = "", device: str = "auto", lang_code: str = "a"
    ) -> None:
        self._model_path = model_path
        self._device = device
        self._lang_code = lang_code
        self._pipeline = None

    def _ensure_pipeline(self) -> None:
        if self._pipeline is not None:
            return
        try:
            from kokoro import KPipeline

            self._pipeline = KPipeline(lang_code=self._lang_code)
        except ImportError:
            raise RuntimeError(
                "kokoro package not installed. Install with: pip install kokoro"
            )

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "wav",
    ) -> TTSResult:
        if not voice_id:
            voice_id = self._VOICES_BY_LANG.get(self._lang_code, ["af_heart"])[0]
        self._ensure_pipeline()
        import numpy as np
        import soundfile as sf

        samples = []
        for _, _, audio in self._pipeline(text, voice=voice_id, speed=speed):
            samples.append(audio)

        if not samples:
            return TTSResult(audio=b"", format=output_format, voice_id=voice_id)

        combined = np.concatenate(samples)
        buf = io.BytesIO()
        sf.write(buf, combined, 24000, format=output_format.upper())
        buf.seek(0)

        return TTSResult(
            audio=buf.read(),
            format=output_format,
            voice_id=voice_id,
            sample_rate=24000,
            duration_seconds=len(combined) / 24000,
            metadata={"backend": "kokoro"},
        )

    def available_voices(self) -> List[str]:
        return self._VOICES_BY_LANG.get(self._lang_code, ["af_heart"])

    def health(self) -> bool:
        try:
            self._ensure_pipeline()
            return True
        except RuntimeError:
            return False
