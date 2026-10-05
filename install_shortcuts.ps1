# Desktop and Start Menu shortcuts that open Max in its own window (no browser, no console).
# Opening it starts Max in the background if it isn't running.
#   powershell -ExecutionPolicy Bypass -File .\install_shortcuts.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$icon = Join-Path $root "max_assistant\app\max.ico"
if (-not (Test-Path $pythonw)) { throw "Run setup.ps1 first (no .venv found)." }
if (-not (Test-Path $icon)) { & $pythonw -c "from max_assistant.app import make_icon; make_icon()" }

$shell = New-Object -ComObject WScript.Shell
$places = @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("Programs")) ""))
foreach ($dir in $places) {
    $link = $shell.CreateShortcut((Join-Path $dir "Max.lnk"))
    $link.TargetPath = $pythonw
    $link.Arguments = "-m max_assistant.app"
    $link.WorkingDirectory = $root
    $link.IconLocation = "$icon,0"
    $link.Description = "Max, your local voice assistant"
    $link.Save()
    Write-Host "Shortcut: $(Join-Path $dir 'Max.lnk')"
}
Write-Host "Done. Open Max from the Desktop or Start Menu (right-click it there to pin it to the taskbar)."
