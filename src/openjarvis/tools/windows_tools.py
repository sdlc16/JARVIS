"""Herramientas de control de Windows (via el puente nativo autenticado).

Cada herramienta llama al puente (proceso Windows) a traves de
``openjarvis.windows.BridgeClient``. Reparto de permisos:

* sin confirmacion: solo lectura/inspeccion o acciones de bajo impacto
  (listar ventanas/archivos, abrir apps de una lista blanca, enfocar, medios);
* ``requires_confirmation=True``: todo lo que teclea, hace clic, cierra,
  lee el texto de una ventana o modifica archivos. ``ToolExecutor`` pide la
  aprobacion al usuario ANTES de ejecutar y solo entonces se envia
  ``confirmed: true`` al puente (que lo exige a su vez).
"""

from __future__ import annotations

import json
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.windows import BridgeActionError, BridgeClient, BridgeUnavailable

_MAX_CONTENT = 6000
_TARGET = {
    "hwnd": {
        "type": "integer",
        "description": "Window handle from windows_windows(action=list). Preferred.",
    },
    "title": {
        "type": "string",
        "description": "Part of the window title (must match exactly one window).",
    },
}


class _WindowsTool(BaseTool):
    sensitive = False
    timeout = 40.0

    def __init__(self, client: BridgeClient | None = None) -> None:
        self._client = client

    def client(self) -> BridgeClient:
        if self._client is None:
            self._client = BridgeClient()
        return self._client

    def _open_windows_hint(self) -> str:
        """Ventanas reales (solo lectura) para que el modelo corrija un hwnd/titulo."""
        try:
            wins = self.client().call("windows.list").get("windows", [])
        except Exception:  # noqa: BLE001 -- es solo una ayuda
            return ""
        rows = "; ".join(f"hwnd={w['hwnd']} «{w['title']}»" for w in wins[:12])
        return f" Ventanas abiertas ahora: {rows}. No inventes hwnd: usa uno de estos."

    def _run(self, route: str, **params: Any) -> ToolResult:
        name = self.tool_id
        try:
            data = self.client().call(route, confirmed=self.sensitive, **params)
        except BridgeUnavailable as exc:
            return ToolResult(
                tool_name=name,
                content=f"El puente de Windows no esta disponible: {exc}",
                success=False,
            )
        except BridgeActionError as exc:
            content = f"Windows rechazo la accion ({exc.code}): {exc.message}"
            if exc.code in ("window_not_found", "window_ambiguous"):
                content += self._open_windows_hint()
            return ToolResult(
                tool_name=name,
                content=content,
                success=False,
                metadata={"code": exc.code, "status": exc.status},
            )
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(text) > _MAX_CONTENT:
            text = text[:_MAX_CONTENT] + "...[truncado]"
        # Un tecleo no verificado es un fallo parcial que el agente debe ver.
        ok = data.get("verified") is not False if isinstance(data, dict) else True
        return ToolResult(
            tool_name=name, content=text, success=ok, metadata={"data": data}
        )

    def _spec(
        self, description: str, properties: dict, required: list[str]
    ) -> ToolSpec:
        return ToolSpec(
            name=self.tool_id,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
            },
            category="windows",
            requires_confirmation=self.sensitive,
            timeout_seconds=self.timeout,
        )


def _need(params: dict, *keys: str) -> str | None:
    missing = [k for k in keys if params.get(k) in (None, "")]
    return f"Faltan parametros: {', '.join(missing)}." if missing else None


def _target(params: dict) -> dict:
    return {k: params[k] for k in ("hwnd", "title") if params.get(k) not in (None, "")}


@ToolRegistry.register("windows_apps")
class WindowsAppsTool(_WindowsTool):
    tool_id = "windows_apps"

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Open a Windows application from the allowed list (e.g. notepad, "
            "calculadora, paint, explorador, edge), a document/folder under the "
            "user's allowed folders, or an http(s) URL. Use action=list to see the "
            "allowed apps.",
            {
                "action": {"type": "string", "enum": ["open", "list"]},
                "name": {"type": "string", "description": "Allowed app name."},
                "path": {"type": "string", "description": "Document or folder path."},
                "url": {"type": "string", "description": "http(s) URL."},
            },
            ["action"],
        )

    def execute(self, **params: Any) -> ToolResult:
        if params.get("action") == "list":
            return self._run("apps.list")
        args = {k: params.get(k) for k in ("name", "path", "url")}
        return self._run("apps.open", **args)


