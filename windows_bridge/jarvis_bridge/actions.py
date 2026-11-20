"""Acciones del puente: politica + validacion + primitivas Win32 inyectadas.

`ctx.win` es el modulo `win32` real en Windows, o un doble en las pruebas
unitarias que corren desde WSL -- la logica de permisos/rutas es la misma.
"""

from __future__ import annotations

import os
import subprocess
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_bridge.security import BridgeError, Policy

MAX_TEXT = 2000
EXECUTABLE_EXT = {
    ".exe",
    ".bat",
    ".cmd",
    ".com",
    ".ps1",
    ".psm1",
    ".vbs",
    ".vbe",
    ".js",
    ".jse",
    ".wsf",
    ".msi",
    ".msp",
    ".lnk",
    ".scr",
    ".jar",
    ".reg",
    ".hta",
    ".cpl",
    ".dll",
}


@dataclass
class Ctx:
    policy: Policy
    win: Any  # modulo win32 (real) o doble de pruebas


Params = dict[str, Any]


def _str(p: Params, key: str, *, required: bool = True, max_len: int = 4096) -> str:
    v = p.get(key)
    if v is None or v == "":
        if required:
            raise BridgeError("bad_request", f"Falta el parametro '{key}'.")
        return ""
    if not isinstance(v, str):
        raise BridgeError("bad_request", f"'{key}' debe ser texto.")
    if len(v) > max_len:
        raise BridgeError("bad_request", f"'{key}' supera {max_len} caracteres.")
    return v


def _int(p: Params, key: str, default: int | None = None) -> int | None:
    v = p.get(key, default)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError) as exc:
        raise BridgeError("bad_request", f"'{key}' debe ser un entero.") from exc


def _target(ctx: Ctx, p: Params) -> dict:
    return ctx.win.find_window(_int(p, "hwnd"), p.get("title") or None)


# ---------------------------------------------------------------------------
# apps
# ---------------------------------------------------------------------------


def apps_list(ctx: Ctx, p: Params) -> dict:
    return {"apps": sorted(ctx.policy.apps)}


def apps_open(ctx: Ctx, p: Params) -> dict:
    name, path, url = (p.get("name") or ""), (p.get("path") or ""), (p.get("url") or "")
    if sum(bool(x) for x in (name, path, url)) != 1:
        raise BridgeError("bad_request", "Indica exactamente uno de: name, path, url.")
    before = {w["hwnd"] for w in ctx.win.list_windows()}
    if name:
        exe = ctx.policy.apps.get(str(name).lower())
        if exe is None:
            raise BridgeError(
                "app_not_allowed",
                f"'{name}' no esta en la lista de apps permitidas: "
                f"{sorted(ctx.policy.apps)}",
                403,
            )
        _launch([exe])
        target = exe
    elif path:
        resolved = ctx.policy.resolve_path(str(path), write=False)
        if not resolved.exists():
            raise BridgeError("not_found", f"No existe: {resolved}", 404)
        if resolved.is_file() and resolved.suffix.lower() in EXECUTABLE_EXT:
            raise BridgeError(
                "app_not_allowed", f"No se abren ejecutables ({resolved.suffix}).", 403
            )
        if resolved.is_dir():
            _launch(["explorer.exe", str(resolved)])
        else:
            # tipo no ejecutable y ruta validada contra las raices permitidas
            os.startfile(str(resolved))  # noqa: S606
        target = str(resolved)
    else:
        if not str(url).lower().startswith(("http://", "https://")):
            raise BridgeError("bad_url", "Solo se abren URLs http/https.", 400)
        webbrowser.open(str(url))
        target = str(url)
    new: list[dict] = []
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        new = [w for w in ctx.win.list_windows() if w["hwnd"] not in before]
        if new:
            break
        time.sleep(0.15)
    return {"target": target, "new_windows": new, "verified": bool(new)}


def _launch(argv: list[str]) -> None:
    try:
        subprocess.Popen(argv, shell=False, close_fds=True)  # noqa: S603
    except OSError as exc:
        raise BridgeError(
            "launch_failed", f"No se pudo lanzar {argv[0]}: {exc}", 500
        ) from exc


# ---------------------------------------------------------------------------
# ventanas
# ---------------------------------------------------------------------------


def windows_list(ctx: Ctx, p: Params) -> dict:
    return {"windows": ctx.win.list_windows()}


