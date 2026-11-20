"""Captura y reproduccion de audio real, via el PulseAudio de WSLg.

T1.5 (ver JARVIS_PROGRESS.md) confirmo que WSL2 tiene acceso real al
microfono/altavoces de Windows a traves del servidor PulseAudio que WSLg
expone (`$PULSE_SERVER`, fuente `RDPSource`, sumidero `RDPSink`) -- no
hace falta pasar el audio por el puente nativo de Windows.

Se usa `parec`/`paplay` (paquete `pulseaudio-utils`, ya instalado) via
subprocess en vez de un binding Python (sounddevice/pyaudio) porque
hablan directamente con el socket de WSLg que ya se valido funcionando,
sin añadir una capa de PortAudio que no se ha probado en este entorno.
"""

from __future__ import annotations

import io
import subprocess
import wave
from collections.abc import Iterator

SAMPLE_RATE = 16000  # Hz -- lo que esperan webrtcvad y faster-whisper
CHANNELS = 1
SAMPLE_FORMAT = "s16le"  # PCM16 little-endian
BYTES_PER_SAMPLE = 2


def frame_bytes(frame_ms: int, sample_rate: int = SAMPLE_RATE) -> int:
    """Tamaño en bytes de un frame de `frame_ms` milisegundos."""
    return int(sample_rate * frame_ms / 1000) * BYTES_PER_SAMPLE * CHANNELS


def record_frames(
    frame_ms: int = 30,
    sample_rate: int = SAMPLE_RATE,
    device: str = "RDPSource",
) -> Iterator[bytes]:
    """Captura audio real del microfono en frames de tamaño fijo.

    Generador infinito (hasta que el consumidor deja de iterar, momento
    en el que se mata el proceso `parec`). Cada frame es PCM16LE mono.
    """
    chunk = frame_bytes(frame_ms, sample_rate)
    proc = subprocess.Popen(
        [
            "parec",
            f"--device={device}",
            f"--rate={sample_rate}",
            f"--channels={CHANNELS}",
            f"--format={SAMPLE_FORMAT}",
            "--raw",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    try:
        while True:
            data = proc.stdout.read(chunk)
            if len(data) < chunk:
                break
            yield data
    finally:
        proc.kill()
        proc.wait()


def pcm_to_wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Envuelve PCM16LE crudo (sin cabecera) en un contenedor WAV valido.

    Necesario porque los backends STT (faster-whisper via PyAV) esperan
    un archivo con cabecera real, no PCM crudo.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(BYTES_PER_SAMPLE)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def play_wav_bytes(wav_bytes: bytes, device: str = "RDPSink") -> None:
    """Reproduce audio WAV real por los altavoces (bloqueante)."""
    subprocess.run(
        ["paplay", f"--device={device}", "--file-format=wav", "/dev/stdin"],
        input=wav_bytes,
        check=True,
        stderr=subprocess.DEVNULL,
    )
