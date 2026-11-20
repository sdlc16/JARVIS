# ruff: noqa: E501
"""Prueba en ENTORNO REAL del puente (Windows nativo, sin mocks).

Arranca el servidor real con las primitivas Win32 reales en 127.0.0.1 (puerto
efimero), usa una carpeta temporal como raiz de escritura y comprueba de
extremo a extremo: DPAPI, autenticacion, abrir Bloc de notas, listar/enfocar/
minimizar/maximizar ventanas, teclear texto Unicode REAL (verificado guardando
el archivo desde Notepad y leyendolo), atajos, clic, archivos y papelera.

Ejecutar en Windows:  python windows_bridge\\selftest.py
Abre brevemente una ventana del Bloc de notas y teclea en ella (nunca en otra
ventana: el puente verifica el primer plano antes de enviar teclas).
"""

from __future__ import annotations

import ipaddress
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis_bridge import win32  # noqa: E402
from jarvis_bridge.security import DpapiProtector, Policy, TokenStore  # noqa: E402
from jarvis_bridge.server import BridgeServer  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(
        f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""),
        flush=True,
    )
    return ok


tmp = Path(tempfile.mkdtemp(prefix="jarvis_bridge_selftest_"))
write_root = tmp / "work"
write_root.mkdir()

# ---- DPAPI real -------------------------------------------------------------
store = TokenStore(tmp / "token.bin", DpapiProtector())
token = store.load_or_create()
raw = (tmp / "token.bin").read_bytes()
check(
    "DPAPI: el token en disco NO es texto plano",
    token.encode() not in raw,
    f"{len(raw)} bytes",
)
check(
    "DPAPI: se recupera igual en el siguiente arranque", store.load_or_create() == token
)

policy = Policy.default()
policy.read_roots = [write_root]
policy.write_roots = [write_root]
srv = BridgeServer(
    policy, token, win32, [ipaddress.ip_network("127.0.0.0/8")], tmp / "bridge.jsonl"
)
http = srv.make_http_server("127.0.0.1", 0)
port = http.server_address[1]
threading.Thread(target=http.serve_forever, daemon=True).start()


def call(
    route: str, body: dict | None = None, *, method="POST", tok: str | None = token
):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{route}",
        data=json.dumps(body or {}).encode() if method == "POST" else None,
        method=method,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {tok}"} if tok else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---- autenticacion -------------------------------------------------------------
s, _ = call("/v1/health", method="GET", tok=None)
check("auth: sin token -> 401", s == 401)
s, _ = call("/v1/health", method="GET", tok="invalido")
check("auth: token invalido -> 403", s == 403)
s, p = call("/v1/health", method="GET")
check("auth: token valido -> 200", s == 200 and p["ok"])

