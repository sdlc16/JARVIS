"""Servicio de voz de la GUI: arranca/para la conversación por voz en un hilo.

Usa exactamente el mismo ``JarvisRuntime`` y ``ConfirmationBroker`` que el chat
(mismas reglas, mismos permisos, mismo historial); la voz solo añade el canal de
confirmación hablada. Los modelos (Whisper/Kokoro) se cargan una vez por
arranque. El micro solo está abierto mientras el servicio está activo y nunca se
guarda audio (salvo el opt-in de depuración de ``[voice]``).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from openjarvis.voice.pipeline import COMPLETED_OUTCOMES, TurnOutcome

logger = logging.getLogger(__name__)

IDLE_POLL_MS = 2000  # tope de espera por turno: así "Parar" responde en ~2 s


class VoiceService:
    def __init__(
        self,
        runtime: Any,
        config: Any,
        emit: Callable[[dict[str, Any]], None],
        pipeline_factory: Callable[[Callable[[str, float, int], str]], Any]
        | None = None,
        session_id: str = "voz",
    ) -> None:
        self.runtime = runtime
        self.config = config
        self._emit = emit
        self._factory = pipeline_factory or self._default_factory
        self.session_id = session_id
        # off | starting | listening | transcribing | thinking | speaking | error
        self.state = "off"
        self.error = ""
        self.mode = "off"
        self.turns = 0
        self.started_at = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pipe: Any = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "mode": self.mode,
            "turns": self.turns,
            "error": self.error,
            "since": self.started_at,
        }

    def _set(self, state: str, error: str = "") -> None:
        self.state = state
        self.error = error
        self._emit({"type": "voice_state", **self.snapshot()})

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, mode: str | None = None) -> bool:
        with self._lock:
            if self.running:
                return False
            vc = self.config.voice
            self.mode = mode if mode in ("off", "model", "stt") else vc.wake_mode
            self._stop.clear()
            self.turns = 0
            self.started_at = time.time()
            self._set("starting")
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout: float = 8.0) -> bool:
        self._stop.set()
        th = self._thread
        if th is not None:
            th.join(timeout)
        stopped = th is None or not th.is_alive()
        if stopped:
            self._cleanup()
            self._set("off")
        return stopped

    def _cleanup(self) -> None:
        pipe, self._pipe = self._pipe, None
        if pipe is not None and hasattr(pipe, "close"):
            try:
                pipe.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    def _default_factory(self, agent_fn: Callable[[str, float, int], str]) -> Any:
        from openjarvis.voice.pipeline import VoicePipeline
        from openjarvis.voice.vad import VadConfig

        vc = self.config.voice
        barge = None
        if vc.barge_in:
            from openjarvis.voice.bargein import BargeInConfig

            barge = BargeInConfig(
                ignore_ms=vc.barge_in_ignore_ms,
                sustain_ms=vc.barge_in_sustain_ms,
                margin_db=vc.barge_in_margin_db,
                echo_margin_db=vc.barge_in_echo_margin_db,
            )
        return VoicePipeline(
            config=self.config,
            agent_fn=agent_fn,
            vad_config=VadConfig(
                aggressiveness=vc.aggressiveness,
                silence_timeout_ms=vc.silence_timeout_ms,
                max_utterance_ms=vc.max_utterance_ms,
                no_speech_timeout_ms=vc.no_speech_timeout_ms,
                min_speech_ms=vc.min_speech_ms,
                preroll_ms=vc.preroll_ms,
                tail_ms=vc.tail_ms,
                energy_gate=vc.energy_gate,
                gate_margin_db=vc.gate_margin_db,
            ),
            stt_prompt=vc.stt_prompt,
            style_hint="",
            barge_in=barge,
        )

    def _agent_fn(self, text: str, _temp: float, max_tokens: int) -> str:
        vc = self.config.voice
        channels = [self._pipe.confirm_channel] if vc.confirm_by_voice else None
        reply = self.runtime.respond(
            text,
            session_id=self.session_id,
            source="voice",
            voice=True,
            max_tokens=max_tokens,
            confirm_channels=channels,
            on_event=lambda e: self._emit({**e, "type": "voice_" + e["type"]}),
        )
        if reply.error:
            raise RuntimeError(reply.error)
        return reply.text

    def _on_result(self, r: Any) -> None:
        if r.outcome in COMPLETED_OUTCOMES:
            self.turns += 1
        if r.outcome == TurnOutcome.NO_SPEECH:
            return  # silencio: no es un turno
        self._emit(
            {
                "type": "voice_turn",
                "outcome": r.outcome.value,
                "transcript": r.transcript,
                "response": r.response_text,
                "cause": r.probable_cause,
                "stt_s": round(r.latency.stt_seconds, 2),
                "agent_s": round(r.latency.agent_seconds, 2),
                "first_audio_s": round(r.latency.tts_time_to_first_audio_seconds, 2),
            }
        )

    def _run(self) -> None:
        try:
            self._pipe = self._factory(self._agent_fn)
            self._pipe.on_stage = lambda st: (not self._stop.is_set()) and self._set(st)
            self._set("listening")
            vc = self.config.voice
            if self.mode == "off":
                self._loop_continuous(vc)
            else:
                self._loop_wake(vc)
        except Exception as exc:  # noqa: BLE001 - se informa a la GUI, no se silencia
            logger.exception("servicio de voz detenido por error")
            self._cleanup()
            self._set("error", f"{type(exc).__name__}: {exc}")
            return
        self._cleanup()
        self._set("off")

    def _loop_continuous(self, vc: Any) -> None:
        fails = 0
        while not self._stop.is_set():
            r = self._pipe.run_turn(
                max_tokens=vc.max_tokens, no_speech_timeout_ms=IDLE_POLL_MS
            )
            self._on_result(r)
            if r.outcome in COMPLETED_OUTCOMES or r.outcome == TurnOutcome.NO_SPEECH:
                fails = 0
            else:
                fails += 1
                if fails >= vc.max_consecutive_failures:
                    why = r.probable_cause or r.outcome.value
                    raise RuntimeError(f"{fails} fallos seguidos: {why}")
            self._set("listening")

    def _loop_wake(self, vc: Any) -> None:
        from openjarvis.voice.wake_session import run_wake_session

        detector = None
        if self.mode == "model":
            from openjarvis.voice.wakeword import WakeWordDetector

            detector = WakeWordDetector(
                vc.wake_model, vc.wake_threshold, vc.wake_patience
            )
        summary = run_wake_session(
            self._pipe,
            mode=self.mode,
            detector=detector,
            word=vc.wake_word,
            followup_s=vc.wake_followup_s,
            max_tokens=vc.max_tokens,
            on_result=self._on_result,
            on_wake=lambda w: self._emit({"type": "voice_wake", **w}),
            should_stop=self._stop.is_set,
        )
        if summary.stopped_reason in ("too_many_failures", "capture_error"):
            raise RuntimeError(f"sesión de voz detenida: {summary.stopped_reason}")
