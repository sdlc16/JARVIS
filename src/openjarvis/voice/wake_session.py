"""Sesion con palabra de activacion: espera "Hey Jarvis", conversa y vuelve a esperar.

Tras activarse, JARVIS dice una confirmacion breve y conversa con turnos
encadenados SIN repetir la palabra mientras el usuario siga hablando: si pasan
`followup_s` segundos sin voz, vuelve al modo de espera. Mientras espera solo
procesa audio en memoria (nada se guarda).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openjarvis.voice.audio_io import CaptureError
from openjarvis.voice.pipeline import (
    DEFAULT_VOICE_MAX_TOKENS,
    TurnOutcome,
    VoicePipeline,
    VoiceTurnResult,
)

ACK_TEXT = "Dime."


@dataclass
class WakeSessionSummary:
    wakes: int = 0
    turns_ok: int = 0
    # completed | interrupted | capture_error | too_many_failures
    stopped_reason: str = "completed"
    results: list[VoiceTurnResult] = field(default_factory=list)


def run_wake_session(
    pipeline: VoicePipeline,
    *,
    mode: str,
    detector: Any = None,
    word: str = "jarvis",
    followup_s: float = 8.0,
    first_wait_s: float = 8.0,
    max_tokens: int = DEFAULT_VOICE_MAX_TOKENS,
    max_wakes: int = 0,
    max_failures: int = 5,
    ack: str = ACK_TEXT,
    on_waiting: Callable[[], None] | None = None,
    on_wake: Callable[[dict[str, Any]], None] | None = None,
    on_result: Callable[[VoiceTurnResult], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> WakeSessionSummary:
    """Bucle principal. `max_wakes=0` = hasta Ctrl+C o `should_stop()`."""
    summary = WakeSessionSummary()
    failures = 0
    try:
        while max_wakes == 0 or summary.wakes < max_wakes:
            if should_stop is not None and should_stop():
                break
            try:
                wake = pipeline.wait_for_wake(
                    mode=mode,
                    detector=detector,
                    word=word,
                    on_ready=on_waiting,
                    should_stop=should_stop,
                )
            except CaptureError:
                failures += 1
                if failures >= max_failures:
                    summary.stopped_reason = "capture_error"
                    return summary
                continue
            if wake.get("stopped"):
                break
            failures = 0
            summary.wakes += 1
            if on_wake is not None:
                on_wake(wake)
            try:
                pipeline.speak(ack)
            except Exception:  # noqa: BLE001 - sin altavoz se sigue escuchando
                pass

            wait_ms = int(first_wait_s * 1000)
            while True:
                result = pipeline.run_turn(
                    max_tokens=max_tokens, no_speech_timeout_ms=wait_ms
                )
                summary.results.append(result)
                if on_result is not None:
                    on_result(result)
                if result.outcome == TurnOutcome.NO_SPEECH:
                    break  # nadie siguio hablando: de vuelta a esperar la palabra
                if result.outcome in (TurnOutcome.OK, TurnOutcome.TTS_ERROR):
                    summary.turns_ok += 1
                    failures = 0
                else:
                    failures += 1
                    if failures >= max_failures:
                        summary.stopped_reason = "too_many_failures"
                        return summary
                    if result.outcome == TurnOutcome.CAPTURE_ERROR:
                        break
                wait_ms = int(followup_s * 1000)
                if should_stop is not None and should_stop():
                    break
    except KeyboardInterrupt:
        summary.stopped_reason = "interrupted"
    return summary
