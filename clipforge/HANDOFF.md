# Handoff (ClipForge)

Read this first, every time you work on ClipForge, before touching anything else.
Update it before you stop, and immediately before any context compaction (see
CLAUDE.md, "ClipForge: HANDOFF.md", for the exact rule).

_Last updated 2026-10-29 (cloud session, Linux container, no GPU). **Where we left
off:** ClipForge has been **moved into its own repo**, `goodguyllc773-oss/clipforge`
(public, branch `main`), with its history kept. It now has MCC-style "start it up"
support (`npm run dev` / `start-dev.cmd` / `.claude/launch.json`) and a
session-start sync hook. **⚠ The GitHub repo doesn't exist yet:** Claude's GitHub
integration isn't allowed to create repositories (403), so the user has to create
it (see "Pending: create the GitHub repo"). Until then, the newest work is backed
up inside the old Alfred branch. Milestone 1 has still **never run on the Windows
PC**: that's the next job after the repo exists._

## Step zero, every session: fetch before trusting this file

**Before reading anything else, run `git fetch origin` and compare local HEAD to
`origin/main`.** The SessionStart hook (`scripts/repo-sync.mjs`) does this
automatically and reports "[ClipForge repo sync …]". With no such line, run
`npm run sync`. This repo is worked on from several machines (Windows PC, Mac,
cloud sessions); a stale checkout means a stale HANDOFF.

- If local is behind: `git pull --ff-only` *before* reading the rest of this file.
- If this file says something different from `git log`, trust `git log` and fix
  this file.

## Pending: create the GitHub repo (user step, once)

1. On github.com, signed in as **goodguyllc773-oss**: **New repository** → name
   `clipforge`, **Public**, and **no** README, .gitignore or license (it must be
   empty).
2. If the Claude GitHub App is installed for "Only select repositories", add
   `clipforge` to it: GitHub → Settings → Applications → Claude → Configure.
3. Tell Claude. Claude then pushes `main` from wherever this repo was prepared.
   - If that cloud container is gone, re-create the repo from the backup:
     `git subtree split --prefix=clipforge` on Alfred's branch
     `claude/clipforge-local-first-ai-ojxjpr`, then push the result to the new
     repo's `main`.
4. After the push: replace `clipforge/` on that Alfred branch with a pointer to
   the new repo, so there's one copy. It was never merged into Alfred's `main`.

## Current state

- **Repo:** `goodguyllc773-oss/clipforge` (public), branch `main`; check
  `git log -1`. Not on GitHub yet (see above).
- **History:** carried over from the Alfred repo with `git subtree split`:
  - `2c8f233` milestone 1 vertical slice
  - `c951c8c` HANDOFF.md + rules
  - then the standalone-repo commit (CLAUDE.md, launcher, sync hook).
- **Where it has run:** only 2026-10-29 cloud sessions:
  - Linux, Python 3.13, Node 22, FFmpeg 6.1.1, 4 CPUs, 15 GB RAM, **no GPU**.
  - **Windows PC: never set up. Mac: never set up.**
- **Running servers:** none that matter. Cloud-session servers and data dirs were
  temporary.
- **Data:** none worth keeping exists anywhere yet. A fresh data dir is created on
  first start:
  - Windows: `%LOCALAPPDATA%\ClipForge`
  - Mac: `~/Library/Application Support/ClipForge`
  - Override with `CLIPFORGE_DATA_DIR`.
  - It holds the DB, renders, models, cache and logs. **Not in git.**
- **Database:** one Alembic migration, `0001_initial_schema`, applied automatically
  on start. `alembic check` reported no drift.
