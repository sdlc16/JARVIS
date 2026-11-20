# GEMINI_TASK_PACK.md

Instrucciones **autosuficientes** para Gemini/Antigravity. No dependen de ninguna conversación previa. Cada tarea aquí corresponde a una fila de `DEVELOPMENT_TASKS.md` marcada como responsable **G** (o **G bajo spec de C**, lo que significa que Claude ya fijó la interfaz exacta antes de delegar — si una tarea de esta lista todavía no tiene interfaz fijada, **no se empieza**: se marca como bloqueada y se devuelve a Claude).

## Contexto del proyecto (necesario para cualquier tarea)

Este repositorio (`sdlc16/JARVIS`) es un fork de trabajo de **OpenJarvis** (framework de Stanford para IA local-first: Python en `src/openjarvis/`, patrón de registro por decoradores — `ToolRegistry`, `EngineRegistry`, `AgentRegistry`, `ChannelRegistry` en `src/openjarvis/core/registry.py`). El objetivo es convertirlo en un asistente personal de escritorio para Windows (vía WSL2 + un puente nativo de Windows separado), sin reconstruir el framework.

**Desambiguación importante:** este documento se dirige a *Gemini/Antigravity como asistente de desarrollo* (quien escribe el código de estas tareas). Esto es distinto de "la API de Gemini como motor de inferencia" — el único motor runtime de JARVIS es Ollama (política de proveedores completa en `CLAUDE.md`). Ninguna tarea de este paquete debe añadir código que haga que JARVIS llame a un proveedor cloud como motor runtime.

Reglas que aplican a **todas** las tareas de este paquete:

1. No instalar ni activar servicios de pago. No tocar nada relacionado con `jarvis mine`/`jarvis pearl`/sección `[mining]`.
2. Todo componente nuevo que actúe como herramienta de un agente se registra vía `@ToolRegistry.register("nombre")` siguiendo el patrón ya usado en `src/openjarvis/tools/*.py` (mirar cualquier archivo existente ahí como plantilla, p. ej. `src/openjarvis/tools/calculator.py` para la forma mínima de una tool).
3. Todo código nuevo en `src/openjarvis/` lleva un test correspondiente en `tests/` siguiendo la misma estructura de carpetas que `src/`.
4. No modificar archivos fuera de los listados explícitamente en "Archivos a tocar" de cada tarea. Si se necesita tocar algo más, parar y reportar el bloqueo en vez de improvisar.
5. Rama: `gemini/<area>-<id-tarea>` partiendo de la rama de integración vigente. No hacer merge directamente — se entrega como diff/PR para revisión de Claude.
6. Nunca usar OAuth de Gemini CLI ni mecanismos de autenticación no oficiales para alimentar herramientas de terceros.

---

## Tarea G-T3.3 — Acciones de ventana (puente de Windows)

**Objetivo:** implementar en el puente nativo de Windows (proceso Python o .NET separado, fuera de WSL2) los handlers para: abrir aplicación por nombre/ruta, cerrar ventana, minimizar, maximizar, cambiar entre ventanas (alt-tab programático), detectar la ventana/aplicación activa.

**Interfaz esperada** (HTTP local, JSON, autenticación por header `X-Jarvis-Token`):

```
POST /window/open      {"target": "brave.exe"}            -> {"success": bool, "pid": int|null, "error": str|null}
POST /window/close     {"handle": "<window_handle>"}      -> {"success": bool, "error": str|null}
POST /window/minimize  {"handle": "<window_handle>"}      -> {"success": bool, "error": str|null}
POST /window/maximize  {"handle": "<window_handle>"}      -> {"success": bool, "error": str|null}
POST /window/switch    {"handle": "<window_handle>"}      -> {"success": bool, "error": str|null}
GET  /window/active    (sin body)                         -> {"handle": str, "title": str, "process": str}
```

**Archivos a tocar:** nuevo directorio `windows_bridge/` en la raíz del repo (fuera de `src/openjarvis/`, es un proceso Windows separado, no parte del paquete Python de OpenJarvis). No tocar nada dentro de `src/openjarvis/`.

