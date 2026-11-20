"""Controlled degradation when the local Ollama engine is unavailable.

Implements JARVIS_MASTER_PLAN.md §4: JARVIS has no cloud fallback engine.
When the configured local engine is unhealthy or fails mid-call, this module
is the single place that decides what happens -- a clear message, a bounded
number of retries, and recording the task as INTERRUPTED/UNKNOWN in the
``TaskStore`` so it is never retried automatically later. It never calls any
other ``InferenceEngine`` instance: the only engine reference it ever touches
is the one explicitly passed in by the caller.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any, Callable, Dict, Optional

from openjarvis.agents.errors import retry_delay
from openjarvis.core.types import Message
from openjarvis.engine._base import EngineConnectionError
from openjarvis.engine._stubs import InferenceEngine
from openjarvis.tasks.store import TaskStore

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3

# ``agents/errors.retry_delay()`` already defines the project's one backoff
# formula (exponential, capped at 300s: 10, 20, 40, 80, 160, 300, 300, ...).
# It is reused here rather than inventing a second one -- but its cadence is
# calibrated for a *background* agent tick (an Operator retrying on its own
# schedule, where waiting up to 300s between attempts is fine). This call
# site is the opposite: it runs inline during a single, synchronous voice
# turn (T2.4's own criterion is a <3s perceived latency for the whole
# turn), so waiting a bare `retry_delay()` would turn "Ollama hiccuped" into
# a 30s+ silence before the first two retries alone. Dividing by
# _INTERACTIVE_BACKOFF_SCALE gives the exact same formula, just scaled to an
# interactive latency budget: retry_delay(0)/10=1.0s, retry_delay(1)/10=2.0s,
# retry_delay(2)/10=4.0s -- identical to this module's previous hand-picked
# (1.0, 2.0, 4.0) for the 3 retries actually used today, and it keeps
# growing exponentially (capped at 30s) if max_retries is ever raised,
# instead of the old tuple's hard repeat of its last value.
_INTERACTIVE_BACKOFF_SCALE = 10


def _interactive_retry_delay(attempt: int) -> float:
    """``retry_delay()`` scaled down for a synchronous, in-turn retry loop."""
    return retry_delay(attempt) / _INTERACTIVE_BACKOFF_SCALE

UNAVAILABLE_MESSAGE = (
    "El cerebro local (Ollama) no está disponible en este momento. "
    "No se va a intentar nada en la nube -- lo reintentaré; si quieres, "
    "puedes decirme que lo reintente más tarde."
)


class DegradedResult(dict):
    """Marker subclass of dict so callers can ``isinstance`` check easily."""


def generate_with_degradation(
    engine: InferenceEngine,
    messages: Sequence[Message],
    *,
    model: str,
    task_store: Optional[TaskStore] = None,
    task_description: str = "",
    task_id: Optional[str] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    delay_fn: Callable[[int], float] = _interactive_retry_delay,
    sleep_fn: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Call ``engine.generate()`` with controlled degradation on failure.

    Returns the engine's normal response dict on success. On failure,
    returns a ``DegradedResult`` dict of the shape::

        {"ok": False, "degraded": True, "message": <str>, "task_id": <str|None>}

    *task_store* is optional -- if given, the task's state is tracked:
    created as PENDING (if *task_id* not supplied), left PENDING while
    unhealthy (nothing started), moved to RUNNING before the call, and to
    INTERRUPTED if the call raised partway through. It is never moved to
    UNKNOWN by this function -- that state is for callers who cannot verify
    a side-effecting action's outcome after the fact (e.g. the Windows
    bridge), which does not apply to a pure ``generate()`` call with no
    external side effect.

    Hard guarantee: this function holds a reference to exactly one
    ``InferenceEngine`` -- *engine* -- and never imports, constructs or calls
    any other engine or cloud provider under any circumstance.
    """
    if task_store is not None and task_id is None:
        task_id = task_store.create(task_description or f"generate:{model}")

    if not _is_healthy(engine):
        logger.warning(
            "engine %s unhealthy before generate()", getattr(engine, "engine_id", "?")
        )
        # Nothing was attempted -- the task stays PENDING, not INTERRUPTED.
        return DegradedResult(
            ok=False,
            degraded=True,
            message=UNAVAILABLE_MESSAGE,
            task_id=task_id,
        )

    if task_store is not None and task_id is not None:
        task_store.mark_running(task_id)

    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            result = engine.generate(messages, model=model, **kwargs)
        except EngineConnectionError as exc:
            last_error = exc
            logger.warning(
                "engine %s unreachable (attempt %d/%d): %s",
                getattr(engine, "engine_id", "?"),
                attempt + 1,
                max_retries,
                exc,
            )
            if attempt < max_retries - 1:
                sleep_fn(delay_fn(attempt))
            continue
        else:
            if task_store is not None and task_id is not None:
                task_store.mark_completed(task_id, result="generate() succeeded")
            return result

    # Exhausted retries without a single successful response: we know it
    # never completed, so this is INTERRUPTED, not UNKNOWN.
    if task_store is not None and task_id is not None:
        task_store.mark_interrupted(
            task_id,
            reason=f"ollama unreachable after {max_retries} attempts: {last_error}",
        )
    return DegradedResult(
        ok=False,
        degraded=True,
        message=UNAVAILABLE_MESSAGE,
        task_id=task_id,
    )


def _is_healthy(engine: InferenceEngine) -> bool:
    try:
        return bool(engine.health())
    except Exception:  # noqa: BLE001 -- health() itself must never crash the caller
        return False


__all__ = [
    "DegradedResult",
    "UNAVAILABLE_MESSAGE",
    "generate_with_degradation",
]
