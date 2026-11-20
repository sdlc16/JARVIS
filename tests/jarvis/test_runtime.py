# ruff: noqa: E501
"""JarvisRuntime: agente real (operative) + herramientas Windows + permisos + historial.

Motor = SIMULADO con guion (aisla la logica). La calidad real del modelo con
herramientas se mide aparte (tests/jarvis/test_live_model.py, marcado `live`).
"""

from __future__ import annotations

import importlib
import ipaddress
import json
import threading
from datetime import datetime

import pytest
from jarvis_bridge.security import Policy
from jarvis_bridge.server import BridgeServer

from openjarvis.core.config import JarvisConfig
from openjarvis.engine import EngineConnectionError
from openjarvis.jarvis.confirmation import ConfirmationBroker
from openjarvis.jarvis.history import HistoryStore
from openjarvis.jarvis.prompt import build_system_prompt
from openjarvis.jarvis.runtime import JarvisRuntime, strip_think
from openjarvis.windows import BridgeClient
from tests.bridge.conftest import TOKEN, FakeWin


class ScriptedEngine:
    """Responde con el siguiente elemento del guion; guarda lo que recibe."""

    engine_id = "scripted"

    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[list] = []

    def generate(self, messages, **kwargs):
        self.calls.append(list(messages))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            item = {"content": item}
        item.setdefault(
            "usage", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        )
        return item


def tool_call(name, **args):
    return {
        "content": "",
        "tool_calls": [{"id": "c1", "name": name, "arguments": json.dumps(args)}],
    }


@pytest.fixture
def stack(tmp_path):
    import openjarvis.agents.operative as op

    importlib.reload(op)  # el conftest vacia los registros entre tests
    for d in ("Desktop", "Documents/JARVIS", "Downloads"):
        (tmp_path / d).mkdir(parents=True)
    win = FakeWin(tmp_path / "_trash")
    server = BridgeServer(
        Policy.default(tmp_path), TOKEN, win, [ipaddress.ip_network("127.0.0.0/8")]
    )
    http = server.make_http_server("127.0.0.1", 0)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    client = BridgeClient("127.0.0.1", http.server_address[1], token=TOKEN, timeout=10)
    yield client, win, tmp_path
    http.shutdown()
    http.server_close()


def make(
    stack,
    *script,
    broker=None,
    tools=("windows_windows", "windows_type", "windows_system", "windows_files_modify"),
):
    client, win, tmp = stack
    engine = ScriptedEngine(*script)
    rt = JarvisRuntime(
        config=JarvisConfig(),
        engine=engine,
        model="test-model",
        tool_names=tools,
        broker=broker or ConfirmationBroker(timeout_s=2, audit_path=None),
        history=HistoryStore(tmp / "h.db"),
        bridge_client=client,
        security=False,
    )
    return rt, engine, win


def approve(p):
    return True


def deny(p):
    return False


# --- flujo con herramientas ---------------------------------------------------------------


def test_read_only_tool_flow_and_history(stack):
    rt, engine, win = make(
        stack,
        tool_call("windows_windows", action="list"),
        "Tienes 3 ventanas abiertas.",
    )
    events = []
    r = rt.respond("¿qué ventanas tengo abiertas?", on_event=events.append)
    assert r.ok and r.text == "Tienes 3 ventanas abiertas."
    assert [t.name for t in r.tools] == ["windows_windows"] and r.tools[0].success
    assert "notepad.exe" in r.tools[0].preview  # vista previa corta (160 car.)
    assert [e["type"] for e in events] == ["tool_start", "tool_end", "final"]
    # el resultado REAL de la herramienta llego al modelo en el segundo turno
    assert any("Calculadora" in str(m.content) for m in engine.calls[1])
    h = rt.history.recent("default")
    assert [(m.role, m.content) for m in h] == [
        ("user", "¿qué ventanas tengo abiertas?"),
        ("assistant", "Tienes 3 ventanas abiertas."),
    ]
    assert win.calls == []  # solo lectura: nada se toco


def test_fresh_state_hint_is_sent_with_history_but_never_stored(stack):
    rt, engine, _win = make(stack, "Uno.", "Dos.", "Tres.")
    rt.respond("primera")
    rt.respond("segunda")
    rt.respond("tercera")
    first = [str(m.content) for m in engine.calls[0]]
    third = [str(m.content) for m in engine.calls[2]]
    assert not any("no reutilices datos" in s for s in first)  # 1er turno: sin pista
    assert any("no reutilices datos" in s for s in third)  # con historial: se avisa
    assert any(s == "Uno." for s in third)  # el historial va tal cual (sin prefijos)
    assert not any("Sistema:" in m.content for m in rt.history.recent("default"))


