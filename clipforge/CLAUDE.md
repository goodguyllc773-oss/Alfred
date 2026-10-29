# CLAUDE.md

Before doing anything else, read, in this order:

1. **HANDOFF.md**: current state, last verified commit, what's confirmed working,
   what's pending or deferred, what's next. This file changes every session. Check
   it first, always.
2. **README.md**: setup and launch commands.
3. **docs/** (`architecture.md`, `decisions.md`, `implementation-plan.md`): kept
   current, the real source of truth for how the system actually works.
   `implementation-plan.md` holds the status checklist. Keep it honest: ✅ only
   when a feature is implemented *and* tested.

## What this is

ClipForge: a local-first AI clipping app. A long video goes in; ranked, captioned
9:16 clips come out for review. A Python engine + FastAPI (`backend/clipforge/`)
and a React/Vite/TypeScript UI (`frontend/`), with SQLite + Alembic, FFmpeg,
faster-whisper and OpenCV. Target machine: Windows 10/11, i5-9400F, RTX 2060
Super 8 GB, 16 GB RAM.

## Several machines: Windows PC + MacBook + cloud sessions

The code lives on GitHub (**public** repo `goodguyllc773-oss/clipforge`, branch
`main`) and is worked on from more than one machine. **The rule: work from any
machine must always reach the others, and every session must start on the latest
version. No progress gets lost.**

1. **Session start is automatic.** A Claude Code SessionStart hook runs
   `scripts/repo-sync.mjs`. The setup scripts install it in the user's Claude
   settings (`scripts/install-sync-hook.mjs`; `npm run install-hook` by hand). Its
   report shows up as "[ClipForge repo sync …]" context.
   - It auto-pulls when this machine is behind and has nothing pending.
   - It auto-pushes commits that never reached GitHub.
   - It changes nothing when there are uncommitted edits or both sides have new
     commits. **When it reports either, sort that out with the user FIRST, before
     any other work.**
   - If no "[ClipForge repo sync]" line appeared (hook not installed, or a cloud
     session): run `npm run sync` yourself, or do HANDOFF.md's "Step zero" by hand.
   - The script is deliberately not called `sync-check.mjs`: MCC's installer
     recognises its own hook by that name and would overwrite this one.
2. **Closeout** = update HANDOFF.md, then commit and push.
3. **Never carried by git** (per machine):
   - `backend/.venv`, `frontend/node_modules` and `frontend/dist`: the setup
     script creates them. Re-run it after pulls that change `backend/pyproject.toml`
     or `frontend/package-lock.json`; the sync hook says when.
   - The data dir: `%LOCALAPPDATA%\ClipForge` on Windows,
     `~/Library/Application Support/ClipForge` on macOS. It holds the database,
     renders, the cache and downloaded Whisper models. To move it between machines,
     copy the whole folder with ClipForge stopped (USB/AirDrop, never git).
     Source videos stay wherever the user keeps them; relink them on the other
     machine if the paths differ.

## Getting up and running

When the user says **"start it up" / "let's get to work" / "let's get up and
running" / "start the dev servers"**, start everything yourself and put the app
on screen. Don't hand the user a command.

1. **Sync first** (the hook, or `npm run sync`). If setup has never run on this
   machine (`backend/.venv` or `frontend/node_modules` missing), run setup first.
   On Windows, launch it the same way as below:
   `explorer.exe "<repo>\setup.cmd"`.
2. **Already running?** If `http://127.0.0.1:8765/api/health` and
   `http://127.0.0.1:5173/` both answer, don't start a second copy.
3. **Start the dev servers outside Claude's tool sandbox:**
   - **Windows:** `explorer.exe "<repo>\start-dev.cmd"`. The repo is expected at
     `C:\Users\xalex\clipforge`; fix this line if it's elsewhere.
   - **macOS:** `open <repo>/start-dev.command` (opens a Terminal window; it's
     executable in git).
   - Either one runs `npm run dev` (`scripts/dev.mjs`) in a normal window in the
     user's session: API + job worker on 127.0.0.1:8765, UI with hot reload on
     127.0.0.1:5173 (it proxies `/api` to :8765).
4. **Verify** that `/api/health` reports `"worker":"running"` and that :5173
   serves the UI.
5. **Put it on screen:** open http://127.0.0.1:5173/ in the Claude app's
   built-in browser pane (`.claude/launch.json` names the same command and port).
6. **Cloud session** (Linux container, no screen of the user's): run `npm run dev`
   in the background and verify the ports. Tell the user it can't be shown on
   their screen from a cloud session.

Don't start the servers with a plain background Bash/PowerShell `npm run dev` on
the user's machines. Processes started that way run inside the sandbox, and on
Windows they see a private copy of `AppData` (next section).

`start.cmd` / `clipforge serve --open` is the non-dev way to use the app: the
built UI served on :8765, no hot reload.

## Sandbox gotcha (Windows): AppData is not shared

Claude's tool sandbox has its own private view of `C:\Users\xalex\AppData`. That
matters a lot here, because ClipForge keeps **everything** in
`%LOCALAPPDATA%\ClipForge`: the database, renders and the Whisper models. A server
or `clipforge doctor` started from a tool call would use a different, empty data
dir, and could download the model a second time. A user-scope Python install also
lands in AppData. So run servers, setup and anything that should touch the real
data dir through `explorer.exe <script>.cmd`. The repo folder itself is shared
normally, so the venv and `node_modules` are the same for both.

Running `pytest` from a tool call is fine: tests use temporary data dirs.

## Keep HANDOFF.md current

Re-read HANDOFF.md (don't rely on memory of an earlier read) whenever:

- Starting a new session, especially on a different device.
- Resuming after a gap: a date-change notice, or the user saying it's been a while.
- The user asks you to check it.

Update HANDOFF.md at BOTH of these points. They're different events, not one:

- **Before ending a work session**: the user says they're done, switching devices,
  or closing out.
- **Immediately before any context compaction**: the user says "compact" /
  "we're gonna compact", or any other signal that one is about to happen.
  Compaction can happen mid-session, not just at a natural stopping point, and
  whatever hasn't been written to HANDOFF.md yet does not survive it. Don't wait
  for a better moment: update it right then.

Each update should capture:

- Current commit.
- What you tested/verified and how. Be specific: machine, video/file, page, what
  you actually saw (numbers, encoder, device).
- Anything left broken, half-done, or explicitly deferred.
- A concrete "what's next", not a vague one.

Prune stale entries as they're resolved. HANDOFF.md describes the current state;
history belongs in `git log` and `docs/implementation-plan.md`. Commit it along
with everything else, then push, so the other machines get it.

## Commands

    # setup (once per machine; safe to re-run)
    setup.cmd                      # Windows (launch via explorer.exe from Claude)
    scripts/setup-mac.sh           # macOS / Linux
    # dev servers
    npm run dev                    # API :8765 + UI :5173 (start-dev.cmd / start-dev.command)
    # tests (needs ffmpeg/ffprobe on PATH; ~2 min)
    cd backend && .venv/bin/python -m pytest            # Windows: .venv\Scripts\python -m pytest
    cd backend && .venv/bin/python -m pytest tests/test_jobs.py
    CLIPFORGE_TEST_WHISPER_MODEL=tiny.en .venv/bin/python -m pytest -m whisper   # real model
    # UI
    cd frontend && npm run typecheck && npm run build
    # diagnostics
    cd backend && .venv/bin/python -m clipforge doctor

Schema changes: edit `backend/clipforge/db/models.py`, then from `backend/` run
`alembic -c alembic.ini upgrade head` (throwaway `dev-clipforge.db`, gitignored),
then `alembic -c alembic.ini revision --autogenerate -m "..."`, and review the
generated file. `alembic -c alembic.ini check` must report no drift.

## Rules that stay in force

- **Source footage is never modified, moved or deleted.** It's only ever an ffmpeg
  input. Raw footage and renders stay local by default.
- **No unauthorized sourcing:** no scraping, no downloading arbitrary third-party
  videos, no getting around access controls, no credential harvesting. Future
  source connectors use creator-authorised sources and official integrations only.
- **No automatic publishing.** Future publishing uses official platform APIs with
  tokens in the OS keychain, never in the repo or the database in plain text.
- **No paid services or recurring costs** without an explicit need and the user's
  OK. The core workflow must run locally for $0.
- **Be honest about intelligence:** `heuristic-v1` is a baseline and must stay
  labelled as one. Never claim semantic ranking, diarization or tracking that
  isn't built and tested.
- Engine modules (`media`, `transcription`, `analysis`, `captions`, `render`,
  `profiles`) must not import `api`, `jobs`, `services` or `context`.
- A job only reports `completed` when its handler returned. Renders only count as
  done after ffprobe and decode validation.
- Verify features live (in the browser, against real media) as well as by tests.
  Record what you saw in HANDOFF.md.
- Secrets (`.env`, tokens, API keys) and data (`*.db`, renders, models) are never
  committed.
- Only commit when the user asks. When they close out, always commit AND push.