@ToolRegistry.register("windows_windows")
class WindowsWindowsTool(_WindowsTool):
    tool_id = "windows_windows"

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Inspect and arrange the user's open windows: list them (title, process, "
            "state), get the active one, bring one to the front, minimize, maximize "
            "or restore. Closing windows is a separate tool.",
            {
                "action": {
                    "type": "string",
                    "enum": [
                        "list",
                        "active",
                        "focus",
                        "minimize",
                        "maximize",
                        "restore",
                    ],
                },
                **_TARGET,
            },
            ["action"],
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "")
        if action in ("list", "active"):
            return self._run(f"windows.{action}")
        if action in ("focus", "minimize", "maximize", "restore"):
            if not (params.get("hwnd") or params.get("title")):
                return ToolResult(self.tool_id, "Indica hwnd o title.", success=False)
            return self._run(f"windows.{action}", **_target(params))
        return ToolResult(
            self.tool_id, f"Accion desconocida: {action!r}", success=False
        )


@ToolRegistry.register("windows_close_window")
class WindowsCloseTool(_WindowsTool):
    tool_id = "windows_close_window"
    sensitive = True

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Close a window politely (the app may ask to save changes first).",
            dict(_TARGET),
            [],
        )

    def execute(self, **params: Any) -> ToolResult:
        if not (params.get("hwnd") or params.get("title")):
            return ToolResult(self.tool_id, "Indica hwnd o title.", success=False)
        return self._run("windows.close", **_target(params))


@ToolRegistry.register("windows_type")
class WindowsTypeTool(_WindowsTool):
    tool_id = "windows_type"
    sensitive = True
    timeout = 120.0

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Type text into a specific window (it is focused first; keys are never "
            "sent to another window). method=type simulates keystrokes (best for "
            "short text); method=paste uses the clipboard (best for long text and "
            "apps with autocorrect, e.g. Windows 11 Notepad). The result includes "
            "'verified': whether the text really ended up in the window; if it is "
            "false, retry with method=paste.",
            {
                "text": {"type": "string", "description": "Text to enter."},
                "method": {"type": "string", "enum": ["type", "paste"]},
                **_TARGET,
            },
            ["text"],
        )

    def execute(self, **params: Any) -> ToolResult:
        if err := _need(params, "text"):
            return ToolResult(self.tool_id, err, success=False)
        if not (params.get("hwnd") or params.get("title")):
            return ToolResult(self.tool_id, "Indica hwnd o title.", success=False)
        if params.get("method") == "paste":
            return self._run("input.paste", text=params["text"], **_target(params))
        result = self._run("input.type", text=params["text"], **_target(params))
        data = (result.metadata or {}).get("data")
        if isinstance(data, dict) and data.get("verified") is False:
            # Apps con autocorreccion/teclas perdidas (Notepad de Win11): el mismo
            # texto, en la misma ventana ya aprobada, por portapapeles.
            retry = self._run("input.paste", text=params["text"], **_target(params))
            retry.metadata = {**(retry.metadata or {}), "retried_with": "paste"}
            return retry
        return result


@ToolRegistry.register("windows_hotkey")
class WindowsHotkeyTool(_WindowsTool):
    tool_id = "windows_hotkey"
    sensitive = True

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Press a keyboard shortcut in a specific window, e.g. keys=['ctrl','s'] "
            "or ['enter']. Combinations with the Windows key are blocked.",
            {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Key names, modifiers first (ctrl, shift, alt).",
                },
                **_TARGET,
            },
            ["keys"],
        )

    def execute(self, **params: Any) -> ToolResult:
        if err := _need(params, "keys"):
            return ToolResult(self.tool_id, err, success=False)
        if not (params.get("hwnd") or params.get("title")):
            return ToolResult(self.tool_id, "Indica hwnd o title.", success=False)
        return self._run("input.hotkey", keys=params["keys"], **_target(params))


@ToolRegistry.register("windows_click")
class WindowsClickTool(_WindowsTool):
    tool_id = "windows_click"
    sensitive = True

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Click at screen coordinates. Prefer windows_ui_act (by control name), "
            "which is far more reliable than coordinates.",
            {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string", "enum": ["left", "right"]},
                "double": {"type": "boolean"},
            },
            ["x", "y"],
        )

    def execute(self, **params: Any) -> ToolResult:
        if err := _need(params, "x", "y"):
            return ToolResult(self.tool_id, err, success=False)
        return self._run(
            "input.click",
            x=params["x"],
            y=params["y"],
            button=params.get("button"),
            double=params.get("double"),
        )


@ToolRegistry.register("windows_ui_inspect")
class WindowsUiInspectTool(_WindowsTool):
    tool_id = "windows_ui_inspect"

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "List the visible controls (buttons, menu items, fields...) of a window "
            "by name, via UI Automation. Use it to find what to press with "
            "windows_ui_act.",
            {
                "types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional types, e.g. ['Button', 'MenuItem'].",
                },
                "max": {"type": "integer"},
                **_TARGET,
            },
            [],
        )

    def execute(self, **params: Any) -> ToolResult:
        if not (params.get("hwnd") or params.get("title")):
            return ToolResult(self.tool_id, "Indica hwnd o title.", success=False)
        return self._run(
            "ui.elements",
            types=params.get("types"),
            max=params.get("max"),
            **_target(params),
        )


