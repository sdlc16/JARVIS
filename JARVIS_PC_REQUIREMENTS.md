# JARVIS_PC_REQUIREMENTS.md — Prerrequisitos para tu PC personal

Auditoría cruzando `CLAUDE.md`/`JARVIS_MASTER_PLAN.md` con las dependencias reales del código (`pyproject.toml`, `scripts/install/install.sh`, `src/openjarvis/_rust_bridge.py`, `speech/`). Nada de esto se instaló ni se verificó en esta sesión (sandbox sin Windows/WSL2/GPU) — cada fila dice explícitamente si está verificado por código o pendiente de comprobar en tu PC.

## 0. Resumen — qué de verdad tienes que hacer tú

El instalador oficial de OpenJarvis (`curl -fsSL https://open-jarvis.github.io/OpenJarvis/install.sh | bash`, ejecutado **dentro de WSL2**) automatiza casi todo: instala `uv`, crea el venv, instala Ollama **dentro de WSL2** (no la app nativa de Windows), arranca `ollama serve`, descarga un modelo pequeño, instala el toolchain de Rust y compila la extensión Rust en segundo plano. **Tu checklist manual real, antes de ejecutar ese instalador, son solo 4 cosas** (sección 1). Todo lo demás (sección 2) lo hace el instalador solo — se documenta para que sepas qué está pasando y cómo verificarlo, no para que lo instales a mano.

## 1. Lo que debes preparar tú mismo (antes del instalador)

| Herramienta | Estado | Importancia | Función | Verificación | Versión | Admin | Momento | Dependencias |
|---|---|---|---|---|---|---|---|---|
| Virtualización en BIOS/UEFI (Intel VT-x) | Pendiente de comprobar en tu PC | Obligatorio | WSL2 no arranca sin esto | En Windows: Administrador de tareas → Rendimiento → CPU → "Virtualización: Habilitada" | — | Sí (BIOS) | Antes de instalar WSL2 | Ninguna |
| Windows 10/11 con WSL2 soportado | Pendiente de comprobar | Obligatorio | Base de toda la arquitectura (núcleo OpenJarvis vive en WSL2) | `winver` (Windows 10 build ≥19041, o Windows 11 cualquiera) | Windows 10 19041+ / Windows 11 | No | Antes de WSL2 | Virtualización activa |
| WSL2 instalado + distro Ubuntu | Pendiente de comprobar | Obligatorio | Entorno Linux donde corre el núcleo de OpenJarvis y Ollama | PowerShell admin: `wsl --status` (debe decir versión predeterminada: 2) y `wsl -l -v` (Ubuntu, Running/Stopped, VERSION 2) | Ubuntu 22.04 o 24.04 | **Sí**, para `wsl --install` | Virtualización + Windows compatible |
| Driver NVIDIA de Windows con soporte CUDA/WSL2 | Pendiente de comprobar | Obligatorio | Es lo único que da acceso real a la RTX 3060 desde dentro de WSL2 — **se instala solo en Windows, nunca el driver Linux dentro de WSL2** | En Windows: `nvidia-smi` (debe mostrar la RTX 3060); dentro de WSL2 después de instalarlo: `nvidia-smi` también debe funcionar ahí | Reciente, de la web oficial de NVIDIA, con soporte WSL explícito (confirmado por NVIDIA que GeForce/RTX en modo WDDM soportan CUDA en WSL2 — no hay una cifra de versión mínima que pueda darte como dato verificado por mí, instala el driver "Game Ready" o "Studio" más reciente de nvidia.com) | **Sí** | Antes de instalar OpenJarvis | Ninguna |

**Nada más es obligatorio antes del instalador.** Git, Python, uv, Ollama, el toolchain de Rust — todo eso lo instala `install.sh` dentro de WSL2 automáticamente (ver sección 2).

## 2. Lo que el instalador oficial hace por ti (verificar, no instalar a mano)

