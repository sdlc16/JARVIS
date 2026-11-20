# CLAUDE_GEMINI_WORKFLOW.md

Reglas de colaboración entre **Claude Code** (arquitecto principal) y **Gemini/Antigravity** (desarrollador auxiliar) sobre el mismo repositorio `sdlc16/JARVIS`.

## 1. Roles (resumen — detalle en `JARVIS_MASTER_PLAN.md`)

- **Claude Code:** arquitectura, decisiones técnicas, diseño del puente de Windows, integración con OpenJarvis, resolución de errores complejos, revisión de seguridad, revisión final de todo módulo antes de integrarse.
- **Gemini/Antigravity:** implementación de módulos acotados y ya especificados por Claude, interfaces, pruebas, documentación, corrección de errores delimitados. Gemini **nunca** diseña la arquitectura ni decide qué se integra sin revisión.

## 2. Ramas

- **No hay un nombre de rama fijo en este documento.** La rama de trabajo de Claude Code cambia entre sesiones (cada sesión nueva puede traer una rama distinta). **Antes de crear cualquier rama auxiliar** (propia o de Gemini), se verifica con `git branch --show-current` y `git status` cuál es la rama base vigente en ese momento — nunca se asume el nombre de una sesión anterior. Cualquier rama de Gemini parte de la rama base vigente verificada en ese momento y vuelve a ella vía revisión de Claude; nunca se fusiona directamente sin pasar por Claude.
- Convención de nombres: `gemini/<area>-<tarea-corta>` (ej. `gemini/ocr-tool`, `gemini/browser-youtube-helpers`). Claude usa `claude/<area>-<tarea-corta>` para trabajo propio fuera de la rama principal cuando el cambio es grande.
- Una rama = una tarea del `DEVELOPMENT_TASKS.md` (identificada por su ID, ej. `T5.2`). No mezclar varias tareas en una misma rama.

## 3. Propiedad de archivos — evitar colisiones

Mientras ambos trabajen en paralelo, cada tarea del `DEVELOPMENT_TASKS.md` declara qué archivos/directorios toca **antes** de empezar (ver `GEMINI_TASK_PACK.md`, que fija esto explícitamente por tarea). Regla dura: **dos tareas activas nunca declaran el mismo archivo.** Si una tarea de Gemini necesita tocar un archivo que Claude está usando activamente (p. ej. `core/registry.py`, el diseño del puente, cualquier esquema de memoria), esa tarea espera a que Claude termine y la libere explícitamente.

Zonas reservadas a Claude (Gemini no las toca salvo instrucción explícita puntual):

- `src/openjarvis/core/*` (registry, config, events, types)
- Diseño de la API del puente de Windows (el contrato, no necesariamente cada handler)
- Esquema de memoria (tablas, migraciones) — Gemini puede implementar funciones *sobre* un esquema ya definido por Claude, no definirlo
- Cualquier archivo de seguridad (`src/openjarvis/security/*`)

Zonas típicas para Gemini (bajo especificación de Claude):

- Implementaciones concretas de handlers del puente ya diseñados (Fase 3 del `DEVELOPMENT_TASKS.md`)
- Helpers de Brave/YouTube/podcasts sobre `tools/browser.py` ya adaptado
- Tool de OCR, tool de monitor de hardware (lectura de datos, no diseño de la API)
- Tests (`tests/` mirror de `src/`)
- Documentación de usuario

## 4. Flujo de una tarea

1. Claude escribe o valida la especificación de la tarea en `GEMINI_TASK_PACK.md` (interfaz, archivos, criterios de aceptación) — autosuficiente, sin depender de esta conversación.
2. Gemini implementa en su rama, siguiendo exactamente esa especificación.
3. Gemini añade tests que cubran el nuevo código (mínimo: camino feliz + un caso de error). Sin tests, la tarea no se considera lista para revisión.
4. Claude revisa el diff completo antes de integrar: correctitud, cumplimiento del patrón de registro (`@ToolRegistry.register(...)` etc. — ver `REVIEW.md` del repo para el checklist heredado de OpenJarvis), seguridad, y que no haya tocado zonas reservadas.
5. Solo Claude integra (merge) a la rama de trabajo principal, tras revisión. Gemini no hace merge de su propio trabajo.
6. Tras integrar, se actualiza el estado de la tarea en `DEVELOPMENT_TASKS.md` (no se borra la tabla, se marca).

## 5. Reglas duras (no negociables)

- Nunca se modifican simultáneamente los mismos archivos desde dos ramas activas.
- Cada tarea entregada por Gemini debe incluir pruebas; sin pruebas no se integra.
- Ningún código se integra sin revisión de Claude.
- Nadie sobrescribe cambios ajenos: si hay conflicto, se resuelve por conversación entre ambos antes de forzar nada (`git push --force` queda prohibido salvo autorización explícita del usuario).
- Documentación (`JARVIS_MASTER_PLAN.md`, `DEVELOPMENT_TASKS.md`, este archivo, `CLAUDE.md`) se mantiene al día cuando una tarea cambia de alcance — no se deja que la documentación quede desincronizada del código.
- Ninguna de las dos IAs activa facturación de ningún proveedor, instala extras de Pearl Mining, ni ejecuta `jarvis mine*`/`jarvis pearl*`. Ver `CLAUDE.md` §Reglas permanentes.
- Ninguna de las dos IAs usa mecanismos de autenticación no oficiales ni el OAuth de Gemini CLI para alimentar herramientas de terceros (restricción explícita del usuario).

## 6. Mensajes de commit

Formato: `[T<id>] <resumen corto en imperativo>` — ej. `[T5.2] Add OCR tool wrapping screen capture`. Permite rastrear cada commit a una tarea concreta del `DEVELOPMENT_TASKS.md`.

## 7. Qué hacer si Gemini se bloquea

Si una tarea de `GEMINI_TASK_PACK.md` resulta ambigua o requiere una decisión de arquitectura no cubierta en la especificación, Gemini **no improvisa la decisión**: se detiene, documenta el bloqueo y lo deja para que Claude lo resuelva en la siguiente ronda de especificación. Esto es más lento pero evita retrabajo e inconsistencias de arquitectura.
