"""Historial de conversaciones de JARVIS (SQLite local, administrable).

Guarda lo que dijeron usuario y asistente (para dar contexto y para que el
usuario lo consulte/borre desde la GUI). Es privado y local; se puede borrar
por sesion o por completo. No guarda los argumentos de las herramientas.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjarvis.core.config import DEFAULT_CONFIG_DIR

DEFAULT_DB = DEFAULT_CONFIG_DIR / "jarvis.db"


@dataclass
class HistoryMessage:
    id: int
    session_id: str
    ts: float
    role: str  # user | assistant
    content: str
    source: str  # text | voice | gui
    meta: dict[str, Any]


class HistoryStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        path = Path(db_path) if db_path else DEFAULT_DB
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " session_id TEXT NOT NULL, ts REAL NOT NULL, role TEXT NOT NULL,"
            " content TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'text',"
            " meta TEXT NOT NULL DEFAULT '{}')"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_session"
            " ON messages(session_id, id)"
        )
        self._conn.commit()

    def add(
        self,
        session_id: str,
        role: str,
        content: str,
        source: str = "text",
        meta: dict[str, Any] | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(session_id, ts, role, content, source, meta)"
                " VALUES (?,?,?,?,?,?)",
                (
                    session_id,
                    time.time(),
                    role,
                    content,
                    source,
                    json.dumps(meta or {}),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def recent(self, session_id: str, limit: int = 24) -> list[HistoryMessage]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, ts, role, content, source, meta FROM messages"
                " WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [
            HistoryMessage(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6]))
            for r in reversed(rows)
        ]

    def sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, COUNT(*), MIN(ts), MAX(ts) FROM messages"
                " GROUP BY session_id ORDER BY MAX(ts) DESC"
            ).fetchall()
        return [
            {"session_id": r[0], "messages": r[1], "first": r[2], "last": r[3]}
            for r in rows
        ]

    def search(self, query: str, limit: int = 30) -> list[HistoryMessage]:
        like = f"%{query}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, ts, role, content, source, meta FROM messages"
                " WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
                (like, limit),
            ).fetchall()
        return [
            HistoryMessage(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6]))
            for r in rows
        ]

    def delete_session(self, session_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            self._conn.commit()
            return cur.rowcount

    def clear(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM messages")
            self._conn.commit()
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
