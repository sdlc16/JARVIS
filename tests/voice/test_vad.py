"""Cierre de turno, pausas, ruido y silencio (VAD). Audio: grabaciones SINTETICAS
controladas + ruido gaussiano al nivel real medido -- no microfono real."""

from __future__ import annotations

import signal

import numpy as np
import pytest

from openjarvis.voice.vad import (
    EndReason,
    NoiseFloorTracker,
    VadConfig,
    VoiceActivityDetector,
    segment_utterance,
)
from tests.voice.helpers import (
    SR,
    at_level,
    build_stream,
    frames_of,
    load_clip,
    noise,
    silence,
    speech_turn,
)

LONG_PHRASE = ["hola_jarvis", "como_estas", "explica", "controlar_pc"]


@pytest.fixture(autouse=True)
def hang_guard():
    """Una regresion del antiguo bucle infinito debe FALLAR, no colgar CI."""
    if not hasattr(signal, "SIGALRM"):
        yield
        return
    signal.signal(
        signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("VAD colgado"))
    )
    signal.alarm(60)
    yield
    signal.alarm(0)


def run(sig, **cfg):
    vad = VoiceActivityDetector(VadConfig(**cfg))
    pcm, stats = segment_utterance(frames_of(sig), vad)
    return pcm, stats, vad


def dur(pcm: bytes) -> float:
    return len(pcm) / 2 / SR


# --- 1/2. frase corta y frase larga ---------------------------------------


def test_short_phrase_is_captured_completely():
    sig, span = speech_turn(["hola_jarvis"])
    pcm, st, _ = run(sig)
    assert st.end_reason == EndReason.SILENCE
    assert dur(pcm) >= span  # incluye toda la voz (+ preroll y cola)
    assert dur(pcm) <= span + 0.3 + 0.3 + 0.15


def test_long_phrase_is_captured_completely():
    sig, span = speech_turn(LONG_PHRASE, pause_s=0.4)
    pcm, st, _ = run(sig)
    assert st.end_reason == EndReason.SILENCE
    assert dur(pcm) >= span
    assert st.voiced_ratio > 0.4


# --- 3/4. pausas naturales y de distinta duracion ---------------------------


@pytest.mark.parametrize("pause_s", [0.3, 0.6, 0.9, 1.1])
def test_internal_pauses_shorter_than_timeout_do_not_split_the_phrase(pause_s):
    sig, span = speech_turn(LONG_PHRASE, pause_s=pause_s)
    pcm, st, _ = run(sig)  # silencio final por defecto: 1400 ms
    assert dur(pcm) >= span, f"se perdio voz con pausas de {pause_s}s"
    assert st.pause_count >= 3
    assert st.longest_pause_seconds >= pause_s * 0.8


def test_old_500ms_timeout_would_have_cut_the_phrase():
    """Regresion del fallo real: con 500 ms la frase se parte en la 1a pausa."""
    sig, span = speech_turn(LONG_PHRASE, pause_s=0.9)
    pcm, _, _ = run(sig, silence_timeout_ms=500)
    assert dur(pcm) < 0.5 * span


def test_pause_longer_than_timeout_ends_the_turn_by_design():
    sig, span = speech_turn(LONG_PHRASE, pause_s=2.0)
    vad = VoiceActivityDetector(VadConfig())
    frames = frames_of(sig)
    first, st1 = segment_utterance(frames, vad)
    second, st2 = segment_utterance(frames, vad)  # el resto sigue siendo audio
    assert st1.end_reason == EndReason.SILENCE
    assert 0 < dur(first) < span
    assert dur(second) > 0  # el resto no se pierde: es el siguiente turno


def test_silence_timeout_is_configurable_without_touching_code():
    sig, span = speech_turn(LONG_PHRASE, pause_s=1.8)
    pcm_default, _, _ = run(sig)
    pcm_long, st, _ = run(sig, silence_timeout_ms=2600)
    assert dur(pcm_default) < span  # 1400 ms parte la frase
    assert dur(pcm_long) >= span  # 2600 ms la conserva
    assert st.end_reason == EndReason.SILENCE


def test_final_silence_closes_turn_within_configured_time():
    sig, _ = speech_turn(["hola_jarvis"], tail_s=5.0)
    _, st, _ = run(sig, silence_timeout_ms=1400, tail_ms=300)
    # se retiene la cola configurada, no los 1.4 s de silencio completos
    assert st.trailing_silence_seconds <= 1.4 + 0.05


# --- pre-roll: no perder la primera silaba ----------------------------------


def test_preroll_keeps_audio_before_detected_onset():
    sig, span = speech_turn(["explica"], lead_s=1.0)
    pcm, st, _ = run(sig)
    assert st.preroll_seconds >= 0.25
    # lo capturado cubre la voz completa aunque el onset se confirme ~90 ms tarde
    assert dur(pcm) >= span


# --- voz suave (micro con poca ganancia) ------------------------------------


@pytest.mark.parametrize("level_db", [-25, -35, -45, -50])
def test_quiet_voice_is_still_captured(level_db):
    sig, span = speech_turn(LONG_PHRASE, level_db=level_db, pause_s=0.5)
    pcm, st, _ = run(sig)
    assert dur(pcm) >= 0.95 * span, f"voz a {level_db} dBFS recortada"


# --- 5. silencio sin hablar ------------------------------------------------


def test_silence_without_speech_times_out_instead_of_blocking():
    sig = build_stream([silence(5.0)])
    pcm, st, _ = run(sig, no_speech_timeout_ms=3000)
    assert pcm == b""
    assert st.end_reason == EndReason.NO_SPEECH
    assert st.wait_for_speech_seconds == pytest.approx(3.0, abs=0.1)


