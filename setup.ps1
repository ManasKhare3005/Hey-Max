# Max one-time setup for Windows. Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
# Safe to re-run: every step skips work that's already done.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Set-Location $PSScriptRoot
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"   # harmless Windows warning from the model downloader

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "    $msg" }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "`n[X] $msg" -ForegroundColor Red; exit 1 }

function Check($what) {
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit code $LASTEXITCODE). Scroll up for the error." }
}

# Returns the path of a Python 3.10-3.12 interpreter, or $null
function Find-Python {
    $candidates = @()
    foreach ($v in @("3.11", "3.12", "3.10")) {
        try {
            $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p) { $candidates += $p.Trim() }
        } catch {}
    }
    foreach ($v in @("311", "312", "310")) {
        $candidates += "$env:LOCALAPPDATA\Programs\Python\Python$v\python.exe"
        $candidates += "$env:ProgramFiles\Python$v\python.exe"
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*WindowsApps*") { $candidates += $cmd.Source }

    foreach ($c in $candidates) {
        if (-not (Test-Path $c)) { continue }
        $ver = $null
        try { $ver = & $c -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null } catch {}
        if ($ver -in @("3.10", "3.11", "3.12")) { return $c }
    }
    return $null
}

# 1. Python ---------------------------------------------------------------
Step "Checking Python (3.10 - 3.12)"
$python = Find-Python
if (-not $python) {
    Warn "No compatible Python found. Downloading Python 3.11 from python.org..."
    $installer = Join-Path $env:TEMP "python-3.11.9-amd64.exe"
    Invoke-WebRequest "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -OutFile $installer
    Info "Installing (just for your user, no admin needed)..."
    Start-Process $installer -Wait -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1", "Include_test=0"
    $python = Find-Python
    if (-not $python) { Fail "Python install didn't work. Install Python 3.11 from python.org manually, then re-run this script." }
}
Info "Using $python"

# 2. Virtual environment + packages -----------------------------------------
Step "Creating virtual environment (.venv)"
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    & $python -m venv .venv; Check "Creating the virtual environment"
}
& $venvPy -m pip install --upgrade pip --quiet; Check "Upgrading pip"
Step "Installing Python packages (a few minutes the first time)"
& $venvPy -m pip install -r requirements.txt; Check "Installing packages"

# 3. Piper voice --------------------------------------------------------------
Step "Downloading the Piper voice"
$voiceDir = Join-Path $PSScriptRoot "models\piper"
New-Item -ItemType Directory -Force -Path $voiceDir | Out-Null
$base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium"
foreach ($f in @("en_US-ryan-medium.onnx", "en_US-ryan-medium.onnx.json")) {
    $dest = Join-Path $voiceDir $f
    if (-not (Test-Path $dest)) { Invoke-WebRequest -Uri "$base/$f" -OutFile $dest }
}
Info "Voice ready."

# 4. Wake word + speech-to-text models ------------------------------------------
Step "Pre-downloading wake word and speech models"
& $venvPy -c "import openwakeword.utils as u; u.download_models()"; Check "Downloading wake word models"
$kwsDir = Join-Path $PSScriptRoot "models\kws\gigaspeech-3.3M"
if (-not (Test-Path (Join-Path $kwsDir "tokens.txt"))) {
    $kwsName = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
    $kwsArchive = Join-Path $env:TEMP "$kwsName.tar.bz2"
    Invoke-WebRequest "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/$kwsName.tar.bz2" -OutFile $kwsArchive
    $kwsParent = Split-Path $kwsDir
    New-Item -ItemType Directory -Force -Path $kwsParent | Out-Null
    tar -xjf $kwsArchive -C $kwsParent; Check "Extracting the keyword spotting model"
    Move-Item (Join-Path $kwsParent $kwsName) $kwsDir
    Remove-Item $kwsArchive
}
# Voice ID: risky requests in other voices need a tap on your phone (speaker model, ~26 MB)
$vidFile = Join-Path $PSScriptRoot "models\voiceid\eres2net_en_voxceleb.onnx"
if (-not (Test-Path $vidFile)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $vidFile) | Out-Null
    Invoke-WebRequest "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx" -OutFile $vidFile
    Check "Downloading the voice ID model"
}
# Live captions while taking notes (streaming model, ~60 MB)
$liveName = "sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06"
$liveParent = Join-Path $PSScriptRoot "models\live"
if (-not (Test-Path (Join-Path $liveParent "$liveName\tokens.txt"))) {
    $liveArchive = Join-Path $env:TEMP "$liveName.tar.bz2"
    Invoke-WebRequest "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$liveName.tar.bz2" -OutFile $liveArchive
    New-Item -ItemType Directory -Force -Path $liveParent | Out-Null
    tar -xjf $liveArchive -C $liveParent; Check "Extracting the live caption model"
    Remove-Item $liveArchive
}
& $venvPy -c "from faster_whisper import WhisperModel; WhisperModel('small.en', device='cpu', compute_type='int8')"
Check "Downloading the Whisper model"
$moonName = "sherpa-onnx-moonshine-base-en-int8"
$moonDir = Join-Path $PSScriptRoot "models\stt\$moonName"
if (-not (Test-Path (Join-Path $moonDir "tokens.txt"))) {
    $moonArchive = Join-Path $env:TEMP "$moonName.tar.bz2"
    Invoke-WebRequest "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$moonName.tar.bz2" -OutFile $moonArchive
    New-Item -ItemType Directory -Force -Path (Split-Path $moonDir) | Out-Null
    tar -xjf $moonArchive -C (Split-Path $moonDir); Check "Extracting the fast speech model"
    Remove-Item $moonArchive
}

