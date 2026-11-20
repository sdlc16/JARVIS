"""Interfaz gráfica de JARVIS: API FastAPI local + SPA (sin dependencias externas).

Seguridad (la interfaz da acceso a un asistente con control del PC):
* solo escucha en 127.0.0.1 (el lanzador lo impone) y NUNCA se expone a Internet;
* toda ruta ``/api`` exige el token aleatorio de esta ejecución en la cabecera
  ``X-Jarvis-Token`` (comparación en tiempo constante). El token viaja en el
  *fragmento* de la URL, que el navegador no envía al servidor ni lo registra;
* la cabecera ``Host`` debe ser ``127.0.0.1``/``localhost`` (anti DNS-rebinding)
  y las peticiones que modifican estado deben venir del mismo origen;
* sin CORS; sin eco de texto privado en los registros; los permisos de acciones
  sensibles los decide el usuario (ConfirmationBroker), nunca la interfaz sola.
"""

from __future__ import annotations

import hmac
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from openjarvis.jarvis.confirmation import POLICY_VALUES, RISK
from openjarvis.jarvis.gui import settings as gui_settings
from openjarvis.jarvis.gui import status as gui_status
from openjarvis.jarvis.gui.events import EventHub

STATIC = Path(__file__).parent / "static"
LOG_NAMES = ("voice", "approvals", "bridge")
SESSION_ID_MAX = 64


def tail_jsonl(path: Path, n: int = 100) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 256 * 1024))
        lines = fh.read().decode("utf-8", "replace").splitlines()
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # linea corrupta/parcial: se ignora
    return out[-n:]


