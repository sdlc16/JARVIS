"""``jarvis test`` -- comando unico de validacion (T2.7).

Ejecuta, en este orden: analisis estatico (`ruff`) y las suites relevantes
(`pytest`), imprime un resumen por etapa y devuelve un codigo de salida
correcto (0 solo si TODO pasa). `--env` añade un diagnostico del entorno real
(Ollama, GPU/VRAM, herramientas de audio, fuentes de audio de WSLg).

Solo funciona desde un checkout del proyecto (necesita `tests/`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import click
from rich.console import Console

# Archivos fuente que se pasan por ruff en cada ejecucion.
LINT_PATHS = [
    "src/openjarvis/voice",
    "src/openjarvis/cli/voice_cmd.py",
    "src/openjarvis/cli/selftest_cmd.py",
    "src/openjarvis/speech",
    "src/openjarvis/engine/ollama.py",
    "src/openjarvis/engine/degradation.py",
    "src/openjarvis/tasks",
    "src/openjarvis/windows",
    "src/openjarvis/jarvis",
    "src/openjarvis/tools/windows_tools.py",
    "windows_bridge/jarvis_bridge",
    "windows_bridge/selftest.py",
    "tests/voice",
    "tests/bridge",
    "tests/jarvis",
]

SUITES: dict[str, list[str]] = {
    # sin las pruebas con modelos reales (rapido, sin descargas)
    "quick": ["tests/voice", "-m", "not slow"],
    # voz completa (incluye Whisper real sobre grabaciones controladas)
    "voice": [
        "tests/voice",
        "tests/bridge",
        "tests/jarvis",
        "tests/tools/test_windows_tools.py",
        "tests/speech",
        "tests/learning/test_device_selection.py",
    ],
    # voz + el resto de lo ya implementado (regresion)
    "full": [
        "tests/voice",
        "tests/bridge",
        "tests/jarvis",
        "tests/tools/test_windows_tools.py",
        "tests/sessions",
        "tests/speech",
        "tests/learning/test_device_selection.py",
        "tests/engine/test_ollama.py",
        "tests/engine/test_degradation.py",
        "tests/tasks",
        "tests/cli/test_ask_e2e.py",
        "tests/cli/test_doctor_cmd.py",
        "tests/cli/test_doctor_labels.py",
        "tests/cli/test_version_check.py",
        "tests/cli/test_telemetry_cmd.py",
        "tests/telemetry",
    ],
}


def _project_root() -> Path | None:
    root = Path(__file__).resolve().parents[3]
    return (
        root
        if (root / "pyproject.toml").exists() and (root / "tests").is_dir()
        else None
    )


def _run(console: Console, title: str, cmd: list[str], cwd: Path) -> bool:
    console.print(
        f"[bold cyan]> {title}[/bold cyan]  [dim]{' '.join(cmd[2:6])}...[/dim]"
    )
    t0 = time.monotonic()
    env = {**os.environ, "OPENJARVIS_NO_UPDATE_CHECK": "1"}
    proc = subprocess.run(cmd, cwd=cwd, env=env)
    ok = proc.returncode == 0
    color = "green" if ok else "red"
    console.print(
        f"[{color}]{'OK' if ok else 'FALLO'}[/{color}] {title} "
        f"({time.monotonic() - t0:.1f}s)\n"
    )
    return ok


def _env_diagnostics(console: Console) -> None:
    console.print("[bold cyan]> Diagnostico del entorno real[/bold cyan]")

    def line(label: str, value: str, ok: bool = True) -> None:
        mark = "[green]ok[/green]" if ok else "[yellow]!![/yellow]"
        console.print(f"  {mark} {label}: {value}")

    try:
        import httpx

        ps = httpx.get("http://127.0.0.1:11434/api/ps", timeout=3).json()
        loaded = [m["name"] for m in ps.get("models", [])]
        line("Ollama", f"responde; modelos cargados: {loaded or 'ninguno'}")
    except Exception as exc:  # noqa: BLE001
        line("Ollama", f"NO responde ({type(exc).__name__})", ok=False)

    smi = shutil.which("nvidia-smi")
    if smi:
        out = subprocess.run(
            [smi, "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        line("GPU / VRAM", out or "sin salida")
    else:
        line("GPU / VRAM", "nvidia-smi no encontrado", ok=False)

    for tool in ("parec", "paplay", "pactl"):
        line(
            tool,
            shutil.which(tool) or "NO instalado (sudo apt install pulseaudio-utils)",
            ok=bool(shutil.which(tool)),
        )
    if shutil.which("pactl"):
        out = subprocess.run(
            ["pactl", "list", "short", "sources"], capture_output=True, text=True
        ).stdout
        has_src = "RDPSource" in out
        line(
            "fuente de micrófono WSLg (RDPSource)",
            "presente" if has_src else "AUSENTE",
            ok=has_src,
        )
    console.print()


@click.command("test")
@click.option(
    "--suite",
    type=click.Choice(sorted(SUITES)),
    default="full",
    show_default=True,
    help="quick = voz sin modelos reales; voice = voz completa; "
    "full = voz + regresion.",
)
@click.option("--env", "show_env", is_flag=True, help="Diagnostico del entorno real.")
@click.option("--no-lint", is_flag=True, help="Omite el analisis estatico (ruff).")
@click.option("-k", "keyword", default=None, help="Filtro de pytest (-k).")
def selftest(suite: str, show_env: bool, no_lint: bool, keyword: str | None) -> None:
    """Valida JARVIS: analisis estatico + pruebas (codigo de salida 0 = todo OK)."""
    console = Console()
    root = _project_root()
    if root is None:
        console.print(
            "[red]`jarvis test` solo funciona desde un checkout del proyecto.[/red]"
        )
        sys.exit(2)

    results: dict[str, bool] = {}
    if show_env:
        _env_diagnostics(console)
    if not no_lint:
        results["ruff"] = _run(
            console,
            "Analisis estatico (ruff)",
            [sys.executable, "-m", "ruff", "check", *LINT_PATHS],
            root,
        )
    pytest_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        *SUITES[suite],
    ]
    if keyword:
        pytest_cmd += ["-k", keyword]
    results[f"pytest[{suite}]"] = _run(
        console, f"Pruebas: suite '{suite}'", pytest_cmd, root
    )

    console.print("[bold]Resumen[/bold]")
    for name, ok in results.items():
        console.print(
            f"  {'[green]OK   [/green]' if ok else '[red]FALLO[/red]'} {name}"
        )
    sys.exit(0 if all(results.values()) else 1)