**Dependencias:** T3.1 (diseño de la API aprobado), T3.2 (servidor base del puente con autenticación ya funcionando).

**Criterio de aceptación:** 10 acciones de ventana distintas probadas manualmente (abrir 3 apps distintas, minimizar/maximizar cada una, cambiar entre ellas, cerrar), 10/10 exitosas, cada respuesta con el `success`/`error` correcto. El servidor rechaza peticiones sin token o que no vengan de `127.0.0.1`.

**Pruebas:** test automatizado que mockee la API de Windows usada (enumeración/control de ventanas) y verifique que cada handler construye la llamada correcta y devuelve el JSON esperado para éxito y para error; la verificación manual de las 10 acciones es adicional, no sustituye el test automatizado.

**Si algo no está claro** (ej. qué librería de Windows usar para enumerar ventanas): detenerse y reportarlo como bloqueo — no decidir la arquitectura del puente, eso lo fija Claude en `T3.1`.

---

## Tarea G-T3.4 — Control de teclado y mouse

**Objetivo:** extender el mismo puente con: escribir texto en el control con foco, ejecutar combinaciones de teclas (ej. `ctrl+s`), clic en coordenadas de pantalla.

**Interfaz esperada:**

```
POST /input/type       {"text": "hola mundo"}                     -> {"success": bool, "error": str|null}
POST /input/hotkey     {"keys": ["ctrl", "s"]}                    -> {"success": bool, "error": str|null}
POST /input/click      {"x": 500, "y": 300, "button": "left"}     -> {"success": bool, "error": str|null}
```

**Archivos a tocar:** `windows_bridge/` (mismos archivos que G-T3.3, misma base de servidor).

**Dependencias:** T3.2, **y G-T3.3 ya integrada** (no en paralelo — mismo archivo, ver `DEVELOPMENT_TASKS.md`).

**Criterio de aceptación:** escribir un texto de prueba en el Bloc de notas da como resultado exactamente ese texto; `ctrl+s` sobre un documento de prueba dispara el diálogo de guardado esperado; un clic en coordenadas conocidas activa el control correcto (verificable visualmente).

**Pruebas:** test automatizado que mockee la API de input simulado (teclado/mouse) y verifique los parámetros exactos enviados para cada tipo de acción; la verificación manual del criterio de aceptación es adicional.

---

## Tarea G-T3.5 — Control de volumen

**Objetivo:** endpoint para subir, bajar, mutear y fijar volumen del sistema (no de una app específica en esta versión).

**Interfaz esperada:**

```
POST /audio/volume   {"action": "up"|"down"|"mute"|"set", "value": 0-100 (solo si action=="set")} -> {"success": bool, "level": int, "error": str|null}
```

**Archivos a tocar:** `windows_bridge/` (mismo servidor).

**Dependencias:** T3.2, **y G-T3.4 ya integrada** (no en paralelo — mismo archivo).

**Criterio de aceptación:** cada acción cambia el volumen real del sistema de forma verificable (comparar antes/después con el control de volumen de Windows).

**Pruebas:** test automatizado que mockee la API de audio del sistema y verifique que cada acción (`up`/`down`/`mute`/`set`) llama a la función correcta con el valor correcto; la verificación manual del criterio es adicional.

---

## Tarea G-T4.2 — Helpers de YouTube/podcasts sobre Brave

**Objetivo:** funciones Python que, usando el objeto de página de Playwright ya conectado a Brave (expuesto por la tool `browser` adaptada en la tarea `T4.1`, que resuelve Claude antes de delegar esta), permitan: buscar un canal por nombre, buscar un video por texto, reproducir el primer resultado, buscar transmisiones en vivo de un canal/tema.

**Dependencias:** `T4.1` (adaptación de `tools/browser.py` a Brave real) debe estar ya integrada. Si no lo está, esta tarea queda bloqueada — no se reimplementa la conexión a Brave por cuenta propia.

