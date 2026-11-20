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
import select
import subprocess
import time
import wave
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000  # Hz -- lo que esperan webrtcvad y faster-whisper
CHANNELS = 1
SAMPLE_FORMAT = "s16le"  # PCM16 little-endian
BYTES_PER_SAMPLE = 2
_EPS = 1e-9

# Por debajo de esto un frame es "señal muerta" (ceros digitales): ningun
# microfono real baja de ~-75 dBFS de ruido propio. Medido en WSLg: los
# primeros ~0.4 s tras arrancar `parec` llegan como ceros (-98 dBFS) hasta
# que la fuente RDP despierta, y un micro silenciado en Windows tambien.
DEAD_DBFS = -90.0


class CaptureError(RuntimeError):
    """El microfono no entrega audio (parec ausente, muerto o bloqueado)."""


class PlaybackError(RuntimeError):
    """La reproduccion fallo (paplay ausente o termino con error)."""


def frame_bytes(frame_ms: int, sample_rate: int = SAMPLE_RATE) -> int:
    """Tamaño en bytes de un frame de `frame_ms` milisegundos."""
    return int(sample_rate * frame_ms / 1000) * BYTES_PER_SAMPLE * CHANNELS


# ---------------------------------------------------------------------------
# Niveles de señal
# ---------------------------------------------------------------------------


def frame_dbfs(frame: bytes) -> float:
    """Nivel RMS de un frame PCM16 en dBFS (0 dBFS = fondo de escala)."""
    x = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
    if x.size == 0:
        return -120.0
    return float(20 * np.log10(np.sqrt(np.mean(x * x)) + _EPS))


@dataclass
class AudioLevels:
    duration_seconds: float = 0.0
    peak_dbfs: float = -120.0
    rms_dbfs: float = -120.0
    clipped_fraction: float = 0.0