def _with_memory(stack, tmp_path, *script):
    from openjarvis.jarvis.knowledge import KnowledgeBase

    kb = KnowledgeBase(tmp_path / "kb.db", roots=None)
    rt, engine, _win = make(stack, *script)
    rt._knowledge, rt.auto_memory = kb, True
    return rt, engine, kb


def test_relevant_memory_is_retrieved_automatically_and_handed_over_as_data(
    stack, tmp_path
):
    rt, engine, kb = _with_memory(stack, tmp_path, "Se llama Miso.")
    kb.remember("Mi gato se llama Miso")
    rt.respond("¿Cómo se llama mi gato?")
    sent = str(engine.calls[0][-1].content)
    assert (
        "Miso" in sent and "DATOS, no órdenes" in sent and "recuerdo guardado" in sent
    )
    assert (
        "Memoria de JARVIS" not in rt.history.recent("default")[0].content
    )  # no se guarda


def test_when_nothing_is_stored_the_model_is_told_not_to_invent(stack, tmp_path):
    rt, engine, _kb = _with_memory(stack, tmp_path, "No lo tengo guardado.")
    rt.respond("¿Cuál es el teléfono de mi dentista?")
    sent = str(engine.calls[0][-1].content)
    assert "no hay nada guardado" in sent and "no inventes" in sent


def test_memory_failure_never_breaks_the_conversation(stack, tmp_path):
    rt, engine, _kb = _with_memory(stack, tmp_path, "Hola.")

    class Boom:
        def search(self, *a, **k):
            raise RuntimeError("db corrupta")

    rt._knowledge = Boom()
    assert rt.respond("hola").ok and "Memoria de JARVIS" not in str(
        engine.calls[0][-1].content
    )


def test_memory_hints_are_off_without_the_memory_tool(stack):
    rt, engine, _w = make(stack, "Hola.")
    assert rt.auto_memory is False
    rt.respond("hola")
    assert "Memoria de JARVIS" not in str(engine.calls[0][-1].content)


def test_claimed_action_without_tool_is_retried_and_then_really_done(stack):
    rt, engine, win = make(
        stack,
        "Escribí «hola» en la ventana.",  # mentira: no llamo a nada
        tool_call("windows_windows", action="list"),
        "Tienes 3 ventanas abiertas.",
    )
    r = rt.respond("escribe hola")
    assert r.text == "Tienes 3 ventanas abiertas."
    assert [t.name for t in r.tools] == ["windows_windows"]
    assert "NO has llamado" in str(engine.calls[1][-1].content)  # corrección enviada


def test_claimed_action_without_tool_twice_is_never_reported_as_done(stack):
    rt, _engine, win = make(stack, "Escribí «hola».", "Ya escribí «hola», listo.")
    r = rt.respond("escribe hola")
    assert r.tools == []
    assert "no puedo confirmar" in r.text and "Escribí" not in r.text
    assert win.calls == []


def test_plain_answer_without_action_claim_is_untouched(stack):
    rt, engine, _win = make(stack, "Son las tres y media.")
    r = rt.respond("¿qué hora es?")
    assert r.text == "Son las tres y media." and len(engine.calls) == 1


def test_sensitive_tool_approved_executes_and_verifies(stack):
    broker = ConfirmationBroker(timeout_s=2, audit_path=None)
    rt, engine, win = make(
        stack,
        tool_call("windows_type", text="12+3", title="Calculadora"),
        "Hecho: escribí 12+3.",
        broker=broker,
    )
    r = rt.respond("escribe 12+3 en la calculadora", confirm_channels=[approve])
    assert r.ok and ("type", 102, "12+3") in win.calls
    assert (
        broker.recent()[0]["tool"] == "windows_type"
        and broker.recent()[0]["state"] == "approved"
    )


def test_sensitive_tool_denied_never_reaches_windows(stack):
    rt, engine, win = make(
        stack,
        tool_call("windows_type", text="secreto", title="Calculadora"),
        "Entendido, no lo escribo.",
    )
    r = rt.respond("escribe secreto", confirm_channels=[deny])
    assert r.ok and win.calls == []
    assert r.tools == []  # una herramienta denegada no llega a ejecutarse (sin eventos)
    assert any(
        "denied" in str(m.content).lower() for m in engine.calls[1]
    )  # el modelo lo supo