**Archivos a tocar:** nuevo archivo `src/openjarvis/tools/youtube_helpers.py` (funciones auxiliares, sin registrar como tool nueva todavía — eso lo decide Claude al integrar si conviene exponerlas como tool independiente o como parte de `browser.py`).

**Criterio de aceptación:** dado un nombre de canal real y un texto de búsqueda, el helper deja la página de Brave mostrando el video/canal/stream correcto y, si se pide "reproducir", el video está en reproducción (verificable por captura o por el estado del reproductor en el DOM).

**Pruebas:** mínimo un test que mockee la página de Playwright y verifique que se llaman los selectores/acciones esperadas, más una prueba manual documentada contra YouTube real (no se automatiza contra YouTube real en CI, solo localmente).

---

## Tarea G-T5.1 / G-T5.2 — Captura de pantalla + OCR

**Objetivo:** una tool de OpenJarvis que capture la pantalla completa o la ventana activa (vía el endpoint `/window/active` del puente) y devuelva el texto reconocido por OCR.

**Interfaz esperada (tool de OpenJarvis, no del puente):**

```python
@ToolRegistry.register("screen_vision")
class ScreenVisionTool(BaseTool):
    tool_id = "screen_vision"
    # método principal: captura (completa o de la ventana activa) + OCR, devuelve texto + metadata (región, timestamp)
```

**Archivos a tocar:** `src/openjarvis/tools/screen_vision.py` (nuevo), `tests/tools/test_screen_vision.py` (nuevo). La captura en sí llama al puente de Windows (`windows_bridge/`) para obtener la imagen, ya que WSL2 no tiene acceso directo a la pantalla de Windows — no intentar capturar desde dentro de WSL2 con herramientas Linux.

**Dependencias:** T3.7 (puente integrado como Tool de OpenJarvis; esta tool necesita poder invocar el endpoint de captura del puente).

**Criterio de aceptación:** sobre un diálogo de error de prueba con texto conocido, el OCR recupera ≥90% de los caracteres correctamente. La tool **nunca** se ejecuta en bucle automático — solo a demanda, una captura por invocación.

**Pruebas:** test unitario que mockee la respuesta del puente (imagen de prueba) y verifique que el OCR se invoca y el texto esperado se extrae; la medición de ≥90% de caracteres se corre como prueba manual documentada, no en CI (depende de la imagen real de prueba).

---

## Tarea G-T6.3 — Detección de hábitos v1 (por frecuencia)

**Objetivo:** función que, leyendo la tabla de memoria episódica (esquema ya definido por Claude en `T6.1` — **no inventar el esquema**, pedir a Claude la definición exacta de columnas antes de empezar si no está disponible), identifique los patrones más frecuentes (app/sitio/horario recurrente) por conteo simple, sin machine learning.

**Archivos a tocar:** `src/openjarvis/tools/habit_detection.py` (nuevo, sobre el esquema ya existente), `tests/tools/test_habit_detection.py` (nuevo).

**Dependencias:** T6.1 (esquema de memoria aprobado), T6.2 (registro automático de interacciones ya poblando la tabla episódica).

**Criterio de aceptación:** con un set de datos de prueba sintético (provisto en el test, no datos reales del usuario) que contenga 3 patrones claros inyectados deliberadamente, la función los identifica correctamente como los 3 más frecuentes, por encima de ruido aleatorio también incluido en el set de prueba.

**Pruebas:** el propio set de datos sintético con los 3 patrones inyectados y ruido aleatorio, usado como fixture del test — no se usan datos reales del usuario en ningún test.

---

## Tarea G-T6.4 — Administración de memoria

**Objetivo:** funciones de consulta/corrección/eliminación/deduplicación/consolidación sobre la memoria existente, expuestas como comandos que un agente pueda invocar (reutilizar el patrón de `tools/memory_manage.py` ya existente en el repo como referencia de estilo, no reemplazarlo si ya cubre parte de esto — leer ese archivo primero).

