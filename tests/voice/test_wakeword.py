"""Palabra de activacion: detector (logica + modelo real), modo STT y sesion.

* Logica del detector = motor SIMULADO con puntuaciones guionizadas.
* Modelo real = openWakeWord ONNX sobre grabaciones SINTETICAS (Kokoro) + ruido
  ambiente a -62 dBFS. NO es voz humana real: la validacion con la voz del usuario
  queda pendiente (JARVIS_PROGRESS.md).
"""

from __future__ import annotations

import numpy as np
import pytest

from openjarvis.voice.audio_io import CaptureError
from openjarvis.voice.pipeline import TurnOutcome
from openjarvis.voice.wake_session import run_wake_session
from openjarvis.voice.wakeword import (
    CHUNK_BYTES,
    WakeWordDetector,
    WakeWordUnavailable,
    matches_wake_word,
)
from tests.voice.helpers import (
    build_stream,
    frames_of,
    load_clip,
    noise,
    silence,
    to_pcm,
)
from tests.voice.test_pipeline import FakeMic, FakeSTT, FakeTTS, Player, Script, make

CHUNK = b"\x00\x00" * (CHUNK_BYTES // 2)


class ScriptedWake:
    def __init__(self, *scores):
        self.scores, self.resets = list(scores), 0

    def predict(self, x):
        assert x.dtype == np.int16 and len(x) == CHUNK_BYTES // 2
        return {"hey_jarvis": self.scores.pop(0) if self.scores else 0.0}

    def reset(self):
        self.resets += 1


# --- detector: logica ---------------------------------------------------------------


def test_needs_patience_consecutive_chunks_over_threshold():
    d = WakeWordDetector(
        threshold=0.5, patience=2, engine=ScriptedWake(0.9, 0.1, 0.9, 0.9)
    )
    res = [d.feed(CHUNK)[0] for _ in range(4)]
    assert res == [False, False, False, True]  # un pico aislado NO activa


def test_threshold_is_inclusive_and_below_never_fires():
    d = WakeWordDetector(threshold=0.5, patience=1, engine=ScriptedWake(0.49, 0.5))
    assert [d.feed(CHUNK)[0] for _ in range(2)] == [False, True]


def test_listen_accepts_frames_of_any_size_and_reports_the_chunk():
    d = WakeWordDetector(threshold=0.5, patience=2, engine=ScriptedWake(0, 0, 0.8, 0.8))
    data = CHUNK * 6
    frames = [data[i : i + 960] for i in range(0, len(data), 960)]  # 30 ms
    r = d.listen(frames)
    assert r.detected and r.detected_at_chunk == 4 and r.score == 0.8


def test_listen_stops_at_max_chunks_and_at_end_of_stream():
    d = WakeWordDetector(engine=ScriptedWake())
    assert not d.listen([CHUNK] * 5, max_chunks=3).detected
    r = d.listen([CHUNK] * 2)
    assert not r.detected and r.chunks == 2


def test_listen_resets_state_so_a_previous_streak_does_not_leak():
    eng = ScriptedWake(0.9, 0.9)
    d = WakeWordDetector(threshold=0.5, patience=3, engine=eng)
    d.feed(CHUNK), d.feed(CHUNK)
    assert not d.listen([CHUNK]).detected  # racha previa descartada
    assert eng.resets >= 1


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError):
        WakeWordDetector(threshold=0, engine=ScriptedWake())
    with pytest.raises(ValueError):
        WakeWordDetector(patience=0, engine=ScriptedWake())
    with pytest.raises(ValueError):
        WakeWordDetector(engine=ScriptedWake()).score_chunk(b"\x00" * 10)


def test_missing_model_is_a_clear_error():
    with pytest.raises(WakeWordUnavailable):
        WakeWordDetector(model="no_existe_este_modelo")


# --- modo STT: coincidencia de la palabra -------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Jarvis, ¿qué hora es?", True),
        ("hey jarvis", True),
        ("Oye, Yarvis", True),
        ("Harbys abre el bloc de notas", True),  # error real observado en Whisper
        ("Charvis", True),
        ("Dile a Javier que venga", False),
        ("Marvin el marciano", False),
        ("Travis Scott suena bien", False),
        ("el servicio es muy bueno", False),
        ("", False),
        ("Jar", False),
    ],
)
def test_wake_word_text_matching(text, expected):
    assert matches_wake_word(text) is expected


# --- modelo real sobre grabaciones sinteticas ---------------------------------------


@pytest.fixture(scope="module")
def real_detector():
    try:
        return WakeWordDetector(threshold=0.5, patience=2)
    except WakeWordUnavailable as exc:
        pytest.skip(f"openWakeWord no disponible: {exc}")


def _frames(sig):
    return list(frames_of(sig))


def test_real_model_detects_hey_jarvis(real_detector):
    sig = build_stream([silence(0.5), load_clip("wake_hey_jarvis"), silence(1.0)])
    r = real_detector.listen(_frames(sig))
    assert r.detected and r.score > 0.9


def test_real_model_ignores_other_speech(real_detector):
    sig = build_stream([silence(0.5), load_clip("wake_negative_speech"), silence(0.5)])
    assert not real_detector.listen(_frames(sig)).detected


def test_real_model_does_not_fire_on_ambient_noise_for_a_minute(real_detector):
    assert not real_detector.listen(_frames(noise(60.0))).detected


# --- pipeline.wait_for_wake ---------------------------------------------------------


