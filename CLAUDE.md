# CLAUDE.md — Reglas del proyecto JARVIS

Este archivo se carga automáticamente en cada sesión de Claude Code sobre este repositorio. Es el punto de referencia persistente; `JARVIS_MASTER_PLAN.md`, `DEVELOPMENT_TASKS.md`, `JARVIS_ROADMAP.md`, `CLAUDE_GEMINI_WORKFLOW.md` y `GEMINI_TASK_PACK.md` dan el detalle.

## Qué es este proyecto

Estamos convirtiendo **OpenJarvis** (framework de investigación de Stanford Hazy Research) en **JARVIS**, un asistente personal autónomo para un PC Windows (RTX 3060 8GB VRAM, 32GB RAM, i5-12400F, SSD 1TB). Primera entrega funcional: **11 de diciembre de 2026**.

## Principio fundamental — no negociable

**No reconstruir OpenJarvis.** Reutilizar al máximo lo que ya funciona (agentes, engine, memoria SQLite, scheduler, EventBus, registry pattern, herramientas, flujo de aprobación) y desarrollar únicamente lo que falta (voz en tiempo real, control nativo de Windows, visión de pantalla, memoria de hábitos, monitor de hardware, modo gaming). Antes de escribir código nuevo, comprobar si ya existe algo reutilizable en `src/openjarvis/`.

## Arquitectura de dos procesos

- **Núcleo OpenJarvis:** corre en **WSL2** (no en Windows nativo — no hay instalador oficial ni soporte probado para Windows nativo; `shell_exec` y otras asunciones del repo son POSIX). CUDA en WSL2 está confirmado por NVIDIA para GPUs GeForce/RTX en modo WDDM.
- **Puente nativo de Windows:** proceso Windows separado, fuera de WSL2, para todo lo que exige la sesión de escritorio real (mouse/teclado/ventanas/capturas/sensores/modo gaming). Solo `localhost`, con token de autenticación local, nunca expuesto a Internet, con log de cada operación. Se integra en OpenJarvis como `Tool`s nuevas vía `@ToolRegistry.register(...)`.

## Reglas de presupuesto y proveedores — no negociables

- **Cero gasto recurrente adicional.** No activar APIs de pago, no agregar tarjetas para habilitar facturación, no introducir servicios externos de pago como dependencia obligatoria.
- **Ollama es el único motor runtime de JARVIS.** No hay proveedor cloud de refuerzo en la arquitectura de inferencia: **Groq queda descartado por completo** — no debe aparecer como proveedor, fallback, dependencia ni alternativa futura en ningún documento ni en el código. **Gemini/Antigravity es exclusivamente herramienta auxiliar de desarrollo** (igual que Claude Code) — nunca forma parte del runtime de JARVIS ni de su arquitectura de proveedores de inferencia, ni como principal ni como fallback. No asumir que una suscripción de consumidor (Google AI Plus/Pro) da acceso a cuota de la API de desarrollador de Gemini.
- Pila de voz 100% local: `faster-whisper` (STT) + `kokoro_tts` (TTS). **No usar** `cartesia_tts.py`, `openai_tts.py`, `openai_whisper.py`, `deepgram.py` (todos de pago) en el camino por defecto.
- **Nunca ejecutar `jarvis mine*` ni `jarvis pearl*`, nunca escribir una sección `[mining]` en ningún `config.toml` del proyecto, nunca instalar el extra `mining-pearl-vllm`.** Pearl Mining existe en el repo pero es irrelevante para este hardware y debe quedar siempre inerte. No se modifica ni se elimina ese código.
- No usar mecanismos de autenticación no oficiales ni el OAuth de Gemini CLI para alimentar herramientas de terceros.

## VRAM y modelos — no asumir, medir

El catálogo de modelos de OpenJarvis (`intelligence/model_catalog.py`) no declara de forma fiable VRAM/cuantización/coste de caché KV para los modelos candidatos en una GPU de 8GB (`qwen3:8b` no declara `min_vram_gb` ni `quantization`; `glm-4.7-flash` y `trinity-mini` declaran `min_vram_gb=8.0` sin cuantización explícita y con contextos de 128K+ tokens que pueden añadir varios GB de caché KV). Cualquier modelo por defecto se fija **después** de medir con `jarvis bench` y el monitor de hardware en la RTX 3060 real, nunca por lectura del catálogo.

## Seguridad

- Sin contraseñas en texto plano, sin credenciales en logs, sin captura de pantalla continua, sin grabación permanente de ambiente, sin puertos de control expuestos a Internet.
- Toda acción sensible (borrado definitivo, envío de correo, secuencias que modifiquen archivos fuera de rutas seguras) pasa por el flujo de aprobación (`proactive_tools.py`) — nunca automática.
- Preferir papelera sobre borrado definitivo; borrado definitivo siempre con confirmación explícita.
- Validar rutas de archivo antes de cualquier operación de archivo.

## Convenciones heredadas de OpenJarvis (ver también `REVIEW.md`)

- Todo componente nuevo (engine, tool, agent, channel) se registra vía el patrón de registro correspondiente (`ToolRegistry`, `EngineRegistry`, `AgentRegistry`, `ChannelRegistry`) en `src/openjarvis/core/registry.py`. No se añaden factories paralelas.
- Nuevos tools/engines/agents/channels llevan tests correspondientes en `tests/`, siguiendo la estructura de `src/`.
- Eventos de ciclo de vida nuevos usan `EventBus` (`core/events.py`), no un mecanismo paralelo.
- No modificar código en archivos fuera del diff de la tarea en curso.

## Colaboración Claude ↔ Gemini

Ver `CLAUDE_GEMINI_WORKFLOW.md` para el detalle completo. Resumen: Claude diseña y revisa todo; Gemini implementa módulos acotados ya especificados; nunca se tocan los mismos archivos desde dos ramas activas a la vez; nada se integra sin revisión de Claude ni sin tests. **Nota de desambiguación:** "Gemini" aquí se refiere siempre al asistente de desarrollo (Gemini/Antigravity escribiendo código bajo especificación de Claude), nunca a la API de Gemini como proveedor de inferencia de JARVIS — esa segunda posibilidad está descartada (ver regla de presupuesto y proveedores arriba).

## Estado de la planificación

Estos documentos (`JARVIS_MASTER_PLAN.md`, `DEVELOPMENT_TASKS.md`, `JARVIS_ROADMAP.md`, `CLAUDE_GEMINI_WORKFLOW.md`, `GEMINI_TASK_PACK.md`, este `CLAUDE.md`) son una **propuesta documental de planificación, todavía no autorizada**. Ninguna implementación empieza hasta que el usuario dé su autorización explícita para pasar de planificación a código. Una vez autorizada, el trabajo avanza por módulos siguiendo `DEVELOPMENT_TASKS.md`, y no se vuelve a replanificar la arquitectura completa salvo que surja un bloqueo real.
