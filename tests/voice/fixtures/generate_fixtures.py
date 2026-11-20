"""Genera las grabaciones de prueba controladas del sistema de voz.

Voz SINTETICA (Kokoro, voz em_alex) -- no es la voz de ninguna persona ni
una grabacion ambiental, asi que se puede versionar. Se guardan a 16 kHz
mono PCM16 (el formato exacto que produce `parec` y espera webrtcvad /
faster-whisper). Regenerar con::

    uv run python tests/voice/fixtures/generate_fixtures.py

Requiere el extra ``speech`` (kokoro). Los tests NO necesitan kokoro: solo
leen los WAV ya generados.
"""

from __future__ import annotations

import pathlib
import wave

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
TARGET_SR = 16000
KOKORO_SR = 24000

CLIPS = {
    "hola_jarvis": "Hola, Jarvis.",
    "como_estas": "¿Cómo estás?",
    "explica": "Quiero que me expliques brevemente qué puedes hacer por mí",
    "controlar_pc": "y cómo puedes ayudarme a controlar mi computadora.",
    # La frase completa del usuario en UNA sola sintesis (prosodia continua).
    "frase_larga": (
        "Hola, Jarvis. ¿Cómo estás? Quiero que me expliques brevemente qué "
        "puedes hacer por mí y cómo puedes ayudarme a controlar mi computadora."
    ),
}


def resample_24k_to_16k(x: np.ndarray) -> np.ndarray:
    """Polifasico 2/3 con FIR de ventana Kaiser (sin depender de scipy)."""
    up, down = 2, 3
    n_taps = 161
    cutoff = 0.9 * (TARGET_SR / 2) / (KOKORO_SR * up)  # fraccion de fs_up
    t = np.arange(n_taps) - (n_taps - 1) / 2
    h = np.sinc(2 * cutoff * t) * np.kaiser(n_taps, 8.0)
    h = h / h.sum() * up
    upsampled = np.zeros(len(x) * up, dtype=np.float64)
    upsampled[::up] = x
    filtered = np.convolve(upsampled, h, mode="same")
    return filtered[::down].astype(np.float32)


def write_wav(path: pathlib.Path, samples: np.ndarray) -> None:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_SR)
        wf.writeframes(pcm16.tobytes())


def main() -> None:
    from kokoro import KPipeline

    pipe = KPipeline(lang_code="e")
    for name, text in CLIPS.items():
        chunks = [
            np.asarray(a, dtype=np.float32)
            for _, _, a in pipe(text, voice="em_alex", speed=1.0, split_pattern=None)
        ]
        audio = np.concatenate(chunks)
        # normaliza a pico -3 dBFS para que el nivel sea reproducible; los
        # tests escalan despues al nivel que quieran simular.
        audio = audio / (np.abs(audio).max() + 1e-9) * 10 ** (-3 / 20)
        audio16 = resample_24k_to_16k(audio)
        write_wav(HERE / f"{name}.wav", audio16)
        print(f"{name}: {len(audio16) / TARGET_SR:.2f}s")


if __name__ == "__main__":
    main()
