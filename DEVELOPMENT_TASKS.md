# JARVIS — Development Tasks

Lista de tareas para la primera entrega (hasta el 11 de diciembre de 2026). Cada tarea indica responsable, dependencias y criterio de aceptación. Ver `JARVIS_ROADMAP.md` para fechas y `CLAUDE_GEMINI_WORKFLOW.md` para reglas de ramas/revisión.

Leyenda de responsable: **C** = Claude Code, **G** = Gemini/Antigravity, **U** = requiere decisión/acción del usuario (no delegable).

---

## Estado de implementación (actualizado 2026-11-20 — detalle y evidencias en `JARVIS_PROGRESS.md`)

| Tarea | Estado | Evidencia |
|---|---|---|
| T0.1–T0.4 | Hecho | WSL2+Ubuntu, GPU RTX 4060 (no 3060), benchmark real, solo Ollama |
| T1.1–T1.5 | Hecho | memoria+agente, config, T1.4 con caída real de Ollama (+bug corregido), puente mínimo 3/3 rondas |
| T2.1 VAD | Hecho — validado con grabaciones controladas y ruta real; **falta validación con voz humana** | `tests/voice/` (78 pruebas), ver criterios ampliados abajo |
| T2.4 STT→agente→TTS | Hecho (`jarvis voice`); latencia a validar con voz humana | primera prueba real del usuario OK |
| T2.4b streaming TTS | Hecho (primer audio 0.6–0.9 s) | medido en `JARVIS_PROGRESS.md` |
| T2.6 errores de reconocimiento | Hecho | transcripción vacía/sin voz/captura/STT/agente/TTS clasificados y reintentables |
| T2.2, T2.3, T2.5 | Pendiente | wake-word, hotkey, barge-in |
| T3.x, resto | Pendiente | |

**Criterios de aceptación ampliados de T2.1/T2.6 (tras el fallo real de cierre de turno del 2026-11-20):** una pausa interna menor que `voice.silence_timeout_ms` (1400 ms por defecto, configurable) no parte la frase; la frase completa llega íntegra a faster-whisper (prueba con Whisper real sobre grabación controlada); el ruido ambiente medido (−62 dBFS) y las ráfagas no abren turnos falsos ni alargan la espera más allá de `no_speech_timeout_ms`; un micrófono mudo/bloqueado/ausente se diagnostica con causa; una transcripción vacía **no** cierra la sesión y se reintenta; las pruebas demuestran que pueden fallar (mutación).

**Metodología de QA:** ver `CLAUDE.md` ("Metodología permanente de desarrollo y QA"). Comando único de validación: `jarvis test` (T2.7, ver abajo).

---

## Fase 0 — Preparación de entorno (sin código)

| ID | Tarea | Resp. | Depende de | Criterio de aceptación |
|---|---|---|---|---|
| T0.1 | Confirmar WSL2 instalado, distro Ubuntu, `nvidia-smi` visible dentro de WSL2 | U/C | — | `nvidia-smi` dentro de WSL2 muestra la RTX 3060 con VRAM correcta. |
| T0.2 | Instalar OpenJarvis dentro de WSL2 con el instalador oficial | U | T0.1 | `jarvis doctor` reporta todo verde; CUDA funciona dentro de WSL2 (`nvidia-smi` visible, Ollama usa la GPU); `ollama ps` con un modelo cargado reporta la distribución real de carga CPU/GPU medida (no se exige el 100% GPU como umbral rígido — si una limitación técnica justificada de WSL2/driver/modelo deja parte de la carga en CPU, se documenta la causa; lo que no se acepta es una carga 100% CPU sin explicación, que sí indicaría que el passthrough no funciona). |
| T0.3 | Benchmark de modelos candidatos (Qwen3 8B, GLM-4.7 Flash, Trinity Mini) con `jarvis bench` en distintos contextos reales de uso | C | T0.2 | Tabla con VRAM real medida, tokens/s y latencia por modelo; se fija el modelo por defecto con datos, no con el catálogo. |
| T0.4 | Confirmar que `config.toml` no define ningún engine cloud (`engine.default = "ollama"` es el único motor runtime) — ver política de proveedores en `CLAUDE.md` | C | T0.2 | `jarvis ask` sin ningún engine cloud configurado responde correctamente con Ollama; `config.toml` no contiene ninguna clave de API cloud. |