def audio_levels(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> AudioLevels:
    """Duracion, pico, RMS y fraccion saturada de un PCM16 mono."""
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if x.size == 0:
        return AudioLevels()
    return AudioLevels(
        duration_seconds=x.size / sample_rate,
        peak_dbfs=float(20 * np.log10(np.abs(x).max() + _EPS)),
        rms_dbfs=float(20 * np.log10(np.sqrt(np.mean(x * x)) + _EPS)),
        clipped_fraction=float(np.mean(np.abs(x) >= 0.999)),
    )


# ---------------------------------------------------------------------------
# Captura
# ---------------------------------------------------------------------------


class MicStream:
    """Captura continua del microfono real como frames PCM16 de tamaño fijo.

    A diferencia de un generador suelto, separa "arrancar" de "iterar":
    `start()` espera al PRIMER frame real y devuelve cuanto tardo parec en
    entregar audio (medido: ~250 ms en WSLg) -- asi se puede avisar al
    usuario "habla ahora" solo cuando el micro ya esta escuchando, y se
    detectan fallos de captura (parec ausente, muerto o sin audio) como
    `CaptureError` explicito en vez de un bucle silencioso.
    """

    def __init__(
        self,
        frame_ms: int = 30,
        sample_rate: int = SAMPLE_RATE,
        device: str = "RDPSource",
        stall_timeout_s: float = 2.0,
        warmup_timeout_s: float = 1.5,
        latency_ms: int = 60,
    ) -> None:
        self.frame_ms = frame_ms
        self.sample_rate = sample_rate
        self.device = device
        self.latency_ms = latency_ms
        self.stall_timeout_s = stall_timeout_s
        self.warmup_timeout_s = warmup_timeout_s
        self.startup_seconds = 0.0
        self.dead_frames_skipped = 0  # ceros de arranque descartados
        self.signal_dead_at_start = False  # nunca llego señal viva (¿micro mudo?)
        self._chunk = frame_bytes(frame_ms, sample_rate)
        self._proc: subprocess.Popen | None = None
        self._first: bytes | None = None

    def _read_exact(self, n: int, timeout_s: float) -> bytes:
        assert self._proc is not None and self._proc.stdout is not None
        fd = self._proc.stdout
        buf = bytearray()
        while len(buf) < n:
            ready, _, _ = select.select([fd], [], [], timeout_s)
            if not ready:
                raise CaptureError(
                    f"parec no entrego audio durante {timeout_s:.1f}s "
                    f"(fuente {self.device}: ¿microfono desactivado en Windows?)"
                )
            data = fd.read(n - len(buf))
            if not data:
                raise CaptureError(self._exit_message())
            buf.extend(data)
        return bytes(buf)

    def _exit_message(self) -> str:
        assert self._proc is not None
        try:
            self._proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        err = b""
        if self._proc.stderr is not None:
            try:
                err = self._proc.stderr.read() or b""
            except Exception:  # noqa: BLE001
                err = b""
        text = err.decode("utf-8", "replace").strip()[:200]
        return f"parec termino inesperadamente (codigo {self._proc.returncode})" + (
            f": {text}" if text else ""
        )

    def start(self, ready_timeout_s: float = 3.0) -> float:
        t0 = time.monotonic()
        try:
            self._proc = subprocess.Popen(
                [
                    "parec",
                    f"--device={self.device}",
                    f"--rate={self.sample_rate}",
                    f"--channels={CHANNELS}",
                    f"--format={SAMPLE_FORMAT}",
                    # Sin esto PulseAudio elige la latencia de captura: en
                    # fuentes tipo monitor llegan bloques de ~2 s (stall
                    # intermitente medido). Pedimos bloques pequeños.
                    f"--latency-msec={self.latency_ms}",
                    "--raw",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError as exc:
            raise CaptureError(
                "parec no esta instalado (sudo apt install pulseaudio-utils)"
            ) from exc
        try:
            frame = self._read_exact(self._chunk, ready_timeout_s)
            # Descarta los ceros de arranque: "listo" = primera señal viva.
            warm_deadline = time.monotonic() + self.warmup_timeout_s
            while frame_dbfs(frame) < DEAD_DBFS and time.monotonic() < warm_deadline:
                self.dead_frames_skipped += 1
                frame = self._read_exact(self._chunk, self.stall_timeout_s)
        except CaptureError:
            self.close()
            raise
        self.signal_dead_at_start = frame_dbfs(frame) < DEAD_DBFS
        self._first = frame
        self.startup_seconds = time.monotonic() - t0
        return self.startup_seconds

    def frames(self) -> Iterator[bytes]:
        if self._proc is None:
            self.start()
        if self._first is not None:
            first, self._first = self._first, None
            yield first
        while True:
            yield self._read_exact(self._chunk, self.stall_timeout_s)

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        proc.kill()
        proc.wait()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    def __enter__(self) -> MicStream:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def record_frames(
    frame_ms: int = 30,
    sample_rate: int = SAMPLE_RATE,
    device: str = "RDPSource",
) -> Iterator[bytes]:
    """Generador simple sobre `MicStream` (se cierra al dejar de iterar)."""
    with MicStream(frame_ms, sample_rate, device) as mic:
        yield from mic.frames()


# ---------------------------------------------------------------------------
# WAV / reproduccion
# ---------------------------------------------------------------------------


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
    try:
        subprocess.run(
            ["paplay", f"--device={device}", "--file-format=wav", "/dev/stdin"],
            input=wav_bytes,
            check=True,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise PlaybackError("paplay no esta instalado") from exc
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", "replace").strip()[:200]
        raise PlaybackError(f"paplay fallo (codigo {exc.returncode}): {err}") from exc


def play_pcm_stream(
    chunks: Iterator[bytes],
    sample_rate: int,
    device: str = "RDPSink",
) -> None:
    """Reproduce PCM16LE mono crudo segun va llegando (streaming real).

    A diferencia de `play_wav_bytes` (que necesita el audio completo ya
    generado), esto alimenta `paplay` por su stdin trozo a trozo: mientras
    el proceso que llama sigue generando el siguiente trozo (p.ej. Kokoro
    sintetizando la frase siguiente), `paplay` ya esta reproduciendo el
    anterior -- la sintesis del resto de la respuesta se solapa con la
    reproduccion en vez de esperar a que todo termine (T2.4b).

    Lanza `PlaybackError` si el reproductor no existe o muere a mitad.
    """
    try:
        proc = subprocess.Popen(
            [
                "paplay",
                f"--device={device}",
                "--raw",
                f"--rate={sample_rate}",
                f"--channels={CHANNELS}",
                f"--format={SAMPLE_FORMAT}",
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise PlaybackError("paplay no esta instalado") from exc
    assert proc.stdin is not None
    broken = False
    try:
        for chunk in chunks:
            try:
                proc.stdin.write(chunk)
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                broken = True
                break
    finally:
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            broken = True
        proc.wait()
    if broken or proc.returncode not in (0, None):
        err = b""
        if proc.stderr is not None:
            err = proc.stderr.read() or b""
        text = err.decode("utf-8", "replace").strip()[:200]
        raise PlaybackError(
            f"paplay termino con error (codigo {proc.returncode})"
            + (f": {text}" if text else "")
        )
