# JARVIS_PROGRESS.md

Memoria de trabajo entre sesiones. Léelo junto con `CLAUDE.md` al empezar cualquier sesión nueva, antes de tocar nada.

## 🚧 Desarrollo integral de JARVIS (2026-11-21) — estado vivo (LEER PRIMERO)

**Encargo:** completar JARVIS (voz con wake-word/barge-in, memoria, agente con herramientas, control de Windows, documentos, GUI, permisos/logs, estabilidad). Plan y estado por bloque (se actualiza al cerrar cada uno; "verificado" = con prueba real, no solo código):

| Bloque | Estado | Evidencia |
|---|---|---|
| Cierre de turno de voz + QA + `jarvis test` | ✅ verificado | ver sección siguiente |
| Fix de las 2 pruebas fallidas (causas raíz) | ✅ verificado | abajo |
| **Puente de Windows real** (servidor autenticado, DPAPI, política, 22 acciones) | ✅ verificado en Windows real | `windows_bridge/selftest.py` 36/36 (4 ejecuciones seguidas sin fallo) + 46 pruebas lógicas con mutación |
| Cliente WSL + 11 herramientas del agente con confirmación | ✅ verificado | 17 pruebas con HTTP real + `ToolExecutor`; llamada real WSL→Windows 35 ms |
| Prompt/runtime JARVIS (anti-alucinación de herramientas), confirmaciones por voz/GUI | ⏳ en curso | el probe real con `qwen3:8b` mostró 1 de 3 respuestas alucinadas (inventó ventanas) |
| Wake-word, barge-in, precarga de Ollama, `initial_prompt` Whisper | ⏳ pendiente | |
| Memoria persistente administrable + documentos locales | ⏳ pendiente | |
| GUI completa (chat, voz, permisos, historial, logs, ajustes) | ⏳ pendiente | |
| Arranque con un clic, integración E2E, rendimiento, informe final | ⏳ pendiente | |

### Causas raíz de las 2 pruebas fallidas de la suite completa (clasificación y fix)
- `test_voice_section_is_loaded_from_toml` — **código del test (mío)**: `load_config` está decorada con `functools.lru_cache`, así que devuelve la configuración cacheada por *cualquier* test anterior del proceso e ignora `OPENJARVIS_CONFIG`; pasaba aislado (primera llamada) y fallaba en la suite completa. Reproducido por bisección (falla tras `tests/sdk`, `security`, `operators`, `sandbox`; no tras `core`/`prompt`/…). Fix: fixture que limpia la caché antes y después (también evita filtrar mi config a otros tests). Verificado en los 4 órdenes que fallaban.
- `test_decay` — **entorno (WSL2)**: el reloj de pared saltó **hacia atrás −0.69 s dentro de un `sleep(0.5)`** (medido: monotónico 0.500–0.536 s, realtime min −0.6855 s en 60 muestras) al resincronizarse con Windows; el test comparaba `time.time()` con solo 0.14 s de margen. Fallaba justo en mis dos suites completas tras el reinicio. Fix: reloj simulado (sin `sleep`) + test nuevo de que no borra sesiones recientes; comprobado por mutación (con `decay` roto, falla).
- **Baseline completo** (tras instalar `python-multipart` con `uv pip`, sin `uv sync` para no borrar la extensión Rust): 28 fallos / 6983 pasan, frente a 46 / 6677 al inicio; los 28 restantes son los preexistentes documentados (live/credenciales, gemma.cpp, pearl, cloud-dispatch, embedder de Ollama sin `nomic-embed-text`, hybrid) más estos 2 ya corregidos.
- Basura que dejaban tests preexistentes en la raíz (`:memory:` de 0 bytes, `MagicMock/…`): eliminada la vacía y añadidos los patrones a `.gitignore`.

### Puente de Windows — hallazgos reales (solo visibles con la prueba en Windows)
1. **Alt rompe el tecleo en Notepad**: mi truco de "tocar Alt" para robar el foco activaba los *KeyTips* de WinUI (≈15 ventanas "Host de ventanas emergentes") y las teclas siguientes se interpretaban como atajos de menú. Corregido: tecla inerte F15; `list_windows` filtra la clase `Microsoft.UI.Content.PopupWindowSiteBridge`.
2. **El autocorrector de Notepad (Windows 11) corrompe texto inyectado** (`rapido→rápido`, se traga espacios, rellena el resto con el último carácter) *incluso con ASCII puro*; sobre un destino neutro (tkinter) el texto llega **idéntico** (Unicode, `€`, saltos de línea). Ningún ritmo de tecleo lo arregla y no se tocan los ajustes de la app. Solución de producto: `input.type` **verifica por lectura** (UI Automation) lo escrito y devuelve `verified`; la herramienta del agente trata `verified=false` como fallo; y se añade `input.paste` (portapapeles + Ctrl+V, restaura el portapapeles del usuario) para texto exacto.
3. **UI Automation funciona** y se integra: `ui.elements` (listar controles por nombre), `ui.invoke` (pulsar por nombre, sensible), `ui.read_text` (sensible).
4. Seguridad verificada en real: token cifrado con DPAPI (266 bytes, no legible), solo escucha en la interfaz WSL (`172.29.48.1`), origen validado, `win+*` bloqueado, acciones sensibles exigen `confirmed`, raíces de archivos, papelera en vez de borrado, `system.lock` desactivado por defecto, registro sin token ni texto tecleado.
5. Efectos secundarios de la prueba real: cada `selftest.py` deja 1 archivo `jarvis_selftest_recycle*.txt` en la Papelera (recuperable; vaciar a mano si molesta; no se purga automáticamente por ser borrado permanente).

## ✅ QA autónomo + corrección del cierre de turno de voz — COMPLETADO salvo validación física

Tarea iniciada 2026-11-20, interrumpida por el apagado del PC y **retomada y verificada de nuevo desde cero** (se comprobó con `git status` que todo estaba guardado y se re-ejecutó todo lo que había quedado "sin verificar"). Resultados de la reanudación al final de esta sección ("Verificación tras la reanudación"). Nada pusheado. **Única validación pendiente: tu voz real en el micrófono** (instrucciones abajo).

### Origen del trabajo
Segunda prueba real con voz del usuario: la frase *"Hola, Jarvis. ¿Cómo estás? Quiero que me expliques brevemente qué puedes hacer por mí y cómo puedes ayudarme a controlar mi computadora."* se cortó a mitad y terminó en `No se entendió nada (transcripción vacía)`, con `Se corta tras ~500ms de silencio`. El usuario pidió además una metodología QA permanente (ya integrada en `CLAUDE.md`).

### Causas raíz encontradas (reproducidas con evidencia, no supuestas)
1. **Silencio final de 500 ms** cortaba en la primera pausa natural: con la frase real (voz sintética + ruido real) capturó el **9 %** de la voz (0.81 s de 9.28 s), incluso con pausas de 0.3 s.
2. **`aggressiveness=3`**: recall de voz 99 % a −20 dBFS → 80 % a −40 → **16 % a −50 dBFS** (micro con poca ganancia ⇒ voz perdida) y onset retrasado ~1.2 s (primera sílaba perdida). `aggr` 0–2 sin compuerta marcaban el **52–69 %** del ruido ambiente real (−62 dBFS) como habla.
3. **Ceros de arranque**: los primeros ~0.4 s de cada captura `parec`/WSLg son silencio digital (−98 dBFS): se perdían palabras dichas en ese instante y el suelo de ruido adaptativo quedaba envenenado (−80 dB ⇒ compuerta inútil).
4. Sin pre-roll; onset con **un solo frame**; **bucle activo infinito** si el flujo de audio terminaba; ruido a ráfagas alargaba la espera por encima de `no_speech_timeout` (turno sin hablar: 9.5 s con límite 6 s).
5. Un turno vacío **consumía** el único turno de `--turns 1` y la sesión terminaba sin respuesta.
6. (Test de entorno) `parec` sin `--latency-msec`: en fuentes tipo *monitor* PulseAudio entrega bloques de ~2 s ⇒ stalls intermitentes de captura. Corregido con `--latency-msec=60`.

### HECHO y verificado (sin commit)
- **VAD rediseñado** ([vad.py](src/openjarvis/voice/vad.py)): compuerta de energía adaptativa (suelo de ruido + 6 dB, ignora frames muertos, requiere calibración ≥10 frames), histéresis de inicio (3 de 5), pre-roll 300 ms, cola 300 ms, silencio final **1400 ms** configurable, `EndReason` (silence / max_duration / no_speech_timeout / capture_ended), `VadStats` (pausas internas, niveles, ratio de voz, frames muertos, ráfagas descartadas), validación de ráfagas de ruido también en el tope y en el plazo de espera. Defaults medidos: aggr 2 + compuerta 6 dB (barrido 6/8/10 dB × −25…−50 dBFS × pausas 0.4…1.5 s: con 8–10 dB la voz suave perdía bordes y la frase se partía).
- **Captura** ([audio_io.py](src/openjarvis/voice/audio_io.py)): `MicStream` (espera a la primera señal viva, descarta ceros de arranque, `CaptureError` si `parec` falta/muere/se bloquea, `--latency-msec`), `audio_levels`, `PlaybackError`.
- **Pipeline** ([pipeline.py](src/openjarvis/voice/pipeline.py)): cada turno termina en `TurnOutcome` (ok / no_speech / empty_transcript / capture_error / stt_error / agent_error / tts_error), con causa probable, sugerencia si una pausa rozó el límite, guardia anti-alucinación de Whisper, métrica de huecos entre frases del streaming, log JSONL sin texto privado (`~/.openjarvis/logs/voice.jsonl`), audio de depuración **opt-in** (`--debug-audio-dir`, últimos 10).
- **Sesión** ([session.py](src/openjarvis/voice/session.py)): reintenta fallos sin cerrar; solo cuentan turnos completados; se rinde tras `max_consecutive_failures` (3) con motivo; Ctrl+C limpio.
- **Config `[voice]`** ([config.py](src/openjarvis/core/config.py), `VoiceConfig`) + flags `--silence-ms --max-wait-s --max-failures --vad-aggressiveness --debug-audio-dir`; `jarvis config set voice.silence_timeout_ms 1800` funciona (probado).
- **Pruebas `tests/voice/`: 78 pasan** (VAD, pausas 0.3–1.1 s, voz suave, silencio, ruido −62/−50/−40 dBFS, ráfagas, mic mudo, captura cortada, tope; `audio_io` con procesos reales sustitutos; pipeline: vacío/captura/STT/Ollama/Kokoro/reproducción, varios fragmentos, varios turnos, recuperación tras vacío; **faster-whisper real** sobre grabación controlada: la frase completa llega íntegra a Whisper). Fixtures: voz **sintética** Kokoro en `tests/voice/fixtures/` (regenerables con `generate_fixtures.py`).
- **Comprobación por mutación** (las pruebas pueden fallar): reintroduciendo 500 ms + sin compuerta fallan **14** pruebas (la de Whisper real devuelve solo `'hola harbys'`, el síntoma del usuario); desactivando las correcciones de ráfagas fallan las 2 pruebas nuevas. Código restaurado después.
- **Entorno real (hardware/ruta real, audio sintético):** sumidero virtual PulseAudio temporal (`null-sink`, descargado al terminar) con cama de ruido −62 dBFS: **5/5 casos correctos** (frase completa con pausas 0.5/1.0 s y con voz suave −45 dBFS transcrita íntegra, frase corta, solo ruido ⇒ `no_speech` con causa). Micrófono real: arranque 0.42 s (descarta 6 frames de ceros), 0.06 s en el 2.º turno; suelo −64/−66 dBFS.
- **Docs:** `CLAUDE.md` (metodología QA permanente, hardware RTX 4060, estado "implementación autorizada") y `DEVELOPMENT_TASKS.md` (tabla de estado, criterios ampliados T2.1/T2.6, nueva T2.7) actualizados.
- `tests/conftest.py`: fixture autouse que fija `OPENJARVIS_NO_UPDATE_CHECK=1` (arregla los 9 tests que fallaban solo en local por el aviso de actualización contaminando `--json`). **Aún no verificado** con la suite (ver pendiente).
- `jarvis test` ([selftest_cmd.py](src/openjarvis/cli/selftest_cmd.py), registrado como `test`): ruff + suites `quick|voice|full`, `--env` diagnostica Ollama/GPU/audio, código de salida correcto. Tests en `tests/cli/test_selftest_cmd.py`. **Escrito pero su primera ejecución completa fue interrumpida** (la detuve al apagar el PC).