def test_wait_for_wake_model_mode_returns_when_detected():
    p = make(
        FakeMic(build_stream([silence(0.3), load_clip("hola_jarvis"), silence(1.0)]))
    )
    det = WakeWordDetector(
        threshold=0.5, patience=2, engine=ScriptedWake(*([0] * 3 + [0.9] * 4))
    )
    out = p.wait_for_wake(mode="model", detector=det)
    assert out["mode"] == "model" and out["score"] == 0.9 and "stopped" not in out


def test_wait_for_wake_model_mode_stops_on_request():
    p = make(FakeMic(build_stream([silence(5.0)])))
    det = WakeWordDetector(engine=ScriptedWake())
    n = {"c": 0}

    def stop():
        n["c"] += 1
        return n["c"] > 10

    assert p.wait_for_wake(mode="model", detector=det, should_stop=stop)["stopped"]


def test_wait_for_wake_stt_mode_ignores_other_phrases_then_accepts_the_word():
    sig = build_stream(
        [
            silence(0.5),
            load_clip("como_estas"),
            silence(1.2),
            load_clip("hola_jarvis"),
            silence(1.5),
        ]
    )
    stt = FakeSTT("qué tal el día", "hey jarvis")
    p = make(FakeMic(sig), stt=stt)
    out = p.wait_for_wake(mode="stt", word="jarvis")
    assert out["text"] == "hey jarvis" and stt.calls == 2


def test_wait_for_wake_stt_mode_does_not_spin_when_the_mic_stream_ends():
    p = make(FakeMic(build_stream([silence(1.0)])), stt=FakeSTT("nada"))
    assert p.wait_for_wake(mode="stt")["stopped"]


def test_wait_for_wake_surfaces_capture_errors():
    p = make(FakeMic(silence(1.0), fail="enter"))
    with pytest.raises(CaptureError):
        p.wait_for_wake(mode="stt")


# --- sesion: activacion -> conversacion -> vuelta a espera ---------------------------


def _wake_pipeline(turn_mics, texts, agent=None):
    """Captura 1 = espera (modo stt); las siguientes alimentan los turnos."""
    wake_sig = build_stream([silence(0.4), load_clip("hola_jarvis"), silence(1.5)])
    tts = FakeTTS()
    p = make(
        Script(FakeMic(wake_sig), *turn_mics),
        stt=FakeSTT("hey jarvis", *texts),
        tts=tts,
        agent=agent or (lambda t, temp, m: f"respuesta a {t}"),
    )
    return p, tts


def test_session_wakes_acknowledges_converses_with_followups_then_returns_to_wait():

    def q():
        return FakeMic(
            build_stream([silence(0.3), load_clip("como_estas"), silence(1.5)])
        )

    p, tts = _wake_pipeline(
        [q(), q(), FakeMic(build_stream([silence(4.0)]))], ["uno", "dos"]
    )
    seen = []
    summary = run_wake_session(
        p,
        mode="stt",
        max_wakes=1,
        first_wait_s=2.5,
        followup_s=2.5,
        on_result=seen.append,
    )
    assert summary.wakes == 1 and summary.turns_ok == 2
    assert tts.texts[0] == "Dime."  # confirmacion tras activarse
    assert [r.outcome for r in seen] == [
        TurnOutcome.OK,
        TurnOutcome.OK,
        TurnOutcome.NO_SPEECH,
    ]
    assert [r.transcript for r in seen[:2]] == ["uno", "dos"]  # sin repetir "Jarvis"


def test_session_gives_up_after_repeated_agent_failures():
    speech = FakeMic(
        build_stream([silence(0.3), load_clip("como_estas"), silence(1.5)])
    )

    def boom(text, temp, max_tokens):
        raise RuntimeError("ollama caido")

    p, _ = _wake_pipeline([speech], ["a", "b", "c"], agent=boom)
    s = run_wake_session(p, mode="stt", max_wakes=1, max_failures=2, first_wait_s=2)
    assert s.stopped_reason == "too_many_failures" and s.turns_ok == 0
    assert [r.outcome for r in s.results] == [TurnOutcome.AGENT_ERROR] * 2


def test_microphone_failure_during_a_conversation_returns_to_waiting():
    p, _ = _wake_pipeline([FakeMic(silence(0.5), fail="enter")], [])
    s = run_wake_session(p, mode="stt", max_wakes=1, max_failures=3, first_wait_s=2)
    assert s.turns_ok == 0 and s.results[0].outcome == TurnOutcome.CAPTURE_ERROR


def test_session_stops_cleanly_on_request_and_never_acts_without_the_word():
    sig = build_stream([silence(0.4), load_clip("como_estas"), silence(1.5)])
    called = []
    p = make(
        Script(FakeMic(sig)),
        stt=FakeSTT("buenos días"),
        agent=lambda t, temp, m: called.append(t) or "x",
    )
    s = run_wake_session(p, mode="stt", should_stop=lambda: bool(p._stt.calls))
    assert s.wakes == 0 and called == []  # sin la palabra, el agente NUNCA se invoca


def test_session_survives_audio_that_the_player_cannot_play():
    p, _ = _wake_pipeline([FakeMic(build_stream([silence(4.0)]))], [])
    p._play_stream = lambda chunks, sr: (_ for _ in ()).throw(
        RuntimeError("sin altavoz")
    )
    s = run_wake_session(p, mode="stt", max_wakes=1, first_wait_s=2)
    assert s.wakes == 1  # la confirmacion fallida no tumba la sesion


def test_player_is_a_noop_helper_for_import():
    assert Player is not None and to_pcm is not None