@ToolRegistry.register("windows_ui_act")
class WindowsUiActTool(_WindowsTool):
    tool_id = "windows_ui_act"
    sensitive = True
    timeout = 60.0

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Act on a window's controls by name via UI Automation: action=invoke "
            "presses a button/menu item/toggles a checkbox (name must match exactly "
            "one control); action=read_text returns the text shown in the window.",
            {
                "action": {"type": "string", "enum": ["invoke", "read_text"]},
                "name": {"type": "string", "description": "Control name (invoke)."},
                "type": {"type": "string", "description": "Control type, e.g. Button."},
                **_TARGET,
            },
            ["action"],
        )

    def execute(self, **params: Any) -> ToolResult:
        if not (params.get("hwnd") or params.get("title")):
            return ToolResult(self.tool_id, "Indica hwnd o title.", success=False)
        action = params.get("action")
        if action == "read_text":
            return self._run("ui.read_text", **_target(params))
        if action == "invoke":
            if err := _need(params, "name"):
                return ToolResult(self.tool_id, err, success=False)
            return self._run(
                "ui.invoke",
                name=params["name"],
                type=params.get("type"),
                **_target(params),
            )
        return ToolResult(
            self.tool_id, f"Accion desconocida: {action!r}", success=False
        )


@ToolRegistry.register("windows_files")
class WindowsFilesTool(_WindowsTool):
    tool_id = "windows_files"

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Browse the user's Windows files (read-only) inside the allowed folders "
            "(Desktop, Documents, Downloads): list a folder, read a text file, or "
            "search by name.",
            {
                "action": {"type": "string", "enum": ["list", "read", "search"]},
                "path": {"type": "string", "description": "Windows path."},
                "query": {"type": "string", "description": "Name fragment (search)."},
                "limit": {"type": "integer"},
            },
            ["action", "path"],
        )

    def execute(self, **params: Any) -> ToolResult:
        if err := _need(params, "action", "path"):
            return ToolResult(self.tool_id, err, success=False)
        action = params["action"]
        if action == "list":
            return self._run(
                "files.list", path=params["path"], limit=params.get("limit")
            )
        if action == "read":
            return self._run("files.read", path=params["path"])
        if action == "search":
            if err := _need(params, "query"):
                return ToolResult(self.tool_id, err, success=False)
            return self._run(
                "files.search",
                path=params["path"],
                query=params["query"],
                limit=params.get("limit"),
            )
        return ToolResult(
            self.tool_id, f"Accion desconocida: {action!r}", success=False
        )


@ToolRegistry.register("windows_files_modify")
class WindowsFilesModifyTool(_WindowsTool):
    tool_id = "windows_files_modify"
    sensitive = True

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Create/overwrite a text file, move a file, or send a file to the "
            "Recycle Bin (never permanent deletion). Only inside the JARVIS "
            "workspace folder (Documents\\JARVIS) unless the user widened it.",
            {
                "action": {"type": "string", "enum": ["write", "move", "trash"]},
                "path": {"type": "string", "description": "Target path (write/trash)."},
                "content": {"type": "string", "description": "File text (write)."},
                "overwrite": {"type": "boolean"},
                "src": {"type": "string", "description": "Source path (move)."},
                "dst": {"type": "string", "description": "Destination path (move)."},
            },
            ["action"],
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action")
        if action == "write":
            if err := _need(params, "path"):
                return ToolResult(self.tool_id, err, success=False)
            return self._run(
                "files.write",
                path=params["path"],
                content=params.get("content", ""),
                overwrite=params.get("overwrite"),
            )
        if action == "move":
            if err := _need(params, "src", "dst"):
                return ToolResult(self.tool_id, err, success=False)
            return self._run("files.move", src=params["src"], dst=params["dst"])
        if action == "trash":
            if err := _need(params, "path"):
                return ToolResult(self.tool_id, err, success=False)
            return self._run("files.trash", path=params["path"])
        return ToolResult(
            self.tool_id, f"Accion desconocida: {action!r}", success=False
        )


@ToolRegistry.register("windows_system")
class WindowsSystemTool(_WindowsTool):
    tool_id = "windows_system"

    @property
    def spec(self) -> ToolSpec:
        return self._spec(
            "Windows system info (RAM, uptime, battery, active window, time) or "
            "media keys: volume_up, volume_down, mute, play_pause, next, previous.",
            {
                "action": {"type": "string", "enum": ["info", "media"]},
                "key": {"type": "string", "description": "Media key (media)."},
                "times": {"type": "integer", "description": "Repeat count (max 20)."},
            },
            ["action"],
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action")
        if action == "info":
            return self._run("system.info")
        if action == "media":
            if err := _need(params, "key"):
                return ToolResult(self.tool_id, err, success=False)
            return self._run(
                "system.media", key=params["key"], times=params.get("times")
            )
        return ToolResult(
            self.tool_id, f"Accion desconocida: {action!r}", success=False
        )


WINDOWS_TOOL_NAMES = (
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
)