# --- 6. ruido ambiental -----------------------------------------------------


@pytest.mark.parametrize("noise_db", [-62, -50, -40])
def test_steady_ambient_noise_never_opens_a_turn(noise_db):
    sig = noise(12.0, noise_db)
    pcm, st, _ = run(sig, no_speech_timeout_ms=10000)
    assert pcm == b"", f"ruido a {noise_db} dBFS abrio un turno falso"
    assert st.end_reason == EndReason.NO_SPEECH


def test_short_loud_click_is_discarded_as_noise_burst():
    click = np.zeros(int(0.1 * SR), dtype=np.float32)
    click[::3] = 0.5  # rafaga brusca de 100 ms
    sig = build_stream([silence(2.0), click, silence(4.0)])
    pcm, st, _ = run(sig, no_speech_timeout_ms=5000)
    assert pcm == b""
    assert st.end_reason == EndReason.NO_SPEECH


def sparse_bursts(total_s: float, period_s: float, burst_s: float = 0.15) -> np.ndarray:
    """Rafagas breves de 'voz' espaciadas: ruido a rafagas, no una frase."""
    burst = at_level(load_clip("explica"), -30)[
        int(0.5 * SR) : int(0.5 * SR) + int(burst_s * SR)
    ]
    parts, t = [silence(1.0)], 1.0
    while t < total_s:
        parts += [burst, silence(period_s - burst_s)]
        t += period_s
    return build_stream(parts)


def test_bursty_noise_cannot_keep_the_wait_open_past_the_timeout():
    """Regresion medida con el micro real: ruido a rafagas mantenia 'activo'
    un turno sin habla y la espera superaba el limite (9.5 s con limite 6 s)."""
    sig = sparse_bursts(total_s=14.0, period_s=1.0)
    pcm, st, _ = run(sig, no_speech_timeout_ms=5000)
    assert pcm == b""
    assert st.end_reason == EndReason.NO_SPEECH
    assert st.frames_seen * 0.03 <= 6.5


def test_cap_reached_with_only_noise_is_discarded_not_returned_as_speech():
    sig = sparse_bursts(total_s=8.0, period_s=1.0)
    pcm, st, _ = run(sig, max_utterance_ms=3000, no_speech_timeout_ms=0)
    assert pcm == b""
    assert st.discarded_noise_bursts >= 1
    assert st.end_reason == EndReason.CAPTURE_ENDED


def test_speech_in_noisy_room_is_captured():
    sig, span = speech_turn(LONG_PHRASE, level_db=-20, noise_db=-45, pause_s=0.5)
    pcm, st, _ = run(sig)
    assert dur(pcm) >= span


# --- captura cortada / mic mudo ---------------------------------------------


def test_exhausted_capture_returns_instead_of_spinning_forever():
    sig = build_stream([silence(2.0)])
    pcm, st, _ = run(sig, no_speech_timeout_ms=0)  # sin limite de espera
    assert pcm == b""
    assert st.end_reason == EndReason.CAPTURE_ENDED


def test_capture_cut_mid_utterance_keeps_what_was_heard():
    sig, span = speech_turn(["controlar_pc"], tail_s=0.0, lead_s=0.6)
    pcm, st, _ = run(sig)
    assert st.end_reason == EndReason.CAPTURE_ENDED
    assert dur(pcm) > 0.8 * span


def test_dead_microphone_is_reported_as_digital_silence():
    sig = np.zeros(5 * SR, dtype=np.float32)
    pcm, st, _ = run(sig, no_speech_timeout_ms=4000)
    assert pcm == b""
    assert st.end_reason == EndReason.NO_SPEECH
    assert st.dead_frame_fraction > 0.95


def test_max_utterance_cap_is_reported():
    clip = at_level(load_clip("frase_larga"), -30)
    sig = build_stream([silence(1.0), clip, clip, clip])
    pcm, st, _ = run(sig, max_utterance_ms=6000)
    assert st.end_reason == EndReason.MAX_DURATION
    assert dur(pcm) == pytest.approx(6.0, abs=0.1)


# --- noise floor / compuerta de energia --------------------------------------


def test_noise_floor_ignores_dead_startup_frames():
    tr = NoiseFloorTracker()
    for _ in range(30):
        tr.update(-98.0)  # ceros de arranque de parec
    assert not tr.calibrated
    for _ in range(40):
        tr.update(-63.0)
    assert tr.calibrated
    assert -65 < tr.floor_db < -61


def test_energy_gate_rejects_frames_webrtcvad_wrongly_flags():
    """webrtcvad solo marcaba como habla el 52-69 % del ruido real; la
    compuerta de energia lo corrige (ver JARVIS_PROGRESS.md)."""
    sig = noise(6.0, -62)
    gated = VoiceActivityDetector(VadConfig(aggressiveness=1, energy_gate=True))
    raw_flags = gated_flags = 0
    n = 0
    for f in frames_of(sig):
        gated.noise_floor.update(-62)
        raw_flags += gated.is_speech(f)
        gated_flags += gated.is_voiced(f)
        n += 1
    assert gated_flags <= 0.02 * n
    assert gated_flags <= raw_flags


def test_two_consecutive_turns_share_noise_floor_calibration():
    s1, _ = speech_turn(["hola_jarvis"], seed=1)
    s2, _ = speech_turn(["como_estas"], seed=2)
    vad = VoiceActivityDetector(VadConfig())
    p1, _ = segment_utterance(frames_of(s1), vad)
    p2, st2 = segment_utterance(frames_of(s2), vad)
    assert p1 and p2
    assert st2.end_reason == EndReason.SILENCE
