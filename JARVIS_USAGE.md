# JARVIS — manual de uso

Asistente personal local para este PC (Windows 11 + WSL2). Todo corre en tu máquina: **Ollama es el único motor de inferencia**, sin nube, sin APIs de pago, sin fallback externo.

## Arrancar y parar

| Qué | Cómo |
|---|---|
| **Arrancar todo** | Doble clic en el acceso **JARVIS** del Escritorio (o `powershell -File scripts\start-jarvis.ps1`). Tarda ~45–70 s la primera vez (carga el modelo en la GPU). Abre la ventana de la aplicación. |
| Arrancar sin abrir ventana | `scripts\start-jarvis.ps1 -NoWindow` |
| **Parar** | `scripts\stop-jarvis.ps1` (añade `-Bridge` para parar también el puente de Windows) |
| Acceso al iniciar sesión (opcional) | `scripts\install-shortcuts.ps1 -Autostart` · quitarlo: `-Remove` |
| Solo terminal | `wsl -d Ubuntu-24.04` → `cd /mnt/c/Users/PC/Desktop/JARVIS` → `uv run --no-sync jarvis gui` |

El script arranca, en este orden: comprobación de WSL2 y Ollama → puente nativo de Windows (oculto) → interfaz (`jarvis gui`). No mantiene el PC despierto: Windows puede suspenderse con normalidad.

> Usa siempre `uv run --no-sync` (o los scripts): `uv sync` borraría la extensión Rust de la memoria.

## La ventana

- **Chat**: escribe una petición. Verás las herramientas que usa (`✓ windows_windows (0.04s)`). Las acciones sensibles abren un aviso amarillo **«JARVIS pide permiso»**: *Permitir esta vez* / *Denegar*. Si no contestas en 30 s se deniega.
- **Voz**: *Iniciar voz* abre el micrófono (solo mientras esté iniciada; no se guarda audio). Modos: siempre escuchando, «Hey Jarvis» (modelo) o «Jarvis» (reconocimiento de voz). Los permisos también se pueden dar **hablando** («sí» / «no»).
- **Permisos**: política por herramienta (preguntar / permitir / denegar). El riesgo alto (`windows_files_modify`) siempre pregunta. Decisiones recientes.
- **Memoria**: «Recordar algo», indexar carpetas (solo Documentos, Escritorio y Descargas), buscar, olvidar, quitar documentos de la base (**nunca borra tus archivos**).
- **Historial**, **Registros** (voz, permisos, puente; sin texto privado ni tokens), **Ajustes** (voz) y **Estado** (Ollama, VRAM, RAM, puente).

Memoria por terminal: `jarvis docs index|search|list|remove|remember|facts|forget|clear`.

## Qué puede hacer JARVIS en Windows

Abrir apps de una lista permitida, listar/enfocar/minimizar/cerrar ventanas, escribir texto y pulsar atajos **en una ventana concreta** (con verificación de lo escrito), leer y operar controles por nombre (UI Automation), listar/buscar archivos en Escritorio/Documentos/Descargas, y escribir/mover/**enviar a la papelera** solo dentro de `Documentos\JARVIS`. Nunca borra de forma definitiva ni toca el resto del disco.

## Seguridad en una frase

Todo local: el puente solo escucha en la interfaz de WSL con un token cifrado con DPAPI; la interfaz solo en `127.0.0.1` con un token aleatorio por ejecución; las acciones sensibles piden tu permiso; los registros no guardan texto privado, audio ni credenciales.

## Si algo falla

| Síntoma | Qué hacer |
|---|---|
| «Ollama caído» | `wsl -d Ubuntu-24.04 -- systemctl status ollama`; el script espera hasta 40 s |
| «Sin puente» (Windows) | volver a ejecutar `start-jarvis.ps1`; revisa `%LOCALAPPDATA%\JARVIS\logs\bridge.jsonl` |
| La voz no oye / `Connection refused` de audio | WSLg se cuelga a veces: `wsl --shutdown` y volver a arrancar |
| Respuestas lentas la primera vez | el modelo se carga en VRAM (~5.8 GB); después queda residente 30 min |
| Validar el sistema | `wsl -d Ubuntu-24.04 -- bash -lc "cd /mnt/c/Users/PC/Desktop/JARVIS && uv run --no-sync jarvis test --suite full"` |

## Lo único que necesita tu presencia (una sola sesión, ~10 min)

Con el micro y los altavoces reales (nada de esto se puede comprobar sin ti):

1. **Clic de aprobación**: en *Chat* pide «Abre el bloc de notas y escribe hola». Aparece el aviso amarillo → *Permitir esta vez* → comprueba que el Bloc de notas contiene «hola». Repite denegando: no debe escribir nada.
2. **Voz básica**: *Voz → Iniciar voz* (modo «siempre escuchando»). Di una frase larga con pausas («Oye Jarvis, dime qué ventanas tengo abiertas… y cuánta memoria usa el modelo») y luego una corta. Debe transcribir entera y contestar hablando.
3. **Permiso hablado**: di «escribe hola mundo en la ventana activa»; responde «sí» al aviso hablado; debe escribir. Repite diciendo «no».
4. **Wake-word**: *Ajustes → wake_mode = model*, reinicia la voz y di «Hey Jarvis, ¿qué hora es?». Si no te detecta (el modelo es inglés), prueba `stt`. Anota cuántas veces de 10 te activa.
5. **Barge-in**: *Ajustes → barge_in = sí*, reinicia la voz, pídele algo largo y interrúmpele hablando encima. Anota si corta y si se activa solo con su propia voz.
6. Pega el contenido de *Registros → voice* y dime qué falló.
