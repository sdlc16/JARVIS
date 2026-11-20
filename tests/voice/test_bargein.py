"""Barge-in: monitor de interrupcion, reproduccion cancelable y hand-off del micro.

* Monitor/pipeline: micro y reproductor SIMULADOS; el tiempo es de audio (frames).
* `play_pcm_stream`: usa `paplay` REAL contra un sumidero nulo de PulseAudio (se
  salta si no hay servidor de audio).
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time

import numpy as np
import pytest

from openjarvis.voice import audio_io
from openjarvis.voice.bargein import BargeInConfig, BargeInMonitor
from openjarvis.voice.vad import VadConfig
from tests.voice.helpers import (
    SR,
    at_level,
    build_stream,
    frames_of,
    load_clip,
    silence,
)
from tests.voice.test_pipeline import FakeMic, FakeSTT, FakeTTS, Script, make

NOISE_DB = -62.0


class RecordingMic(FakeMic):
    """FakeMic que cuenta que frames se consumieron y no se cierra solo."""

    def __init__(self, sig, pace=0.0, **kw):
        super().__init__(sig, **kw)
        self.closed = False
        self.pace = pace

    def frames(self):
        for f in super().frames():
            if self.pace:
                time.sleep(self.pace)
            yield f

    def close(self):
        self.closed = True

    def __exit__(self, *exc):
        self.closed = True
        return False


def voice(level_db=-24.0):
    return at_level(load_clip("hola_jarvis"), level_db)


def run_monitor(sig, cfg=None):
    mic = RecordingMic(sig)
    mon = BargeInMonitor(lambda: mic, NOISE_DB, cfg)
    mon.start(playing=True)
    mon._thread.join(timeout=5)  # el micro simulado se consume al instante
    return mon, mic


def echo(seconds, level_db):
    """Eco constante de los altavoces (ruido de banda ancha a `level_db`)."""
    rng = np.random.default_rng(3)
    x = rng.normal(0, 1, int(seconds * SR)).astype(np.float32)
    return at_level(x, level_db)


# --- monitor ---------------------------------------------------------------------


def test_sustained_voice_over_ambient_noise_triggers():
    mon, _ = run_monitor(build_stream([silence(1.0), voice(), silence(1.0)]))
    assert mon.triggered.is_set()
    assert mon.trigger_at_ms is not None and mon.trigger_db > NOISE_DB + 12


def test_silence_and_ambient_noise_never_trigger():
    mon, _ = run_monitor(build_stream([silence(6.0)]))
    assert not mon.triggered.is_set()


def test_voice_during_the_ignore_window_does_not_trigger():
    # habla desde t=0 pero SOLO durante 390 ms: dentro de ignore_ms (400)
    clip = voice()[: int(0.39 * SR)]
    mon, _ = run_monitor(build_stream([clip, silence(2.0)]))
    assert not mon.triggered.is_set()


def test_a_short_noise_burst_shorter_than_sustain_does_not_trigger():
    blip = voice()[: int(0.15 * SR)]  # 150 ms < sustain 300 ms
    mon, _ = run_monitor(build_stream([silence(1.0), blip, silence(1.5)]))
    assert not mon.triggered.is_set()


def test_loud_echo_of_the_speakers_is_not_mistaken_for_the_user():
    # los altavoces se oyen a -30 dBFS desde el principio: eso es eco, no usuario
    mon, _ = run_monitor(build_stream([echo(4.0, -30.0)], noise_db=None))
    assert not mon.triggered.is_set()
    assert mon.echo_db > -40  # se midio el eco


def test_user_voice_clearly_above_the_echo_triggers():
    sig = build_stream(
        [echo(1.0, -40.0), voice(-18.0) + echo(2.0, -40.0)[: len(voice())]],
        noise_db=None,
    )
    mon, _ = run_monitor(sig)
    assert mon.triggered.is_set()
    assert mon.trigger_db > mon.echo_db + 6


def test_a_long_user_phrase_does_not_raise_the_echo_reference_before_triggering():
    # Fallo medido con PulseAudio real: los frames del usuario entraban en la
    # referencia de eco y subian el umbral por encima de su propia voz.
    long_voice = np.concatenate([voice(-16.0), silence(0.1), voice(-16.0)])
    sig = build_stream(
        [echo(2.0, -42.0), long_voice + echo(3.0, -42.0)[: len(long_voice)]],
        noise_db=None,
    )
    mon, _ = run_monitor(sig)
    assert mon.triggered.is_set() and mon.trigger_at_ms < 3000


def test_user_onset_with_a_hiccup_does_not_poison_the_echo_reference():
    # Con eco corto en historial, 4 frames de voz al inicio (antes de una pequeña
    # caida) no pueden entrar en la referencia o el umbral sube por encima del
    # propio usuario y nunca se dispara (fallo medido con PulseAudio real).
    v = voice(-16.0)
    head, tail = v[: 4 * 480], v[4 * 480 : 4 * 480 + int(1.5 * SR)]
    sig = build_stream(
        [echo(0.8, -42.0), head, echo(0.09, -42.0), tail, silence(0.5)], noise_db=None
    )
    mon, _ = run_monitor(sig)
    assert mon.triggered.is_set()


def test_stricter_margin_blocks_a_quiet_voice():
    sig = build_stream(
        [silence(1.0), at_level(load_clip("hola_jarvis"), -40.0), silence(1)]
    )
    assert run_monitor(sig, BargeInConfig(margin_db=12.0))[0].triggered.is_set()
    assert not run_monitor(sig, BargeInConfig(margin_db=30.0))[0].triggered.is_set()


class AnchoredMic(RecordingMic):
    """Avisa de `playback_started` al llegar al frame `anchor` (como el pipeline)."""

    def __init__(self, sig, anchor, **kw):
        super().__init__(sig, **kw)
        self.anchor, self.mon = anchor, None

    def frames(self):
        for i, f in enumerate(super().frames()):
            if i == self.anchor and self.mon is not None:
                self.mon.playback_started()
            yield f


def run_anchored(sig, anchor_s, cfg=None):
    mic = AnchoredMic(sig, anchor=int(anchor_s * 1000 / 30))
    mon = BargeInMonitor(lambda: mic, NOISE_DB, cfg)
    mic.mon = mon
    mon.start()
    mon._thread.join(timeout=5)
    return mon


def test_nothing_triggers_before_jarvis_starts_playing():
    mic = RecordingMic(build_stream([silence(1.0), voice(), silence(1.0)]))
    mon = BargeInMonitor(lambda: mic, NOISE_DB)
    mon.start()  # sin playback_started(): JARVIS aun sintetizando
    mon._thread.join(timeout=5)
    assert not mon.triggered.is_set()


def test_echo_is_measured_from_when_playback_really_starts():
    # 1.5 s de silencio mientras Kokoro sintetiza, luego el eco a -30 dBFS.
    # Medir el eco desde el inicio del monitor lo dejaba en -180 y el propio
    # JARVIS se interrumpia (fallo medido con PulseAudio real).
    sig = build_stream([silence(1.5), echo(4.0, -30.0)], noise_db=None)
    mon = run_anchored(sig, anchor_s=1.5)
    assert not mon.triggered.is_set() and mon.echo_db > -40


def test_late_arriving_audio_is_not_taken_for_the_user():
    # paplay tarda ~1 s en emitir: tras playback_started llegan ceros digitales
    # y despues el eco de JARVIS a -25 dBFS. No es el usuario (fallo medido).
    dead = np.zeros(int(1.5 * SR), dtype=np.float32)
    sig = np.concatenate([silence(0.5), dead, echo(4.0, -25.0)])
    mon = run_anchored(sig, anchor_s=0.5)
    assert not mon.triggered.is_set() and mon.echo_db > -35


def test_voice_after_the_echo_window_triggers_when_anchored():
    sig = build_stream(
        [silence(1.5), echo(1.0, -40.0), voice(-16.0), silence(1.0)], noise_db=None
    )
    mon = run_anchored(sig, anchor_s=1.5)
    assert mon.triggered.is_set() and mon.trigger_at_ms > 550


def test_stop_without_trigger_closes_the_mic_and_with_trigger_hands_it_over():
    mon, mic = run_monitor(build_stream([silence(2.0)]))
    assert mon.stop() is None and mic.closed
    mon, mic = run_monitor(build_stream([silence(1.0), voice(), silence(1.0)]))
    hand = mon.stop()
    assert hand is not None and not mic.closed and len(hand.prefix) >= 10  # preroll


def test_microphone_failure_is_reported_but_never_raises():
    class Boom(RecordingMic):
        def frames(self):
            raise RuntimeError("parec murio")
            yield b""

    mic = Boom(silence(1.0))
    mon = BargeInMonitor(lambda: mic, NOISE_DB)
    mon.start()
    mon._thread.join(timeout=3)
    assert isinstance(mon.error, RuntimeError) and mon.stop() is None


# --- play_pcm_stream cancelable (paplay real) -------------------------------------


@pytest.mark.skipif(shutil.which("paplay") is None, reason="sin PulseAudio")
def test_real_playback_is_cut_immediately_when_cancelled():
    ok = subprocess.run(["pactl", "info"], capture_output=True).returncode == 0
    if not ok:
        pytest.skip("servidor de audio no disponible")
    sink = subprocess.run(
        ["pactl", "load-module", "module-null-sink", "sink_name=jarvis_bi_test"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        cancel = threading.Event()

        def chunks():  # 10 s de audio, soltado en trozos de 100 ms a ritmo real
            for _ in range(100):
                time.sleep(0.1)
                yield b"\x01\x00" * 2400

        threading.Timer(0.6, cancel.set).start()
        t0 = time.monotonic()
        interrupted = audio_io.play_pcm_stream(
            chunks(), 24000, device="jarvis_bi_test", cancel=cancel
        )
        elapsed = time.monotonic() - t0
        assert interrupted is True and elapsed < 1.5  # no esperó los 10 s
        t0 = time.monotonic()  # sin cancelar: sigue siendo reproduccion normal
        assert (
            audio_io.play_pcm_stream(
                iter([b"\x01\x00" * 2400] * 2), 24000, device="jarvis_bi_test"
            )
            is False
        )
    finally:
        subprocess.run(["pactl", "unload-module", sink], capture_output=True)


# --- pipeline: interrupcion de la respuesta y siguiente turno sin perder voz -------


def _speech_after(delay_s):
    return build_stream(
        [silence(delay_s), at_level(load_clip("como_estas"), -24.0), silence(2.0)]
    )


def interruptible_player(release_s=5.0):
    """Reproductor simulado: 'suena' hasta que cancel se activa o pasa release_s."""
    state = {"cancelled": False, "played": 0}

    def play(chunks, sample_rate, cancel=None):
        for chunk in chunks:
            state["played"] += 1
            if cancel is not None and cancel.wait(0.05):
                state["cancelled"] = True
                return True
        if cancel is not None and cancel.wait(release_s):
            state["cancelled"] = True
            return True
        return False

    return play, state


def test_user_barge_in_cuts_the_answer_and_the_next_turn_hears_the_whole_phrase():
    answer_mic = RecordingMic(_speech_after(1.0), pace=0.004)  # interrumpe a ~1 s
    next_mic = RecordingMic(silence(1.0))  # NO debe usarse: se hereda el micro vivo
    play, state = interruptible_player()
    stt = FakeSTT("hola jarvis", "como estas")
    first_turn = FakeMic(
        build_stream([silence(0.2), load_clip("hola_jarvis"), silence(1.5)])
    )
    p = make(
        Script(first_turn, answer_mic, next_mic),
        stt=stt,
        tts=FakeTTS(chunk_samples=(2400,) * 20),
        player=play,
        barge_in=BargeInConfig(),
        vad_config=VadConfig(no_speech_timeout_ms=3000),
    )
    r1 = p.run_turn()
    assert state["cancelled"] and r1.latency.tts_detail["barge_in"]["triggered"]
    assert p._pending_handoff is not None and not answer_mic.closed  # micro retenido
    r2 = p.run_turn()  # reutiliza el micro y el audio previo al disparo
    assert r2.transcript == "como estas" and r2.vad.speech_seconds > 0.5
    assert answer_mic.closed  # liberado al terminar la escucha
    assert p._pending_handoff is None


def test_answer_plays_to_the_end_without_interruption_and_releases_the_mic():
    mic = RecordingMic(build_stream([silence(3.0)]))
    done = {"v": False}

    def play(chunks, sample_rate, cancel=None):
        list(chunks)
        time.sleep(0.3)  # deja al monitor consumir el micro simulado
        done["v"] = True
        return False

    p = make(
        Script(
            FakeMic(
                build_stream([silence(0.2), load_clip("hola_jarvis"), silence(1.5)])
            ),
            mic,
        ),
        player=play,
        barge_in=BargeInConfig(),
    )
    r = p.run_turn()
    assert done["v"] and not r.latency.tts_detail["barge_in"]["triggered"]
    assert p._pending_handoff is None and mic.closed


def test_barge_in_is_off_by_default_and_never_touches_the_mic():
    answer_mic = RecordingMic(_speech_after(0.5))
    p = make(
        Script(
            FakeMic(
                build_stream([silence(0.2), load_clip("hola_jarvis"), silence(1.5)])
            ),
            answer_mic,
        )
    )
    r = p.run_turn()
    assert "barge_in" not in r.latency.tts_detail and not answer_mic.closed


def test_a_missing_microphone_never_prevents_the_answer_from_being_spoken():
    def capture_calls():
        if capture_calls.n:
            raise audio_io.CaptureError("sin micro")
        capture_calls.n += 1
        return FakeMic(
            build_stream([silence(0.2), load_clip("hola_jarvis"), silence(1.5)])
        )

    capture_calls.n = 0
    p = make(capture_calls, barge_in=BargeInConfig())
    r = p.run_turn()
    assert r.outcome.value == "ok" and r.audio_played


def test_close_releases_a_pending_handoff():
    p = make(Script(silence(0.1)))
    mic = RecordingMic(silence(0.1))
    from openjarvis.voice.bargein import Handoff

    p._pending_handoff = Handoff(mic, [])
    p.close()
    assert mic.closed and p._pending_handoff is None
    assert frames_of is not None
