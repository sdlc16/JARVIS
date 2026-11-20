"""GUI: seguridad de la API, chat con SSE y permisos, memoria, ajustes, voz, registros.

Runtime REAL (agente operative + herramientas Windows + puente HTTP real con
ventanas simuladas); solo el modelo (ScriptedEngine) y el micro/altavoz son simulados.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from openjarvis.jarvis.gui import app as gui_app
from openjarvis.jarvis.gui import status as gui_status
from openjarvis.jarvis.gui.events import EventHub
from openjarvis.jarvis.gui.voice_service import VoiceService
from openjarvis.jarvis.knowledge import KnowledgeBase
from tests.jarvis.test_runtime import make, stack, tool_call  # noqa: F401

TOKEN = "t" * 32
H = {"X-Jarvis-Token": TOKEN}
BASE = "http://127.0.0.1:8765"


class FakeVoice:
    def __init__(self):
        self.state, self.mode, self.calls = "off", "off", []

    def snapshot(self):
        return {"state": self.state, "mode": self.mode, "turns": 0, "error": ""}

    def start(self, mode=None):
        self.calls.append(("start", mode))
        if self.state != "off":
            return False
        self.state, self.mode = "listening", mode or "off"
        return True

    def stop(self):
        self.calls.append(("stop",))
        self.state = "off"
        return True


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(
        gui_status,
        "ollama_status",
        lambda host, timeout=2.0: {"up": True, "host": host, "loaded": []},
    )
    monkeypatch.setattr(gui_status, "gpu_status", lambda: {"available": False})


def build(stack, tmp_path, *script, voice=None, tools=None):  # noqa: F811
    kw = {"tools": tools} if tools else {}
    rt, engine, win = make(stack, *script, **kw)
    kb = KnowledgeBase(tmp_path / "kb.db", roots=[tmp_path / "Docs"])
    (tmp_path / "Docs").mkdir(exist_ok=True)
    rt._knowledge, rt.auto_memory = kb, False
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    app = gui_app.create_app(
        runtime=rt,
        broker=rt.broker,
        token=TOKEN,
        kb=kb,
        voice=voice,
        bridge_client=stack[0],
        log_dir=log_dir,
        bridge_log=log_dir / "bridge.jsonl",
        settings_path=tmp_path / "config.toml",
    )
    return TestClient(app, base_url=BASE), rt, win, kb, log_dir


@pytest.fixture
def ctx(stack, tmp_path):  # noqa: F811
    return build(stack, tmp_path, voice=FakeVoice())


def sse(resp):
    return [json.loads(ln[6:]) for ln in resp.iter_lines() if ln.startswith("data: ")]


# --- seguridad ------------------------------------------------------------------------


def test_api_requires_the_token_and_the_page_does_not(ctx):
    c = ctx[0]
    assert c.get("/").status_code == 200 and "JARVIS" in c.get("/").text
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"X-Jarvis-Token": "x" * 32}).status_code == 401
    assert c.get("/api/status", headers={"X-Jarvis-Token": ""}).status_code == 401
    assert c.get("/api/status", headers=H).status_code == 200
    assert c.get("/api/status?token=" + TOKEN).status_code == 401  # nunca por URL


def test_foreign_hosts_are_rejected_against_dns_rebinding(ctx):
    c = ctx[0]
    for host in ("evil.example.com", "192.168.1.5:8765", "127.0.0.1.evil.com"):
        r = c.get("/api/status", headers={**H, "Host": host})
        assert r.status_code == 403, host
    assert c.get("/", headers={"Host": "evil.com"}).status_code == 403
    assert (
        c.get("/api/status", headers={**H, "Host": "localhost:8765"}).status_code == 200
    )


def test_state_changing_requests_from_other_origins_are_rejected(ctx):
    c = ctx[0]
    bad = {**H, "Origin": "http://evil.example.com"}
    assert (
        c.post("/api/memory/remember", json={"text": "x"}, headers=bad).status_code
        == 403
    )
    ok = {**H, "Origin": BASE}
    assert (
        c.post("/api/memory/remember", json={"text": "dato"}, headers=ok).status_code
        == 200
    )


def test_security_headers_and_no_docs(ctx):
    c = ctx[0]
    r = c.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path, headers=H).status_code == 404


def test_short_token_is_refused(stack, tmp_path):  # noqa: F811
    rt, _e, _w = make(stack, "x")
    with pytest.raises(ValueError):
        gui_app.create_app(runtime=rt, broker=rt.broker, token="corto")


# --- estado ---------------------------------------------------------------------------


def test_status_aggregates_model_ollama_bridge_memory_voice(ctx):
    c, rt, _w, kb, _l = ctx
    kb.remember("hola")
    s = c.get("/api/status", headers=H).json()
    assert s["model"] == "test-model" and s["ollama"]["up"] and s["bridge"]["up"]
    assert s["memory"]["facts"] == 1 and s["voice"]["state"] == "off"
    assert s["busy"] is False and s["pending_approvals"] == 0


# --- chat -----------------------------------------------------------------------------


def test_chat_streams_tool_events_and_final_and_persists_history(ctx, stack, tmp_path):  # noqa: F811
    c, rt, _w, _kb, _l = build(
        stack,
        tmp_path,
        tool_call("windows_windows", action="list"),
        "Tienes 3 ventanas.",
    )
    with c.stream("POST", "/api/chat", json={"text": "¿ventanas?"}, headers=H) as r:
        evs = sse(r)
    kinds = [e["type"] for e in evs]
    assert kinds[:2] == ["tool_start", "tool_end"] and kinds[-1] == "final"
    assert evs[-1]["text"] == "Tienes 3 ventanas." and evs[1]["success"] is True
    h = c.get("/api/history/gui", headers=H).json()
    assert [m["role"] for m in h] == ["user", "assistant"] and h[0]["source"] == "gui"


def test_chat_input_validation_and_busy(ctx):
    c, rt, *_ = ctx
    assert c.post("/api/chat", json={"text": "  "}, headers=H).status_code == 400
    assert c.post("/api/chat", json={"text": "x" * 4001}, headers=H).status_code == 400
    with rt._lock:  # otra petición en curso
        assert c.post("/api/chat", json={"text": "hola"}, headers=H).status_code == 409


def test_sensitive_action_waits_for_gui_approval_then_executes(stack, tmp_path):  # noqa: F811
    c, rt, win, _kb, _l = build(
        stack,
        tmp_path,
        tool_call("windows_type", text="hola", title="Calculadora"),
        "Escrito y verificado.",
    )
    out: dict = {}

    def chat():
        with c.stream(
            "POST", "/api/chat", json={"text": "escribe hola"}, headers=H
        ) as r:
            out["events"] = sse(r)

    t = threading.Thread(target=chat)
    t.start()
    pending = []
    for _ in range(100):
        pending = c.get("/api/approvals", headers=H).json()["pending"]
        if pending:
            break
        time.sleep(0.05)
    assert pending and pending[0]["tool"] == "windows_type" and not win.calls
    assert (
        c.post(
            f"/api/approvals/{pending[0]['id']}", json={"approved": True}, headers=H
        ).status_code
        == 200
    )
    t.join(10)
    assert any(
        e["type"] == "final" and "verificado" in e["text"] for e in out["events"]
    )
    assert any(call[0] == "type" for call in win.calls)
    done = c.get("/api/approvals", headers=H).json()["recent"][0]
    assert done["state"] == "approved" and done["decided_by"] == "gui"
    assert (
        c.post(
            "/api/approvals/inexistente", json={"approved": True}, headers=H
        ).status_code
        == 404
    )


def test_denied_action_never_reaches_windows(stack, tmp_path):  # noqa: F811
    c, rt, win, _kb, _l = build(
        stack,
        tmp_path,
        tool_call("windows_type", text="x", title="Calculadora"),
        "Vale.",
    )

    def chat():
        with c.stream("POST", "/api/chat", json={"text": "escribe"}, headers=H) as r:
            sse(r)

    t = threading.Thread(target=chat)
    t.start()
    for _ in range(100):
        p = c.get("/api/approvals", headers=H).json()["pending"]
        if p:
            c.post(f"/api/approvals/{p[0]['id']}", json={"approved": False}, headers=H)
            break
        time.sleep(0.05)
    t.join(10)
    assert not any(call[0] == "type" for call in win.calls)
    assert c.post(
        "/api/approvals/x", json={"approved": "si"}, headers=H
    ).status_code in (400, 404)


def test_policy_rules(ctx):
    c = ctx[0]
    assert (
        c.put(
            "/api/policy/windows_type", json={"value": "allow"}, headers=H
        ).status_code
        == 200
    )
    assert (
        c.put(
            "/api/policy/windows_files_modify", json={"value": "allow"}, headers=H
        ).status_code
        == 400
    )
    assert (
        c.put(
            "/api/policy/windows_files_modify", json={"value": "deny"}, headers=H
        ).status_code
        == 200
    )
    assert (
        c.put(
            "/api/policy/windows_type", json={"value": "siempre"}, headers=H
        ).status_code
        == 400
    )
    assert (
        c.put(
            "/api/policy/herramienta_rara", json={"value": "ask"}, headers=H
        ).status_code
        == 404
    )
    pol = c.get("/api/approvals", headers=H).json()["policy"]
    assert pol == {"windows_type": "allow", "windows_files_modify": "deny"}


# --- historial ------------------------------------------------------------------------


def test_history_sessions_search_and_delete(ctx):
    c, rt, *_ = ctx
    rt.history.add("a", "user", "quiero pizza", "gui")
    rt.history.add("a", "assistant", "marchando pizza", "gui")
    rt.history.add("b", "user", "otra cosa", "voice")
    assert {
        s["session_id"] for s in c.get("/api/history/sessions", headers=H).json()
    } == {"a", "b"}
    assert len(c.get("/api/history/search?q=pizza", headers=H).json()) == 2
    assert c.delete("/api/history/a", headers=H).json() == {"deleted": 2}
    assert c.get("/api/history/a", headers=H).json() == []


# --- memoria --------------------------------------------------------------------------


def test_memory_endpoints_end_to_end(ctx, tmp_path):
    c, _rt, _w, kb, _l = ctx
    fid = c.post(
        "/api/memory/remember", json={"text": "Mi gato es Miso"}, headers=H
    ).json()["id"]
    assert (
        c.post("/api/memory/remember", json={"text": ""}, headers=H).status_code == 400
    )
    doc = tmp_path / "Docs" / "nota.txt"
    doc.write_text("La clave del armario es 1234 y está en el cajón azul.")
    r = c.post("/api/memory/index", json={"path": str(doc)}, headers=H).json()
    assert r["indexed"] == 1 and r["chunks"] == 1
    hit = c.get("/api/memory/search?q=cajon azul", headers=H).json()[0]
    assert hit["kind"] == "document" and "1234" in hit["content"]
    mem = c.get("/api/memory", headers=H).json()
    assert (
        mem["facts"][0]["text"] == "Mi gato es Miso" and mem["stats"]["documents"] == 1
    )
    outside = tmp_path / "fuera.txt"
    outside.write_text("secreto")
    assert (
        c.post("/api/memory/index", json={"path": str(outside)}, headers=H).status_code
        == 403
    )
    assert (
        c.post(
            "/api/memory/index",
            json={"path": str(tmp_path / "Docs" / "no.txt")},
            headers=H,
        ).status_code
        == 404
    )
    assert c.post(
        "/api/memory/remove", json={"source": str(doc)}, headers=H
    ).json() == {"removed": 1}
    assert doc.exists()
    assert c.delete(f"/api/memory/fact/{fid}", headers=H).json() == {"ok": True}
    assert c.get("/api/memory", headers=H).json()["facts"] == []


# --- ajustes --------------------------------------------------------------------------


def test_settings_validation_persistence_and_atomicity(ctx, tmp_path, monkeypatch):
    c = ctx[0]
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(cfg))
    from openjarvis.core.config import load_config

    load_config.cache_clear()
    r = c.put(
        "/api/settings",
        json={"voice.silence_timeout_ms": 1800, "voice.barge_in": True},
        headers=H,
    )
    assert r.status_code == 200 and r.json()["applied"] == {
        "voice.silence_timeout_ms": 1800,
        "voice.barge_in": True,
    }
    text = cfg.read_text()
    assert "silence_timeout_ms = 1800" in text and "barge_in = true" in text
    values = c.get("/api/settings", headers=H).json()["values"]
    assert (
        values["voice.silence_timeout_ms"] == 1800 and values["voice.barge_in"] is True
    )
    before = cfg.read_text()
    for bad in (
        {"voice.silence_timeout_ms": 10},  # fuera de rango
        {"voice.wake_mode": "siempre"},  # opcion invalida
        {"voice.aggressiveness": 2.5},  # no entero
        {"engine.ollama.host": "http://evil"},  # no editable
        {"voice.max_tokens": 100, "voice.aggressiveness": 9},  # mezcla: no aplica NADA
    ):
        assert c.put("/api/settings", json=bad, headers=H).status_code == 400, bad
    assert cfg.read_text() == before  # nada a medias
    assert c.put("/api/settings", json={}, headers=H).status_code == 400
    load_config.cache_clear()


# --- voz ------------------------------------------------------------------------------


def test_voice_endpoints_start_stop_and_validation(ctx):
    c, *_ = ctx
    r = c.post("/api/voice/start", json={"mode": "stt"}, headers=H).json()
    assert r["started"] is True and r["state"] == "listening" and r["mode"] == "stt"
    assert c.post("/api/voice/start", json={}, headers=H).json()["started"] is False
    assert (
        c.post("/api/voice/start", json={"mode": "raro"}, headers=H).status_code == 400
    )
    assert c.post("/api/voice/stop", json={}, headers=H).json()["state"] == "off"
    assert c.get("/api/voice", headers=H).json()["state"] == "off"


def test_voice_unavailable_returns_503(stack, tmp_path):  # noqa: F811
    c = build(stack, tmp_path, voice=None)[0]
    assert c.post("/api/voice/start", json={}, headers=H).status_code == 503
    assert c.post("/api/voice/stop", json={}, headers=H).status_code == 503


# --- registros ------------------------------------------------------------------------


def test_logs_tail_and_whitelist(ctx):
    c, *_, log_dir = ctx
    (log_dir / "voice.jsonl").write_text(
        "\n".join(json.dumps({"ts": i, "outcome": "ok"}) for i in range(5))
        + "\nbasura{\n"
    )
    rows = c.get("/api/logs/voice?n=3", headers=H).json()
    assert [r["ts"] for r in rows] == [2, 3, 4]  # ignora la linea corrupta
    assert c.get("/api/logs/approvals", headers=H).json() == []  # aun no existe
    assert c.get("/api/logs/../../etc/passwd", headers=H).status_code in (404, 422)
    assert c.get("/api/logs/secretos", headers=H).status_code == 404


# --- EventHub y VoiceService ----------------------------------------------------------


def test_event_hub_fanout_and_slow_client_never_blocks():
    hub = EventHub()
    a, slow = hub.subscribe(), hub.subscribe(maxsize=1)
    for i in range(5):
        hub.publish({"type": "x", "i": i})
    assert [a.get_nowait()["i"] for _ in range(5)] == [0, 1, 2, 3, 4]
    assert slow.qsize() == 1  # el lento perdio eventos pero no bloqueo a nadie
    hub.unsubscribe(a)
    assert hub.subscribers == 1 and len(hub.recent()) == 5


class FakePipe:
    def __init__(self, results, agent_fn):
        from openjarvis.voice.pipeline import TurnOutcome

        self.results, self.agent_fn, self.on_stage = list(results), agent_fn, None
        self.closed = False
        self.TurnOutcome = TurnOutcome
        self.confirm_channel = lambda p: None

    def run_turn(self, **kw):
        time.sleep(0.01)
        return self.results.pop(0) if self.results else _result("no_speech")

    def close(self):
        self.closed = True


def _result(outcome, text="", resp=""):
    from types import SimpleNamespace

    from openjarvis.voice.pipeline import TurnOutcome

    return SimpleNamespace(
        outcome=TurnOutcome(outcome),
        transcript=text,
        response_text=resp,
        probable_cause="",
        latency=SimpleNamespace(
            stt_seconds=0.5, agent_seconds=1.0, tts_time_to_first_audio_seconds=0.7
        ),
    )


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_voice_service_runs_turns_reports_states_and_stops_cleanly(stack, tmp_path):  # noqa: F811
    from openjarvis.core.config import JarvisConfig

    rt, *_ = make(stack, "x")
    events, pipes = [], []

    def factory(agent_fn):
        p = FakePipe([_result("ok", "hola", "buenas")], agent_fn)
        pipes.append(p)
        return p

    svc = VoiceService(rt, JarvisConfig(), events.append, pipeline_factory=factory)
    assert svc.start("off") is True and svc.start("off") is False  # no se duplica
    assert wait_for(lambda: svc.turns == 1)
    assert svc.stop() and svc.state == "off" and pipes[0].closed
    turn = next(e for e in events if e["type"] == "voice_turn")
    assert turn["transcript"] == "hola" and turn["response"] == "buenas"
    states = [e["state"] for e in events if e["type"] == "voice_state"]
    assert states[0] == "starting" and "listening" in states and states[-1] == "off"
    assert not any(
        e.get("outcome") == "no_speech" for e in events
    )  # silencio no es turno


def test_voice_service_reports_persistent_failures_instead_of_hiding_them(stack):  # noqa: F811
    from openjarvis.core.config import JarvisConfig

    rt, *_ = make(stack, "x")
    bad = [_result("capture_error") for _ in range(10)]
    svc = VoiceService(
        rt, JarvisConfig(), lambda e: None, pipeline_factory=lambda f: FakePipe(bad, f)
    )
    svc.start("off")
    assert wait_for(lambda: svc.state == "error")
    assert svc.error and not svc.running
    svc.stop()


def test_voice_service_factory_failure_is_an_error_state_not_a_crash(stack):  # noqa: F811
    from openjarvis.core.config import JarvisConfig

    rt, *_ = make(stack, "x")

    def boom(_f):
        raise RuntimeError("kokoro no carga")

    svc = VoiceService(rt, JarvisConfig(), lambda e: None, pipeline_factory=boom)
    svc.start("off")
    assert wait_for(lambda: svc.state == "error") and "kokoro" in svc.error


def test_gpu_status_parses_decimals_and_na(monkeypatch):
    """Fallo real: nvidia-smi dio temperatura 53.38 y la GPU salia no disponible."""
    import subprocess
    from types import SimpleNamespace

    monkeypatch.setattr(gui_status.shutil, "which", lambda _n: "/usr/bin/nvidia-smi")
    out = "NVIDIA GeForce RTX 4060, 6960, 8188, 0, 53.38\n"
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=out, returncode=0)
    )
    # (el autouse sustituye gpu_status: se llama a la original guardada)
    real = gui_status.__dict__["_real_gpu_status"]
    g = real()
    assert g["available"] and g["vram_used_mb"] == 6960 and g["temp_c"] == 53
    out2 = "GPU X, [N/A], 8188, [N/A], [N/A]\n"
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=out2, returncode=0)
    )
    assert real()["vram_used_mb"] == 0
    monkeypatch.setattr(gui_status.shutil, "which", lambda _n: None)
    assert real() == {"available": False}


def test_status_uses_the_real_engine_host_not_the_empty_config_value(ctx, monkeypatch):
    c, rt, *_ = ctx
    seen = {}

    def fake(host, timeout=2.0):
        seen["host"] = host
        return {"up": True, "host": host, "loaded": []}

    monkeypatch.setattr(gui_status, "ollama_status", fake)
    rt._raw_engine = type("E", (), {"_host": "http://10.1.2.3:11434"})()
    c.get("/api/status", headers=H)
    assert seen["host"] == "http://10.1.2.3:11434"
    rt._raw_engine = object()  # sin atributo: valor por defecto, nunca ""
    c.get("/api/status", headers=H)
    assert seen["host"] == "http://localhost:11434"