| Herramienta | Estado | Importancia | Función | Verificación (dentro de WSL2, tras el instalador) | Versión | Admin | Momento | Dependencias |
|---|---|---|---|---|---|---|---|---|
| `uv` (gestor de paquetes/entornos Python) | Lo instala el script | Obligatorio | Crea el venv, resuelve dependencias de `pyproject.toml` | `uv --version` | Cualquiera reciente | No | Durante el instalador | WSL2 ya funcionando |
| Python | Lo gestiona `uv`, no se instala a mano | Obligatorio | Runtime del núcleo de OpenJarvis | `uv run python --version` dentro del repo | **≥3.10** (`requires-python` en `pyproject.toml`; esta sesión usó 3.13 sin problemas) | No | Durante el instalador | `uv` |
| Git | Normalmente ya viene en Ubuntu, o se instala con `apt` | Obligatorio | Clonar `sdlc16/JARVIS`, control de versiones | `git --version` | Cualquiera reciente | No (dentro de WSL2) | Antes de clonar el repo | WSL2 |
| Ollama (versión **Linux**, dentro de WSL2) | Lo instala el script (`curl -fsSL https://ollama.com/install.sh \| sh`) | Obligatorio | Único motor de inferencia local de JARVIS | `ollama --version`; `ollama ps` (debe mostrar `100%` o la distribución de carga real, ver T0.2); `curl http://localhost:11434/api/tags` | La que instale su propio script, no fijamos versión | **Sí** (el instalador de Ollama pide sudo) | Durante el instalador de OpenJarvis | Driver NVIDIA ya funcionando dentro de WSL2 |
| Rust (`rustup`) + extensión Rust (`maturin`) | Lo instala el script en segundo plano | Obligatorio (el backend Rust es **mandatorio** — `src/openjarvis/_rust_bridge.py` lanza `ImportError` si falta, no hay fallback Python) | Compila `openjarvis_rust`, usado por la memoria SQLite/FTS5 y otras piezas | `jarvis doctor` debe reportar la extensión Rust como disponible; si no, `~/.openjarvis/.scripts/install-rust.sh && ~/.openjarvis/.scripts/build-extension.sh` | La que fije `rustup` (estable) | No (dentro de WSL2) | En segundo plano tras el instalador, puede tardar varios minutos | `uv`/venv ya creado |
| Modelo inicial de Ollama (`qwen3.5:2b`) | Lo descarga el script | Obligatorio para arrancar, **no** es el modelo final de JARVIS | Primer modelo con el que `jarvis` ya responde algo mientras se decide el definitivo | `ollama list` | — | No | Durante el instalador | Ollama ya corriendo |

## 3. Selección del modelo definitivo — no se fija aquí, se mide (T0.3)

Ninguno de los candidatos del catálogo de OpenJarvis (Qwen3 8B, GLM-4.7-Flash, Trinity Mini) declara de forma fiable su VRAM real ni su cuantización (ya auditado en una ronda anterior de este proyecto — `qwen3:8b` no declara `min_vram_gb`; `glm-4.7-flash`/`trinity-mini` declaran `8.0` sin cuantización explícita y con contextos de 128K+ que pueden sumar varios GB de caché KV). **No se recomienda ningún modelo por defecto en este documento** — eso es exactamente lo que mide el procedimiento T0.3 (ver `JARVIS_T0_EXECUTION_GUIDE.md`) con `jarvis bench` en tu RTX 3060 real.

## 4. Programas necesarios en fases posteriores (no para T0.1-T0.3, no instalar todavía)

| Herramienta | Para qué fase | Notas |
|---|---|---|
| Brave Browser (Windows, instalación normal) | Fase 4 (automatización web) | Instalador `.exe` estándar de brave.com; no requiere admin si se instala solo para el usuario actual (modo típico de su instalador). No es prerrequisito de T0.1-T0.3. |
| `playwright` + navegador Chromium bundleado | Fase 4 | Se instala con `uv sync --extra browser` + `playwright install chromium` dentro de WSL2; T4.1 lo adapta para apuntar a Brave real en vez de a este Chromium interno. |
| `sounddevice` + `soundfile` (captura/reproducción de audio) | Fase 2 | **Decisión pendiente, no resuelta todavía** (ver `10_EVALUACION_COMPATIBILIDAD.md` del paquete de investigación): si WSL2/WSLg da acceso fiable al micrófono físico, estas dependencias van dentro de WSL2; si no, la captura de audio real se mueve al puente nativo de Windows. Esto se decide y se prueba en tu PC, no se instala a ciegas ahora. |
| `faster-whisper` (extra `speech`) | Fase 2 | `uv sync --extra speech`. Puede correr en CPU (más lento) o GPU (comparte VRAM con el modelo principal — otro motivo para medir en T0.3 antes de decidir cuánto margen de VRAM dejar). |
| Paquete `kokoro` (TTS) | Fase 2 | **No está en `pyproject.toml` como extra formal** — se instala aparte con `pip install kokoro` dentro del venv de OpenJarvis cuando se llegue a esa tarea. Falla de forma controlada (sin romper nada) si no está instalado, según el propio código de `kokoro_tts.py`. |
| Herramientas del puente nativo de Windows (a definir en T3.1) | Fase 3 | Aún no diseñadas — Python nativo de Windows o .NET, según decida Claude al cerrar T3.1. No instalar nada de esto todavía. |

