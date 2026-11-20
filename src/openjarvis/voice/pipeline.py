"""Pipeline de voz completo (T2.4): VAD -> STT -> agente -> TTS -> audio.

Reutiliza el agente/motor/seguridad existentes (`openjarvis.cli.ask`,
`openjarvis.security`) en vez de reimplementar el camino de inferencia --
ver CLAUDE.md, principio de reutilizacion. Lo unico nuevo aqui es el
pegamento de audio real (captura/VAD/STT/TTS/reproduccion).

Cada turno termina siempre en un `TurnOutcome` explicito (nunca lanza por
un fallo esperado): asi se distingue ausencia de voz, transcripcion vacia,
fallo de captura, de STT, del agente (Ollama) y de TTS, y la sesion puede
reintentar en vez de morir.
"""

from __future__ import annotations

import logging
import re
import time
import wave
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

from openjarvis.core.events import EventBus
from openjarvis.core.types import Message, Role
from openjarvis.voice import audio_io, turnlog
from openjarvis.voice.audio_io import AudioLevels, CaptureError
from openjarvis.voice.vad import (
    FRAME_MS,
    EndReason,
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

# Frases que Whisper "alucina" sobre ruido/silencio breve. Solo se descartan
# si el habla detectada es muy corta (ver `looks_like_hallucination`).
_WHISPER_HALLUCINATIONS = frozenset(
    {
        "gracias",
        "gracias por ver el video",
        "gracias por ver",
        "subtitulos realizados por la comunidad de amaraorg",
        "suscribete",
        "adios",
        "hasta la proxima",
    }
)
_HALLUCINATION_MAX_VOICED_S = 1.0


class TurnOutcome(str, Enum):
    OK = "ok"
    NO_SPEECH = "no_speech"  # nadie hablo (o solo ruido) en el tiempo maximo
    EMPTY_TRANSCRIPT = "empty_transcript"  # hubo audio pero STT no saco texto
    CAPTURE_ERROR = "capture_error"  # el microfono/parec fallo
    STT_ERROR = "stt_error"  # faster-whisper lanzo una excepcion
    AGENT_ERROR = "agent_error"  # Ollama/agente fallo o respondio vacio
    TTS_ERROR = "tts_error"  # Kokoro/reproduccion fallo (texto si llega)


# El turno cuenta como completado aunque falle la voz de salida: la
# respuesta existe y se muestra como texto.
COMPLETED_OUTCOMES = frozenset({TurnOutcome.OK, TurnOutcome.TTS_ERROR})


@dataclass
class TurnLatency:
    # Captura: arranque del microfono (hasta la primera señal viva).
    capture_startup_seconds: float = 0.0
    # VAD: desglosado porque la mayor parte suele ser tiempo de reaccion
    # del propio usuario (cuanto tarda en empezar a hablar), no overhead.
    vad_wait_for_speech_seconds: float = 0.0
    vad_speech_seconds: float = 0.0
    vad_trailing_silence_seconds: float = 0.0

    stt_seconds: float = 0.0
    agent_seconds: float = 0.0

    # TTS: "stream" (Kokoro) solapa sintesis+reproduccion; "batch" (otros
    # backends) las hace secuenciales. tts_detail trae el desglose crudo
    # (trozos, huecos entre frases, intervalos de sintesis).
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
    outcome: TurnOutcome = TurnOutcome.OK
    transcript: str = ""
    response_text: str = ""
    latency: TurnLatency = field(default_factory=TurnLatency)
    language: str | None = None
    language_probability: float | None = None  # NO es confianza del texto
    audio: AudioLevels | None = None
    vad: VadStats | None = None
    error: str = ""  # excepcion/mensaje tecnico
    probable_cause: str = ""  # explicacion para el usuario
    hint: str = ""  # sugerencia opcional (p.ej. subir el silencio final)

    @property
    def ok(self) -> bool:
        return self.outcome in COMPLETED_OUTCOMES

    @property
    def audio_played(self) -> bool:
        return self.outcome == TurnOutcome.OK


# ---------------------------------------------------------------------------
# Diagnostico de fallos (funciones puras, testeables)
# ---------------------------------------------------------------------------


def diagnose_no_speech(stats: VadStats, signal_dead_at_start: bool = False) -> str:
    if signal_dead_at_start or stats.dead_frame_fraction > 0.9:
        return (
            "El microfono entrega silencio digital (ceros): parece silenciado o "
            "desactivado en Windows (Configuracion > Sistema > Sonido > Entrada)."
        )
    if stats.max_idle_dbfs < -55:
        return (
            f"Se oyo muy poco nivel (maximo {stats.max_idle_dbfs:.0f} dBFS): sube "
            "el volumen de entrada del microfono en Windows o acercate mas."
        )
    return (
        f"No se detecto voz en {stats.wait_for_speech_seconds:.0f} s "
        f"(ruido ambiente ~{stats.noise_floor_dbfs:.0f} dBFS, sin habla)."
    )


def diagnose_empty_transcript(stats: VadStats, levels: AudioLevels) -> str:
    if levels.duration_seconds < 0.5:
        return (
            f"Audio demasiado corto ({levels.duration_seconds:.2f} s): solo se "
            "capto una rafaga breve, no una frase."
        )
    if levels.peak_dbfs < -45:
        return (
            f"Nivel de voz muy bajo (pico {levels.peak_dbfs:.0f} dBFS): sube el "
            "volumen de entrada del microfono o habla mas cerca."
        )
    if levels.clipped_fraction > 0.01:
        return (
            f"Audio saturado ({100 * levels.clipped_fraction:.1f}% de muestras "
            "al maximo): baja la ganancia del microfono."
        )
    if stats.voiced_ratio < 0.3:
        return (
            f"Poca voz en el audio ({100 * stats.voiced_ratio:.0f}% de frames): "
            "probablemente ruido, no habla."
        )
    return (
        f"Whisper no reconocio palabras en {levels.duration_seconds:.1f} s de "
        f"audio con nivel normal (pico {levels.peak_dbfs:.0f} dBFS): ¿habla "
        "ininteligible o en otro idioma?"
    )


def pause_hint(stats: VadStats, silence_timeout_ms: int) -> str:
    """Avisa si una pausa interna estuvo cerca de cortar el turno."""
    limit = silence_timeout_ms / 1000
    if stats.longest_pause_seconds >= 0.75 * limit:
        return (
            f"Tu pausa mas larga ({stats.longest_pause_seconds:.1f} s) estuvo "
            f"cerca del limite ({limit:.1f} s): si te corta frases, sube "
            "voice.silence_timeout_ms (--silence-ms)."
        )
    return ""


def looks_like_hallucination(text: str, voiced_seconds: float) -> bool:
    """Whisper inventa 'Gracias.' & co. sobre ruido breve: no es una orden."""
    if voiced_seconds >= _HALLUCINATION_MAX_VOICED_S:
        return False
    norm = re.sub(
        r"[^a-z0-9 ]", "", text.lower().translate(str.maketrans("áéíóúüñ", "aeiouun"))
    ).strip()
    return norm in _WHISPER_HALLUCINATIONS


class VoicePipeline:
    """Un turno de voz: escuchar, transcribir, responder (agente), hablar."""

    def __init__(
        self,
        *,
        engine=None,
        model_name: str = "",
        config=None,
        agent_name: str = "",
        stt_backend: str = "faster-whisper",
        stt_model_size: str = "base",
        tts_backend: str = "kokoro",
        tts_lang_code: str = "e",
        tts_voice_id: str = "",
        vad_config: VadConfig | None = None,
        warm_up: bool = True,
        # --- inyeccion (tests / alternativas) ---
        stt=None,
        tts=None,
        capture: Callable[[], object] | None = None,
        play_stream: Callable[..., None] | None = None,
        play_wav: Callable[[bytes], None] | None = None,
        agent_fn: Callable[[str, float, int], str] | None = None,
        stt_prompt: str = "",
        style_hint: str | None = None,
        run_id: str | None = None,
        log_path: Path | None = None,
        log_turns: bool = True,
        debug_audio_dir: str | Path | None = None,
    ) -> None:
        self._model_name = model_name
        self._agent_name = agent_name
        self._config = config
        self._bus = EventBus(record_history=True)
        self.run_id = run_id or turnlog.new_run_id()
        self._turn_index = 0
        self._log_path = log_path
        self._log_turns = log_turns
        self._debug_audio_dir = Path(debug_audio_dir) if debug_audio_dir else None

        self._agent_fn = agent_fn
        self._stt_prompt = stt_prompt
        self._style_hint = VOICE_STYLE_HINT if style_hint is None else style_hint
        self._engine = engine
        self._capability_policy = None
        if agent_fn is None:
            from openjarvis.security import setup_security

            sec = setup_security(config, engine, self._bus)
            self._engine = sec.engine
            self._capability_policy = sec.capability_policy

        if stt is not None:
            self._stt = stt
        else:
            import openjarvis.speech  # noqa: F401 -- dispara el registro
            from openjarvis.core.registry import SpeechRegistry

            # CPU explicito: la GPU (8GB) es solo para Ollama -- ver
            # CLAUDE.md "evitar cargar simultaneamente modelos innecesarios
            # en VRAM".
            self._stt = SpeechRegistry.create(
                stt_backend,
                model_size=stt_model_size,
                device="cpu",
                compute_type="int8",
            )
        if tts is not None:
            self._tts = tts
        else:
            import openjarvis.speech  # noqa: F401
            from openjarvis.core.registry import TTSRegistry

            if tts_backend == "kokoro":
                self._tts = TTSRegistry.create(
                    tts_backend, device="cpu", lang_code=tts_lang_code
                )
            else:
                self._tts = TTSRegistry.create(tts_backend)
        self._tts_voice_id = tts_voice_id

        self._capture = capture or (lambda: audio_io.MicStream(frame_ms=FRAME_MS))
        self._play_stream = play_stream or audio_io.play_pcm_stream
        self._play_wav = play_wav or audio_io.play_wav_bytes

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

    # ------------------------------------------------------------------
    # Etapas
    # ------------------------------------------------------------------

    def listen_once(
        self,
        on_ready: Callable[[], None] | None = None,
        vad: VoiceActivityDetector | None = None,
    ) -> tuple[bytes, VadStats, float, bool]:
        """Espera y captura UN turno de habla real del microfono.

        Devuelve (pcm, VadStats, segundos_de_arranque_del_micro,
        señal_muerta_al_arrancar). `on_ready` se llama cuando el micro ya
        entrega señal viva: ese es el momento de decir "habla ahora" (los
        ~0.4 s previos son ceros y cualquier palabra dicha entonces se
        pierde). Lanza `CaptureError` si la captura falla.
        """
        with self._capture() as mic:
            startup = getattr(mic, "startup_seconds", 0.0)
            dead = getattr(mic, "signal_dead_at_start", False)
            if on_ready is not None:
                on_ready()
            pcm, stats = segment_utterance(mic.frames(), vad or self._vad)
        return pcm, stats, startup, dead

    def transcribe(self, pcm: bytes) -> tuple[str, str | None, float | None]:
        """PCM crudo -> (texto, idioma, probabilidad_de_idioma)."""
        wav_bytes = audio_io.pcm_to_wav_bytes(pcm)
        kwargs = {"initial_prompt": self._stt_prompt} if self._stt_prompt else {}
        result = self._stt.transcribe(wav_bytes, format="wav", language="es", **kwargs)
        return result.text, result.language, result.confidence

    # ------------------------------------------------------------------
    # Confirmacion de acciones por voz
    # ------------------------------------------------------------------

    def _listen_short(self) -> str:
        """Escucha UNA respuesta corta (si/no) y la transcribe ('' si no hubo)."""
        cfg = replace(
            self._vad.config,
            silence_timeout_ms=900,
            no_speech_timeout_ms=9000,
            max_utterance_ms=6000,
        )
        vad = VoiceActivityDetector(cfg)
        vad.noise_floor = self._vad.noise_floor  # ya calibrado en esta sesion
        pcm, _stats, _startup, _dead = self.listen_once(vad=vad)
        if not pcm:
            return ""
        wav_bytes = audio_io.pcm_to_wav_bytes(pcm)
        result = self._stt.transcribe(
            wav_bytes,
            format="wav",
            language="es",
            initial_prompt="Sí. No. Hazlo. Adelante. Confirmo. Cancela. Dale.",
        )
        return result.text or ""

    def confirm_by_voice(self, description: str, attempts: int = 2) -> bool | None:
        """Pregunta en voz alta y escucha 'si'/'no'. None si no hay respuesta clara."""
        from openjarvis.jarvis.confirmation import parse_yes_no

        for attempt in range(attempts):
            question = (
                f"Necesito tu permiso para: {description}. ¿Lo confirmas? Di sí o no."
                if attempt == 0
                else "No te he entendido. ¿Sí o no?"
            )
            try:
                self.speak(question)
                answer = parse_yes_no(self._listen_short())
            except CaptureError:
                return None
            if answer is not None:
                return answer
        return None

    @property
    def confirm_channel(self) -> Callable[[Any], bool | None]:
        """Canal para ConfirmationBroker: usa la voz para pedir el permiso."""

        def channel(pending: Any) -> bool | None:
            return self.confirm_by_voice(pending.description)

        channel.channel_name = "voz"  # type: ignore[attr-defined]
        return channel

    def ask_agent(
        self,
        text: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = DEFAULT_VOICE_MAX_TOKENS,
        style_hint: str | None = None,
    ) -> str:
        """Texto transcrito -> respuesta del agente JARVIS (reutiliza cli.ask)."""
        hint = self._style_hint if style_hint is None else style_hint
        augmented_text = f"{hint}\n\n{text}" if hint else text
        if self._agent_fn is not None:
            return self._agent_fn(augmented_text, temperature, max_tokens)

        from openjarvis.cli._tool_names import resolve_tool_names
        from openjarvis.cli.ask import _run_agent

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
        El detalle incluye `gap_seconds`/`underruns` (silencio audible entre
        frases si la sintesis va mas lenta que la reproduccion).
        """
        tts_kwargs: dict = {}
        if self._tts_voice_id:
            tts_kwargs["voice_id"] = self._tts_voice_id

        stream_fn = getattr(self._tts, "synthesize_stream", None)
        if stream_fn is not None:
            sample_rate = getattr(self._tts, "sample_rate", 24000)
            t0 = time.monotonic()
            detail: dict = {
                "chunks": 0,
                "first_chunk_s": None,
                "chunk_intervals_s": [],
                "underruns": 0,
                "gap_seconds": 0.0,
            }
            state = {"last": t0, "play_end": None}

            def _timed_chunks():
                for chunk in stream_fn(text, **tts_kwargs):
                    now = time.monotonic()
                    dur = len(chunk) / 2 / sample_rate
                    if detail["first_chunk_s"] is None:
                        detail["first_chunk_s"] = now - t0
                        state["play_end"] = now + dur
                    else:
                        if now > state["play_end"]:
                            detail["underruns"] += 1
                            detail["gap_seconds"] += now - state["play_end"]
                            state["play_end"] = now + dur
                        else:
                            state["play_end"] += dur
                    detail["chunk_intervals_s"].append(round(now - state["last"], 3))
                    state["last"] = now
                    detail["chunks"] += 1
                    yield chunk

            self._play_stream(_timed_chunks(), sample_rate=sample_rate)
            total_s = time.monotonic() - t0
            first_audio_s = detail["first_chunk_s"] or total_s
            return "stream", first_audio_s, total_s, detail

        t0 = time.monotonic()
        tts_result = self._tts.synthesize(text, output_format="wav", **tts_kwargs)
        synth_s = time.monotonic() - t0
        t0 = time.monotonic()
        self._play_wav(tts_result.audio)
        playback_s = time.monotonic() - t0
        detail = dict(tts_result.metadata or {})
        detail["playback_s"] = playback_s
        return "batch", synth_s, synth_s + playback_s, detail

    # ------------------------------------------------------------------
    # Turno completo
    # ------------------------------------------------------------------

    def run_turn(
        self,
        *,
        temperature: float = 0.7,
        max_tokens: int = DEFAULT_VOICE_MAX_TOKENS,
        on_ready: Callable[[], None] | None = None,
    ) -> VoiceTurnResult:
        """Un ciclo completo: escuchar -> transcribir -> responder -> hablar.

        Nunca lanza por un fallo esperado: devuelve un `VoiceTurnResult` con
        el `TurnOutcome`, la causa probable y las medidas para poder
        reintentar. Solo `KeyboardInterrupt` se propaga.
        """
        self._turn_index += 1
        res = VoiceTurnResult()
        lat = res.latency

        def done(
            outcome: TurnOutcome, error: str = "", cause: str = ""
        ) -> VoiceTurnResult:
            res.outcome = outcome
            res.error = error
            res.probable_cause = cause
            self._log(res)
            return res

        # 1) captura + VAD -------------------------------------------------
        try:
            pcm, stats, startup, dead = self.listen_once(on_ready)
        except CaptureError as exc:
            return done(
                TurnOutcome.CAPTURE_ERROR,
                error=str(exc),
                cause=f"Fallo de captura del microfono: {exc}",
            )
        res.vad = stats
        lat.capture_startup_seconds = startup
        lat.vad_wait_for_speech_seconds = stats.wait_for_speech_seconds
        lat.vad_speech_seconds = stats.speech_seconds
        lat.vad_trailing_silence_seconds = stats.trailing_silence_seconds

        if stats.end_reason == EndReason.NO_SPEECH:
            return done(
                TurnOutcome.NO_SPEECH,
                cause=diagnose_no_speech(stats, dead),
            )
        if not pcm:
            return done(
                TurnOutcome.CAPTURE_ERROR,
                error="capture_ended_without_speech",
                cause="El flujo de audio termino antes de capturar voz utilizable.",
            )

        res.audio = audio_io.audio_levels(pcm)
        self._save_debug_audio(pcm)

        # 2) STT -----------------------------------------------------------
        t0 = time.monotonic()
        try:
            text, language, lang_prob = self.transcribe(pcm)
        except Exception as exc:  # noqa: BLE001
            lat.stt_seconds = time.monotonic() - t0
            return done(
                TurnOutcome.STT_ERROR,
                error=f"{type(exc).__name__}: {exc}",
                cause="Fallo del reconocimiento de voz (faster-whisper).",
            )
        lat.stt_seconds = time.monotonic() - t0
        res.language, res.language_probability = language, lang_prob
        text = (text or "").strip()

        if not text or looks_like_hallucination(text, stats.voiced_seconds):
            res.transcript = ""
            cause = diagnose_empty_transcript(stats, res.audio)
            if text:
                cause = (
                    f"Transcripcion sospechosa de alucinacion de Whisper ({text!r} "
                    f"sobre {stats.voiced_seconds:.1f} s de voz): se descarta. " + cause
                )
            res.hint = pause_hint(stats, self._vad.config.silence_timeout_ms)
            return done(TurnOutcome.EMPTY_TRANSCRIPT, cause=cause)
        res.transcript = text
        res.hint = pause_hint(stats, self._vad.config.silence_timeout_ms)

        # 3) agente (Ollama) -----------------------------------------------
        t0 = time.monotonic()
        try:
            response = self.ask_agent(
                text, temperature=temperature, max_tokens=max_tokens
            )
        except Exception as exc:  # noqa: BLE001 -- incluye EngineConnectionError
            lat.agent_seconds = time.monotonic() - t0
            return done(
                TurnOutcome.AGENT_ERROR,
                error=f"{type(exc).__name__}: {exc}",
                cause="El agente/Ollama no respondio (¿Ollama detenido?).",
            )
        lat.agent_seconds = time.monotonic() - t0
        res.response_text = (response or "").strip()
        if not res.response_text:
            return done(
                TurnOutcome.AGENT_ERROR,
                error="empty_response",
                cause="El modelo devolvio una respuesta vacia.",
            )

        # 4) TTS + reproduccion --------------------------------------------
        try:
            mode, first_audio_s, total_s, detail = self.speak(res.response_text)
        except Exception as exc:  # noqa: BLE001 -- incluye PlaybackError
            return done(
                TurnOutcome.TTS_ERROR,
                error=f"{type(exc).__name__}: {exc}",
                cause="Fallo la voz de salida (Kokoro/reproduccion); "
                "la respuesta se muestra solo como texto.",
            )
        lat.tts_mode = mode
        lat.tts_time_to_first_audio_seconds = first_audio_s
        lat.tts_total_seconds = total_s
        lat.tts_detail = detail
        return done(TurnOutcome.OK)

    # ------------------------------------------------------------------
    # Soporte
    # ------------------------------------------------------------------

    def _log(self, res: VoiceTurnResult) -> None:
        if not self._log_turns:
            return
        lat = res.latency
        event = {
            "run_id": self.run_id,
            "turn": self._turn_index,
            "outcome": res.outcome.value,
            "error": res.error[:200],
            "probable_cause": res.probable_cause[:300],
            "transcript_words": len(res.transcript.split()),
            "response_words": len(res.response_text.split()),
            "language": res.language,
            "latency": {
                k: round(v, 3) for k, v in asdict(lat).items() if isinstance(v, float)
            },
            "tts_mode": lat.tts_mode,
            "tts_chunks": lat.tts_detail.get("chunks"),
            "tts_underruns": lat.tts_detail.get("underruns"),
            "vad_config": {
                "silence_timeout_ms": self._vad.config.silence_timeout_ms,
                "aggressiveness": self._vad.config.aggressiveness,
                "gate_margin_db": self._vad.config.gate_margin_db,
            },
        }
        if res.vad is not None:
            v = res.vad
            event["vad"] = {
                "end_reason": v.end_reason.value,
                "voiced_seconds": round(v.voiced_seconds, 3),
                "voiced_ratio": round(v.voiced_ratio, 3),
                "longest_pause_s": round(v.longest_pause_seconds, 3),
                "pause_count": v.pause_count,
                "discarded_noise_bursts": v.discarded_noise_bursts,
                "noise_floor_dbfs": round(v.noise_floor_dbfs, 1),
                "max_idle_dbfs": round(v.max_idle_dbfs, 1),
                "dead_frame_fraction": round(v.dead_frame_fraction, 3),
            }
        if res.audio is not None:
            event["audio"] = {
                "duration_s": round(res.audio.duration_seconds, 3),
                "peak_dbfs": round(res.audio.peak_dbfs, 1),
                "rms_dbfs": round(res.audio.rms_dbfs, 1),
                "clipped_fraction": round(res.audio.clipped_fraction, 4),
            }
        turnlog.append_event(event, self._log_path)

    def _save_debug_audio(self, pcm: bytes, keep: int = 10) -> None:
        """Opt-in (--debug-audio-dir): guarda el turno para diagnostico."""
        if self._debug_audio_dir is None:
            return
        try:
            self._debug_audio_dir.mkdir(parents=True, exist_ok=True)
            name = f"utt-{self.run_id}-{self._turn_index:03d}.wav"
            path = self._debug_audio_dir / name
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(audio_io.CHANNELS)
                wf.setsampwidth(audio_io.BYTES_PER_SAMPLE)
                wf.setframerate(audio_io.SAMPLE_RATE)
                wf.writeframes(pcm)
            files = sorted(self._debug_audio_dir.glob("utt-*.wav"))
            for old in files[:-keep]:
                old.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("debug audio not saved: %s", exc)
