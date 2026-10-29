@echo off
rem One-time ClipForge setup (Windows). Passes arguments through, e.g. setup.cmd -Gpu
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-windows.ps1" %*
