"""Kokoro TTS backend — fully open-source, runs locally.

Requires the kokoro package: pip install kokoro
Falls back gracefully if not installed.
"""

from __future__ import annotations

import io
import time
from collections.abc import Iterator
from typing import List

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult

KOKORO_SAMPLE_RATE = 24000


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
        self.sample_rate = KOKORO_SAMPLE_RATE

    def _ensure_pipeline(self) -> float:
        """Carga el pipeline si hace falta. Devuelve los segundos que tardo
        (0.0 si ya estaba cargado) -- para poder desglosar carga vs sintesis.
        """
        if self._pipeline is not None:
            return 0.0
        t0 = time.monotonic()
        try:
            from kokoro import KPipeline

            self._pipeline = KPipeline(lang_code=self._lang_code)
        except ImportError:
            raise RuntimeError(
                "kokoro package not installed. Install with: pip install kokoro"
            )
        return time.monotonic() - t0

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
        load_s = self._ensure_pipeline()
        import numpy as np
        import soundfile as sf

        t0 = time.monotonic()
        samples = [
            audio for _, _, audio in self._pipeline(text, voice=voice_id, speed=speed)
        ]
        synth_s = time.monotonic() - t0

        if not samples:
            return TTSResult(
                audio=b"",
                format=output_format,
                voice_id=voice_id,
                metadata={"backend": "kokoro", "load_s": load_s, "synth_s": synth_s},
            )

        t0 = time.monotonic()
        combined = np.concatenate(samples)
        buf = io.BytesIO()
        sf.write(buf, combined, KOKORO_SAMPLE_RATE, format=output_format.upper())
        buf.seek(0)
        audio_bytes = buf.read()
        encode_s = time.monotonic() - t0

        return TTSResult(
            audio=audio_bytes,
            format=output_format,
            voice_id=voice_id,
            sample_rate=KOKORO_SAMPLE_RATE,
            duration_seconds=len(combined) / KOKORO_SAMPLE_RATE,
            metadata={
                "backend": "kokoro",
                "load_s": load_s,
                "synth_s": synth_s,
                "encode_s": encode_s,
            },
        )

    # Por defecto Kokoro solo trocea en saltos de linea ('\n+'), asi que un
    # texto de una sola linea (lo habitual en una respuesta hablada) sale
    # como UN solo trozo -- sin beneficio real de streaming (medido: ver
    # JARVIS_PROGRESS.md, "primer_audio" == "total" para texto sin \n).
    # Forzamos el troceo por frase para que cada oracion se reproduzca en
    # cuanto esta lista, sin esperar a las siguientes.
    _SENTENCE_SPLIT_PATTERN = r"(?<=[.!?])\s+"

    def synthesize_stream(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        split_pattern: str | None = _SENTENCE_SPLIT_PATTERN,
    ) -> Iterator[bytes]:
        """Sintetiza y entrega PCM16LE crudo frase-a-frase segun se genera.

        Con `split_pattern` trocenado por oracion, Kokoro produce audio
        por frase en un generador -- esto expone ese streaming tal cual,
        en vez de esperar a que termine todo el texto y concatenar (que
        es lo que hace `synthesize`). Pensado para reproducirse con
        `audio_io.play_pcm_stream` mientras se sigue generando el resto,
        no para guardarse como un WAV completo.
        """
        if not voice_id:
            voice_id = self._VOICES_BY_LANG.get(self._lang_code, ["af_heart"])[0]
        self._ensure_pipeline()
        import numpy as np

        for _, _, audio in self._pipeline(
            text, voice=voice_id, speed=speed, split_pattern=split_pattern
        ):
            pcm16 = (np.asarray(audio) * 32767.0).astype(np.int16)
            yield pcm16.tobytes()

    def available_voices(self) -> List[str]:
        return self._VOICES_BY_LANG.get(self._lang_code, ["af_heart"])

    def health(self) -> bool:
        try:
            self._ensure_pipeline()
            return True
        except RuntimeError:
            return False