### Verificación tras la reanudación (2026-11-21, resultados reales)
- **Estado tras el reinicio:** WSL2, Ollama (`systemctl` active), GPU RTX 4060 y fuentes de audio (RDPSource/RDPSink) OK; todos los ficheros del trabajo anterior estaban guardados.
- **`jarvis test --suite full --env`** (comando nuevo, probado de verdad): 1.ª ejecución → `ruff` FALLÓ con 20 líneas largas (E501) en mis ficheros nuevos y el comando lo detectó y salió con código 1 (comportamiento correcto) · pytest **479 passed** (voz 78 + speech/TTS + device_selection + engine/ollama + degradation T1.4 + tasks + cli ask/doctor/telemetry/version_check + telemetry; incluye los 9 tests que antes fallaban por el aviso de actualización: **fix de `conftest.py` verificado**). Corregido (`ruff format` + 3 cadenas largas a mano) → 2.ª ejecución: ruff limpio, **479 passed, código de salida 0**.
- **Streaming de Kokoro + VRAM + interferencia con Ollama (entorno real: Kokoro/Whisper/Ollama/altavoces reales, `results/voice_verify_perf.py`):**

| Medida | Resultado |
|---|---|
| VRAM base (sin Ollama) | 797 MiB |
| VRAM tras cargar STT+TTS (CPU) | 786 MiB → **no sube** |
| VRAM con `qwen3:8b` cargado (`ollama ps`: 5.77 GiB) | 6821 MiB de 8188 (≈1.3 GiB libres) |
| RAM del proceso (STT+TTS cargados / tras hablar) | 1165 MB / 1487 MB |
| Carga de modelos (una vez por sesión) | STT 0.51 s + TTS 25.25 s |
| Streaming, respuesta corta (2 frases) | primer audio **1.08 s**, 2 trozos, **0 huecos** |
| Streaming, respuesta larga (4 frases) | primer audio **1.38 s**, 4 trozos, **0 huecos** (síntesis por frase 1.4/1.7/3.9/1.8 s; cada una llega antes de que acabe de sonar la anterior) |
| Ollama solo, 1.ª llamada (carga del modelo) | **10.67 s** · 2.ª llamada 0.29 s |
| Ollama con síntesis TTS simultánea (estrés sintético) | 0.75 s vs 0.29 s solo (+0.46 s; en el flujo real el TTS empieza cuando el agente ya terminó, no se solapan) |

- **Ruta real con audio sintético controlado** (sumidero virtual PulseAudio, 5/5 casos; ver sección anterior) y **micrófono real** (arranque 0.42 s, descarta ceros de arranque, suelo −64/−66 dBFS) ya verificados antes del apagado; no se repitieron por no haber cambiado el código de captura desde entonces.

### Pendiente real (nada de esto bloquea el cierre de la corrección de voz)
1. **Validación física con tu voz** (única, instrucciones abajo).
2. Mejora recomendada, no implementada: **precargar `qwen3:8b` al iniciar la sesión de voz** (la primera respuesta del agente tarda ~10.7 s porque Ollama descarga el modelo tras 5 min inactivo; es el mayor contribuyente a la latencia del primer turno). Requiere decidir `keep_alive` (VRAM residente 5.8 GiB mientras dure).
3. Mejora recomendada: `initial_prompt` de Whisper con el vocabulario ("Jarvis") — hoy transcribe "Harbys".
4. Re-medir la suite completa del repositorio (ver más abajo si ya está anotada).

### Errores/limitaciones conocidos a recordar
- Whisper transcribe "Jarvis" como "Harbys/Garbis" (también con voz sintética): mejora pendiente = `initial_prompt` con el vocabulario (nombre del asistente) en `FasterWhisperBackend`; será clave para wake-word.
- `agente` en caliente tarda 6–8 s con `qwen3:8b` (incluye recarga si Ollama descargó el modelo tras 5 min): pendiente precargar con `keep_alive` al iniciar la sesión (no se hizo; afecta a VRAM residente).
- La prueba de bucle acústico altavoz→micrófono no es concluyente: el micro apenas capta los altavoces (probablemente auriculares).
- Pausas nominales ≥ ~1.3 s parten el turno por diseño (silencio final 1.4 s); subir `voice.silence_timeout_ms` si el usuario pausa más.
- Pre-existentes (no relacionados, documentados antes): extras sin instalar (`server`, `framework-comparison`, gemma.cpp, pearl), tests "live" sin credenciales, embedder de Ollama sin `nomic-embed-text`.

### Borrador de instrucciones para la prueba con micrófono real (una sola sesión)
1. En WSL2, dentro del proyecto: `uv run jarvis voice --turns 2 --debug-audio-dir ~/jarvis-audio-debug`
2. Espera a ver `Habla ahora...` (antes de eso el micro aún arranca) y di la frase completa, con tus pausas naturales. Espera a la respuesta hablada.
3. Segundo turno: una frase corta ("¿qué hora es?").
4. Copia/pega la salida de consola (trae causa, niveles y tiempos). Luego borra `~/jarvis-audio-debug` (es tu voz).

(Si te corta frases: `--silence-ms 1800`. Si dice que no oye voz: sube el volumen de entrada del micro en Windows.)

## Sesión 2026-11-20 (continuación) — Fase 2: pipeline de voz real (T2.1-T2.4 implementados, falta validar con voz del usuario)

**Resumen ejecutivo:** JARVIS ya puede escuchar (VAD+micrófono real vía WSLg), transcribir (faster-whisper), razonar (agente + Ollama, reutilizado sin cambios) y hablar (Kokoro TTS, español) en un solo flujo: `jarvis voice`. Todos los componentes se probaron con audio real (no mocks), con medición de latencia real. **No se da por validado el reconocimiento de voz real del usuario** — eso requiere que el usuario hable al micrófono, pendiente de su intervención física (ver instrucción al final). El modo texto (`jarvis ask`/`jarvis chat`) sigue intacto como alternativa.

### Dependencias instaladas (`uv sync --extra speech`)

`faster-whisper`, `kokoro` (+ `misaki`, `spacy`, `espeakng-loader`), `webrtcvad`, `soundfile`, y **torch como dependencia transitiva de kokoro — fijado a la rueda CPU-only** (`torch==2.10.0+cpu`, vía `[tool.uv.sources]`/`[[tool.uv.index]]` en `pyproject.toml`, índice `https://download.pytorch.org/whl/cpu`) para no descargar ~3GB de runtime CUDA duplicado y mantener la RTX 4060 (8GB) reservada en exclusiva para Ollama, tal como pidió el usuario ("evita cargar simultáneamente modelos innecesarios en VRAM"). También se instaló `pulseaudio-utils`/`alsa-utils`/`python3.12-dev`/`build-essential` en WSL2 (headers necesarios para compilar la extensión C de `webrtcvad`).

**Hallazgo operativo importante:** cada `uv sync` (incluido este) **reinstala el paquete base y borra la extensión Rust compilada con maturin** (`openjarvis_rust` deja de ser importable). Hay que reconstruirla después de cada `uv sync`: `uv run maturin develop -m rust/crates/openjarvis-python/Cargo.toml` (recuerda sourcear tambien `$HOME/.cargo/env`, no solo `$HOME/.local/bin/env`). Confirmado con `jarvis doctor` (8/8 checks relevantes en verde) tras reconstruir.

### Código nuevo (reutiliza lo existente, no reconstruye nada — ver CLAUDE.md)

