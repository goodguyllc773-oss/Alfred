<#
  Start ClipForge (API + job worker + web UI) and open it in the browser.
  Everything stays on this machine: the server only listens on 127.0.0.1.
#>
param([int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
  Write-Host "ClipForge isn't set up yet. Run .\setup.cmd first." -ForegroundColor Yellow
  exit 1
}
$cfArgs = @("-m", "clipforge", "serve", "--port", $Port)
if (-not $NoBrowser) { $cfArgs += "--open" }
& $Py @cfArgs