def windows_active(ctx: Ctx, p: Params) -> dict:
    return {"window": ctx.win.foreground_window()}


def windows_focus(ctx: Ctx, p: Params) -> dict:
    w = _target(ctx, p)
    ok = ctx.win.focus(w["hwnd"])
    return {"focused": ok, "window": w}


def _show(cmd_name: str) -> Callable[[Ctx, Params], dict]:
    def run(ctx: Ctx, p: Params) -> dict:
        w = _target(ctx, p)
        ctx.win.show(w["hwnd"], getattr(ctx.win, cmd_name))
        return {"window": w, "action": cmd_name}

    return run


def windows_close(ctx: Ctx, p: Params) -> dict:
    w = _target(ctx, p)
    ctx.win.close(w["hwnd"])
    time.sleep(0.4)
    still = any(x["hwnd"] == w["hwnd"] for x in ctx.win.list_windows())
    return {
        "window": w,
        "closed": not still,
        "note": "La app puede estar pidiendo guardar cambios." if still else "",
    }


# ---------------------------------------------------------------------------
# entrada
# ---------------------------------------------------------------------------


def _focus_target_or_fail(ctx: Ctx, p: Params) -> dict:
    """Nunca teclea a ciegas: enfoca la ventana pedida y verifica que lo esta."""
    if not (p.get("hwnd") or p.get("title")):
        raise BridgeError(
            "bad_request", "Indica la ventana destino ('hwnd' o 'title')."
        )
    w = _target(ctx, p)
    if not ctx.win.focus(w["hwnd"]):
        raise BridgeError(
            "focus_failed",
            f"No se pudo poner en primer plano '{w['title'][:60]}': "
            "no se envio ninguna tecla.",
            409,
        )
    time.sleep(0.2)  # la app tarda un instante en estar lista para recibir teclas
    return w


def _verify_typed(ctx: Ctx, hwnd: int, text: str) -> dict:
    """Lee (UI Automation) lo que quedo en la ventana y lo compara con `text`."""
    time.sleep(0.5)
    try:
        from jarvis_bridge import uia

        doc = uia.run("read", hwnd, max=100000)["result"].get("text")
    except BridgeError:
        doc = None
    if doc is None:
        return {"verified": None}
    norm = doc.replace("\r\n", "\n").replace("\r", "\n")
    ok = text in norm
    out: dict = {"verified": ok}
    if not ok:
        out["readback_tail"] = norm.rstrip("\n")[-(len(text) + 20) :]
        out["note"] = (
            "El texto en la ventana no coincide con lo tecleado (autocorreccion "
            "de la app o entrada perdida). Prueba input.paste para texto largo."
        )
    return out


def input_type(ctx: Ctx, p: Params) -> dict:
    """Teclea y VERIFICA por lectura (UI Automation) lo que quedo en la ventana.

    Algunas apps modifican o pierden texto inyectado (el Bloc de notas de
    Windows 11 autocorrige 'rapido'->'rápido' y se desincroniza en su primer
    limite de palabra, medido con la prueba real). El agente recibe `verified`
    para no asumir exito.
    """
    text = _str(p, "text", max_len=MAX_TEXT)
    w = _focus_target_or_fail(ctx, p)
    n = ctx.win.send_text(text)
    out: dict = {"typed_chars": n, "window": w, "verified": None}
    if p.get("verify", True):
        out.update(_verify_typed(ctx, w["hwnd"], text))
    return out


def input_paste(ctx: Ctx, p: Params) -> dict:
    """Pega texto por portapapeles (Ctrl+V): inmune al autocorrector y rapido.

    Sobrescribe el portapapeles un instante y, si contenia texto, lo restaura.
    """
    text = _str(p, "text", max_len=20000)
    w = _focus_target_or_fail(ctx, p)
    previous = ctx.win.clipboard_get()
    ctx.win.clipboard_set(text)
    time.sleep(0.1)
    ctx.win.send_hotkey(["ctrl", "v"])
    time.sleep(0.4)
    restored = False
    if p.get("restore_clipboard", True) and previous is not None:
        ctx.win.clipboard_set(previous)
        restored = True
    out: dict = {"pasted_chars": len(text), "window": w, "clipboard_restored": restored}
    if p.get("verify", True):
        out.update(_verify_typed(ctx, w["hwnd"], text))
    return out