**Archivos a tocar:** extender `src/openjarvis/tools/memory_manage.py` si ya existe funcionalidad similar, o crear `src/openjarvis/tools/memory_admin.py` si Claude determina que conviene separarlo (confirmar con Claude cuál de las dos antes de escribir código, es una decisión de diseño, no de implementación).

**Dependencias:** T6.1 (esquema de memoria aprobado).

**Criterio de aceptación:** comandos de prueba "olvida X", "corrige X por Y", "qué recuerdas de Z" producen el efecto correcto sobre una base de datos de prueba (no sobre datos reales) y quedan registrados en el log de auditoría correspondiente.

**Pruebas:** tests sobre una base de datos de prueba (fixture aislada, nunca la base real) cubriendo cada comando y su registro en el log de auditoría.

---

## Tarea G-T7.1 — Monitor de hardware (lectura de datos)

**Objetivo:** endpoint del puente que devuelva CPU%, GPU%, RAM usada/total, VRAM usada/total, temperaturas (si hay fuente disponible), lista de procesos con mayor consumo. **No inventar ni estimar ningún valor sin fuente real** — si una métrica no tiene fuente fiable disponible en el sistema (ej. FPS/frametime/1% lows sin RTSS configurado), el campo correspondiente se omite o se marca explícitamente como `"available": false`, nunca se rellena con un valor inventado.

**Interfaz esperada:**

```
GET /hardware/status -> {
  "cpu_percent": float, "ram_used_gb": float, "ram_total_gb": float,
  "gpu_percent": float|null, "vram_used_gb": float|null, "vram_total_gb": float|null,
  "temps": {"cpu": float|null, "gpu": float|null},
  "top_processes": [{"name": str, "cpu_percent": float, "ram_mb": float}],
  "fps": null, "frametime_ms": null, "lows_1pct": null   // null explícito mientras no haya fuente RTSS confirmada
}
```

**Archivos a tocar:** `windows_bridge/` (nuevo módulo de hardware dentro del mismo proceso del puente).

**Dependencias:** T3.2 (servidor base del puente).

**Criterio de aceptación:** cada valor numérico devuelto coincide, con margen razonable, con lo mostrado por una herramienta de referencia (Task Manager o similar) comprobado manualmente. Ningún campo de FPS/frametime/1% lows contiene un valor no verificado.

**Pruebas:** test automatizado que mockee las fuentes de datos (salida de `nvidia-smi`/`pynvml`, `psutil`) y verifique que el JSON de salida tiene la forma exacta esperada, incluyendo los campos `null` cuando la fuente no está disponible.

---

## Tarea G-T7.3 — Reconocimiento de juego activo

**Objetivo:** función que, a partir de `top_processes` (tarea anterior) o de `/window/active`, detecte si Dota 2 o GTA V están en ejecución, y lo exponga como campo adicional (`"active_game": "dota2"|"gtav"|null`).

**Archivos a tocar:** mismo módulo de `windows_bridge/` que `G-T7.1`.

**Dependencias:** T7.1 (`top_processes` ya disponible en el endpoint de hardware), **y G-T7.1 ya integrada** (no en paralelo — mismo archivo).

**Criterio de aceptación:** con el proceso de cada juego en ejecución (nombre de proceso real, no simulado), el campo se rellena correctamente; con ningún juego activo, el campo es `null`. No se activa ningún cambio de perfil automáticamente desde esta función — solo reporta.

**Pruebas:** test automatizado con una lista de procesos simulada (incluyendo el proceso real de Dota 2/GTA V y casos sin ningún juego) que verifique la detección correcta.

---

## Tarea G-T2.3 — Hotkey global standalone de activación por voz

**Objetivo:** un listener de tecla rápida global en Windows (ej. `Ctrl+Shift+J`, configurable) que, al pulsarse, active la escucha de voz exactamente igual que la wake-word — **sin depender del puente completo** (T3.1-T3.7 pueden no existir todavía). Es un proceso/script nativo de Windows pequeño y aislado, no un endpoint del puente.

