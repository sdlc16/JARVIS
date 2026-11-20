# ruff: noqa: E501
"""Pruebas del puente de Windows: la logica de politica/servidor corre desde WSL
con un doble de Win32. El comportamiento Win32 REAL se prueba en Windows con
``windows_bridge/selftest.py`` y desde WSL con tests/bridge/test_bridge_live.py.
"""

from __future__ import annotations

import ipaddress
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2] / "windows_bridge"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis_bridge.security import (  # noqa: E402
    BridgeError,
    PlainProtector,
    Policy,
    TokenStore,
)
from jarvis_bridge.server import BridgeServer  # noqa: E402

TOKEN = "t" * 43


class FakeWin:
    """Doble de win32: registra llamadas, no toca el escritorio."""

    SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE = 6, 3, 9

    def __init__(self, trash_dir: pathlib.Path):
        self.windows = [
            {
                "hwnd": 101,
                "title": "Documento - Bloc de notas",
                "pid": 1,
                "process": "notepad.exe",
                "rect": [0, 0, 10, 10],
                "state": "normal",
                "foreground": True,
            },
            {
                "hwnd": 102,
                "title": "Calculadora",
                "pid": 2,
                "process": "calculatorapp.exe",
                "rect": [0, 0, 10, 10],
                "state": "normal",
                "foreground": False,
            },
            {
                "hwnd": 103,
                "title": "Notas de la reunion - Bloc de notas",
                "pid": 3,
                "process": "notepad.exe",
                "rect": [0, 0, 10, 10],
                "state": "normal",
                "foreground": False,
            },
        ]
        self.calls: list[tuple] = []
        self.focus_ok = True
        self.trash_dir = trash_dir
        self.fg = 101

    def list_windows(self):
        return [dict(w, foreground=(w["hwnd"] == self.fg)) for w in self.windows]

    def find_window(self, hwnd=None, title=None):
        wins = self.list_windows()
        if hwnd:
            for w in wins:
                if w["hwnd"] == hwnd:
                    return w
            raise BridgeError("window_not_found", "no hwnd", 404)
        hits = [w for w in wins if title and title.lower() in w["title"].lower()]
        if not hits:
            raise BridgeError("window_not_found", "no title", 404)
        if len(hits) > 1:
            raise BridgeError("window_ambiguous", "varias", 409)
        return hits[0]

    def focus(self, hwnd, timeout_s=1.0):
        self.calls.append(("focus", hwnd))
        if self.focus_ok:
            self.fg = hwnd
        return self.focus_ok

    def show(self, hwnd, cmd):
        self.calls.append(("show", hwnd, cmd))

    def close(self, hwnd):
        self.calls.append(("close", hwnd))
        self.windows = [w for w in self.windows if w["hwnd"] != hwnd]

    def foreground_window(self):
        return next((w for w in self.list_windows() if w["foreground"]), None)

    def send_text(self, text):
        self.calls.append(("type", self.fg, text))
        return len(text)

    def send_hotkey(self, keys):
        self.calls.append(("hotkey", self.fg, tuple(keys)))

    def click(self, x, y, button="left", double=False):
        self.calls.append(("click", x, y, button, double))

    def media_key(self, key):
        self.calls.append(("media", key))

    clip = None

    def clipboard_get(self):
        return self.clip

    def clipboard_set(self, text):
        self.calls.append(("clip_set", text))
        self.clip = text

    def system_info(self):
        return {"ram_total_gb": 32.0}

    def lock_workstation(self):
        self.calls.append(("lock",))

    def recycle(self, path):
        self.calls.append(("recycle", str(path)))
        self.trash_dir.mkdir(exist_ok=True)
        shutil.move(str(path), str(self.trash_dir / path.name))


@pytest.fixture
def home(tmp_path):
    for d in ("Desktop", "Documents/JARVIS", "Downloads", "Secretos"):
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def win(tmp_path):
    return FakeWin(tmp_path / "_trash")


@pytest.fixture
def server(home, win):
    policy = Policy.default(home)
    return BridgeServer(
        policy,
        TOKEN,
        win,
        [ipaddress.ip_network("172.29.48.0/20")],
        home / "bridge.jsonl",
    )


@pytest.fixture
def call(server):
    """call('POST','/v1/files/list', {...}) -> (status, json). IP de WSL y token validos."""

    def _call(method, path, body=None, *, token=TOKEN, ip="172.29.63.188", raw=None):
        import json

        headers = {"authorization": f"Bearer {token}"} if token else {}
        data = (
            raw
            if raw is not None
            else (json.dumps(body).encode() if body is not None else b"")
        )
        status, payload = server.handle(method, path, headers, data, ip)
        return status, payload

    return _call


@pytest.fixture
def token_store(tmp_path):
    return TokenStore(tmp_path / "token.bin", PlainProtector())
