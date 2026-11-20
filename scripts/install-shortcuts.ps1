# install-shortcuts.ps1 -- crea el acceso directo "JARVIS" en el Escritorio.
#   -Autostart : ADEMAS arranca JARVIS al iniciar sesion (opt-in; carpeta Inicio del usuario).
#   -Remove    : elimina el acceso del Escritorio y el de Inicio (solo los creados por este script).
# Sin permisos de administrador. No mantiene el PC despierto.

param([switch]$Autostart, [switch]$Remove)

$root = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $root 'scripts\start-jarvis.ps1'
$desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'JARVIS.lnk'
$startupLnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'JARVIS.lnk'

if ($Remove) {
    foreach ($l in @($desktopLnk, $startupLnk)) {
        if (Test-Path $l) { Remove-Item $l -Force; Write-Host "Eliminado: $l" }
    }
    exit 0
}

function New-JarvisShortcut([string]$path) {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($path)
    $s.TargetPath = (Get-Command powershell.exe).Source
    $s.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`""
    $s.WorkingDirectory = $root
    $s.Description = 'Arranca JARVIS (puente de Windows + interfaz)'
    $s.IconLocation = (Get-Command msedge.exe -ErrorAction SilentlyContinue).Source + ',0'
    $s.Save()
    Write-Host "Creado: $path" -ForegroundColor Green
}

New-JarvisShortcut $desktopLnk
if ($Autostart) { New-JarvisShortcut $startupLnk }
