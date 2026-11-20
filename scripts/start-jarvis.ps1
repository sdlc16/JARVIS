# start-jarvis.ps1 -- arranca JARVIS completo desde Windows con un clic.
#
#   1. Comprueba WSL2 (Ubuntu-24.04) y que Ollama responde.
#   2. Arranca el puente nativo de Windows (oculto) si no esta en marcha.
#   3. Arranca la interfaz grafica (jarvis gui) dentro de WSL2 y abre su ventana.
#
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\start-jarvis.ps1 [-NoWindow]
#   -NoWindow : no abre la ventana (util para pruebas automaticas).
# Registro: %LOCALAPPDATA%\JARVIS\logs\launcher.log
# No mantiene el PC despierto ni cambia ajustes de Windows.

param([switch]$NoWindow)

$ErrorActionPreference = 'Stop'
$distro = 'Ubuntu-24.04'
$root = Split-Path -Parent $PSScriptRoot
$bridgeDir = Join-Path $root 'windows_bridge'
$logDir = Join-Path $env:LOCALAPPDATA 'JARVIS\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir 'launcher.log'

function Log([string]$msg, [string]$color = 'Gray') {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $logFile -Value $line -Encoding utf8
    Write-Host $msg -ForegroundColor $color
}

function Fail([string]$msg) {
    Log "ERROR: $msg" 'Red'
    exit 1
}

# 1) WSL2 -------------------------------------------------------------------
wsl -d $distro -- true 2>$null
if ($LASTEXITCODE -ne 0) { Fail "no se encontro la distro WSL2 '$distro' (wsl --install -d $distro)." }
Log 'WSL2 disponible.' 'Cyan'

# Ollama arranca con systemd al iniciar WSL; esperamos a que responda.
$ollamaUp = $false
for ($i = 0; $i -lt 40 -and -not $ollamaUp; $i++) {
    try {
        $null = Invoke-WebRequest -Uri 'http://127.0.0.1:11434/api/version' -UseBasicParsing -TimeoutSec 2
        $ollamaUp = $true
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ollamaUp) { Fail 'Ollama no responde en 127.0.0.1:11434 (dentro de WSL2: systemctl status ollama).' }
Log 'Ollama activo.' 'Green'

# 2) Puente nativo de Windows -------------------------------------------------
function Get-BridgeProcess {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -match '-m\s+jarvis_bridge' -and $_.CommandLine -notmatch '--print-token|--rotate-token|--init-config' }
}
if (-not (Get-BridgeProcess)) {
    $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $pyw) { $pyw = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
    if (-not $pyw) { Fail 'no se encontro Python en Windows para el puente.' }
    Start-Process -FilePath $pyw -ArgumentList '-m', 'jarvis_bridge' -WorkingDirectory $bridgeDir -WindowStyle Hidden
    for ($i = 0; $i -lt 20 -and -not (Get-BridgeProcess); $i++) { Start-Sleep -Milliseconds 500 }
    if (-not (Get-BridgeProcess)) { Fail 'el puente de Windows no arranco (revisa %LOCALAPPDATA%\JARVIS\logs\bridge.jsonl).' }
    Log 'Puente de Windows iniciado (oculto).' 'Green'
} else {
    Log 'Puente de Windows ya en marcha.' 'Green'
}

# 3) Interfaz grafica ----------------------------------------------------------
$drive = $root.Substring(0, 1).ToLower()
$wslRoot = '/mnt/' + $drive + $root.Substring(2).Replace('\', '/')
$openFlag = if ($NoWindow) { '' } else { '--open' }
# Cierra antes (de forma sincrona) cualquier interfaz anterior: asi la espera de abajo
# no se confunde con una instancia vieja que aun responde.
$prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
wsl -d $distro -- bash -lc "cd '$wslRoot' && source `$HOME/.local/bin/env && PYTHONWARNINGS=ignore uv run --no-sync jarvis gui --stop" 2>&1 | Out-Null
$ErrorActionPreference = $prev
$cmd = "cd '$wslRoot' && source `$HOME/.local/bin/env && export OPENJARVIS_NO_UPDATE_CHECK=1 && exec uv run --no-sync jarvis gui --replace $openFlag"
$guiLog = Join-Path $logDir 'gui.log'
Start-Process -FilePath 'wsl.exe' -ArgumentList '-d', $distro, '--', 'bash', '-lc', "`"$cmd > /dev/null 2>&1`"" -WindowStyle Hidden
Log 'Iniciando la interfaz de JARVIS (carga modelos; unos segundos)...' 'Cyan'

$ready = $false
for ($i = 0; $i -lt 90 -and -not $ready; $i++) {
    foreach ($port in 8765..8784) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 1
            if ($r.Content -match '<title>JARVIS</title>') { $ready = $true; $readyPort = $port; break }
        } catch { }
    }
    if (-not $ready) { Start-Sleep -Seconds 1 }
}
if (-not $ready) { Fail 'la interfaz no respondio a tiempo (ejecuta "wsl -d Ubuntu-24.04 -- uv run jarvis gui" para ver el error).' }
Log "JARVIS listo en http://127.0.0.1:$readyPort/ ." 'Green'
if (-not $NoWindow) { Log 'La ventana se abre sola (si no, ejecuta de nuevo este script).' 'Gray' }
exit 0
