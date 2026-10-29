#!/bin/bash
# ClipForge one-time setup for macOS (also works on Linux).
# Needs: Python 3.10+, FFmpeg, Node.js 20+  (macOS: brew install python@3.12 ffmpeg node)
# Safe to re-run: reuses backend/.venv, re-installs packages, rebuilds the UI.
set -euo pipefail
cd "$(dirname "$0")/.."
say() { printf '\n==> %s\n' "$1"; }

say "Checking prerequisites"
PY=""
for c in python3.12 python3.13 python3.11 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "Python 3.10+ not found (macOS: brew install python@3.12)"; exit 1; }
command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || { echo "FFmpeg not found (macOS: brew install ffmpeg)"; exit 1; }
command -v npm >/dev/null || { echo "Node.js not found (macOS: brew install node)"; exit 1; }
echo "python: $($PY --version)  ffmpeg: $(ffmpeg -hide_banner -version | head -1)  node: $(node --version)"

say "Python packages (backend/.venv)"
[ -x backend/.venv/bin/python ] || "$PY" -m venv backend/.venv
backend/.venv/bin/python -m pip install --upgrade pip --quiet
backend/.venv/bin/python -m pip install -e "backend[dev]" --quiet

say "Web UI (frontend/)"
(cd frontend && npm ci --no-audit --no-fund && npm run build)

say "Claude session sync hook"
node scripts/install-sync-hook.mjs

say "Diagnostics"
(cd backend && .venv/bin/python -m clipforge doctor) || true
printf '\nDone. Dev servers: open start-dev.command   ·   App only: backend/.venv/bin/python -m clipforge serve --open\n'