def create_app(
    *,
    runtime: Any,
    broker: Any,
    token: str,
    kb: Any = None,
    voice: Any = None,
    hub: EventHub | None = None,
    bridge_client: Any = None,
    config: Any = None,
    log_dir: Path | None = None,
    bridge_log: Path | None = None,
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost"),
    settings_path: Path | None = None,
) -> FastAPI:
    if len(token) < 16:
        raise ValueError("token demasiado corto")
    hub = hub or EventHub()
    app = FastAPI(title="JARVIS", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.hub = hub
    log_dir = log_dir or Path.home() / ".openjarvis" / "logs"
    log_files = {
        "voice": log_dir / "voice.jsonl",
        "approvals": log_dir / "approvals.jsonl",
        "bridge": bridge_log,
    }
    broker.subscribe(lambda e: hub.publish({"type": "approval_" + e["type"], **e}))

    # ---------------------------------------------------------- seguridad
    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in allowed_hosts:
            return JSONResponse({"error": "host no permitido"}, status_code=403)
        path = request.url.path
        if path.startswith("/api"):
            given = request.headers.get("x-jarvis-token", "")
            if not hmac.compare_digest(given.encode(), token.encode()):
                return JSONResponse({"error": "token inválido"}, status_code=401)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
                    return JSONResponse(
                        {"error": "origen no permitido"}, status_code=403
                    )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        return response

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    # ---------------------------------------------------------- estado
    @app.get("/api/status")
    def status() -> dict[str, Any]:
        # el host REAL del motor (la config puede estar vacia y usar OLLAMA_HOST o
        # el valor por defecto): medido en la prueba real, "" daba "Ollama caido".
        host = getattr(runtime._raw_engine, "_host", "") or "http://localhost:11434"
        info: dict[str, Any] = {
            "model": runtime.model,
            "ollama": gui_status.ollama_status(host),
            "gpu": gui_status.gpu_status(),
            "system": gui_status.system_status(),
            "voice": voice.snapshot() if voice else {"state": "off"},
            "busy": runtime._lock.locked(),
        }
        client = bridge_client or getattr(runtime, "bridge_client", None)
        if client is not None:
            info["bridge"] = gui_status.bridge_status(client)
        if kb is not None:
            try:
                info["memory"] = kb.stats()
            except Exception as exc:  # noqa: BLE001
                info["memory"] = {"error": str(exc)[:120]}
        info["pending_approvals"] = len(broker.pending())
        return info

    # ---------------------------------------------------------- chat
    @app.post("/api/chat")
    async def chat(request: Request):
        body = await request.json()
        text = str(body.get("text", "")).strip()
        sid = str(body.get("session_id") or "gui")[:SESSION_ID_MAX]
        if not text:
            raise HTTPException(400, "texto vacío")
        if len(text) > 4000:
            raise HTTPException(400, "texto demasiado largo (máx. 4000)")
        if runtime._lock.locked():
            raise HTTPException(409, "JARVIS está ocupado con otra petición")
        q: queue.Queue = queue.Queue()

        def on_event(e: dict[str, Any]) -> None:
            q.put(e)

        unsub = broker.subscribe(
            lambda e: q.put({**e, "type": "approval_" + e["type"]})
        )

        def work() -> None:
            try:
                runtime.respond(text, session_id=sid, source="gui", on_event=on_event)
            except Exception as exc:  # noqa: BLE001
                q.put({"type": "final", "text": "Error interno.", "error": str(exc)})
            finally:
                q.put(None)

        threading.Thread(target=work, daemon=True).start()

        def stream():
            try:
                while True:
                    try:
                        ev = q.get(timeout=15)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    if ev is None:
                        break
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            finally:
                unsub()

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/events")
    def events():
        q = hub.subscribe()

        def stream():
            try:
                for ev in hub.recent()[-20:]:
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                while True:
                    try:
                        ev = q.get(timeout=15)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            finally:
                hub.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # ---------------------------------------------------------- permisos
    @app.get("/api/approvals")
    def approvals() -> dict[str, Any]:
        return {
            "pending": broker.pending(),
            "recent": broker.recent()[:30],
            "policy": broker.policy,
            "risk": RISK,
            "policy_values": list(POLICY_VALUES),
        }

    @app.post("/api/approvals/{pid}")
    async def decide(pid: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body.get("approved"), bool):
            raise HTTPException(400, "falta 'approved' (true/false)")
        ok = broker.resolve(pid, body["approved"], by="gui")
        if not ok:
            raise HTTPException(404, "esa petición ya no está pendiente")
        return {"ok": True}

    @app.put("/api/policy/{tool}")
    async def set_policy(tool: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        value = body.get("value")
        if tool not in RISK:
            raise HTTPException(404, "herramienta desconocida")
        if value not in POLICY_VALUES:
            raise HTTPException(400, f"valor inválido: {POLICY_VALUES}")
        if value == "allow" and RISK[tool] == "alto":
            raise HTTPException(
                400, "las acciones de riesgo alto siempre piden permiso"
            )
        broker.set_policy(tool, value)
        hub.publish({"type": "policy_changed", "tool": tool, "value": value})
        return {"ok": True, "policy": broker.policy}

    # ---------------------------------------------------------- historial
    @app.get("/api/history/sessions")
    def sessions() -> list[dict[str, Any]]:
        return runtime.history.sessions()

    @app.get("/api/history/search")
    def history_search(q: str) -> list[dict[str, Any]]:
        return [_msg(m) for m in runtime.history.search(q[:200])]

    @app.get("/api/history/{sid}")
    def history_get(sid: str) -> list[dict[str, Any]]:
        return [_msg(m) for m in runtime.history.recent(sid[:SESSION_ID_MAX], 200)]

    @app.delete("/api/history/{sid}")
    def history_delete(sid: str) -> dict[str, Any]:
        return {"deleted": runtime.history.delete_session(sid[:SESSION_ID_MAX])}

    # ---------------------------------------------------------- memoria
    def need_kb():
        if kb is None:
            raise HTTPException(503, "memoria no disponible")
        return kb

    @app.get("/api/memory")
    def memory() -> dict[str, Any]:
        k = need_kb()
        return {
            "stats": k.stats(),
            "sources": [
                {
                    "source": s.source,
                    "kind": s.kind,
                    "chunks": s.chunks,
                    "at": s.indexed_at,
                }
                for s in k.list_sources()
            ],
            "facts": [{"id": i, "text": t} for i, t in k.list_facts()],
        }

    @app.get("/api/memory/search")
    def memory_search(q: str) -> list[dict[str, Any]]:
        return [
            {
                "id": h.id,
                "kind": h.kind,
                "source": h.source,
                "score": round(h.score, 2),
                "content": h.content[:400],
            }
            for h in need_kb().search(q[:300], 8)
        ]

    @app.post("/api/memory/remember")
    async def remember(request: Request) -> dict[str, Any]:
        text = str((await request.json()).get("text", "")).strip()
        if not text or len(text) > 2000:
            raise HTTPException(400, "texto vacío o demasiado largo")
        return {"id": need_kb().remember(text)}

    @app.delete("/api/memory/fact/{doc_id}")
    def forget(doc_id: str) -> dict[str, Any]:
        return {"ok": need_kb().forget(doc_id)}

    @app.post("/api/memory/index")
    async def index_path(request: Request) -> dict[str, Any]:
        from openjarvis.jarvis.knowledge import PathNotAllowed

        path = str((await request.json()).get("path", "")).strip()
        if not path:
            raise HTTPException(400, "falta la ruta")
        try:
            rep = need_kb().index_path(path)
        except PathNotAllowed as exc:
            raise HTTPException(403, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "indexed": len(rep.indexed),
            "unchanged": len(rep.unchanged),
            "chunks": rep.chunks,
            "skipped": rep.skipped,
        }

    @app.post("/api/memory/remove")
    async def remove_source(request: Request) -> dict[str, Any]:
        src = str((await request.json()).get("source", ""))
        return {"removed": need_kb().remove_source(src)}

    # ---------------------------------------------------------- voz
    @app.get("/api/voice")
    def voice_state() -> dict[str, Any]:
        return voice.snapshot() if voice else {"state": "off", "error": "no disponible"}

    @app.post("/api/voice/start")
    async def voice_start(request: Request) -> dict[str, Any]:
        if voice is None:
            raise HTTPException(503, "voz no disponible")
        body = await request.json() if (await request.body()) else {}
        mode = body.get("mode")
        if mode not in (None, "off", "model", "stt"):
            raise HTTPException(400, "modo inválido")
        return {"started": voice.start(mode), **voice.snapshot()}

    @app.post("/api/voice/stop")
    def voice_stop() -> dict[str, Any]:
        if voice is None:
            raise HTTPException(503, "voz no disponible")
        return {"stopped": voice.stop(), **voice.snapshot()}

    # ---------------------------------------------------------- registros
    @app.get("/api/logs/{name}")
    def logs(name: str, n: int = 100) -> list[dict[str, Any]]:
        if name not in LOG_NAMES:
            raise HTTPException(404, "registro desconocido")
        path = log_files.get(name)
        return tail_jsonl(Path(path), max(1, min(n, 500))) if path else []

    # ---------------------------------------------------------- ajustes
    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return {
            "values": gui_settings.current_values(),
            "schema": gui_settings.schema(),
        }

    @app.put("/api/settings")
    async def put_settings(request: Request) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict) or not body:
            raise HTTPException(400, "cuerpo vacío")
        try:
            applied = gui_settings.apply(body, settings_path)
        except gui_settings.SettingError as exc:
            raise HTTPException(400, str(exc)) from exc
        hub.publish({"type": "settings_changed", "keys": sorted(applied)})
        return {
            "applied": applied,
            "note": "Se aplican al (re)iniciar la voz.",
            "ts": time.time(),
        }

    return app


def _msg(m: Any) -> dict[str, Any]:
    return {
        "id": m.id,
        "session": m.session_id,
        "ts": m.ts,
        "role": m.role,
        "content": m.content,
        "source": m.source,
        "meta": m.meta,
    }
