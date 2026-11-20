"""La configuracion del turno de voz es ajustable sin tocar codigo."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from openjarvis.core.config import VoiceConfig, load_config, validate_config_key


@pytest.fixture(autouse=True)
def _fresh_config_cache():
    """`load_config` esta decorada con lru_cache: sin limpiar la cache devuelve la
    config de CUALQUIER test anterior del proceso (fallo medido: el test pasaba
    aislado y fallaba en la suite completa) y, al reves, filtraria la nuestra."""
    load_config.cache_clear()
    yield
    load_config.cache_clear()


def test_defaults_are_the_measured_values():
    vc = VoiceConfig()
    assert vc.silence_timeout_ms == 1400  # antes 500: cortaba la 1a pausa
    assert vc.aggressiveness == 2 and vc.energy_gate and vc.gate_margin_db == 6.0
    assert vc.max_tokens == 200 and vc.max_consecutive_failures == 3
    assert vc.debug_audio_dir == ""  # guardar audio es opt-in


def test_voice_section_is_loaded_from_toml(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[voice]\nsilence_timeout_ms = 1800\ngate_margin_db = 8.5\n")
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(cfg))
    c = load_config()
    assert c.voice.silence_timeout_ms == 1800
    assert c.voice.gate_margin_db == 8.5
    assert c.voice.aggressiveness == 2  # lo no indicado conserva el defecto


def test_voice_keys_are_settable_via_jarvis_config():
    assert validate_config_key("voice.silence_timeout_ms") is int
    assert validate_config_key("voice.gate_margin_db") is float


def test_voice_command_exposes_the_tuning_flags():
    from openjarvis.cli import cli

    out = CliRunner().invoke(cli, ["voice", "--help"]).output
    for flag in ("--silence-ms", "--max-wait-s", "--max-failures", "--debug-audio-dir"):
        assert flag in out


def test_wake_word_defaults_are_off_until_validated_with_a_real_voice():
    vc = VoiceConfig()
    assert vc.wake_mode == "off"  # no se activa solo: falta validar con voz humana
    assert vc.wake_model == "hey_jarvis" and vc.wake_patience >= 2
    assert validate_config_key("voice.wake_mode") is str
    assert validate_config_key("voice.wake_threshold") is float


def test_voice_command_exposes_the_wake_option():
    from openjarvis.cli.voice_cmd import voice

    res = CliRunner().invoke(voice, ["--help"])
    assert "--wake" in res.output and "model" in res.output and "stt" in res.output