def input_hotkey(ctx: Ctx, p: Params) -> dict:
    keys = p.get("keys")
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        raise BridgeError(
            "bad_request", "'keys' debe ser una lista de nombres de teclas."
        )
    w = _focus_target_or_fail(ctx, p)
    ctx.win.send_hotkey(keys)
    return {"keys": keys, "window": w}


def input_click(ctx: Ctx, p: Params) -> dict:
    x, y = _int(p, "x"), _int(p, "y")
    if x is None or y is None:
        raise BridgeError("bad_request", "Faltan 'x' e 'y'.")
    button = str(p.get("button", "left"))
    if button not in ("left", "right"):
        raise BridgeError("bad_request", "'button' debe ser left o right.")
    ctx.win.click(x, y, button, bool(p.get("double", False)))
    return {"clicked": [x, y], "button": button}


# ---------------------------------------------------------------------------
# UI Automation (controles por nombre)
# ---------------------------------------------------------------------------


def _uia(ctx: Ctx, p: Params, op: str, **extra: Any) -> dict:
    from jarvis_bridge import uia

    w = _target(ctx, p)
    res = uia.run(op, w["hwnd"], **extra)["result"]
    return {"window": w, "result": res}


def ui_elements(ctx: Ctx, p: Params) -> dict:
    types = p.get("types") or []
    if not isinstance(types, list):
        raise BridgeError(
            "bad_request", "'types' debe ser una lista (p. ej. ['Button'])."
        )
    out = _uia(ctx, p, "elements", types=types, max=min(_int(p, "max", 80) or 80, 300))
    return {"window": out["window"], "elements": out["result"]}


def ui_invoke(ctx: Ctx, p: Params) -> dict:
    name = _str(p, "name", max_len=200)
    out = _uia(ctx, p, "invoke", name=name, type=str(p.get("type") or ""))
    res = out["result"]
    if "error" in res:
        status = {"not_found": 404, "ambiguous": 409}.get(res["error"], 422)
        raise BridgeError(
            f"ui_{res['error']}",
            f"No se pudo accionar '{name}' en '{out['window']['title'][:50]}': {res}",
            status,
        )
    return {"window": out["window"], **res}


def ui_read_text(ctx: Ctx, p: Params) -> dict:
    out = _uia(ctx, p, "read", max=min(_int(p, "max", 20000) or 20000, 100000))
    res = out["result"]
    if "error" in res:
        raise BridgeError("ui_no_text", "La ventana no expone texto legible.", 422)
    return {"window": out["window"], "text": res["text"]}


# ---------------------------------------------------------------------------
# archivos
# ---------------------------------------------------------------------------


def files_list(ctx: Ctx, p: Params) -> dict:
    d = ctx.policy.resolve_path(_str(p, "path"), write=False)
    if not d.is_dir():
        raise BridgeError("not_a_directory", f"No es una carpeta: {d}", 400)
    limit = min(_int(p, "limit", 200) or 200, 1000)
    entries = []
    children = sorted(d.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
    total = len(children)
    n_dirs = sum(1 for c in children if c.is_dir())
    for child in children:
        try:
            st = child.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": None if child.is_dir() else st.st_size,
                "modified": time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(st.st_mtime)
                ),
            }
        )
        if len(entries) >= limit:
            break
    return {
        # Recuentos calculados aqui: los modelos cuentan mal las listas largas
        # (medido: 65/54/53 para la misma carpeta).
        "path": str(d),
        "total_entries": total,
        "total_folders": n_dirs,
        "total_files": total - n_dirs,
        "returned": len(entries),
        "entries": entries,
        "truncated": total > len(entries),
    }


def files_read(ctx: Ctx, p: Params) -> dict:
    f = ctx.policy.resolve_path(_str(p, "path"), write=False)
    if not f.is_file():
        raise BridgeError("not_found", f"No existe el archivo: {f}", 404)
    size = f.stat().st_size
    if size > ctx.policy.max_read_bytes:
        raise BridgeError(
            "too_large",
            f"{size} bytes supera el maximo de {ctx.policy.max_read_bytes}.",
            413,
        )
    raw = f.read_bytes()
    if b"\x00" in raw[:4096]:
        raise BridgeError("binary_file", "Archivo binario: solo se leen textos.", 415)
    return {
        "path": str(f),
        "size": size,
        "content": raw.decode("utf-8", errors="replace"),
    }


