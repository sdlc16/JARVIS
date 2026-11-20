# ruff: noqa: E501
"""Politica, autenticacion, confirmacion, rutas y registro del puente (doble de Win32)."""

from __future__ import annotations

import json

import pytest
from jarvis_bridge.security import (
    MAGIC,
    BridgeError,
    PlainProtector,
    Policy,
    TokenStore,
)

from tests.bridge.conftest import TOKEN


def data(resp):
    status, payload = resp
    assert payload["ok"], payload
    return payload["data"]


def err(resp, status, code):
    s, payload = resp
    assert s == status and not payload["ok"], (s, payload)
    assert payload["error"]["code"] == code, payload


# --- autenticacion / origen (criterios de T1.5, ahora sobre el puente completo) ------


def test_missing_and_invalid_token_are_rejected_and_nothing_runs(call, win):
    err(
        call("POST", "/v1/system/media", {"key": "mute"}, token=None),
        401,
        "missing_token",
    )
    err(
        call("POST", "/v1/system/media", {"key": "mute"}, token="x" * 43),
        403,
        "invalid_token",
    )
    assert win.calls == []


def test_origin_outside_wsl_subnet_is_rejected(call):
    err(call("GET", "/v1/health", ip="192.168.1.50"), 403, "origin_not_allowed")
    err(call("GET", "/v1/health", ip="8.8.8.8"), 403, "origin_not_allowed")
    err(call("GET", "/v1/health", ip="not-an-ip"), 403, "origin_not_allowed")


def test_health_and_capabilities_need_auth_and_report_policy(call):
    assert data(call("GET", "/v1/health"))["version"]
    caps = data(call("GET", "/v1/capabilities"))["capabilities"]
    assert caps["files.trash"] == {"enabled": True, "sensitive": True}
    assert (
        caps["system.lock"]["enabled"] is False
    )  # bloquear sesion: apagado por defecto
    assert caps["windows.read"]["sensitive"] is False


def test_unknown_routes_and_bad_bodies(call):
    err(call("POST", "/v1/nope/nada", {}), 404, "unknown_action")
    err(call("GET", "/otra/cosa"), 404, "not_found")
    err(call("POST", "/v1/windows/list", raw=b"{no json"), 400, "bad_json")
    err(call("POST", "/v1/windows/list", raw=b"[1,2]"), 400, "bad_json")


# --- confirmacion de acciones sensibles --------------------------------------------


@pytest.mark.parametrize(
    "route,body",
    [
        ("/v1/input/type", {"text": "hola", "title": "Bloc de notas"}),
        ("/v1/input/paste", {"text": "hola", "title": "Bloc de notas"}),
        ("/v1/input/hotkey", {"keys": ["ctrl", "s"], "title": "Bloc de notas"}),
        ("/v1/input/click", {"x": 1, "y": 1}),
        ("/v1/windows/close", {"hwnd": 102}),
        ("/v1/files/write", {"path": "X", "content": "a"}),
        ("/v1/files/trash", {"path": "X"}),
    ],
)
def test_sensitive_actions_require_confirmation(call, win, route, body):
    err(call("POST", route, body), 428, "confirmation_required")
    assert win.calls == []  # no se ejecuto nada sin confirmar


def test_non_sensitive_actions_do_not_need_confirmation(call):
    assert data(call("POST", "/v1/windows/list", {}))["windows"]
    assert data(call("POST", "/v1/system/info", {}))["ram_total_gb"] == 32.0
    assert data(call("POST", "/v1/system/media", {"key": "mute"}))["key"] == "mute"


def test_disabled_capability_is_refused_even_when_confirmed(call, server):
    err(
        call("POST", "/v1/system/lock", {"confirmed": True}), 403, "capability_disabled"
    )


# --- ventanas / teclado: nunca teclear a ciegas ------------------------------------------


def test_type_focuses_target_then_types_and_reports_window(call, win):
    r = data(
        call(
            "POST",
            "/v1/input/type",
            {"title": "Calculadora", "text": "12+3", "confirmed": True},
        )
    )
    assert r["typed_chars"] == 4 and r["window"]["hwnd"] == 102
    assert win.calls == [("focus", 102), ("type", 102, "12+3")]


def test_type_aborts_without_sending_keys_if_focus_fails(call, win):
    win.focus_ok = False
    err(
        call(
            "POST",
            "/v1/input/type",
            {"title": "Calculadora", "text": "secreto", "confirmed": True},
        ),
        409,
        "focus_failed",
    )
    assert not any(c[0] == "type" for c in win.calls)


