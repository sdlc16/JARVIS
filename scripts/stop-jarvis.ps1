# stop-jarvis.ps1 -- cierra la interfaz de JARVIS (y opcionalmente el puente).
#   -Bridge : tambien detiene el puente nativo de Windows.
# No borra nada: ni memoria, ni historial, ni archivos.

param([switch]$Bridge)

$distro = 'Ubuntu-24.04'
$root = Split-Path -Parent $PSScriptRoot
$drive = $root.Substring(0, 1).ToLower()
$wslRoot = '/mnt/' + $drive + $root.Substring(2).Replace('\', '/')
wsl -d $distro -- bash -lc "cd '$wslRoot' && source `$HOME/.local/bin/env && PYTHONWARNINGS=ignore uv run --no-sync jarvis gui --stop" 2>&1 | Out-Null

if ($Bridge) {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -match '-m\s+jarvis_bridge' -and $_.CommandLine -notmatch '--print-token|--rotate-token|--init-config' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId; Write-Host "Puente detenido (pid $($_.ProcessId))." }
}
Write-Host 'JARVIS detenido.' -ForegroundColor Green
