@echo off
rem Start Max in its own minimized window, independent of VS Code or any terminal.
rem Double-click this file, or pin a shortcut to it on the taskbar / in shell:startup.
cd /d "%~dp0"
start "Max" /min cmd /k run.bat %*
