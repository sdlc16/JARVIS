# start-jarvis.ps1 — arranca JARVIS desde Windows.
#
# Qué hace:
#   1. Comprueba que la distro WSL2 "Ubuntu-24.04" está disponible.
#   2. Comprueba (y arranca si hace falta) el servicio systemd de Ollama.
#   3. Entra en una sesion de chat interactiva de jarvis (uv run jarvis chat)
#      dentro de WSL2, sobre esta misma carpeta del proyecto.
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File scripts\start-jarvis.ps1
#   (o clic derecho -> "Ejecutar con PowerShell")

$distro = "Ubuntu-24.04"

# No se usa "wsl -l -q" para detectar la distro: wsl.exe emite UTF-16 cuando
# su salida esta redirigida (no es una consola real), lo que corrompe la
# comparacion de texto en algunos contextos. Probar a ejecutar algo en la
# distro directamente es mas fiable.
wsl -d $distro -- true 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: no se encontro la distro WSL2 '$distro'. Ejecuta 'wsl --install -d Ubuntu-24.04' primero." -ForegroundColor Red
    exit 1
}

Write-Host "Comprobando Ollama dentro de WSL2..." -ForegroundColor Cyan
wsl -d $distro -- bash -c "sudo systemctl is-active --quiet ollama || sudo systemctl start ollama"
wsl -d $distro -- bash -c "for i in 1 2 3 4 5 6 7 8 9 10; do curl -s http://127.0.0.1:11434/api/version > /dev/null && exit 0; sleep 1; done; echo 'Ollama no respondio a tiempo' >&2; exit 1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Ollama no arranco a tiempo dentro de WSL2." -ForegroundColor Red
    exit 1
}
Write-Host "Ollama activo. Iniciando JARVIS..." -ForegroundColor Green

wsl -d $distro -- bash -c "cd /mnt/c/Users/PC/Desktop/JARVIS && source `$HOME/.local/bin/env && uv run jarvis chat"
