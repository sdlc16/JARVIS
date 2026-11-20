# ruff: noqa: E501
"""Broker de confirmaciones: politica, canales, timeout (deniega), auditoria."""

from __future__ import annotations

import json
import threading
import time

import pytest

from openjarvis.jarvis.confirmation import (
    ConfirmationBroker,
    describe_action,
    parse_prompt,
    parse_yes_no,
)


def prompt_for(tool, args):
    return f"Allow execution of tool '{tool}' with args {args}?"


# --- parseo del aviso de ToolExecutor -------------------------------------------------


def test_parse_prompt_roundtrip_with_tricky_args():
    args = {
        "text": "él dijo 'hola' ¿qué tal?\nlínea2",
        "title": "Bloc",
        "n": 3,
        "ok": True,
    }
    tool, got = parse_prompt(prompt_for("windows_type", args))
    assert tool == "windows_type" and got == args


@pytest.mark.parametrize(
    "bad", ["", "hola", "Allow execution of tool 'x' with args {oops?"]
)
def test_parse_prompt_rejects_garbage_safely(bad):
    tool, args = parse_prompt(bad)
    assert args == {} and (tool == "" or tool == "x")


def test_unparseable_prompt_is_denied_not_approved():
    b = ConfirmationBroker(timeout_s=0.2, audit_path=None)
    assert b.callback()("texto inesperado") is False


# --- descripciones legibles --------------------------------------------------------------


@pytest.mark.parametrize(
    "tool,args,expected",
    [
        (
            "windows_type",
            {"text": "hola", "title": "Bloc de notas"},
            "Escribir «hola» en la ventana «Bloc de notas»",
        ),
        (
            "windows_type",
            {"text": "x", "hwnd": 5, "method": "paste"},
            "Pegar «x» en la ventana #5",
        ),
        ("windows_hotkey", {"keys": ["ctrl", "s"], "title": "Bloc"}, "Pulsar ctrl + s"),
        ("windows_click", {"x": 10, "y": 20}, "(10, 20)"),
        (
            "windows_close_window",
            {"title": "Calculadora"},
            "Cerrar la ventana «Calculadora»",
        ),
        (
            "windows_ui_act",
            {"action": "invoke", "name": "Guardar", "hwnd": 1},
            "Pulsar el control «Guardar»",
        ),
        ("windows_ui_act", {"action": "read_text", "hwnd": 1}, "Leer el texto"),
        ("windows_files_modify", {"action": "trash", "path": "C:/a/b.txt"}, "papelera"),
        (
            "windows_files_modify",
            {"action": "write", "path": "C:/a.txt", "content": "abc"},
            "(3 caracteres)",
        ),
        ("herramienta_rara", {"a": 1}, "Ejecutar herramienta_rara"),
    ],
)
def test_describe_action_is_human_readable(tool, args, expected):
    assert expected in describe_action(tool, args)


def test_long_text_is_shortened_in_the_description():
    d = describe_action("windows_type", {"text": "a" * 500, "title": "x"})
    assert len(d) < 160 and "…" in d


@pytest.mark.parametrize(
    "said,expected",
    [
        ("sí", True),
        ("Sí, hazlo", True),
        ("claro que sí", True),
        ("vale", True),
        ("adelante", True),
        ("no", False),
        ("No, cancela", False),
        ("sí pero no", False),
        ("para", False),
        ("cancela eso", False),
        ("mmm", None),
        ("", None),
        ("quizás", None),
        ("hazlo", True),
        ("hazllo", True),  # error tipico del STT
        ("no hazlo", False),
        ("afloj", None),  # irreconocible: nunca se asume que si
    ],
)
def test_parse_yes_no(said, expected):
    assert parse_yes_no(said) is expected


# --- flujo de peticiones --------------------------------------------------------------------


