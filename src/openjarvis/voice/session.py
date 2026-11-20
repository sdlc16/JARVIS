"""Bucle de sesion de voz: reintenta los turnos fallidos sin cerrar la sesion.

Separado del comando CLI para poder probarlo de forma automatica. Reglas:

* Un turno **completado** (OK, o TTS_ERROR: la respuesta existe como texto)
  cuenta para `num_turns`; un fallo (no hubo voz, transcripcion vacia, fallo
  de captura/STT/agente) NO cuenta y se reintenta -- antes, un unico turno
  vacio con `--turns 1` terminaba la sesion sin respuesta.
* Tras `max_consecutive_failures` fallos seguidos se detiene con una razon
  explicita (`too_many_failures`) en vez de reintentar para siempre, porque
  un fallo persistente (micro silenciado, Ollama caido) no se arregla solo.
* Ctrl+C termina limpiamente (`interrupted`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from openjarvis.voice.pipeline import (
    DEFAULT_VOICE_MAX_TOKENS,
    VoicePipeline,
    VoiceTurnResult,
)


@dataclass
class SessionSummary:
    run_id: str = ""
    turns_ok: int = 0
    attempts: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    stopped_reason: str = "completed"  # completed | too_many_failures | interrupted
    results: list[VoiceTurnResult] = field(default_factory=list)

    @property
    def failed_attempts(self) -> int:
        return sum(self.failures.values())


def run_session(
    pipeline: VoicePipeline,
    *,
    num_turns: int = 1,
    max_consecutive_failures: int = 3,
    max_tokens: int = DEFAULT_VOICE_MAX_TOKENS,
    on_attempt: Callable[[int, int], None] | None = None,
    on_ready: Callable[[], None] | None = None,
    on_result: Callable[[VoiceTurnResult], None] | None = None,
) -> SessionSummary:
    """Ejecuta `num_turns` turnos completados (0 = continuo hasta Ctrl+C)."""
    summary = SessionSummary(run_id=pipeline.run_id)
    consecutive = 0
    try:
        while num_turns == 0 or summary.turns_ok < num_turns:
            summary.attempts += 1
            if on_attempt is not None:
                on_attempt(summary.attempts, summary.turns_ok)
            result = pipeline.run_turn(max_tokens=max_tokens, on_ready=on_ready)
            summary.results.append(result)
            if on_result is not None:
                on_result(result)
            if result.ok:
                summary.turns_ok += 1
                consecutive = 0
                continue
            key = result.outcome.value
            summary.failures[key] = summary.failures.get(key, 0) + 1
            consecutive += 1
            if consecutive >= max_consecutive_failures:
                summary.stopped_reason = "too_many_failures"
                break
    except KeyboardInterrupt:
        summary.stopped_reason = "interrupted"
    return summary