## Fase 1 — Núcleo operativo

| ID | Tarea | Resp. | Depende de | Criterio de aceptación |
|---|---|---|---|---|
| T1.1 | Verificar agentes existentes (`orchestrator`, `operative`) funcionando end-to-end vía CLI con memoria SQLite activa | C | T0.3 | `jarvis ask` con contexto de memoria responde citando fuente; reinicio no pierde datos. |
| T1.2 | Definir esquema de config.toml del proyecto (`engine.default = "ollama"` sin ningún otro engine cloud configurado, rutas de memoria, flags de voz/puente desactivados por defecto hasta que existan) | C | T1.1 | `config.toml` versionado en el repo (sin secretos, sin claves cloud) con comentarios explicando cada sección. |
| T1.3 | Confirmar que `[mining]` no está presente en ningún config y no se invoca `jarvis mine*`/`jarvis pearl*` en ningún script del proyecto | C | — | Grep de `mine ` / `pearl` en scripts propios devuelve cero resultados de invocación. |
| T1.4 | Mecanismo de degradación controlada cuando Ollama no responde: detección vía `health()`, mensaje claro al usuario, reintento acotado con backoff, y máquina de estados de tareas (`pendiente` / `en_ejecucion` / `interrumpida` / `completada` / `resultado_desconocido`) sobre SQLite, sin segunda base de datos y sin fallback externo (ver `JARVIS_MASTER_PLAN.md` §4) | C | T1.1 | Con Ollama detenido deliberadamente: el sistema responde con un mensaje claro en <2s, reintenta un número acotado de veces, y al reiniciar Ollama la tarea pendiente sigue disponible en memoria con su estado correcto. Prueba específica: una tarea marcada `interrumpida` o `resultado_desconocido` (simulado deteniendo el puente/Ollama a mitad de una acción) **no se reintenta automáticamente** — se le informa al usuario y se espera confirmación explícita antes de reintentar. Ninguna llamada saliente a un proveedor cloud queda registrada en los logs en ningún escenario de fallo. |
| **T1.5** | **Prueba mínima temprana del puente de Windows** (no el puente completo de la Fase 3, alcance limitado a una sola acción trivial, ej. abrir el Bloc de notas desde WSL2 vía el puente): validar en paralelo a esta fase que la comunicación WSL2 ↔ proceso nativo de Windows funciona de extremo a extremo | C | T0.1 (no depende de T1.1-T1.4, corre en paralelo) | Los 4 puntos siguientes, verificados sobre la misma acción trivial (no se amplía el alcance a más acciones): (1) **Autenticación:** una petición con token válido desde `127.0.0.1` se acepta y ejecuta la acción. (2) **Rechazo sin token:** una petición sin token o con token inválido se rechaza explícitamente (código de error claro, la acción NO se ejecuta) — probado al menos 2 veces. (3) **Ejecución real en Windows:** la acción produce un efecto verificable en el sistema Windows real (el proceso del Bloc de notas existe y tiene ventana), no una simulación o mock. (4) **Confirmación del resultado desde WSL2:** el proceso que corre en WSL2 recibe de vuelta, por el mismo canal, la confirmación de que la acción se ejecutó (no solo "petición enviada", sino "resultado verificado"). Los 4 puntos se repiten 3 veces consecutivas sin fallo. Cualquier problema de permisos/UAC/comunicación se detecta aquí, no en la Fase 3. |

## Fase 2 — Voz conversacional

