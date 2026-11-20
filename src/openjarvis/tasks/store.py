"""SQLite-backed task state store.

Mirrors the plain-``sqlite3`` pattern used by ``traces/store.py`` and
``scheduler/store.py`` — its own table in its own small database file under
``DEFAULT_CONFIG_DIR``, no second storage technology, no dependency on the
Rust-backed document memory (``tools/storage/sqlite.py``), which is for
unstructured document retrieval, not structured task bookkeeping.

States (JARVIS_MASTER_PLAN.md §4 / DEVELOPMENT_TASKS.md T1.4):

    PENDING      -- instructed, not yet attempted.
    RUNNING      -- currently executing.
    INTERRUPTED  -- started executing, stopped before finishing (we know it
                    did not complete).
    COMPLETED    -- executed and its outcome was explicitly verified
                    (success or failure, but *verified*).
    UNKNOWN      -- executed but its real effect could not be confirmed
                    (e.g. a bridge action whose confirmation never arrived).

Hard rule: a task in INTERRUPTED or UNKNOWN is never retried automatically.
``TaskStore.can_auto_retry()`` is the single source of truth for that rule --
every caller that might retry an action must consult it first.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.config import DEFAULT_CONFIG_DIR


class TaskState(str, Enum):
    """Task lifecycle state."""

    PENDING = "pending"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


# States from which an action may *never* be retried automatically.
_NON_RETRYABLE = frozenset({TaskState.INTERRUPTED, TaskState.UNKNOWN})

# Legal transition graph. A transition not listed here (e.g. COMPLETED ->
# RUNNING, which would mean re-executing a finished task; or INTERRUPTED ->
# RUNNING directly, which would skip the explicit approve_retry() step) is
# rejected. INTERRUPTED/UNKNOWN -> PENDING exists *only* via approve_retry(),
# never via the ordinary mark_* methods.
_ALLOWED_TRANSITIONS: Dict[TaskState, frozenset] = {
    TaskState.PENDING: frozenset({TaskState.RUNNING}),
    TaskState.RUNNING: frozenset(
        {TaskState.INTERRUPTED, TaskState.UNKNOWN, TaskState.COMPLETED}
    ),
    TaskState.INTERRUPTED: frozenset({TaskState.PENDING}),  # via approve_retry only
    TaskState.UNKNOWN: frozenset({TaskState.PENDING}),  # via approve_retry only
    TaskState.COMPLETED: frozenset(),  # terminal -- no transition out
}


class TransitionError(ValueError):
    """Raised when a state transition is illegal or lost a concurrent race."""


@dataclass(slots=True)
class TaskRecord:
    """A single tracked task/action and its current state."""

    task_id: str
    description: str
    state: TaskState
    result: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS tasks (
    task_id    TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    state      TEXT NOT NULL,
    result     TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    metadata   TEXT NOT NULL DEFAULT '{}'
);
"""


