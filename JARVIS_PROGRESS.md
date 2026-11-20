# JARVIS_PROGRESS.md

Memoria de trabajo entre sesiones. Léelo junto con `CLAUDE.md` al empezar cualquier sesión nueva, antes de tocar nada.

## Sesión de instalación en PC real (2026-11-19/20) — primera versión funcional de JARVIS

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

- **Commit/push:** sigue pendiente de que el usuario dé su nombre/email para Git (no se ha inventado ninguno, tal como se pidió). Nada perdido — mismos 2035+ archivos en stage.
- **T1.5** (puente nativo de Windows): no empezado — es la siguiente pieza grande no construida todavía.
- Actualización v0.1.1→v1.0.4 de OpenJarvis: **no aplicada deliberadamente** — un salto de versión mayor podría traer cambios incompatibles con nuestras personalizaciones de T1.4; requiere revisión antes de aceptarla.
- `jarvis memory search` con score siempre en 0.0000 — posible defecto menor, no investigado.
- Node.js no instalado (solo lo necesitan ClaudeCodeAgent y el puente de WhatsApp, fuera de alcance ahora).

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