- **Per-machine setup (git doesn't carry it):**
  - `backend/.venv`, `frontend/node_modules`, `frontend/dist`, the Whisper model,
    and the Claude sync hook in `~/.claude/settings.json`.
  - `setup.cmd` (Windows) or `scripts/setup-mac.sh` (macOS) creates all of them.
  - Re-run it after pulls that change dependencies; the sync hook says when.
- **"Start it up":** `npm run dev` (`scripts/dev.mjs`) runs the API + worker on
  :8765 and the hot-reload UI on :5173 (which proxies `/api`).
  - Windows: Claude launches it with `explorer.exe "<repo>\start-dev.cmd"`.
  - Mac: `open start-dev.command`.
  - Then Claude opens http://127.0.0.1:5173/ in the built-in browser pane. See
    CLAUDE.md, "Getting up and running".
  - `start.cmd` is the non-dev way: the built UI on :8765.
- **Gotcha #1 (Windows): AppData isn't shared with Claude's sandbox,** and
  ClipForge keeps its DB, renders and Whisper models in `%LOCALAPPDATA%`. Start
  servers and setup through `explorer.exe`, never as a plain tool-call process.
- **Gotcha #2:** the UI on :8765 is `frontend/dist`. After changing
  `frontend/src`, `npm run build`, or use the dev servers (:5173 hot-reloads).
- **Gotcha #3:** `CLIPFORGE_TRANSCRIBER=sidecar` reads `.srt/.vtt/.json` files
  next to the video instead of running Whisper. That's how cloud sessions test
  without a model. The default is `faster-whisper`.
- **Gotcha #4:** in a Bash tool call, `pkill -f` / `pgrep -f` / `kill $(…grep…)`
  patterns match the tool's own shell command line and kill it. Put
  process-hunting in a script file, or use `ps -eo pid,args` with a `[b]racket`
  pattern.
- **Gotcha #5:** in Playwright, `has-text("Approve")` also matches the
  "approved (0)" tab. Use `✓ Approve`.
- **Gotcha #6:** cloud sessions couldn't reach huggingface.co (network policy), so
  no Whisper models there unless the environment's Network access allows it.
- **Gotcha #7:** schema changes: `alembic -c alembic.ini upgrade head`, then
  `revision --autogenerate`. Autogenerating against an empty DB re-emits the whole
  schema.
- **Gotcha #8:** MCC's setup recognises its hook by `sync-check.mjs` in the
  command, so ClipForge's script is `repo-sync.mjs` and its installer matches
  `repo-sync.mjs` + `clipforge`. Don't rename either to `sync-check.mjs`.

## First run on the Windows PC: the runbook

This has **never been done**. Do it in order, and record what you actually see
here.

1. **Get the code** (after the GitHub repo exists):
   `git clone https://github.com/goodguyllc773-oss/clipforge.git C:\Users\xalex\clipforge`.
   If you put it elsewhere, fix the path in CLAUDE.md.
2. **Prerequisites** (`setup.cmd` checks each one and prints the winget command):
   - Python 3.12: `winget install Python.Python.3.12`
   - FFmpeg: `winget install Gyan.FFmpeg`. Open a new terminal afterwards so PATH
     updates.
   - Node.js LTS: `winget install OpenJS.NodeJS.LTS`
3. **Setup:** `explorer.exe "C:\Users\xalex\clipforge\setup.cmd"`, or the user
   double-clicks it.
   - It creates `backend\.venv`, installs packages, offers the CUDA libraries
     (`-Gpu` = yes), builds the UI, installs the Claude sync hook and runs
     `clipforge doctor`.
   - ⚠ **The PowerShell scripts have never been executed** (no PowerShell in the
     cloud). Expect small fixes on this first run.
4. **Check `doctor` output:**
   - `h264_nvenc` should say **OK**. In the cloud it said "Cannot load
     libcuda.so.1", which was expected with no GPU.
   - `CUDA` should say **1 CUDA device(s)**.
   - If CUDA shows 0 while `nvidia-smi` works, suspect
     `transcription/whisper.py` → `ensure_cuda_dll_dirs` first (never run on
     Windows).
5. **Tests:** `backend\.venv\Scripts\python -m pytest` in `backend\`. Expect 101
   passed, 1 skipped. Then the real-Whisper test:
   `set CLIPFORGE_TEST_WHISPER_MODEL=tiny.en` and `… -m pytest -m whisper`.
6. **"Start it up":**
   - `explorer.exe "C:\Users\xalex\clipforge\start-dev.cmd"`.
   - Verify `:8765/api/health` reports `"worker":"running"` and :5173 answers.
   - Open http://127.0.0.1:5173/ in the built-in browser pane.
   - Settings → Diagnostics should show NVENC ✓ and the CUDA device.
7. **A real end-to-end:**
   - Import a real long video the user has rights to. The first transcription
     downloads `small` (≈460 MB).
   - Record: transcription time per hour of audio, the transcript's device
     (`cuda`/`cpu`), and the render encoder (`h264_nvenc`?).
   - Approve a clip, play it, export it, open the exported MP4.
8. **Robustness:**
   - Close the dev-server window mid-transcription and mid-render, start again,
     and confirm the transcription resumes and the render re-runs.
   - Rename a source file: it should show "missing" and relink should work.
   - Paths with spaces and non-ASCII names.
9. **Next Claude session on that PC** should begin with a "[ClipForge repo sync …]"
   line. If not, run `npm run install-hook`.
10. Update this file ("Current state" + "Last verified working"), then commit and
    push.

## Last verified working (2026-10-29, cloud session)

- **The standalone repo, from a fresh clone layout:**
  - `scripts/setup-mac.sh` ran clean: venv, packages, `npm ci` + UI build, sync
    hook, doctor.
  - `pytest` from the repo's own `backend/.venv`: **101 passed, 1 skipped**
    (101.6 s).
- **`npm run dev`:**
  - Both servers answered: `/api/health` → `{"ok":true,"worker":"running"}`, and
    :5173 → 200, including `/api/health` proxied through :5173.
  - Headless Chromium loaded the UI from :5173 with 0 console errors.
  - **Bug found and fixed:** stopping the runner left Vite orphaned on :5173
    (signalling `npm` doesn't reach `vite`). Children now get their own process
    group and are stopped as a group, with SIGKILL after 5 s; Windows uses
    `taskkill /T`.
  - Re-tested two cases, and both left 0 processes and both ports closed:
    - Ctrl+C to the runner;
    - `kill -9` of the API (the runner then stopped the UI too).
- **Sync hook** (`repo-sync.mjs`), against a local bare repo standing in for
  GitHub plus a second clone as "the other machine": all 7 cases behaved as
  designed.
  - in sync → no change
  - behind → fast-forward pull plus "re-read HANDOFF"
  - ahead → pushed
  - diverged → nothing changed, told to merge
  - behind with uncommitted edits → nothing pulled, edits kept
  - `frontend/package-lock.json` changed → "re-run setup" hint
  - hook JSON (`SessionStart`) is well-formed
- **Hook installer:** merged into a `settings.json` that already had MCC's
  `sync-check.mjs` hook, leaving MCC's entry untouched; a re-run said "already
  installed".
- **Not verified:**
  - `start-dev.cmd` / `setup.cmd` via `explorer.exe` on Windows;
  - the Windows `taskkill` path;
  - the hook against the real GitHub remote (it doesn't exist yet).

**Milestone 1 itself (same day, before the move):**

- **Automated tests:** `python -m pytest` in `backend/` gave **101
  passed, 1 skipped** in 103 s.
  - The skip is `test_real_whisper_transcribes_synthetic_speech`, which needs a
    downloaded model.
  - Test media is generated by ffmpeg at test time (test patterns + `flite`
    synthetic speech, with an SRT timed to the exact sentence edges). No
    third-party footage.
  - `docs/implementation-plan.md` maps each of the 17 required test areas to its
    tests.
- **Full workflow in a real browser** (headless Chromium through Playwright
  against `python -m clipforge serve` with `CLIPFORGE_TRANSCRIBER=sidecar`):
  - Created "Demo Podcast" (podcast profile) and imported an 80 s synthetic-speech
    video with its `.srt`.
  - Transcribe and analyze both completed; 2 clips were found:
    - #1 scored 72, 0:00–0:44. Its reasons: question followed by a 32 s answer;
      ends on a finished sentence + 1.9 s pause; opens with a question.
    - #2 scored 53, 0:47–1:20.
  - Approve → a render job ran automatically → the "Rendered v1" player showed the
    1080×1920 clip with yellow word-highlight captions. Encoder: libx264.
  - Export wrote `.mp4` + `.json` + `.txt` into the chosen folder; the "Copy
    caption" box appeared.
  - Jobs page and Settings/Diagnostics rendered. **0 console errors.**
- **A/V sync was measured, not assumed:** a flash + beep every 2 s, cut from 0.5 s.
  The flash onsets were exactly [0.5, 2.5, 4.5, 6.5] s on the video frames, and the
  beep onsets matched within the 5 ms measuring window.
- **Hardware detection on a no-GPU machine:** NVENC and QSV are compiled into
  ffmpeg but correctly reported as not working ("Cannot load libcuda.so.1", "Error
  creating a MFX session: -9"), so renders fell back to libx264.
- **Crash-resume:** a test transcriber made chunk 1 fail; the retry skipped
  committed chunk 0 and finished (attempts = 2).
- Bugs found and fixed while testing (all covered by tests now):
  - a clip end could round 0.3 ms past the video's end;
  - `safe_filename` treated `_etc_passwd` as a file extension;
  - the review player cut off the bottom of 9:16 clips (the captions).

## Decisions (made by Claude, NOT asked or confirmed by the user)

All recorded with reasons in `docs/decisions.md` (D1–D12). Ask before relying on
the big ones as if the user had agreed:

- The UI is a localhost web page for now; Tauri was deferred.
- A SQLite job queue (no Redis/Celery).
- Default Whisper model `small`.
- Approving a clip auto-renders it; export needs approval.
- Silent videos get scene-based "weak" candidates and a silent audio track.

## Not verified yet / known limits

1. **Real Whisper never ran.** Only a stand-in model was used (models couldn't be
   downloaded). Covered by runbook steps 5 and 7.
2. **NVENC never encoded:** only its detection and fallback logic were tested.
   Runbook steps 4 and 7.
3. **Windows scripts never executed** (`setup.cmd`, `start.cmd`, both `.ps1`
   files). Neither was `ensure_cuda_dll_dirs`.
4. **Face framing is untested on real faces:** the synthetic videos have none.
   Only the crop math is tested.
5. **Clip ranking is a heuristic baseline (`heuristic-v1`).** It scores structure
   (hook, finished thought, energy…), not meaning. The UI says so.
6. No speaker detection. Facecam and split-screen regions are set by hand.
7. SRT/VTT imports have estimated word timings, so the highlight can drift within
   a line.
8. **No committed UI test:** the browser walkthrough was a one-off script.

## What's next

1. **User:** create the empty public GitHub repo (see "Pending: create the GitHub
   repo"). Then **Claude:** push `main`, and reduce Alfred's `clipforge/` to a
   pointer.
2. **First run on the Windows PC:** the runbook above, steps 1–10. Fix whatever
   breaks in the setup scripts; record real Whisper speed and NVENC.
3. **Milestone 2** (details in `docs/implementation-plan.md`). First item: a local
   semantic re-ranker through Ollama (`analysis/semantic.py` is the plug-in point),
   plus a small hand-labelled set of real videos to measure pick quality.