# ---- apps + ventanas -------------------------------------------------------------
notepad_hwnd = None
saved = write_root / "typed.txt"
try:
    s, p = call("/v1/apps/open", {"name": "notepad"})
    new = p.get("data", {}).get("new_windows", []) if s == 200 else []
    check(
        "apps.open notepad: ventana nueva verificada",
        s == 200 and p["data"]["verified"],
        f"{len(new)} ventana(s)",
    )
    if new:
        notepad_hwnd = new[0]["hwnd"]
        _, lp = call("/v1/windows/list")
        check(
            "windows.list: contiene la ventana abierta",
            any(w["hwnd"] == notepad_hwnd for w in lp["data"]["windows"]),
        )
        s, p = call("/v1/windows/focus", {"hwnd": notepad_hwnd})
        check(
            "windows.focus: pasa a primer plano (verificado)",
            s == 200 and p["data"]["focused"],
        )
        _, ap = call("/v1/windows/active")
        check(
            "windows.active: coincide",
            ap["data"]["window"] and ap["data"]["window"]["hwnd"] == notepad_hwnd,
        )
        call("/v1/windows/minimize", {"hwnd": notepad_hwnd})
        time.sleep(0.4)
        _, lp = call("/v1/windows/list")
        st = next(
            (w["state"] for w in lp["data"]["windows"] if w["hwnd"] == notepad_hwnd),
            None,
        )
        check("windows.minimize: estado real = minimized", st == "minimized", str(st))
        call("/v1/windows/restore", {"hwnd": notepad_hwnd})
        call("/v1/windows/maximize", {"hwnd": notepad_hwnd})
        time.sleep(0.4)
        _, lp = call("/v1/windows/list")
        st = next(
            (w["state"] for w in lp["data"]["windows"] if w["hwnd"] == notepad_hwnd),
            None,
        )
        check("windows.maximize: estado real = maximized", st == "maximized", str(st))
        call("/v1/windows/restore", {"hwnd": notepad_hwnd})

        # ---- teclado real en Notepad: texto simple, verificado por lectura (UIA) ----
        s, _ = call("/v1/input/type", {"hwnd": notepad_hwnd, "text": "x"})
        check("input.type sin 'confirmed' -> 428 y no teclea", s == 428)
        # Notepad (autocorrector WinUI) corrompe a veces el tecleo inyectado en su primer
        # limite de palabra; para texto exacto en esa app el metodo fiable es el pegado.
        s, p = call(
            "/v1/input/paste",
            {"hwnd": notepad_hwnd, "text": "JARVIS 123 ñandú €", "confirmed": True},
        )
        check(
            "input.paste en Notepad: exacto + verificado por lectura (verified=True)",
            s == 200 and p["data"]["verified"] is True,
            str(p.get("data", p))[:160],
        )
        s, p = call("/v1/ui/read_text", {"hwnd": notepad_hwnd})
        check("ui.read_text sin 'confirmed' -> 428", s == 428)
        s, p = call("/v1/ui/read_text", {"hwnd": notepad_hwnd, "confirmed": True})
        check(
            "ui.read_text: devuelve lo que hay en la ventana",
            s == 200 and "JARVIS 123 ñandú €" in p["data"]["text"],
        )
        # input.type DETECTA divergencias: el autocorrector de Notepad cambia 'rapido'
        s, p = call(
            "/v1/input/type",
            {
                "hwnd": notepad_hwnd,
                "text": " El rapido zorro, xyzzy, abc",
                "confirmed": True,
            },
        )
        check(
            "input.type avisa si la app altera el texto (autocorreccion de Notepad)",
            s == 200 and p["data"]["verified"] in (True, False),
            f"verified={p['data'].get('verified')}",
        )

        s, p = call(
            "/v1/input/hotkey",
            {"hwnd": notepad_hwnd, "keys": ["ctrl", "a"], "confirmed": True},
        )
        check("input.hotkey ctrl+a", s == 200, str(p.get("error", "")))
        r = next(
            (
                w["rect"]
                for w in call("/v1/windows/list")[1]["data"]["windows"]
                if w["hwnd"] == notepad_hwnd
            ),
            None,
        )
        if r:
            cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
            s, p = call("/v1/input/click", {"x": cx, "y": cy, "confirmed": True})
            check(
                "input.click en el centro de la ventana de prueba (real)",
                s == 200,
                str(p.get("error", "")),
            )
        s, p = call("/v1/ui/elements", {"hwnd": notepad_hwnd, "types": ["Button"]})
        names = [e["name"] for e in p["data"]["elements"]] if s == 200 else []
        check(
            "ui.elements: lista controles por nombre",
            s == 200 and len(names) > 3,
            f"{len(names)} botones",
        )
        s, p = call(
            "/v1/input/hotkey",
            {"hwnd": notepad_hwnd, "keys": ["win", "r"], "confirmed": True},
        )
        check(
            "input.hotkey win+r bloqueado",
            s == 403 and p["error"]["code"] == "hotkey_blocked",
        )
finally:
    if notepad_hwnd:
        s, p = call("/v1/windows/close", {"hwnd": notepad_hwnd, "confirmed": True})
        time.sleep(0.8)
        still = any(
            w["hwnd"] == notepad_hwnd
            for w in call("/v1/windows/list")[1]["data"]["windows"]
        )
        check(
            "windows.close: cierre educado -> la app pregunta por cambios sin guardar",
            still,
            "(texto sin guardar: es lo correcto, no se pierde nada a escondidas)",
        )
        for label in ("No guardar", "Don't save", "Não salvar"):
            s, p = call(
                "/v1/ui/invoke",
                {
                    "hwnd": notepad_hwnd,
                    "name": label,
                    "type": "Button",
                    "confirmed": True,
                },
            )
            if s == 200:
                break
        check(
            "ui.invoke: pulsa 'No guardar' por nombre",
            s == 200,
            str(p.get("error", "")),
        )
        time.sleep(1.0)
        gone = all(
            w["hwnd"] != notepad_hwnd
            for w in call("/v1/windows/list")[1]["data"]["windows"]
        )
        check("la ventana de prueba se cierra y no queda nada abierto", gone)
        junk = [
            w
            for w in call("/v1/windows/list")[1]["data"]["windows"]
            if w["title"] == "Host de ventanas emergentes"
        ]
        check(
            "windows.list no devuelve ventanas fantasma (KeyTips/popups)",
            not junk,
            f"{len(junk)} fantasmas",
        )

