"""Bus de eventos de la GUI: publicación/suscripción con colas acotadas."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Any


class EventHub:
    def __init__(self, history: int = 100) -> None:
        self._lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self._recent: deque[dict[str, Any]] = deque(maxlen=history)

    def publish(self, event: dict[str, Any]) -> None:
        event = {"ts": round(time.time(), 3), **event}
        with self._lock:
            self._recent.append(event)
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:  # cliente lento: se descarta, nunca bloquea al agente
                pass

    def subscribe(self, maxsize: int = 200) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def recent(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._recent)

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)