| ID | Tarea | Resp. | Depende de | Criterio de aceptación |
|---|---|---|---|---|
| T2.1 | Integrar VAD (silero-vad o webrtcvad) para detectar fin de intervención | C | T1.1 | Grabación de prueba: el sistema detecta silencio y corta la escucha en <500ms tras dejar de hablar. |
| T2.2 | Integrar wake-word ("Hey JARVIS") con openWakeWord | C | T2.1 | 20 pruebas de activación por voz en español con ≥90% de aciertos y <5% falsos positivos en ambiente normal. |
| T2.3 | Tecla rápida / botón como activación alternativa (hotkey global) — implementación mínima **standalone** (un listener de hotkey global aislado, sin depender del puente completo) para no bloquearse en la Fase 2; se reintegra con el puente de input (T3.4) cuando ese exista | G | T2.1 | Pulsar la tecla activa la escucha igual que la wake-word, sin necesidad de pronunciar nada; funciona incluso antes de que exista el puente completo. |
| T2.4 | Pipeline STT (faster-whisper, español latam) → agente → TTS (Kokoro, voz masculina) | C | T2.1 | Instrucción hablada simple ("abre el navegador") se transcribe, se ejecuta y se responde en voz en <3s de latencia percibida. **Este criterio se cumple con una llamada simple a Kokoro, sin streaming** — el streaming (T2.4b) es una optimización encima de esta base, no un requisito de T2.4. |
| **T2.4b** | **Optimización condicional, no bloqueante:** streaming de TTS por frase (reutilizando el patrón de troceo de `cli/_voice_chat.py` de upstream — ver `PLAN_DE_REUTILIZACION.md` del paquete de investigación) para reducir la latencia percibida de T2.4. **Se intenta en V1, después de que T2.4 ya funcione de forma estable sin streaming.** Si su integración amenaza el calendario o la estabilidad del pipeline de voz, se retira de inmediato sin bloquear T2.4/T2.5/T3.8 y pasa a V2 — nunca al revés. | C | T2.4 (T2.4 debe estar ya estable antes de intentar esto) | Con streaming: la primera porción de la respuesta se escucha antes de que la respuesta completa termine de sintetizarse, sin cortar palabras entre fragmentos. Sin streaming (si se retira): T2.4 sigue funcionando exactamente igual que antes de intentar T2.4b — la retirada no puede dejar el pipeline roto. |
| T2.5 | Barge-in: interrumpir TTS en reproducción al detectar nueva voz | C | T2.4 (no depende de T2.4b) | Hablar mientras JARVIS responde corta el audio en curso y empieza a escuchar la nueva instrucción. |
| T2.6 | Gestión de errores de reconocimiento (reintento, pedir repetición) | G | T2.4 | Transcripción vacía o de baja confianza dispara una respuesta de "no te entendí" en vez de ejecutar una acción incorrecta. |
| T2.7 | Comando único de validación `jarvis test` (análisis estático + suites relevantes + diagnóstico de entorno opcional) | C | T2.4 | Devuelve código de salida 0 solo si `ruff` y las suites de voz/voz-TTS/engine/tareas/CLI pasan; `--env` informa Ollama, GPU/VRAM, `parec`/`paplay` y fuentes de audio; no rompe los comandos existentes. |

## Fase 3 — Puente nativo de Windows

