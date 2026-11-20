# JARVIS — Master Plan

> Asistente personal autónomo construido **sobre** OpenJarvis (Stanford Hazy Research), no reconstruido desde cero.
> Repositorio: `sdlc16/JARVIS`. La rama de trabajo de Claude Code varía entre sesiones — se verifica al inicio de cada sesión (`git branch --show-current`), nunca se asume fija (ver `CLAUDE_GEMINI_WORKFLOW.md`).
> Primera entrega funcional: **11 de diciembre de 2026** (23 días desde hoy, 18 de noviembre de 2026).

## 1. Visión

JARVIS no es un chatbot con voz. Es un agente que:

1. **Entiende** una instrucción en lenguaje natural (voz o texto).
2. **Ejecuta** acciones reales en el PC (apps, ventanas, navegador, archivos).
3. **Comprueba** que la acción tuvo el efecto esperado (lectura de pantalla/OCR, estado de ventana, código de salida).
4. **Recuerda** lo relevante (preferencias, correcciones, hábitos) y lo usa en interacciones futuras.
5. **Aprende** patrones de uso y sugiere proactivamente, sin ser invasivo ni actuar solo en lo sensible.

Principio fundamental: **reutilizar lo que OpenJarvis ya resuelve** (agentes, engine de inferencia, memoria, scheduler, EventBus, registry pattern, herramientas) y **desarrollar únicamente lo que falta** (voz en tiempo real, control nativo de Windows, visión de pantalla, memoria de hábitos, monitor de hardware, modo gaming).

## 2. Hardware y plataforma

| Recurso | Valor | Implicación |
|---|---|---|
| GPU | RTX 3060, 8 GB VRAM | Un solo modelo cuantizado cargado a la vez; descargar al entrar en modo gaming pesado. |
| RAM | 32 GB | Margen amplio para memoria FAISS/BM25 in-memory y procesos auxiliares (STT/TTS/OCR). |
| CPU | Intel i5-12400F (por confirmar) | Suficiente para VAD, wake-word y OCR locales sin GPU. |
| Disco | SSD 1 TB | Sin restricción relevante en esta fase. |
| OS | Windows (único entorno de desarrollo y ejecución en esta etapa) | Ver §3. No se desarrolla ni optimiza para macOS/Apple Silicon en esta fase. iPhone queda como extensión futura (post-V1), sin bloquear la entrega. |

Reglas de recursos (aplican a todos los módulos):

- Nunca dos modelos LLM grandes cargados en VRAM simultáneamente.
- Descargar el modelo de GPU al entrar en modo Gaming pesado (§11 del plan funcional).
- No competir por GPU/CPU con un videojuego en curso salvo que el usuario lo autorice explícitamente.
- Ningún tamaño de modelo se fija "a ojo": se mide con `jarvis bench` + `nvidia-smi`/el monitor de hardware antes de confirmarlo como predeterminado (ver §4 — corrección deliberada frente al catálogo de OpenJarvis, que no declara VRAM/cuantización fiable para los modelos candidatos).

## 3. Arquitectura — núcleo OpenJarvis + puente nativo de Windows

Dos procesos separados, nunca mezclados:

```
┌─────────────────────────────┐        loopback HTTP/named pipe        ┌──────────────────────────────┐
│   NÚCLEO OpenJarvis (WSL2)   │  ───────────────────────────────────▶ │  PUENTE NATIVO DE WINDOWS     │
│                              │ ◀───────────────────────────────────  │  (proceso Windows, sin WSL2)  │
│ agentes · memoria · scheduler│        solo localhost, con token      │  mouse/teclado/ventanas       │
│ engine (Ollama, único)       │                                       │  captura de pantalla · OCR    │
│ voz (STT/TTS/VAD/wake-word)  │                                       │  monitor de hardware          │
│ navegador (Brave/Playwright) │                                       │  modo gaming (perfil activo)  │
└─────────────────────────────┘                                       └──────────────────────────────┘
```

**Por qué WSL2 y no Windows nativo para el núcleo:** CUDA está soportado y confirmado por NVIDIA en WSL2 para GPUs GeForce/RTX en modo WDDM (la RTX 3060 cualifica); es la única vía que OpenJarvis soporta e instala oficialmente (instalador, detección de hardware, arranque de Ollama sin systemd). Windows nativo no tiene instalador oficial y al menos una herramienta del núcleo (`shell_exec`) se comporta de forma distinta/peor fuera de un shell POSIX. Mantener el núcleo en WSL2 evita mantener un fork de facto del proyecto.

