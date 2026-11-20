"""JARVIS -- puente nativo de Windows, prueba minima T1.5.

Proceso Windows nativo (fuera de WSL2), ver DEVELOPMENT_TASKS.md T1.5 y
CLAUDE.md "Arquitectura de dos procesos". Alcance deliberadamente limitado
a UNA sola accion trivial (abrir el Bloc de notas) para validar que el
canal WSL2 <-> Windows nativo funciona de extremo a extremo antes de
construir el puente completo de la Fase 3 (T3.1+).

No usa ToolRegistry ni se integra con OpenJarvis todavia: eso es alcance
de T3.1/T3.2 (diseno de API + implementacion base del puente), posterior
y dependiente de este resultado. Este script es standalone a proposito.

Seguridad (dentro del alcance de T1.5, ver DEVELOPMENT_TASKS.md):
  - Solo escucha en la IP del adaptador vEthernet (WSL) -- nunca en la red
    real del usuario ("CASA", categoria Public) ni en 0.0.0.0.
  - Exige un token Bearer generado al arrancar; sin token o con token
    invalido, la peticion se rechaza y la accion NO se ejecuta.
  - El token vive en texto plano SOLO para esta prueba (fuera del repo,
    en windows_bridge/.t1_5_token, ignorado por git). El cifrado con
    DPAPI es alcance explicito de T3.2, no de T1.5.
  - Cada peticion se registra (timestamp, ruta, resultado) en
    windows_bridge/t1_5_bridge.log.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import secrets
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / ".t1_5_token"
LOG_FILE = BASE_DIR / "t1_5_bridge.log"

# IP del host Windows sobre el vEthernet (WSL) -- NUNCA 0.0.0.0, NUNCA la IP
# de la red real del usuario. Ver `Get-NetIPAddress -InterfaceAlias "*WSL*"`.
BIND_HOST = "172.29.48.1"
BIND_PORT = 47811

# Subred real del vEthernet (WSL), ver `ip route` dentro de WSL2
# ("172.29.48.0/20 dev eth0"). Cualquier origen fuera de esta subred se
# rechaza explicitamente, con independencia de en que interfaz escuche.
WSL_SUBNET = ipaddress.ip_network("172.29.48.0/20")

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(message)s",
)


def _load_or_create_token() -> str:
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    token = secrets.token_hex(32)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    return token


EXPECTED_TOKEN = _load_or_create_token()


def _process_image_name(pid: int) -> str:
    """Nombre del ejecutable de un PID via QueryFullProcessImageNameW (sin psutil)."""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return Path(buf.value).name.lower()
    finally:
        kernel32.CloseHandle(handle)


def _enum_notepad_windows() -> dict[int, int]:
    """Ventanas visibles cuyo proceso propietario es notepad.exe: {hwnd: pid}.

    El Bloc de notas moderno de Windows 11 es una app empaquetada (MSIX):
    el proceso que lanza `notepad.exe` puede ser un intermediario que no
    es el mismo PID que termina siendo propietario de la ventana real
    (y si ya hay una ventana abierta, una nueva invocacion puede sumarse
    como pestana a la MISMA ventana en vez de crear una nueva). Por eso
    verificamos "existe una ventana visible de notepad.exe", no "existe
    una ventana con el PID exacto devuelto por Popen".
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    windows: dict[int, int] = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if _process_image_name(owner_pid.value) == "notepad.exe":
            windows[hwnd] = owner_pid.value
        return True

    user32.EnumWindows(_enum_proc, 0)
    return windows


def _close_window(hwnd: int) -> None:
    """Cierra una ventana (WM_CLOSE) -- limpieza reversible del propio test."""
    import ctypes

    WM_CLOSE = 0x0010
    ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def _open_and_verify_notepad(timeout_s: float = 5.0) -> tuple[bool, list[int]]:
    """Lanza notepad.exe y confirma -- de forma real, no simulada -- que
    aparece una ventana nueva perteneciente a un proceso notepad.exe.
    Devuelve (verificado, hwnds_nuevos).
    """
    before = set(_enum_notepad_windows())
    subprocess.Popen(["notepad.exe"])

    deadline = time.time() + timeout_s
    new_hwnds: list[int] = []
    while time.time() < deadline:
        after = _enum_notepad_windows()
        new_hwnds = [h for h in after if h not in before]
        if new_hwnds:
            return True, new_hwnds
        time.sleep(0.1)
    return False, []


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "JarvisT1.5Bridge/0.1"

    def _reject(self, code: int, reason: str) -> None:
        logging.info("REJECT path=%s reason=%s from=%s", self.path, reason, self.client_address[0])
        body = json.dumps({"ok": False, "error": reason}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._reject(401, "missing_token")
            return False
        token = auth[len("Bearer "):].strip()
        if not secrets.compare_digest(token, EXPECTED_TOKEN):
            self._reject(403, "invalid_token")
            return False
        return True

    def do_POST(self) -> None:
        try:
            origin_ok = ipaddress.ip_address(self.client_address[0]) in WSL_SUBNET
        except ValueError:
            origin_ok = False
        if not origin_ok:
            # Defensa en profundidad: aunque el bind ya restringe la interfaz,
            # rechazamos explicitamente cualquier origen fuera de la subred WSL.
            self._reject(403, "origin_not_allowed")
            return

        if self.path != "/actions/open-notepad":
            self._reject(404, "unknown_action")
            return

        if not self._check_auth():
            return

        window_ok, new_hwnds = _open_and_verify_notepad(timeout_s=5.0)
        for hwnd in new_hwnds:
            _close_window(hwnd)  # limpieza: cierra la ventana vacia que abrimos para la prueba

        logging.info(
            "EXECUTE path=%s window_verified=%s new_hwnds=%s from=%s",
            self.path, window_ok, new_hwnds, self.client_address[0],
        )
        self._ok({
            "ok": True,
            "action": "open-notepad",
            "window_verified": window_ok,
            "new_window_handles": new_hwnds,
        })

    def log_message(self, fmt: str, *args) -> None:  # silencia stderr, ya logueamos a archivo
        pass


def main() -> None:
    server = ThreadingHTTPServer((BIND_HOST, BIND_PORT), BridgeHandler)
    print(f"T1.5 bridge escuchando en http://{BIND_HOST}:{BIND_PORT} (token en {TOKEN_FILE})")
    logging.info("START bind=%s:%s", BIND_HOST, BIND_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        logging.info("STOP")


if __name__ == "__main__":
    main()
