"""``jarvis gui`` -- interfaz gráfica local de JARVIS (chat, voz, permisos, memoria…).

Solo escucha en 127.0.0.1 con un token aleatorio por ejecución. Con ``--open`` abre
la ventana de la aplicación en el navegador de Windows (Edge en modo app).
"""

from __future__ import annotations

import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser

import click
from rich.console import Console

from openjarvis.cli._banner import print_banner


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
@click.option("-m", "--model", "model_name", default=None, help="Modelo (Ollama).")
@click.option(
    "--no-bridge-tools",
    is_flag=True,
    help="Sin herramientas de Windows (solo chat y memoria).",
)
def gui(
    port: int, open_it: bool, model_name: str | None, no_bridge_tools: bool
) -> None:
    """Abre la interfaz gráfica de JARVIS (solo local)."""
    print_banner(quiet=False)
    console = Console(stderr=True)

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
        uvicorn.run(app, host="127.0.0.1", port=real_port, log_level="warning")
    finally:
        voice.stop()
        time.sleep(0.1)


def _preload(runtime, console: Console) -> None:
    try:
        runtime.preload()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Ollama no se precargó: {exc}[/yellow]")


def _bridge_log_candidates():
    from pathlib import Path

    users = Path("/mnt/c/Users")
    try:
        entries = list(users.iterdir()) if users.is_dir() else []
    except OSError:
        entries = []
    for u in entries:
        yield u / "AppData" / "Local" / "JARVIS" / "logs" / "bridge.jsonl"