def test_type_requires_an_explicit_target_window(call, win):
    err(
        call("POST", "/v1/input/type", {"text": "x", "confirmed": True}),
        400,
        "bad_request",
    )
    assert win.calls == []


def test_ambiguous_window_title_is_refused(call):
    err(
        call("POST", "/v1/windows/focus", {"title": "Bloc de notas"}),
        409,
        "window_ambiguous",
    )


def test_window_actions(call, win):
    data(call("POST", "/v1/windows/minimize", {"hwnd": 102}))
    data(call("POST", "/v1/windows/maximize", {"hwnd": 102}))
    data(call("POST", "/v1/windows/restore", {"hwnd": 102}))
    assert [c[2] for c in win.calls if c[0] == "show"] == [6, 3, 9]
    r = data(call("POST", "/v1/windows/close", {"hwnd": 102, "confirmed": True}))
    assert r["closed"] is True


def test_type_text_length_limit(call):
    err(
        call(
            "POST",
            "/v1/input/type",
            {"title": "Calculadora", "text": "a" * 2001, "confirmed": True},
        ),
        400,
        "bad_request",
    )


# --- verificacion de lo tecleado / pegado --------------------------------------------------


def test_type_reports_verified_true_when_readback_matches(call, monkeypatch):
    from jarvis_bridge import uia

    monkeypatch.setattr(
        uia, "run", lambda op, hwnd, **kw: {"result": {"text": "hola 12+3"}}
    )
    r = data(
        call(
            "POST",
            "/v1/input/type",
            {"title": "Calculadora", "text": "12+3", "confirmed": True},
        )
    )
    assert r["verified"] is True and "note" not in r


def test_type_detects_when_the_app_alters_the_text(call, monkeypatch):
    """Notepad autocorrige 'rapido'->'rápido': el agente debe enterarse."""
    from jarvis_bridge import uia

    monkeypatch.setattr(
        uia, "run", lambda op, hwnd, **kw: {"result": {"text": "El rápido zorro"}}
    )
    r = data(
        call(
            "POST",
            "/v1/input/type",
            {"title": "Calculadora", "text": "El rapido zorro", "confirmed": True},
        )
    )
    assert r["verified"] is False
    assert r["readback_tail"].endswith("rápido zorro") and "autocorreccion" in r["note"]


def test_type_verified_is_none_when_window_exposes_no_text(call, monkeypatch):
    from jarvis_bridge import uia

    def no_text(op, hwnd, **kw):
        raise BridgeError("ui_failed", "sin texto", 500)

    monkeypatch.setattr(uia, "run", no_text)
    r = data(
        call(
            "POST",
            "/v1/input/type",
            {"title": "Calculadora", "text": "x", "confirmed": True},
        )
    )
    assert r["verified"] is None


def test_verify_can_be_disabled(call, monkeypatch):
    from jarvis_bridge import uia

    monkeypatch.setattr(uia, "run", lambda *a, **k: pytest.fail("no debia leer"))
    r = data(
        call(
            "POST",
            "/v1/input/type",
            {"title": "Calculadora", "text": "x", "verify": False, "confirmed": True},
        )
    )
    assert r["verified"] is None


def test_paste_sets_clipboard_sends_ctrl_v_and_restores_previous(
    call, win, monkeypatch
):
    from jarvis_bridge import uia

    win.clip = "mi portapapeles previo"
    monkeypatch.setattr(
        uia, "run", lambda op, hwnd, **kw: {"result": {"text": "texto largo ñ"}}
    )
    r = data(
        call(
            "POST",
            "/v1/input/paste",
            {"title": "Calculadora", "text": "texto largo ñ", "confirmed": True},
        )
    )
    assert (
        r["pasted_chars"] == 13
        and r["verified"] is True
        and r["clipboard_restored"] is True
    )
    assert ("clip_set", "texto largo ñ") in win.calls
    assert ("hotkey", 102, ("ctrl", "v")) in win.calls
    assert (
        win.clip == "mi portapapeles previo"
    )  # el portapapeles del usuario se conserva


