"""``jarvis voice`` -- conversacion por voz real (T2.1-T2.4).

Escucha por el microfono real (WSLg), transcribe con faster-whisper,
responde con el agente configurado, y habla la respuesta con Kokoro.
El modo por texto (`jarvis ask` / `jarvis chat`) sigue disponible sin
cambios como alternativa -- ver DEVELOPMENT_TASKS.md T2.4.
"""

from __future__ import annotations

import logging
import sys
import time

import click
from rich.console import Console

from openjarvis.cli._banner import print_banner
from openjarvis.core.config import load_config
from openjarvis.engine import EngineConnectionError, get_engine

logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "-m", "--model", "model_name", default=None, help="Modelo a usar (Ollama)."
)
@click.option("-e", "--engine", "engine_key", default=None, help="Motor de inferencia.")
@click.option(
    "-a",
    "--agent",
    "agent_name",
    default=None,
    help="Agente a usar (vacio = directo al motor).",
)
@click.option(
    "--stt-model",
    "stt_model_size",
    default="base",
    help="Tamano del modelo faster-whisper (tiny/base/small/medium).",
)
@click.option(
    "--tts-voice",
    "tts_voice_id",
    default="",
    help="Voice ID de Kokoro (vacio = por defecto del idioma).",
)
@click.option(
    "--lang",
    "tts_lang_code",
    default="e",
    help="Codigo de idioma Kokoro para TTS (e=espanol, a=ingles).",
)
@click.option(
    "--turns",
    "num_turns",
    default=1,
    type=int,
    help="Numero de turnos de voz a ejecutar (0 = continuo hasta Ctrl+C).",
)
@click.option(
    "--vad-aggressiveness", default=3, type=int, help="Agresividad del VAD (0-3)."
)
def voice(
    model_name: str | None,
    engine_key: str | None,
    agent_name: str | None,
    stt_model_size: str,
    tts_voice_id: str,
    tts_lang_code: str,
    num_turns: int,
    vad_aggressiveness: int,
) -> None:
    """Conversa con Jarvis por voz (microfono y altavoces reales)."""
    print_banner(quiet=False)
    console = Console(stderr=True)

    config = load_config()

    effective_engine_key = engine_key or config.intelligence.preferred_engine or None
    resolved = get_engine(config, effective_engine_key)
    if resolved is None:
        console.print("[red bold]Ningun motor de inferencia disponible.[/red bold]")
        sys.exit(1)
    engine_name, engine = resolved

    if model_name is None:
        model_name = config.intelligence.default_model
    if not model_name:
        console.print("[red]No hay modelo configurado.[/red]")
        sys.exit(1)

    from openjarvis.voice.pipeline import VoicePipeline
    from openjarvis.voice.vad import VadConfig

    try:
        pipeline = VoicePipeline(
            engine=engine,
            model_name=model_name,
            config=config,
            agent_name=agent_name or "",
            stt_model_size=stt_model_size,
            tts_lang_code=tts_lang_code,
            tts_voice_id=tts_voice_id,
            vad_config=VadConfig(aggressiveness=vad_aggressiveness),
        )
    except Exception as exc:
        console.print(f"[red]No se pudo inicializar el pipeline de voz:[/red] {exc}")
        sys.exit(1)

    silence_ms = pipeline.vad_config.silence_timeout_ms
    console.print(
        f"[green]Jarvis escuchando[/green] (motor={engine_name}, modelo={model_name}, "
        f"STT=faster-whisper/{stt_model_size}, TTS=kokoro/{tts_lang_code}). "
        f"Habla cuando quieras — se corta tras ~{silence_ms}ms de silencio."
    )

    turn = 0
    try:
        while num_turns == 0 or turn < num_turns:
            turn += 1
            console.print(f"\n[dim]--- turno {turn} ---[/dim]")
            console.print("[yellow]Escuchando...[/yellow]")
            t_total = time.monotonic()
            try:
                result = pipeline.run_turn()
            except EngineConnectionError as exc:
                console.print(f"[red]Error de motor:[/red] {exc}")
                continue

            if not result.transcript.strip():
                console.print("[dim]No se entendio nada (transcripcion vacia).[/dim]")
                continue

            console.print(f"[cyan]Tu:[/cyan] {result.transcript}")
            console.print(f"[magenta]Jarvis:[/magenta] {result.response_text}")
            total = time.monotonic() - t_total
            lat = result.latency
            console.print(
                f"[dim]latencia: vad={lat.vad_seconds:.2f}s stt={lat.stt_seconds:.2f}s "
                f"agente={lat.agent_seconds:.2f}s tts+audio={lat.tts_seconds:.2f}s "
                f"total={total:.2f}s | idioma={result.language} "
                f"confianza={result.stt_confidence}[/dim]"
            )
    except KeyboardInterrupt:
        console.print("\n[dim]Interrumpido por el usuario.[/dim]")
