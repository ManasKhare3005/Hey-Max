# Start Max automatically when you log in to Windows, in the background with a tray icon.
# It restarts itself if it ever crashes. Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1              # install + start now
#   powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 -Uninstall   # remove
param([switch]$Uninstall, [switch]$NoStart)

$ErrorActionPreference = "Stop"
$TaskName = "Max voice assistant"
$OverlayTask = "Max overlay"
$root = $PSScriptRoot

if ($Uninstall) {
    foreach ($name in @($TaskName, $OverlayTask)) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "Removed: '$name' no longer starts at login." -ForegroundColor Green
        }
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
# The desktop overlay: its own small process, so it can start Max when Max is off
$overlayAction = New-ScheduledTaskAction -Execute $pythonw -Argument "-m max_assistant.overlay" -WorkingDirectory $root
$overlayTrigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$overlayTrigger.Delay = "PT20S"
Register-ScheduledTask -TaskName $OverlayTask -Action $overlayAction -Trigger $overlayTrigger -Settings $settings `
    -Principal $principal -Description "Max desktop overlay: start/stop, push-to-talk, chats, memories, due today" -Force | Out-Null

Write-Host "Installed: Max and its desktop overlay start in the background each time you log in." -ForegroundColor Green
Write-Host "  Overlay: drag the pill anywhere. Mic = listen now, chat / memory / due-today panels, power = start/stop."
Write-Host "  Tray icon (near the clock): open dashboard, pause listening, sleep models, quit."
Write-Host "  Remove with: powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 -Uninstall"

if (-not $NoStart) {
    Start-ScheduledTask -TaskName $TaskName
    Start-ScheduledTask -TaskName $OverlayTask
    Write-Host "Started now." -ForegroundColor Green
}
