"""Cliente HTTP del puente de Windows (corre en WSL2).

* Descubre la IP del host Windows (gateway de la red NAT de WSL2): ``localhost``
  desde WSL2 NO llega a Windows (medido en T1.5).
* Obtiene el token SIN dejarlo en disco: variable de entorno si existe; si no,
  pide a Windows (interop) que lo descifre con DPAPI y lo guarda solo en memoria.
* Nunca registra el token; los errores llevan un codigo estable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

import httpx

DEFAULT_PORT = 47811
_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: dict[str, str] = {}


class BridgeError(Exception):
    """Error base del puente."""


class BridgeUnavailable(BridgeError):
    """No se pudo conectar con el puente (¿no esta en marcha?)."""


class BridgeActionError(BridgeError):
    """El puente rechazo o fallo la accion (codigo estable en ``code``)."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.status = code, message, status


def default_host() -> str:
    """IP del host Windows vista desde WSL2 (gateway por defecto)."""
    env = os.environ.get("JARVIS_BRIDGE_HOST")
    if env:
        return env
    try:
        out = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    return "127.0.0.1"


def windows_bridge_dir() -> str | None:
    """Ruta Windows (C:\\...) de la carpeta windows_bridge del repo, o None."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "windows_bridge"
        if (cand / "jarvis_bridge").is_dir():
            m = re.match(r"^/mnt/([a-z])/(.*)$", cand.as_posix())
            if m:
                return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\")
            return None
    return None


def fetch_token() -> str:
    """Token del puente: entorno -> cache en memoria -> descifrado por Windows."""
    env = os.environ.get("JARVIS_BRIDGE_TOKEN")
    if env:
        return env
    with _TOKEN_LOCK:
        if "t" in _TOKEN_CACHE:
            return _TOKEN_CACHE["t"]
        wdir = windows_bridge_dir()
        if wdir is None:
            raise BridgeUnavailable(
                "No encuentro windows_bridge en una unidad de Windows; "
                "define JARVIS_BRIDGE_TOKEN."
            )
        try:
            proc = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"Set-Location '{wdir}'; python -m jarvis_bridge --print-token",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BridgeUnavailable(f"No pude pedir el token a Windows: {exc}") from exc
        token = proc.stdout.strip()
        if proc.returncode != 0 or not token:
            raise BridgeUnavailable(
                "Windows no devolvio el token del puente "
                f"({proc.stderr.strip()[:120] or 'sin salida'})."
            )
        _TOKEN_CACHE["t"] = token
        return token


def forget_token() -> None:
    with _TOKEN_LOCK:
        _TOKEN_CACHE.clear()


# Tiempo maximo por tipo de accion (segundos): escribir/leer UI es lento.
_TIMEOUTS = {"input.type": 90.0, "input.paste": 60.0, "ui.": 45.0, "apps.open": 30.0}


class BridgeClient:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.host = host or default_host()
        self.port = int(port or os.environ.get("JARVIS_BRIDGE_PORT") or DEFAULT_PORT)
        self._token = token
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            self._token = fetch_token()
        return {"Authorization": f"Bearer {self._token}"}

    def _request(
        self, method: str, path: str, body: dict | None, timeout: float
    ) -> Any:
        try:
            resp = httpx.request(
                method,
                self.base_url + path,
                headers=self._headers(),
                json=body if method == "POST" else None,
                timeout=timeout,
            )
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.TimeoutException,
        ) as exc:
            raise BridgeUnavailable(
                f"El puente de Windows no responde en {self.base_url} "
                "(arrancalo con scripts\\start-jarvis.ps1)."
            ) from exc
        except httpx.TransportError as exc:
            raise BridgeUnavailable(f"Fallo de red con el puente: {exc}") from exc
        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise BridgeActionError(
                "bad_response",
                f"Respuesta no JSON ({resp.status_code})",
                resp.status_code,
            ) from exc
        if (
            resp.status_code == 403
            and payload.get("error", {}).get("code") == "invalid_token"
        ):
            forget_token()  # token rotado: se vuelve a pedir en la siguiente llamada
        if not payload.get("ok"):
            err = payload.get("error", {})
            raise BridgeActionError(
                err.get("code", "unknown"), err.get("message", ""), resp.status_code
            )
        return payload["data"]

    def call(self, route: str, confirmed: bool = False, **params: Any) -> dict:
        """``call("windows.list")`` -> datos. ``route`` usa puntos: 'files.read'."""
        timeout = self.timeout
        for prefix, t in _TIMEOUTS.items():
            if route.startswith(prefix):
                timeout = max(timeout, t)
        body = {k: v for k, v in params.items() if v is not None}
        if confirmed:
            body["confirmed"] = True
        return self._request("POST", "/v1/" + route.replace(".", "/"), body, timeout)

    def health(self) -> dict:
        return self._request("GET", "/v1/health", None, 5.0)

    def capabilities(self) -> dict:
        return self._request("GET", "/v1/capabilities", None, 5.0)

    def available(self) -> bool:
        try:
            self.health()
            return True
        except BridgeError:
            return False
