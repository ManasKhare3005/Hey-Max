@echo off
rem Open Max in its own window (starts Max in the background if it isn't running).
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m max_assistant.app
