"""Task state tracking — persistent state machine for agent-initiated actions.

Implements JARVIS_MASTER_PLAN.md §4 ("mecanismo de degradación controlada")
and DEVELOPMENT_TASKS.md T1.4: every action an agent executes (an inference
call, a Windows-bridge action, etc.) is tracked through an explicit state
machine stored in SQLite, so that an action whose outcome could not be
verified is never retried automatically.
"""

from __future__ import annotations

from openjarvis.tasks.store import TaskRecord, TaskState, TaskStore

__all__ = ["TaskRecord", "TaskState", "TaskStore"]
