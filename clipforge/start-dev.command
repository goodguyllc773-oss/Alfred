#!/bin/bash
# macOS: double-click (or `open start-dev.command`) to start ClipForge's dev
# servers in a Terminal window — API + worker on 127.0.0.1:8765, UI on 127.0.0.1:5173.
# The Windows equivalent is start-dev.cmd.
cd "$(dirname "$0")" || exit 1
npm run dev
