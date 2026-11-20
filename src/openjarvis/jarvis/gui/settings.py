"""Ajustes editables desde la GUI: lista blanca, validación y persistencia en TOML.

Solo se pueden cambiar claves conocidas de ``[voice]`` con tipo y rango
validados; el resto de la configuración (motor, modelo, rutas) no se toca desde
la interfaz. Los cambios se guardan en ``config.toml`` y se aplican al
(re)iniciar la voz.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# clave -> (tipo, mínimo, máximo, opciones)
EDITABLE: dict[str, tuple[type, float | None, float | None, tuple | None]] = {
    "voice.silence_timeout_ms": (int, 300, 5000, None),
    "voice.no_speech_timeout_ms": (int, 0, 120000, None),
    "voice.max_tokens": (int, 20, 1000, None),
    "voice.aggressiveness": (int, 0, 3, None),
    "voice.gate_margin_db": (float, 0, 30, None),
    "voice.confirm_by_voice": (bool, None, None, None),
    "voice.confirm_timeout_s": (float, 5, 300, None),
    "voice.wake_mode": (str, None, None, ("off", "model", "stt")),
    "voice.wake_threshold": (float, 0.1, 1.0, None),
    "voice.wake_followup_s": (float, 2, 60, None),
    "voice.barge_in": (bool, None, None, None),
    "voice.barge_in_margin_db": (float, 3, 40, None),
    "voice.ollama_keep_alive": (str, None, None, ("5m", "30m", "1h", "24h")),
}


class SettingError(ValueError):
    pass


def config_path() -> Path:
    from openjarvis.core.config import DEFAULT_CONFIG_DIR

    return Path(os.environ.get("OPENJARVIS_CONFIG", DEFAULT_CONFIG_DIR / "config.toml"))


def coerce(key: str, value: Any) -> Any:
    if key not in EDITABLE:
        raise SettingError(f"Ajuste no editable: {key}")
    typ, lo, hi, options = EDITABLE[key]
    try:
        if typ is bool:
            if isinstance(value, bool):
                v: Any = value
            elif str(value).lower() in ("true", "1", "yes", "si", "sí"):
                v = True
            elif str(value).lower() in ("false", "0", "no"):
                v = False
            else:
                raise ValueError(value)
        elif typ is int:
            if isinstance(value, bool) or (
                isinstance(value, float) and not value.is_integer()
            ):
                raise ValueError(value)
            v = int(value)
        elif typ is float:
            if isinstance(value, bool):
                raise ValueError(value)
            v = float(value)
        else:
            v = str(value)
    except (TypeError, ValueError) as exc:
        raise SettingError(f"{key}: valor inválido ({value!r})") from exc
    if options is not None and v not in options:
        raise SettingError(f"{key}: debe ser uno de {list(options)}")
    if lo is not None and v < lo or hi is not None and v > hi:
        raise SettingError(f"{key}: fuera de rango [{lo}, {hi}]")
    return v


def current_values() -> dict[str, Any]:
    from openjarvis.core.config import load_config

    cfg = load_config().voice
    return {k: getattr(cfg, k.split(".", 1)[1]) for k in EDITABLE}


def schema() -> list[dict[str, Any]]:
    out = []
    for key, (typ, lo, hi, options) in EDITABLE.items():
        out.append(
            {
                "key": key,
                "type": typ.__name__,
                "min": lo,
                "max": hi,
                "options": list(options) if options else None,
            }
        )
    return out


def apply(changes: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Valida TODO antes de escribir nada; devuelve los valores efectivos."""
    import tomlkit

    clean = {k: coerce(k, v) for k, v in changes.items()}
    path = path or config_path()
    doc = (
        tomlkit.parse(path.read_text(encoding="utf-8"))
        if path.exists()
        else (tomlkit.document())
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    for key, value in clean.items():
        section, name = key.split(".", 1)
        if section not in doc:
            doc.add(section, tomlkit.table())
        doc[section][name] = value
    tmp = path.with_suffix(".tmp")
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    os.replace(tmp, path)  # escritura atómica: nunca queda un TOML a medias
    from openjarvis.core.config import load_config

    load_config.cache_clear()
    return clean
