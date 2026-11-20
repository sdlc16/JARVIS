"""Primitivas Win32 reales (ctypes). Solo importable en Windows."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("jarvis_bridge.win32 solo funciona en Windows")

from jarvis_bridge.security import BridgeError

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetForegroundWindow.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

# Ventanas de infraestructura de la UI que NO son ventanas "de usuario" (p. ej.
# los KeyTips/popups de WinUI aparecen como "Host de ventanas emergentes").
JUNK_CLASSES = {
    "Microsoft.UI.Content.PopupWindowSiteBridge",
    "tooltips_class32",
    "ApplicationFrameInputSinkWindow",
}
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetTickCount64.restype = ctypes.c_ulonglong

WM_CLOSE = 0x0010
SW_SHOW, SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 5, 3, 6, 9
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


# ---------------------------------------------------------------------------
# Ventanas
# ---------------------------------------------------------------------------


def process_image_name(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(520)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return Path(buf.value).name.lower()
    finally:
        kernel32.CloseHandle(handle)


def _window_title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _is_cloaked(hwnd) -> bool:
    cloaked = wintypes.DWORD(0)
    dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
    )
    return bool(cloaked.value)


def list_windows() -> list[dict]:
    """Ventanas de nivel superior visibles con titulo (como las ve el usuario)."""
    fg = user32.GetForegroundWindow()
    out: list[dict] = []

    @WNDENUMPROC
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if not title:
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if _is_cloaked(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in JUNK_CLASSES:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if user32.IsIconic(hwnd):
            state = "minimized"
        elif user32.IsZoomed(hwnd):
            state = "maximized"
        else:
            state = "normal"
        out.append(
            {
                "hwnd": int(hwnd or 0),
                "title": title,
                "class": cls.value,
                "pid": int(pid.value),
                "process": process_image_name(pid.value),
                "rect": [rect.left, rect.top, rect.right, rect.bottom],
                "state": state,
                "foreground": bool(fg) and int(hwnd or 0) == int(fg or 0),
            }
        )
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_window(hwnd: int | None = None, title: str | None = None) -> dict:
    wins = list_windows()
    if hwnd:
        for w in wins:
            if w["hwnd"] == int(hwnd):
                return w
        raise BridgeError(
            "window_not_found", f"No hay ventana visible con hwnd={hwnd}.", 404
        )
    if title:
        needle = title.lower()
        hits = [w for w in wins if needle in w["title"].lower()]
        if not hits:
            raise BridgeError(
                "window_not_found", f"Ninguna ventana contiene '{title}'.", 404
            )
        if len(hits) > 1:
            names = "; ".join(f"{w['hwnd']}:{w['title'][:40]}" for w in hits[:6])
            raise BridgeError(
                "window_ambiguous",
                f"{len(hits)} ventanas coinciden con '{title}': {names}. Usa hwnd.",
                409,
            )
        return hits[0]
    raise BridgeError("bad_request", "Indica 'hwnd' o 'title'.")


def _tap_inert_key() -> None:
    """Entrada 'reciente' inocua para que Windows deje a SetForegroundWindow
    cambiar el foco. NO se usa Alt: en Notepad/WinUI activa los KeyTips y las
    teclas siguientes se interpretan como atajos de menu (defecto real medido
    con la prueba en Windows: se perdian las primeras letras tecleadas)."""
    send_keys([("f15", True)])
    send_keys([("f15", False)])


def focus(hwnd: int, timeout_s: float = 1.0) -> bool:
    h = wintypes.HWND(hwnd)
    if user32.IsIconic(h):
        user32.ShowWindow(h, SW_RESTORE)
    fg = user32.GetForegroundWindow()
    cur = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = False
    if fg_tid and fg_tid != cur:
        attached = bool(user32.AttachThreadInput(cur, fg_tid, True))
    try:
        _tap_inert_key()
        user32.BringWindowToTop(h)
        user32.SetForegroundWindow(h)
        user32.ShowWindow(h, SW_SHOW)
    finally:
        if attached:
            user32.AttachThreadInput(cur, fg_tid, False)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if int(user32.GetForegroundWindow() or 0) == int(hwnd):
            return True
        time.sleep(0.05)
    return int(user32.GetForegroundWindow() or 0) == int(hwnd)


def show(hwnd: int, cmd: int) -> None:
    user32.ShowWindow(wintypes.HWND(hwnd), cmd)


def close(hwnd: int) -> None:
    """Cierre educado (WM_CLOSE): la app pregunta si hay cambios sin guardar."""
    user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)


def foreground_window() -> dict | None:
    fg = int(user32.GetForegroundWindow() or 0)
    for w in list_windows():
        if w["hwnd"] == fg:
            return w
    return None


# ---------------------------------------------------------------------------
# Entrada (teclado / raton)
# ---------------------------------------------------------------------------


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x2, 0x4
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x8, 0x10

VK = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "win": 0x5B,
}
VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})  # F1..F24 (F15 = tecla inerte)
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
MEDIA = {
    "mute": 0xAD,
    "volume_down": 0xAE,
    "volume_up": 0xAF,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
    "play_pause": 0xB3,
}


def _send(inputs: list[INPUT]) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise BridgeError(
            "input_blocked",
            f"Windows rechazo la entrada ({sent}/{len(inputs)}): "
            "¿ventana con mas privilegios?",
            409,
        )


def _key(vk: int, down: bool) -> INPUT:
    flags = 0 if down else KEYEVENTF_KEYUP
    if vk in EXTENDED or vk in MEDIA.values():
        flags |= KEYEVENTF_EXTENDEDKEY
    i = INPUT(type=INPUT_KEYBOARD)
    i.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    return i


def vk_for(name: str) -> int:
    n = name.strip().lower()
    if n in VK:
        return VK[n]
    if len(n) == 1:
        scan = user32.VkKeyScanW(n)
        if scan == -1:
            raise BridgeError("bad_key", f"Tecla no soportada: '{name}'.")
        return scan & 0xFF
    raise BridgeError("bad_key", f"Tecla no soportada: '{name}'.")


def send_keys(events: list[tuple[str, bool]]) -> None:
    _send([_key(vk_for(n), d) for n, d in events])


def send_hotkey(keys: list[str]) -> None:
    names = [k.lower() for k in keys]
    if not names or len(names) > 4:
        raise BridgeError("bad_request", "Indica de 1 a 4 teclas.")
    if "win" in names:
        raise BridgeError(
            "hotkey_blocked",
            "Las combinaciones con la tecla Windows estan bloqueadas.",
            403,
        )
    mods = [n for n in names if n in ("ctrl", "control", "shift", "alt")]
    rest = [n for n in names if n not in mods]
    seq = [_key(vk_for(m), True) for m in mods]
    for k in rest:
        seq += [_key(vk_for(k), True), _key(vk_for(k), False)]
    seq += [_key(vk_for(m), False) for m in reversed(mods)]
    _send(seq)


def send_text(text: str) -> int:
    """Teclea texto Unicode real (no pega): respeta cualquier distribucion."""
    seq: list[INPUT] = []
    for ch in text:
        if ch == "\n":
            seq += [_key(VK["enter"], True), _key(VK["enter"], False)]
            continue
        if ch == "\t":
            seq += [_key(VK["tab"], True), _key(VK["tab"], False)]
            continue
        data = ch.encode("utf-16-le")
        for j in range(0, len(data), 2):
            unit = int.from_bytes(data[j : j + 2], "little")
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                i = INPUT(type=INPUT_KEYBOARD)
                i.ki = KEYBDINPUT(0, unit, flags, 0, 0)
                seq.append(i)
    for start in range(0, len(seq), 200):  # lotes: no saturar la cola de entrada
        _send(seq[start : start + 200])
    return len(text)


def media_key(name: str) -> None:
    if name not in MEDIA:
        raise BridgeError("bad_key", f"Tecla multimedia desconocida: {name}.")
    _send([_key(MEDIA[name], True), _key(MEDIA[name], False)])


def click(x: int, y: int, button: str = "left", double: bool = False) -> None:
    left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    top = user32.GetSystemMetrics(77)
    w = user32.GetSystemMetrics(78)
    h = user32.GetSystemMetrics(79)
    if not (left <= x < left + w and top <= y < top + h):
        raise BridgeError("bad_coordinates", f"({x},{y}) esta fuera de la pantalla.")
    user32.SetCursorPos(int(x), int(y))
    down, up = (
        (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
        if button == "left"
        else (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
    )
    seq = []
    for _ in range(2 if double else 1):
        for f in (down, up):
            i = INPUT(type=INPUT_MOUSE)
            i.mi = MOUSEINPUT(0, 0, 0, f, 0, 0)
            seq.append(i)
    _send(seq)


# ---------------------------------------------------------------------------
# Portapapeles (texto Unicode)
# ---------------------------------------------------------------------------

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]


def _open_clipboard() -> None:
    for _ in range(20):  # otro proceso puede tenerlo abierto un instante
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise BridgeError("clipboard_busy", "No se pudo abrir el portapapeles.", 409)


def clipboard_get() -> str | None:
    """Texto actual del portapapeles, o None si no contiene texto."""
    _open_clipboard()
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_set(text: str) -> None:
    data = (text + "\0").encode("utf-16-le")
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise BridgeError(
                "clipboard_failed", "Sin memoria para el portapapeles.", 500
            )
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise BridgeError(
                "clipboard_failed", "No se pudo escribir el portapapeles.", 500
            )
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------------------
# Sistema / archivos
# ---------------------------------------------------------------------------


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


def system_info() -> dict:
    mem = MEMORYSTATUSEX()
    mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
    pw = SYSTEM_POWER_STATUS()
    kernel32.GetSystemPowerStatus(ctypes.byref(pw))
    fg = foreground_window()
    return {
        "ram_total_gb": round(mem.ullTotalPhys / 2**30, 1),
        "ram_free_gb": round(mem.ullAvailPhys / 2**30, 1),
        "ram_load_percent": int(mem.dwMemoryLoad),
        "cpu_threads": os.cpu_count(),
        "uptime_hours": round(kernel32.GetTickCount64() / 3_600_000, 1),
        "on_ac_power": {0: False, 1: True}.get(pw.ACLineStatus),
        "battery_percent": None
        if pw.BatteryLifePercent == 255
        else int(pw.BatteryLifePercent),
        "foreground_title": fg["title"] if fg else None,
        "local_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def lock_workstation() -> None:
    if not user32.LockWorkStation():
        raise BridgeError("lock_failed", "Windows no pudo bloquear la sesion.", 500)


def recycle(path: Path) -> None:
    """Mueve a la Papelera de reciclaje (reversible). Nunca borrado definitivo."""
    kind = "DeleteDirectory" if path.is_dir() else "DeleteFile"
    script = (
        "Add-Type -AssemblyName Microsoft.VisualBasic;"
        f"[Microsoft.VisualBasic.FileIO.FileSystem]::{kind}("
        "$env:JARVIS_RECYCLE_PATH,'OnlyErrorDialogs','SendToRecycleBin')"
    )
    env = {**os.environ, "JARVIS_RECYCLE_PATH": str(path)}
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0 or path.exists():
        raise BridgeError(
            "trash_failed",
            f"No se pudo enviar a la papelera: {proc.stderr.strip()[:200]}",
            500,
        )