**Interfaz esperada:** un script standalone (`hotkey_listener.py` o similar) que:
- Registra la combinación de teclas vía `RegisterHotKey` de la API de Windows (ver referencia de técnica en `AndreyLebedenko/jarvis-local` dentro de `JARVIS_RESEARCH_PACK`, solo como consulta de diseño, no se copia código).
- Al detectarse la combinación, notifica al proceso de voz (que corre en WSL2) mediante el mecanismo más simple posible: una petición HTTP a un puerto local fijo que el pipeline de voz expone (ej. `POST http://localhost:<puerto>/activate`), o alternativamente escribir un archivo de señal que el pipeline de voz vigile — **la elección entre estas dos opciones la decide Claude al revisar el diff, no Gemini por iniciativa propia**; implementar la opción HTTP como predeterminada y dejar la de archivo comentada como alternativa.
- Expone una función `main()` ejecutable como script independiente (`python hotkey_listener.py`), sin dependencias de `openjarvis.*` (standalone real).

**Archivos a tocar:** nuevo archivo `windows_bridge/hotkey_listener.py` (o `tools/windows_hotkey/hotkey_listener.py` si Claude decide que debe vivir junto al resto del puente — confirmar con Claude antes de crear la carpeta).

**Dependencias:** T2.1 (el pipeline de voz debe tener un punto de entrada "activar escucha" ya definido, aunque sea mínimo, para que este listener tenga a quién notificar).

**Criterio de aceptación:** con el pipeline de voz corriendo, pulsar la combinación de teclas configurada activa la escucha exactamente como si se hubiera dicho "Hey JARVIS" — verificable en Windows real, no en este sandbox.

**Pruebas:** test unitario que mockee la llamada HTTP de notificación y verifique que se dispara exactamente una vez por pulsación (no repetida mientras se mantiene presionada); prueba manual real en Windows pendiente de hardware.

---

## Tarea G-T2.6 — Gestión de errores de reconocimiento de voz

**Objetivo:** función que, dado el resultado de una transcripción STT (texto + score de confianza si el backend lo expone), decida si la instrucción es lo bastante fiable para ejecutarse o si JARVIS debe responder "no te entendí, ¿puedes repetirlo?" en vez de actuar sobre una transcripción dudosa.

**Interfaz esperada:**
```python
def should_retry_transcription(text: str, confidence: float | None = None, *, min_chars: int = 2) -> bool:
    """True si la transcripción es demasiado pobre/vacía para actuar sobre ella."""
```
Casos que deben devolver `True` (pedir repetición): texto vacío o solo espacios, texto de 1 carácter, `confidence` por debajo de un umbral configurable (si el backend STT lo da; faster-whisper expone `avg_logprob`/`no_speech_prob` por segmento — usar `no_speech_prob` alto como señal, no inventar un umbral sin base).

**Archivos a tocar:** nuevo archivo `src/openjarvis/speech/error_handling.py` (o ubicarlo junto al pipeline de voz que Claude defina en T2.4, si para entonces ya existe un módulo más natural — confirmar con Claude antes de crear el archivo si T2.4 ya está en marcha).

**Dependencias:** T2.4 (necesita saber la forma exacta del resultado que devuelve el pipeline STT elegido, para no inventar un contrato que luego no coincida).

**Criterio de aceptación:** con una transcripción vacía o de `no_speech_prob` alto, el sistema responde pidiendo repetición en vez de ejecutar una acción; con una transcripción normal, no se activa este camino.

**Pruebas:** tests unitarios con transcripciones sintéticas (vacía, de baja confianza, normal) cubriendo los 3 casos del criterio de aceptación — totalmente ejecutable sin hardware de voz real.

---

## Tarea G-T4.3 — Gestión de pestañas de Brave

**Objetivo:** abrir, cerrar y cambiar entre varias pestañas de Brave desde un agente de OpenJarvis.

