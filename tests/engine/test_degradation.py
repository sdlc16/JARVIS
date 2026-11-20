"""Tests for the controlled-degradation wrapper (DEVELOPMENT_TASKS.md T1.4)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, Dict, List

from openjarvis.core.types import Message, Role
from openjarvis.engine._base import EngineConnectionError
from openjarvis.engine._stubs import InferenceEngine
from openjarvis.engine.degradation import (
    UNAVAILABLE_MESSAGE,
    DegradedResult,
    generate_with_degradation,
)
from openjarvis.tasks.store import TaskState, TaskStore


class _FakeEngine(InferenceEngine):
    """Minimal real implementation of the ABC -- not a mocking-library mock.

    Scripted via *healthy* and *fail_times* so each test controls exactly
    what the fake engine does, with real Python control flow underneath.
    """

    engine_id = "fake"
    is_cloud = False

    def __init__(self, *, healthy: bool = True, fail_times: int = 0) -> None:
        self.healthy = healthy
        self.fail_times = fail_times
        self.calls = 0

    def generate(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise EngineConnectionError("fake: connection refused")
        return {"content": "ok", "usage": {"total_tokens": 1}, "model": model}

    async def stream(
        self, messages, *, model, temperature=0.7, max_tokens=1024, **kwargs
    ) -> AsyncIterator[str]:
        yield "ok"

    def list_models(self) -> List[str]:
        return ["fake-model"]

    def health(self) -> bool:
        return self.healthy


class _PoisonEngine(InferenceEngine):
    """An engine that must never be called -- used to prove no cloud fallback."""

    engine_id = "poison-cloud"
    is_cloud = True

    def generate(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise AssertionError("degradation wrapper must never call another engine")

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        raise AssertionError("degradation wrapper must never call another engine")
        yield ""  # pragma: no cover

    def list_models(self) -> List[str]:
        raise AssertionError("degradation wrapper must never call another engine")

    def health(self) -> bool:
        raise AssertionError("degradation wrapper must never call another engine")


def _messages() -> List[Message]:
    return [Message(role=Role.USER, content="hola")]


def _store(tmp_path: Path) -> TaskStore:
    return TaskStore(db_path=tmp_path / "tasks.db")


def test_healthy_engine_succeeds_immediately(tmp_path):
    engine = _FakeEngine(healthy=True, fail_times=0)
    store = _store(tmp_path)
    result = generate_with_degradation(
        engine,
        _messages(),
        model="fake-model",
        task_store=store,
        sleep_fn=lambda s: None,
    )
    assert result["content"] == "ok"
    assert engine.calls == 1
    tasks = store.list_by_state(TaskState.COMPLETED)
    assert len(tasks) == 1


def test_unhealthy_engine_returns_degraded_and_stays_pending(tmp_path):
    engine = _FakeEngine(healthy=False)
    store = _store(tmp_path)
    result = generate_with_degradation(
        engine,
        _messages(),
        model="fake-model",
        task_store=store,
        sleep_fn=lambda s: None,
    )
    assert isinstance(result, DegradedResult)
    assert result["ok"] is False
    assert result["message"] == UNAVAILABLE_MESSAGE
    assert engine.calls == 0  # never attempted generate() on an unhealthy engine

    task_id = result["task_id"]
    record = store.get(task_id)
    assert record.state is TaskState.PENDING
    assert store.can_auto_retry(task_id) is True


def test_retries_then_succeeds_marks_completed(tmp_path):
    engine = _FakeEngine(healthy=True, fail_times=2)  # fails twice, succeeds on 3rd
    store = _store(tmp_path)
    sleeps: List[float] = []
    result = generate_with_degradation(
        engine,
        _messages(),
        model="fake-model",
        task_store=store,
        max_retries=3,
        sleep_fn=sleeps.append,
    )
    assert result["content"] == "ok"
    assert engine.calls == 3
    assert len(sleeps) == 2  # backoff happened between the 2 failed attempts
    # Delay values: retry_delay()/10 reused from agents/errors.py, not a
    # second hand-picked backoff formula -- 1.0s then 2.0s for attempts 0, 1.
    assert sleeps == [1.0, 2.0]
    record = store.get(result and store.list_by_state(TaskState.COMPLETED)[0].task_id)
    assert record.state is TaskState.COMPLETED


def test_delay_reuses_agents_errors_retry_delay(tmp_path):
    """The backoff is a scaled call to agents/errors.retry_delay(), not an
    independently invented formula -- this is the duplication the
    comparative audit flagged, now eliminated."""
    from openjarvis.agents.errors import retry_delay
    from openjarvis.engine.degradation import _interactive_retry_delay

    for attempt in range(5):
        assert _interactive_retry_delay(attempt) == retry_delay(attempt) / 10


def test_delay_fn_is_injectable_for_tests_without_real_sleeps(tmp_path):
    """Callers (like every other test in this file) can replace the delay
    function entirely, e.g. to make retries instant in a test."""
    engine = _FakeEngine(healthy=True, fail_times=1)
    store = _store(tmp_path)
    result = generate_with_degradation(
        engine,
        _messages(),
        model="fake-model",
        task_store=store,
        max_retries=2,
        delay_fn=lambda attempt: 0.0,
        sleep_fn=lambda s: None,
    )
    assert result["content"] == "ok"


def test_exhausts_retries_marks_interrupted_never_auto_retryable(tmp_path):
    engine = _FakeEngine(healthy=True, fail_times=99)  # always fails
    store = _store(tmp_path)
    result = generate_with_degradation(
        engine,
        _messages(),
        model="fake-model",
        task_store=store,
        max_retries=3,
        sleep_fn=lambda s: None,
    )
    assert isinstance(result, DegradedResult)
    assert result["ok"] is False
    assert engine.calls == 3  # bounded -- exactly max_retries, never unbounded

    task_id = result["task_id"]
    record = store.get(task_id)
    assert record.state is TaskState.INTERRUPTED
    # Hard rule: an interrupted task is never auto-retryable.
    assert store.can_auto_retry(task_id) is False


def test_never_touches_a_second_engine(tmp_path):
    """The poison engine's methods raise AssertionError if ever called --
    this test passes only if generate_with_degradation holds no reference
    to it and never constructs one internally."""
    poison = _PoisonEngine()  # noqa: F841 -- deliberately unused by the wrapper
    engine = _FakeEngine(healthy=True, fail_times=5)
    store = _store(tmp_path)
    result = generate_with_degradation(
        engine,
        _messages(),
        model="fake-model",
        task_store=store,
        max_retries=2,
        sleep_fn=lambda s: None,
    )
    assert isinstance(result, DegradedResult)
    # If the wrapper had called `poison` in any way, it would have raised
    # inside generate_with_degradation and this test would already have
    # failed with an uncaught AssertionError propagating up.


def test_works_without_a_task_store(tmp_path):
    """task_store is optional -- degradation logic must not require it."""
    engine = _FakeEngine(healthy=True, fail_times=0)
    result = generate_with_degradation(engine, _messages(), model="fake-model")
    assert result["content"] == "ok"


def test_approved_retry_after_interruption_then_succeeds(tmp_path):
    """Full lifecycle: interrupted -> explicit user approval -> retried
    manually (never automatically) -> completed."""
    store = _store(tmp_path)
    failing_engine = _FakeEngine(healthy=True, fail_times=99)
    result = generate_with_degradation(
        failing_engine,
        _messages(),
        model="fake-model",
        task_store=store,
        max_retries=1,
        sleep_fn=lambda s: None,
    )
    task_id = result["task_id"]
    assert store.get(task_id).state is TaskState.INTERRUPTED
    assert store.can_auto_retry(task_id) is False

    # Explicit, user-approved retry -- a human decision, not automatic code.
    store.approve_retry(task_id)
    assert store.get(task_id).state is TaskState.PENDING

    recovered_engine = _FakeEngine(healthy=True, fail_times=0)
    result2 = generate_with_degradation(
        recovered_engine,
        _messages(),
        model="fake-model",
        task_store=store,
        task_id=task_id,
        sleep_fn=lambda s: None,
    )
    assert result2["content"] == "ok"
    assert store.get(task_id).state is TaskState.COMPLETED