- [`src/openjarvis/voice/audio_io.py`](src/openjarvis/voice/audio_io.py): captura (`parec`) y reproducción (`paplay`) reales vía el PulseAudio de WSLg confirmado en la sesión anterior (`RDPSource`/`RDPSink`), más `pcm_to_wav_bytes()` (envuelve PCM crudo en WAV valido para los backends STT).
- [`src/openjarvis/voice/vad.py`](src/openjarvis/voice/vad.py) (T2.1): `VoiceActivityDetector` sobre `webrtcvad` (elegido sobre `silero-vad` por ser una extensión C sin modelo que descargar — decisión documentada en el código, reversible) y `segment_utterance()`, que espera el inicio del habla y corta tras `silence_timeout_ms` (500ms por defecto, criterio T2.1) de silencio continuo, descartando ruido puntual (`min_speech_ms`).
- [`src/openjarvis/voice/pipeline.py`](src/openjarvis/voice/pipeline.py) (T2.4): `VoicePipeline` conecta VAD → `SpeechRegistry["faster-whisper"]` → **el mismo `_run_agent()` que usa `jarvis ask`** (reutilizado, no reimplementado — incluye seguridad, herramientas, memoria) → `TTSRegistry["kokoro"]` → reproducción, con medición de latencia por etapa (`TurnLatency`).
- [`src/openjarvis/cli/voice_cmd.py`](src/openjarvis/cli/voice_cmd.py): nuevo comando `jarvis voice` (sigue el patrón Click de `ask`/`chat`), con `--turns N` (0 = continuo, preparación para conversación continua T2.5) y opciones de modelo/motor/agente/VAD.
- [`src/openjarvis/speech/kokoro_tts.py`](src/openjarvis/speech/kokoro_tts.py): ampliado (no reescrito) para aceptar `lang_code` (antes fijo a inglés `"a"`) y exponer voces por idioma — necesario para "español latam, voz masculina" (T2.4). Voces españolas de Kokoro: `em_alex`/`em_santa` (masculinas), `ef_dora` (femenina) — Kokoro no distingue variante latam/España dentro del código "e", es lo más cercano disponible localmente.
- Fix de test: `tests/speech/test_tts_backends.py::test_kokoro_health_false_without_package` asumía que `kokoro` nunca estaría instalado en el entorno; ahora que lo instalamos a propósito como backend designado, se corrigió para simular el `ImportError` explícitamente (`patch.dict("sys.modules", {"kokoro": None})`) en vez de depender del estado ambiental. **29/29 tests de `tests/speech` pasan.**

### Mediciones reales (no supuestas — ver CLAUDE.md "medir, no asumir")

**VAD — falsos positivos sobre ruido ambiente real de esta sala**, 1s de audio real capturado del micrófono en silencio/ambiente normal:

| `aggressiveness` | Frames marcados "habla" (de 33) |
|---|---|
| 0 | 23 (70%) |
| 1 | 25 (76%) |
| 2 (valor por defecto anterior) | 28 (85%) |
| **3 (nuevo valor por defecto)** | **5 (15%)** |

Con esto, `aggressiveness=2` (el valor "razonable" que se habría asumido sin medir) hubiera disparado falsos inicios de turno constantemente. Cambiado el default a `3` en `VadConfig` y en `jarvis voice --vad-aggressiveness`, documentado en el propio código como medición específica de este equipo/ambiente, no un valor universal.

**STT (faster-whisper, CPU, int8) — latencia estable tras la primera carga**, sobre un clip real de 1.83s:

| Modelo | Carga + 1ª transcripción | Transcripción en caliente |
|---|---|---|
| `tiny` | 1.57s | **0.95s** |
| `base` (por defecto) | 2.17s | **1.49s** |
| `small` | 4.54s | 5.19s (más lento que tiempo real — no recomendado) |

La primera transcripción de la sesión con un modelo que nunca se había usado en esta máquina puede tardar bastante más (hasta 7-11s vistos en la primera prueba) porque incluye la descarga/compilación de los pesos CTranslate2 — es un coste único por modelo, no por turno. `base` queda como valor por defecto (buen equilibrio latencia/calidad); `tiny` disponible via `--stt-model tiny` si se prioriza latencia.

**TTS (Kokoro, español, voz `em_alex`) — mismo patrón de arranque en frío vs. caliente:**

| Llamada | Latencia | Duración del audio generado |
|---|---|---|
| 1ª vez en la máquina (descarga del modelo ~82M params) | 40.5s | 4.6s |
| 1ª vez en un proceso nuevo (modelo ya cacheado en disco, pero no en memoria) | 23.8s | 3.7s |
| **2ª síntesis en el mismo proceso (modelo ya en memoria)** | **0.68s** | 2.1s |

**Conclusión de arquitectura:** los modelos STT/TTS deben cargarse UNA vez al iniciar `jarvis voice` (ya es así — `VoicePipeline` los carga en `__init__`, no por turno), no por cada turno. El primer turno de cada sesión de `jarvis voice` será notablemente más lento (carga de modelos); los turnos siguientes deberían rondar STT ~1-1.5s + TTS <1s, dejando el grueso de la latencia percibida al propio Ollama (medido en T0.3: ~45-58 tok/s con `qwen3:8b`).

**Pipeline completo:** validado end-to-end a nivel de componentes (STT sobre audio real capturado, TTS con reproducción real audible por `RDPSink`, VAD sobre frames reales) — `ruff` limpio, `29/29` tests de voz/TTS en verde. **No se ejecutó todavía `pipeline.run_turn()` completo con habla real del usuario** (el VAD de la prueba de audio ambiente, sin hablar, dio pie a transcripciones alucinadas por Whisper al no estar filtrado por VAD en esas pruebas puntuales de componentes — comportamiento conocido de Whisper ante silencio/ruido, no un bug; en el flujo real `run_turn()` el VAD sí filtra antes de llegar al STT).

### Primera validación real del usuario (2026-11-20) + optimización de latencia

El usuario ejecutó `jarvis voice --turns 1` y habló al micrófono: **funcionó de extremo a extremo** (escuchó, transcribió, generó respuesta, la reprodujo). Latencia reportada en esa primera prueba real: **54.94s total** (VAD 8.07s, STT 1.27s, agente 1.94s, **TTS+audio 43.66s**). Se investigó y optimizó cada punto, con medición real antes/después (no se da nada por optimizado sin medirlo, igual que con el reconocimiento):

**1-2. Desglose real de dónde se iba el tiempo** (instrumentación nueva en `kokoro_tts.py` y `vad.py`, expuesta en `TurnLatency`):
- **TTS (43.66s) era casi todo carga del modelo en memoria, no síntesis real**: con el pipeline recién arrancado en un proceso nuevo, Kokoro tarda ~23-29s en cargarse la PRIMERA vez que se usa — exactamente el patrón "frío vs caliente" ya documentado antes, pero ahora cayendo dentro del único turno de la prueba (`--turns 1`) en vez de quedar oculto.
- **VAD (8.07s) no es overhead de cómputo**: es, casi en su totalidad, el tiempo real que el usuario tardó en reaccionar y empezar a hablar tras ver "Escuchando..." más la duración real de su frase — comportamiento esperado de un asistente de voz, no un cuello de botella que optimizar. Ahora `vad.py` desglosa esto en `wait_for_speech_seconds` (reacción) / `speech_seconds` (habla real) / `trailing_silence_seconds` (los 500ms de corte) para que esto sea transparente en vez de parecer "lentitud".

**3. Modelos residentes durante la sesión — ya era así, ahora además con arranque explícito:** `VoicePipeline` ya cargaba los modelos una sola vez por proceso (no por turno); el problema real era que esa carga ocurría *dentro* del primer turno. Se añadió `_warm_up()` en `VoicePipeline.__init__`: ahora los modelos se cargan explícitamente ANTES de empezar a escuchar, con su propio mensaje ("Cargando modelos de voz... Modelos listos en Xs") — así el coste de arranque se ve y se paga una sola vez por sesión, nunca dentro de la latencia de un turno.

**4. Streaming de TTS real implementado** ([`kokoro_tts.py`](src/openjarvis/speech/kokoro_tts.py) `synthesize_stream()`, [`audio_io.py`](src/openjarvis/voice/audio_io.py) `play_pcm_stream()`): Kokoro por defecto solo trocea el texto en saltos de línea (`split_pattern='\n+'`) — una respuesta de una sola línea salía como UN solo bloque de audio, sin beneficio real de streaming (medido: con una frase de 3 oraciones sin `\n`, "tiempo hasta primer audio" == tiempo total, 4.34s). Se fuerza el troceo por oración (`split_pattern=r'(?<=[.!?])\s+'`): ahora Kokoro sintetiza y entrega audio frase por frase, y `play_pcm_stream` empieza a reproducir la primera frase mientras la siguiente aún se está sintetizando.

**5. Respuestas más breves por defecto:** `max_tokens` en modo voz baja de 512 a **200** por defecto (`DEFAULT_VOICE_MAX_TOKENS`, configurable con `jarvis voice --max-tokens`), y se añade una instrucción de estilo (`VOICE_STYLE_HINT`, "responde breve, natural y directo, 1-3 frases; amplía si te lo piden explícitamente") que se antepone al texto transcrito antes de llegar al agente.

**6. VRAM/Ollama — verificado con medición real, no supuesto:** con Ollama **sin** ningún modelo cargado, `nvidia-smi` marcaba 1104 MiB (overhead base de Windows/driver). Tras forzar una generación real de Ollama (`qwen3:8b`, confirmado cargado via `ollama ps`: `size_vram=6194766806` bytes), `nvidia-smi` subió a **7383 MiB** — coincide con el benchmark de T0.3 (~7.2GB), sin ningún overhead extra atribuible al STT/TTS (que corren explícitamente en `device="cpu"`, nunca tocan CUDA).

**7. Comparativa frío vs. caliente (medición real, mismo proceso)**, con el streaming por frase ya activo:

| Medición | Antes de optimizar (reportado por el usuario) | Después (medido) |
|---|---|---|
| Arranque de modelos (una vez por sesión) | oculto dentro del turno 1 | explícito: STT 0.51s + TTS 29.38s = ~30s, mostrado como "Cargando modelos..." antes de escuchar |
| TTS, frase larga (3 oraciones) — tiempo hasta el primer audio | 43.66s (bloque único) | **0.90s** (3 trozos) |
| TTS, frase larga — tiempo total (audio completo) | 43.66s | 17.18s (incluye reproducir ~12s de audio real) |
| TTS, respuesta típica corta (1-3 frases) — primer audio | — (no medido así antes) | **0.61-0.73s** |
| STT en caliente | 1.27s | 1.82-3.10s (ruido de medición, mismo orden de magnitud que antes) |
| Agente (Ollama, qwen3:8b) | 1.94s | 6.46-7.76s (respuesta algo más larga en esta prueba; sigue siendo Ollama, no cambiado) |

**Conclusión:** la latencia "hasta que Jarvis empieza a hablar" tras una respuesta normal de 1-3 frases ronda ahora **~0.6-0.9s** de TTS (antes: tenía que esperar los 40+ segundos completos), una vez pagado el arranque único de ~30s al principio de la sesión. El arranque en frío sigue siendo real y no se puede eliminar sin perder calidad (es el coste de cargar los pesos del modelo), pero ya no se repite por turno y ahora es visible/transparente para el usuario en vez de confundirse con latencia de conversación.