**Por qué el puente es un proceso Windows separado y no "WSL2 con permisos especiales":** WSL2 no tiene acceso a la sesión de escritorio interactiva de Windows (no puede mover el cursor del usuario ni leer sensores de placa base de forma fiable). Todo lo que exige la sesión de escritorio real (mouse/teclado/ventanas/capturas/sensores/perfil de gaming) vive en Windows nativo, como servicio local propio — nunca expuesto a Internet, con autenticación por token local y registro de cada operación (§13 del plan funcional).

El puente se integra en OpenJarvis como **Tools nuevas** (`windows_control`, `screen_vision`, `hardware_monitor`, `gaming_mode`) que llaman al puente por HTTP local, siguiendo el patrón de registro existente (`@ToolRegistry.register(...)`). El núcleo no necesita saber que "Windows" existe del otro lado; solo ve herramientas.

## 4. Cerebro — motor de inferencia

**Ollama es el único motor runtime de JARVIS.** No hay proveedor cloud de refuerzo en la arquitectura de inferencia (política de proveedores descartados: ver `CLAUDE.md`). Gemini **no es un motor de inferencia de JARVIS** — ver la distinción con su rol de desarrollo más abajo.

| Componente | Rol | Reglas |
|---|---|---|
| **Ollama** | Único cerebro de inferencia en tiempo de ejecución, local, offline, siempre disponible. | Modelo por defecto a confirmar con benchmark real en la RTX 3060 (candidatos: Qwen3 8B, GLM-4.7 Flash, Trinity Mini — el catálogo de OpenJarvis no declara VRAM/cuantización fiable para ninguno de ellos; no se asume, se mide). |
| **Claude Code** | Arquitecto principal **durante el desarrollo** (este proceso). Herramienta de construcción, no componente de JARVIS. | JARVIS **no depende de la API de Claude para funcionar** en producción. |
| **Gemini / Antigravity** | Herramienta auxiliar **exclusivamente de desarrollo** (implementación de módulos, pruebas, documentación — ver `CLAUDE_GEMINI_WORKFLOW.md`), según la cuota disponible en la cuenta del usuario. | **No forma parte del runtime de JARVIS ni de su arquitectura de proveedores de inferencia** en ningún escenario, ni como principal ni como fallback. Una suscripción Google AI Plus/Pro da cuota dentro de AI Studio/la app — no otorga acceso a la Gemini Developer API. No activar facturación de Google Cloud. Esto aplica únicamente al uso de Gemini como asistente de desarrollo; JARVIS en sí nunca llama a la API de Gemini. |

**Consecuencia de no tener proveedor cloud de refuerzo:** JARVIS depende por completo de que Ollama esté disponible. La arquitectura sigue siendo exclusivamente local — no se añade un proveedor cloud alternativo — y en su lugar se define un **mecanismo de degradación controlada** para cuando Ollama falla:

1. **Detección de indisponibilidad:** antes de cada turno, se usa `health()` del engine (ya expuesto por `InferenceEngine` en OpenJarvis) para comprobar que Ollama responde; si no, no se intenta generar y se pasa directo al paso 2.
2. **Mensaje claro al usuario:** respuesta explícita (por voz y texto) de que el cerebro local no está disponible en este momento — nunca un error silencioso ni una respuesta inventada.
3. **Reintento controlado:** un número acotado de reintentos con backoff (ej. 3 intentos, espaciados), no un bucle indefinido ni reintentos silenciosos en segundo plano que oculten el problema.
4. **Conservación segura de tareas pendientes, con estados explícitos.** Cada tarea/acción registrada en la memoria episódica (SQLite, sin segunda base de datos) tiene uno de estos estados:
   - `pendiente` — instruida pero todavía no se intentó ejecutar.
   - `en_ejecucion` — se está ejecutando en este momento.
   - `interrumpida` — se empezó a ejecutar pero se detuvo antes de terminar (ej. por caída de Ollama o del puente a mitad de la acción).
   - `completada` — se ejecutó y su resultado se verificó explícitamente (éxito o fallo, pero *verificado*).
   - `resultado_desconocido` — se ejecutó pero no fue posible confirmar su efecto real (ej. el puente no respondió a la verificación, o la caída de Ollama ocurrió entre la ejecución y la confirmación).
   
   **Regla dura:** una tarea en `interrumpida` o `resultado_desconocido` **nunca se reintenta automáticamente**. Se informa al usuario con el estado exacto y se le pregunta si quiere reintentarla — repetir a ciegas una acción cuyo resultado no se pudo verificar (ej. un envío, un borrado, un clic) puede duplicar el efecto o empeorarlo.
