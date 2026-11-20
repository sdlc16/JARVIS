# Instalación de JARVIS en Windows 11 (WSL2 + Ubuntu)

JARVIS es un asistente personal **100 % local** construido sobre [OpenJarvis](https://github.com/open-jarvis/OpenJarvis). Corre en dos procesos:

- **Núcleo** (agente, voz, memoria, interfaz) dentro de **WSL2 / Ubuntu-24.04**, con **Ollama** como único motor de inferencia.
- **Puente nativo de Windows** (`windows_bridge/`, proceso Python de Windows) para ventanas, teclado, archivos y sistema, autenticado con un token cifrado por DPAPI.

No necesita claves de API, cuentas ni servicios de pago. **No hay ningún secreto que configurar** (por eso no existe `.env`).

> Estado de esta guía: los pasos reproducen lo que se hizo en el PC de desarrollo (Windows 11 Pro, RTX 4060 8 GB, 32 GB RAM, i5-12400F). **No se ha probado todavía una instalación desde cero en un equipo limpio.** Cualquier GPU NVIDIA de 8 GB sirve; el modelo por defecto (`qwen3:8b`) ocupa ≈ 5.8 GB de VRAM.

## 1. Requisitos

| Requisito | Detalle |
|---|---|
| Windows 11 con virtualización activada en BIOS/UEFI | necesaria para WSL2 |
| GPU NVIDIA con driver reciente para Windows | `nvidia-smi` debe funcionar también dentro de WSL2 |
| Python 3.10+ en Windows | solo para el puente (`python`/`pythonw` en el PATH) |
| ≈ 20 GB de disco | modelos Ollama + Whisper/Kokoro + entorno |
| Micrófono y altavoces | para la voz (WSLg los expone a Linux) |

## 2. WSL2 y Ubuntu (PowerShell como administrador, una vez)

```powershell
wsl --install -d Ubuntu-24.04
```

Reinicia si lo pide. Dentro de Ubuntu crea tu usuario y activa systemd (`/etc/wsl.conf`):

```ini
[boot]
systemd=true
```

Luego `wsl --shutdown` y vuelve a abrir Ubuntu.

## 3. Dentro de Ubuntu: herramientas

```bash
sudo apt update && sudo apt install -y build-essential git curl pulseaudio-utils libsndfile1
curl -LsSf https://astral.sh/uv/install.sh | sh
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # para compilar la extensión Rust de la memoria
curl -fsSL https://ollama.com/install.sh | sh                   # servicio systemd en 127.0.0.1:11434
ollama pull qwen3:8b
```

## 4. Obtener el código

El proyecto se usa desde una carpeta de Windows accesible desde WSL (así el puente y el núcleo comparten archivos):

```bash
cd /mnt/c/Users/<tu_usuario>/Desktop
git clone https://github.com/sdlc16/JARVIS.git
cd JARVIS
git checkout integration/consolidated-project
```

## 5. Entorno Python (¡importante: `--no-sync` después!)

```bash
source "$HOME/.local/bin/env"
uv sync --extra dev --extra server --extra speech --extra wakeword
uv pip install torch --index-url https://download.pytorch.org/whl/cpu   # Kokoro/Whisper en CPU: no consumen VRAM
uv run maturin develop -m rust/crates/openjarvis-python/Cargo.toml     # extensión Rust (memoria SQLite)
```

> **Nunca vuelvas a ejecutar `uv sync` después**: borra la extensión Rust. Usa siempre `uv run --no-sync ...`. Si ocurre, repite el paso `maturin develop`.

Modelo de la palabra de activación (opcional, gratuito):

```bash
uv run --no-sync python -c "from openwakeword.utils import download_models; download_models(['hey_jarvis'])"
```

## 6. Puente de Windows (una vez)

En PowerShell (Windows), desde la carpeta del proyecto:

```powershell
cd windows_bridge
python -m jarvis_bridge --init-config     # crea %LOCALAPPDATA%\JARVIS\bridge_config.json (carpetas permitidas)
python selftest.py                        # 36 comprobaciones sobre Windows real (opcional)
```

El puente se arranca solo con el script del paso 7. El token se genera y se cifra con DPAPI en `%LOCALAPPDATA%\JARVIS`; nunca se escribe en el repositorio.

## 7. Arrancar

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-shortcuts.ps1   # acceso "JARVIS" en el Escritorio
powershell -ExecutionPolicy Bypass -File scripts\start-jarvis.ps1        # o doble clic en el acceso
```

El script comprueba WSL2 y Ollama, arranca el puente oculto y abre la interfaz (`http://127.0.0.1:8765`, solo local). Para parar: `scripts\stop-jarvis.ps1` (`-Bridge` también detiene el puente). Arranque automático al iniciar sesión (opcional): `install-shortcuts.ps1 -Autostart`.

## 8. Comprobar la instalación

```bash
uv run --no-sync jarvis doctor
uv run --no-sync jarvis test --suite full     # ruff + ~740 pruebas (≈ 2.5 min)
uv run --no-sync jarvis test --env            # Ollama, GPU, audio
```

Manual de uso y sesión de validación con micrófono: `JARVIS_USAGE.md`. Estado real, medidas y límites: `JARVIS_PROGRESS.md`.

## Solución de problemas

| Síntoma | Qué hacer |
|---|---|
| `Connection refused` de audio | WSLg se cuelga a veces: `wsl --shutdown` y reiniciar |
| `openjarvis_rust` no importable | repetir `maturin develop` (paso 5) |
| «Ollama caído» | `systemctl status ollama` dentro de WSL |
| «Sin puente» | volver a ejecutar `start-jarvis.ps1`; log en `%LOCALAPPDATA%\JARVIS\logs\bridge.jsonl` |
