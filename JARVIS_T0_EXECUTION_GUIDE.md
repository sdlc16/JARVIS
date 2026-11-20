# Guía de ejecución T0.1 – T0.3 (para tu PC personal)

Sigue exactamente `DEVELOPMENT_TASKS.md` T0.1-T0.3. No se cambia ningún criterio de aceptación aquí — esta guía solo da los comandos exactos, el orden y cómo interpretar el resultado. Completa primero la lista de `JARVIS_PC_REQUIREMENTS.md` §8.

---

## T0.1 — Confirmar WSL2 instalado, distro Ubuntu, `nvidia-smi` visible dentro de WSL2

**Criterio de aceptación (sin cambios):** `nvidia-smi` dentro de WSL2 muestra la RTX 3060 con VRAM correcta.

**Desde dónde ejecutar:** PowerShell como administrador (pasos 1-2), luego dentro de la terminal Ubuntu de WSL2 (paso 3).

```powershell
wsl --status
wsl -l -v
```
**Resultado esperado:** `wsl --status` dice "Versión predeterminada: 2". `wsl -l -v` lista una distro Ubuntu con `VERSION 2`.

Abre la distro Ubuntu (menú inicio → "Ubuntu", o `wsl` desde PowerShell) y ejecuta:
```bash
nvidia-smi
```
**Resultado esperado:** una tabla con el nombre "NVIDIA GeForce RTX 3060" y su VRAM total (8192 MiB / 8 GB).

**Si falla:** revisa `JARVIS_PC_REQUIREMENTS.md` §7 ("nvidia-smi funciona en Windows pero no dentro de WSL2"). **No continúes a T0.2 si `nvidia-smi` no funciona dentro de WSL2** — todo lo posterior depende de esto.

---

## T0.2 — Instalar OpenJarvis dentro de WSL2 con el instalador oficial

**Criterio de aceptación (sin cambios):** `jarvis doctor` reporta todo verde; CUDA funciona dentro de WSL2; `ollama ps` con un modelo cargado reporta la distribución real de carga CPU/GPU (sin exigir 100% GPU como umbral rígido, pero sin aceptar 100% CPU sin explicación).

**Desde dónde ejecutar:** terminal Ubuntu de WSL2, directorio home del usuario (`cd ~`).

```bash
curl -fsSL https://open-jarvis.github.io/OpenJarvis/install.sh | bash
```
**Resultado esperado:** termina en ~3 minutos en primer plano (uv, venv, Ollama, modelo inicial `qwen3.5:2b`); deja compilando la extensión Rust en segundo plano.

Tras que termine, abre una terminal nueva (para recargar el `PATH`) y comprueba:
```bash
jarvis doctor
```
**Resultado esperado:** todas las comprobaciones en verde, incluida la extensión Rust (puede tardar varios minutos en background; si sigue en rojo, espera y vuelve a ejecutar `jarvis doctor`).

Verifica el uso real de GPU con un modelo cargado:
```bash
ollama ps
```
**Resultado esperado:** la columna de procesador muestra el reparto real (ideal: GPU alto; aceptable: GPU con una fracción en CPU si hay una razón documentada — ej. modelo que excede ligeramente la VRAM; **no aceptable:** 100% CPU sin explicación, que indica que el passthrough no funciona).

**Si `jarvis` no se encuentra:** `source ~/.bashrc` o abre una terminal nueva (el PATH se actualiza ahí).

**Si `ollama ps` muestra 100% CPU:** detente, no continúes a T0.3 todavía — vuelve a verificar T0.1 (`nvidia-smi` dentro de WSL2) antes de seguir. Ejecutar el benchmark con la GPU rota solo produciría datos inútiles.

---

## T0.3 — Benchmark de modelos candidatos con `jarvis bench`

**Criterio de aceptación (sin cambios):** tabla con VRAM real medida, tokens/s y latencia por modelo; el modelo por defecto se fija con estos datos, nunca con el catálogo.

