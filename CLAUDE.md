# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Alfred started as a collection of small, self-contained static web tools — one tool
per top-level directory, a single `index.html` with inline `<style>`/`<script>`, no
build, opened straight in a browser.

- `timebox-timer/` — a Pomodoro-style focus timer. Still a pure static file.
- `alfred/` — a personal "chief of staff" desk (chat with Alfred + employee tabs for
  email/Miles, orders/Otto, ideas/Ivy, revenue). The UI is still one static
  `index.html` and still works opened directly, **but it now also has an Electron
  wrapper** (`main.js` / `preload.js` / `package.json`) that adds real Gmail access.
  When run in Electron the page sees `window.alfredNative`; without it, the Gmail
  features hide and everything else works as before.

## Running / developing

`timebox-timer`: open `index.html` in a browser.

`alfred` in a browser: open `alfred/index.html` directly (no Gmail).

`alfred` as the desktop app:

    cd alfred
    npm install
    npm start            # dev run
    npm run dist         # build dist/Alfred-Setup-<version>.exe (electron-builder)

Still no lint or test setup. See `alfred/README.md` for the one-time Google Cloud /
OAuth setup needed before Gmail will connect.

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

#### alfred — Electron / Gmail layer

Two Gmail transports in `main.js`; `imapActive()` (the `imap-creds.bin` file exists)
picks IMAP, otherwise OAuth. Every `gmail:*` IPC handler branches on it, so the
renderer only ever calls `NATIVE.gmail.list/get/send/draft/modify/trash`.

- **IMAP + SMTP path** (simplest for the user — no Cloud project). `imapConnect()`
  verifies an address + Google **App Password** against `imap.gmail.com:993`, then
  encrypts `{user,pass,host,port,smtpHost,smtpPort}` to `userData/imap-creds.bin`.
  Reads via `imapflow` (`imapList` = last N of INBOX, `imapGet` + `mailparser` for
  bodies); `modify {remove:["INBOX"]}` → move to `\All`, `trash` → move to `\Trash`
  (`boxFor()` resolves special-use, Gmail folder names as fallback); `send` via
  `nodemailer` SMTP `465`; `draft` = IMAP `APPEND` to `\Drafts`. `imapflow` /
  `mailparser` / `nodemailer` are lazy-`require`d so a missing `npm install` yields
  `IMAP_DEPS` rather than a startup crash.
- **OAuth path**: loopback + PKCE for a "Desktop app" client. `startAuth()` runs an
  `http` server on `127.0.0.1:0`, opens the system browser, catches `?code=`, trades
  it at `oauth2.googleapis.com/token` for access + **refresh** token. Refresh token
  encrypted at `userData/gmail-refresh.bin`; access token in memory,
  `getAccessToken()` refreshes. `invalid_grant` → `TOKEN_EXPIRED` (consent screen
  still "Testing" = 7-day cap; fix = publish to production). Client id/secret in
  `userData/google-creds.json` or `GOOGLE_CLIENT_*` env / `alfred/.env` (gitignored).
- **Every write asks first**: `confirmWrite()` (`dialog.showMessageBox`) on send /
  draft / archive / trash, both transports; returns `{cancelled:true}` if declined.
  IPC handlers wrapped in `guard()` → `{ok, data|error, code}`.
- `preload.js` exposes `window.alfredNative.gmail.*`. In `index.html`, `NATIVE` gates
  the Gmail path: `gmailBoot()` renders connection state + swaps the IMAP / OAuth
  setup blocks in Settings, `gmailSync()` maps messages into `emails` (tagged `gid`;
  hand-added emails without `gid` preserved), per-item buttons call `gmailWrite()` /
  `gmailDraftReply()`. `confirmAI()` gates sending any email body to Anthropic ("ask
  me per action"; `#gAiAlways` / `settings.aiEmailAlways` opts out); `alfredSystem()`
  drops email bodies from the chat system prompt while Gmail is connected unless
  `aiAlways`.
- **Voice master switch**: `#voiceToggle` / `setVoiceEnabled()` flips `settings.voice`
  (the old `#setVoice` checkbox is now a hidden state holder); `#voiceStop` calls
  `stopSpeaking()`. `speak()` already early-returns when `!settings.voice`.

## Conventions

- Palette and spacing are driven by CSS custom properties on `:root`. `timebox-timer`
  ships light + a `prefers-color-scheme: dark` override; `alfred` is dark-only by
  design. Either way, reuse the variables rather than hard-coding colors.
- `timebox-timer` stays dependency-free and single-file. `alfred`'s **UI** stays a
  single `index.html` that must keep working when opened directly (feature-detect
  `window.alfredNative`); its Electron wrapper is the one place npm deps are allowed.
