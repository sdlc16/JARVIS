"""Pipeline de voz completo (T2.4): VAD -> STT -> agente -> TTS -> audio.

Reutiliza el agente/motor/seguridad existentes (`openjarvis.cli.ask`,
`openjarvis.security`) en vez de reimplementar el camino de inferencia --
ver CLAUDE.md, principio de reutilizacion. Lo unico nuevo aqui es el
pegamento de audio real (captura/VAD/STT/TTS/reproduccion).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from openjarvis.core.events import EventBus
from openjarvis.core.types import Message, Role
from openjarvis.voice import audio_io
from openjarvis.voice.vad import (
    FRAME_MS,
    VadConfig,
    VadStats,
    VoiceActivityDetector,
    segment_utterance,
)

logger = logging.getLogger(__name__)

# Instruccion breve para que las respuestas suenen a conversacion hablada
# en vez de a un ensayo escrito -- medido en la primera prueba real: una
# respuesta larga alarga directamente la sintesis+reproduccion de TTS.
# El modelo puede ampliar si el usuario se lo pide explicitamente.
VOICE_STYLE_HINT = (
    "Responde en espanol de forma breve, natural y directa, como en una "
    "conversacion hablada (1-3 frases). Si el usuario pide mas detalle "
    "explicitamente, amplia la respuesta."
)

DEFAULT_VOICE_MAX_TOKENS = 200


@dataclass
class TurnLatency:
    # VAD: desglosado porque la mayor parte suele ser tiempo de reaccion
    # del propio usuario (cuanto tarda en empezar a hablar), no overhead.
    vad_wait_for_speech_seconds: float = 0.0
    vad_speech_seconds: float = 0.0
    vad_trailing_silence_seconds: float = 0.0

    stt_seconds: float = 0.0
    agent_seconds: float = 0.0

    # TTS: "stream" (Kokoro) solapa sintesis+reproduccion; "batch" (otros
    # backends) las hace secuenciales. tts_detail trae el desglose crudo.
    tts_mode: str = ""
    tts_time_to_first_audio_seconds: float = 0.0
    tts_total_seconds: float = 0.0
    tts_detail: dict = field(default_factory=dict)

    @property
    def vad_seconds(self) -> float:
        return (
            self.vad_wait_for_speech_seconds
            + self.vad_speech_seconds
            + self.vad_trailing_silence_seconds
        )

    @property
    def total_seconds(self) -> float:
        return (
            self.vad_seconds
            + self.stt_seconds
            + self.agent_seconds
            + self.tts_total_seconds
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
        warm_up: bool = True,
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

        # Carga los modelos AHORA, no en el primer turno: asi el coste de
        # arranque (descarga/carga en memoria, ver JARVIS_PROGRESS.md) no
        # se mete dentro de la latencia del primer turno real, y los
        # turnos siguientes de la misma sesion ya los reutilizan (quedan
        # residentes en self._stt/self._tts mientras el proceso viva).
        self.warm_up_seconds: dict[str, float] = {}
        if warm_up:
            self.warm_up_seconds = self._warm_up()

    def _warm_up(self) -> dict[str, float]:
        timings: dict[str, float] = {}

        t0 = time.monotonic()
        ensure_model = getattr(self._stt, "_ensure_model", None)
        if ensure_model is not None:
            ensure_model()
        timings["stt_load_s"] = time.monotonic() - t0

        t0 = time.monotonic()
        ensure_pipeline = getattr(self._tts, "_ensure_pipeline", None)
        if ensure_pipeline is not None:
            ensure_pipeline()
        timings["tts_load_s"] = time.monotonic() - t0

        return timings

    @property
    def vad_config(self) -> VadConfig:
        return self._vad.config

    def listen_once(self) -> tuple[bytes, VadStats]:
        """Espera y captura UN turno de habla real del microfono.

        Bloquea hasta que el VAD detecta inicio y fin de habla (silencio
        >= config.silence_timeout_ms). Ver VadStats para el desglose de
        a donde se fue el tiempo (la mayor parte suele ser al propio
        usuario tardando en empezar a hablar, no overhead de computo).
        """
        frames = audio_io.record_frames(frame_ms=FRAME_MS)
        return segment_utterance(frames, self._vad)

    def transcribe(self, pcm: bytes) -> tuple[str, str | None, float | None]:
        """PCM crudo -> (texto, idioma, confianza)."""
        wav_bytes = audio_io.pcm_to_wav_bytes(pcm)
        result = self._stt.transcribe(wav_bytes, format="wav", language="es")
        return result.text, result.language, result.confidence

    def ask_agent(
        self,
        text: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = DEFAULT_VOICE_MAX_TOKENS,
        style_hint: str = VOICE_STYLE_HINT,
    ) -> str:
        """Texto transcrito -> respuesta del agente JARVIS (reutiliza cli.ask)."""
        from openjarvis.cli._tool_names import resolve_tool_names
        from openjarvis.cli.ask import _run_agent

        augmented_text = f"{style_hint}\n\n{text}" if style_hint else text

        default_agent = (self._config.agent.default_agent or "").strip()
        agent_name = self._agent_name or default_agent
        if not agent_name:
            result = self._engine.generate(
                [Message(role=Role.USER, content=augmented_text)],
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
            augmented_text,
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

    def speak(self, text: str) -> tuple[str, float, float, dict]:
        """Respuesta -> TTS real -> reproduccion por los altavoces reales.

        Devuelve (modo, segundos_hasta_primer_audio, segundos_totales, detalle).
        Usa streaming (sintesis y reproduccion solapadas) cuando el backend
        lo soporta (Kokoro); si no, cae al camino sintetizar-todo-y-reproducir.
        """
        tts_kwargs: dict = {}
        if self._tts_voice_id:
            tts_kwargs["voice_id"] = self._tts_voice_id

        stream_fn = getattr(self._tts, "synthesize_stream", None)
        if stream_fn is not None:
            sample_rate = getattr(self._tts, "sample_rate", 24000)
            t0 = time.monotonic()
            timing = {"first_chunk_s": None, "chunks": 0}

            def _timed_chunks():
                for chunk in stream_fn(text, **tts_kwargs):
                    if timing["first_chunk_s"] is None:
                        timing["first_chunk_s"] = time.monotonic() - t0
                    timing["chunks"] += 1
                    yield chunk

            audio_io.play_pcm_stream(_timed_chunks(), sample_rate=sample_rate)
            total_s = time.monotonic() - t0
            first_audio_s = timing["first_chunk_s"] or total_s
            return "stream", first_audio_s, total_s, timing

        t0 = time.monotonic()
        tts_result = self._tts.synthesize(text, output_format="wav", **tts_kwargs)
        synth_s = time.monotonic() - t0
        t0 = time.monotonic()
        audio_io.play_wav_bytes(tts_result.audio)
        playback_s = time.monotonic() - t0
        detail = dict(tts_result.metadata or {})
        detail["playback_s"] = playback_s
        return "batch", synth_s, synth_s + playback_s, detail

    def run_turn(
        self,
        *,
        temperature: float = 0.7,
        max_tokens: int = DEFAULT_VOICE_MAX_TOKENS,
    ) -> VoiceTurnResult:
        """Un ciclo completo: escuchar -> transcribir -> responder -> hablar."""
        latency = TurnLatency()

        pcm, vad_stats = self.listen_once()
        latency.vad_wait_for_speech_seconds = vad_stats.wait_for_speech_seconds
        latency.vad_speech_seconds = vad_stats.speech_seconds
        latency.vad_trailing_silence_seconds = vad_stats.trailing_silence_seconds

        t0 = time.monotonic()
        text, language, confidence = self.transcribe(pcm)
        latency.stt_seconds = time.monotonic() - t0

        t0 = time.monotonic()
        response_text = self.ask_agent(
            text, temperature=temperature, max_tokens=max_tokens
        )
        latency.agent_seconds = time.monotonic() - t0

        if response_text.strip():
            mode, first_audio_s, total_s, detail = self.speak(response_text)
            latency.tts_mode = mode
            latency.tts_time_to_first_audio_seconds = first_audio_s
            latency.tts_total_seconds = total_s
            latency.tts_detail = detail

        return VoiceTurnResult(
            transcript=text,
            response_text=response_text,
            latency=latency,
            language=language,
            stt_confidence=confidence,
            audio_played=bool(response_text.strip()),
        )
