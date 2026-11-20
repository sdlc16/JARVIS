# JARVIS_OPTIMIZATION_AUDIT.md

Auditoría comparativa `sdlc16/JARVIS` (nuestro fork, commit `4309def`, 2026-05-26) vs `open-jarvis/OpenJarvis` (upstream oficial, commit `c3ed63f`, 2026-11-18) — código real, no solo README. Objetivo: reutilizar al máximo, integrar selectivamente, desarrollar únicamente lo que falte, evitar desarrollo duplicado.

## Matriz de clasificación

A = ya existe y funciona, reutilizar directamente · B = existe parcialmente, integrar/adaptar · C = no existe, desarrollar · D = no necesaria para V1, aplazar.

| Capacidad del roadmap | Clasificación | Evidencia (código real) |
|---|---|---|
| **Agentes** (orchestrator, operative, native_react, etc.) | **A** | Idénticos en estructura salvo 2 archivos ausentes en nuestro fork: `agents/opencode.py` (variante de agente de código, no crítica) y `agents/tool_resolver.py` (consistencia de tools entre modos del agente "managed", no crítico para nuestro flujo voz→acción). Núcleo (`BaseAgent`, `ToolUsingAgent`, `AgentRegistry`) igual. |
| **Herramientas** (`tools/`) | **A**, con huecos menores | Mismo conjunto salvo `apple_calendar.py` (solo macOS), `fx_macro.py` (fuera de alcance), `weather.py` (no en roadmap V1), `_paths.py` (utilidad menor). Ninguno bloquea nada de `DEVELOPMENT_TASKS.md`. |
| **Memoria** (extracción automática de hechos) | **B** | Ya evaluado en detalle en `10_EVALUACION_COMPATIBILIDAD.md` (`JARVIS_RESEARCH_PACK`): existe completo en upstream (`memory/store.py`+`service.py`+`extractor.py`), depende de 3 piezas ausentes en nuestro `core/` (`FactStoreRegistry`, `EventType.CHAT_EXCHANGE_COMPLETED`, campos de `StorageConfig`). Decisión: adaptar `store.py`+`extractor.py`, no copiar `service.py`. |
| **Persistencia** (SQLite/FTS5, backends documentales) | **A** | `tools/storage/*` idéntico en lo esencial. |
| **Gestión de tareas** (estados PENDING/RUNNING/INTERRUPTED/COMPLETED/UNKNOWN) | **C**, confirmado sin duplicación | Verificado expresamente: `engine/multi.py` upstream es solo un router por nombre de modelo (no fallback por fallo). `a2a/protocol.py` tiene su propio `TaskState` (SUBMITTED/WORKING/INPUT_REQUIRED/COMPLETED/CANCELED/FAILED) pero es el protocolo A2A de Google para delegación remota entre agentes — dominio distinto, sin relación con `tasks/store.py`. T1.4 es trabajo nuevo legítimo. |
| **Interfaz** (dashboard, GUI) | **D** (aplazada, según el roadmap) | `cli/gui_cmd.py` y `system/` idénticos a upstream — Fase 14/posterior en nuestro plan. |
| **Conversación** (`jarvis chat`, loop principal) | **B** | `cli/_voice_chat.py` — adaptar el patrón de troceo de frases, no el archivo completo. `cli/chat_cmd.py` no comparado línea por línea todavía. |
| **Acceso a modelos locales** (engine Ollama) | **A** | `engine/ollama.py` sin diferencias relevantes. |
| **Ejecución de herramientas** (`ToolExecutor`) | **A** | Sin diferencias relevantes. |
| **Recuperación ante errores** | **A existente + duplicación interna corregida** | Ya existía en nuestro propio fork (no es upstream nuevo): `agents/errors.py` (`RetryableError`/`FatalError`/`EscalateError`, clasificación por patrón, `retry_delay()` con backoff exponencial `min(10×2^intento, 300)`), usado por `agents/executor.py`. **Hallazgo:** `engine/degradation.py` (T1.4) no reutilizó este patrón — inventó su propio backoff. **Ya corregido** (ver §"Duplicación eliminada" abajo). |
| **Voz y reconocimiento de voz** | **B** | `speech/voice_io.py` (D, solo el algoritmo RMS como referencia, no sustituye VAD real) y `cli/_voice_chat.py` (B, troceo de frases). |
| **Integraciones** (`channels/`, `connectors/`) | **A**, con huecos menores | `channels/` idéntico. `connectors/` solo le falta `apple_calendar.py` (macOS) e `imap.py` (útil para Fase 12, no bloqueante). |