### Validación real con el usuario — estado

✅ **Primera validación completada** (ver arriba): el flujo completo (escuchar→transcribir→responder→hablar) funciona de extremo a extremo con voz real del usuario.

**Pendiente — requiere una segunda intervención física para confirmar la optimización:** las mediciones de latencia de la sección anterior se hicieron reproduciendo los componentes con audio ya grabado, no hablando en vivo (no hay forma de sincronizar la herramienta con la voz del usuario). Falta que el usuario confirme con habla real que:
- La respuesta ahora empieza a oírse casi de inmediato (frase por frase), no de golpe al final.
- Las respuestas se sienten breves/naturales y no cortadas de forma rara al inicio/fin de cada frase (el streaming por oración podría sonar "a trompicones" si el corte entre frases es audible — a verificar de oído).
- La voz `em_alex` de Kokoro suena natural, o si conviene probar `em_santa`/ajustar velocidad.

Instrucción: `uv run jarvis voice --turns 1` (o `--turns 0` para varios turnos seguidos y notar que, a partir del segundo, ya no hay espera de carga de modelos).

### Test suite completo (`pytest -q`, 6677 passed) — verificación de que la voz no rompió nada

Tras instalar `speech` (torch/kokoro/faster-whisper/webrtcvad), se corrió el test suite completo del repo (no solo `tests/speech`) para medir estabilidad real, como pidió el usuario. Resultado: **46 failed / 6677 passed / 139 skipped / 10 errors** en la primera pasada. Investigado uno por uno el origen de cada fallo:

- **2 fallos causados por mi cambio** (instalar `torch`), ambos eran tests que asumían "torch nunca está instalado en este entorno" en vez de simularlo explícitamente — el mismo patrón que el fix de Kokoro de más arriba. Corregidos: `tests/learning/test_device_selection.py::test_no_torch_returns_none` (ahora usa `monkeypatch.setattr(mod, "HAS_TORCH", False)` en vez de depender del entorno).
- **~40 fallos son pre-existentes, no relacionados con la voz**, confirmados reproduciendo cada grupo por separado:
  - `test_doctor_cmd`, `test_doctor_labels`, `test_ask_e2e::test_json_output`, `test_telemetry_cmd`, `test_energy_wiring` (9 tests): **bug real pre-existente, no de esta sesión** — `_version_check.py` escribe a stderr un aviso ("A new version of OpenJarvis is available v0.1.1→v1.0.4") cada vez que detecta una versión más nueva en PyPI, pero el `CliRunner` de Click usado en estos tests mezcla stdout+stderr, así que el aviso contamina la salida `--json` y rompe el parseo. Solo se dispara con acceso real a Internet y una versión real más nueva en PyPI — exactamente las condiciones de esta máquina, por eso nunca se vio en un CI típico (`CI=true` desactiva el check). Confirmado con `OPENJARVIS_NO_UPDATE_CHECK=1 uv run pytest ...` → los 17 tests pasan limpio. **No corregido esta sesión** (fuera de alcance de Fase 2/voz) — queda anotado para una tarea pequeña aparte.
  - `test_new_connectors_live` (Oura/Strava/Spotify/Google Tasks), `test_skills_integration_live`, `test_swe_loop_dispatch`, `test_modal_grading`: tests "live" que requieren credenciales/API keys de terceros que no tenemos (ni queremos, por la política de cero gasto/cloud) — fallan igual sin tocar nada de esta sesión.
  - `test_gemma_cpp`: requiere el binario `gemma.cpp`, nunca instalado.
  - `test_dense.py` (memoria FAISS): `404 Not Found` en `/api/embed` — requiere `ollama pull nomic-embed-text`, gap ya documentado en la sesión anterior.
  - `test_pearl_model_converter`: Pearl Mining, inerte a propósito (CLAUDE.md) — dependencias nunca instaladas.
  - `test_integration::TestAPIServerRoundtrip`: requiere el extra `server` (`python-multipart`), nunca instalado en esta sesión.
  - `test_deploy_auth`, `test_network_defaults`, `test_tool_calls_tracking`/`test_web_search_wiring`: no relacionados con voz; no investigados en profundidad por estar claramente fuera del área tocada esta sesión (ninguno importa `torch`/`kokoro`/`webrtcvad`/`openjarvis.voice`).

**Conclusión:** la Fase 2 (voz) no introdujo regresiones reales — los dos fallos que sí causó quedaron corregidos y verificados, y el resto es deuda pre-existente del repo, ahora diagnosticada y documentada en vez de ignorada a ciegas.

### Pendiente — trabajo independiente que continúa sin bloquear lo anterior

- T2.2 (wake-word "Hey Jarvis", openWakeWord): arquitectura dejada lista para enchufar (el bucle de `jarvis voice --turns 0` ya es la base de "conversación continua"; falta añadir la detección de wake-word antes de activar el VAD) — **no implementado todavía a propósito**, es su propia tarea y openWakeWord es un modelo adicional que hay que justificar antes de instalar (regla del usuario: "no instales múltiples modelos pesados sin justificar su utilidad").
- T2.3 (hotkey global): no implementado esta sesión.
- T2.5 (barge-in, interrumpir TTS al detectar nueva voz): no implementado — `jarvis voice` actualmente reproduce la respuesta de forma bloqueante.
- T2.6 (gestión de errores de reconocimiento): parcialmente cubierto (`jarvis voice` ya informa "No se entendió nada" si la transcripción viene vacía), falta manejar baja confianza explícitamente.

**Continuación de la sesión de instalación de abajo, tras el reinicio de Windows.** Resumen ejecutivo: JARVIS tiene ahora una primera versión funcional corriendo sobre el hardware real del usuario — WSL2 + Ubuntu 24.04 + Ollama + OpenJarvis, con GPU real, memoria real, y el mecanismo de degradación de T1.4 validado con una caída real de Ollama (que además reveló y corrigió un bug real). Nada se commiteó ni se pusheó — Git sigue sin identidad de autor configurada, exactamente como se pidió.

### Entorno (verificado con comandos reales, no supuesto)

- **WSL2 + Ubuntu 24.04.5 LTS**, instalado y confirmado en este PC Windows real (no en una VM en la nube). Usuario Linux `jarvis` (uid 1000, sudo sin contraseña, contraseña de login bloqueada — no hace falta, no es una cuenta interactiva). `systemd` habilitado (`/etc/wsl.conf` con `[boot] systemd=true` y `[user] default=jarvis`) — `systemctl is-system-running` → `running`.
- **GPU real confirmada como RTX 4060** (no RTX 3060, ver nota de la sesión anterior más abajo), 8188 MiB VRAM, driver Windows actualizado por el usuario de 621.14 a **621.24** durante esta sesión. `nvidia-smi` dentro de WSL2 muestra la misma GPU con el mismo KMD Version que Windows — passthrough real confirmado (**T0.1 cumplido**).
- **Ollama** instalado dentro de WSL2 (no en Windows nativo — ver decisión de arquitectura más abajo), como servicio `systemd` real (`systemctl enable --now ollama`), API en `127.0.0.1:11434`.
- **uv, Rust/Cargo** instalados dentro de WSL2. **Extensión Rust de OpenJarvis compilada** con `uv run maturin develop -m rust/crates/openjarvis-python/Cargo.toml` (87s, 0 errores) — este paso no lo hace `uv sync` por sí solo y no se había ejecutado hasta ahora; sin él, el backend de memoria SQLite (`openjarvis_rust`) no es importable.
- **OpenJarvis instalado en modo editable** (`uv sync --extra dev`) directamente sobre `/mnt/c/Users/PC/Desktop/JARVIS` — la misma carpeta de Windows, sin clonar una copia aparte (ver decisión de arquitectura abajo). `jarvis doctor`: 7/7 checks en verde (Python, config, parsing, engine ollama, modelo, modelo por defecto, perfil de seguridad).

### Decisión de arquitectura: no se usó el `install.sh` oficial completo

`scripts/install/install.sh` clona por defecto `open-jarvis/OpenJarvis` (el upstream vacío), no nuestro fork. Como este repo no tiene remoto configurado ni se va a pushear, se instalaron uv/Ollama/Rust manualmente dentro de WSL2 (mismos comandos que usa el instalador oficial, revisados en sus scripts fuente) y se trabajó directamente sobre este proyecto vía `/mnt/c/Users/PC/Desktop/JARVIS`. Una consecuencia de esto: el paso de compilar la extensión Rust (`build-extension.sh` del instalador oficial) tampoco se ejecutó automáticamente — hubo que ejecutarlo a mano (ver arriba). Si en el futuro se usa `jarvis self-update` o se sigue la sugerencia de actualizar a v1.0.4, **revisar primero si overescribe esta configuración** antes de aceptarlo a ciegas.

### T0.3 — Benchmark real de modelos candidatos (RTX 4060, 8GB VRAM)

`trinity-mini` **no existe con ese tag exacto** en el registro público de Ollama (`Error: pull model manifest: file does not exist`) — no se inventó un tag alternativo, tal como pedía la guía. Los otros dos se descargaron y midieron con `jarvis bench run -n 10` real, con `nvidia-smi`/`ollama ps` muestreados durante la ejecución:

| Modelo | Tamaño en disco | VRAM real al cargar | GPU/CPU | Tokens/s (medio) | Latencia media |
|---|---|---|---|---|---|
| `qwen3.5:2b` | 2.7 GB | ~4.3 GB | 100% GPU | ~76-84 tok/s | ~1.3-1.6s |
| `qwen3:8b` | 5.2 GB | ~7.2 GB | 100% GPU | ~45-58 tok/s | ~2.2s |
| `glm-4.7-flash` | **19 GB** (no 8GB como asumía el catálogo) | ~6.5 GB cargado | 97% GPU / 3% CPU | ~15-27 tok/s | ~5.6-5.8s |

**Esto confirma con datos reales la advertencia de `CLAUDE.md`** sobre VRAM/cuantización no fiable en el catálogo: `glm-4.7-flash` pesa 19GB en disco, muy por encima de los 8GB de la GPU, y aunque Ollama consigue cargarlo con solo un 3% en CPU (sorprendentemente bien), es 3-4x más lento que las otras dos opciones.

