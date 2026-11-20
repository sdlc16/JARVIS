"""Confirmaciones del usuario para acciones sensibles (GUI, voz, CLI).

El agente llama a ``broker.callback()`` (el ``confirm_callback`` que ya espera
``ToolExecutor``). Cada peticion queda *pendiente* hasta que un canal la
resuelve: la GUI (``resolve``), la voz (canal que pregunta y escucha "si/no") o
la politica (por herramienta: ask | allow | deny). Sin respuesta en
``timeout_s`` se **deniega** (nunca se asume que si).

Auditoria en JSONL: herramienta, decision, quien decidio y tiempos -- NUNCA los
argumentos (pueden contener texto privado tecleado).
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openjarvis.core.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)

AUDIT_PATH = DEFAULT_CONFIG_DIR / "logs" / "approvals.jsonl"
POLICY_VALUES = ("ask", "allow", "deny")

_PROMPT_RE = re.compile(
    r"^Allow execution of tool '(?P<tool>[^']+)' with args (?P<args>.*)\?$", re.DOTALL
)

# nivel de riesgo mostrado en la interfaz
RISK = {
    "windows_close_window": "medio",
    "windows_type": "medio",
    "windows_hotkey": "medio",
    "windows_click": "medio",
    "windows_ui_act": "medio",
    "windows_files_modify": "alto",
}


def parse_prompt(prompt: str) -> tuple[str, dict[str, Any]]:
    """Extrae (herramienta, argumentos) del texto que genera ToolExecutor."""
    m = _PROMPT_RE.match(prompt.strip())
    if not m:
        return "", {}
    try:
        args = ast.literal_eval(m.group("args"))
        return m.group("tool"), args if isinstance(args, dict) else {}
    except (ValueError, SyntaxError):
        return m.group("tool"), {}


def _short(text: Any, n: int = 80) -> str:
    s = str(text).replace("\n", " ⏎ ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _window(args: dict[str, Any]) -> str:
    if args.get("title"):
        return f"la ventana «{_short(args['title'], 40)}»"
    if args.get("hwnd"):
        return f"la ventana #{args['hwnd']}"
    return "la ventana indicada"


def describe_action(tool: str, args: dict[str, Any]) -> str:
    """Frase en espanol de lo que se va a hacer (lo que ve/oye el usuario)."""
    if tool == "windows_type":
        verb = "Pegar" if args.get("method") == "paste" else "Escribir"
        return f"{verb} «{_short(args.get('text', ''))}» en {_window(args)}"
    if tool == "windows_hotkey":
        return f"Pulsar {' + '.join(args.get('keys', []))} en {_window(args)}"
    if tool == "windows_click":
        return f"Hacer clic en la pantalla en ({args.get('x')}, {args.get('y')})"
    if tool == "windows_close_window":
        return f"Cerrar {_window(args)}"
    if tool == "windows_ui_act":
        if args.get("action") == "read_text":
            return f"Leer el texto que muestra {_window(args)}"
        return (
            f"Pulsar el control «{_short(args.get('name', ''), 40)}» en {_window(args)}"
        )
    if tool == "windows_files_modify":
        action = args.get("action")
        if action == "write":
            n = len(str(args.get("content", "")))
            path = _short(args.get("path", ""), 70)
            return f"Guardar el archivo {path} ({n} caracteres)"
        if action == "move":
            src, dst = _short(args.get("src", ""), 40), _short(args.get("dst", ""), 40)
            return f"Mover {src} a {dst}"
        if action == "trash":
            return f"Enviar a la papelera {_short(args.get('path', ''), 70)}"
    return f"Ejecutar {tool} con {_short(json.dumps(args, ensure_ascii=False), 100)}"


def _norm(text: str) -> list[str]:
    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z]+", t)


_YES = {
    "si", "claro", "vale", "confirmo", "adelante", "hazlo", "dale", "ok", "okay",
    "correcto", "afirmativo", "acuerdo", "supuesto", "perfecto", "procede",
    "autorizo", "yes",
}  # fmt: skip
_NO = {
    "no", "cancela", "cancelar", "cancelalo", "para", "detente", "negativo",
    "nada", "olvidalo", "denegar", "deniego", "nunca", "jamas", "tampoco",
}  # fmt: skip


def parse_yes_no(text: str) -> bool | None:
    """Interpreta una respuesta hablada. Ante duda o mezcla, NO (mas seguro)."""
    words = _norm(text)
    if any(w in _NO for w in words):
        return False
    if any(w in _YES for w in words):
        return True
    # tolerancia a errores del STT en palabras clave largas (p. ej. "hazlo")
    import difflib

    if any(
        len(w) >= 4
        and difflib.get_close_matches(w, ("hazlo", "adelante", "confirmo"), 1, 0.8)
        for w in words
    ):
        return True
    return None


@dataclass
class Pending:
    id: str
    tool: str
    args: dict[str, Any]
    description: str
    risk: str
    created: float
    state: str = "pending"  # pending | approved | denied | timeout | policy
    decided_by: str = ""
    decided_at: float = 0.0
    _event: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "description": self.description,
            "risk": self.risk,
            "state": self.state,
            "decided_by": self.decided_by,
            "created": self.created,
        }


Channel = Callable[[Pending], "bool | None"]


class ConfirmationBroker:
    def __init__(
        self,
        timeout_s: float = 60.0,
        policy: dict[str, str] | None = None,
        audit_path: Path | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self.policy: dict[str, str] = dict(policy or {})
        self.audit_path = audit_path if audit_path is not None else AUDIT_PATH
        self._lock = threading.Lock()
        self._pending: dict[str, Pending] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=200)
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self.channels: list[Channel] = []

    # -- observadores (GUI / voz) ------------------------------------------
    def subscribe(self, fn: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def _emit(self, kind: str, p: Pending) -> None:
        event = {"type": kind, **p.public()}
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:  # noqa: BLE001 -- un observador roto no frena al agente
                logger.exception("listener de confirmacion fallo")

    # -- API ---------------------------------------------------------------
    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [p.public() for p in self._pending.values() if p.state == "pending"]

    def recent(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._recent)

    def resolve(self, pid: str, approved: bool, by: str = "gui") -> bool:
        with self._lock:
            p = self._pending.get(pid)
            if p is None or p.state != "pending":
                return False
            p.state = "approved" if approved else "denied"
            p.decided_by, p.decided_at = by, time.time()
            p._event.set()
        return True

    def set_policy(self, tool: str, value: str) -> None:
        if value not in POLICY_VALUES:
            raise ValueError(f"politica invalida: {value}")
        self.policy[tool] = value

    def request(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        timeout: float | None = None,
        channels: list[Channel] | None = None,
    ) -> bool:
        """Bloquea hasta que alguien decide. Devuelve True solo si se aprueba."""
        p = Pending(
            id=uuid.uuid4().hex[:8],
            tool=tool,
            args=args,
            description=describe_action(tool, args),
            risk=RISK.get(tool, "medio"),
            created=time.time(),
        )
        mode = self.policy.get(tool, "ask")
        if mode in ("allow", "deny"):
            p.state = "policy"
            p.decided_by = f"politica:{mode}"
            p.decided_at = time.time()
            self._finish(p, mode == "allow")
            return mode == "allow"

        with self._lock:
            self._pending[p.id] = p
        self._emit("requested", p)

        def run_channel(ch: Channel) -> None:
            try:
                decision = ch(p)
            except Exception:  # noqa: BLE001
                logger.exception("canal de confirmacion fallo")
                return
            if decision is not None:
                self.resolve(
                    p.id, bool(decision), by=getattr(ch, "channel_name", "canal")
                )

        for ch in (channels or []) + self.channels:
            threading.Thread(target=run_channel, args=(ch,), daemon=True).start()

        got = p._event.wait(timeout if timeout is not None else self.timeout_s)
        if not got:
            with self._lock:
                if p.state == "pending":
                    p.state, p.decided_by, p.decided_at = (
                        "timeout",
                        "timeout",
                        time.time(),
                    )
                    p._event.set()
        approved = p.state == "approved"
        self._finish(p, approved)
        return approved

    def callback(self, **kw: Any) -> Callable[[str], bool]:
        """``confirm_callback`` para ToolExecutor (recibe el texto del aviso)."""

        def cb(prompt: str) -> bool:
            tool, args = parse_prompt(prompt)
            if not tool:  # formato inesperado: se deniega
                return False
            return self.request(tool, args, **kw)

        return cb

    def _finish(self, p: Pending, approved: bool) -> None:
        with self._lock:
            self._pending.pop(p.id, None)
            self._recent.appendleft(p.public())
        self._emit("resolved", p)
        self._audit(p, approved)

    def _audit(self, p: Pending, approved: bool) -> None:
        if self.audit_path is None:
            return
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "ts": round(time.time(), 3),
                            "id": p.id,
                            "tool": p.tool,
                            "approved": approved,
                            "state": p.state,
                            "by": p.decided_by,
                            "wait_s": round(p.decided_at - p.created, 2),
                        }
                    )
                    + "\n"
                )
        except OSError:
            pass
