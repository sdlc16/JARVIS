"""Registro estructurado (JSONL) de los turnos de voz.

Una linea JSON por turno, con identificador de sesion (`run_id`), medidas
de latencia/audio y la causa de los fallos -- lo necesario para distinguir
entre ausencia de voz, corte por silencio, fallo de captura y fallo de
transcripcion sin tener que reproducir el problema.

Privacidad (CLAUDE.md): NO se guarda el texto de lo que dijo el usuario ni
de la respuesta (solo su longitud en palabras), ni audio, ni credenciales.
Es un registro local (`~/.openjarvis/logs/voice.jsonl`, permisos 0600) y
nunca interrumpe la conversacion: cualquier error al escribirlo se ignora.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from openjarvis.core.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = DEFAULT_CONFIG_DIR / "logs" / "voice.jsonl"
MAX_LOG_BYTES = 5 * 1024 * 1024


def new_run_id() -> str:
    return uuid.uuid4().hex[:8]


def append_event(event: dict[str, Any], path: Path | None = None) -> None:
    """Añade un evento al log (best-effort, rota a `.1` al pasar 5 MB)."""
    target = path or DEFAULT_LOG_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size > MAX_LOG_BYTES:
            target.replace(target.with_suffix(target.suffix + ".1"))
        record = {"ts": round(time.time(), 3), **event}
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001 -- el log nunca debe romper un turno
        logger.debug("voice turn log not written: %s", exc)
