"""Pipeline de voz: clasificacion de fallos, recuperacion y streaming.

VAD/segmentacion = real sobre grabaciones sinteticas controladas; STT, TTS,
agente (Ollama), microfono y reproductor = SIMULADOS para aislar cada fallo.
Estos tests prueban la logica de orquestacion, NO el hardware ni los modelos
reales (ver test_voice_models.py y JARVIS_PROGRESS.md para eso).
"""

from __future__ import annotations

import json
import time
import wave
from types import SimpleNamespace

import pytest

from openjarvis.engine import EngineConnectionError
from openjarvis.voice.audio_io import CaptureError
from openjarvis.voice.pipeline import (
    TurnOutcome,
    VoicePipeline,
    diagnose_empty_transcript,
    diagnose_no_speech,
    looks_like_hallucination,
)
from openjarvis.voice.session import run_session
from openjarvis.voice.vad import VadConfig, VadStats
from tests.voice.helpers import build_stream, frames_of, silence, speech_turn

VAD = VadConfig(no_speech_timeout_ms=3000)


class FakeMic:
    def __init__(self, sig, startup=0.05, dead=False, fail=None):
        self._sig, self.startup_seconds = sig, startup
        self.signal_dead_at_start = dead
        self._fail = fail

    def __enter__(self):
        if self._fail == "enter":
            raise CaptureError("parec no entrego audio durante 2.0s")
        return self

    def __exit__(self, *exc):
        return False

    def frames(self):
        yield from frames_of(self._sig)
        if self._fail == "midstream":
            raise CaptureError("parec termino inesperadamente (codigo 1)")


def utterance(clip="hola_jarvis", **kw):
    sig, _ = speech_turn([clip], **kw)
    return sig


class Script:
    """Reparte una captura distinta a cada turno."""

    def __init__(self, *mics):
        self.mics = list(mics)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.mics[min(self.calls - 1, len(self.mics) - 1)]


class FakeSTT:
    def __init__(self, *texts, boom=False):
        self.texts, self.calls, self.boom = list(texts), 0, boom

    def transcribe(self, wav, format="wav", language=None, initial_prompt=None):
        self.calls += 1
        self.prompts = [*getattr(self, "prompts", []), initial_prompt]
        if self.boom:
            raise RuntimeError("ctranslate2 exploto")
        text = self.texts[min(self.calls - 1, len(self.texts) - 1)]
        return SimpleNamespace(text=text, language="es", confidence=0.98)


class FakeTTS:
    sample_rate = 24000

    def __init__(self, chunk_samples=(2400, 2400, 2400), synth_delay=0.0, boom=False):
        self.chunk_samples, self.synth_delay, self.boom = (
            chunk_samples,
            synth_delay,
            boom,
        )
        self.texts = []

    def synthesize_stream(self, text, **kw):
        self.texts.append(text)
        if self.boom:
            raise RuntimeError("kokoro exploto")
        for n in self.chunk_samples:
            time.sleep(self.synth_delay)
            yield b"\x01\x00" * n


class Player:
    def __init__(self):
        self.chunks = []

    def __call__(self, chunks, sample_rate):
        self.chunks.extend(list(chunks))


def make(mics, stt=None, tts=None, agent=None, player=None, tmp_path=None, **kw):
    agent = agent or (lambda text, t, m: "Claro, dime que necesitas.")
    return VoicePipeline(
        stt=stt or FakeSTT("hola jarvis como estas"),
        tts=tts or FakeTTS(),
        capture=mics if callable(mics) else Script(mics),
        play_stream=player or Player(),
        agent_fn=agent,
        vad_config=kw.pop("vad_config", VAD),
        warm_up=False,
        log_path=(tmp_path / "voice.jsonl") if tmp_path else None,
        log_turns=tmp_path is not None,
        **kw,
    )


# --- turno correcto ---------------------------------------------------------