5. **Ningún envío automático a proveedores externos.** Bajo ninguna circunstancia una falla de Ollama, del puente o de la verificación dispara una llamada automática a una API cloud (Gemini, OpenAI o cualquier otra) para "tapar" el fallo o para decidir qué hacer con una tarea de estado desconocido. Esto es una regla dura, no una preferencia. El almacenamiento de estos estados es siempre SQLite (extensión del esquema de memoria existente, ver §6) — no se introduce un almacén externo ni un servicio de cola de terceros.

Este mecanismo se implementa como parte del núcleo (Fase 1, ver `DEVELOPMENT_TASKS.md` T1.4) y se prueba explícitamente antes de dar por cerrada la primera entrega.

Pearl Mining (`jarvis mine` / `jarvis pearl`, sección `[mining]` de `config.toml`) existe en el repo pero **no se activa nunca**: requiere tres pasos manuales explícitos (instalar extra, `mine init`, `mine start`) que no forman parte de ningún flujo de JARVIS. No se modifica ni se elimina ese código — simplemente no se invoca. Ver `CLAUDE.md` para la regla permanente.

## 5. Componentes de OpenJarvis que se reutilizan tal cual

- Agentes (`orchestrator`, `operative`, `monitor_operative`, `proactive`-style flow) como base de ejecución de tareas con herramientas.
- `EngineRegistry` / `AgentRegistry` / `ToolRegistry` / `ChannelRegistry` — patrón de registro para añadir todo lo nuevo sin tocar el core.
- `EventBus` (`core/events.py`) para enganchar telemetría, auditoría y futuras automatizaciones.
- Memoria SQLite/FTS5 (`tools/storage/sqlite.py`) como backend persistente por defecto — **se extiende con tablas propias para memoria personal/episódica/hábitos, no se sustituye.**
- Scheduler (`scheduler/scheduler.py` + `store.py`) para rutinas, resúmenes periódicos y tareas de mantenimiento de memoria (deduplicación, caducidad).
- `proactive_tools.py` (flujo propose → approve → execute) como base del sistema de aprobación de acciones sensibles.
- `speech/faster_whisper.py` y `speech/kokoro_tts.py` como backends locales de voz — se excluyen explícitamente `cartesia_tts.py`, `openai_tts.py`, `openai_whisper.py`, `deepgram.py` (todos de pago, ver auditoría previa).
- `tools/browser.py` (Playwright) como base de automatización web — se adapta para apuntar al Brave real del usuario (ver §6 funcional).

## 6. Componentes nuevos (no existen en OpenJarvis hoy)

1. Pipeline de voz conversacional en tiempo real (wake-word + VAD + barge-in) sobre el STT/TTS ya existente.
2. Puente nativo de Windows (control de mouse/teclado/ventanas, hotkeys, volumen, detección de ventana activa, verificación de acciones).
3. Visión de pantalla (captura + OCR) a demanda, nunca continua.
4. Esquema de memoria personal/episódica/hábitos sobre SQLite + heurísticas de detección de patrones.
5. Monitor de hardware del sistema fuera de la inferencia (CPU/GPU/RAM/VRAM/temperaturas/procesos; FPS/frametime/1% lows solo si se confirma una fuente fiable — no se inventan métricas).
6. Modo Gaming (3 perfiles) que coordina voz, scheduler y carga de modelo con el estado del juego.
7. Adaptación de `tools/browser.py` para controlar Brave real (perfil persistente, headful) en vez del Chromium interno headless de Playwright.

## 7. Seguridad (transversal, no una fase aparte)

- Puente de Windows: solo `localhost`, token de autenticación local almacenado con cifrado nativo de Windows (DPAPI) en vez de texto plano, logging de cada operación ejecutada, nunca expuesto a Internet.
- Acciones sensibles (borrado definitivo, envío de correo, ejecución de secuencias que modifiquen archivos fuera de rutas seguras) pasan por el flujo de aprobación de `proactive_tools.py` — nunca automáticas.
- Sin contraseñas en texto plano, sin credenciales en logs, sin captura de pantalla continua ni grabación permanente de ambiente.
- Validación de rutas de archivo antes de cualquier operación de archivo.
- Preferencia por "mover a papelera" sobre borrado definitivo; borrado definitivo siempre con confirmación explícita.

## 8. Qué queda fuera de esta primera entrega (V2, ver `JARVIS_ROADMAP.md`)

Gestión de archivos avanzada, productividad (Gmail/Calendar), proactividad basada en aprendizaje real de hábitos (más allá de conteo de frecuencia), FPS/frametime/1% lows vía RTSS, interfaz HUD futurista, extensión remota iPhone. Todo esto está diseñado en la arquitectura pero no se construye en los 23 días hasta el 11 de diciembre — ver criterios de priorización en el roadmap.
