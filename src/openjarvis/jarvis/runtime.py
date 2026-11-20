"""JarvisRuntime: el cerebro unico que usan la voz, la GUI y la CLI.

Une: motor (Ollama), agente ``operative`` con llamadas a herramientas
nativas, herramientas de Windows + memoria, broker de confirmaciones,
historial persistente y seguridad. Un solo sitio => mismas reglas de permisos
y mismo comportamiento por voz, texto o interfaz grafica.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openjarvis.agents._stubs import AgentContext
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import Conversation, Message, Role
from openjarvis.jarvis.confirmation import ConfirmationBroker
from openjarvis.jarvis.history import HistoryStore
from openjarvis.jarvis.prompt import build_system_prompt

logger = logging.getLogger(__name__)

DEFAULT_TOOLS = (
    "windows_apps",
    "windows_windows",
    "windows_close_window",
    "windows_type",
    "windows_hotkey",
    "windows_click",
    "windows_ui_inspect",
    "windows_ui_act",
    "windows_files",
    "windows_files_modify",
    "windows_system",
    "calculator",
    "think",
    "memory_store",
    "memory_search",
)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think(text: str) -> str:
    """Quita el razonamiento <think>…</think> de modelos tipo qwen3."""
    out = _THINK_RE.sub("", text or "")
    if "<think>" in out:  # razonamiento sin cerrar (truncado por max_tokens)
        out = out.split("<think>")[0]
    return out.strip()


_CLAIM_RE = re.compile(
    r"\b(escrib[ií]|abr[ií]|cerr[eé]|envi[eé]|borr[eé]|guard[eé]|puls[eé]|"
    r"mov[ií]|cre[eé]|instal[eé]|ejecut[eé]|apagu[eé]|minimic[eé]|maximic[eé]|"
    r"he (escrito|abierto|cerrado|enviado|borrado|guardado|pulsado|movido|"
    r"creado|ejecutado))\b",
    re.IGNORECASE,
)


STALE_NOTE = (
    "[Respondí consultando el PC con herramientas; ese dato ya está caducado: "
    "si hace falta, vuelve a consultarlo] "
)


FRESH_HINT = (
    "\n\n[Sistema: si la petición depende del estado actual del PC (ventanas, "
    "apps abiertas, archivos, sistema), consulta ahora con las herramientas; "
    "no reutilices datos de mensajes anteriores.]"
)


def _context_messages(history: list[Any]) -> list[Message]:
    """Historial tal y como lo ve el modelo.

    Solo la ULTIMA respuesta del asistente va completa (para entender un "sí" o
    "ese"). Las anteriores se acortan y llevan una nota de caducidad: medido en
    E2E real, el modelo copiaba ventanas/estado viejos del historial sin volver
    a consultar el PC (y sin llamar a ninguna herramienta).
    """
    last_assistant = max(
        (i for i, m in enumerate(history) if m.role == "assistant"), default=-1
    )
    out = []
    for i, m in enumerate(history):
        content = m.content
        if m.role == "assistant" and i != last_assistant:
            content = STALE_NOTE + content[:80]
        out.append(Message(role=Role(m.role), content=content))
    return out


def claims_action(text: str) -> bool:
    """True si el texto afirma (1a persona, pasado) haber realizado una accion."""
    return bool(_CLAIM_RE.search(text or ""))


@dataclass
class ToolEvent:
    name: str
    success: bool
    latency: float
    preview: str


@dataclass
class RuntimeReply:
    text: str
    tools: list[ToolEvent] = field(default_factory=list)
    turns: int = 0
    latency_s: float = 0.0
    session_id: str = "default"
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


EventCallback = Callable[[dict[str, Any]], None]


class JarvisRuntime:
    def __init__(
        self,
        *,
        config: Any = None,
        engine: Any = None,
        model: str = "",
        tool_names: tuple[str, ...] | list[str] | None = None,
        broker: ConfirmationBroker | None = None,
        history: HistoryStore | None = None,
        bridge_client: Any = None,
        agent_name: str = "operative",
        max_history_messages: int = 12,
        security: bool = True,
    ) -> None:
        if config is None:
            from openjarvis.core.config import load_config

            config = load_config()
        self.config = config
        self.model = model or config.intelligence.default_model
        self.agent_name = agent_name
        self.tool_names = tuple(tool_names if tool_names is not None else DEFAULT_TOOLS)
        self.broker = broker or ConfirmationBroker()
        self.history = history or HistoryStore()
        self.bridge_client = bridge_client
        self.max_history_messages = max_history_messages
        self._lock = threading.Lock()  # un turno de agente a la vez

        self._bus = EventBus(record_history=False)
        self._capability_policy = None
        if engine is None:
            from openjarvis.engine import get_engine

            resolved = get_engine(config, config.intelligence.preferred_engine or None)
            if resolved is None:
                raise RuntimeError("No hay motor de inferencia (¿Ollama detenido?).")
            engine = resolved[1]
        # keep_alive ANTES de envolver el motor (los envoltorios no lo reenvian)
        self._raw_engine = engine
        if hasattr(engine, "set_keep_alive"):
            engine.set_keep_alive(config.voice.ollama_keep_alive)
        if security:
            from openjarvis.security import setup_security

            sec = setup_security(config, engine, self._bus)
            engine = sec.engine
            self._capability_policy = sec.capability_policy
        self.engine = engine

    # ------------------------------------------------------------------
    def _instantiate_tools(self) -> list[Any]:
        from openjarvis.tools import windows_tools

        tools: list[Any] = []
        others: list[str] = []
        for name in self.tool_names:
            if name.startswith("windows_"):
                cls = next(
                    (
                        c
                        for c in vars(windows_tools).values()
                        if isinstance(c, type)
                        and getattr(c, "tool_id", None) == name
                        and c.__module__ == windows_tools.__name__
                    ),
                    None,
                )
                if cls is not None:
                    tools.append(cls(self.bridge_client))
            else:
                others.append(name)
        if others:
            import openjarvis.tools  # noqa: F401 -- registra las herramientas
            from openjarvis.cli.ask import _build_tools

            tools.extend(_build_tools(others, self.config, self.engine, self.model))
        return tools

    def preload(self) -> float:
        """Carga el modelo en memoria sin generar (Ollama). Devuelve segundos."""
        fn = getattr(self._raw_engine, "preload", None)
        if fn is None:
            return 0.0
        return float(fn(self.model))

    def windows_info(self) -> dict | None:
        """Rutas/apps permitidas segun el puente (cache 60 s; None si no hay puente)."""
        now = time.monotonic()
        cached = getattr(self, "_win_info", None)
        if cached is not None and now - cached[0] < 60:
            return cached[1]
        info: dict | None = None
        if any(n.startswith("windows_") for n in self.tool_names):
            try:
                from openjarvis.windows import BridgeClient

                caps = (self.bridge_client or BridgeClient()).capabilities()
                info = {
                    "read_roots": caps.get("read_roots", []),
                    "write_roots": caps.get("write_roots", []),
                    "apps": caps.get("apps", []),
                }
            except Exception:  # noqa: BLE001 -- sin puente el agente sigue (sin herramientas utiles)
                info = None
        self._win_info = (now, info)
        return info

    def _make_agent(
        self,
        *,
        voice: bool,
        confirm: Callable[[str], bool],
        max_tokens: int,
        temperature: float,
    ) -> Any:
        import openjarvis.agents  # noqa: F401 -- registra agentes
        from openjarvis.core.registry import AgentRegistry

        cls = AgentRegistry.get(self.agent_name)
        kwargs: dict[str, Any] = {
            "bus": self._bus,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": self._instantiate_tools(),
            "max_turns": 8,
            "interactive": True,
            "confirm_callback": confirm,
            "system_prompt": build_system_prompt(
                voice=voice, windows_info=self.windows_info()
            ),
        }
        if self._capability_policy is not None:
            kwargs["capability_policy"] = self._capability_policy
        return cls(self.engine, self.model, **kwargs)

    # ------------------------------------------------------------------
    def respond(
        self,
        text: str,
        *,
        session_id: str = "default",
        source: str = "text",
        voice: bool = False,
        max_tokens: int | None = None,
        temperature: float = 0.3,
        on_event: EventCallback | None = None,
        confirm_channels: list[Any] | None = None,
    ) -> RuntimeReply:
        """Un turno: contexto -> agente (con herramientas/permisos) -> historial."""
        t0 = time.monotonic()
        max_tokens = max_tokens or (self.config.voice.max_tokens if voice else 800)
        msgs = _context_messages(
            self.history.recent(session_id, self.max_history_messages)
        )
        ctx = AgentContext(conversation=Conversation(messages=msgs))
        tools_seen: list[ToolEvent] = []
        pending_names: list[str] = []

        def emit(event: dict[str, Any]) -> None:
            if on_event is not None:
                try:
                    on_event(event)
                except Exception:  # noqa: BLE001
                    logger.exception("on_event fallo")

        def on_start(ev: Any) -> None:
            name = ev.data.get("tool", "")
            pending_names.append(name)
            emit({"type": "tool_start", "tool": name})

        def on_end(ev: Any) -> None:
            d = ev.data
            te = ToolEvent(
                name=d.get("tool", ""),
                success=bool(d.get("success")),
                latency=float(d.get("latency", 0.0)),
                preview=str(d.get("result", ""))[:160],
            )
            tools_seen.append(te)
            emit(
                {
                    "type": "tool_end",
                    "tool": te.name,
                    "success": te.success,
                    "latency": round(te.latency, 2),
                    "preview": te.preview,
                }
            )

        self._bus.subscribe(EventType.TOOL_CALL_START, on_start)
        self._bus.subscribe(EventType.TOOL_CALL_END, on_end)

        callback = self.broker.callback(channels=confirm_channels)
        try:
            with self._lock:
                agent = self._make_agent(
                    voice=voice,
                    confirm=callback,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                run_text = text + FRESH_HINT if msgs else text
                result = agent.run(run_text, context=ctx)
                if not tools_seen and claims_action(strip_think(result.content)):
                    # Afirma haber actuado sin llamar a ninguna herramienta
                    # (visto en E2E real: copio un "Escribi..." del historial).
                    logger.warning("accion afirmada sin herramienta; reintento")
                    result = agent.run(
                        text + "\n\n[Sistema: en este turno NO has llamado a ninguna "
                        "herramienta, así que no has hecho nada todavía. Llama "
                        "ahora a la herramienta adecuada y cuenta solo el "
                        "resultado real.]",
                        context=ctx,
                    )
            reply_text = strip_think(result.content)
            if not tools_seen and claims_action(reply_text):
                reply_text = (
                    "No he llegado a ejecutar esa acción, así que no puedo "
                    "confirmar que se haya hecho. ¿Quieres que lo intente de nuevo?"
                )
            reply = RuntimeReply(
                text=reply_text or "No tengo una respuesta para eso.",
                tools=tools_seen,
                turns=result.turns,
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001 -- Ollama caido, etc.
            logger.exception("fallo del agente")
            reply = RuntimeReply(
                text="No he podido procesar eso ahora mismo.",
                error=f"{type(exc).__name__}: {exc}",
                tools=tools_seen,
                session_id=session_id,
            )
        finally:
            self._bus.unsubscribe(EventType.TOOL_CALL_START, on_start)
            self._bus.unsubscribe(EventType.TOOL_CALL_END, on_end)

        reply.latency_s = time.monotonic() - t0
        if reply.ok:
            self.history.add(session_id, "user", text, source)
            self.history.add(
                session_id,
                "assistant",
                reply.text,
                source,
                {"tools": [{"name": t.name, "ok": t.success} for t in reply.tools]},
            )
        emit({"type": "final", "text": reply.text, "error": reply.error})
        return reply
