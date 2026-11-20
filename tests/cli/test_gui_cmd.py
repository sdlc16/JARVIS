"""`jarvis gui`: instancia unica por pidfile (sin secretos) y parada segura."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest
from click.testing import CliRunner

from openjarvis.cli import gui_cmd

linux_only = pytest.mark.skipif(
    not os.path.exists("/proc/self/cmdline"), reason="requiere /proc (Linux/WSL)"
)


@pytest.fixture
def pidfile(tmp_path, monkeypatch):
    p = tmp_path / "gui.pid"
    monkeypatch.setattr(gui_cmd, "pidfile_path", lambda: p)
    return p


def spawn(*extra):
    """Proceso real cuya linea de comandos parece `jarvis gui` (o no)."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", *extra],
    )


@linux_only
def test_pidfile_holds_only_the_pid_never_a_token(pidfile):
    gui_cmd.write_pidfile()
    assert pidfile.read_text() == str(os.getpid())
    gui_cmd.clear_pidfile()
    assert not pidfile.exists()
    gui_cmd.clear_pidfile()  # idempotente


@linux_only
def test_stale_pid_is_cleaned_and_not_reported_as_running(pidfile):
    pidfile.write_text("999999")  # no existe
    assert gui_cmd.running_instance() is None and not pidfile.exists()
    pidfile.write_text("no-es-un-numero")
    assert gui_cmd.running_instance() is None


@linux_only
def test_a_reused_pid_of_an_unrelated_process_is_never_killed(pidfile):
    other = spawn("--otra-cosa")  # vivo, pero NO es la interfaz de JARVIS
    try:
        pidfile.write_text(str(other.pid))
        assert gui_cmd.running_instance() is None  # no se confunde
        assert gui_cmd.stop_instance(other.pid) is False
        assert other.poll() is None  # sigue vivo: no se le envio ninguna señal
    finally:
        other.kill()
        other.wait()


@linux_only
def test_running_gui_is_detected_and_stopped(pidfile):
    ours = spawn("jarvis", "gui")
    try:
        pidfile.write_text(str(ours.pid))
        assert gui_cmd.running_instance() == ours.pid
        assert gui_cmd.stop_instance(ours.pid, timeout=5) is True
        ours.wait(timeout=5)
        assert ours.poll() is not None and not pidfile.exists()
    finally:
        if ours.poll() is None:
            ours.kill()


@linux_only
def test_cli_refuses_a_second_instance_and_stop_closes_it(pidfile):
    ours = spawn("jarvis", "gui")
    try:
        pidfile.write_text(str(ours.pid))
        r = CliRunner().invoke(gui_cmd.gui, [])
        assert r.exit_code == 3 and "Ya hay una interfaz" in r.output
        assert ours.poll() is None  # no se toco
        r = CliRunner().invoke(gui_cmd.gui, ["--stop"])
        assert r.exit_code == 0 and "detenida" in r.output
        ours.wait(timeout=8)
        assert ours.poll() is not None
    finally:
        if ours.poll() is None:
            ours.kill()
    r = CliRunner().invoke(gui_cmd.gui, ["--stop"])
    assert "No había" in r.output


def test_free_port_skips_busy_ports():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        s.listen()
        assert gui_cmd._free_port(busy) != busy
    time.sleep(0)
