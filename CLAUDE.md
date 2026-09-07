# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Alfred is a collection of small, self-contained web tools. Each tool lives in its own
top-level directory and is a single static HTML file with no build step, no
dependencies, and no server — inline `<style>` and `<script>`, opened directly in a
browser.

Current tools:
- `timebox-timer/index.html` — a Pomodoro-style focus timer.

## Running / developing

There is no build, lint, test, or package manager setup. To work on a tool, open its
`index.html` in a browser and reload after edits. For a local server (e.g. to test
across devices):

    python -m http.server 8000    # then visit http://localhost:8000/timebox-timer/

## Architecture notes

### timebox-timer

Single-file app; all logic is in one IIFE at the bottom of `index.html`.

- **Timekeeping is wall-clock based, not tick-counted.** On start, `endAt = Date.now()
  + remaining*1000`; a `requestAnimationFrame` loop recomputes `remaining` from
  `endAt - Date.now()` every frame. This keeps the timer accurate when the tab is
  backgrounded or throttled. `pause()` freezes `remaining`; `start()` re-derives
  `endAt` from it.
- **The dial** is two SVG circles. Progress is drawn by animating `stroke-dashoffset`
  of `#prog` between `0` and `CIRC` (the circumference, `2πr` with `r=100`), where the
  offset fraction is `remaining / durationSec`.
- **Daily tally** persists in `localStorage` under a per-day key
  (`timebox-YYYY-MM-DD`, UTC date). It increments once per completed session in
  `finish()` and renders one `.dot` per count. No cross-day cleanup — old keys just
  accumulate.
- **Chime** is synthesized with the WebAudio API (three oscillators, no audio asset).
- The `<title>` doubles as a live countdown display while running.
- Keyboard: Space = start/pause, R = reset.

## Conventions

- Palette and spacing are driven by CSS custom properties on `:root`, with a
  `prefers-color-scheme: dark` override block. Reuse these variables rather than
  hard-coding colors.
- Keep tools dependency-free and single-file unless there's a strong reason not to.