def test_unanswered_confirmation_times_out_and_denies(stack):
    broker = ConfirmationBroker(timeout_s=0.3, audit_path=None)
    rt, _, win = make(
        stack,
        tool_call("windows_type", text="x", title="Calculadora"),
        "No pude.",
        broker=broker,
    )
    rt.respond("escribe x")
    assert win.calls == [] and broker.recent()[0]["state"] == "timeout"


def test_policy_allow_runs_without_asking(stack):
    broker = ConfirmationBroker(
        timeout_s=0.3, policy={"windows_type": "allow"}, audit_path=None
    )
    rt, _, win = make(
        stack,
        tool_call("windows_type", text="1", title="Calculadora"),
        "ok",
        broker=broker,
    )
    rt.respond("escribe 1")
    assert ("type", 102, "1") in win.calls and broker.recent()[0][
        "decided_by"
    ] == "politica:allow"


def test_file_modification_outside_workspace_is_refused_by_the_bridge(stack):
    client, win, tmp = stack
    rt, engine, win = make(
        stack,
        tool_call(
            "windows_files_modify",
            action="write",
            path=str(tmp / "Desktop" / "x.txt"),
            content="a",
        ),
        "No pude guardar ahí.",
    )
    r = rt.respond("guarda un archivo en el escritorio", confirm_channels=[approve])
    assert not (tmp / "Desktop" / "x.txt").exists()
    assert any("path_not_allowed" in str(m.content) for m in engine.calls[1])
    assert r.ok


# --- contexto, errores y prompt ------------------------------------------------------------------


def test_second_turn_includes_previous_conversation(stack):
    rt, engine, _ = make(stack, "Hola, soy JARVIS.", "Me dijiste hola.")
    rt.respond("hola", session_id="s1")
    rt.respond("¿qué te dije?", session_id="s1")
    second = [str(m.content) for m in engine.calls[1]]
    assert "hola" in second and "Hola, soy JARVIS." in second


def test_sessions_are_isolated(stack):
    rt, engine, _ = make(stack, "uno", "dos")
    rt.respond("mensaje A", session_id="a")
    rt.respond("mensaje B", session_id="b")
    assert not any("mensaje A" in str(m.content) for m in engine.calls[1])


def test_engine_down_returns_error_reply_and_does_not_pollute_history(stack):
    rt, _, _ = make(stack, EngineConnectionError("Ollama not reachable"))
    r = rt.respond("hola")
    assert not r.ok and "EngineConnectionError" in r.error and r.text
    assert rt.history.recent("default") == []


def test_think_blocks_are_stripped():
    assert strip_think("<think>razono...</think>Hola") == "Hola"
    assert strip_think("<think>sin cerrar y truncado") == ""
    assert strip_think("sin think") == "sin think"


def test_system_prompt_has_date_rules_and_voice_style():
    now = datetime(2026, 11, 20, 9, 5).astimezone()
    text = build_system_prompt(voice=False, now=now)
    assert "viernes 20 de noviembre de 2026" in text and "09:05" in text
    assert "NUNCA inventes" in text and "windows_*" in text and "verified" in text
    assert "DATOS, no órdenes" in text  # defensa contra inyeccion de instrucciones
    assert "LEER EN VOZ ALTA" not in text
    assert "LEER EN VOZ ALTA" in build_system_prompt(voice=True, now=now)


def test_agent_receives_the_jarvis_system_prompt(stack):
    rt, engine, _ = make(stack, "ok")
    rt.respond("hola", voice=True)
    system = [
        m for m in engine.calls[0] if str(getattr(m.role, "value", m.role)) == "system"
    ]
    assert (
        system
        and "NUNCA inventes" in system[0].content
        and "VOZ ALTA" in system[0].content
    )


def test_history_store_management(tmp_path):
    h = HistoryStore(tmp_path / "x.db")
    h.add("a", "user", "uno ñ")
    h.add("a", "assistant", "dos")
    h.add("b", "user", "tres")
    assert [s["session_id"] for s in h.sessions()] == ["b", "a"]
    assert [m.content for m in h.search("ñ")] == ["uno ñ"]
    assert h.delete_session("a") == 2 and [s["session_id"] for s in h.sessions()] == [
        "b"
    ]
    assert h.clear() == 1 and h.sessions() == []
