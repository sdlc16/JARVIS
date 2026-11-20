# ruff: noqa: E501  (script PowerShell embebido: no se puede partir)
"""UI Automation de Windows (via PowerShell 5.1, sin dependencias extra).

Permite inspeccionar y accionar controles por NOMBRE (botones de dialogos,
menus, campos) y leer el texto de una ventana -- mucho mas fiable que clics
por coordenadas. Los parametros viajan en una variable de entorno JSON (jamas
interpolados en el script) para evitar inyeccion.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from jarvis_bridge.security import BridgeError

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$p = $env:JARVIS_UIA | ConvertFrom-Json
$AE = [System.Windows.Automation.AutomationElement]
$root = $AE::FromHandle([IntPtr][int64]$p.hwnd)
function Out-Json($o) { [Console]::Out.Write((ConvertTo-Json -InputObject $o -Compress -Depth 5)) }

if ($p.op -eq 'elements') {
  $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                       [System.Windows.Automation.Condition]::TrueCondition)
  $types = @($p.types)
  $out = New-Object System.Collections.ArrayList
  foreach ($e in $all) {
    $c = $e.Current
    if (-not $c.Name -or $c.IsOffscreen -or -not $c.IsEnabled) { continue }
    $t = $c.ControlType.ProgrammaticName -replace '^ControlType\.', ''
    if ($types.Count -gt 0 -and ($types -notcontains $t)) { continue }
    [void]$out.Add([pscustomobject]@{ name = $c.Name; type = $t; id = $c.AutomationId })
    if ($out.Count -ge [int]$p.max) { break }
  }
  Out-Json @($out)
}
elseif ($p.op -eq 'invoke') {
  $cond = New-Object System.Windows.Automation.PropertyCondition($AE::NameProperty, [string]$p.name,
          [System.Windows.Automation.PropertyConditionFlags]::IgnoreCase)
  $hits = @($root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond) |
            Where-Object { -not $_.Current.IsOffscreen -and $_.Current.IsEnabled })
  if ($p.type) { $hits = @($hits | Where-Object { ($_.Current.ControlType.ProgrammaticName -replace '^ControlType\.', '') -eq $p.type }) }
  if ($hits.Count -eq 0) { Out-Json @{ error = 'not_found' }; exit 0 }
  if ($hits.Count -gt 1) { Out-Json @{ error = 'ambiguous'; count = $hits.Count }; exit 0 }
  $e = $hits[0]; $used = $null
  foreach ($pat in @(
    @('Invoke', [System.Windows.Automation.InvokePattern]::Pattern),
    @('Toggle', [System.Windows.Automation.TogglePattern]::Pattern),
    @('SelectionItem', [System.Windows.Automation.SelectionItemPattern]::Pattern),
    @('ExpandCollapse', [System.Windows.Automation.ExpandCollapsePattern]::Pattern))) {
    $obj = $null
    if ($e.TryGetCurrentPattern($pat[1], [ref]$obj)) {
      switch ($pat[0]) {
        'Invoke' { $obj.Invoke() } 'Toggle' { $obj.Toggle() }
        'SelectionItem' { $obj.Select() } 'ExpandCollapse' { $obj.Expand() }
      }
      $used = $pat[0]; break
    }
  }
  if (-not $used) { Out-Json @{ error = 'no_pattern' }; exit 0 }
  Out-Json @{ invoked = $true; name = $e.Current.Name; pattern = $used }
}
elseif ($p.op -eq 'read') {
  $cond = New-Object System.Windows.Automation.PropertyCondition($AE::IsTextPatternAvailableProperty, $true)
  $el = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
  $text = $null
  if ($el) {
    $tp = $el.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
    $text = $tp.DocumentRange.GetText([int]$p.max)
  } else {
    $cond2 = New-Object System.Windows.Automation.PropertyCondition($AE::IsValuePatternAvailableProperty, $true)
    $el2 = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond2)
    if ($el2) { $text = $el2.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value }
  }
  if ($null -eq $text) { Out-Json @{ error = 'no_text' } } else { Out-Json @{ text = $text } }
}
else { Out-Json @{ error = 'bad_op' } }
"""


def run(op: str, hwnd: int, timeout: float = 25.0, **params) -> dict:
    if sys.platform != "win32":
        raise BridgeError("unsupported", "UI Automation solo existe en Windows.", 501)
    env = {
        **os.environ,
        "JARVIS_UIA": json.dumps({"op": op, "hwnd": int(hwnd), **params}),
    }
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
            env=env,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(
            "uia_timeout", f"UI Automation tardo mas de {timeout:.0f}s.", 504
        ) from exc
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0 or not out:
        err = proc.stderr.decode("utf-8", errors="replace").strip()[:200]
        raise BridgeError(
            "uia_failed", f"UI Automation fallo: {err or 'sin salida'}", 500
        )
    try:
        return {"result": json.loads(out)}
    except ValueError as exc:
        raise BridgeError(
            "uia_failed", f"Salida no valida de UIA: {out[:100]}", 500
        ) from exc