def test_successful_turn_end_to_end(tmp_path):
    player = Player()
    p = make(FakeMic(utterance()), player=player, tmp_path=tmp_path)
    r = p.run_turn()
    assert r.outcome == TurnOutcome.OK and r.ok and r.audio_played
    assert r.transcript == "hola jarvis como estas"
    assert r.response_text.startswith("Claro")
    assert len(player.chunks) == 3
    assert r.latency.tts_mode == "stream" and r.latency.tts_detail["chunks"] == 3
    assert r.vad.voiced_seconds > 0 and r.audio.peak_dbfs > -60


def test_agent_receives_the_voice_style_hint_and_token_cap():
    seen = {}

    def agent(text, temperature, max_tokens):
        seen.update(text=text, max_tokens=max_tokens)
        return "ok"

    p = make(FakeMic(utterance()), agent=agent)
    p.run_turn(max_tokens=123)
    assert "breve" in seen["text"] and "hola jarvis" in seen["text"]
    assert seen["max_tokens"] == 123


# --- 7. transcripcion vacia ---------------------------------------------------


def test_empty_transcript_is_reported_with_cause_and_never_calls_the_agent():
    called = []
    p = make(
        FakeMic(utterance()),
        stt=FakeSTT(""),
        agent=lambda *a: called.append(a) or "no deberia llamarse",
    )
    r = p.run_turn()
    assert r.outcome == TurnOutcome.EMPTY_TRANSCRIPT and not r.ok
    assert r.probable_cause and not called
    assert r.response_text == ""  # sin respuestas ficticias


def test_whisper_hallucination_on_short_noise_is_not_treated_as_a_command():
    assert looks_like_hallucination("¡Gracias!", voiced_seconds=0.4)
    assert not looks_like_hallucination("Gracias", voiced_seconds=2.5)
    assert not looks_like_hallucination("abre el navegador", voiced_seconds=0.4)
    p = make(FakeMic(utterance("hola_jarvis")), stt=FakeSTT("Gracias."))
    r = p.run_turn()
    # hola_jarvis dura ~1.2 s de voz: Gracias es legitimo; con voz < 1 s no
    assert r.outcome in (TurnOutcome.OK, TurnOutcome.EMPTY_TRANSCRIPT)


def test_diagnoses_distinguish_causes():
    quiet = VadStats(voiced_ratio=0.6, noise_floor_dbfs=-62)
    from openjarvis.voice.audio_io import AudioLevels

    assert "corto" in diagnose_empty_transcript(quiet, AudioLevels(0.3, -20, -30))
    assert "muy bajo" in diagnose_empty_transcript(quiet, AudioLevels(2.0, -55, -70))
    assert "saturado" in diagnose_empty_transcript(
        quiet, AudioLevels(2.0, -1, -10, clipped_fraction=0.2)
    )
    noisy = VadStats(voiced_ratio=0.1)
    assert "ruido" in diagnose_empty_transcript(noisy, AudioLevels(2.0, -20, -30))
    assert "Whisper" in diagnose_empty_transcript(quiet, AudioLevels(2.0, -20, -30))
    assert "silenciado" in diagnose_no_speech(VadStats(dead_frame_fraction=1.0))
    assert "volumen" in diagnose_no_speech(VadStats(max_idle_dbfs=-70))
    assert "No se detecto voz" in diagnose_no_speech(VadStats(max_idle_dbfs=-40))


def test_long_pause_near_limit_produces_a_hint():
    sig, _ = speech_turn(["hola_jarvis", "como_estas"], pause_s=1.1)
    p = make(FakeMic(sig), vad_config=VadConfig(no_speech_timeout_ms=3000))
    r = p.run_turn()
    assert r.outcome == TurnOutcome.OK
    assert r.vad.longest_pause_seconds >= 0.75 * 1.4
    assert "silence_timeout_ms" in r.hint


# --- ausencia de voz / captura -------------------------------------------------


def test_no_speech_outcome_on_silence():
    p = make(FakeMic(build_stream([silence(5.0)])))
    r = p.run_turn()
    assert r.outcome == TurnOutcome.NO_SPEECH
    assert "voz" in r.probable_cause.lower() or "microfono" in r.probable_cause.lower()


