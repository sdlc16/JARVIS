"""Captura/reproduccion: errores de captura, ceros de arranque, reproduccion.

Se sustituye el binario (`parec`/`paplay`) por un subproceso Python REAL que
emite/consume bytes por tuberias, de modo que se ejercita el codigo real de
select/lectura/escritura -- lo que NO se prueba aqui es el hardware (eso se
hace con el micro real, ver JARVIS_PROGRESS.md).
"""

from __future__ import annotations

import subprocess
import sys
import wave
from io import BytesIO

import numpy as np
import pytest

from openjarvis.voice import audio_io
from openjarvis.voice.audio_io import (
    CaptureError,
    MicStream,
    PlaybackError,
    audio_levels,
    frame_bytes,
    frame_dbfs,
    pcm_to_wav_bytes,
)

FB = frame_bytes(30)  # 960 bytes


def _patch_popen(monkeypatch, script: str):
    real = subprocess.Popen

    def fake(args, **kw):
        return real([sys.executable, "-c", script], **kw)

    monkeypatch.setattr(audio_io.subprocess, "Popen", fake)


def _frames_script(
    zero_frames: int,
    live_frames: int,
    then_sleep: float = 0.0,
    frame_delay: float = 0.0,
) -> str:
    return (
        "import sys,time,random,struct\n"
        f"fb={FB}\n"
        "z=bytes(fb)\n"
        "n=fb//2\n"
        "live=struct.pack('<'+'h'*n,*[random.randint(-300,300) for _ in range(n)])\n"
        "def emit(b):\n"
        "    sys.stdout.buffer.write(b); sys.stdout.buffer.flush()\n"
        f"    time.sleep({frame_delay})\n"
        f"for _ in range({zero_frames}): emit(z)\n"
        f"for _ in range({live_frames}): emit(live)\n"
        f"time.sleep({then_sleep})\n"
    )


def test_levels_of_known_signal():
    x = (np.sin(np.linspace(0, 200 * np.pi, 16000)) * 0.5 * 32767).astype(np.int16)
    lv = audio_levels(x.tobytes())
    assert lv.duration_seconds == pytest.approx(1.0)
    assert lv.peak_dbfs == pytest.approx(-6.0, abs=0.2)
    assert lv.rms_dbfs == pytest.approx(-9.0, abs=0.3)
    assert lv.clipped_fraction == 0.0


def test_levels_detect_clipping_and_empty():
    clipped = np.full(1000, 32767, dtype=np.int16)
    assert audio_levels(clipped.tobytes()).clipped_fraction == 1.0
    assert audio_levels(b"").duration_seconds == 0.0
    assert frame_dbfs(bytes(FB)) < audio_io.DEAD_DBFS


def test_wav_roundtrip_is_valid_for_whisper():
    pcm = (np.arange(1600) % 100).astype(np.int16).tobytes()
    wav = pcm_to_wav_bytes(pcm)
    with wave.open(BytesIO(wav), "rb") as wf:
        assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (
            1,
            2,
            16000,
        )
        assert wf.readframes(wf.getnframes()) == pcm


def test_missing_parec_is_a_capture_error(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("parec")

    monkeypatch.setattr(audio_io.subprocess, "Popen", boom)
    with pytest.raises(CaptureError, match="parec"):
        MicStream().start()


def test_dead_startup_frames_are_skipped_until_live_signal(monkeypatch):
    _patch_popen(
        monkeypatch, _frames_script(zero_frames=12, live_frames=20, then_sleep=2)
    )
    with MicStream() as mic:
        assert mic.dead_frames_skipped == 12
        assert not mic.signal_dead_at_start
        first = next(mic.frames())
        assert frame_dbfs(first) > audio_io.DEAD_DBFS
        assert mic.startup_seconds > 0


def test_permanently_dead_signal_is_flagged_but_keeps_streaming(monkeypatch):
    # ceros a ritmo de tiempo real (30 ms/frame): el plazo de 0.3 s vence antes
    _patch_popen(
        monkeypatch,
        _frames_script(zero_frames=100, live_frames=0, then_sleep=2, frame_delay=0.03),
    )
    mic = MicStream(warmup_timeout_s=0.3)
    mic.start()
    try:
        assert mic.signal_dead_at_start
        assert len(next(mic.frames())) == FB
    finally:
        mic.close()


def test_stalled_capture_raises_instead_of_hanging(monkeypatch):
    _patch_popen(
        monkeypatch, _frames_script(zero_frames=0, live_frames=2, then_sleep=5)
    )
    mic = MicStream(stall_timeout_s=0.4)
    mic.start()
    try:
        with pytest.raises(CaptureError, match="no entrego audio"):
            for _ in mic.frames():
                pass
    finally:
        mic.close()


def test_capture_process_exit_is_reported_with_code(monkeypatch):
    _patch_popen(
        monkeypatch, "import sys; sys.stderr.write('Connection refused'); sys.exit(7)"
    )
    with pytest.raises(CaptureError, match="7"):
        MicStream().start()


def test_play_pcm_stream_delivers_all_chunks_in_order(monkeypatch, tmp_path):
    out = tmp_path / "played.raw"
    script = f"import sys\nopen({str(out)!r},'wb').write(sys.stdin.buffer.read())\n"
    _patch_popen(monkeypatch, script)
    chunks = [b"\x01\x00" * 100, b"\x02\x00" * 50, b"\x03\x00" * 10]
    audio_io.play_pcm_stream(iter(chunks), sample_rate=24000)
    assert out.read_bytes() == b"".join(chunks)


def test_player_that_dies_raises_playback_error(monkeypatch):
    _patch_popen(monkeypatch, "import sys; sys.stderr.write('no sink'); sys.exit(3)")
    big = (b"\x00\x00" * 200000 for _ in range(50))
    with pytest.raises(PlaybackError):
        audio_io.play_pcm_stream(big, sample_rate=24000)


def test_missing_player_raises_playback_error(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("paplay")

    monkeypatch.setattr(audio_io.subprocess, "Popen", boom)
    with pytest.raises(PlaybackError, match="paplay"):
        audio_io.play_pcm_stream(iter([b"\x00\x00"]), sample_rate=24000)
