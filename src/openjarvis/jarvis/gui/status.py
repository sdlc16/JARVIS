"""Estado del sistema para la GUI: Ollama, GPU, RAM, puente de Windows, memoria."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any


def ollama_status(host: str, timeout: float = 2.0) -> dict[str, Any]:
    import httpx

    out: dict[str, Any] = {"up": False, "host": host, "loaded": []}
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/ps", timeout=timeout)
        r.raise_for_status()
        out["up"] = True
        for m in r.json().get("models", []):
            out["loaded"].append(
                {
                    "name": m.get("name"),
                    "vram_gb": round(m.get("size_vram", 0) / 1024**3, 2),
                    "size_gb": round(m.get("size", 0) / 1024**3, 2),
                    "expires_at": m.get("expires_at"),
                }
            )
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return out


def gpu_status() -> dict[str, Any]:
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return {"available": False}
    try:
        res = subprocess.run(
            [
                exe,
                "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        name, used, total, util, temp = [
            x.strip() for x in res.stdout.strip().split(",")
        ]

        def num(x: str) -> int:  # nvidia-smi puede dar "54.60" o "[N/A]"
            try:
                return int(float(x))
            except ValueError:
                return 0

        return {
            "available": True,
            "name": name,
            "vram_used_mb": num(used),
            "vram_total_mb": num(total),
            "util_pct": num(util),
            "temp_c": num(temp),
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


def system_status() -> dict[str, Any]:
    try:
        import psutil

        vm = psutil.virtual_memory()
        return {
            "ram_used_gb": round(vm.used / 1024**3, 1),
            "ram_total_gb": round(vm.total / 1024**3, 1),
            "cpu_pct": psutil.cpu_percent(interval=None),
        }
    except Exception:  # noqa: BLE001
        return {}


def bridge_status(client: Any) -> dict[str, Any]:
    try:
        caps = client.capabilities()
        return {
            "up": True,
            "apps": len(caps.get("apps", [])),
            "read_roots": caps.get("read_roots", []),
            "write_roots": caps.get("write_roots", []),
        }
    except Exception as exc:  # noqa: BLE001
        return {"up": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


_real_gpu_status = gpu_status  # (las pruebas sustituyen gpu_status)