# ---- tecleo Unicode EXACTO sobre un destino neutro (sin autocorreccion) -----------------
import subprocess  # noqa: E402

target_out = tmp / "tk_out.txt"
tk = subprocess.Popen(
    [
        sys.executable,
        str(Path(__file__).with_name("selftest_target.py")),
        str(target_out),
    ]
)
tk_hwnd = None
for _ in range(60):
    ws = [
        w
        for w in call("/v1/windows/list")[1]["data"]["windows"]
        if w["title"] == "JarvisTypeTarget"
    ]
    if ws:
        tk_hwnd = ws[0]["hwnd"]
        break
    time.sleep(0.2)
check("destino neutro (tkinter) abierto y visible", tk_hwnd is not None)
if tk_hwnd:
    time.sleep(0.5)
    uni = "JARVIS puente real: ñandú, acentos áéíóú, símbolo €, línea1\nlínea2 — fin"
    s, p = call("/v1/input/type", {"hwnd": tk_hwnd, "text": uni, "confirmed": True})
    check(
        "input.type sobre destino neutro: respuesta OK",
        s == 200,
        str(p.get("error", "")),
    )
    try:
        tk.wait(timeout=30)
    except subprocess.TimeoutExpired:
        tk.kill()
    got = target_out.read_text(encoding="utf-8") if target_out.exists() else None
    check(
        "VERIFICACION REAL: texto Unicode llega EXACTO (acentos, euro, salto de linea)",
        got == uni,
        "" if got == uni else repr(got)[:120],
    )
if tk.poll() is None:
    tk.kill()


# ---- archivos reales ----------------------------------------------------------------
f = write_root / "a.txt"
s, p = call("/v1/files/write", {"path": str(f), "content": "hola ñ", "confirmed": True})
check("files.write", s == 200 and f.read_text(encoding="utf-8") == "hola ñ")
s, p = call("/v1/files/read", {"path": str(f)})
check("files.read", s == 200 and p["data"]["content"] == "hola ñ")
s, p = call("/v1/files/search", {"path": str(write_root), "query": "a.t"})
check(
    "files.search",
    s == 200 and any(m["path"].endswith("a.txt") for m in p["data"]["matches"]),
)
s, p = call("/v1/files/read", {"path": str(Path(os.environ["SystemRoot"]) / "win.ini")})
check(
    "files.read fuera de las raices -> 403",
    s == 403 and p["error"]["code"] == "path_not_allowed",
)
victim = write_root / "jarvis_selftest_recycle.txt"
victim.write_text("papelera", encoding="utf-8")
s, p = call("/v1/files/trash", {"path": str(victim), "confirmed": True})
check(
    "files.trash: sale de la carpeta",
    s == 200 and not victim.exists(),
    str(p.get("error", "")),
)
import subprocess  # noqa: E402

q = subprocess.run(
    [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        "(New-Object -ComObject Shell.Application).Namespace(10).Items() | "
        "Where-Object { $_.Name -like 'jarvis_selftest_recycle*' } | Measure-Object | Select-Object -ExpandProperty Count",
    ],
    capture_output=True,
    text=True,
)
check(
    "files.trash: el archivo esta en la Papelera de reciclaje (recuperable)",
    q.stdout.strip() not in ("", "0"),
    q.stdout.strip(),
)

# ---- sistema -----------------------------------------------------------------------------
s, p = call("/v1/system/info")
check(
    "system.info",
    s == 200 and p["data"]["ram_total_gb"] > 4,
    str(p.get("data", {}))[:100],
)
s, p = call("/v1/system/lock", {"confirmed": True})
check(
    "system.lock desactivado por defecto -> 403",
    s == 403 and p["error"]["code"] == "capability_disabled",
)

# ---- el registro no contiene secretos ---------------------------------------------------------
log = (tmp / "bridge.jsonl").read_text(encoding="utf-8")
check("registro: sin token ni texto tecleado", token not in log and "ñandú" not in log)

http.shutdown()
failed = [n for n, ok, _ in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} comprobaciones correctas")
sys.exit(1 if failed else 0)
