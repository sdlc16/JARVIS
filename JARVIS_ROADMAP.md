# JARVIS — Roadmap hasta el 11 de diciembre de 2026

Planificación: 18 de noviembre de 2026. Entrega: 11 de diciembre de 2026. **23 días.** Reorganizado por dependencias reales (el control de Windows, por ejemplo, necesita el puente antes que los hotkeys de voz o el modo gaming).

## Resumen visual

| Fase | Días | Ventana | Contenido |
|---|---|---|---|
| 0 | 1-2 | 18-19 nov | Entorno: WSL2 + CUDA, instalación, benchmark de modelos |
| 1 | 2 | 20 nov | Núcleo operativo: agentes + memoria + config + degradación controlada (T1.4) + prueba mínima del puente en paralelo (T1.5) |
| 2 | 4 | 21-24 nov | Voz conversacional: VAD, wake-word, STT/TTS, barge-in. **En paralelo:** T3.1, diseño de la API del puente, arranca en cuanto T1.5 (Fase 1) dé resultados — no espera a la Fase 3. |
| 3 | 5 | 25-29 nov | Puente nativo de Windows: implementación (servidor, ventanas, input, volumen, verificación) sobre el diseño de T3.1 ya cerrado durante la Fase 2 |
| 4 | 3 | 30 nov-2 dic | Brave y automatización web |
| 5 | 2 | 3-4 dic | Visión y OCR a demanda |
| 6 | 2 | 5-6 dic | Memoria adaptativa (personal/episódica/hábitos v1) |
| 7 | 1 | 7 dic | Monitor de hardware + Modo Gaming v1 |
| 8 | 4 | 8-11 dic | Integración, pruebas end-to-end y **margen de estabilización dedicado** (ver abajo) |

Total: 23 días. Se gana **1 día neto** recortando Fase 7 de 2 a 1 día (el monitor de hardware y el modo gaming son P1/P2, ver prioridades abajo, y absorben el primer recorte si hace falta) y moviéndolo a Fase 8, que pasa de 3 a 4 días. El resultado es que Fase 8 contiene **3 días completos de estabilización pura** (9-11 dic, ver detalle de Fase 8 más abajo), no solo 1 — el "1 día" es la ganancia neta sobre el calendario anterior, no el tamaño del margen final. Si Fases 2-3 (las más críticas y nuevas) se atrasan más allá de ese margen, se recorta en el orden que marca la tabla de prioridades P0/P1/P2, nunca al azar y nunca eliminando una funcionalidad del plan — se reduce su alcance dentro de V1 o se mueve explícitamente a V2.

## Prioridades (P0/P1/P2) — qué se protege y qué se recorta primero

Ninguna funcionalidad de este documento se elimina. Esta tabla fija el **orden de recorte de alcance** si el calendario se aprieta, protegiendo primero lo que define si JARVIS "es un asistente que ejecuta y comprueba" (objetivo central del proyecto).

| Prioridad | Significado | Incluye |
|---|---|---|
| **P0 — intocable** | Si esto no funciona para el 11 dic, no hay entrega. Nunca se recorta; si hace falta tiempo, se lo quita a P1/P2. | Núcleo operativo (Fase 1) + degradación controlada de Ollama (T1.4) · Voz conversacional mínima (T2.1, T2.2, T2.4, T2.5) · Puente de Windows mínimo (T3.1-T3.3, T3.6, T3.7) · Hito T3.8 (flujo completo voz→comprensión→planificación→ejecución→verificación→respuesta) · Seguridad transversal (auth del puente, flujo de aprobación, auditoría). |
| **P1 — reducible, no eliminable** | Se entrega, pero su alcance puede reducirse si el tiempo aprieta (menos casos cubiertos, no cero casos). | Brave (Fase 4): si falta tiempo, se entrega solo búsqueda+reproducción de YouTube, sin formularios ni resumen de contenido (T4.4 se recorta primero dentro de esta fase). Visión/OCR (Fase 5): se mantiene como verificación de acciones (T5.3), se recorta la robustez general del OCR en casos difíciles. Memoria adaptativa (Fase 6): se mantiene memoria personal/episódica y administración manual (T6.1, T6.2, T6.4); la detección de hábitos (T6.3) y la proactividad (T6.5) son lo primero que se reduce a una versión mínima o se pospone días dentro de la misma fase. **Streaming de TTS por frase (T2.4b):** se intenta en V1 justo después de que T2.4 esté estable sin streaming; es lo primero que se retira de la Fase 2 si amenaza el calendario o la estabilidad del pipeline de voz — nunca bloquea T2.4/T2.5/T3.8, cae a V2 sin dejar nada roto. |
| **P2 — primero en recortarse** | Si el calendario se aprieta, esto es lo primero que se reduce a su versión más simple o se empuja a los últimos días de margen. Sigue estando en el plan — no desaparece. | Monitor de hardware avanzado y Modo Gaming (Fase 7): versión mínima aceptable es perfiles `normal`/`gaming_pesado` únicamente (sin `gaming_ligero` como perfil intermedio) y monitor con solo CPU/GPU/RAM/VRAM (sin lista de procesos ni reconocimiento de juego específico) si hace falta. Hotkeys secundarios (T2.3 standalone, T7.4) pueden quedar con una sola tecla configurada en vez de ser configurables. |

