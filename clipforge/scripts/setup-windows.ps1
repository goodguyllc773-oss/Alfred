<#
  ClipForge one-time setup for Windows 10/11.

  Run from the clipforge folder:   .\setup.cmd      (or: powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1)

  What it does (and nothing else):
    1. checks Python 3.10+ (installs nothing system-wide without asking)
    2. checks FFmpeg; offers `winget install Gyan.FFmpeg` if missing
    3. creates backend\.venv and installs ClipForge's Python packages into it
    4. if an NVIDIA GPU is present, offers the CUDA libraries faster-whisper needs (pip, into the venv)
    5. builds the web UI if Node.js is available
    6. installs the Claude SessionStart sync hook (scripts/install-sync-hook.mjs)
    7. runs `clipforge doctor`
#>
param([switch]$Gpu, [switch]$NoGpu, [switch]$Yes)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Venv = Join-Path $Backend ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ask($q) { if ($Yes) { return $true }; return (Read-Host "$q [y/N]") -match '^(y|yes)$' }
function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

Step "Checking Python"
$pyCmd = $null
foreach ($c in @("py -3.12", "py -3.11", "py -3.13", "py -3", "python")) {
  try {
    $v = & cmd /c "$c -c ""import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))""" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v -and [version]$v -ge [version]"3.10") { $pyCmd = $c; break }
  } catch { }
}
if (-not $pyCmd) {
  Write-Host "Python 3.10+ not found. Install it with:  winget install Python.Python.3.12" -ForegroundColor Yellow
  Write-Host "(or from https://www.python.org/downloads/ - tick 'Add python.exe to PATH'), then re-run this script."
  exit 1
}
Write-Host "Using: $pyCmd ($v)"

Step "Checking FFmpeg"
if (-not ((Have "ffmpeg") -and (Have "ffprobe"))) {
  if ((Have "winget") -and (Ask "FFmpeg isn't on PATH. Install it now with winget (Gyan.FFmpeg)?")) {
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    Write-Host "FFmpeg installed. Close and reopen the terminal so PATH updates, then re-run setup." -ForegroundColor Yellow
    exit 0
  }
  Write-Host "FFmpeg is required. Install it (winget install Gyan.FFmpeg) or set CLIPFORGE_FFMPEG / CLIPFORGE_FFPROBE to the .exe paths." -ForegroundColor Yellow
} else {
  (& ffmpeg -hide_banner -version | Select-Object -First 1)
}

Step "Creating virtual environment in backend\.venv"
if (-not (Test-Path $Py)) { & cmd /c "$pyCmd -m venv ""$Venv""" }
& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install -e "${Backend}[dev]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

$hasNvidia = Have "nvidia-smi"
if ($hasNvidia -and -not $NoGpu) {
  Step "NVIDIA GPU detected"
  & nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
  if ($Gpu -or (Ask "Install CUDA libraries for GPU transcription (cuBLAS + cuDNN 9, ~1 GB, into the venv only)?")) {
    & $Py -m pip install "nvidia-cublas-cu12" "nvidia-cudnn-cu12==9.*"
  } else {
    Write-Host "Skipped. Transcription will run on the CPU (slower). Re-run with -Gpu later."
  }
}

Step "Building the web UI"
if (Have "npm") {
  Push-Location $Frontend
  try { npm ci --no-audit --no-fund; npm run build } finally { Pop-Location }
} else {
  Write-Host "Node.js not found - skipping UI build. Install it (winget install OpenJS.NodeJS.LTS) and re-run, " -ForegroundColor Yellow
  Write-Host "or run the UI in dev mode later. The API still works without it."
}

Step "Claude session sync hook"
if (Have "node") { node (Join-Path $PSScriptRoot "install-sync-hook.mjs") }
else { Write-Host "Node.js not found - skipped the Claude sync hook (re-run setup after installing Node)." -ForegroundColor Yellow }

Step "Diagnostics"
& $Py -m clipforge doctor
Write-Host "`nDone. Start ClipForge with:  .\start.cmd   (dev servers: .\start-dev.cmd)" -ForegroundColor Green
