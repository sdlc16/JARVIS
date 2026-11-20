# ruff: noqa: E501
"""Herramientas de control de Windows: permisos, confirmacion y errores.

El puente corre EN PROCESO sobre HTTP real (BridgeServer + doble de Win32) y se
integra con ``ToolExecutor`` para probar que, sin aprobacion del usuario, no se
ejecuta ninguna accion sensible. Hardware real: windows_bridge/selftest.py.
"""

from __future__ import annotations

import importlib
import ipaddress
import json
import threading

import pytest
from jarvis_bridge.security import Policy
from jarvis_bridge.server import BridgeServer

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolCall
from openjarvis.tools import windows_tools as wt
from openjarvis.tools._stubs import ToolExecutor
from openjarvis.windows import BridgeClient
from tests.bridge.conftest import TOKEN, FakeWin

SENSITIVE_TOOLS = {
    "windows_close_window",
    "windows_type",
    "windows_hotkey",
    "windows_click",
    "windows_ui_act",
    "windows_files_modify",
}


@pytest.fixture
def env(tmp_path):
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


def call(tool_name, /, **args):
    return ToolCall(id="1", name=tool_name, arguments=json.dumps(args))


def executor(client, approve=None, interactive=True):
    tools = [
        getattr(wt, cls)(client)
        for cls in (
            "WindowsAppsTool",
            "WindowsWindowsTool",
            "WindowsCloseTool",
            "WindowsTypeTool",
            "WindowsHotkeyTool",
            "WindowsClickTool",
            "WindowsUiInspectTool",
            "WindowsUiActTool",
            "WindowsFilesTool",
            "WindowsFilesModifyTool",
            "WindowsSystemTool",
        )
    ]
    return ToolExecutor(
        tools,
        interactive=interactive,
        confirm_callback=(lambda prompt: approve) if approve is not None else None,
    )


def test_all_tools_register_with_valid_specs_and_expected_sensitivity():
    importlib.reload(wt)  # el conftest vacia los registros entre tests
    assert set(wt.WINDOWS_TOOL_NAMES) <= set(ToolRegistry.keys())
    sensitive = set()
    for n in wt.WINDOWS_TOOL_NAMES:
        spec = ToolRegistry.get(n)().spec
        props = spec.parameters["properties"]
        assert set(spec.parameters["required"]) <= set(props), n
        assert spec.name == n and spec.description and spec.category == "windows"
        if spec.requires_confirmation:
            sensitive.add(n)
    assert sensitive == SENSITIVE_TOOLS


def test_read_only_tools_run_without_confirmation(env):
    client, _, home = env
    ex = executor(client, approve=None, interactive=False)
    assert ex.execute(call("windows_windows", action="list")).success
    r = ex.execute(call("windows_apps", action="list"))
    assert r.success and "notepad" in r.content
    assert ex.execute(call("windows_system", action="info")).success
    (home / "Desktop" / "n.txt").write_text("hola")
    r = ex.execute(
        call("windows_files", action="read", path=str(home / "Desktop" / "n.txt"))
    )
    assert r.success and "hola" in r.content


@pytest.mark.parametrize(
    "tool,args",
    [
        ("windows_type", {"text": "x", "title": "Calculadora"}),
        ("windows_hotkey", {"keys": ["ctrl", "s"], "title": "Calculadora"}),
        ("windows_click", {"x": 1, "y": 1}),
        ("windows_close_window", {"hwnd": 102}),
        ("windows_ui_act", {"action": "read_text", "hwnd": 102}),
        ("windows_files_modify", {"action": "write", "path": "X", "content": "a"}),
    ],
)
def test_denied_confirmation_executes_nothing(env, tool, args):
    client, win, _ = env
    r = executor(client, approve=False).execute(call(tool, **args))
    assert not r.success and "denied" in r.content.lower()
    assert win.calls == []  # el puente ni siquiera vio la accion


@pytest.mark.parametrize(
    "tool,args",
    [
        ("windows_type", {"text": "x", "title": "Calculadora"}),
        ("windows_click", {"x": 1, "y": 1}),
        ("windows_files_modify", {"action": "write", "path": "X", "content": "a"}),
    ],
)
def test_sensitive_tools_refuse_to_run_without_a_confirmation_channel(env, tool, args):
    client, win, _ = env
    r = executor(client, approve=None, interactive=False).execute(call(tool, **args))
    assert not r.success and "requires confirmation" in r.content
    assert win.calls == []


def test_approved_type_reaches_the_window(env):
    client, win, _ = env
    r = executor(client, approve=True).execute(
        call("windows_type", text="12+3", title="Calculadora")
    )
    assert r.success, r.content
    assert ("type", 102, "12+3") in win.calls


def test_approved_file_write_and_trash_stay_inside_the_workspace(env):
    client, win, home = env
    f = home / "Documents" / "JARVIS" / "nota.txt"
    ex = executor(client, approve=True)
    assert ex.execute(
        call("windows_files_modify", action="write", path=str(f), content="hola")
    ).success
    assert f.read_text() == "hola"
    r = ex.execute(
        call(
            "windows_files_modify",
            action="write",
            path=str(home / "Desktop" / "no.txt"),
            content="x",
        )
    )
    assert not r.success and "path_not_allowed" in r.content
    assert ex.execute(call("windows_files_modify", action="trash", path=str(f))).success
    assert not f.exists() and (win.trash_dir / "nota.txt").exists()


def test_unverified_typing_is_reported_as_a_failure(env, monkeypatch):
    from jarvis_bridge import uia

    monkeypatch.setattr(
        uia, "run", lambda op, hwnd, **kw: {"result": {"text": "El rápido"}}
    )
    client, _, _ = env
    r = executor(client, approve=True).execute(
        call("windows_type", text="El rapido", title="Calculadora")
    )
    assert not r.success  # verified=False => el agente debe verlo y reintentar
    assert '"verified":false' in r.content and "rápido" in r.content


def test_bridge_rejections_become_actionable_tool_errors(env):
    client, _, _ = env
    r = executor(client, approve=True).execute(
        call("windows_apps", action="open", name="cmd.exe")
    )
    assert not r.success and "app_not_allowed" in r.content
    r = executor(client, approve=True).execute(
        call("windows_windows", action="focus", title="Bloc de notas")
    )
    assert not r.success and "window_ambiguous" in r.content


def test_missing_target_or_params_never_hit_the_bridge(env):
    client, win, _ = env
    ex = executor(client, approve=True)
    assert not ex.execute(call("windows_type", text="x")).success
    assert not ex.execute(call("windows_hotkey", keys=["enter"])).success
    assert not ex.execute(call("windows_windows", action="focus")).success
    assert not ex.execute(call("windows_files", action="search", path="C:/x")).success
    assert win.calls == []


def test_bridge_down_is_reported_not_raised():
    r = wt.WindowsWindowsTool(
        BridgeClient("127.0.0.1", 1, token="x", timeout=2)
    ).execute(action="list")
    assert not r.success and "no esta disponible" in r.content
    assert "start-jarvis" in r.content