## Margen de estabilización (Fase 8, 8-11 dic)

4 días, no 3: el primer día (8 dic) es integración activa (T8.1); **los 3 últimos días (9-11 dic) son estabilización pura** — a partir del 9 de diciembre no se acepta alcance nuevo de ninguna fase, solo corrección de errores sobre lo ya construido y el checklist de entrega (T8.2-T8.4). Si para el 8 de diciembre el hito T3.8 (P0) no se ha cumplido, estos 3 días se dedican exclusivamente a cerrarlo, sacrificando primero todo lo que quede pendiente de P2 y luego de P1.

## Detalle por fase

### Fase 0 — Entorno (18-19 nov)
Confirmar WSL2+CUDA funcionando con la RTX 3060, instalar OpenJarvis oficialmente, medir VRAM/latencia real de los modelos candidatos con `jarvis bench`, confirmar que `config.toml` no define ningún engine cloud (política de proveedores en `CLAUDE.md`). **Bloqueante para todo lo demás.**

### Fase 1 — Núcleo operativo (20 nov)
Verificar que agentes + memoria + scheduler de OpenJarvis responden correctamente vía CLI con el modelo elegido. Definir `config.toml` del proyecto. Confirmar que Pearl Mining permanece inerte. Implementar el mecanismo de degradación controlada si Ollama falla (T1.4 — detección, mensaje claro, reintento acotado, conservación de tareas pendientes, cero envíos externos). **En paralelo**, arrancar la prueba mínima del puente de Windows (T1.5) para validar cuanto antes comunicación WSL2↔Windows, permisos y ejecución de una acción trivial — esto no es el puente completo (eso es la Fase 3), es una validación temprana de riesgo.

### Fase 2 — Voz conversacional (21-24 nov)
VAD → wake-word ("Hey JARVIS") → STT (faster-whisper, español latam) → agente → TTS (Kokoro) → barge-in → gestión de errores de reconocimiento. Esta fase define si JARVIS "se siente" como un asistente real; se prioriza sobre cualquier otra cosa si hay que elegir. **En paralelo, no en secuencia:** T3.1 (diseño completo de la API del puente) arranca en esta misma ventana, apoyándose en los resultados de T1.5 (Fase 1) — así la Fase 3 empieza directamente a implementar sobre un diseño ya cerrado, en vez de perder días de la Fase 3 diseñando.

### Fase 3 — Puente nativo de Windows (25-29 nov)
**Implementación** sobre el diseño ya cerrado en T3.1: servidor local con autenticación → acciones de ventana → control de teclado/mouse → volumen → verificación de resultado → integración como Tools de OpenJarvis. Es la fase de mayor riesgo técnico (ver `Riesgos` abajo) — el riesgo se adelanta en parte por T1.5 (Fase 1) y T3.1 (en paralelo a la Fase 2), pero la implementación completa sigue siendo la pieza más incierta del roadmap.

