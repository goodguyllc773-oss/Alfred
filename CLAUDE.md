# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Alfred is a collection of small, self-contained web tools. Each tool lives in its own
top-level directory and is a single static HTML file with no build step, no
dependencies, and no server — inline `<style>` and `<script>`, opened directly in a
browser.

Current tools:
- `timebox-timer/index.html` — a Pomodoro-style focus timer.
- `alfred/index.html` — a personal "chief of staff" desk: a chat with Alfred plus
  employee tabs for email (Miles), orders (Otto), ideas (Ivy) and revenue.

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

### alfred

Single-file app; one IIFE at the bottom of `index.html`. Committed dark navy palette
on `:root` — no light override and `<meta name="color-scheme" content="dark">`, so it
ignores the OS setting (deliberate: it's a console, not a document). Cyan (`--accent`)
is the only accent; `--warn` / `--danger` / `--ok` for status.

- **Two brains for Alfred.** With no Anthropic key, `localAlfred()` intent-routes the
  message (brief / orders / inbox / ideas / help) and answers from local data. With a
  key in Settings, `askClaude()` POSTs directly to `api.anthropic.com/v1/messages`
  from the browser (`anthropic-dangerous-direct-browser-access: true`); on any failure
  it falls back to the local brain. Each employee tab (Brief me / recap / ideas /
  simplify) works the same way — AI path first, local template fallback.
- **State** is all in `localStorage` under `alfred:*` keys (`settings`, `orders`,
  `emails`, `ideas`, `chat`, `tab`). `orders`/`emails` seed with clearly-tagged
  sample rows on first run. Every `store.set` is wrapped — a storage failure raises a
  fix card instead of throwing.
- **Voice.** `speak(text, who)` — `who` is `alfred|miles|otto|ivy`. Each teammate has
  its own ElevenLabs voice id (`settings.elevenVoice` / `voiceMiles` / `voiceOtto` /
  `voiceIvy`, all defaulting to built-in voices George/Brian/Daniel/Sarah);
  `elevenVoiceFor(who)` resolves it, falling back to Alfred's. The browser-synth
  fallback nudges `rate`/`pitch` per `who` (`BROWSER_TUNE`) so they still sound
  distinct with no key. `speak()` resolves on playback end, so the "Hear all four"
  button (`#testEleven`) can play the intros sequentially. Employee outputs auto-speak
  a trimmed version via `sayGist(node, who)` after Brief / recap / ideas / simplify;
  the chat reply is spoken in a teammate's voice when the reply text starts with
  "Otto…/Miles…/Ivy…". Greeting fires on load; if autoplay is blocked the utterance is
  stashed in `pendingSpeech` and released on the first `pointerdown` / the 🔊 button.
  Mic button = `SpeechRecognition` dictation. Settings → "Load my voices"
  (`loadElevenVoices()` → `GET /v1/voices`) fills all four `<select>`s (`VOICE_PICKERS`);
  free plans can only use `category:"premade"` voices via the API (others 402), so
  options are tagged "built-in" vs "needs paid plan" and `elevenError()` maps 402 to
  that explanation.
- **HUD ring** (`hudSVG()`) is a generated SVG reused on the Alfred and Revenue tabs:
  concentric circles, a JS-built 60-tick ring, a dot-pattern + radial-gradient sphere,
  CSS `spin` / `spin-rev` / `pulse` animations gated by `prefers-reduced-motion`.
- **Fix cards** (`fixCard(title, steps[], kind, opts)`) are the "when something goes
  wrong, give me steps" mechanism — bottom-right stack, `opts.once` de-dupes by key.
  Every API path (Anthropic, ElevenLabs, RevenueCat, storage, mic) maps its error
  codes to numbered recovery steps.
- **RevenueCat** is stubbed: `tryRevenueCat()` attempts `api.revenuecat.com/v2`, but
  the browser is CORS-blocked, so the Revenue tab shows demo figures and a fix card
  explaining the relay that live numbers need.
- `fmt()` is a tiny escape-first markdown renderer (bold / italic / code / bullets)
  used for all AI and template output.

## Conventions

- Palette and spacing are driven by CSS custom properties on `:root`. `timebox-timer`
  ships light + a `prefers-color-scheme: dark` override; `alfred` is dark-only by
  design. Either way, reuse the variables rather than hard-coding colors.
- Keep tools dependency-free and single-file unless there's a strong reason not to.
