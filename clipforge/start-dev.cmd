@echo off
rem Start ClipForge's dev servers in their own window:
rem   API + job worker on http://127.0.0.1:8765, UI (hot reload) on http://127.0.0.1:5173
rem Claude launches this with explorer.exe so it runs in your session, not Claude's sandbox
rem (the sandbox has a private copy of AppData, where ClipForge keeps its data and models).
title ClipForge - dev servers
cd /d "%~dp0"
npm run dev
pause