def test_dead_microphone_diagnosed_as_muted():
    import numpy as np

    p = make(FakeMic(np.zeros(5 * 16000, dtype="float32"), dead=True))
    r = p.run_turn()
    assert r.outcome == TurnOutcome.NO_SPEECH
    assert "silenciado" in r.probable_cause


@pytest.mark.parametrize("fail", ["enter", "midstream"])
def test_capture_errors_are_classified_not_raised(fail):
    sig = build_stream([silence(0.6)])  # nunca se llega a detectar voz
    p = make(FakeMic(sig, fail=fail))
    r = p.run_turn()
    assert r.outcome == TurnOutcome.CAPTURE_ERROR and not r.ok
    assert "captura" in r.probable_cause.lower()


def test_capture_stream_ending_without_speech_is_a_capture_error():
    p = make(
        FakeMic(build_stream([silence(0.5)])),
        vad_config=VadConfig(no_speech_timeout_ms=0),
    )
    r = p.run_turn()
    assert r.outcome == TurnOutcome.CAPTURE_ERROR


# --- 9/10/11. errores de STT, Ollama y Kokoro -----------------------------------


def test_stt_error_is_classified():
    p = make(FakeMic(utterance()), stt=FakeSTT(boom=True))
    r = p.run_turn()
    assert r.outcome == TurnOutcome.STT_ERROR
    assert "RuntimeError" in r.error and not r.ok


def test_ollama_down_is_classified_and_nothing_is_spoken():
    def down(*a):
        raise EngineConnectionError("Ollama not reachable at http://localhost:11434")

    tts, player = FakeTTS(), Player()
    p = make(FakeMic(utterance()), agent=down, tts=tts, player=player)
    r = p.run_turn()
    assert r.outcome == TurnOutcome.AGENT_ERROR and not r.ok
    assert "Ollama" in r.error and not tts.texts and not player.chunks


def test_empty_model_response_is_an_agent_error_not_silence():
    p = make(FakeMic(utterance()), agent=lambda *a: "   ")
    assert p.run_turn().outcome == TurnOutcome.AGENT_ERROR


def test_kokoro_failure_keeps_the_text_answer():
    p = make(FakeMic(utterance()), tts=FakeTTS(boom=True))
    r = p.run_turn()
    assert r.outcome == TurnOutcome.TTS_ERROR
    assert r.ok and not r.audio_played  # el turno cuenta: la respuesta existe
    assert r.response_text


def test_playback_failure_is_classified():
    def bad_player(chunks, sample_rate):
        raise RuntimeError("paplay murio")

    p = make(FakeMic(utterance()), player=bad_player)
    assert p.run_turn().outcome == TurnOutcome.TTS_ERROR


# --- 12. reproduccion de varios fragmentos / continuidad ----------------------------


def test_chunks_are_played_in_order_as_they_are_synthesized():
    order = []

    class OrderedTTS(FakeTTS):
        def synthesize_stream(self, text, **kw):
            for i in (1, 2, 3):
                order.append(f"synth{i}")
                yield bytes([i, 0]) * 100

    class P(Player):
        def __call__(self, chunks, sample_rate):
            for c in chunks:
                order.append(f"play{c[0]}")

    p = make(FakeMic(utterance()), tts=OrderedTTS(), player=P())
    p.run_turn()
    # cada trozo se reproduce antes de sintetizar el siguiente (solapamiento real)
    assert order == ["synth1", "play1", "synth2", "play2", "synth3", "play3"]


def test_gap_metric_detects_underruns_when_synthesis_is_slower_than_playback():
    # trozos de 4 ms de audio que tardan 60 ms en sintetizarse -> huecos
    slow = FakeTTS(chunk_samples=(100, 100, 100), synth_delay=0.06)
    p = make(FakeMic(utterance()), tts=slow)
    d = p.run_turn().latency.tts_detail
    assert d["chunks"] == 3 and d["underruns"] == 2 and d["gap_seconds"] > 0.08


