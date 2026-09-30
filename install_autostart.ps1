# Start Max automatically when you log in to Windows, in the background with a tray icon.
# It restarts itself if it ever crashes. Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1              # install + start now
#   powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 -Uninstall   # remove
param([switch]$Uninstall, [switch]$NoStart)

$ErrorActionPreference = "Stop"
$TaskName = "Max voice assistant"
$root = $PSScriptRoot

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed: Max will no longer start at login." -ForegroundColor Green
    } else {
        Write-Host "Max wasn't set to start at login."
    }
    exit 0
}

$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "Run setup.ps1 first (no .venv found at $root)." }

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m max_assistant --tray" -WorkingDirectory $root
# A short delay after login gives Ollama (which also starts at login) time to come up
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$trigger.Delay = "PT30S"
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Max: local voice assistant (tray icon; dashboard at http://127.0.0.1:8765)" -Force | Out-Null
Write-Host "Installed: Max starts in the background each time you log in." -ForegroundColor Green
Write-Host "  Tray icon (near the clock): open dashboard, pause listening, sleep models, quit."
Write-Host "  Remove with: powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 -Uninstall"

if (-not $NoStart) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started now." -ForegroundColor Green
}