## 5. Orden correcto de instalación (resumen operativo)

1. Comprobar virtualización activa en BIOS/UEFI.
2. Confirmar versión de Windows compatible con WSL2.
3. Instalar/activar WSL2 + distro Ubuntu (requiere admin).
4. Instalar el driver NVIDIA de Windows con soporte WSL2/CUDA (requiere admin) — **nunca** un driver NVIDIA Linux dentro de WSL2.
5. Verificar `nvidia-smi` tanto en Windows como dentro de WSL2.
6. Ejecutar el instalador oficial de OpenJarvis dentro de WSL2 (no requiere admin dentro de WSL2, salvo el paso interno de instalar Ollama que sí pide sudo).
7. Esperar a que termine la compilación en segundo plano de la extensión Rust (`jarvis doctor` para confirmar).
8. Recién entonces: T0.1-T0.3 (ver guía separada).
9. Brave, Playwright, audio, puente de Windows: en sus fases correspondientes, no antes.

## 6. Requisitos administrativos — resumen

| Paso | ¿Necesita admin de Windows? |
|---|---|
| Activar virtualización en BIOS | Sí (acceso físico a BIOS/UEFI) |
| `wsl --install` / habilitar WSL2 | Sí |
| Instalar driver NVIDIA en Windows | Sí |
| Ejecutar `install.sh` dentro de WSL2 | No, salvo el `sudo` interno para instalar Ollama (pedido por el propio script de Ollama, no por nosotros) |
| Instalar Brave (fase posterior) | Depende del modo de instalación elegido — normalmente no, si se instala solo para el usuario actual |

**Nota sobre la laptop de trabajo con restricciones administrativas:** nada de esta sección 1 se puede ejecutar ahí — es exactamente la razón por la que esta sesión se limitó a documentación. Todo esto queda para tu PC personal.

## 7. Errores habituales y soluciones (documentados, no probados en este sandbox)

| Error | Causa probable | Solución |
|---|---|---|
| `wsl --install` falla o `wsl --status` no muestra versión 2 | Virtualización no activa, o Windows sin la actualización necesaria | Revisar BIOS/UEFI; ejecutar Windows Update |
| `nvidia-smi` funciona en Windows pero no dentro de WSL2 | Se instaló un driver NVIDIA Linux dentro de WSL2 (rompe el passthrough), o el driver de Windows es muy antiguo | Nunca instalar el driver Linux dentro de WSL2; reinstalar/actualizar el driver de Windows |
| `ollama ps` muestra 100% CPU en vez de GPU | El passthrough no está funcionando, o el modelo excede la VRAM disponible | Primero confirmar `nvidia-smi` dentro de WSL2; si eso funciona, probar con un modelo más pequeño |
| `jarvis doctor` dice que la extensión Rust no está disponible | La compilación en segundo plano no terminó o falló | Reintentar manualmente: `~/.openjarvis/.scripts/install-rust.sh && ~/.openjarvis/.scripts/build-extension.sh` |
| `command not found: jarvis` | `~/.local/bin` no está en el PATH | `source ~/.bashrc` o abrir una terminal nueva |

## 8. Lista de comprobación final (antes de pasar a T0.1)

- [ ] Virtualización activa en BIOS/UEFI.
- [ ] `wsl --status` muestra versión predeterminada 2.
- [ ] `wsl -l -v` muestra una distro Ubuntu en estado Running/Stopped, VERSION 2.
- [ ] `nvidia-smi` funciona en Windows (fuera de WSL2) y muestra la RTX 3060.
- [ ] Instalador oficial de OpenJarvis ejecutado dentro de WSL2 sin errores.
- [ ] `jarvis doctor` dentro de WSL2 reporta todo en verde (incluida la extensión Rust).
- [ ] `nvidia-smi` funciona **dentro** de WSL2 (no solo en Windows).
- [ ] `ollama list` muestra al menos el modelo inicial descargado por el instalador.

Cuando los 8 puntos estén marcados, pasa a `JARVIS_T0_EXECUTION_GUIDE.md`.
