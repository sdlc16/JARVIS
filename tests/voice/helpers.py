"""Utilidades para las pruebas de voz: flujos de audio controlados.

Todo el audio de voz proviene de grabaciones SINTETICAS versionadas
(``fixtures/*.wav``, ver ``fixtures/generate_fixtures.py``); el ruido es
gaussiano con semilla fija al nivel medido en este equipo (-62 dBFS). Es
decir: son grabaciones controladas, NO el microfono real -- la validacion
con micro real se documenta aparte (JARVIS_PROGRESS.md).
"""

from __future__ import annotations

import pathlib
import wave
from collections.abc import Iterator

import numpy as np

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
SR = 16000
FRAME_MS = 30
FRAME = SR * FRAME_MS // 1000
REAL_NOISE_DBFS = -62.0  # nivel de ruido ambiente medido en este equipo


def load_clip(name: str, trim_silence: bool = True) -> np.ndarray:
    with wave.open(str(FIX / f"{name}.wav"), "rb") as wf:
        assert wf.getframerate() == SR and wf.getnchannels() == 1
        x = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    x = x.astype(np.float32) / 32768.0
    if trim_silence:
        thr = np.abs(x).max() * 10 ** (-35 / 20)
        idx = np.where(np.abs(x) > thr)[0]
        pad = int(0.05 * SR)
        x = x[max(0, idx[0] - pad) : idx[-1] + pad]
    return x


def rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x**2)) + 1e-9))


def at_level(x: np.ndarray, target_db: float) -> np.ndarray:
    """Escala la voz para que su RMS ACTIVO sea `target_db` dBFS."""
    active = x[np.abs(x) > 0.01 * np.abs(x).max()]
    return x * 10 ** ((target_db - rms_db(active)) / 20)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def noise(
    seconds: float, level_db: float = REAL_NOISE_DBFS, seed: int = 7
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(seconds * SR)).astype(np.float32)
    return x * 10 ** (level_db / 20) / x.std()


def build_stream(
    parts: list[np.ndarray], noise_db: float | None = REAL_NOISE_DBFS, seed: int = 7
) -> np.ndarray:
    sig = np.concatenate(parts)
    if noise_db is not None:
        sig = sig + noise(len(sig) / SR, noise_db, seed)
    return sig


def speech_turn(
    clips: list[str],
    level_db: float = -30.0,
    pause_s: float = 0.4,
    lead_s: float = 1.0,
    tail_s: float = 3.0,
    noise_db: float | None = REAL_NOISE_DBFS,
    seed: int = 7,
) -> tuple[np.ndarray, float]:
    """Flujo: silencio + clips separados por pausas + silencio final.

    Devuelve (señal, duracion_del_habla_incluyendo_pausas_internas).
    """
    parts = [silence(lead_s)]
    span = 0.0
    for i, name in enumerate(clips):
        c = at_level(load_clip(name), level_db)
        parts.append(c)
        span += len(c) / SR
        if i < len(clips) - 1:
            parts.append(silence(pause_s))
            span += pause_s
    parts.append(silence(tail_s))
    return build_stream(parts, noise_db, seed), span


def to_pcm(sig: np.ndarray) -> bytes:
    return (np.clip(sig, -1, 1) * 32767).astype(np.int16).tobytes()


def frames_of(sig: np.ndarray) -> Iterator[bytes]:
    pcm = np.clip(sig, -1, 1)
    pcm = (pcm * 32767).astype(np.int16)
    for i in range(0, len(pcm) - FRAME + 1, FRAME):
        yield pcm[i : i + FRAME].tobytes()