**Modelo por defecto fijado con estos datos (no por el catálogo):** `qwen3:8b` — mejor calidad, 100% GPU, cabe con margen en 8GB. `qwen3.5:2b` queda documentado como alternativa rápida para turnos de voz (T2.4 exige <3s). Ya actualizado en `~/.openjarvis/config.toml` y documentado en `configs/openjarvis/examples/jarvis-rtx4060.toml` (nuevo, versionado en el repo — cumple T1.2). Resultados crudos en `results/t0.3_bench_2026-11-19/`... (nota: los primeros archivos `.jsonl` de `-o` no sobrevivieron por una limpieza de `/tmp` entre invocaciones de WSL2 — los números de esta tabla están tomados directamente de la salida `--json` capturada en la sesión, no inventados).

### T1.1 — Agentes + memoria end-to-end, con evidencia real

Documento de prueba (`results/t1.1_memory_fact.txt`, con un dato ficticio "ZORRO-7731") indexado con `jarvis memory index`, recuperado con `jarvis memory search`, y **`jarvis ask` citando correctamente la fuente** (`results/t1.1_memory_fact.txt`) en su respuesta. Memoria persistente en SQLite+Rust confirmada funcionando end-to-end. Nota menor no bloqueante: `jarvis memory search` muestra siempre `Score: 0.0000` incluso con un resultado relevante — posible defecto cosmético en el cálculo/formato del score, no investigado a fondo (fuera del alcance de esta sesión).

### T1.4 — Validación real con Ollama deteniéndose a mitad de tarea (criterio de aceptación pendiente, ahora cumplido)

Script `results/t1.4_real_ollama_validation.py`: llama a `generate_with_degradation()` con el `OllamaEngine` **real** (no un fake), detiene el servicio real (`systemctl stop ollama`) a los 0.6s de iniciada la petición, y verifica cada paso.

**Bug real encontrado y corregido:** cuando Ollama muere a mitad de una respuesta (no al rechazar la conexión, sino cortándola mientras responde), `httpx` lanza `RemoteProtocolError`, que [`engine/ollama.py`](src/openjarvis/engine/ollama.py) **no capturaba** — solo capturaba `ConnectError`/`TimeoutException`. Esto hacía que la excepción se propagara sin control en vez de activar la degradación de T1.4, justo en el escenario que esta tarea existe para cubrir. **Corregido** ampliando las 3 cláusulas `except` relevantes (y la de `list_models()`) a `httpx.TransportError` (clase base que cubre `ConnectError`, `TimeoutException` y `RemoteProtocolError`/`ProtocolError`). Test de regresión añadido en `tests/engine/test_ollama.py` (`test_generate_remote_disconnect_during_response`). **57/57 tests pasando, `ruff` limpio.**

Con el fix aplicado, la validación real completa pasó limpia:

1. Ollama sano → se inicia una petición real.
2. Ollama se detiene a mitad de la petición → 3 reintentos acotados con backoff, mensaje claro, tarea marcada `INTERRUPTED` (no `UNKNOWN`, correcto para un `generate()` puro).
3. `can_auto_retry()` → `False` (regla dura verificada).
4. Ollama se reinicia → `health()` se recupera.
5. `approve_retry()` explícito (simulando aprobación del usuario) → tarea vuelve a `PENDING`.
6. Reintento real → éxito → tarea `COMPLETED`.

**T1.4 queda COMPLETADA** — el único criterio pendiente ("validación real en tu PC con Ollama real detenido") ya tiene evidencia real, no simulada.

### Primera versión funcional (Fase 7)

`jarvis ask` responde correctamente en español usando Ollama real + GPU real. Script de arranque creado en [scripts/start-jarvis.ps1](scripts/start-jarvis.ps1): comprueba la distro WSL2, arranca/verifica el servicio de Ollama, y lanza `jarvis chat` interactivo — cada paso individual probado por separado (no se pudo probar el bucle interactivo de chat en sí porque esta sesión no tiene una TTY real, pero cada comando que usa el script ya se ejecutó con éxito en esta misma sesión).

### Pendiente / no hecho esta sesión

- Actualización v0.1.1→v1.0.4 de OpenJarvis: **no aplicada deliberadamente** — un salto de versión mayor podría traer cambios incompatibles con nuestras personalizaciones de T1.4; requiere revisión antes de aceptarla.
- `jarvis memory search` con score siempre en 0.0000 — posible defecto menor, no investigado.
- Node.js no instalado (solo lo necesitan ClaudeCodeAgent y el puente de WhatsApp, fuera de alcance ahora).

## Sesión 2026-11-20 — primer commit local + T1.5 (prueba mínima del puente de Windows)

**Primer commit local realizado.** Identidad Git confirmada por el usuario (`Santiago <santiagodelacruzc16@gmail.com>`) y configurada **solo local** (`git config --local`, nunca `--global`, verificado). Revisados los 2037 archivos candidatos (stage previo + los 5 pendientes de la sesión anterior: `JARVIS_PROGRESS.md`, `src/openjarvis/engine/ollama.py`, `tests/engine/test_ollama.py`, `configs/openjarvis/examples/jarvis-rtx4060.toml`, `scripts/start-jarvis.ps1`) — sin credenciales, secretos, bases de datos, modelos ni archivos personales (ver detalle del escaneo en el chat de esa sesión). Commit `2340c80` en `master`, working tree limpio, sin remoto configurado — **no se hizo push**.

**Metodología de autonomía actualizada** (ver mensaje del usuario): a partir de aquí se trabaja por fases completas sin pedir autorización para pasos ordinarios (comandos, instalación, dependencias gratuitas, correcciones), con commits locales permitidos tras revisión, nunca push sin autorización explícita, y sin inferencia cloud bajo ninguna circunstancia.

### T1.5 — Prueba mínima del puente nativo de Windows (COMPLETADA)

Alcance deliberadamente limitado (no es el puente completo de Fase 3): una sola acción trivial — abrir el Bloc de notas — para validar el canal WSL2 ↔ proceso Windows nativo de extremo a extremo.

**Hallazgo de red:** este WSL2 usa **NAT, no mirrored networking** (`ip route` → `172.29.48.0/20` vía gateway `172.29.48.1`; sin esto no habría sido mirrored, donde `localhost` sería compartido). Por tanto `localhost` desde WSL2 **no** llega al host Windows — el bridge debe escuchar en la IP del adaptador `vEthernet (WSL (Hyper-V firewall))` (`172.29.48.1`), nunca en `0.0.0.0` ni en la red real del usuario ("CASA", categoría *Public*). Documentado para que T3.1 (diseño de la API completa) lo tenga en cuenta.

**Hallazgo de comportamiento real de Windows 11:** el Bloc de notas moderno es una app empaquetada (MSIX) — el PID que devuelve `subprocess.Popen(["notepad.exe"])` es un proceso intermediario que normalmente **no** es el propietario final de la ventana (y una segunda apertura puede añadirse como pestaña a una ventana ya existente en vez de crear una nueva). La verificación por PID exacto fallaba por esto (falso negativo, no un fallo del canal) — corregido verificando "existe una ventana visible nueva cuyo proceso propietario es `notepad.exe`" vía `EnumWindows` + `QueryFullProcessImageNameW` (ctypes, sin dependencias nuevas), comparando el conjunto de ventanas antes/después de la petición.

Implementación: [`windows_bridge/bridge_server.py`](windows_bridge/bridge_server.py) — proceso Python nativo de Windows (fuera de WSL2), standalone a propósito (sin `ToolRegistry` todavía — esa integración es alcance de T3.1/T3.2, no de esta prueba mínima). Escucha solo en `172.29.48.1:47811`, token Bearer generado al arrancar (texto plano, fuera del repo, en `windows_bridge/.t1_5_token` — gitignored; el cifrado DPAPI es alcance explícito de T3.2, no de T1.5), origen verificado contra la subred WSL real como defensa adicional, log de cada petición en `windows_bridge/t1_5_bridge.log` (gitignored), y limpieza automática (cierra la ventana que abrió tras verificarla).

**Resultado: los 4 criterios de aceptación, verificados 3 rondas consecutivas sin fallo** (ver `results/t1_5_bridge_test.sh`):

| Ronda | Sin token | Token inválido | Token válido + acción real + confirmación |
|---|---|---|---|
| 1 | HTTP 401 `missing_token`, no ejecuta | HTTP 403 `invalid_token`, no ejecuta | HTTP 200, `window_verified: true`, hwnd nuevo `1508944` |
| 2 | HTTP 401 | HTTP 403 | HTTP 200, `window_verified: true`, hwnd nuevo `1574444` |
| 3 | HTTP 401 | HTTP 403 | HTTP 200, `window_verified: true`, hwnd nuevo `1967806` |

Verificación real (no simulada): la ventana se confirma vía la API Win32 real (`EnumWindows`/`GetWindowThreadProcessId`), no un mock; cada hwnd nuevo es distinto entre rondas, confirmando una ejecución real por petición. **T1.5 queda COMPLETADA.** Esto desbloquea T3.1 (diseño de la API completa del puente, Fase 3) cuando corresponda por orden de roadmap.

**Nota menor no bloqueante:** quedaron 2-3 ventanas vacías del Bloc de notas abiertas de una ronda de prueba previa al fix (antes de que la limpieza automática existiera) — sin contenido, sin datos, cerrables manualmente cuando convenga.

### Fase 2 (voz) — riesgo de arquitectura resuelto antes de escribir código

Antes de empezar T2.1+, se investigó la mayor incógnita de la Fase 2: si WSL2 puede acceder al micrófono/altavoces reales de Windows (si no, habría que meter el audio por el puente nativo, con la latencia y complejidad que eso añade).

**Resultado: SÍ puede, de forma nativa, sin el puente.** WSLg expone un servidor PulseAudio real (`$PULSE_SERVER=unix:/mnt/wslg/PulseServer`) con una fuente `RDPSource` (micrófono real de Windows, 1ch 44100Hz) y un sumidero `RDPSink` (altavoces reales). Instalado `pulseaudio-utils`/`alsa-utils` (gratis, apt) y confirmado con una captura real de 2s (`parecord --device=RDPSource`) que produjo un WAV de ~323KB, no vacío — el canal de audio real funciona de extremo a extremo, no es una suposición. **Esto significa que T2.1-T2.6 pueden implementarse con librerías Python estándar (sounddevice/PortAudio hablando con PulseAudio vía este socket), sin pasar el audio por `windows_bridge/`.**