def test_paste_does_not_clobber_non_text_clipboard_restore_flag(call, win, monkeypatch):
    from jarvis_bridge import uia

    win.clip = None  # el portapapeles tenia una imagen u otro formato: no se puede restaurar texto
    monkeypatch.setattr(uia, "run", lambda *a, **k: {"result": {"text": "x"}})
    r = data(
        call(
            "POST",
            "/v1/input/paste",
            {"title": "Calculadora", "text": "x", "confirmed": True},
        )
    )
    assert r["clipboard_restored"] is False


def test_paste_aborts_without_touching_clipboard_if_focus_fails(call, win):
    win.focus_ok = False
    win.clip = "intacto"
    err(
        call(
            "POST",
            "/v1/input/paste",
            {"title": "Calculadora", "text": "x", "confirmed": True},
        ),
        409,
        "focus_failed",
    )
    assert win.clip == "intacto" and not any(c[0] == "clip_set" for c in win.calls)


# --- UI Automation (controles por nombre) -------------------------------------------------


def test_ui_actions_route_through_policy_and_uia(call, monkeypatch):
    from jarvis_bridge import uia

    seen = []

    def fake_run(op, hwnd, **kw):
        seen.append((op, hwnd, kw))
        return {
            "elements": {"result": [{"name": "Guardar", "type": "Button", "id": ""}]},
            "invoke": {
                "result": {"invoked": True, "name": "No guardar", "pattern": "Invoke"}
            },
            "read": {"result": {"text": "contenido de la ventana"}},
        }[op]

    monkeypatch.setattr(uia, "run", fake_run)
    r = data(call("POST", "/v1/ui/elements", {"hwnd": 102, "types": ["Button"]}))
    assert r["elements"][0]["name"] == "Guardar" and seen[0][1] == 102
    err(
        call("POST", "/v1/ui/invoke", {"hwnd": 102, "name": "No guardar"}),
        428,
        "confirmation_required",
    )
    err(call("POST", "/v1/ui/read_text", {"hwnd": 102}), 428, "confirmation_required")
    assert [s[0] for s in seen] == [
        "elements"
    ]  # sensibles: no se ejecutaron sin confirmar
    r = data(
        call(
            "POST",
            "/v1/ui/invoke",
            {"hwnd": 102, "name": "No guardar", "confirmed": True},
        )
    )
    assert r["invoked"] and r["pattern"] == "Invoke"
    r = data(call("POST", "/v1/ui/read_text", {"hwnd": 102, "confirmed": True}))
    assert r["text"] == "contenido de la ventana"


def test_ui_invoke_reports_not_found_and_ambiguous(call, monkeypatch):
    from jarvis_bridge import uia

    monkeypatch.setattr(
        uia, "run", lambda op, hwnd, **kw: {"result": {"error": "not_found"}}
    )
    err(
        call("POST", "/v1/ui/invoke", {"hwnd": 102, "name": "X", "confirmed": True}),
        404,
        "ui_not_found",
    )
    monkeypatch.setattr(
        uia,
        "run",
        lambda op, hwnd, **kw: {"result": {"error": "ambiguous", "count": 2}},
    )
    err(
        call("POST", "/v1/ui/invoke", {"hwnd": 102, "name": "X", "confirmed": True}),
        409,
        "ui_ambiguous",
    )


def test_uia_is_unavailable_off_windows():
    import sys

    from jarvis_bridge import uia

    if sys.platform == "win32":
        pytest.skip("en Windows se prueba con selftest.py")
    with pytest.raises(BridgeError) as e:
        uia.run("elements", 1)
    assert e.value.code == "unsupported"


# --- apps ----------------------------------------------------------------------------


def test_app_not_in_allowlist_is_refused(call):
    err(call("POST", "/v1/apps/open", {"name": "cmd.exe"}), 403, "app_not_allowed")
    err(call("POST", "/v1/apps/open", {"name": "powershell"}), 403, "app_not_allowed")


def test_open_requires_exactly_one_target_and_http_urls_only(call):
    err(call("POST", "/v1/apps/open", {}), 400, "bad_request")
    err(
        call("POST", "/v1/apps/open", {"name": "notepad", "url": "http://x"}),
        400,
        "bad_request",
    )
    err(
        call("POST", "/v1/apps/open", {"url": "file:///C:/Windows/System32/cmd.exe"}),
        400,
        "bad_url",
    )
    err(call("POST", "/v1/apps/open", {"url": "javascript:alert(1)"}), 400, "bad_url")