class TaskStore:
    """Persists task state transitions in SQLite (stdlib ``sqlite3``)."""

    def __init__(self, db_path: str | Path = "") -> None:
        if not db_path:
            db_path = DEFAULT_CONFIG_DIR / "tasks.db"
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        # ``check_same_thread=False`` only lifts sqlite3's same-thread check --
        # it does NOT make concurrent execute()/commit() calls from multiple
        # threads on one Connection safe (CPython's sqlite3 module is not
        # internally synchronized for that). Without this lock, two threads
        # racing mark_running() could both hit the C-level connection at once
        # and raise sqlite3.InterfaceError instead of cleanly losing the
        # compare-and-swap race -- found by test_concurrent_mark_running_only_one_wins
        # failing intermittently (nondeterministic thread scheduling).
        self._lock = threading.Lock()
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    # -- creation / transitions ---------------------------------------------

    def create(
        self,
        description: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> str:
        """Register a new task in ``PENDING`` state and return its id."""
        task_id = task_id or str(uuid.uuid4())
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (task_id, description, state, result, "
                "created_at, updated_at, metadata) VALUES (?, ?, ?, NULL, ?, ?, ?)",
                (
                    task_id,
                    description,
                    TaskState.PENDING.value,
                    now,
                    now,
                    json.dumps(metadata or {}),
                ),
            )
            self._conn.commit()
        return task_id

    def mark_running(self, task_id: str) -> None:
        """Start execution. Only legal from PENDING.

        Raises ``TransitionError`` if the task is not PENDING -- including
        the case where a concurrent caller already won the race and moved
        it to RUNNING first. This is what prevents two callers from both
        believing they started the same task (duplicate execution).
        """
        self._transition(task_id, TaskState.RUNNING)

    def mark_interrupted(self, task_id: str, *, reason: str = "") -> None:
        """The action is known to have stopped before finishing.

        Only legal from RUNNING.
        """
        self._transition(task_id, TaskState.INTERRUPTED, result=reason or None)

    def mark_unknown(self, task_id: str, *, reason: str = "") -> None:
        """The action ran but its real effect could not be confirmed.

        Only legal from RUNNING.
        """
        self._transition(task_id, TaskState.UNKNOWN, result=reason or None)

    def mark_completed(self, task_id: str, *, result: str = "") -> None:
        """The action ran and its outcome was explicitly verified.

        Only legal from RUNNING.

        COMPLETED is terminal -- ``_ALLOWED_TRANSITIONS[COMPLETED]`` is empty,
        so a second call on an already-completed task raises instead of
        silently letting it run again.
        """
        self._transition(task_id, TaskState.COMPLETED, result=result or None)

    def approve_retry(self, task_id: str) -> str:
        """Explicit user-approved transition back to ``PENDING``.

        This is the *only* way an INTERRUPTED/UNKNOWN task may be retried --
        it must be called from a user-approval path, never automatically.
        Raises ``ValueError``/``TransitionError`` if the task is not in a
        non-retryable state (nothing to approve) or does not exist.
        """
        record = self.get(task_id)
        if record is None:
            raise ValueError(f"no such task: {task_id}")
        if record.state not in _NON_RETRYABLE:
            raise ValueError(
                f"task {task_id} is in state {record.state.value!r}, "
                "not INTERRUPTED/UNKNOWN -- nothing to approve"
            )
        self._transition(task_id, TaskState.PENDING, _from=record.state)
        return task_id

    def _transition(
        self,
        task_id: str,
        new_state: TaskState,
        *,
        result: Optional[str] = None,
        _from: Optional[TaskState] = None,
    ) -> None:
        """Atomically move *task_id* to *new_state*, enforcing the legal-
        transition graph and detecting concurrent races in one SQL statement.

        The ``WHERE state IN (...)`` clause makes this a compare-and-swap:
        the row only updates if it is currently in one of the states allowed
        to reach *new_state*. If zero rows are affected, either the task does
        not exist, it is already in an incompatible state, or another caller
        won a concurrent race and changed it first between our read and our
        write -- all three are reported as the same ``TransitionError`` so
        callers cannot distinguish "illegal" from "lost the race" and
        mistakenly retry past a real state conflict.
        """
        if _from is not None:
            allowed_from: tuple = (_from,)
        else:
            allowed_from = tuple(
                src for src, dsts in _ALLOWED_TRANSITIONS.items() if new_state in dsts
            )
        if not allowed_from:
            raise TransitionError(f"no state may transition to {new_state.value!r}")

        now = time.time()
        placeholders = ",".join("?" for _ in allowed_from)
        params: tuple = (new_state.value,)
        if result is not None:
            query = (
                f"UPDATE tasks SET state = ?, result = ?, updated_at = ? "
                f"WHERE task_id = ? AND state IN ({placeholders})"
            )
            params = (new_state.value, result, now, task_id) + tuple(
                s.value for s in allowed_from
            )
        else:
            query = (
                f"UPDATE tasks SET state = ?, updated_at = ? "
                f"WHERE task_id = ? AND state IN ({placeholders})"
            )
            params = (new_state.value, now, task_id) + tuple(
                s.value for s in allowed_from
            )
        with self._lock:
            cursor = self._conn.execute(query, params)
            self._conn.commit()
            rowcount = cursor.rowcount
        if rowcount == 0:
            current = self.get(task_id)
            if current is None:
                raise TransitionError(f"no such task: {task_id}")
            raise TransitionError(
                f"task {task_id} cannot move from {current.state.value!r} to "
                f"{new_state.value!r} (either illegal or lost a concurrent race)"
            )

    # -- queries --------------------------------------------------------------

    def get(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT task_id, description, state, result, created_at, "
                "updated_at, metadata FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_by_state(self, state: TaskState) -> List[TaskRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT task_id, description, state, result, created_at, "
                "updated_at, metadata FROM tasks WHERE state = ? ORDER BY updated_at",
                (state.value,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def can_auto_retry(self, task_id: str) -> bool:
        """False for INTERRUPTED/UNKNOWN (or a missing task) -- the hard rule."""
        record = self.get(task_id)
        if record is None:
            return False
        return record.state not in _NON_RETRYABLE

    @staticmethod
    def _row_to_record(row: tuple) -> TaskRecord:
        task_id, description, state, result, created_at, updated_at, metadata = row
        return TaskRecord(
            task_id=task_id,
            description=description,
            state=TaskState(state),
            result=result,
            created_at=created_at,
            updated_at=updated_at,
            metadata=json.loads(metadata) if metadata else {},
        )

    def close(self) -> None:
        self._conn.close()


__all__ = ["TaskRecord", "TaskState", "TaskStore"]
