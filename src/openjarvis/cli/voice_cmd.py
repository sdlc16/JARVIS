"""``jarvis voice`` -- conversacion por voz real (T2.1-T2.4).

Escucha por el microfono real (WSLg), transcribe con faster-whisper,
responde con el agente configurado, y habla la respuesta con Kokoro.
El modo por texto (`jarvis ask` / `jarvis chat`) sigue disponible sin
cambios como alternativa -- ver DEVELOPMENT_TASKS.md T2.4.

Los parametros del VAD/turno viven en ``[voice]`` de config.toml
(``jarvis config set voice.silence_timeout_ms 1800``); cada flag de esta
orden los sobrescribe solo para esa ejecucion.
"""

from __future__ import annotations

import logging
import sys
import threading
import time

import click
from rich.console import Console

from openjarvis.cli._banner import print_banner
from openjarvis.core.config import load_config
from openjarvis.engine import get_engine
from openjarvis.voice.pipeline import TurnOutcome, VoiceTurnResult

logger = logging.getLogger(__name__)

_OUTCOME_LABEL = {
    TurnOutcome.NO_SPEECH: ("yellow", "No se detecto voz."),
    TurnOutcome.EMPTY_TRANSCRIPT: (
        "yellow",
        "No se entendio nada (transcripcion vacia).",
    ),
    TurnOutcome.CAPTURE_ERROR: ("red", "Fallo de captura del microfono."),
    TurnOutcome.STT_ERROR: ("red", "Fallo del reconocimiento de voz."),
    TurnOutcome.AGENT_ERROR: ("red", "El agente/Ollama no respondio."),
    TurnOutcome.TTS_ERROR: ("yellow", "Fallo la voz de salida (solo texto)."),
}