**Desde dónde ejecutar:** terminal Ubuntu de WSL2, dentro del directorio del proyecto clonado (`cd ~/.openjarvis` si el instalador clonó ahí, o donde hayas clonado `sdlc16/JARVIS` si trabajas sobre el fork propio — confirma con `jarvis doctor` o `pwd` cuál es tu checkout activo antes de seguir).

Antes de correr el benchmark, descarga cada modelo candidato (si no está ya):
```bash
ollama pull qwen3:8b
ollama pull glm-4.7-flash
ollama pull trinity-mini
```
*(Si algún nombre de tag exacto no coincide en el registro de Ollama, usa `ollama list` y el nombre real que aparezca ahí para ese modelo — no inventes un tag distinto al disponible.)*

Para cada modelo, ejecuta el benchmark completo (latencia + throughput) y vuelca el resultado a un archivo:
```bash
jarvis bench run -m qwen3:8b -e ollama -n 20 --json -o bench_qwen3_8b.jsonl
jarvis bench run -m glm-4.7-flash -e ollama -n 20 --json -o bench_glm47flash.jsonl
jarvis bench run -m trinity-mini -e ollama -n 20 --json -o bench_trinity_mini.jsonl
```

**Mientras cada benchmark corre**, en otra terminal de WSL2 (no en la misma, para no afectar la medición), observa el uso real de VRAM:
```bash
watch -n 1 nvidia-smi
```
Anota manualmente, para cada modelo: VRAM máxima usada (columna "Memory-Usage"), y si en algún momento aparece carga de CPU compartida.

**Resultado esperado por modelo:** el `--json` de `jarvis bench run` imprime un resumen con tokens/s (throughput) y latencia (ms) — cópialo o guárdalo. Combinado con la VRAM anotada de `nvidia-smi`, esto es la "tabla con VRAM real medida, tokens/s y latencia" que pide el criterio de aceptación.

**Repite con al menos un contexto largo realista** (no solo el prompt corto por defecto del benchmark) para capturar el coste de la caché KV en contextos grandes, ya señalado como riesgo en la auditoría de VRAM de este proyecto:
```bash
jarvis bench run -m qwen3:8b -e ollama -n 10 --json -o bench_qwen3_8b_ctx_largo.jsonl
```
*(Si `jarvis bench` no soporta directamente un flag de longitud de contexto en tu versión instalada, documenta el contexto real usado en la prueba manualmente junto al resultado — no fuerces un comando que no existe.)*

**Con los 3 (o más) resultados en la mano:** el modelo por defecto se fija comparando tokens/s, latencia y VRAM real de los 3 — el que mejor equilibrio dé en tu RTX 3060 de 8GB, no el que "suene mejor" por su tamaño o popularidad. Documenta la elección y los números exactos en `JARVIS_PROGRESS.md` cuando vuelvas a esta sesión (o la que retome el trabajo).

**Cuándo detenerte y no continuar:**
- Si **ningún** modelo candidato cabe sin exceder la VRAM disponible de forma consistente (swapping a CPU constante, no solo una fracción pequeña) — detente y repórtalo, no fuerces un modelo que no funciona bien.
- Si `jarvis bench` falla con un error de conexión al engine — vuelve a T0.2, algo no quedó bien configurado.
- Si los tres benchmarks completan pero los números no tienen sentido (ej. 0 tokens/s) — no los uses como base de decisión; repite o reporta el problema antes de fijar ningún modelo por defecto.

---

## Después de T0.3

Con los resultados reales en la mano, la siguiente tarea desbloqueada es **T0.4** (confirmar que `config.toml` no define ningún engine cloud) y luego **T1.1** (verificar agentes end-to-end con el modelo ya elegido) — ver `DEVELOPMENT_TASKS.md` para sus criterios exactos. Si vuelves a trabajar con Claude Code en esta sesión o en una nueva, pégale los resultados de T0.3 (la tabla de VRAM/tokens-por-segundo/latencia) para que pueda continuar sin tener que adivinar nada.