**Precondición dura:** `T4.1` (adaptación de `tools/browser.py` a Brave real, con `headless=False` y perfil persistente) debe estar ya integrada — hoy `_BrowserSession` en `browser.py` solo gestiona **una** página (`self._page`), sin lista de pestañas; no se puede implementar gestión de pestañas sobre la sesión actual sin que T4.1 la haya extendido primero a multi-página.

**Interfaz esperada (nuevo archivo, no modificar `browser.py` directamente — evita competir con T4.1/T4.2/T4.4 por el mismo archivo si corren en paralelo):**
```python
@ToolRegistry.register("browser_tabs")
class BrowserTabsTool(BaseTool):
    # acciones: "open" (nueva pestaña con URL opcional), "close" (por índice),
    # "switch" (por índice), "list" (devuelve títulos/URLs de todas las pestañas abiertas)
```

**Archivos a tocar:** nuevo archivo `src/openjarvis/tools/browser_tabs.py`, `tests/tools/test_browser_tabs.py` (nuevo). Lee (no modifica) `browser.py` para reutilizar `_session` si T4.1 ya expone una lista de páginas; si no la expone todavía, **detenerse y reportar el bloqueo a Claude** en vez de modificar `browser.py` por cuenta propia.

**Dependencias:** T4.1 (multi-página ya disponible en `_BrowserSession`).

**Criterio de aceptación:** con 3 pestañas abiertas, cambiar entre ellas por índice funciona, cerrar una no afecta a las demás, `list` devuelve exactamente las pestañas reales abiertas en ese momento.

**Pruebas:** test que mockee `_BrowserSession`/Playwright y verifique cada acción (`open`/`close`/`switch`/`list`) sobre un conjunto simulado de páginas; prueba manual contra Brave real pendiente de hardware.

---

## Tarea G-T4.4 — Formularios simples y extracción/resumen de contenido

**Objetivo:** (a) rellenar un formulario web de varios campos a partir de una instrucción en lenguaje natural, reutilizando `BrowserTypeTool`/`BrowserClickTool` ya existentes; (b) resumir el contenido de una página reutilizando `BrowserExtractTool` ya existente + una llamada al LLM local para condensar el texto extraído.

**Interfaz esperada:**
```python
@ToolRegistry.register("browser_fill_form")
class BrowserFillFormTool(BaseTool):
    # params: {"fields": [{"selector": str, "value": str}, ...], "submit_selector": str | None}
    # Internamente llama BrowserTypeTool por cada campo y, si se da submit_selector, BrowserClickTool al final.

@ToolRegistry.register("browser_summarize")
class BrowserSummarizeTool(BaseTool):
    # params: {"url": str | None, "max_sentences": int = 5}
    # Si se da url, navega primero (BrowserNavigateTool); extrae con BrowserExtractTool;
    # resume con una llamada al engine configurado (el que ya use el agente, nunca uno nuevo).
```

**Archivos a tocar:** nuevo archivo `src/openjarvis/tools/browser_forms.py` (ambas tools, para no competir por `browser.py`), `tests/tools/test_browser_forms.py` (nuevo). No modificar `browser.py` — solo importar y componer sus tools existentes.

**Dependencias:** T4.1 (Brave real adaptado).

**Criterio de aceptación:** un formulario de prueba con 3 campos se completa correctamente (verificable por captura o por el valor final de cada campo en el DOM); un artículo de prueba se resume en ≤5 frases citando la fuente (URL).

**Pruebas:** test que mockee las tools de `browser.py` compuestas y verifique que `browser_fill_form` llama `BrowserTypeTool` una vez por campo con los valores correctos; test que mockee `BrowserExtractTool` + el engine y verifique que `browser_summarize` nunca llama a un segundo engine (mismo principio de "no fallback" que `engine/degradation.py`, aplicado aquí: usa el engine que el agente ya tiene, no instancia uno propio).

---

## Tarea G-T7.4 — Hotkey global de reactivación tras modo Gaming pesado

**Objetivo:** tecla rápida global en Windows que, con la escucha de JARVIS completamente suspendida (modo `gaming_pesado`), la reactive de inmediato — mismo mecanismo técnico que G-T2.3 (listener nativo + notificación al proceso de voz), pero disparando además la salida del perfil `gaming_pesado` hacia `normal` (o el perfil que corresponda) en el puente.

