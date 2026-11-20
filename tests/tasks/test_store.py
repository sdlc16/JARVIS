"""Tests for the task state store (DEVELOPMENT_TASKS.md T1.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.tasks.store import TaskState, TaskStore, TransitionError


def _store(tmp_path: Path) -> TaskStore:
    return TaskStore(db_path=tmp_path / "tasks.db")


def test_create_starts_pending(tmp_path):
    store = _store(tmp_path)
    task_id = store.create("abrir el Bloc de notas")
    record = store.get(task_id)
    assert record is not None
    assert record.state is TaskState.PENDING
    assert record.description == "abrir el Bloc de notas"


def test_full_happy_path_transitions(tmp_path):
    store = _store(tmp_path)
    task_id = store.create("abrir Brave")
    store.mark_running(task_id)
    assert store.get(task_id).state is TaskState.RUNNING
    store.mark_completed(task_id, result="ventana abierta y verificada")
    record = store.get(task_id)
    assert record.state is TaskState.COMPLETED
    assert record.result == "ventana abierta y verificada"


def test_interrupted_cannot_auto_retry(tmp_path):
    store = _store(tmp_path)
    task_id = store.create("escribir texto")
    store.mark_running(task_id)
    store.mark_interrupted(task_id, reason="el puente se cayó a mitad de la acción")
    assert store.get(task_id).state is TaskState.INTERRUPTED
    assert store.can_auto_retry(task_id) is False


def test_unknown_result_cannot_auto_retry(tmp_path):
    store = _store(tmp_path)
    task_id = store.create("hacer clic en coordenadas")
    store.mark_running(task_id)
    store.mark_unknown(task_id, reason="no llegó confirmación desde el puente")
    assert store.get(task_id).state is TaskState.UNKNOWN
    assert store.can_auto_retry(task_id) is False


def test_pending_and_running_and_completed_can_auto_retry_is_true(tmp_path):
    """can_auto_retry is only a hard False for INTERRUPTED/UNKNOWN; other
    states are not inherently blocked by this guard (retry policy for them
    is the caller's business, e.g. bounded backoff on PENDING)."""
    store = _store(tmp_path)
    for setup in ("pending_only", "running", "completed"):
        task_id = store.create(f"case {setup}")
        if setup in ("running", "completed"):
            store.mark_running(task_id)  # legal path: PENDING -> RUNNING -> ...
        if setup == "completed":
            store.mark_completed(task_id, result="ok")
        assert store.can_auto_retry(task_id) is True


def test_can_auto_retry_false_for_missing_task(tmp_path):
    store = _store(tmp_path)
    assert store.can_auto_retry("does-not-exist") is False


def test_approve_retry_moves_interrupted_back_to_pending(tmp_path):
    store = _store(tmp_path)
    task_id = store.create("enviar un correo")
    store.mark_running(task_id)
    store.mark_interrupted(task_id, reason="conexión perdida")
    assert store.can_auto_retry(task_id) is False

    store.approve_retry(task_id)  # explicit, user-approved call

    record = store.get(task_id)
    assert record.state is TaskState.PENDING
    assert store.can_auto_retry(task_id) is True


def test_approve_retry_rejects_non_retryable_state(tmp_path):
    store = _store(tmp_path)
    task_id = store.create("tarea normal")
    store.mark_running(task_id)
    store.mark_completed(task_id, result="ok")
    with pytest.raises(ValueError):
        store.approve_retry(task_id)  # COMPLETED is not INTERRUPTED/UNKNOWN


def test_approve_retry_rejects_unknown_task(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.approve_retry("no-such-id")


def test_list_by_state(tmp_path):
    store = _store(tmp_path)
    a = store.create("a")
    b = store.create("b")
    store.mark_running(b)
    store.mark_interrupted(b, reason="x")

    pending = store.list_by_state(TaskState.PENDING)
    interrupted = store.list_by_state(TaskState.INTERRUPTED)

    assert [r.task_id for r in pending] == [a]
    assert [r.task_id for r in interrupted] == [b]


def test_metadata_round_trips(tmp_path):
    store = _store(tmp_path)
    task_id = store.create(
        "con metadata", metadata={"tool": "windows_control", "op": "open"}
    )
    record = store.get(task_id)
    assert record.metadata == {"tool": "windows_control", "op": "open"}


def test_persists_across_reconnects(tmp_path):
    """Same SQLite file, new TaskStore instance -- state survives (no second
    database technology, same file persists like any other OpenJarvis
    SQLite-backed store)."""
    db_path = tmp_path / "tasks.db"
    store1 = TaskStore(db_path=db_path)
    task_id = store1.create("tarea persistente")
    store1.mark_running(task_id)
    store1.close()

    store2 = TaskStore(db_path=db_path)
    record = store2.get(task_id)
    assert record is not None
    assert record.state is TaskState.RUNNING


# ---------------------------------------------------------------------------
# Hardening added after the compatibility audit: a completed task must never
# be re-executed, illegal transitions must be rejected, and a concurrent
# race for the same PENDING task must not let two callers both "win".
# ---------------------------------------------------------------------------


def test_completed_task_cannot_be_re_run(tmp_path):
    """The defect found in the audit: mark_running() on an already-COMPLETED
    task used to succeed silently, which would let a finished task execute
    again. It must now raise instead."""
    store = _store(tmp_path)
    task_id = store.create("enviar un correo")
    store.mark_running(task_id)
    store.mark_completed(task_id, result="enviado")

    with pytest.raises(TransitionError):
        store.mark_running(task_id)

    # The illegal attempt must not have changed anything.
    assert store.get(task_id).state is TaskState.COMPLETED


def test_cannot_skip_running_straight_to_completed(tmp_path):
    """PENDING -> COMPLETED directly (skipping RUNNING) is not a real
    execution and must be rejected."""
    store = _store(tmp_path)
    task_id = store.create("tarea sin ejecutar")
    with pytest.raises(TransitionError):
        store.mark_completed(task_id, result="ok")
    assert store.get(task_id).state is TaskState.PENDING


def test_cannot_bypass_approve_retry_from_interrupted(tmp_path):
    """mark_running() directly on an INTERRUPTED task must fail -- the only
    legal way back to PENDING (and then RUNNING) is approve_retry()."""
    store = _store(tmp_path)
    task_id = store.create("acción interrumpida")
    store.mark_running(task_id)
    store.mark_interrupted(task_id, reason="caída del puente")

    with pytest.raises(TransitionError):
        store.mark_running(task_id)

    assert store.get(task_id).state is TaskState.INTERRUPTED


def test_concurrent_mark_running_only_one_wins(tmp_path):
    """Two callers racing to start the same PENDING task: only one may
    succeed. This is what prevents duplicate execution under concurrency --
    verified with real threads against the real SQLite file, not simulated."""
    import threading

    db_path = tmp_path / "tasks.db"
    store = TaskStore(db_path=db_path)
    task_id = store.create("tarea en carrera")

    results: list[bool] = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        barrier.wait()
        try:
            store.mark_running(task_id)
            results.append(True)
        except TransitionError:
            results.append(False)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [False, True]  # exactly one winner, one loser
    assert store.get(task_id).state is TaskState.RUNNING
