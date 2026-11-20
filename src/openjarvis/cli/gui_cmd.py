"""``jarvis gui`` -- interfaz gráfica local de JARVIS (chat, voz, permisos, memoria…).

Solo escucha en 127.0.0.1 con un token aleatorio por ejecución. Con ``--open`` abre
la ventana de la aplicación en el navegador de Windows (Edge en modo app).
"""

from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import click
from rich.console import Console

from openjarvis.cli._banner import print_banner


def pidfile_path() -> Path:
    from openjarvis.core.config import DEFAULT_CONFIG_DIR

    return DEFAULT_CONFIG_DIR / "gui.pid"  # solo el PID: sin token ni secretos


def write_pidfile() -> None:
    p = pidfile_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(os.getpid()), encoding="utf-8")


def clear_pidfile() -> None:
    try:
        pidfile_path().unlink()
    except OSError:
        pass


def _is_our_gui(pid: int) -> bool:
    """El PID existe Y es una interfaz de JARVIS (no un PID reutilizado)."""
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode()
    except OSError:
        return False
    return "jarvis" in cmd and "gui" in cmd


def running_instance() -> int | None:
    try:
        pid = int(pidfile_path().read_text().strip())
    except (OSError, ValueError):
        return None
    if pid != os.getpid() and _is_our_gui(pid):
        return pid
    clear_pidfile()  # PID huérfano de una ejecución anterior
    return None


def stop_instance(pid: int, timeout: float = 8.0) -> bool:
    if not _is_our_gui(pid):
        return False
    os.kill(pid, signal.SIGTERM)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _is_our_gui(pid):
            clear_pidfile()
            return True
        time.sleep(0.2)
    os.kill(pid, signal.SIGKILL)
    clear_pidfile()
    return True


def _free_port(preferred: int) -> int:
    for port in (preferred, *range(preferred + 1, preferred + 20)):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise click.ClickException("No hay un puerto libre para la interfaz.")


def open_window(url: str) -> bool:
    """Abre la GUI como aplicación de Windows (Edge --app) o en el navegador."""
    for cmd in (
        ["cmd.exe", "/c", "start", "", "msedge", f"--app={url}"],
        ["cmd.exe", "/c", "start", "", url],
    ):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            continue
    return webbrowser.open(url)


@click.command()
@click.option("--port", default=8765, type=int, help="Puerto local preferido.")
@click.option("--open", "open_it", is_flag=True, help="Abrir la ventana al iniciar.")
@click.option(
    "--replace",
    is_flag=True,
    help="Si ya hay una interfaz en marcha, cerrarla y arrancar esta.",
)
@click.option(
    "--stop", "stop_only", is_flag=True, help="Solo cierra la interfaz en marcha."
)
@click.option("-m", "--model", "model_name", default=None, help="Modelo (Ollama).")
@click.option(
    "--no-bridge-tools",
    is_flag=True,
    help="Sin herramientas de Windows (solo chat y memoria).",
)
def gui(
    port: int,
    open_it: bool,
    replace: bool,
    stop_only: bool,
    model_name: str | None,
    no_bridge_tools: bool,
) -> None:
    """Abre la interfaz gráfica de JARVIS (solo local)."""
    console = Console(stderr=True)
    old = running_instance()
    if stop_only:
        console.print(
            "[green]Interfaz detenida.[/green]"
            if old and stop_instance(old)
            else "[dim]No había ninguna interfaz en marcha.[/dim]"
        )
        return
    if old is not None:
        if not replace:
            console.print(
                f"[yellow]Ya hay una interfaz en marcha (pid {old}).[/yellow] "
                "Usa --replace para reiniciarla o --stop para cerrarla."
            )
            sys.exit(3)
        stop_instance(old)
    # PID registrado YA (antes de cargar modelos): --stop/--replace funcionan
    # aunque la interfaz aun este arrancando.
    write_pidfile()
    print_banner(quiet=False)

    import uvicorn

    from openjarvis.core.config import load_config
    from openjarvis.engine import get_engine
    from openjarvis.jarvis.confirmation import ConfirmationBroker
    from openjarvis.jarvis.gui.app import create_app
    from openjarvis.jarvis.gui.events import EventHub
    from openjarvis.jarvis.gui.voice_service import VoiceService
    from openjarvis.jarvis.knowledge import KnowledgeBase, default_roots
    from openjarvis.jarvis.runtime import DEFAULT_TOOLS, JarvisRuntime
    from openjarvis.windows import BridgeClient

    config = load_config()
    resolved = get_engine(config, config.intelligence.preferred_engine or None)
    if resolved is None:
        console.print(
            "[red bold]Ollama no responde. Inícialo y vuelve a probar.[/red bold]"
        )
        sys.exit(1)
    _name, engine = resolved
    model = model_name or config.intelligence.default_model
    if not model:
        console.print("[red]No hay modelo configurado.[/red]")
        sys.exit(1)

    broker = ConfirmationBroker(timeout_s=config.voice.confirm_timeout_s)
    bridge = BridgeClient()
    tools = tuple(
        t for t in DEFAULT_TOOLS if not (no_bridge_tools and t.startswith("windows_"))
    )
    runtime = JarvisRuntime(
        config=config,
        engine=engine,
        model=model,
        broker=broker,
        bridge_client=bridge,
        tool_names=tools,
    )
    caps = None
    try:
        caps = bridge.capabilities()
    except Exception:  # noqa: BLE001
        console.print(
            "[yellow]Puente de Windows no disponible (se verá en Estado).[/yellow]"
        )
    kb = KnowledgeBase(roots=default_roots(caps))
    runtime._knowledge = kb  # misma base para el agente y para la GUI
    hub = EventHub()
    voice = VoiceService(runtime, config, hub.publish)
    token = secrets.token_urlsafe(24)
    real_port = _free_port(port)
    bridge_log = None
    for cand in _bridge_log_candidates():
        try:
            if cand.exists():
                bridge_log = cand
                break
        except OSError:  # carpetas de otros usuarios de Windows sin permiso
            continue
    app = create_app(
        runtime=runtime,
        broker=broker,
        token=token,
        kb=kb,
        voice=voice,
        hub=hub,
        bridge_client=bridge,
        config=config,
        bridge_log=bridge_log,
    )
    url = f"http://127.0.0.1:{real_port}/#t={token}"
    threading.Thread(target=lambda: _preload(runtime, console), daemon=True).start()
    console.print(f"[green]JARVIS listo[/green] -> {url}")
    console.print("[dim]Solo accesible desde este PC. Ctrl+C para cerrar.[/dim]")
    if open_it:
        threading.Timer(1.5, lambda: open_window(url)).start()
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=real_port,
            log_level="warning",
            timeout_graceful_shutdown=3,  # el SSE abierto no bloquea el cierre
        )
    finally:
        voice.stop()
        clear_pidfile()
        time.sleep(0.1)


def _preload(runtime, console: Console) -> None:
    try:
        runtime.preload()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Ollama no se precargó: {exc}[/yellow]")


def _bridge_log_candidates():
    users = Path("/mnt/c/Users")
    try:
        entries = list(users.iterdir()) if users.is_dir() else []
    except OSError:
        entries = []
    for u in entries:
        yield u / "AppData" / "Local" / "JARVIS" / "logs" / "bridge.jsonl"
