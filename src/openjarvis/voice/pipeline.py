"""Pipeline de voz completo (T2.4): VAD -> STT -> agente -> TTS -> audio.

Reutiliza el agente/motor/seguridad existentes (`openjarvis.cli.ask`,
`openjarvis.security`) en vez de reimplementar el camino de inferencia --
ver CLAUDE.md, principio de reutilizacion. Lo unico nuevo aqui es el
pegamento de audio real (captura/VAD/STT/TTS/reproduccion).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from openjarvis.core.events import EventBus
from openjarvis.core.types import Message, Role
from openjarvis.voice import audio_io
from openjarvis.voice.vad import (
    FRAME_MS,
    VadConfig,
    VoiceActivityDetector,
    segment_utterance,
)

logger = logging.getLogger(__name__)


@dataclass
class TurnLatency:
    vad_seconds: float = 0.0
    stt_seconds: float = 0.0
    agent_seconds: float = 0.0
    tts_seconds: float = 0.0
    playback_seconds: float = 0.0

    @property
    def total_seconds(self) -> float:
        return (
            self.vad_seconds
            + self.stt_seconds
            + self.agent_seconds
            + self.tts_seconds
            + self.playback_seconds
        )


@dataclass
class VoiceTurnResult:
    transcript: str
    response_text: str
    latency: TurnLatency
    language: str | None = None
    stt_confidence: float | None = None
    audio_played: bool = False


class VoicePipeline:
    """Un turno de voz: escuchar, transcribir, responder (agente), hablar."""

    def __init__(
        self,
        *,
        engine,
        model_name: str,
        config,
        agent_name: str = "",
        stt_backend: str = "faster-whisper",
        stt_model_size: str = "base",
        tts_backend: str = "kokoro",
        tts_lang_code: str = "e",
        tts_voice_id: str = "",
        vad_config: VadConfig | None = None,
    ) -> None:
        import openjarvis.speech  # noqa: F401 -- dispara el registro de backends
        from openjarvis.core.registry import SpeechRegistry, TTSRegistry
        from openjarvis.security import setup_security

        self._model_name = model_name
        self._agent_name = agent_name
        self._config = config
        self._bus = EventBus(record_history=True)

        sec = setup_security(config, engine, self._bus)
        self._engine = sec.engine
        self._capability_policy = sec.capability_policy

        # CPU explicito: la GPU (8GB) es solo para Ollama -- ver CLAUDE.md
        # "evitar cargar simultaneamente modelos innecesarios en VRAM".
        self._stt = SpeechRegistry.create(
            stt_backend, model_size=stt_model_size, device="cpu", compute_type="int8"
        )
        if tts_backend == "kokoro":
            self._tts = TTSRegistry.create(
                tts_backend, device="cpu", lang_code=tts_lang_code
            )
        else:
            self._tts = TTSRegistry.create(tts_backend)
        self._tts_voice_id = tts_voice_id

        self._vad = VoiceActivityDetector(vad_config)

    @property
    def vad_config(self) -> VadConfig:
        return self._vad.config

    def listen_once(self) -> tuple[bytes, float]:
        """Espera y captura UN turno de habla real del microfono.

        Devuelve (pcm16le_bytes, segundos_de_vad). Bloquea hasta que el VAD
        detecta inicio y fin de habla (silencio >= config.silence_timeout_ms).
        """
        t0 = time.monotonic()
        frames = audio_io.record_frames(frame_ms=FRAME_MS)
        pcm = segment_utterance(frames, self._vad)
        return pcm, time.monotonic() - t0

    def transcribe(self, pcm: bytes) -> tuple[str, str | None, float | None]:
        """PCM crudo -> (texto, idioma, confianza)."""
        wav_bytes = audio_io.pcm_to_wav_bytes(pcm)
        result = self._stt.transcribe(wav_bytes, format="wav", language="es")
        return result.text, result.language, result.confidence

    def ask_agent(
        self, text: str, *, temperature: float = 0.7, max_tokens: int = 512
    ) -> str:
        """Texto transcrito -> respuesta del agente JARVIS (reutiliza cli.ask)."""
        from openjarvis.cli._tool_names import resolve_tool_names
        from openjarvis.cli.ask import _run_agent

        default_agent = (self._config.agent.default_agent or "").strip()
        agent_name = self._agent_name or default_agent
        if not agent_name:
            result = self._engine.generate(
                [Message(role=Role.USER, content=text)],
                model=self._model_name,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return result.get("content", "")

        tool_names = resolve_tool_names(
            None,
            getattr(self._config.tools, "enabled", None),
            getattr(self._config.agent, "tools", None),
        )
        result = _run_agent(
            agent_name,
            text,
            self._engine,
            self._model_name,
            tool_names,
            self._config,
            self._bus,
            temperature,
            max_tokens,
            capability_policy=self._capability_policy,
        )
        return result.content

    def speak(self, text: str) -> float:
        """Respuesta -> TTS real -> reproduccion por los altavoces reales."""
        t0 = time.monotonic()
        tts_kwargs = {}
        if self._tts_voice_id:
            tts_kwargs["voice_id"] = self._tts_voice_id
        tts_result = self._tts.synthesize(text, output_format="wav", **tts_kwargs)
        audio_io.play_wav_bytes(tts_result.audio)
        return time.monotonic() - t0

    def run_turn(
        self, *, temperature: float = 0.7, max_tokens: int = 512
    ) -> VoiceTurnResult:
        """Un ciclo completo: escuchar -> transcribir -> responder -> hablar."""
        latency = TurnLatency()

        pcm, vad_s = self.listen_once()
        latency.vad_seconds = vad_s

        t0 = time.monotonic()
        text, language, confidence = self.transcribe(pcm)
        latency.stt_seconds = time.monotonic() - t0

        t0 = time.monotonic()
        response_text = self.ask_agent(
            text, temperature=temperature, max_tokens=max_tokens
        )
        latency.agent_seconds = time.monotonic() - t0

        latency.tts_seconds = (
            self.speak(response_text) if response_text.strip() else 0.0
        )

        return VoiceTurnResult(
            transcript=text,
            response_text=response_text,
            latency=latency,
            language=language,
            stt_confidence=confidence,
            audio_played=bool(response_text.strip()),
        )