## Qué sustituir, qué conservar, qué adaptar

- **Conservar tal cual:** agentes, herramientas base, persistencia, engine de Ollama, ejecución de herramientas, channels/connectors.
- **Adaptar (no copiar completo):** memoria (`store.py`+`extractor.py`, sin `service.py`), voz (algoritmo RMS + troceo de frases de TTS), captura de pantalla (script PowerShell de `cli/_screen.py`, solo dentro del puente nativo de Windows — nunca en el núcleo WSL2, porque `sys.platform` nunca es `"win32"` ahí).
- **Nada que sustituir en Gestión de tareas** — T1.4 es trabajo nuevo, sin equivalente en ningún lado del repo.

## Duplicación eliminada: `degradation.py` ahora reutiliza `agents/errors.retry_delay()`

Encontrada y corregida en esta misma ronda de trabajo. `engine/degradation.py` (T1.4) tenía su propia tupla de backoff (`1.0, 2.0, 4.0`) en vez de reutilizar `retry_delay()`.

**Verificación de compatibilidad antes de integrar:** `retry_delay()` es pura y sin estado, segura de reutilizar — pero su cadencia está calibrada para un tick de agente en background (Operator programado, 300s de espera aceptable), no para una llamada síncrona dentro de un turno de voz (T2.4 exige <3s de latencia percibida). Usarla sin ajuste habría cambiado 3s de espera total a ~30s — regresión real, no aceptable.

**Resolución aplicada:** `degradation.py` importa y llama a `retry_delay()` real, escalada por un divisor documentado (`_interactive_retry_delay(attempt) = retry_delay(attempt) / 10`), que reproduce exactamente los valores anteriores (1.0s, 2.0s, 4.0s) y generaliza mejor si `max_retries` aumenta. El parámetro pasó de `backoff_seconds: Sequence[float]` a `delay_fn: Callable[[int], float]`, igualmente inyectable en tests.

**Hallazgo adicional durante la corrección:** `sqlite3.connect(..., check_same_thread=False)` no hace segura la ejecución concurrente desde varios hilos — `TaskStore` tenía una carrera real (`sqlite3.InterfaceError` intermitente). Corregido con un `threading.Lock()` que serializa el acceso a la conexión. Verificado con 8 ejecuciones consecutivas del test de concurrencia, 8/8 en verde.

**Resultado final: 25/25 tests pasando** (`tests/tasks/test_store.py` 16, `tests/engine/test_degradation.py` 9), `ruff check` limpio.

## Repositorios externos auditados (resumen, detalle completo en `JARVIS_RESEARCH_PACK`)

6 repos externos (Ari-VoiceCommand, jarvis_local, ARIA, AndreyLebedenko/jarvis-local, Ag3nt47/JARVIS-Local, onlyflames360/jarvis) + el upstream oficial `open-jarvis/OpenJarvis`. Ninguno usa nuestra arquitectura WSL2+puente. Componentes reutilizables identificados: cifrado DPAPI del token del puente (de `onlyflames360/jarvis`, ya aplicado a `JARVIS_MASTER_PLAN.md`/`DEVELOPMENT_TASKS.md` T3.2), patrón de token de archivo (`AndreyLebedenko/jarvis-local`). El paquete de investigación completo (`JARVIS_RESEARCH_PACK/`, ~142MB con los clones, ZIP de 31MB sin `.git`) vive **fuera** de este repositorio, en `/home/user/JARVIS_RESEARCH_PACK/` — no incluido en esta exportación salvo que se pida explícitamente.