**Hito T3.8 (fin de esta fase, no después):** antes de empezar la Fase 4, se demuestra el flujo completo **voz → comprensión → planificación → ejecución → verificación → respuesta** sobre una sola acción trivial, de principio a fin, por voz. Este es el requisito más importante de todo el roadmap — es la prueba de que JARVIS ya es "un asistente que ejecuta y comprueba", no solo módulos sueltos. Si no se cumple en este punto, se recorta alcance de las fases siguientes (ver tabla de riesgos) en vez de seguir construyendo módulos nuevos sobre una base no validada.

### Fase 4 — Brave y automatización web (30 nov-2 dic)
Adaptar `browser.py` a Brave real con perfil persistente, helpers de YouTube/podcasts, gestión de pestañas, formularios simples, resumen de contenido.

### Fase 5 — Visión y OCR (3-4 dic)
Captura a demanda + OCR + verificación visual de acciones del puente (cierra el ciclo "ejecutar → comprobar" del objetivo principal).

### Fase 6 — Memoria adaptativa (5-6 dic)
Esquema sobre SQLite existente (personal/episódica/hábitos), registro automático de interacciones, detección de hábitos v1 por frecuencia, administración de memoria (consultar/corregir/eliminar/deduplicar), proactividad v1 vía flujo de aprobación existente.

### Fase 7 — Monitor de hardware y Modo Gaming (7 dic)
Lectura de CPU/GPU/RAM/VRAM/temperaturas/procesos desde fuentes verificables; perfiles `normal`/`gaming_ligero`/`gaming_pesado`; reconocimiento de Dota 2/GTA V como sugerencia, no cambio forzado; hotkey de reactivación. **Prioridad P2** (ver tabla de prioridades): si esta fase no cabe entera en su día, se entrega la versión mínima (`normal`/`gaming_pesado` sin perfil intermedio, monitor sin lista de procesos) y el resto se completa durante el margen de estabilización si queda tiempo, o pasa a V2.

### Fase 8 — Integración y estabilización (8-11 dic)
Día 1 (8 dic): integración activa, pruebas end-to-end de los flujos críticos (T8.1). Días 2-4 (9-11 dic): **margen de estabilización puro** — revisión de seguridad completa, documentación final, empaquetado de arranque, y corrección de errores; no se acepta alcance nuevo de ninguna fase anterior en estos 3 días.

## Funcionalidades listas para la primera entrega (V1, 11 dic)

- Chat y ejecución de tareas con el núcleo de OpenJarvis (Ollama local, único motor runtime, sin fallback cloud).
- Conversación por voz con activación por "Hey JARVIS" o hotkey, en español latam, con barge-in. **Streaming de TTS por frase (T2.4b) incluido si no amenaza el calendario/estabilidad** — condicional, no garantizado; si se retira, cae a V2 sin afectar al resto de la entrega (ver tabla de prioridades).
- Control nativo de Windows: abrir/cerrar apps, gestión de ventanas, teclado/mouse, hotkeys, volumen, detección de ventana activa, verificación básica de resultado.
- Automatización de Brave: búsqueda y reproducción en YouTube/podcasts, gestión de pestañas, formularios simples, resumen de contenido.
- Visión a demanda: captura de pantalla + OCR + verificación visual de acciones.
- Memoria personal y episódica persistente, con administración manual (consultar/corregir/eliminar).
- Detección de hábitos v1 (por frecuencia) con sugerencias proactivas no invasivas, siempre aprobadas por el usuario.
- Monitor de hardware básico (CPU/GPU/RAM/VRAM/temperaturas/procesos) sin métricas inventadas.
- Modo Gaming con 3 perfiles y reconocimiento de Dota 2/GTA V, sin promesas de FPS.
- Seguridad transversal: puente local sin exposición a Internet, flujo de aprobación para acciones sensibles, auditoría de operaciones.

## Funcionalidades que quedan para la segunda versión (V2, después del 11 dic)

