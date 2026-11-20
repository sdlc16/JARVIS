"""Token cifrado (DPAPI), politica de capacidades y validacion de rutas.

Parte "pura": no necesita Windows salvo ``DpapiProtector`` (ctypes/crypt32);
el resto se prueba tambien desde WSL.
"""

from __future__ import annotations

import ctypes
import ipaddress
import json
import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

MAGIC = b"JBR1"

# Capacidades que exigen "confirmed": true (las pone el nucleo tras aprobar).
SENSITIVE = frozenset(
    {
        "input.keyboard",
        "input.mouse",
        "windows.close",
        "files.write",
        "files.trash",
        "system.lock",
        "ui.invoke",
        "ui.read_text",
    }
)

ALL_CAPABILITIES = (
    "ui.inspect",
    "ui.invoke",
    "ui.read_text",
    "apps.open",
    "windows.read",
    "windows.control",
    "windows.close",
    "input.keyboard",
    "input.mouse",
    "files.read",
    "files.write",
    "files.trash",
    "system.info",
    "system.media",
    "system.lock",
)


class BridgeError(Exception):
    """Error de peticion con codigo estable y estado HTTP."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ---------------------------------------------------------------------------
# DPAPI
# ---------------------------------------------------------------------------


class Protector(Protocol):
    def protect(self, data: bytes) -> bytes: ...
    def unprotect(self, blob: bytes) -> bytes: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


class DpapiProtector:
    """Cifra con DPAPI (ligado al usuario de Windows que lo ejecuta)."""

    ENTROPY = b"jarvis-bridge-token-v1"

    def _call(self, fn_name: str, data: bytes) -> bytes:
        if sys.platform != "win32":
            raise RuntimeError("DPAPI solo existe en Windows")
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_string_buffer(data, len(data))
        ent = ctypes.create_string_buffer(self.ENTROPY, len(self.ENTROPY))
        blob_in = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_ent = _DataBlob(
            len(self.ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char))
        )
        blob_out = _DataBlob()
        fn = getattr(crypt32, fn_name)  # mismas posiciones en Protect/Unprotect
        ok = fn(
            ctypes.byref(blob_in),
            None,
            ctypes.byref(blob_ent),
            None,
            None,
            0,
            ctypes.byref(blob_out),
        )
        if not ok:
            raise OSError(f"{fn_name} fallo (error {ctypes.GetLastError()})")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._call("CryptProtectData", data)

    def unprotect(self, blob: bytes) -> bytes:
        return self._call("CryptUnprotectData", blob)


class PlainProtector:
    """SOLO para pruebas fuera de Windows: no cifra (marcado como inseguro)."""

    insecure = True

    def protect(self, data: bytes) -> bytes:
        return b"PLAIN" + data

    def unprotect(self, blob: bytes) -> bytes:
        if not blob.startswith(b"PLAIN"):
            raise ValueError("blob no valido")
        return blob[5:]


class TokenStore:
    """Guarda el token cifrado; nunca en texto plano en disco."""

    def __init__(self, path: Path, protector: Protector | None = None) -> None:
        self.path = Path(path)
        self.protector = protector or DpapiProtector()

    def load_or_create(self) -> str:
        if self.path.exists():
            raw = self.path.read_bytes()
            if not raw.startswith(MAGIC):
                raise ValueError("fichero de token con formato desconocido")
            return self.protector.unprotect(raw[len(MAGIC) :]).decode("ascii")
        return self.rotate()

    def rotate(self) -> str:
        token = secrets.token_urlsafe(32)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(MAGIC + self.protector.protect(token.encode("ascii")))
        return token


# ---------------------------------------------------------------------------
# Politica
# ---------------------------------------------------------------------------


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


@dataclass
class Policy:
    capabilities: dict[str, bool] = field(default_factory=dict)
    read_roots: list[Path] = field(default_factory=list)
    write_roots: list[Path] = field(default_factory=list)
    apps: dict[str, str] = field(default_factory=dict)
    max_read_bytes: int = 2_000_000

    @staticmethod
    def default(home: Path | None = None) -> Policy:
        home = home or Path.home()
        caps = {c: True for c in ALL_CAPABILITIES}
        caps["system.lock"] = False  # bloquear la sesion: desactivado por defecto
        return Policy(
            capabilities=caps,
            read_roots=[home / "Desktop", home / "Documents", home / "Downloads"],
            write_roots=[home / "Documents" / "JARVIS"],
            apps={
                "notepad": "notepad.exe",
                "bloc de notas": "notepad.exe",
                "calculadora": "calc.exe",
                "calculator": "calc.exe",
                "paint": "mspaint.exe",
                "explorador": "explorer.exe",
                "explorer": "explorer.exe",
                "edge": "msedge.exe",
                "navegador": "msedge.exe",
            },
        )

    @staticmethod
    def load(path: Path, home: Path | None = None) -> Policy:
        pol = Policy.default(home)
        if not Path(path).exists():
            return pol
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for k, v in data.get("capabilities", {}).items():
            if k in ALL_CAPABILITIES:
                pol.capabilities[k] = bool(v)
        if "read_roots" in data:
            pol.read_roots = [_expand(p) for p in data["read_roots"]]
        if "write_roots" in data:
            pol.write_roots = [_expand(p) for p in data["write_roots"]]
        if "apps" in data:
            pol.apps = {k.lower(): v for k, v in data["apps"].items()}
        pol.max_read_bytes = int(data.get("max_read_bytes", pol.max_read_bytes))
        return pol

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(
                {
                    "capabilities": self.capabilities,
                    "read_roots": [str(p) for p in self.read_roots],
                    "write_roots": [str(p) for p in self.write_roots],
                    "apps": self.apps,
                    "max_read_bytes": self.max_read_bytes,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    # -- comprobaciones --------------------------------------------------
    def require(self, capability: str, confirmed: bool) -> None:
        if not self.capabilities.get(capability, False):
            raise BridgeError(
                "capability_disabled",
                f"La capacidad '{capability}' esta desactivada en la politica "
                "del puente.",
                403,
            )
        if capability in SENSITIVE and not confirmed:
            raise BridgeError(
                "confirmation_required",
                f"'{capability}' es una accion sensible: requiere confirmacion "
                "del usuario.",
                428,
            )

    def resolve_path(self, raw: str, *, write: bool) -> Path:
        """Valida una ruta: dentro de una raiz permitida y sin escapar por enlaces."""
        if not raw or "\x00" in raw:
            raise BridgeError("bad_path", "Ruta vacia o invalida.")
        roots = self.write_roots if write else self.read_roots + self.write_roots
        try:
            target = Path(raw).expanduser().resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise BridgeError("bad_path", f"Ruta no resoluble: {exc}") from exc
        for root in roots:
            try:
                resolved_root = root.resolve(strict=False)
            except OSError:
                continue
            if _is_within(target, resolved_root):
                return target
        names = ", ".join(str(r) for r in roots) or "(ninguna)"
        raise BridgeError(
            "path_not_allowed",
            f"Ruta fuera de las carpetas permitidas "
            f"({'escritura' if write else 'lectura'}): {names}",
            403,
        )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)  # WindowsPath compara sin distinguir mayusculas
        return True
    except ValueError:
        return False


def origin_allowed(client_ip: str, networks: list[ipaddress._BaseNetwork]) -> bool:
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(ip in net for net in networks)