| ID | Tarea | Resp. | Depende de | Criterio de aceptación |
|---|---|---|---|---|
| T3.1 | Diseño de la API del puente (endpoints, payloads, códigos de error, esquema de autenticación por token local) — se apoya en lo aprendido en la prueba mínima T1.5 | C | T1.5 | Documento de interfaz aprobado antes de implementar (incluido en el task pack de Gemini si aplica). |
| T3.2 | Implementación base del puente: servidor local (loopback only) + autenticación por token, token almacenado cifrado con DPAPI (no en texto plano) | C | T3.1 | El puente rechaza peticiones sin token válido y sin origen `127.0.0.1`; log de cada petición con timestamp y resultado; el token en disco no es legible como texto plano. |
| T3.3 | Acciones de ventana: abrir/cerrar app, minimizar/maximizar/cerrar ventana, cambiar entre ventanas, detectar ventana activa | G (bajo spec de C) | T3.2 | Suite de prueba manual: 10 acciones de ventana distintas, 10/10 exitosas y verificables. |
| T3.4 | Control de teclado/mouse: escribir texto, combinaciones de teclas, clic en coordenadas | G (bajo spec de C) | T3.2, **T3.3** (mismo servidor del puente — secuencial, no en paralelo con T3.3, para evitar tocar el mismo archivo desde dos ramas a la vez) | Escribir un texto de prueba en el Bloc de notas y ejecutar una combinación de teclas reconocida (ej. Ctrl+S) funciona de forma determinista. |
| T3.5 | Control de volumen del sistema | G | T3.2, **T3.4** (mismo servidor del puente — secuencial) | Subir/bajar/mutear volumen vía comando de voz o texto cambia el volumen real del sistema. |
| T3.6 | Verificación de resultado de acción (¿la ventana se abrió?, ¿el proceso sigue vivo?) | C | T3.3 | Después de "abrir Brave", el sistema confirma que el proceso existe y la ventana está en foco antes de reportar éxito. |
| T3.7 | Integrar el puente como Tools de OpenJarvis (`windows_control`) vía `ToolRegistry` | C | T3.3-T3.6 | Un agente de OpenJarvis puede invocar `windows_control` como cualquier otra tool y recibe `ToolResult` estándar. |
| **T3.8** | **Hito de integración temprana:** flujo completo voz → comprensión → planificación → ejecución → verificación → respuesta sobre una sola acción trivial (ej. "abre la calculadora y confírmame que se abrió"), usando lo mínimo ya construido de voz (T2.4) y del puente (T3.6, T3.7) — **antes** de seguir ampliando el alcance de Brave/visión/memoria | C | T2.4, T3.6, T3.7 | El flujo completo se ejecuta de principio a fin, por voz, sin intervención manual intermedia, al menos 3 veces consecutivas sin fallo. Si esto falla repetidamente, es la señal para recortar alcance de fases posteriores en vez de seguir sumando módulos nuevos. |

## Fase 4 — Navegación y Brave

| ID | Tarea | Resp. | Depende de | Criterio de aceptación |
|---|---|---|---|---|
| T4.1 | Adaptar `tools/browser.py` para lanzar Brave real (perfil persistente, `headless=False`) en vez del Chromium interno de Playwright | C | T3.7 | El navegador que se abre es Brave, con el perfil del usuario (sesiones ya logueadas), no una ventana headless invisible. |
| T4.2 | Helpers de YouTube/podcasts: buscar canal, buscar video, reproducir, buscar transmisión en vivo | G (bajo spec de C) | T4.1 | "Busca el podcast X y reprodúcelo" ejecuta la búsqueda y comienza la reproducción sin intervención manual. |
| T4.3 | Gestión de pestañas (abrir, cerrar, cambiar) | G | T4.1 | Comandos de voz/texto para cambiar de pestaña funcionan con múltiples pestañas abiertas. |
| T4.4 | Rellenar formularios simples y extraer/resumir contenido de una página (reutilizando `web_search`/`retrieval` existentes) | G (bajo spec de C) | T4.1 | Un formulario de prueba con 3 campos se completa correctamente; un artículo se resume en ≤5 líneas con la fuente citada. |

## Fase 5 — Visión y OCR (a demanda)

| ID | Tarea | Resp. | Depende de | Criterio de aceptación |
|---|---|---|---|---|
| T5.1 | Tool de captura de pantalla (completa o de la ventana activa) | G (bajo spec de C) | T3.7 | Captura guardada/legible por el agente en <1s, sin captura continua en segundo plano. |
| T5.2 | OCR sobre la captura (texto de botones, mensajes de error, formularios) | G | T5.1 | Texto de un diálogo de error de prueba se extrae correctamente ≥90% de los caracteres. |
| T5.3 | Verificación visual post-acción ("¿se abrió el diálogo esperado?") integrada en el flujo de acciones del puente | C | T5.2, T3.6 | Una acción fallida (ej. ventana no se abrió) se detecta por OCR/estado visual y se reporta como fallo, no como éxito falso. |

## Fase 6 — Memoria adaptativa