**Interfaz esperada:** segunda combinación de teclas registrada en el mismo proceso de `hotkey_listener.py`, con su propio callback:
```python
def register_gaming_reactivation_hotkey(
    combo: str = "ctrl+shift+g",
    *,
    on_trigger: Callable[[], None],
) -> None:
    """Registra la combinación de reactivación. on_trigger() hace dos cosas,
    en este orden: (1) notifica al pipeline de voz para reanudar la escucha
    (mismo mecanismo HTTP que G-T2.3), (2) llama al endpoint del puente que
    cambia el perfil de gaming_pesado a normal."""
```
`on_trigger` no ejecuta las dos acciones si la primera falla silenciosamente — si la notificación de voz falla, se registra el error y NO se cambia el perfil, para no dejar el sistema en un estado a medias (escucha suspendida pero perfil ya cambiado, o viceversa).

**Archivos a tocar:** extensión de `windows_bridge/hotkey_listener.py` (el mismo de G-T2.3, una vez integrado) para registrar una segunda combinación de teclas; **no crear un segundo listener independiente** — un solo proceso que registra ambas combinaciones es más simple y evita dos procesos compitiendo por el mismo mecanismo de `RegisterHotKey`.

**Dependencias:** G-T2.3 ya integrada (mismo archivo, secuencial, no en paralelo), T7.2 (perfiles de Gaming ya definidos — necesita saber a qué perfil volver).

**Criterio de aceptación:** con `gaming_pesado` activo y la escucha suspendida, pulsar la combinación la reactiva de inmediato y el perfil vuelve a `normal` (o al que corresponda), verificable en Windows real.

**Pruebas:** test unitario que mockee el cambio de perfil y verifique que se invoca exactamente una vez por pulsación; prueba manual real en Windows pendiente de hardware.

---

## Tareas que SÍ cumplen ya los 6 campos (actualizado)

Las 5 tareas que antes estaban bloqueadas por falta de especificación (**T2.3, T2.6, T4.3, T4.4, T7.4**) ya tienen su especificación completa más arriba (secciones **G-T2.3**, **G-T2.6**, **G-T4.3**, **G-T4.4**, **G-T7.4**), con los 6 campos exigidos por `CLAUDE_GEMINI_WORKFLOW.md`. **Siguen sin poder entregarse a Gemini todavía por sus propias dependencias** (T2.3 depende de T2.1, T2.6 de T2.4, T4.3/T4.4 de T4.1, T7.4 de T7.2 — todas bloqueadas hoy por hardware real, ver `JARVIS_PROGRESS.md`), no por falta de especificación. Cuando esas dependencias se desbloqueen (acceso al PC Windows del usuario), estas 5 tareas quedan listas para delegarse sin otra ronda de especificación.

Ya no queda ninguna tarea bloqueada exclusivamente por falta de especificación en este paquete.

Claude completa cada especificación en una ronda dedicada, más cerca de cuando su fase arranque, siguiendo exactamente la plantilla de la sección final de este documento. Hasta entonces, esta tabla es la fuente de verdad de "lo que no está listo" — ninguna otra sección de este paquete reemplaza este bloqueo.

## Plantilla para tareas futuras no listadas aquí

Cuando Claude delegue una tarea nueva a Gemini que no esté en este paquete, debe completar los mismos 6 campos obligatorios antes de entregarla (ver sección anterior): (1) objetivo en una frase, (2) archivos autorizados (lista cerrada), (3) interfaz exacta de entrada/salida (firma de función o endpoint con tipos), (4) dependencias, (5) criterios de aceptación verificables, (6) pruebas necesarias. Más qué hacer si algo no está claro durante la implementación (por defecto: detenerse y reportar, nunca decidir arquitectura por cuenta propia). Sin los 6 campos, la tarea no se entrega — se añade a la tabla de bloqueadas.