Step "Downloading the memory embedding model"
& $venvPy -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"; Check "Downloading the embedding model"

# 4a. Dashboard (React, needs Node.js to build) ------------------------------------
Step "Building the dashboard"
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location (Join-Path $PSScriptRoot "dashboard")
    npm install --no-fund --no-audit; Check "Installing dashboard packages"
    npm run build; Check "Building the dashboard"
    Pop-Location
    Info "Dashboard ready: it opens at http://127.0.0.1:8765 while Max is running."
} else {
    Warn "Node.js not found, so the dashboard wasn't built (Max works without it). Install Node.js, then run: cd dashboard; npm install; npm run build"
}

# 4b. Private web search (SearXNG in Docker) -------------------------------------
Step "Setting up private web search (SearXNG)"
$envFile = Join-Path $PSScriptRoot "searxng\.env"
if (-not (Test-Path $envFile)) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    "SEARXNG_SECRET=" + (($bytes | ForEach-Object { $_.ToString("x2") }) -join "") | Set-Content -Encoding ascii $envFile
}
if (Get-Command docker -ErrorAction SilentlyContinue) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) {
        docker compose up -d; Check "Starting SearXNG"
        Info "Search ready at http://127.0.0.1:8888"
    } else {
        Warn "Docker Desktop isn't running. Start it, then run: docker compose up -d  (Max uses DuckDuckGo until then)"
    }
} else {
    Warn "Docker not installed; Max will search with DuckDuckGo. Install Docker Desktop for private search."
}

# 5. Ollama ---------------------------------------------------------------------
Step "Checking Ollama"
function Find-Ollama {
    # Reload PATH in case Ollama was installed after this terminal was opened
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
                     "$env:ProgramFiles\Ollama\ollama.exe",
                     "${env:ProgramFiles(x86)}\Ollama\ollama.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}
$ollama = Find-Ollama
if (-not $ollama) {
    Warn "Ollama not found. Downloading the installer from ollama.com (~1 GB)..."
    $installer = Join-Path $env:TEMP "OllamaSetup.exe"
    Invoke-WebRequest "https://ollama.com/download/OllamaSetup.exe" -OutFile $installer
    Info "Installing Ollama..."
    Start-Process $installer -Wait -ArgumentList "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"
    $ollama = Find-Ollama
    if (-not $ollama) { Fail "Ollama install didn't work. Install it from https://ollama.com/download, then re-run this script." }
}
Info "Using $ollama"

# Smaller conversation cache so the fast model fits entirely on a 4 GB GPU
$ollamaEnv = @{ OLLAMA_FLASH_ATTENTION = "1"; OLLAMA_KV_CACHE_TYPE = "q8_0" }
$envChanged = $false
foreach ($k in $ollamaEnv.Keys) {
    if ([Environment]::GetEnvironmentVariable($k, "User") -ne $ollamaEnv[$k]) {
        [Environment]::SetEnvironmentVariable($k, $ollamaEnv[$k], "User"); $envChanged = $true
    }
    Set-Item "env:$k" $ollamaEnv[$k]
}
if ($envChanged) { Warn "Set Ollama GPU memory settings. If Ollama was already running, quit it from the tray and start it again." }

function Test-OllamaUp {
    try { Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}
if (-not (Test-OllamaUp)) {
    Info "Starting the Ollama server..."
    Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 20 -and -not (Test-OllamaUp); $i++) { Start-Sleep 1 }
    if (-not (Test-OllamaUp)) { Fail "Ollama didn't start. Open the Ollama app from the Start Menu and re-run this script." }
}

# 6. LLMs -----------------------------------------------------------------------
$fast = (Select-String -Path config.yaml -Pattern "^\s*fast_model:\s*(\S+)").Matches[0].Groups[1].Value
$planner = (Select-String -Path config.yaml -Pattern "^\s*planner_model:\s*(\S+)").Matches[0].Groups[1].Value
Step "Downloading LLMs: $fast and $planner (~8 GB, one time)"
& $ollama pull $fast; Check "Downloading $fast"
& $ollama pull $planner; Check "Downloading $planner"

New-Item -ItemType Directory -Force -Path data, logs | Out-Null

Write-Host "`nAll set!" -ForegroundColor Green
Write-Host "  Text mode (try this first):  .\run.bat --text"
Write-Host "  Voice mode:                  .\run.bat"