def test_executables_are_never_opened_by_path(call, home):
    exe = home / "Downloads" / "setup.exe"
    exe.write_text("x")
    err(call("POST", "/v1/apps/open", {"path": str(exe)}), 403, "app_not_allowed")


# --- archivos: raices permitidas, enlaces, papelera ---------------------------------------


def test_read_roots_allow_desktop_and_forbid_everything_else(call, home):
    (home / "Desktop" / "nota.txt").write_text("hola mundo", encoding="utf-8")
    r = data(
        call("POST", "/v1/files/read", {"path": str(home / "Desktop" / "nota.txt")})
    )
    assert r["content"] == "hola mundo"
    err(
        call("POST", "/v1/files/read", {"path": str(home / "Secretos" / "x.txt")}),
        403,
        "path_not_allowed",
    )
    err(
        call("POST", "/v1/files/read", {"path": "C:\\Windows\\System32\\config\\SAM"}),
        403,
        "path_not_allowed",
    )
    err(
        call("POST", "/v1/files/read", {"path": "/etc/passwd"}), 403, "path_not_allowed"
    )


def test_dotdot_traversal_is_normalised_and_blocked(call, home):
    sneaky = str(home / "Desktop" / ".." / "Secretos" / "x.txt")
    err(call("POST", "/v1/files/read", {"path": sneaky}), 403, "path_not_allowed")


def test_symlink_escape_is_blocked(call, home):
    secret = home / "Secretos" / "clave.txt"
    secret.write_text("hunter2")
    link = home / "Desktop" / "enlace.txt"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("sin permisos para symlinks")
    err(call("POST", "/v1/files/read", {"path": str(link)}), 403, "path_not_allowed")


def test_list_returns_exact_counts_not_left_to_the_model(call, home):
    d = home / "Downloads"
    for i in range(7):
        (d / f"f{i}.txt").write_text("x")
    (d / "carpeta").mkdir()
    r = data(call("POST", "/v1/files/list", {"path": str(d), "limit": 3}))
    assert r["total_entries"] == 8 and r["total_files"] == 7 and r["total_folders"] == 1
    assert r["returned"] == 3 and r["truncated"] is True
    r = data(call("POST", "/v1/files/list", {"path": str(d)}))
    assert r["returned"] == 8 and r["truncated"] is False


def test_list_and_search(call, home):
    (home / "Documents" / "informe_final.txt").write_text("x")
    (home / "Documents" / "otro.md").write_text("x")
    r = data(call("POST", "/v1/files/list", {"path": str(home / "Documents")}))
    assert {e["name"] for e in r["entries"]} == {
        "JARVIS",
        "informe_final.txt",
        "otro.md",
    }
    s = data(
        call(
            "POST",
            "/v1/files/search",
            {"path": str(home / "Documents"), "query": "informe"},
        )
    )
    assert [m["path"].endswith("informe_final.txt") for m in s["matches"]] == [True]


def test_binary_and_large_files_are_not_read(call, home):
    b = home / "Desktop" / "bin.dat"
    b.write_bytes(b"\x00\x01\x02" * 100)
    err(call("POST", "/v1/files/read", {"path": str(b)}), 415, "binary_file")
    big = home / "Desktop" / "big.txt"
    big.write_text("a" * 2_100_000)
    err(call("POST", "/v1/files/read", {"path": str(big)}), 413, "too_large")


def test_write_only_inside_write_roots_and_never_executables(call, home):
    ok = home / "Documents" / "JARVIS" / "notas.txt"
    r = data(
        call(
            "POST",
            "/v1/files/write",
            {"path": str(ok), "content": "ñandú", "confirmed": True},
        )
    )
    assert ok.read_text(encoding="utf-8") == "ñandú" and r["bytes"] == len(
        "ñandú".encode()
    )
    err(
        call(
            "POST",
            "/v1/files/write",
            {
                "path": str(home / "Desktop" / "x.txt"),
                "content": "a",
                "confirmed": True,
            },
        ),
        403,
        "path_not_allowed",
    )
    err(
        call(
            "POST",
            "/v1/files/write",
            {
                "path": str(home / "Documents" / "JARVIS" / "mal.bat"),
                "content": "a",
                "confirmed": True,
            },
        ),
        403,
        "ext_blocked",
    )
    err(
        call(
            "POST",
            "/v1/files/write",
            {"path": str(ok), "content": "b", "confirmed": True},
        ),
        409,
        "exists",
    )
    data(
        call(
            "POST",
            "/v1/files/write",
            {"path": str(ok), "content": "b", "overwrite": True, "confirmed": True},
        )
    )
    assert ok.read_text() == "b"