def files_search(ctx: Ctx, p: Params) -> dict:
    root = ctx.policy.resolve_path(_str(p, "path"), write=False)
    query = _str(p, "query", max_len=200).lower()
    limit = min(_int(p, "limit", 50) or 50, 200)
    deadline = time.monotonic() + 8.0
    hits: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if not d.startswith((".", "$")) and d != "node_modules"
        ]
        for name in filenames + dirnames:
            if query in name.lower():
                full = Path(dirpath) / name
                hits.append(
                    {"path": str(full), "type": "dir" if full.is_dir() else "file"}
                )
                if len(hits) >= limit:
                    return {"matches": hits, "truncated": True}
        if time.monotonic() > deadline:
            return {
                "matches": hits,
                "truncated": True,
                "note": "busqueda cortada por tiempo",
            }
    return {"matches": hits, "truncated": False}


def files_write(ctx: Ctx, p: Params) -> dict:
    f = ctx.policy.resolve_path(_str(p, "path"), write=True)
    content = _str(p, "content", required=False, max_len=1_000_000)
    if f.suffix.lower() in EXECUTABLE_EXT:
        raise BridgeError("ext_blocked", f"No se escriben ficheros {f.suffix}.", 403)
    if f.exists() and not p.get("overwrite", False):
        raise BridgeError("exists", f"Ya existe {f}; usa overwrite=true.", 409)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    return {"path": str(f), "bytes": len(content.encode("utf-8"))}


def files_move(ctx: Ctx, p: Params) -> dict:
    src = ctx.policy.resolve_path(_str(p, "src"), write=True)
    dst = ctx.policy.resolve_path(_str(p, "dst"), write=True)
    if not src.exists():
        raise BridgeError("not_found", f"No existe: {src}", 404)
    if dst.exists():
        raise BridgeError("exists", f"Ya existe el destino: {dst}", 409)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return {"src": str(src), "dst": str(dst)}


def files_trash(ctx: Ctx, p: Params) -> dict:
    f = ctx.policy.resolve_path(_str(p, "path"), write=True)
    if not f.exists():
        raise BridgeError("not_found", f"No existe: {f}", 404)
    if any(f == r.resolve(strict=False) for r in ctx.policy.write_roots):
        raise BridgeError(
            "path_not_allowed", "No se envia a la papelera una raiz completa.", 403
        )
    ctx.win.recycle(f)
    return {"trashed": str(f), "recoverable": "Papelera de reciclaje de Windows"}


# ---------------------------------------------------------------------------
# sistema
# ---------------------------------------------------------------------------


def system_info(ctx: Ctx, p: Params) -> dict:
    return ctx.win.system_info()


def system_media(ctx: Ctx, p: Params) -> dict:
    key = _str(p, "key", max_len=20)
    times = max(1, min(_int(p, "times", 1) or 1, 20))
    for _ in range(times):
        ctx.win.media_key(key)
        time.sleep(0.05)
    return {"key": key, "times": times}


def system_lock(ctx: Ctx, p: Params) -> dict:
    ctx.win.lock_workstation()
    return {"locked": True}


# ruta -> (capacidad, manejador)
ROUTES: dict[str, tuple[str, Callable[[Ctx, Params], dict]]] = {
    "apps.list": ("apps.open", apps_list),
    "apps.open": ("apps.open", apps_open),
    "windows.list": ("windows.read", windows_list),
    "windows.active": ("windows.read", windows_active),
    "windows.focus": ("windows.control", windows_focus),
    "windows.minimize": ("windows.control", _show("SW_MINIMIZE")),
    "windows.maximize": ("windows.control", _show("SW_MAXIMIZE")),
    "windows.restore": ("windows.control", _show("SW_RESTORE")),
    "windows.close": ("windows.close", windows_close),
    "ui.elements": ("ui.inspect", ui_elements),
    "ui.invoke": ("ui.invoke", ui_invoke),
    "ui.read_text": ("ui.read_text", ui_read_text),
    "input.type": ("input.keyboard", input_type),
    "input.paste": ("input.keyboard", input_paste),
    "input.hotkey": ("input.keyboard", input_hotkey),
    "input.click": ("input.mouse", input_click),
    "files.list": ("files.read", files_list),
    "files.read": ("files.read", files_read),
    "files.search": ("files.read", files_search),
    "files.write": ("files.write", files_write),
    "files.move": ("files.write", files_move),
    "files.trash": ("files.trash", files_trash),
    "system.info": ("system.info", system_info),
    "system.media": ("system.media", system_media),
    "system.lock": ("system.lock", system_lock),
}