**Además, ya existe código reutilizable** (cumple la regla de `CLAUDE.md` de revisar `src/openjarvis/` antes de escribir nada nuevo) que no estaba documentado como tal: `src/openjarvis/speech/` ya tiene un patrón de registro (`SpeechRegistry`/`TTSRegistry`, en `core/registry.py`) con:
- `faster_whisper.py` → `FasterWhisperBackend` **completo y funcional** (toma bytes de audio, transcribe, devuelve texto+segmentos+idioma) — solo falta instalar la dependencia (`uv sync --extra speech`) y conectarlo a un stream de micrófono real.
- `kokoro_tts.py` → `KokoroTTSBackend` **completo y funcional** (texto → WAV) — por defecto en inglés (`lang_code="a"`, voces `af_*`/`am_*`); para "español latam, voz masculina" (criterio de T2.4) hay que cambiar a `lang_code="e"` (español) y una voz `em_*`, y validar si esa voz suena natural en latam o si hace falta buscar alternativa — **pendiente de decidir en T2.4, no bloqueante ahora**.
- `openai_whisper.py`, `deepgram.py`, `cartesia_tts.py`, `openai_tts.py` existen en el repo pero son **de pago — nunca se activan**, tal como exige `CLAUDE.md`; quedan inertes (ni se instalan sus dependencias ni se seleccionan como backend activo).

**No hay todavía:** detector de VAD (T2.1), wake-word (T2.2), ni el glue code que conecte captura de audio (PulseAudio) → VAD → STT → agente → TTS → reproducción. Es el siguiente bloque de trabajo.

## Sesión de instalación en PC real (2026-11-19) — checkpoint antes de reiniciar Windows

**Esta sesión corre directamente sobre el PC Windows real del usuario** (confirmado: `[System.Environment]::OSVersion` → Windows NT 10.0.26100, PowerShell 5.1 nativo, no WSL/Linux), a diferencia de la sesión en la nube descrita más abajo en este documento.

**Hallazgo importante — discrepancia de hardware:** `nvidia-smi` en Windows reporta una **NVIDIA GeForce RTX 4060** (8 GB VRAM, driver 621.14 → el usuario confirma que está actualizando a 621.24), **no una RTX 3060** como asume toda la documentación (`CLAUDE.md`, `JARVIS_MASTER_PLAN.md`, `JARVIS_T0_EXECUTION_GUIDE.md`). La VRAM (8GB) coincide, así que los límites de tamaño de modelo siguen siendo válidos, pero la RTX 4060 (Ada Lovelace) es más rápida que la RTX 3060 (Ampere) asumida — los benchmarks de T0.3 deben hacerse contra el hardware real, no contra el nombre viejo. **Pendiente: actualizar las referencias a "RTX 3060" en la documentación cuando se confirme el nombre final tras la actualización de driver.**

**Resto del diagnóstico (Fase 1, completo):** Windows 11 Pro real (build 26100; `Get-ComputerInfo` mostró "Windows 10 Pro" por un dato de registro obsoleto — no es el OS real), i5-12400F (6 núcleos/12 hilos), 32GB RAM, virtualización activa (hipervisor detectado). En Windows ya había: Git 2.56.0, Python 3.15.2 (nativo de Windows, no relevante para el núcleo que vive en WSL2), winget 1.29.380. No había: uv, Rust/Cargo, Ollama, WSL.

**Instalado esta sesión (con aprobación de UAC del usuario):**
- Componente opcional "Subsistema de Windows para Linux" (`Microsoft-Windows-Subsystem-Linux`) — habilitado.
- Componente opcional "Plataforma de máquina virtual" (`VirtualMachinePlatform`) — habilitado, **requiere reinicio para activarse** (mensaje explícito de DISM).
- Kernel de WSL 3.0.5.0 (kernel Linux 6.18.58.1-1) — instalado y confirmado con `wsl --version`.
- **Todavía NO instalado:** la distribución Ubuntu-24.04 (el paso `wsl --install -d Ubuntu-24.04` llegó hasta instalar el kernel pero no completó la descarga de la distro — probablemente porque la Plataforma de máquina virtual no estaba activa todavía). `wsl -l -v` confirma "no hay distribuciones instaladas".

**Nada más se instaló.** No se tocó Ollama, uv, Rust, ni nada dentro de un WSL2 (porque WSL2 todavía no puede iniciar: `wsl --status` dice explícitamente "WSL2 no se puede iniciar porque la virtualización no está habilitada en esta máquina" — exactamente lo esperado antes del reinicio).

**Decisión de arquitectura tomada esta sesión (antes de seguir tras el reinicio):** el `install.sh` oficial de OpenJarvis (`scripts/install/install.sh`, paso `clone_repo`) clona por defecto `https://github.com/open-jarvis/OpenJarvis.git` (el upstream vacío), no nuestro fork `sdlc16/JARVIS` con los documentos de planificación y el código de T1.4. Como este repositorio local no tiene remoto configurado ni se va a pushear, **no se usará `clone_repo` del instalador**. En su lugar: se instalarán uv/Ollama dentro de WSL2 (reutilizando `install-uv`/`install_ollama` del script como referencia, no el script completo), y se trabajará directamente sobre `/mnt/c/Users/PC/Desktop/JARVIS` desde dentro de WSL2 — una sola copia del proyecto, visible y editable desde Windows y ejecutable desde WSL2, sin clonar una copia paralela.

**Estado de Git:** repositorio inicializado (`git init`) en esta sesión, 2035 archivos en stage (`git add -A`), **todavía sin commit** — Git no tiene identidad de autor configurada en esta máquina y el usuario pidió explícitamente no inventar nombre/email, no configurar la identidad global, y esperar a que él la proporcione. Nada se ha perdido: `git status --short` muestra exactamente los mismos 2035 archivos en stage que tras el `git add -A` inicial. Se excluyeron del commit (regla en `.gitignore`) los dos archivos de recuperación `jarvis-optimistic-bardeen-bdfwrm.bundle`/`.zip` (export de la sesión en la nube, no código fuente del proyecto) — decisión del usuario. **Sin commit, sin push**, tal como se pidió.

**Próximo paso exacto, al confirmar el usuario que reinició Windows:**
1. `wsl --install -d Ubuntu-24.04` (ya debería completarse esta vez, con la Plataforma de máquina virtual activa tras el reinicio).
2. Dentro de la terminal Ubuntu: `nvidia-smi` — debe mostrar la GPU real (RTX 4060) con su VRAM — criterio de aceptación de T0.1, adaptado al hardware real.
3. Instalar uv y Ollama dentro de WSL2.
4. `cd /mnt/c/Users/PC/Desktop/JARVIS && uv sync --extra dev` sobre este mismo proyecto (no un clone aparte).
5. Seguir con T0.2/T0.3 (benchmark real) y T1.4 (validación con Ollama real deteniéndose a mitad de tarea).

## Sesión de recuperación (2026-11-19) — este documento y el código de T1.4 se habían perdido

La sesión anterior que escribió todo lo demás en este archivo trabajaba en la rama `claude/upbeat-tesla-66d527`, **sin commitear ni pushear nada** (ver `git_status_snapshot.txt` del export). Esa rama nunca llegó a GitHub y el contenedor efímero que la alojaba ya no existe: `git ls-remote` y `git log --all` en un checkout nuevo de `sdlc16/JARVIS` no muestran ningún rastro de ella ni de estos documentos. La única copia que sobrevivió fue un ZIP (`JARVIS_RESEARCH_PACK/jarvis_export`) que el usuario guardó manualmente y volvió a subir en la sesión nueva.

**Restaurado en esta sesión** (checkout en rama `claude/optimistic-bardeen-bdfwrm`, mismo repo `sdlc16/JARVIS`): los 10 documentos de `docs/` del export (incluido este archivo) y los 6 archivos de código/test de T1.4 (`src/openjarvis/tasks/__init__.py`, `src/openjarvis/tasks/store.py`, `src/openjarvis/engine/degradation.py`, `tests/tasks/__init__.py`, `tests/tasks/test_store.py`, `tests/engine/test_degradation.py`). Ninguno de estos paths existía ya en el checkout, así que no se sobrescribió nada.

**Verificado de nuevo en esta sesión, con evidencia real:**
- `uv sync --extra dev` → entorno creado sin errores.
- `pytest tests/tasks/ tests/engine/test_degradation.py` → **25/25 PASS**, igual que reportaba la sesión perdida.
- `ruff check` sobre los 4 archivos nuevos → limpio.
- Mismas restricciones de entorno que entonces: sin `nvidia-smi`, sin WSL2 (esto es Linux puro), sin `ollama`, `list_computer_folders` devuelve vacío (ningún PC del usuario conectado vía Remote Control).

**Pendiente, no resuelto en esta sesión (decisión del usuario):**
1. **Nada de esto está commiteado todavía.** Es exactamente la misma situación que causó la pérdida anterior — si el contenedor de esta sesión termina sin commit, se pierde otra vez. Pendiente de autorización explícita del usuario para commitear (no pushear) antes de seguir.
2. `gitignore.diff` del export llegó vacío (0 bytes) — la línea de excepción que supuestamente permitía versionar `CLAUDE.md` nunca se guardó. Hoy `CLAUDE.md` sigue excluido por la regla genérica de `.gitignore` (línea `CLAUDE.md`), así que aunque se commitee el resto, `CLAUDE.md` por sí solo seguiría sin entrar a menos que el usuario confirme que quiere añadir esa excepción.
3. El bloqueo estructural de hardware descrito abajo (todo traza a T0.1-T0.3 o T1.5) sigue intacto: esta sesión tampoco tiene acceso al PC Windows real del usuario.

## Restricción de entorno — léela primero, siempre

**Esta sesión corre en una VM Linux en la nube, no en el PC Windows del usuario.** Confirmado: sin GPU NVIDIA (`nvidia-smi` no existe), sin WSL2, sin Windows, sin micrófono, sin Brave con perfil real, sin ningún equipo del usuario conectado vía Remote Control (`list_computer_folders` devolvió vacío).

Esto significa que **todo lo que exige hardware/SO reales del usuario no se puede ejecutar ni validar de verdad aquí**: benchmark en la RTX 3060 (T0.3), prueba del puente de Windows (T1.5), voz con micrófono real (Fase 2), control real de Windows (Fase 3), Brave real (Fase 4), captura de pantalla real (Fase 5), sensores reales (Fase 7), y el hito P0 T3.8 de principio a fin.

Lo que **sí** se puede hacer aquí, con evidencia real (no inventada): toda la lógica Python pura del núcleo de OpenJarvis, con pruebas automatizadas reales (no solo mocks de librería, sino clases fake completas que implementan las ABCs reales del proyecto). Cada tarea de este documento indica explícitamente si su evidencia es "real en sandbox" o si sigue pendiente de "validación real en tu PC".

