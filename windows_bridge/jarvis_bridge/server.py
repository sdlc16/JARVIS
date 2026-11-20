"""Servidor HTTP del puente: autenticacion, origen, politica, registro."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from jarvis_bridge import __version__
from jarvis_bridge.actions import ROUTES, Ctx
from jarvis_bridge.security import (
    ALL_CAPABILITIES,
    SENSITIVE,
    BridgeError,
    Policy,
    origin_allowed,
)

DEFAULT_PORT = 47811
MAX_BODY = 1_000_000
MAX_LOG_BYTES = 5 * 1024 * 1024

logger = logging.getLogger("jarvis_bridge")


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "JARVIS" if base else Path.home() / ".jarvis_bridge"


def detect_wsl_interface(timeout: float = 10.0) -> tuple[str, int] | None:
    """(IPv4, prefijo) del adaptador virtual de WSL, o None si no existe aun."""
    if sys.platform != "win32":
        return None
    cmd = (
        "Get-NetIPAddress -InterfaceAlias '*WSL*' -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue | Select-Object -First 1 IPAddress,PrefixLength "
        "| ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        ).stdout.strip()
        if not out:
            return None
        info = json.loads(out)
        return str(info["IPAddress"]), int(info["PrefixLength"])
    except (subprocess.SubprocessError, ValueError, KeyError):
        return None


def _redact(route: str, params: dict[str, Any]) -> dict[str, Any]:
    """Resumen para el registro: nunca el texto tecleado ni contenido de archivos."""
    out: dict[str, Any] = {}
    for k, v in params.items():
        if k in ("text", "content"):
            out[k] = f"<{len(str(v))} chars>"
        elif k == "confirmed":
            out[k] = bool(v)
        else:
            out[k] = v if len(str(v)) <= 120 else str(v)[:117] + "..."
    return out


class BridgeServer:
    def __init__(
        self,
        policy: Policy,
        token: str,
        win: Any,
        networks: list[ipaddress._BaseNetwork],
        log_path: Path | None = None,
    ) -> None:
        self.ctx = Ctx(policy=policy, win=win)
        self.token = token
        self.networks = networks
        self.log_path = log_path

    # -- registro -------------------------------------------------------
    def log(self, event: dict[str, Any]) -> None:
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size > MAX_LOG_BYTES:
                self.log_path.replace(self.log_path.with_suffix(".jsonl.1"))
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {"ts": round(time.time(), 3), **event}, ensure_ascii=False
                    )
                    + "\n"
                )
        except OSError:
            pass

    # -- despacho (testeable sin sockets) ---------------------------------
    def handle(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        client_ip: str,
    ) -> tuple[int, dict[str, Any], dict[str, Any]]:
        """Devuelve (status, respuesta_json, resumen_para_log)."""
        t0 = time.monotonic()
        route = path.split("?", 1)[0].rstrip("/")
        meta: dict[str, Any] = {"route": route, "method": method, "client": client_ip}

        def done(status: int, payload: dict[str, Any], **extra: Any):
            meta.update(
                status=status, ms=round((time.monotonic() - t0) * 1000, 1), **extra
            )
            self.log(meta)
            return status, payload

        def fail(err: BridgeError):
            return done(
                err.status,
                {"ok": False, "error": {"code": err.code, "message": err.message}},
                error_code=err.code,
            )

        # 1) origen (defensa en profundidad ademas de la interfaz de escucha)
        if not origin_allowed(client_ip, self.networks):
            return fail(BridgeError("origin_not_allowed", "Origen no permitido.", 403))
        # 2) token
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return fail(BridgeError("missing_token", "Falta el token Bearer.", 401))
        if not secrets.compare_digest(auth[7:].strip(), self.token):
            return fail(BridgeError("invalid_token", "Token invalido.", 403))
        # 3) rutas de servicio
        if route == "/v1/health" and method == "GET":
            return done(
                200,
                {
                    "ok": True,
                    "data": {"version": __version__, "platform": sys.platform},
                },
            )
        if route == "/v1/capabilities" and method == "GET":
            pol = self.ctx.policy
            return done(
                200,
                {
                    "ok": True,
                    "data": {
                        "capabilities": {
                            c: {
                                "enabled": pol.capabilities.get(c, False),
                                "sensitive": c in SENSITIVE,
                            }
                            for c in ALL_CAPABILITIES
                        },
                        "read_roots": [str(p) for p in pol.read_roots],
                        "write_roots": [str(p) for p in pol.write_roots],
                        "apps": sorted(pol.apps),
                    },
                },
            )
        # 4) acciones
        if method != "POST" or not route.startswith("/v1/"):
            return fail(
                BridgeError("not_found", f"Ruta desconocida: {method} {route}", 404)
            )
        key = route[len("/v1/") :].replace("/", ".")
        if key not in ROUTES:
            return fail(
                BridgeError("unknown_action", f"Accion desconocida: {key}", 404)
            )
        if len(body) > MAX_BODY:
            return fail(BridgeError("too_large", "Cuerpo demasiado grande.", 413))
        try:
            params = json.loads(body.decode("utf-8")) if body else {}
            if not isinstance(params, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return fail(
                BridgeError("bad_json", "El cuerpo debe ser un objeto JSON.", 400)
            )
        meta["params"] = _redact(key, params)
        capability, fn = ROUTES[key]
        try:
            self.ctx.policy.require(capability, bool(params.get("confirmed", False)))
            data = fn(self.ctx, params)
        except BridgeError as err:
            return fail(err)
        except Exception as exc:  # noqa: BLE001 -- el servidor no debe caerse
            logger.exception("fallo interno en %s", key)
            return done(
                500,
                {
                    "ok": False,
                    "error": {
                        "code": "internal",
                        "message": f"{type(exc).__name__}: {exc}",
                    },
                },
                error_code="internal",
            )
        return done(200, {"ok": True, "data": data}, action=key)

    # -- HTTP -------------------------------------------------------------
    def make_http_server(self, host: str, port: int) -> ThreadingHTTPServer:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = f"JarvisBridge/{__version__}"

            def _serve(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(min(length, MAX_BODY + 1)) if length else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                status, payload = bridge.handle(
                    method, self.path, headers, body, self.client_address[0]
                )
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802
                self._serve("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._serve("POST")

            def log_message(self, fmt: str, *args: object) -> None:  # silencioso
                pass

        return ThreadingHTTPServer((host, port), Handler)