def test_no_gaps_when_synthesis_keeps_ahead_of_playback():
    fast = FakeTTS(chunk_samples=(48000, 48000, 48000), synth_delay=0.0)  # 2 s c/u
    p = make(FakeMic(utterance()), tts=fast)
    d = p.run_turn().latency.tts_detail
    assert d["underruns"] == 0 and d["gap_seconds"] == 0.0


# --- 13/14. varios turnos y recuperacion ---------------------------------------------


def test_several_consecutive_turns():
    mics = Script(*[FakeMic(utterance(seed=s)) for s in (1, 2, 3)])
    p = make(mics, stt=FakeSTT("uno", "dos", "tres"))
    s = run_session(p, num_turns=3)
    assert s.turns_ok == 3 and s.attempts == 3 and not s.failures
    assert [r.transcript for r in s.results] == ["uno", "dos", "tres"]
    assert s.stopped_reason == "completed"


def test_session_recovers_after_an_empty_transcript_without_ending():
    mics = Script(FakeMic(utterance(seed=1)), FakeMic(utterance(seed=2)))
    p = make(mics, stt=FakeSTT("", "ahora si"))
    s = run_session(p, num_turns=1)  # --turns 1: el turno vacio NO cuenta
    assert s.turns_ok == 1 and s.attempts == 2
    assert s.failures == {"empty_transcript": 1}
    assert s.results[-1].transcript == "ahora si"


def test_session_recovers_from_no_speech_then_succeeds():
    mics = Script(FakeMic(build_stream([silence(4.0)])), FakeMic(utterance()))
    p = make(mics)
    s = run_session(p, num_turns=1)
    assert s.turns_ok == 1 and s.failures == {"no_speech": 1}


def test_session_gives_up_after_consecutive_failures_with_reason():
    p = make(Script(FakeMic(utterance())), stt=FakeSTT(""))
    s = run_session(p, num_turns=1, max_consecutive_failures=3)
    assert s.stopped_reason == "too_many_failures"
    assert s.turns_ok == 0 and s.attempts == 3


def test_a_success_resets_the_consecutive_failure_counter():
    mics = Script(*[FakeMic(utterance(seed=s)) for s in range(1, 6)])
    p = make(mics, stt=FakeSTT("", "", "bien", "", "bien2"))
    s = run_session(p, num_turns=2, max_consecutive_failures=3)
    assert s.turns_ok == 2 and s.stopped_reason == "completed"


def test_ctrl_c_ends_the_session_cleanly():
    class Interrupting(Script):
        def __call__(self):
            raise KeyboardInterrupt

    s = run_session(make(Interrupting()), num_turns=0)
    assert s.stopped_reason == "interrupted"


def test_ollama_outage_does_not_kill_the_session_and_task_continues():
    state = {"n": 0}

    def flaky(text, t, m):
        state["n"] += 1
        if state["n"] == 1:
            raise EngineConnectionError("down")
        return "ya estoy de vuelta"

    mics = Script(FakeMic(utterance(seed=1)), FakeMic(utterance(seed=2)))
    s = run_session(make(mics, agent=flaky), num_turns=1)
    assert s.turns_ok == 1 and s.failures == {"agent_error": 1}


# --- registro estructurado y audio de depuracion ----------------------------


def test_turn_log_has_measurements_but_no_private_text(tmp_path):
    p = make(
        FakeMic(utterance()), stt=FakeSTT("mi clave secreta es 1234"), tmp_path=tmp_path
    )
    p.run_turn()
    line = (tmp_path / "voice.jsonl").read_text().splitlines()[0]
    ev = json.loads(line)
    assert ev["run_id"] == p.run_id and ev["turn"] == 1 and ev["outcome"] == "ok"
    assert ev["transcript_words"] == 5
    assert "secreta" not in line and "1234" not in line and "Claro" not in line
    assert "vad" in ev and "audio" in ev and ev["vad"]["end_reason"] == "silence"


def test_failures_are_logged_with_their_cause(tmp_path):
    p = make(FakeMic(utterance()), stt=FakeSTT(""), tmp_path=tmp_path)
    p.run_turn()
    ev = json.loads((tmp_path / "voice.jsonl").read_text().splitlines()[0])
    assert ev["outcome"] == "empty_transcript" and ev["probable_cause"]