- Gestión de archivos avanzada (organización de escritorio, duplicados, limpieza).
- Productividad personal (Gmail, calendario, borradores, recordatorios).
- Aprendizaje de hábitos más allá de frecuencia simple (modelos de patrones, excepciones, corrección fina).
- Métricas de gaming avanzadas (FPS, frametime, 1% lows) vía RTSS — solo si se confirma una fuente de lectura fiable; no se fuerza su inclusión en V1.
- Interfaz HUD futurista (núcleo animado, ondas de voz, panel de estado/recursos/memoria/historial).
- Extensión remota a iPhone 16 Pro Max.
- Reconocimiento visual avanzado de elementos de UI más allá de OCR (detección de botones por modelo de visión).
- **Streaming de TTS por frase (T2.4b), solo si se retiró de V1** por amenazar el calendario o la estabilidad del pipeline de voz — no es una funcionalidad de V2 "de origen", es la misma optimización de T2.4b degradada aquí si no cupo en V1.

## Riesgos y dependencias técnicas

| Riesgo | Impacto | Mitigación |
|---|---|---|
| El puente de Windows (Fase 3) resulta más complejo de lo previsto (permisos, UAC, apps que bloquean input simulado) | Alto — bloquea Fases 4, 5, 7 y parte de la 2 (hotkeys) | Riesgo ya adelantado por T1.5 (prueba mínima en Fase 1) y T3.1 (diseño en paralelo a la Fase 2, no dentro de la Fase 3); si aun así se atrasa, se recorta primero P2 (Fase 7) y después el alcance reducible de P1 (ver tabla de prioridades), nunca el núcleo P0 del puente (T3.1-T3.3, T3.6, T3.7). |
| VRAM real de los modelos candidatos no coincide con lo esperado (catálogo no confiable, ver auditoría) | Medio — puede forzar modelo más pequeño de lo deseado | Benchmark real en Fase 0, antes de construir nada encima; decisión temprana. |
| Latencia de wake-word/STT/TTS local insuficiente para sensación de "tiempo real" en la RTX 3060 compartida con el modelo LLM | Medio — degrada la experiencia de voz | Medir con el modelo LLM cargado simultáneamente, no en aislamiento; considerar modelo STT más pequeño si hace falta. |
| OCR impreciso en elementos de UI pequeños o fuentes no estándar | Medio — afecta verificación de acciones | OCR es complemento de verificación de estado de proceso/ventana (más fiable), no único mecanismo. |
| Ollama es el único motor runtime, sin proveedor cloud alternativo: si el modelo local falla o el servicio se cae, no hay plan B automático | Medio — puede dejar a JARVIS sin respuesta | Mitigado por diseño, no por un proveedor alternativo: mecanismo de degradación controlada (T1.4 — detección vía `health()`, mensaje claro, reintento acotado, conservación de tareas pendientes en memoria, cero llamadas externas automáticas) + dimensionar el modelo con margen de VRAM suficiente (T0.3) para minimizar caídas. |
| Límites de cuota de Gemini/Antigravity como herramienta de desarrollo se agotan durante jornadas intensivas de programación | Bajo — no afecta a JARVIS en producción, afecta solo la velocidad de desarrollo | Usar Claude Code como arquitecto principal de forma continua; reservar Gemini/Antigravity para tareas acotadas del `GEMINI_TASK_PACK.md`, no como dependencia continua de todo el flujo de desarrollo. |
| Doble trabajo o conflictos de archivo entre Claude y Gemini | Bajo si se respeta `CLAUDE_GEMINI_WORKFLOW.md` | Propiedad de archivos declarada por tarea, una rama por tarea, revisión obligatoria antes de integrar. |

## Tareas que pueden empezar inmediatamente (no dependen de nada más)

- T0.1–T0.4 (entorno, benchmark, config sin engines cloud) — ver `DEVELOPMENT_TASKS.md`.
- T1.5 (prueba mínima del puente de Windows) puede empezar tan pronto como esté WSL2 funcionando (T0.1), **en paralelo** a toda la Fase 1 y la Fase 2 — es la validación temprana de riesgo que pediste, no espera a la Fase 3.
- T3.1 (diseño completo de la API del puente) se apoya en los resultados de T1.5 y puede empezar en paralelo a la Fase 2.
- T6.1 (esquema de memoria sobre SQLite) puede diseñarse en paralelo desde la Fase 1, independientemente de voz/puente.