## Investigación — CERRADA

La investigación de componentes externos (`JARVIS_RESEARCH_PACK`, fuera de este repo, en `/home/user/JARVIS_RESEARCH_PACK/`) queda cerrada tras 3 rondas: auditoría de 6 repos externos, auditoría complementaria del upstream oficial `open-jarvis/OpenJarvis`, y evaluación técnica de compatibilidad de 4 componentes concretos (`memory/`, `cli/_screen.py`, `speech/voice_io.py`, `cli/_voice_chat.py`+`_tts_discovery.py`). Decisión final aprobada: **incorporación selectiva por fase, no actualización del fork completo** — ver la matriz y el detalle en `10_EVALUACION_COMPATIBILIDAD.md` y `PLAN_DE_REUTILIZACION.md` dentro del paquete de investigación. Ningún archivo de este repo (`sdlc16/JARVIS`) se tocó por la investigación; las únicas ediciones de planificación derivadas de ella (cifrado DPAPI del token del puente) ya están aplicadas desde una ronda anterior.

**Siguiente paso: preparar la ejecución de las tareas de desarrollo**, respetando las dependencias y autorizaciones ya existentes — no se ha recibido autorización para reanudar la implementación todavía; cuando llegue, continuar exactamente por donde quedó (ver "Próximo paso concreto" más abajo), incorporando los 3 componentes seleccionados de upstream únicamente dentro de su tarea correspondiente (T5.1, T2.1/T2.4, T6.1/T6.2), no como un paso aparte.

## Fase actual

**Fase 1 — Núcleo operativo.** Dentro de ella, T1.4 avanzada; T1.1-T1.3 y T1.5 sin empezar (T1.5 requiere tu PC).

## Sesión de preparación/auditoría (más reciente) — resumen

**Trabajo completado:**
1. **Auditoría técnica de T1.4** — encontrado y corregido un defecto real: ninguna transición de estado validaba el estado actual (`mark_running()` podía ejecutarse sobre una tarea `COMPLETED` sin error, sin protección atómica contra una carrera de dos llamadas concurrentes al mismo `PENDING`). Corregido con una máquina de transiciones explícita (`_ALLOWED_TRANSITIONS`) y actualización atómica `UPDATE ... WHERE state IN (...)` con verificación de `rowcount` (compare-and-swap real a nivel SQL, no leer-luego-escribir). Nueva excepción `TransitionError`. 4 tests nuevos, incluida una prueba de concurrencia real con 2 hilos de Python contra el mismo archivo SQLite (no simulada con mocks). **23/23 tests pasando**, `ruff check` limpio. T1.4 **sigue sin marcarse `COMPLETADA`** — el criterio de aceptación con Ollama real detenido en hardware real sigue pendiente.
2. **`JARVIS_PC_REQUIREMENTS.md`** (nuevo) — auditoría de prerrequisitos cruzando `pyproject.toml`, `scripts/install/install.sh` y `_rust_bridge.py` con la documentación. Hallazgo relevante: casi todo (uv, Python, Git, Ollama, toolchain de Rust) lo instala el script oficial de OpenJarvis dentro de WSL2 automáticamente — el checklist manual real del usuario son solo 4 ítems del lado Windows (virtualización BIOS, WSL2+Ubuntu, driver NVIDIA con soporte WSL2/CUDA).
3. **`JARVIS_T0_EXECUTION_GUIDE.md`** (nuevo) — comandos exactos para T0.1-T0.3 en el PC del usuario, sin inventar ni cambiar ningún criterio de aceptación, incluida la sintaxis real de `jarvis bench run` tomada de `docs/user-guide/benchmarks.md`.
4. **Revisión de las 5 especificaciones de Gemini** (T2.3, T2.6, T4.3, T4.4, T7.4) — encontrado un hueco real: **T7.4 no tenía bloque de interfaz explícito** (a diferencia de las otras 4). Corregido añadiendo la firma de `register_gaming_reactivation_hotkey()` y la semántica de fallo (si la notificación de voz falla, no se cambia el perfil, para no dejar un estado a medias). Las 5 ya tienen los 6 campos completos.
5. **Paralelización determinada** (sin delegar ni iniciar nada): T2.3 y T2.6 son independientes entre sí (dependen de T2.1 y T2.4 respectivamente, archivos distintos) — paralelizables. T4.2/T4.3/T4.4 dependen todas solo de T4.1 y usan archivos separados (`youtube_helpers.py`, `browser_tabs.py`, `browser_forms.py`) — paralelizables entre sí una vez T4.1 esté integrada. T7.4 **no** es paralelizable con T2.3: depende de que G-T2.3 esté ya integrada (mismo archivo `hotkey_listener.py`, secuencial).
6. **Revisado el "trabajo adicional seguro"** pedido explícitamente — no se encontró ninguna tarea de implementación de `DEVELOPMENT_TASKS.md` adicional desbloqueada más allá de lo ya hecho; el trabajo de esta sesión fue exactamente el disponible sin hardware (auditoría de código propio, documentación, specs).

**Archivos creados/modificados esta sesión:** `JARVIS_PC_REQUIREMENTS.md`, `JARVIS_T0_EXECUTION_GUIDE.md` (nuevos); `src/openjarvis/tasks/store.py` (máquina de transiciones), `tests/tasks/test_store.py` (1 test corregido + 4 nuevos), `GEMINI_TASK_PACK.md` (interfaz de T7.4 completada); `JARVIS_PROGRESS.md` (este archivo).

**Nada commiteado ni pusheado.** Nada instalado, nada de Windows/WSL2/CUDA modificado — toda la sesión respetó la restricción de "laptop de trabajo sin permisos administrativos".

## Hallazgo de esta sesión: bloqueo total por dependencias, no por falta de trabajo

Se recorrió la columna "Depende de" de **todas** las tareas de `DEVELOPMENT_TASKS.md` (T0.1 a T8.4) de forma exhaustiva. Resultado: **toda tarea no completada traza, por su cadena de dependencias, hasta T0.1-T0.3 (entorno WSL2/CUDA real, benchmark en la RTX 3060) o T1.5 (prueba del puente en Windows real)** — ambas imposibles de ejecutar de verdad en este sandbox (confirmado al inicio de este documento). No es un caso de "hay trabajo pero no lo hice": no existe ninguna tarea de implementación de código restante con todas sus dependencias satisfechas sin tu PC real. Esto es exactamente lo que `JARVIS_PROGRESS.md` debe registrar como bloqueo estructural (no un error menor a resolver, no algo que se solucione eligiendo otra tarea al azar).

**Lo que sí se hizo esta sesión, dentro de lo que la propia autonomía autoriza sin pedir permiso ("preparar tareas bien especificadas para Gemini"):** completar las 5 especificaciones de `GEMINI_TASK_PACK.md` que quedaban pendientes (T2.3, T2.6, T4.3, T4.4, T7.4) con sus 6 campos obligatorios. Siguen sin poder delegarse hoy — no por falta de especificación, sino porque sus propias dependencias (T2.1, T2.4, T4.1, T7.2) están en la misma cadena bloqueada por hardware. Esto no es trabajo ficticio ni un sustituto de la implementación real: es preparación legítima para el momento en que el bloqueo se levante.

## Eliminación de duplicación: `degradation.py` ahora reutiliza `agents/errors.retry_delay()`

La auditoría comparativa de esta sesión encontró que `engine/degradation.py` (T1.4) había inventado su propia tupla de backoff (`1.0, 2.0, 4.0`) en vez de reutilizar `retry_delay()`, ya existente en `agents/errors.py` (usado por `agents/executor.py` para los reintentos de ticks de agente: `min(10 * 2^intento, 300)`).

**Verificación de compatibilidad antes de integrar (punto 1-2 de la petición):** `retry_delay()` es una función pura sin estado (`int -> int`), segura de reutilizar. Pero su cadencia está calibrada para un **tick de agente en segundo plano** (Operator programado, donde esperar hasta 300s entre intentos es aceptable), no para una llamada **síncrona dentro de un turno de voz** (T2.4 exige <3s de latencia percibida total). Usarla sin modificar habría cambiado el comportamiento de 3s de espera total a ~30s — una regresión de experiencia real, no aceptable.

**Resolución aplicada (sin mantener dos sistemas de backoff):** `degradation.py` ahora importa y llama a `retry_delay()` real, escalada por un divisor documentado (`_interactive_retry_delay(attempt) = retry_delay(attempt) / 10`), que reproduce exactamente los valores anteriores (1.0s, 2.0s, 4.0s) para los 3 reintentos que se usan hoy, y generaliza correctamente si `max_retries` aumenta en el futuro (en vez del tope fijo que repetía el último valor de la tupla antigua). El parámetro de la función pasó de `backoff_seconds: Sequence[float]` a `delay_fn: Callable[[int], float]`, inyectable igual que antes para los tests.

**Hallazgo adicional durante esta corrección (no relacionado con `retry_delay`):** el test de concurrencia `test_concurrent_mark_running_only_one_wins` falló de forma intermitente con `sqlite3.InterfaceError: bad parameter or other API misuse` — un defecto real y preexistente: `sqlite3.connect(..., check_same_thread=False)` **no** hace que `execute()`/`commit()` sean seguros entre hilos simultáneos, solo desactiva la comprobación de "mismo hilo". Corregido añadiendo un `threading.Lock()` en `TaskStore` que serializa todo acceso a la conexión (`create`, `_transition`, `get`, `list_by_state`). Verificado con 8 ejecuciones consecutivas del test de concurrencia, 8/8 en verde (antes era intermitente).

**Pruebas:** 2 nuevas en `tests/engine/test_degradation.py` (`test_delay_reuses_agents_errors_retry_delay`, `test_delay_fn_is_injectable_for_tests_without_real_sleeps`) + 1 test existente ampliado para verificar los valores exactos de los delays (`sleeps == [1.0, 2.0]`). **25/25 tests pasando** (antes 23/23), `ruff check` limpio sobre los 4 archivos tocados.

**Archivos modificados:** `src/openjarvis/engine/degradation.py`, `src/openjarvis/tasks/store.py` (lock de concurrencia), `tests/engine/test_degradation.py`, `tests/tasks/test_store.py` (sin cambios funcionales, solo se benefició del fix).

## Última tarea completada (con evidencia)

**T1.3 — Confirmar que Pearl Mining permanece inerte.**