def test_debug_audio_is_opt_in_and_pruned(tmp_path):
    p = make(Script(FakeMic(utterance())), debug_audio_dir=tmp_path / "dbg")
    for _ in range(12):
        p.run_turn()
    files = sorted((tmp_path / "dbg").glob("utt-*.wav"))
    assert len(files) == 10
    with wave.open(str(files[-1]), "rb") as wf:
        assert wf.getframerate() == 16000 and wf.getnchannels() == 1

    q = make(Script(FakeMic(utterance())))
    assert q._debug_audio_dir is None  # por defecto NO se guarda ningun audio


# --- confirmacion de acciones por voz --------------------------------------


def _confirm_pipeline(stt, tts=None, mic=None):
    tts = tts or FakeTTS()
    return make(mic or FakeMic(utterance()), stt=stt, tts=tts), tts


def test_voice_confirmation_yes_asks_out_loud_and_listens():
    stt = FakeSTT("Sí, hazlo")
    p, tts = _confirm_pipeline(stt)
    assert p.confirm_by_voice("Escribir «hola» en la ventana «Bloc»") is True
    assert "Sí. No. Hazlo" in stt.prompts[0]  # vocabulario de si/no, no el general
    assert "permiso" in tts.texts[0] and "Escribir «hola»" in tts.texts[0]
    assert "sí o no" in tts.texts[0].lower()


def test_voice_confirmation_no_denies():
    p, _ = _confirm_pipeline(FakeSTT("No, cancela eso"))
    assert p.confirm_by_voice("Cerrar la ventana") is False


def test_ambiguous_answer_is_asked_again_then_gives_up_without_approving():
    p, tts = _confirm_pipeline(FakeSTT("mmm déjame pensar", "quizás"))
    assert p.confirm_by_voice("Hacer clic") is None
    assert len(tts.texts) == 2 and "No te he entendido" in tts.texts[1]


def test_second_attempt_can_resolve():
    p, tts = _confirm_pipeline(FakeSTT("eh", "vale"))
    assert p.confirm_by_voice("Hacer clic") is True and len(tts.texts) == 2


def test_silence_during_confirmation_does_not_approve():
    p, _ = _confirm_pipeline(FakeSTT("sí"), mic=FakeMic(build_stream([silence(12.0)])))
    assert p.confirm_by_voice("Hacer clic") is None  # nadie habló => no se aprueba


def test_microphone_failure_during_confirmation_is_not_an_approval():
    p, _ = _confirm_pipeline(
        FakeSTT("sí"), mic=FakeMic(build_stream([silence(1)]), fail="enter")
    )
    assert p.confirm_by_voice("Hacer clic") is None


def test_voice_channel_resolves_the_broker_and_is_credited():
    from openjarvis.jarvis.confirmation import ConfirmationBroker

    p, _ = _confirm_pipeline(FakeSTT("sí"))
    broker = ConfirmationBroker(timeout_s=5, audit_path=None)
    ok = broker.request(
        "windows_type", {"text": "hola", "title": "Bloc"}, channels=[p.confirm_channel]
    )
    assert ok is True and broker.recent()[0]["decided_by"] == "voz"


def test_whisper_receives_the_vocabulary_prompt():
    seen = {}

    class Spy(FakeSTT):
        def transcribe(self, wav, format="wav", language=None, initial_prompt=None):
            seen["prompt"] = initial_prompt
            return super().transcribe(wav, format, language)

    p = make(FakeMic(utterance()), stt=Spy("hola"), stt_prompt="Hola, Jarvis.")
    p.run_turn()
    assert seen["prompt"] == "Hola, Jarvis."


def test_style_hint_can_be_disabled_when_the_runtime_owns_the_prompt():
    got = {}

    def agent(text, t, m):
        got["text"] = text
        return "ok"

    p = make(FakeMic(utterance()), agent=agent, style_hint="")
    p.run_turn()
    assert got["text"] == "hola jarvis como estas"  # sin prefijo: lo pone el runtime
