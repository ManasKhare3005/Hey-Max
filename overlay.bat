@echo off
rem Desktop overlay for Max: start/stop, push-to-talk, chats, memories, due today.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m max_assistant.overlay
