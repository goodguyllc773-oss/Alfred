@echo off
rem Start ClipForge on http://127.0.0.1:8765/
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-windows.ps1" %*