| ID | Tarea | Resp. | Depende de | Criterio de aceptación |
|---|---|---|---|---|
| T6.1 | Esquema de tablas sobre SQLite existente: memoria personal, episódica, hábitos (sin segunda base de datos independiente) | C | T1.1 | Migraciones aplicadas sobre `memory.db` sin romper el backend `sqlite` existente; tests de regresión de memoria pasan. |
| T6.2 | Registro automático de interacciones relevantes (tarea, resultado, corrección) como memoria episódica | C | T6.1 | Cada acción ejecutada por un agente queda registrada con resultado (éxito/fallo) consultable. |
| T6.3 | Detección de hábitos v1 por frecuencia (apps/sitios/horarios recurrentes) — sin ML, heurística simple y auditable | G (bajo spec de C) | T6.2 | Tras una semana simulada de logs de prueba, el sistema identifica correctamente los 3 patrones más frecuentes inyectados en los datos de prueba. |
| T6.4 | Administración de memoria: consultar, corregir, eliminar, deduplicar, consolidar, caducidad | G (bajo spec de C) | T6.1 | Comandos "olvida X", "corrige X por Y", "qué recuerdas de Z" funcionan y quedan auditados. |
| T6.5 | Proactividad v1: sugerencia basada en hábito detectado, siempre vía flujo de aprobación existente, nunca automática en acciones sensibles | C | T6.3, proactive_tools existente | Una sugerencia ("sueles abrir X a esta hora, ¿lo abro?") se presenta y solo se ejecuta si el usuario aprueba. |

## Fase 7 — Monitor de hardware y Modo Gaming

| ID | Tarea | Resp. | Depende de | Criterio de aceptación |
|---|---|---|---|---|
| T7.1 | Tool de monitor de hardware en el puente: CPU/GPU/RAM/VRAM/temperaturas/procesos vía fuentes verificables (nvidia-smi/pynvml, psutil, WMI) | G (bajo spec de C) | T3.2 | Cada métrica reportada coincide (±margen razonable) con lo mostrado por una herramienta de referencia (ej. Task Manager/HWiNFO64) en una comparación manual. Métricas sin fuente fiable (FPS/frametime/1% lows) se omiten explícitamente, no se estiman. |
| T7.2 | Tres perfiles (`normal`, `gaming_ligero`, `gaming_pesado`) con reglas de qué se pausa/descarga en cada uno | C | T7.1, voz (Fase 2) | Activar `gaming_pesado` descarga el modelo de la VRAM, pausa el scheduler no esencial y detiene la escucha activa (reactivable por hotkey). |
| T7.3 | Reconocimiento de juego activo (Dota 2, GTA V) para sugerir el perfil adecuado, sin forzarlo automáticamente | G | T7.1, T7.2 | Detecta el proceso del juego y sugiere el perfil; el cambio real requiere confirmación o hotkey. |
| T7.4 | Hotkey global de reactivación tras suspensión total de escucha | G | T3.4 (hotkeys), T7.2 | Con la escucha suspendida, la tecla configurada la reactiva de inmediato. |

## Fase 8 — Integración y estabilización

| ID | Tarea | Resp. | Depende de | Criterio de aceptación |
|---|---|---|---|---|
| T8.1 | Pruebas end-to-end de los flujos críticos: voz → acción Windows → verificación; voz → Brave → resumen; hábito detectado → sugerencia aprobada | C | Fases 1-7 | Cada flujo crítico documentado en esta tabla se ejecuta 3 veces sin fallo en condiciones normales. |
| T8.2 | Revisión de seguridad final (puente sin exponer a Internet, sin credenciales en logs, flujo de aprobación cubre todas las acciones sensibles listadas en el plan) | C | Fases 1-7 | Checklist de `§13` del plan funcional revisado explícitamente, ítem por ítem, sin pendientes críticos. |
| T8.3 | Documentación actualizada (README del proyecto JARVIS, estado real vs plan) | G (bajo spec de C) | T8.1 | Documentación refleja exactamente lo que funciona en la entrega, sin funcionalidades "fantasma". |
| T8.4 | Empaquetado de la primera entrega (script de arranque único, checklist de arranque manual si algo no se automatiza aún) | C | T8.1-T8.3 | Un arranque desde cero (reinicio de PC) deja JARVIS operativo siguiendo únicamente el checklist documentado. |

---

## Tareas explícitamente fuera de esta entrega (V2)

Gestión de archivos avanzada (§9), productividad/Gmail (§12), FPS/frametime/1% lows vía RTSS, HUD/interfaz futurista (§14), extensión remota iPhone. No se crean tareas para ellas en este documento; se retoman en una segunda ronda de planificación una vez estabilizada la V1.