def test_trash_goes_to_recycle_bin_never_permanent_delete(call, home, win):
    f = home / "Documents" / "JARVIS" / "borrame.txt"
    f.write_text("x")
    r = data(call("POST", "/v1/files/trash", {"path": str(f), "confirmed": True}))
    assert "Papelera" in r["recoverable"]
    assert ("recycle", str(f)) in win.calls and not f.exists()
    assert (win.trash_dir / "borrame.txt").exists()  # recuperable, no destruido


def test_trash_refuses_files_outside_write_roots_and_whole_roots(call, home):
    f = home / "Desktop" / "no_tocar.txt"
    f.write_text("x")
    err(
        call("POST", "/v1/files/trash", {"path": str(f), "confirmed": True}),
        403,
        "path_not_allowed",
    )
    err(
        call(
            "POST",
            "/v1/files/trash",
            {"path": str(home / "Documents" / "JARVIS"), "confirmed": True},
        ),
        403,
        "path_not_allowed",
    )
    assert f.exists()


def test_move_stays_inside_write_roots(call, home):
    a = home / "Documents" / "JARVIS" / "a.txt"
    a.write_text("x")
    b = home / "Documents" / "JARVIS" / "sub" / "b.txt"
    data(
        call(
            "POST", "/v1/files/move", {"src": str(a), "dst": str(b), "confirmed": True}
        )
    )
    assert b.exists() and not a.exists()
    err(
        call(
            "POST",
            "/v1/files/move",
            {"src": str(b), "dst": str(home / "Desktop" / "b.txt"), "confirmed": True},
        ),
        403,
        "path_not_allowed",
    )


# --- registro: sin token ni texto tecleado -------------------------------------------------


def test_log_records_requests_without_token_or_typed_text(call, home):
    call(
        "POST",
        "/v1/input/type",
        {"title": "Calculadora", "text": "MI-CLAVE-SECRETA", "confirmed": True},
    )
    call("POST", "/v1/system/media", {"key": "mute"}, token="bad")
    log = (home / "bridge.jsonl").read_text(encoding="utf-8")
    assert (
        TOKEN not in log
        and "MI-CLAVE-SECRETA" not in log
        and "bad" not in log.replace("bad_", "")
    )
    events = [json.loads(line) for line in log.splitlines()]
    assert {"input.type", None} <= {e.get("action") for e in events}
    assert any(e.get("params", {}).get("text") == "<16 chars>" for e in events)
    assert any(e["status"] == 403 for e in events) and all("ms" in e for e in events)


# --- politica y token -------------------------------------------------------------------


def test_policy_roundtrip_and_overrides(tmp_path, home):
    pol = Policy.default(home)
    pol.capabilities["input.mouse"] = False
    pol.apps["bloc"] = "notepad.exe"
    cfg = tmp_path / "cfg.json"
    pol.save(cfg)
    loaded = Policy.load(cfg, home)
    assert (
        loaded.capabilities["input.mouse"] is False
        and loaded.apps["bloc"] == "notepad.exe"
    )
    cfg.write_text(
        json.dumps({"capabilities": {"files.read": False, "inventada": True}})
    )
    p2 = Policy.load(cfg, home)
    assert p2.capabilities["files.read"] is False and "inventada" not in p2.capabilities


def test_token_is_never_stored_in_plain_text_and_is_stable(token_store):
    t1 = token_store.load_or_create()
    raw = token_store.path.read_bytes()
    assert raw.startswith(MAGIC) and len(t1) >= 40
    assert token_store.load_or_create() == t1  # estable entre arranques
    t2 = token_store.rotate()
    assert t2 != t1 and token_store.load_or_create() == t2


def test_dpapi_protector_is_not_used_off_windows():
    import sys

    from jarvis_bridge.security import DpapiProtector

    if sys.platform == "win32":
        pytest.skip("en Windows se prueba con selftest.py")
    with pytest.raises(RuntimeError):
        DpapiProtector().protect(b"x")


def test_plain_protector_rejects_garbage(tmp_path):
    store = TokenStore(tmp_path / "t.bin", PlainProtector())
    (tmp_path / "t.bin").write_bytes(b"BASURA")
    with pytest.raises(ValueError):
        store.load_or_create()
    assert BridgeError("c", "m", 400).status == 400