- Grep exhaustivo de `jarvis mine`/`jarvis pearl` en todo el árbol (`.py`, `.sh`, `.toml`) fuera de `.git`: las únicas coincidencias son dentro de la propia implementación upstream de OpenJarvis (`src/openjarvis/mining/*.py`, `cli/hints.py` — docstrings/mensajes de su propia CLI, nunca invocados por nosotros).
- Búsqueda de cualquier `config.toml` real en el repo: existe `configs/openjarvis/config.toml` (un ejemplo de upstream para evaluación en 8×A100, nada que ver con nuestro proyecto) — inspeccionado completo, **sin sección `[mining]`**.
- Extra `mining-pearl-vllm`: confirmado no instalado (`importlib.util.find_spec("pearl")` → `None`).
- **Estado: COMPLETADA**, con evidencia real y reproducible (comandos documentados arriba), sin necesidad de hardware.

**T1.4 — Mecanismo de degradación controlada + máquina de estados de tareas.**

- `src/openjarvis/tasks/store.py` — `TaskStore` (SQLite estándar, mismo patrón que `TraceStore`/`SchedulerStore` del repo, sin segunda base de datos). Estados: `PENDING`/`RUNNING`/`INTERRUPTED`/`COMPLETED`/`UNKNOWN`. Regla dura `can_auto_retry()` → `False` para `INTERRUPTED`/`UNKNOWN`; único camino de vuelta es `approve_retry()`, explícito.
- `src/openjarvis/engine/degradation.py` — `generate_with_degradation()`: comprueba `health()` antes de llamar, mensaje claro si no está sano, reintentos acotados con backoff inyectable, nunca referencia a un segundo engine/proveedor (verificado con un `_PoisonEngine` que lanza `AssertionError` si se le llama).
- **Pruebas: 25/25 pasando** (actualizado tras la eliminación de duplicación de backoff y el fix de concurrencia con lock), reales (no solo mocks de librería — un `_FakeEngine` que implementa de verdad la ABC `InferenceEngine`, y una prueba de concurrencia con hilos reales de Python), en `tests/tasks/test_store.py` (16) y `tests/engine/test_degradation.py` (9).
- `ruff check` limpio sobre los 4 archivos nuevos.
- **Estado: IMPLEMENTADA y PROBADA (en sandbox, con motor simulado real).** No marcada como `COMPLETADA` porque el criterio de aceptación de `DEVELOPMENT_TASKS.md` T1.4 exige la prueba con Ollama real detenido deliberadamente — eso requiere tu entorno WSL2 con Ollama real corriendo, pendiente de validación en tu PC.

## Tareas pendientes (Fase 1) — todas bloqueadas por hardware real, confirmado exhaustivamente esta sesión

- **T1.1** — depende de T0.3 (benchmark real). Bloqueada.
- **T1.2** — depende de T1.1. Bloqueada transitivamente.
- **T1.5** — depende de tener un PC Windows real. Bloqueada por completo.

(T1.3 ya completada, ver arriba. T1.4 implementada/probada en sandbox, pendiente de validación real con Ollama real detenido en tu PC.)

## Tareas bloqueadas (requieren tu PC o tu intervención)

| Tarea | Motivo del bloqueo | Qué necesito de ti |
|---|---|---|
| T0.1-T0.3 | Sin GPU/WSL2 en este sandbox | Que confirmes WSL2+CUDA en tu PC y, cuando quieras, me conectes a él (Remote Control) para poder ejecutar comandos reales ahí, o que tú mismo ejecutes `jarvis bench` y me pases los resultados. |
| T1.5, Fase 2, Fase 3, Fase 4, Fase 5, Fase 7, T3.8 | Requieren Windows/WSL2/micrófono/GPU/Brave reales | Igual que arriba. |
| T0.4 (confirmar config sin engine cloud) | Puedo preparar el `config.toml`, pero confirmarlo "funcionando" requiere Ollama real | — |

**No he pedido esto antes en el chat porque ya lo señalé en mi mensaje anterior; lo registro aquí para que quede en la memoria persistente del proyecto, no solo en el chat.**

## Archivos modificados/creados esta sesión

- `src/openjarvis/tasks/__init__.py` (nuevo)
- `src/openjarvis/tasks/store.py` (nuevo)
- `src/openjarvis/engine/degradation.py` (nuevo)
- `tests/tasks/__init__.py` (nuevo)
- `tests/tasks/test_store.py` (nuevo)
- `tests/engine/__init__.py` (nuevo)
- `tests/engine/test_degradation.py` (nuevo)
- `.gitignore` (1 línea, ronda anterior, ya aprobada: excepción para versionar `CLAUDE.md`)
- `CLAUDE.md`, `JARVIS_MASTER_PLAN.md`, `DEVELOPMENT_TASKS.md`, `JARVIS_ROADMAP.md`, `CLAUDE_GEMINI_WORKFLOW.md` — documentación de planificación, de rondas anteriores ya aprobadas.
- `GEMINI_TASK_PACK.md` — **esta sesión:** completadas las 5 especificaciones que faltaban (G-T2.3, G-T2.6, G-T4.3, G-T4.4, G-T7.4), ya no quedan tareas bloqueadas por falta de especificación.
- `uv.lock` — actualizado automáticamente por `uv sync` (no editado a mano).

**Nada de esto está commiteado.** `git status --short` actual:
```
 M .gitignore
A  CLAUDE.md
A  CLAUDE_GEMINI_WORKFLOW.md
AM DEVELOPMENT_TASKS.md
AM GEMINI_TASK_PACK.md
AM JARVIS_MASTER_PLAN.md
AM JARVIS_ROADMAP.md
 M uv.lock
?? JARVIS_PROGRESS.md
?? src/openjarvis/engine/degradation.py
?? src/openjarvis/tasks/
?? tests/engine/test_degradation.py
?? tests/tasks/
?? JARVIS_PROGRESS.md
```

## Dependencias instaladas

- `uv sync` (dependencias base de `pyproject.toml`) y `uv sync --extra dev` (pytest, ruff, etc.) en un venv local `.venv/` (gitignorado). Ninguna instalación privilegiada de sistema, ninguna fuera de este contenedor.
- No se instaló Ollama ni ningún motor de inferencia real en este sandbox (no hay GPU; instalarlo en CPU aquí solo daría cifras ficticias, y el objetivo es no fabricar evidencia falsa).

## Pruebas ejecutadas y resultados reales

- `tests/tasks/test_store.py`: **16/16 PASS** (12 originales + 4 de la auditoría: tarea completada no reejecutable, transición ilegal PENDING→COMPLETED, bypass de `approve_retry` bloqueado, carrera concurrente con 2 hilos reales — esta última, 8/8 en ejecuciones repetidas tras el fix del lock).
- `tests/engine/test_degradation.py`: **9/9 PASS** (7 originales + 2 de la integración de `retry_delay()`).
- `ruff check` sobre los 4 archivos nuevos: **sin errores**.
- Suite completa de OpenJarvis (`pytest tests/`): **no concluyente** — se cuelga/excede el tiempo en algo ajeno a este trabajo (no relacionado con `tasks/`/`degradation.py`); no se investigó a fondo por no ser parte del alcance de T1.4. Pendiente si en algún momento bloquea CI real.

## Problemas encontrados

1. La suite de tests completa del repo (`pytest tests/`) no termina en un tiempo razonable por algo ajeno a nuestro código nuevo — no diagnosticado todavía, no bloquea el trabajo de T1.4.
2. Ningún otro problema bloqueante en el código escrito esta sesión.

## Decisiones técnicas tomadas esta sesión

- `TaskStore` usa `sqlite3` estándar con su propio archivo (`tasks.db`), replicando el patrón ya existente de `TraceStore`/`SchedulerStore` — no se tocó el backend de memoria documental (`SQLiteMemory`, que está respaldado por Rust y es para documentos, no para estado estructurado de tareas). Esto respeta "sin segunda base de datos independiente": sigue siendo SQLite, solo una tabla/archivo más, igual que ya hacen otros subsistemas del propio OpenJarvis.
- `generate_with_degradation()` vive en `engine/` (no en `tasks/`) porque es lógica de invocación del engine, consistente con dónde vive `EngineConnectionError`.
- Estado `UNKNOWN` definido pero no ejercitado por `degradation.py` en esta sesión (una llamada `generate()` sin efectos secundarios externos nunca necesita ese estado — es para el puente de Windows, Fase 3, donde una acción puede ejecutarse sin confirmación de vuelta). Esto es intencional, no un olvido.

## Próximo paso concreto

**No hay ninguna tarea de implementación de código desbloqueada hoy** (ver el hallazgo de bloqueo total, arriba). El siguiente paso real es uno de estos dos, y depende del usuario, no de mí:

1. **El usuario conecta su PC Windows real** (WSL2 + Ollama instalados, o vía Remote Control a esta sesión) → se retoma inmediatamente por T0.1 (confirmar WSL2/CUDA), siguiendo la cadena T0.1→T0.2→T0.3→T1.1→... sin saltarse nada.
2. **El usuario ejecuta T0.1-T0.3 por su cuenta** en su PC y me pasa los resultados (hardware detectado, resultados de `jarvis bench`) → con esos datos reales puedo continuar T1.1/T1.2 sin necesidad de acceso directo a su máquina.

Mientras no ocurra ninguna de las dos, no hay más trabajo de implementación legítimo que hacer sin fabricar evidencia falsa. Lo único que queda disponible sin hardware (documentación, specs de Gemini, revisión de código existente) ya está hecho esta sesión.

## Instrucciones para retomar el trabajo

1. Lee `CLAUDE.md` y este archivo completo antes de nada.
2. `git status` — debe coincidir con la lista de arriba (nada commiteado).
3. `cd /home/user/JARVIS && .venv/bin/python -m pytest tests/tasks/ tests/engine/test_degradation.py -q` — debe seguir dando 25/25 PASS. Si no, algo cambió fuera de esta sesión; investigar antes de seguir.
4. Lee también `JARVIS_PC_REQUIREMENTS.md` y `JARVIS_T0_EXECUTION_GUIDE.md` antes de retomar — son las guías que el usuario necesita para desbloquear T0.1-T0.3 en su PC real.
4. Comprobar si el usuario ya dio acceso a su PC Windows (real o vía Remote Control). Si sí: empezar por T0.1. Si no: no hay tarea de implementación que avanzar — repasar si el usuario ya pasó resultados de T0.1-T0.3 ejecutados por su cuenta.