def test_gui_style_resolution_from_another_thread():
    b = ConfirmationBroker(timeout_s=5, audit_path=None)
    seen = []
    b.subscribe(seen.append)

    def approve_when_pending():
        for _ in range(100):
            p = b.pending()
            if p:
                assert (
                    p[0]["tool"] == "windows_type" and "Escribir" in p[0]["description"]
                )
                b.resolve(p[0]["id"], True, by="gui")
                return
            time.sleep(0.02)

    t = threading.Thread(target=approve_when_pending)
    t.start()
    assert b.request("windows_type", {"text": "hola", "title": "x"}) is True
    t.join()
    assert [e["type"] for e in seen] == ["requested", "resolved"]
    assert b.recent()[0]["decided_by"] == "gui" and b.pending() == []


def test_denial_and_timeout_deny():
    b = ConfirmationBroker(timeout_s=0.3, audit_path=None)
    assert b.request("windows_click", {"x": 1, "y": 1}) is False  # nadie contesta
    assert b.recent()[0]["state"] == "timeout"

    def deny():
        time.sleep(0.05)
        b.resolve(b.pending()[0]["id"], False)

    threading.Thread(target=deny).start()
    assert (
        ConfirmationBroker.request(b, "windows_click", {"x": 1, "y": 1}, timeout=3)
        is False
    )
    assert b.recent()[0]["state"] == "denied"


def test_voice_style_channel_resolves_first_and_is_credited():
    b = ConfirmationBroker(timeout_s=5, audit_path=None)

    def voice(p):
        assert "Escribir" in p.description
        return True

    voice.channel_name = "voz"
    assert (
        b.request("windows_type", {"text": "x", "title": "t"}, channels=[voice]) is True
    )
    assert b.recent()[0]["decided_by"] == "voz"


def test_channel_returning_none_does_not_decide_and_late_results_are_ignored():
    b = ConfirmationBroker(timeout_s=0.4, audit_path=None)
    assert (
        b.request("windows_click", {"x": 1, "y": 1}, channels=[lambda p: None]) is False
    )
    assert b.resolve("zzzz", True) is False  # id inexistente
    late = b.recent()[0]
    assert b.resolve(late["id"], True) is False  # ya resuelta: no se puede cambiar


def test_broken_channel_does_not_crash_or_approve():
    def boom(p):
        raise RuntimeError("canal roto")

    b = ConfirmationBroker(timeout_s=0.3, audit_path=None)
    assert b.request("windows_click", {"x": 1, "y": 1}, channels=[boom]) is False


def test_policy_allow_and_deny_skip_the_prompt():
    b = ConfirmationBroker(
        timeout_s=0.2,
        policy={"windows_type": "allow", "windows_click": "deny"},
        audit_path=None,
    )
    assert b.request("windows_type", {"text": "x", "title": "t"}) is True
    assert b.request("windows_click", {"x": 1, "y": 1}) is False
    assert [r["decided_by"] for r in b.recent()] == ["politica:deny", "politica:allow"]
    with pytest.raises(ValueError):
        b.set_policy("windows_type", "siempre")
    b.set_policy("windows_type", "ask")
    assert (
        b.request("windows_type", {"text": "x", "title": "t"}) is False
    )  # vuelve a preguntar -> timeout


def test_audit_log_has_decisions_but_never_arguments(tmp_path):
    log = tmp_path / "audit.jsonl"
    b = ConfirmationBroker(
        timeout_s=0.2, policy={"windows_type": "allow"}, audit_path=log
    )
    b.request("windows_type", {"text": "MI-CONTRASEÑA-123", "title": "banco"})
    b.request("windows_click", {"x": 1, "y": 1})
    raw = log.read_text(encoding="utf-8")
    assert "MI-CONTRASEÑA-123" not in raw and "banco" not in raw
    rows = [json.loads(line) for line in raw.splitlines()]
    assert rows[0]["tool"] == "windows_type" and rows[0]["approved"] is True
    assert rows[1]["approved"] is False and rows[1]["state"] == "timeout"