def _print_result(console: Console, result: VoiceTurnResult) -> None:
    if result.outcome in _OUTCOME_LABEL:
        color, label = _OUTCOME_LABEL[result.outcome]
        console.print(f"[{color}]{label}[/{color}]")
        if result.probable_cause:
            console.print(f"[dim]  causa probable: {result.probable_cause}[/dim]")
        if result.hint:
            console.print(f"[dim]  sugerencia: {result.hint}[/dim]")
        if result.error and result.outcome != TurnOutcome.EMPTY_TRANSCRIPT:
            console.print(f"[dim]  detalle tecnico: {result.error}[/dim]")

    if result.transcript:
        console.print(f"[cyan]Tu:[/cyan] {result.transcript}")
    if result.response_text:
        console.print(f"[magenta]Jarvis:[/magenta] {result.response_text}")

    lat, vad, audio = result.latency, result.vad, result.audio
    if vad is not None:
        console.print(
            f"[dim]escucha[{vad.end_reason.value}]: "
            f"reaccion={lat.vad_wait_for_speech_seconds:.2f}s "
            f"habla={lat.vad_speech_seconds:.2f}s "
            f"silencio_final={lat.vad_trailing_silence_seconds:.2f}s "
            f"pausa_max={vad.longest_pause_seconds:.2f}s "
            f"micro_arranque={lat.capture_startup_seconds:.2f}s[/dim]"
        )
    if audio is not None:
        console.print(
            f"[dim]audio: {audio.duration_seconds:.2f}s pico={audio.peak_dbfs:.0f}dBFS "
            f"rms={audio.rms_dbfs:.0f}dBFS ruido={vad.noise_floor_dbfs:.0f}dBFS "
            f"voz={100 * vad.voiced_ratio:.0f}%[/dim]"
        )
    if result.audio_played:
        gaps = lat.tts_detail.get("gap_seconds", 0.0)
        console.print(
            f"[dim]procesamiento: stt={lat.stt_seconds:.2f}s "
            f"agente={lat.agent_seconds:.2f}s "
            f"tts[{lat.tts_mode}]=1er_audio={lat.tts_time_to_first_audio_seconds:.2f}s/"
            f"total={lat.tts_total_seconds:.2f}s huecos_entre_frases={gaps:.2f}s "
            f"| idioma={result.language}[/dim]"
        )


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
    help="Turnos COMPLETADOS a ejecutar (0 = continuo hasta Ctrl+C). "
    "Los intentos fallidos se reintentan y no cuentan.",
)
@click.option(
    "--silence-ms",
    "silence_ms",
    default=None,
    type=int,
    help="Silencio final que cierra el turno (defecto: [voice] silence_timeout_ms).",
)
@click.option(
    "--vad-aggressiveness",
    default=None,
    type=int,
    help="Agresividad del VAD 0-3 (defecto: [voice] aggressiveness).",
)
@click.option(
    "--max-wait-s",
    "max_wait_s",
    default=None,
    type=float,
    help="Segundos maximos esperando a que hables, 0 = sin limite.",
)
@click.option(
    "--max-failures",
    "max_failures",
    default=None,
    type=int,
    help="Intentos fallidos seguidos antes de rendirse.",
)
@click.option(
    "--max-tokens",
    "max_tokens",
    default=None,
    type=int,
    help="Tope de tokens de la respuesta (por defecto: breve, pensado para voz).",
)
@click.option(
    "--debug-audio-dir",
    "debug_audio_dir",
    default=None,
    type=click.Path(file_okay=False),
    help="OPT-IN: guarda el WAV de cada turno (ultimos 10) para diagnostico. "
    "Es tu voz: borralo despues.",
)
def voice(
    model_name: str | None,
    engine_key: str | None,
    agent_name: str | None,
    stt_model_size: str,
    tts_voice_id: str,
    tts_lang_code: str,
    num_turns: int,
    silence_ms: int | None,
    vad_aggressiveness: int | None,
    max_wait_s: float | None,
    max_failures: int | None,
    max_tokens: int | None,
    debug_audio_dir: str | None,
) -> None:
    """Conversa con Jarvis por voz (microfono y altavoces reales)."""
    print_banner(quiet=False)
    console = Console(stderr=True)

    config = load_config()
    vc = config.voice

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

    from openjarvis.voice.session import run_session
    from openjarvis.voice.vad import VadConfig

    aggr = vad_aggressiveness if vad_aggressiveness is not None else vc.aggressiveness
    silence = silence_ms if silence_ms is not None else vc.silence_timeout_ms
    no_speech_ms = (
        int(max_wait_s * 1000) if max_wait_s is not None else vc.no_speech_timeout_ms
    )
    vad_config = VadConfig(
        aggressiveness=aggr,
        silence_timeout_ms=silence,
        max_utterance_ms=vc.max_utterance_ms,
        no_speech_timeout_ms=no_speech_ms,
        min_speech_ms=vc.min_speech_ms,
        preroll_ms=vc.preroll_ms,
        tail_ms=vc.tail_ms,
        energy_gate=vc.energy_gate,
        gate_margin_db=vc.gate_margin_db,
    )
    effective_max_tokens = max_tokens if max_tokens is not None else vc.max_tokens
    effective_failures = (
        max_failures if max_failures is not None else vc.max_consecutive_failures
    )
    debug_dir = debug_audio_dir or vc.debug_audio_dir or None

    from openjarvis.jarvis.confirmation import ConfirmationBroker
    from openjarvis.jarvis.runtime import JarvisRuntime
    from openjarvis.voice.pipeline import VoicePipeline

    console.print(
        "[dim]Cargando modelos (voz + Ollama) una sola vez por sesion...[/dim]"
    )
    t0 = time.monotonic()
    try:
        broker = ConfirmationBroker(timeout_s=vc.confirm_timeout_s)
        runtime = JarvisRuntime(
            config=config,
            engine=engine,
            model=model_name,
            broker=broker,
            agent_name=agent_name or "operative",
        )
    except Exception as exc:
        console.print(f"[red]No se pudo iniciar el cerebro de JARVIS:[/red] {exc}")
        sys.exit(1)

    # Ollama se precarga EN PARALELO con la carga de Whisper/Kokoro (CPU).
    preload_s: dict[str, float | str] = {}

    def _preload() -> None:
        try:
            preload_s["s"] = runtime.preload()
        except Exception as exc:  # noqa: BLE001
            preload_s["err"] = f"{type(exc).__name__}: {exc}"

    th = threading.Thread(target=_preload, daemon=True)
    th.start()

    holder: dict[str, VoicePipeline] = {}

    def agent_fn(text: str, _temperature: float, max_tokens: int) -> str:
        pipe = holder["p"]
        channels = [pipe.confirm_channel] if vc.confirm_by_voice else None
        reply = runtime.respond(
            text,
            source="voice",
            voice=True,
            max_tokens=max_tokens,
            confirm_channels=channels,
        )
        if reply.error:
            raise RuntimeError(reply.error)
        return reply.text

    try:
        pipeline = VoicePipeline(
            config=config,
            agent_fn=agent_fn,
            stt_model_size=stt_model_size,
            tts_lang_code=tts_lang_code,
            tts_voice_id=tts_voice_id,
            vad_config=vad_config,
            debug_audio_dir=debug_dir,
            stt_prompt=vc.stt_prompt,
            style_hint="",
        )
    except Exception as exc:
        console.print(f"[red]No se pudo inicializar el pipeline de voz:[/red] {exc}")
        sys.exit(1)
    holder["p"] = pipeline
    th.join(timeout=120)
    warm_s = time.monotonic() - t0
    wu = pipeline.warm_up_seconds
    ollama_txt = (
        f"Ollama={preload_s['s']:.1f}s"
        if "s" in preload_s
        else f"Ollama NO precargado ({preload_s.get('err', 'sin respuesta')})"
    )
    console.print(
        f"[dim]Listo en {warm_s:.2f}s "
        f"(STT={wu.get('stt_load_s', 0):.2f}s, TTS={wu.get('tts_load_s', 0):.2f}s, "
        f"{ollama_txt}, en paralelo). "
        f"Esto no se vuelve a pagar en los turnos siguientes de esta sesion.[/dim]"
    )
    wait_txt = (
        f"{vad_config.no_speech_timeout_ms / 1000:.0f}s"
        if vad_config.no_speech_timeout_ms
        else "sin limite"
    )
    console.print(
        f"[green]Jarvis listo[/green] (sesion {pipeline.run_id}, motor={engine_name}, "
        f"modelo={model_name}, STT=faster-whisper/{stt_model_size}, "
        f"TTS=kokoro/{tts_lang_code}, max_tokens={effective_max_tokens}). "
        f"Se cierra el turno tras {vad_config.silence_timeout_ms / 1000:.1f}s de "
        f"silencio; espera maxima a que hables: {wait_txt}."
    )
    if debug_dir:
        console.print(
            f"[yellow]Guardando tus audios en {debug_dir} (ultimos 10). "
            "Es tu voz: borralos cuando termines de diagnosticar.[/yellow]"
        )

    def on_attempt(attempt: int, done: int) -> None:
        console.print(
            f"\n[dim]--- intento {attempt} (turnos completados: {done}) ---[/dim]"
        )
        console.print("[dim]Preparando microfono...[/dim]")

    def on_ready() -> None:
        console.print("[yellow bold]Habla ahora...[/yellow bold]")

    summary = run_session(
        pipeline,
        num_turns=num_turns,
        max_consecutive_failures=effective_failures,
        max_tokens=effective_max_tokens,
        on_attempt=on_attempt,
        on_ready=on_ready,
        on_result=lambda r: _print_result(console, r),
    )

    fails = ", ".join(f"{k}={v}" for k, v in summary.failures.items()) or "ninguno"
    console.print(
        f"\n[dim]sesion {summary.run_id}: {summary.turns_ok} turno(s) completado(s) "
        f"en {summary.attempts} intento(s); fallos: {fails}; "
        f"fin: {summary.stopped_reason}. "
        "Registro: ~/.openjarvis/logs/voice.jsonl[/dim]"
    )
    if summary.stopped_reason == "too_many_failures":
        console.print(
            f"[red]Me rindo tras {effective_failures} intentos fallidos "
            "seguidos.[/red] Revisa las causas probables de arriba."
        )
        sys.exit(1)
    if summary.stopped_reason == "interrupted":
        console.print("[dim]Interrumpido por el usuario.[/dim]")
